from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from seo_hub.errors import SeoHubError
from seo_hub.adapters.mcp_transport import HTTPMCPClient, is_loopback_url, validate_service_url


OPENSEO_PINNED_COMMIT = "ac9ee482d2b4cd8f472065d6f9b57db35cec560e"
OPENSEO_MCP_VERSION = "0.0.12"
OPENSEO_READ_TOOLS = frozenset(
    {
        "list_projects",
        "get_project_context",
        "get_rank_tracker",
        "list_saved_keywords",
    }
)


class OpenSEOAdapterError(SeoHubError, ValueError):
    code = "OPENSEO_ADAPTER_ERROR"


class OpenSEOPinContractError(OpenSEOAdapterError):
    """A structured MCP response cannot be trusted for this pinned adapter."""


class OpenSEOAuthError(OpenSEOAdapterError):
    code = "OPENSEO_AUTH_NOT_READY"

    def to_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "safe_message": "OpenSEO credentials are not ready.",
            "retryable": False,
            "details": {"reason": "missing_service_credentials"},
        }


class OpenSEOMCPClient(Protocol):
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Any:
        ...


@dataclass(frozen=True)
class OpenSEOAdapter:
    mcp_url: str
    client: OpenSEOMCPClient | None = None
    access_client_id: str = field(default="", repr=False)
    access_client_secret: str = field(default="", repr=False)
    timeout_seconds: float = 30
    api_key: str = field(default="", repr=False)
    max_rank_age_seconds: float = 7 * 86400

    def __post_init__(self):
        validate_service_url(self.mcp_url)
        if self.timeout_seconds <= 0 or self.max_rank_age_seconds <= 0:
            raise OpenSEOAdapterError("Request and freshness limits must be positive")
        if any(c in self.api_key + self.access_client_id + self.access_client_secret for c in "\r\n"):
            raise OpenSEOAuthError("Invalid OpenSEO credentials")
        if self.client is None:
            object.__setattr__(self, "client", HTTPMCPClient(self.mcp_url))

    async def read_tool(self, name: str, **arguments: Any) -> dict[str, Any]:
        if name not in OPENSEO_READ_TOOLS:
            raise OpenSEOAdapterError(f"OpenSEO MCP tool is not allowlisted for read-only runs: {name}")
        headers = self._access_headers()
        aliases = {"project_id": "projectId", "rank_tracker_id": "trackerId"}
        normalized = {}
        for key, value in arguments.items():
            if value is None:
                continue
            name_key = aliases.get(key, key)
            if name_key in normalized:
                raise OpenSEOAdapterError("Duplicate tool argument")
            normalized[name_key] = value
        # Omit unset optional arguments so OpenSEO MCP applies its documented defaults.
        payload = await self.client.call_tool(
            name,
            normalized,
            headers=headers,
            timeout_seconds=self.timeout_seconds,
        )
        if isinstance(payload, dict) and payload.get("isError"):
            raise OpenSEOAdapterError(f"OpenSEO MCP tool returned an error result: {name}")
        result = _normalize_tool_result(payload)
        if result["structured"]:
            _verify_pinned_contract(name, result["structuredContent"])
        return result

    async def collect_project_evidence(
        self,
        *,
        project_id: str,
        rank_tracker_id: str | None,
        window: dict[str, str],
    ) -> dict[str, Any]:
        context = await self.read_tool("get_project_context", project_id=project_id)
        try:
            tracker = await self.read_tool(
                "get_rank_tracker", project_id=project_id, rank_tracker_id=rank_tracker_id,
            )
        except OpenSEOPinContractError:
            raise
        except SeoHubError:
            tracker = {"structured": False, "text": "Rank tracker evidence unavailable"}
        saved_keywords = await self.read_tool("list_saved_keywords", project_id=project_id)

        # A scoped request must come back with the identity it asked for: a missing
        # `id` is "cannot confirm this is the right project", and for a fail-closed
        # gate that is the same as a mismatch — otherwise evidence from an
        # unverified project ships under the requested id.
        project = None
        if context["structured"]:
            project = _structured(context).get("project")
            if not isinstance(project, dict) or project.get("id") != project_id:
                got = project.get("id") if isinstance(project, dict) else project
                raise OpenSEOAdapterError(
                    f"OpenSEO project identity unconfirmed: requested {project_id!r}, got {got!r}"
                )
        # The tracker identity stays "mismatch only": the recorded MCP transcript
        # puts `id` at the top level of structuredContent, but existing callers and
        # fixtures also carry it under `config`, so requiring it here would reject
        # shapes we have not yet confirmed against the live server. Tightening this
        # belongs with the pinned-contract verification (#2740).
        if tracker["structured"] and rank_tracker_id is not None:
            returned_tracker_id = tracker.get("structuredContent", {}).get("id")
            if returned_tracker_id is not None and returned_tracker_id != rank_tracker_id:
                raise OpenSEOAdapterError(
                    "OpenSEO rank tracker identity unconfirmed: "
                    f"requested {rank_tracker_id!r}, got {returned_tracker_id!r}"
                )
        tracker_payload = _structured_or_text(tracker)
        keyword_data = _structured(saved_keywords)
        keywords_payload = keyword_data.get("rows", keyword_data.get("keywords", []))
        quality = "complete" if all(item["structured"] for item in (context, tracker, saved_keywords)) else "partial"
        total_count = keyword_data.get("totalCount")
        if not isinstance(keywords_payload, list):
            keywords_payload = []
            quality = "partial"
        if total_count is not None and (not isinstance(total_count, int) or total_count > len(keywords_payload)):
            quality = "partial"
        rank_data = _structured(tracker)
        rank_availability = _rank_availability(rank_data, self.max_rank_age_seconds)
        if rank_availability["state"] != "available" or not keywords_payload:
            quality = "partial"
        observation_count = rank_availability["observation_count"] + len(keywords_payload)
        if observation_count == 0:
            quality = "missing"
        # These tools expose cached/latest data, never a requested history range.
        if quality == "complete" and (window.get("start") or window.get("end")):
            quality = "partial"
        checked = rank_availability["last_checked_at"]
        effective_window = ({"start": checked, "end": checked, "kind": "latest_snapshot"}
                            if checked and rank_availability["run_status"] == "completed" else None)
        server_info = getattr(self.client, "server_info", {})

        return {
            "source": "openseo_mcp",
            "mcp": {
                "server_version": server_info.get("version") if isinstance(server_info, dict) else None,
                "commit": None,
                "url": self.mcp_url,
            },
            "project": project,
            "project_context": _structured_or_text(context),
            "window": _collection_window(),
            "requested_window": dict(window),
            "effective_window": effective_window,
            "window_applied": False,
            "window_note": "Latest rank snapshot only; saved keywords and context have no applied date filter",
            "rank_tracker": tracker_payload,
            "rank_availability": rank_availability,
            "saved_keywords": keywords_payload if isinstance(keywords_payload, list) else [],
            "saved_keywords_total": total_count,
            "quality": quality,
            "observation_count": observation_count,
            "availability": "available" if observation_count else "no_measurements",
        }

    def _access_headers(self) -> dict[str, str]:
        if bool(self.access_client_id) != bool(self.access_client_secret):
            raise OpenSEOAuthError("OpenSEO Access service credentials are missing.")
        headers = {}
        cid = self.access_client_id
        csec = self.access_client_secret
        if cid:
            headers["CF-Access-Client-Id"] = cid
            headers["CF-Access-Client-Secret"] = csec
        authz = self.api_key
        if authz:
            headers["Authorization"] = "Bearer " + authz
        if not headers and not is_loopback_url(self.mcp_url):
            raise OpenSEOAuthError("OpenSEO credentials are missing.")
        return headers


def _normalize_tool_result(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and (payload.get("isError") or "error" in payload):
        raise OpenSEOAdapterError("OpenSEO tool failed")
    if not isinstance(payload, dict):
        raise OpenSEOAdapterError("OpenSEO tool result must be an object")
    structured = payload.get("structuredContent")
    if isinstance(structured, dict):
        return {"structured": True, "structuredContent": structured}
    text = _text_content(payload.get("content"))
    return {"structured": False, "text": text}


def _rank_availability(payload: dict[str, Any], max_age: float) -> dict[str, Any]:
    results = payload.get("results")
    results = results if isinstance(results, dict) else {}
    rank_rows = payload.get("rank_rows")
    if not results and isinstance(rank_rows, list):
        # Pinned get_rank_tracker (0.0.12) returns cached rows directly under
        # structuredContent, with no run/timestamp wrapper: freshness cannot be
        # judged, but the rows themselves are real cached ranking evidence.
        return {"state": "available" if rank_rows else "no_measurements", "run_status": "completed" if rank_rows else None,
                "last_checked_at": None, "age_seconds": None, "max_age_seconds": max_age,
                "observation_count": len(rank_rows), "cached_row_count": len(rank_rows)}
    run = results.get("run")
    run = run if isinstance(run, dict) else {}
    rows = results.get("rows")
    rows = rows if isinstance(rows, list) else []
    status = run.get("status")
    checked = run.get("lastCheckedAt")
    parsed = None
    if isinstance(checked, str):
        try:
            parsed = datetime.fromisoformat(checked.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = None
        except ValueError:
            pass
    age = (datetime.now(timezone.utc) - parsed).total_seconds() if parsed else None
    if not run:
        state = "not_configured" if payload.get("configs") == [] else "no_run"
    elif status != "completed":
        state = "run_not_completed"
    elif not rows:
        state = "no_measurements"
    elif age is None:
        state = "unknown_freshness"
    elif age < -300:
        state = "invalid_timestamp"
    elif age > max_age:
        state = "stale"
    else:
        state = "available"
    return {"state": state, "run_status": status, "last_checked_at": checked if parsed else None,
            "age_seconds": age, "max_age_seconds": max_age,
            "observation_count": len(rows) if state == "available" else 0,
            "cached_row_count": len(rows)}


def _structured(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("structuredContent") if payload.get("structured") is True else {}
    return dict(value) if isinstance(value, dict) else {}


def _structured_or_text(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("structured") is True:
        return _structured(payload)
    return {"structured": False, "text": str(payload.get("text") or "")}


def _text_content(content: Any) -> str:
    if isinstance(content, list):
        parts = [str(item.get("text")) for item in content if isinstance(item, dict) and item.get("type") == "text"]
        return "\n".join(part for part in parts if part)
    return ""


def _verify_pinned_contract(name: str, structured_content: dict[str, Any]) -> None:
    commit = structured_content.get("commit")
    server_version = structured_content.get("server_version")
    if commit != OPENSEO_PINNED_COMMIT or server_version != OPENSEO_MCP_VERSION:
        raise OpenSEOPinContractError(
            f"OpenSEO MCP response for {name} does not match the pinned contract: "
            f"commit={commit!r} server_version={server_version!r}"
        )


def _collection_window() -> dict[str, str]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {"start": today, "end": today, "timezone": "UTC"}
