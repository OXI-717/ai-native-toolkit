from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from seo_hub.adapters.base import AdapterResult
from seo_hub.errors import SeoHubError
from seo_hub.adapters.mcp_transport import NoRedirect, validate_service_url


class ElmoAdapterError(SeoHubError, ValueError):
    code = "ELMO_ADAPTER_ERROR"


PINNED_ELMO_COMMIT = "814af9c069331696d7bd54ababe7417302a2886e"
PINNED_ELMO_WEB_VERSION = "0.3.0"
ELMO_ENDPOINTS = (
    "analytics",
    "citation-domains",
    "query-fan-out",
    "opportunities",
    "prompt-performance",
)
# Read-only server functions from elmohq/elmo-web:0.3.0. Opportunities is
# deliberately absent: that UI GET regenerates stale reports using a provider.
ELMO_030_FUNCTIONS = {
    "analytics": "11c2deba98af4895020c451e07332ccef0c715ea70da26df5ac1b0c1ffca90d7",
    "citation-domains": "5641d455e2edf9b119c506bec5c19457cb537c15c6e0ba75dbd5946581f3a460",
    "query-fan-out": "bb2bb785d141880b1ce95e0f119182564c8b822f27d57519ab11f968738e8cda",
    "prompt-performance": "fa6e609692c85ea891db536dfa72149c08c748166ef79f1eb571f985da9ea8c0",
}


@dataclass(frozen=True)
class ElmoHTTPResponse:
    status: int
    body: str
    headers: dict[str, str]


class ElmoAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        credential_value: str = "",
        session_cookie: str = "",
        lookback: str = "1m",
        transport: Callable[..., Awaitable[ElmoHTTPResponse]] | None = None,
        timeout_seconds: float = 20,
        max_attempts: int = 2,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        validate_service_url(base_url)
        if not credential_value and not session_cookie:
            raise ElmoAdapterError("Elmo session or API credentials are missing")
        if credential_value and session_cookie:
            raise ElmoAdapterError("Choose one Elmo authentication mode")
        if any(c in credential_value + session_cookie for c in "\r\n"):
            raise ElmoAdapterError("Invalid Elmo credentials")
        if lookback not in {"1w", "1m", "3m", "6m", "1y"}:
            raise ElmoAdapterError("Unsupported Elmo lookback")
        if timeout_seconds <= 0 or max_response_bytes < 1:
            raise ElmoAdapterError("Elmo request limits must be positive")
        if max_attempts < 1:
            raise ElmoAdapterError("max_attempts must be at least 1")
        self.base_url = base_url.rstrip("/")
        self.credential_value = credential_value
        self.session_cookie = session_cookie
        self.lookback = lookback
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.max_response_bytes = max_response_bytes
        self.transport = transport or _default_transport(self.max_response_bytes)

    async def read_ai_visibility(
        self,
        *,
        brand_id: str,
        window_start: str,
        window_end: str,
        locale: str,
    ) -> AdapterResult:
        request = {
            "brand_id": brand_id,
            "window": {"start": window_start, "end": window_end},
            "locale": locale,
        }
        if self.session_cookie:
            request["effective_window"] = {"lookback": self.lookback, "timezone": "UTC", "relative": True}
            request["window_note"] = "Installed UI functions use relative windows; requested dates and locale are not applied"
        endpoints: dict[str, dict[str, Any]] = {}
        for endpoint in ELMO_ENDPOINTS:
            if endpoint == "opportunities":
                endpoints[endpoint] = {"status": "skipped", "reason": "provider_generation_not_allowed"}
                continue
            url = self._url(
                endpoint,
                brand_id=brand_id,
                window_start=window_start,
                window_end=window_end,
                locale=locale,
            )
            endpoint_result = await self._read_endpoint(endpoint, url)
            endpoints[endpoint] = endpoint_result
            if endpoint_result["status"] == "succeeded":
                payload = endpoint_result.get("payload")
                schema_error = _native_schema_error(endpoint, payload, session=bool(self.session_cookie))
                if not schema_error and not self.session_cookie:
                    schema_error = _identity_mismatch_error(
                        endpoint,
                        payload,
                        brand_id=brand_id,
                        window_start=window_start,
                        window_end=window_end,
                    )
                if schema_error:
                    return AdapterResult(
                        name="elmo.ai_visibility",
                        status="failed",
                        quality="missing",
                        source_status=self._source_status(request, endpoints),
                        error={"code": "ELMO_SCHEMA_ERROR", "message": schema_error},
                    )

        failed = [name for name, item in endpoints.items() if item["status"] != "succeeded"]
        succeeded = [item for item in endpoints.values() if item["status"] == "succeeded"]
        quality = "missing" if not succeeded else "partial" if failed else "complete"
        evidence = self._source_status(request, endpoints)
        if evidence["observation_count"] == 0:
            quality = "missing"
        return AdapterResult(
            name="elmo.ai_visibility",
            status="succeeded" if succeeded else "failed",
            quality=quality,
            source_status=evidence,
        )

    def _url(self, endpoint: str, *, brand_id: str, window_start: str, window_end: str, locale: str) -> str:
        if endpoint not in ELMO_ENDPOINTS:
            raise ElmoAdapterError("Elmo endpoint is not allowlisted for read-only import")
        if endpoint == "opportunities":
            raise ElmoAdapterError("Opportunities may execute a provider")
        if self.session_cookie:
            data: dict[str, Any] = {"brandId": brand_id}
            if endpoint == "citation-domains":
                data["days"] = {"1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365}[self.lookback]
            else:
                data.update(lookback=self.lookback, timezone="UTC")
            query = urllib.parse.urlencode({"payload": json.dumps(_seroval_encode({"data": data}))})
            return f"{self.base_url}/_serverFn/{ELMO_030_FUNCTIONS[endpoint]}?{query}"
        if not window_start or not window_end:
            raise ElmoAdapterError("Elmo REST analytics requires start and end timestamps")
        path = {"citation-domains": "citations/domains", "query-fan-out": "query-fanout"}.get(endpoint, endpoint)
        query = urllib.parse.urlencode(
            {
                "start": _rest_timestamp(window_start, exclusive_end=False),
                "end": _rest_timestamp(window_end, exclusive_end=True),
                "locale": locale,
            }
        )
        return f"{self.base_url}/api/v1/brands/{urllib.parse.quote(brand_id, safe='')}/{path}?{query}"

    async def _read_endpoint(self, endpoint: str, url: str) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.session_cookie:
            parsed = urllib.parse.urlsplit(self.base_url)
            headers.update({"Cookie": self.session_cookie, "Origin": f"{parsed.scheme}://{parsed.netloc}",
                            "x-tsr-serverFn": "true"})
        else:
            headers["Authorization"] = f"Bearer {self.credential_value}"
        last_error: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self.transport(url, headers=headers, timeout_seconds=self.timeout_seconds)
                if response.status != 200:
                    last_error = f"HTTP {response.status}"
                    if response.status < 500 and response.status != 429:
                        break
                    continue
                if len(response.body.encode("utf-8")) > self.max_response_bytes:
                    last_error = "response exceeded max_response_bytes"
                    continue
                payload = json.loads(response.body)
                if self.session_cookie and isinstance(payload, dict) and "t" in payload:
                    payload = _seroval_decode(payload)
                    if not isinstance(payload, dict) or payload.get("error") is not None:
                        raise ElmoAdapterError("Elmo server function failed")
                    payload = payload.get("result")
                if not isinstance(payload, dict):
                    last_error = "JSON payload must be an object"
                    continue
                return {"status": "succeeded", "attempts": attempt, "http_status": response.status, "payload": payload}
            except (
                ElmoAdapterError,
                TimeoutError,
                urllib.error.URLError,
                OSError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                RecursionError,
                TypeError,
                KeyError,
                AttributeError,
            ) as exc:
                last_error = f"Elmo read failed ({exc.__class__.__name__})"
        return {"status": "failed", "attempts": attempt, "error": last_error or "request failed"}

    def _source_status(self, request: dict[str, Any], endpoints: dict[str, dict[str, Any]]) -> dict[str, Any]:
        analytics = endpoints.get("analytics", {}).get("payload", {})
        totals = analytics.get("totals", {})
        count = analytics.get("totalRuns") if self.session_cookie else totals.get("runs") if isinstance(totals, dict) else None
        if not _valid_count(count):
            count = None
        for endpoint, payload_key, is_list in (
            ("query-fan-out", "totalQueries", False),
            ("citation-domains", "totalCitations", False),
            ("prompt-performance", "prompts", True),
        ):
            if count is not None:
                break
            payload = endpoints.get(endpoint, {}).get("payload", {})
            if not isinstance(payload, dict):
                continue
            if self.session_cookie:
                value = len(payload[payload_key]) if is_list and isinstance(payload.get(payload_key), list) else payload.get(payload_key)
            else:
                value = len(payload["data"]) if isinstance(payload.get("data"), list) else None
            if _valid_count(value):
                count = value
        if count is None:
            count = 0
        return {
            "provider": "elmo",
            "schema_version": 1,
            "pin": {"web": "0.3.0", "contract": "serverFn-0.3.0"} if self.session_cookie else {"contract": "rest-v1"},
            "auth": {"scheme": "session" if self.session_cookie else "bearer", "value": "[REDACTED]"},
            "request": request,
            "endpoints": endpoints,
            "observation_count": count,
            "availability": "available" if count else "no_measurements",
        }


def _valid_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _default_transport(max_response_bytes: int) -> Callable[..., Awaitable[ElmoHTTPResponse]]:
    async def transport(url: str, *, headers: dict[str, str], timeout_seconds: float) -> ElmoHTTPResponse:
        return await asyncio.to_thread(
            _blocking_urlopen_json,
            url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )

    return transport


def _blocking_urlopen_json(
    url: str, *, headers: dict[str, str], timeout_seconds: float, max_response_bytes: int = 2_000_000
) -> ElmoHTTPResponse:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=timeout_seconds) as response:
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise ElmoAdapterError("Elmo response exceeded max_response_bytes")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                body = raw.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                body = raw.decode("utf-8", errors="replace")
            return ElmoHTTPResponse(
                status=int(response.status),
                body=body,
                headers={str(key).lower(): str(value) for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        with exc:
            return ElmoHTTPResponse(status=int(exc.code), body="", headers={})


_BARE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _rest_timestamp(value: str, *, exclusive_end: bool) -> str:
    """The pinned REST API rejects a bare date: `start`/`end` must be ISO 8601
    timestamps and the range is half-open. A bare end date therefore becomes the
    start of the following day, so the requested day is still inside the window."""
    if not _BARE_DATE.match(value):
        return value
    day = date.fromisoformat(value)
    if exclusive_end:
        day += timedelta(days=1)
    return f"{day.isoformat()}T00:00:00Z"


def _native_schema_error(endpoint: str, payload: object, *, session: bool) -> str | None:
    if not isinstance(payload, dict):
        return f"{endpoint} schema mismatch"
    if session:
        required = {"analytics": {"totalPrompts", "totalRuns", "visibilityTimeSeries", "citationTimeSeries"},
                    "citation-domains": {"domainDistribution", "totalCitations"},
                    "query-fan-out": {"topQueries", "totalQueries", "byPrompt"},
                    "prompt-performance": {"prompts"}}[endpoint]
    else:
        required = {"brandId", "range"} | ({"data"} if endpoint != "analytics" else {"visibility"})
    if not required <= payload.keys():
        return f"{endpoint} schema mismatch"
    list_keys = ({"analytics": ["visibilityTimeSeries", "citationTimeSeries"],
                  "citation-domains": ["domainDistribution"], "query-fan-out": ["topQueries", "byPrompt"],
                  "prompt-performance": ["prompts"]}[endpoint] if session else
                 ["data"] if endpoint != "analytics" else [])
    if any(not isinstance(payload.get(key), list) for key in list_keys):
        return f"{endpoint} schema mismatch"
    if not session and (not isinstance(payload.get("range"), dict) or
                        (endpoint == "analytics" and not isinstance(payload.get("visibility"), dict))):
        return f"{endpoint} schema mismatch"
    return None


def _identity_mismatch_error(
    endpoint: str,
    payload: object,
    *,
    brand_id: str,
    window_start: str,
    window_end: str,
) -> str | None:
    if not isinstance(payload, dict):
        return None
    # `brandId` is required by the REST schema check that runs first, so anything
    # reaching here has the key. Compare by value without narrowing to `str`: a
    # non-string brandId is not the requested brand either, and letting it through
    # would make the identity gate fail open on exactly the misrouted payload it
    # exists to reject.
    if payload.get("brandId") != brand_id:
        return f"{endpoint} response brandId does not match requested brand_id"
    actual_range = payload.get("range")
    if not isinstance(actual_range, dict):
        return f"{endpoint} response range does not match requested window"
    expected_range = _expected_rest_range(window_start=window_start, window_end=window_end)
    actual_start = _parse_rest_timestamp(actual_range.get("start"))
    actual_end = _parse_rest_timestamp(actual_range.get("end"))
    expected_start = _parse_rest_timestamp(expected_range["start"])
    expected_end = _parse_rest_timestamp(expected_range["end"])
    if (actual_start is None or actual_end is None or expected_start is None or expected_end is None or
            actual_start != expected_start or actual_end != expected_end):
        return f"{endpoint} response range does not match requested window"
    return None


def _expected_rest_range(*, window_start: str, window_end: str) -> dict[str, str]:
    # Elmo REST v1 accepts request `start` as the inclusive timestamp and request
    # `end` as the exclusive timestamp, then echoes that accepted identity in
    # response `range.start` / `range.end`. Live endpoint checks for PR #2772
    # showed the provider does not shift the end itself: this adapter sends a
    # bare end date as the next midnight, so that one-day shift is legitimate.
    # Locale is intentionally not checked because REST responses do not confirm it.
    return {
        "start": _rest_timestamp(window_start, exclusive_end=False),
        "end": _rest_timestamp(window_end, exclusive_end=True),
    }


def _parse_rest_timestamp(value: object) -> datetime | None:
    """Parse an offset-aware ISO 8601 instant, rejecting ambiguous values."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _seroval_encode(value: dict[str, Any]) -> dict[str, Any]:
    """The installed TanStack GET accepts Seroval's JSON tree, not plain JSON."""
    index = 0

    def encode(item):
        nonlocal index
        if isinstance(item, str):
            escaped = json.dumps(item, ensure_ascii=False)[1:-1].replace("<", "\\x3C")
            return {"t": 1, "s": escaped}
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            return {"t": 0, "s": item}
        if item is None or isinstance(item, bool):
            return {"t": 2, "s": 0 if item is None else 2 if item else 3}
        identity = index
        index += 1
        if isinstance(item, list):
            return {"t": 9, "i": identity, "a": [encode(x) for x in item], "o": 0}
        if isinstance(item, dict):
            return {"t": 10, "i": identity, "p": {"k": list(item), "v": [encode(x) for x in item.values()]}, "o": 0}
        raise ElmoAdapterError("Unsupported server function input")

    return {"t": encode(value), "f": 127, "m": []}


def _seroval_decode(node: dict[str, Any]) -> Any:
    # Decode only data nodes. Never evaluate the JS serializer or plugin payloads.
    refs: dict[int, Any] = {}

    def decode(item, depth=0):
        if depth > 80 or not isinstance(item, dict):
            raise ElmoAdapterError("Unsupported Elmo response serialization")
        kind = item.get("t")
        if kind == 0:
            return item.get("s")
        if kind == 1:
            escaped = str(item.get("s", "")).replace("\\x3C", "\\u003c")
            return json.loads('"' + escaped + '"')
        if kind == 2 and item.get("s") in (0, 1, 2, 3):
            return {0: None, 1: None, 2: True, 3: False}[item["s"]]
        if kind == 5:
            return item.get("s")
        if kind == 4 and item.get("i") in refs:
            return refs[item["i"]]
        if kind in (10, 11):
            props = item.get("p", {})
            keys, values = props.get("k", []), props.get("v", [])
            if not isinstance(keys, list) or not isinstance(values, list) or len(keys) != len(values):
                raise ElmoAdapterError("Invalid Elmo object")
            result = {key: decode(value, depth + 1) for key, value in zip(keys, values) if isinstance(key, str)}
            refs[item.get("i")] = result
            return result
        if kind == 9:
            result = [decode(x, depth + 1) for x in item.get("a", [])]
            refs[item.get("i")] = result
            return result
        raise ElmoAdapterError("Unsupported Elmo response serialization or server error")

    return decode(node)
