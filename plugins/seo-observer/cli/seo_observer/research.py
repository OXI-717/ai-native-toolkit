from __future__ import annotations

import dataclasses
import hashlib
import json
import ssl
import urllib.request
from pathlib import Path
from typing import Any, Protocol


class ResearchTransport(Protocol):
    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        ...


class WebSearchAdapter(Protocol):
    def search(
        self,
        *,
        query: str,
        num_results: int,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        contents: dict[str, Any] | None = None,
        structured_output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


@dataclasses.dataclass(frozen=True)
class FixtureResearchTransport:
    fixture_path: Path

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        value = json.loads(Path(self.fixture_path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        return value


class ExaHttpTransport:
    def __init__(self, endpoint: str, api_key: str, *, timeout_seconds: int = 30) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.ssl_context = _default_ssl_context()

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        url = self.endpoint if self.endpoint.endswith("/search") else f"{self.endpoint}/search"
        payload = _exa_search_payload(request)
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        credential = self.api_key
        headers = {"Content-Type": "application/json"}
        headers["x-api-key"] = credential
        http_request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds, context=self.ssl_context) as response:
                response_payload = response.read().decode("utf-8")
        except Exception as exc:
            raise RuntimeError("Exa search request failed; provider details redacted.") from exc
        value = json.loads(response_payload)
        if not isinstance(value, dict):
            raise RuntimeError("Exa search response must be a JSON object.")
        return value


class ExaResearchAdapter:
    provider = "exa"

    def __init__(self, transport: ResearchTransport, *, observed_at: str | None = None):
        self.transport = transport
        self.observed_at = observed_at or _utc_now()

    def search(
        self,
        *,
        query: str,
        num_results: int,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        contents: dict[str, Any] | None = None,
        structured_output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = {
            "query": _safe_request_value(query),
            "num_results": int(num_results),
            "include_domains": list(include_domains or []),
            "exclude_domains": list(exclude_domains or []),
            "contents": dict(contents or {"highlights": True}),
            "structured_output": dict(structured_output or {}),
        }
        try:
            raw = self.transport.search(request)
        except Exception:
            return {
                "ok": False,
                "provider": self.provider,
                "quality": "partial",
                "rows": [],
                "metadata": {"provider_request_ids": []},
                "cost": {"provider_cost_usd": None, "estimated_cost_usd": None, "currency": "USD"},
                "errors": [
                    {
                        "code": "RESEARCH_PROVIDER_ERROR",
                        "safe_message": "Research provider request failed; details redacted.",
                        "details": {},
                    }
                ],
                "request": request,
            }
        rows, errors = _normalize_exa_rows(raw.get("results") or [], observed_at=self.observed_at)
        provider_request_ids = _provider_request_ids(raw)
        cost = _cost(raw)
        quality = "partial" if errors else "research-only"
        return {
            "ok": True,
            "provider": self.provider,
            "quality": quality,
            "rows": rows,
            "metadata": {"provider_request_ids": provider_request_ids},
            "cost": cost,
            "errors": errors,
            "request": request,
        }


class FixtureWebSearchAdapter(ExaResearchAdapter):
    provider = "web_search_fixture"


def _exa_search_payload(request: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": request.get("query"),
        "numResults": request.get("num_results"),
        "contents": request.get("contents") or {"highlights": True},
    }
    include_domains = request.get("include_domains")
    if include_domains:
        payload["includeDomains"] = include_domains
    exclude_domains = request.get("exclude_domains")
    if exclude_domains:
        payload["excludeDomains"] = exclude_domains
    structured_output = request.get("structured_output")
    if structured_output:
        payload["outputSchema"] = structured_output
    return {key: value for key, value in payload.items() if value is not None}


def _normalize_exa_rows(rows: Any, *, observed_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return normalized, [
            _row_error(index=0, reason="results_not_list"),
        ]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(_row_error(index=index, reason="not_object"))
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            errors.append(_row_error(index=index, reason="missing_url"))
            continue
        title = str(row.get("title") or "").strip()
        highlights = row.get("highlights")
        snippet = _snippet(row.get("text"), highlights)
        row_id = str(row.get("id") or row.get("citation_id") or _hash(url))
        normalized.append(
            {
                "citation_id": row_id if row_id.startswith("research:") else f"research:{row_id}",
                "url": url,
                "title": title or url,
                "snippet": snippet,
                "quality": "research-only",
                "observed_at": observed_at,
                "provider": "exa",
                "score": row.get("score"),
                "published_date": row.get("publishedDate") or row.get("published_date"),
            }
        )
    return normalized, errors


def _snippet(text: Any, highlights: Any) -> str:
    parts: list[str] = []
    if isinstance(highlights, list):
        parts.extend(str(item).strip() for item in highlights if str(item).strip())
    elif isinstance(highlights, str) and highlights.strip():
        parts.append(highlights.strip())
    if not parts and isinstance(text, str) and text.strip():
        parts.append(text.strip())
    return " ".join(parts)[:500]


def _provider_request_ids(raw: dict[str, Any]) -> list[str]:
    for key in ("requestId", "request_id", "id"):
        value = raw.get(key)
        if value:
            return [str(value)]
    return []


def _cost(raw: dict[str, Any]) -> dict[str, Any]:
    cost_value = raw.get("costDollars") or raw.get("cost") or {}
    provider_cost = None
    currency = "USD"
    if isinstance(cost_value, dict):
        provider_cost = cost_value.get("total") or cost_value.get("provider_cost_usd")
        currency = str(cost_value.get("currency") or currency)
    elif isinstance(cost_value, (int, float)):
        provider_cost = cost_value
    return {
        "provider_cost_usd": float(provider_cost) if provider_cost is not None else None,
        "estimated_cost_usd": None,
        "currency": currency,
    }


def _row_error(*, index: int, reason: str) -> dict[str, Any]:
    return {
        "code": "RESEARCH_ROW_MALFORMED",
        "safe_message": "Skipped malformed research result row.",
        "details": {"index": index, "reason": reason},
    }


def _safe_request_value(value: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in ("token=", "api_key=", "password=", "secret=")):
        return "[REDACTED]"
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _default_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
