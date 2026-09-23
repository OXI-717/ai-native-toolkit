from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from seo_observer.budget import BudgetGuard


DATAFORSEO_COMPETITORS_DOMAIN_ENDPOINT = "/v3/dataforseo_labs/google/competitors_domain/live"
DATAFORSEO_DOMAIN_INTERSECTION_ENDPOINT = "/v3/dataforseo_labs/google/domain_intersection/live"
DATAFORSEO_RANKED_KEYWORDS_ENDPOINT = "/v3/dataforseo_labs/google/ranked_keywords/live"
DATAFORSEO_SERP_GOOGLE_ORGANIC_ADVANCED_ENDPOINT = "/v3/serp/google/organic/live/advanced"
DATAFORSEO_USER_DATA_ENDPOINT = "/v3/user_data"
DATAFORSEO_DEFAULT_BASE_URL = "https://api.dataforseo.com"
SAFE_SECRET_KEYS = frozenset(
    {
        "login",
        "key",
        "secret",
        "api_key",
        "client_secret",
        "password",
        "token",
        "refresh_token",
        "access_token",
    }
)


class DataForSEOTransport(Protocol):
    def post_json(
        self,
        endpoint: str,
        *,
        json: Any,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        ...

    def get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        ...


class DataForSEOAuthError(ValueError):
    def to_error(self, *, endpoint: str | None = None) -> dict[str, Any]:
        return dataforseo_error(
            "DATAFORSEO_AUTH_NOT_READY",
            endpoint=endpoint,
            safe_message="DataForSEO credentials are not ready.",
            retryable=False,
            details={"reason": "missing_or_invalid_credentials"},
        )


@dataclass(frozen=True)
class DataForSEOAuth:
    login: str
    password: str


@dataclass(frozen=True)
class DataForSEOSource:
    credential_env: str
    auth_value: str | None = None
    endpoint: str = DATAFORSEO_DEFAULT_BASE_URL
    provider_mode: str = "artifact"
    allow_paid: bool = False
    per_run_budget_usd: float | None = None
    monthly_budget_usd: float | None = None


class DataForSEOAdapter:
    def __init__(
        self,
        source: DataForSEOSource,
        transport: DataForSEOTransport,
        *,
        env: dict[str, str] | None = None,
        budget_guard: BudgetGuard | None = None,
    ) -> None:
        self.source = source
        self.transport = transport
        self.env = env or {}
        self.budget_guard = budget_guard or BudgetGuard(
            command="dataforseo",
            project="__unknown__",
            provider="dataforseo",
            provider_mode=source.provider_mode,
            allow_paid=source.allow_paid,
            planned_calls=1,
            per_run_budget_usd=source.per_run_budget_usd,
            monthly_budget_usd=source.monthly_budget_usd,
        )

    def fetch_competitors_domain(
        self,
        target: str,
        location_code: int | str | None = None,
        language_code: str | None = None,
        limit: int | None = None,
        *,
        location_name: str | None = None,
        exclude_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = _payload_with_location(
            {
                "target": target,
                "language_code": language_code,
                "limit": limit,
            },
            location_code=location_code,
            location_name=location_name,
        )
        return self._post(
            DATAFORSEO_COMPETITORS_DOMAIN_ENDPOINT,
            payload,
            collection="competitor_candidates",
            parser=lambda items: _parse_competitor_rows(items),
        )

    def fetch_domain_intersection(
        self,
        target1: str,
        target2: str,
        location_code: int | str | None = None,
        language_code: str | None = None,
        limit: int | None = None,
        *,
        location_name: str | None = None,
    ) -> dict[str, Any]:
        payload = _payload_with_location(
            {
                "target1": target1,
                "target2": target2,
                "language_code": language_code,
                "limit": limit,
                "include_serp_info": True,
            },
            location_code=location_code,
            location_name=location_name,
        )
        return self._post(
            DATAFORSEO_DOMAIN_INTERSECTION_ENDPOINT,
            payload,
            collection="keyword_gap_rows",
            parser=lambda items: _parse_intersection_rows(items, competitor_domain=target2),
        )

    def fetch_ranked_keywords(
        self,
        target: str,
        location_code: int | str | None = None,
        language_code: str | None = None,
        limit: int | None = None,
        *,
        location_name: str | None = None,
    ) -> dict[str, Any]:
        payload = _payload_with_location(
            {
                "target": target,
                "language_code": language_code,
                "limit": limit,
                "include_serp_info": True,
            },
            location_code=location_code,
            location_name=location_name,
        )
        return self._post(
            DATAFORSEO_RANKED_KEYWORDS_ENDPOINT,
            payload,
            collection="ranked_keywords",
            parser=lambda items: _parse_ranked_keyword_rows(items, ranked_domain=target),
        )

    def fetch_organic_serp(
        self,
        keyword: str,
        location_code: int | str | None = None,
        location_name: str | None = None,
        language_code: str | None = None,
        device: str = "desktop",
        depth: int = 10,
    ) -> dict[str, Any]:
        """Engine-neutral name of the `SerpProviderAdapter` seam.

        DataForSEO SERPs are always Google, so there is a single implementation;
        the separate name exists so the audit loop does not need to know which
        engine the market is built on.
        """
        return self.fetch_google_organic_serp(
            keyword, location_code, location_name, language_code, device, depth
        )

    def fetch_google_organic_serp(
        self,
        keyword: str,
        location_code: int | str | None = None,
        location_name: str | None = None,
        language_code: str | None = None,
        device: str = "desktop",
        depth: int = 10,
    ) -> dict[str, Any]:
        payload = _payload_with_location(
            {
                "keyword": keyword,
                "language_code": language_code,
                "device": device,
                "depth": depth,
            },
            location_code=location_code,
            location_name=location_name,
        )
        return self._post(
            DATAFORSEO_SERP_GOOGLE_ORGANIC_ADVANCED_ENDPOINT,
            payload,
            collection="serp_results",
            parser=lambda items: _parse_serp_rows(items, keyword=keyword),
        )

    def fetch_user_data(self) -> dict[str, Any]:
        return self._get(
            DATAFORSEO_USER_DATA_ENDPOINT,
            collection="provider_account",
            parser=lambda items: [dict(item) for item in items if isinstance(item, dict)],
        )

    def sanitized_request_envelope(self, method: str, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = _sanitize_mapping(payload)
        return {
            "method": method,
            "endpoint_path": endpoint,
            "headers_included": False,
            "payload_shape": {key: _shape(value) for key, value in payload.items() if value is not None},
            "payload": sanitized,
        }

    def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        collection: str,
        parser,
    ) -> dict[str, Any]:
        return self._request("POST", endpoint, payload, collection=collection, parser=parser)

    def _get(
        self,
        endpoint: str,
        *,
        collection: str,
        parser,
    ) -> dict[str, Any]:
        return self._request("GET", endpoint, {}, collection=collection, parser=parser)

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any],
        *,
        collection: str,
        parser,
    ) -> dict[str, Any]:
        envelope = self.sanitized_request_envelope(method, endpoint, payload)
        preflight_error = self.budget_guard.preflight()
        if preflight_error is not None:
            return self._blocked_result(collection, endpoint, envelope, preflight_error)
        try:
            auth = self._auth()
        except DataForSEOAuthError as exc:
            return self._blocked_result(collection, endpoint, envelope, exc.to_error(endpoint=endpoint))
        headers = {"Authorization": f"Basic {_basic_payload(auth)}"}
        try:
            if method == "GET":
                page = self.transport.get_json(endpoint, params={}, headers=headers)
            else:
                page = self.transport.post_json(endpoint, json=[payload], headers=headers)
        except Exception:
            return self._response(
                collection=collection,
                endpoint=endpoint,
                request=envelope,
                rows=[],
                metadata=_empty_metadata("partial"),
                cost=_cost(0.0),
                errors=[
                    dataforseo_error(
                        "DATAFORSEO_PROVIDER_ERROR",
                        endpoint=endpoint,
                        safe_message="DataForSEO transport failed; details redacted.",
                        retryable=True,
                        details={},
                    )
                ],
            )
        return self._parse_response(collection, endpoint, envelope, page, parser)

    def _parse_response(
        self,
        collection: str,
        endpoint: str,
        envelope: dict[str, Any],
        page: dict[str, Any],
        parser,
    ) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        if not isinstance(page, dict):
            page = {}
        tasks = page.get("tasks")
        if not isinstance(tasks, list):
            errors.append(
                dataforseo_error(
                    "DATAFORSEO_RESPONSE_INVALID",
                    endpoint=endpoint,
                    safe_message="DataForSEO response did not contain tasks.",
                    retryable=False,
                    details={},
                )
            )
            result = self._response(
                collection=collection,
                endpoint=endpoint,
                request=envelope,
                rows=[],
                metadata=_empty_metadata("partial"),
                cost=_cost(_float(page.get("cost"))),
                errors=errors,
            )
            self.budget_guard.record_provider_cost(result["cost"]["provider_cost_usd"])
            return result
        provider_request_ids: list[str] = []
        status_codes: list[int] = []
        item_rows: list[dict[str, Any]] = []
        response_cost = _float(page.get("cost"))
        task_cost = 0.0
        no_result_tasks = 0
        for task in tasks:
            if not isinstance(task, dict):
                errors.append(
                    dataforseo_error(
                        "DATAFORSEO_RESPONSE_INVALID",
                        endpoint=endpoint,
                        safe_message="DataForSEO task had an invalid shape.",
                        retryable=False,
                        details={},
                    )
                )
                continue
            provider_request_ids.append(str(task.get("id") or ""))
            status_code = _int(task.get("status_code"))
            if status_code is not None:
                status_codes.append(status_code)
            task_cost += _float(task.get("cost"))
            if status_code is not None and status_code >= 40000:
                details: dict[str, Any] = {"provider_status_code": status_code}
                status_message = task.get("status_message")
                if isinstance(status_message, str) and status_message:
                    details["provider_status_message"] = status_message
                errors.append(
                    dataforseo_error(
                        "DATAFORSEO_PROVIDER_ERROR",
                        endpoint=endpoint,
                        safe_message="DataForSEO task returned an error.",
                        retryable=status_code >= 50000,
                        details=details,
                    )
                )
                continue
            result_parts = task.get("result")
            if not isinstance(result_parts, list) or not result_parts:
                no_result_tasks += 1
                continue
            for part in result_parts:
                if isinstance(part, dict) and isinstance(part.get("items"), list):
                    item_rows.extend(part["items"])
                elif isinstance(part, dict) and endpoint == DATAFORSEO_USER_DATA_ENDPOINT:
                    item_rows.append(part)
                elif isinstance(part, dict):
                    # Some account endpoints return result objects directly; unsupported
                    # object shapes are left to parser-specific malformed accounting.
                    item_rows.append(part)
                else:
                    errors.append(
                        dataforseo_error(
                            "DATAFORSEO_RESPONSE_INVALID",
                            endpoint=endpoint,
                            safe_message="DataForSEO result part had an invalid shape.",
                            retryable=False,
                            details={},
                        )
                    )
        rows, malformed_count, deduped_count = _parse_rows(parser, item_rows)
        provider_cost = round(response_cost + task_cost, 6)
        quality = "live"
        if errors or malformed_count or no_result_tasks:
            quality = "partial"
        metadata = {
            "provider_request_ids": [item for item in provider_request_ids if item],
            "status_codes": status_codes,
            "malformed_row_count": malformed_count,
            "deduped_row_count": deduped_count,
            "result_part_count": len(item_rows),
            "no_result_task_count": no_result_tasks,
            "response_cost_usd": response_cost,
            "task_cost_usd": round(task_cost, 6),
            "dataset_coverage": "empty" if not rows else "complete",
            "quality": quality,
        }
        result = self._response(
            collection=collection,
            endpoint=endpoint,
            request=envelope,
            rows=rows,
            metadata=metadata,
            cost=_cost(provider_cost),
            errors=errors,
        )
        runtime_error = self.budget_guard.record_provider_cost(provider_cost)
        if runtime_error is not None:
            result["errors"].append(runtime_error)
            result["quality"] = "partial"
            result["metadata"]["quality"] = "partial"
        return result

    def _response(
        self,
        *,
        collection: str,
        endpoint: str,
        request: dict[str, Any],
        rows: list[dict[str, Any]],
        metadata: dict[str, Any],
        cost: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "ok": not errors,
            "collection": collection,
            "provider": "dataforseo",
            "endpoint": endpoint,
            "request": request,
            "rows": rows,
            "metadata": metadata,
            "cost": cost,
            "quality": metadata["quality"],
            "errors": errors,
        }

    def _blocked_result(
        self,
        collection: str,
        endpoint: str,
        request: dict[str, Any],
        error: dict[str, Any],
    ) -> dict[str, Any]:
        error = {**error, "endpoint": endpoint}
        return self._response(
            collection=collection,
            endpoint=endpoint,
            request=request,
            rows=[],
            metadata=_empty_metadata("blocked"),
            cost=_cost(0.0),
            errors=[error],
        )

    def _auth(self) -> DataForSEOAuth:
        env = dict(self.env)
        if self.source.auth_value is not None:
            env[self.source.credential_env] = self.source.auth_value
        return parse_dataforseo_auth(self.source.credential_env, env)


def parse_dataforseo_auth(credential_env: str, env: dict[str, str]) -> DataForSEOAuth:
    value = env.get(credential_env, "")
    if not isinstance(value, str) or not value.strip():
        raise DataForSEOAuthError("DataForSEO credentials are not ready.")
    raw = value.strip()
    candidates = [raw]
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        decoded = ""
    if decoded:
        candidates.insert(0, decoded)
    for candidate in candidates:
        if candidate.count(":") == 1:
            login, password = candidate.split(":", 1)
            if _valid_auth_part(login) and _valid_auth_part(password):
                return DataForSEOAuth(login=login, password=password)
    raise DataForSEOAuthError("DataForSEO credentials are not ready.")


def dataforseo_error(
    code: str,
    *,
    endpoint: str | None,
    safe_message: str,
    retryable: bool,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "provider": "dataforseo",
        "endpoint": endpoint,
        "safe_message": safe_message,
        "retryable": retryable,
        "details": _sanitize_mapping(details),
    }


def _payload_with_location(
    payload: dict[str, Any],
    *,
    location_code: int | str | None,
    location_name: str | None,
) -> dict[str, Any]:
    clean = {key: value for key, value in payload.items() if value is not None}
    if location_name is not None:
        clean["location_name"] = location_name
    if location_code is not None:
        clean["location_code"] = location_code
    return clean


def _parse_competitor_rows(items: list[Any]) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            malformed += 1
            continue
        domain = _clean_str(item.get("domain"))
        if not domain:
            malformed += 1
            continue
        if domain in seen:
            continue
        seen.add(domain)
        metrics = item.get("full_domain_metrics") if isinstance(item.get("full_domain_metrics"), dict) else {}
        organic = metrics.get("organic") if isinstance(metrics.get("organic"), dict) else {}
        rows.append(
            {
                "domain": domain,
                "intersections": _int(item.get("intersections")) or 0,
                "organic_keywords": _int(organic.get("count")) or 0,
                "estimated_traffic": _float(organic.get("etv")),
                "avg_position": _float(item.get("avg_position")),
                "rank_distribution": {
                    key: value
                    for key, value in organic.items()
                    if isinstance(key, str) and key.startswith("pos_") and _int(value) is not None
                },
                "source": "dataforseo",
                "quality": "live",
            }
        )
    return rows, malformed, max(0, len(items) - malformed - len(rows))


def _parse_intersection_rows(items: list[Any], *, competitor_domain: str) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            malformed += 1
            continue
        keyword_data = item.get("keyword_data") if isinstance(item.get("keyword_data"), dict) else {}
        keyword = _clean_str(keyword_data.get("keyword"))
        serp = item.get("second_domain_serp_element") if isinstance(item.get("second_domain_serp_element"), dict) else {}
        rank = _int(serp.get("rank_group"))
        if not keyword or rank is None:
            malformed += 1
            continue
        if keyword in seen:
            continue
        seen.add(keyword)
        keyword_info = keyword_data.get("keyword_info") if isinstance(keyword_data.get("keyword_info"), dict) else {}
        rows.append(
            {
                "keyword": keyword,
                "search_volume": _int(keyword_info.get("search_volume")),
                "cpc": _float(keyword_info.get("cpc")),
                "competitor_domain": competitor_domain,
                "competitor_rank": rank,
                "competitor_url": _clean_str(serp.get("url")),
                "our_domain_present": item.get("first_domain_serp_element") is not None,
                "source": "dataforseo",
                "quality": "live",
            }
        )
    return rows, malformed, max(0, len(items) - malformed - len(rows))


def _parse_ranked_keyword_rows(items: list[Any], *, ranked_domain: str) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            malformed += 1
            continue
        keyword_data = item.get("keyword_data") if isinstance(item.get("keyword_data"), dict) else {}
        serp = item.get("ranked_serp_element") if isinstance(item.get("ranked_serp_element"), dict) else {}
        keyword = _clean_str(keyword_data.get("keyword"))
        if not keyword:
            malformed += 1
            continue
        if keyword in seen:
            continue
        seen.add(keyword)
        keyword_info = keyword_data.get("keyword_info") if isinstance(keyword_data.get("keyword_info"), dict) else {}
        rows.append(
            {
                "keyword": keyword,
                "search_volume": _int(keyword_info.get("search_volume")),
                "cpc": _float(keyword_info.get("cpc")),
                "ranked_domain": _clean_str(serp.get("domain")) or ranked_domain,
                "rank_group": _int(serp.get("rank_group")),
                "rank_absolute": _int(serp.get("rank_absolute")),
                "url": _clean_str(serp.get("url")),
                "source": "dataforseo",
                "quality": "live",
            }
        )
    return rows, malformed, max(0, len(items) - malformed - len(rows))


def _parse_serp_rows(items: list[Any], *, keyword: str) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    for item in items:
        if not isinstance(item, dict):
            malformed += 1
            continue
        row_keyword = _clean_str(item.get("keyword")) or keyword
        row = _parse_serp_row(item, keyword=row_keyword)
        if row is not None:
            rows.append(row)
            continue
        nested = item.get("items") if isinstance(item.get("items"), list) else None
        if not nested:
            malformed += 1
            continue
        nested_rows = 0
        for serp in nested:
            if not isinstance(serp, dict):
                malformed += 1
                continue
            row = _parse_serp_row(serp, keyword=row_keyword)
            if row is None:
                continue
            rows.append(row)
            nested_rows += 1
        if nested_rows == 0 and not (_clean_str(item.get("type")) or _clean_str(item.get("result_type"))):
            malformed += 1
    return rows, malformed, 0


def _parse_serp_row(serp: dict[str, Any], *, keyword: str) -> dict[str, Any] | None:
    result_type = _clean_str(serp.get("type")) or _clean_str(serp.get("result_type"))
    rank_absolute = _int(serp.get("rank_absolute"))
    if not result_type or rank_absolute is None:
        return None
    return {
        "keyword": keyword,
        "rank_group": _int(serp.get("rank_group")),
        "rank_absolute": rank_absolute,
        "result_type": result_type,
        "domain": _clean_str(serp.get("domain")),
        "url": _clean_str(serp.get("url")),
        "title": _clean_str(serp.get("title")),
        "description": _clean_str(serp.get("description")),
        "source": "dataforseo",
        "quality": "live",
    }


def _parse_rows(parser, items: list[Any]) -> tuple[list[dict[str, Any]], int, int]:
    try:
        parsed = parser(items)
    except (TypeError, ValueError, KeyError):
        return [], len(items), 0
    if isinstance(parsed, tuple) and len(parsed) == 3:
        return parsed
    if isinstance(parsed, list):
        return parsed, 0, 0
    return [], len(items), 0


def _empty_metadata(quality: str) -> dict[str, Any]:
    return {
        "provider_request_ids": [],
        "status_codes": [],
        "malformed_row_count": 0,
        "deduped_row_count": 0,
        "quality": quality,
    }


def _cost(provider_cost_usd: float) -> dict[str, Any]:
    return {
        "provider_cost_usd": round(float(provider_cost_usd), 6),
        "estimated_cost_usd": None,
        "currency": "USD",
    }


def _basic_payload(auth: DataForSEOAuth) -> str:
    return base64.b64encode(f"{auth.login}:{auth.password}".encode("utf-8")).decode("ascii")


def _valid_auth_part(value: str) -> bool:
    return bool(value) and "\n" not in value and "\r" not in value and " " not in value


def _sanitize_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _sanitize_value(str(key), item) for key, item in value.items() if item is not None}


def _sanitize_value(key: str, value: Any) -> Any:
    if key.lower() in SAFE_SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return _sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_value(key, item) for item in value]
    if isinstance(value, str):
        return _redact_query_secrets(value)
    return value


def _redact_query_secrets(value: str) -> str:
    parts = urlsplit(value)
    if not parts.query:
        return value
    redacted = []
    changed = False
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in SAFE_SECRET_KEYS:
            redacted.append((key, "[REDACTED]"))
            changed = True
        else:
            redacted.append((key, item))
    if not changed:
        return value
    query = "&".join(f"{quote(key, safe='')}={quote(item, safe='[]')}" for key, item in redacted)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _shape(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
