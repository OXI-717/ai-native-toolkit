from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


DEFAULT_LIMIT = 500
BROKEN_INTERNAL_LINKS_LIMIT_MAX = 100
SEARCH_URL_EVENT_SAMPLES_LIMIT_MAX = 100
POPULAR_QUERIES_ENDPOINT = "search-queries/popular"
QUERY_INDICATORS = ("TOTAL_SHOWS", "TOTAL_CLICKS", "AVG_SHOW_POSITION")
HOST_ID_PREFIXES = ("http:", "https:")


class WebmasterRequestError(ValueError):
    pass


class WebmasterTransport(Protocol):
    def get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class WebmasterPeriod:
    date_from: str
    date_to: str

    def validate(self) -> None:
        if not self.date_from or not self.date_to:
            raise WebmasterRequestError(
                "Yandex Webmaster temporal API calls require explicit date_from/date_to."
            )


@dataclass(frozen=True)
class WebmasterSource:
    user_id: str
    host_id: str
    token: str
    property_id: str
    timezone: str
    limit: int = DEFAULT_LIMIT
    finalize_after: str | None = None


class WebmasterAdapter:
    def __init__(self, source: WebmasterSource, transport: WebmasterTransport) -> None:
        validate_host_id(source.host_id)
        self.source = source
        self.transport = transport

    def fetch_popular_queries(
        self,
        period: WebmasterPeriod | None,
        *,
        device: str = "ALL",
        order_by: str = "TOTAL_SHOWS",
    ) -> dict[str, Any]:
        period = _require_period(period)
        request = self._request(
            POPULAR_QUERIES_ENDPOINT,
            params={
                "order_by": order_by,
                "query_indicator": list(QUERY_INDICATORS),
                "device_type_indicator": device,
                "date_from": period.date_from,
                "date_to": period.date_to,
                "offset": 0,
                "limit": self.source.limit,
            },
        )
        pages = self._fetch_offset_pages(request, "queries", "count")
        metadata = _metadata(
            _rows(pages, "queries"),
            _total_rows(pages, "queries", "count"),
            pages_received=len(pages),
            finalize_after=self.source.finalize_after,
        )
        observations = [
            self._query_observation(row, period, device, metadata) for row in _rows(pages, "queries")
        ]
        return {"collection": "search_performance", "metadata": metadata, "observations": observations}

    def fetch_query_history(
        self,
        period: WebmasterPeriod | None,
        *,
        query_id: str,
        query_text: str | None = None,
        device: str = "ALL",
    ) -> dict[str, Any]:
        period = _require_period(period)
        if not query_id:
            raise WebmasterRequestError("Yandex Webmaster query history requires query_id.")
        request = self._request(
            f"search-queries/{query_id}/history",
            params={
                "query_indicator": list(QUERY_INDICATORS),
                "device_type_indicator": device,
                "date_from": period.date_from,
                "date_to": period.date_to,
            },
        )
        page = self._fetch(request)
        rows = _history_query_rows(page, query_id, query_text)
        metadata = _metadata(
            rows,
            len(rows),
            pages_received=1,
            finalize_after=self.source.finalize_after,
        )
        observations = [self._query_observation(row, period, device, metadata) for row in rows]
        return {"collection": "search_performance", "metadata": metadata, "observations": observations}

    def fetch_indexing_history(self, period: WebmasterPeriod | None) -> dict[str, Any]:
        period = _require_period(period)
        request = self._request(
            "indexing/history",
            params={"date_from": period.date_from, "date_to": period.date_to},
        )
        page = self._fetch(request)
        observations: list[dict[str, Any]] = []
        partial = False
        indicators = page.get("indicators", {})
        if not isinstance(indicators, dict):
            raise WebmasterRequestError("Yandex Webmaster indexing response indicators must be an object.")
        for state, points in indicators.items():
            if not isinstance(points, list):
                partial = True
                continue
            for point in points:
                if not isinstance(point, dict) or point.get("date") is None or point.get("value") is None:
                    partial = True
                    continue
                observations.append(
                    {
                        **self._base_point_observation(str(point["date"])),
                        "index_state": str(state),
                        "searchable_pages": _int_or_none(point.get("value")),
                        "excluded_pages": None,
                        "submitted_urls": None,
                        "indexed_urls": None,
                        "problem_count": None,
                        "dataset_coverage": "complete",
                        "freshness": _freshness(self.source.finalize_after),
                        "comparability": "comparable",
                        "normalizer_version": "webmaster-v1",
                    }
                )
        metadata = _point_metadata(
            observations,
            partial=partial,
            finalize_after=self.source.finalize_after,
        )
        for observation in observations:
            observation["dataset_coverage"] = metadata["dataset_coverage"]
            observation["freshness"] = metadata["freshness"]
        return {"collection": "index_coverage", "metadata": metadata, "observations": observations}

    def fetch_sitemaps(self, *, effective_at: str, user_added: bool = False) -> dict[str, Any]:
        endpoint = "user-added-sitemaps" if user_added else "sitemaps"
        request = self._request(endpoint, params={})
        page = self._fetch(request)
        sitemap_rows = _rows([page], "sitemaps")
        observations = [
            self._sitemap_observation(row, effective_at) for row in sitemap_rows
        ]
        metadata = _metadata(
            sitemap_rows,
            len(sitemap_rows),
            pages_received=1,
            finalize_after=self.source.finalize_after,
        )
        for observation in observations:
            observation["dataset_coverage"] = metadata["dataset_coverage"]
            observation["freshness"] = metadata["freshness"]
        return {"collection": "index_coverage", "metadata": metadata, "observations": observations}

    def fetch_diagnostics(self, *, effective_at: str) -> dict[str, Any]:
        page = self._fetch(self._request("diagnostics", params={}))
        problems = page.get("problems", page.get("diagnostics", []))
        if not isinstance(problems, list):
            problems = []
        observations = []
        for problem in problems:
            if not isinstance(problem, dict):
                continue
            severity = str(problem.get("severity") or problem.get("type") or "__all__")
            code = str(problem.get("name") or problem.get("code") or "__all__")
            observations.append(
                {
                    **self._base_point_observation(effective_at),
                    "index_state": "diagnostic_problem",
                    "problem_severity": severity,
                    "problem_code": code,
                    "sitemap_id": "__all__",
                    "searchable_pages": None,
                    "excluded_pages": None,
                    "submitted_urls": None,
                    "indexed_urls": None,
                    "problem_count": 1,
                    "dataset_coverage": "complete",
                    "freshness": _freshness(self.source.finalize_after),
                    "comparability": "comparable",
                    "normalizer_version": "webmaster-v1",
                }
            )
        metadata = _metadata(
            observations,
            len(observations),
            pages_received=1,
            finalize_after=self.source.finalize_after,
        )
        for observation in observations:
            observation["dataset_coverage"] = metadata["dataset_coverage"]
            observation["freshness"] = metadata["freshness"]
        return {"collection": "index_coverage", "metadata": metadata, "observations": observations}

    def fetch_broken_internal_links(self, *, effective_at: str) -> dict[str, Any]:
        limit = max(1, min(int(self.source.limit), BROKEN_INTERNAL_LINKS_LIMIT_MAX))
        page = self._fetch(self._request("links/internal/broken/samples", params={"offset": 0, "limit": limit}))
        if page.get("status") == "unsupported":
            return {
                "collection": "index_coverage",
                "metadata": {
                    "dataset_coverage": "unavailable",
                    "freshness": "stale",
                    "comparability": "population_mismatch",
                    "rows_received": 0,
                    "total_rows": 0,
                    "pages_received": 1,
                    "finalize_after": self.source.finalize_after,
                    "unsupported_reason": "unsupported",
                },
                "observations": [],
            }
        links = page.get("links", page.get("samples", []))
        if not isinstance(links, list):
            links = []
        observations = [
            {
                **self._base_point_observation(effective_at),
                "index_state": "broken_internal_link",
                "problem_severity": "__all__",
                "problem_code": str(link.get("indicator") or link.get("error_code") or "__all__")
                if isinstance(link, dict)
                else "__all__",
                "sitemap_id": "__all__",
                "searchable_pages": None,
                "excluded_pages": None,
                "submitted_urls": None,
                "indexed_urls": None,
                "problem_count": 1,
                "dataset_coverage": "complete",
                "freshness": _freshness(self.source.finalize_after),
                "comparability": "comparable",
                "normalizer_version": "webmaster-v1",
            }
            for link in links
        ]
        metadata = _metadata(
            observations,
            len(observations),
            pages_received=1,
            finalize_after=self.source.finalize_after,
        )
        for observation in observations:
            observation["dataset_coverage"] = metadata["dataset_coverage"]
            observation["freshness"] = metadata["freshness"]
        return {"collection": "index_coverage", "metadata": metadata, "observations": observations}

    def fetch_search_url_event_samples(self, *, effective_at: str) -> dict[str, Any]:
        limit = max(1, min(int(self.source.limit), SEARCH_URL_EVENT_SAMPLES_LIMIT_MAX))
        request = self._request("search-urls/events/samples", params={"offset": 0, "limit": limit})
        pages = self._fetch_offset_pages(request, "samples", "count")
        sample_rows = _rows(pages, "samples")
        observations = [
            self._search_url_event_sample_observation(row, effective_at) for row in sample_rows
        ]
        metadata = _metadata(
            sample_rows,
            _total_rows(pages, "samples", "count"),
            pages_received=len(pages),
            finalize_after=self.source.finalize_after,
        )
        for observation in observations:
            observation["dataset_coverage"] = metadata["dataset_coverage"]
            observation["freshness"] = metadata["freshness"]
            observation["comparability"] = metadata["comparability"]
        return {"collection": "index_coverage", "metadata": metadata, "observations": observations}

    def _request(self, endpoint: str, *, params: dict[str, Any]) -> dict[str, Any]:
        return {"endpoint": f"/v4/user/{self.source.user_id}/hosts/{self.source.host_id}/{endpoint}", "params": params}

    def _fetch(self, request: dict[str, Any]) -> dict[str, Any]:
        page = self.transport.get_json(
            request["endpoint"],
            params=request["params"],
            headers={"Authorization": f"OAuth {self.source.token}"},
        )
        if not isinstance(page, dict):
            raise WebmasterRequestError("Yandex Webmaster transport returned a non-object response.")
        return page

    def _fetch_offset_pages(
        self,
        request: dict[str, Any],
        rows_key: str,
        total_key: str,
    ) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        offset = int(request["params"]["offset"])
        limit = int(request["params"]["limit"])
        while True:
            page = self._fetch({**request, "params": {**request["params"], "offset": offset}})
            pages.append(page)
            rows_received = len(_rows(pages, rows_key))
            total_rows = _total_rows(pages, rows_key, total_key)
            if rows_received >= total_rows or len(_rows([page], rows_key)) < limit:
                break
            offset += limit
        return pages

    def _query_observation(
        self,
        row: dict[str, Any],
        period: WebmasterPeriod,
        device: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        indicators = row.get("indicators", {})
        if not isinstance(indicators, dict):
            indicators = {}
        impressions = _number_or_none(indicators.get("TOTAL_SHOWS"))
        clicks = _number_or_none(indicators.get("TOTAL_CLICKS"))
        position = _number_or_none(indicators.get("AVG_SHOW_POSITION"))
        unavailable_metrics = [
            metric for metric in QUERY_INDICATORS if _number_or_none(indicators.get(metric)) is None
        ]
        metric_anomalies = []
        if impressions is not None and clicks is not None and clicks > impressions:
            metric_anomalies.append("clicks_exceed_impressions")
        ctr = None
        if impressions is not None and clicks is not None and impressions > 0:
            ctr = clicks / impressions
        coverage = "unavailable" if unavailable_metrics else metadata["dataset_coverage"]
        return {
            "project_id": "__pending__",
            "property_id": self.source.property_id,
            "source": "yandex_webmaster",
            "effective_start": period.date_from,
            "effective_end": period.date_to,
            "source_timezone": self.source.timezone,
            "query_id": str(row.get("query_id") or "__all__"),
            "query_text": str(row.get("query_text") or "__all__"),
            "page_id": "__all__",
            "page_url": "__all__",
            "search_engine": "yandex",
            "device": _device(device),
            "country": "__all__",
            "region": "__all__",
            "segment_id": "__all__",
            "impressions": int(impressions) if impressions is not None else None,
            "clicks": int(clicks) if clicks is not None else None,
            "ctr": ctr,
            "average_position": position,
            "dataset_coverage": coverage,
            "freshness": metadata["freshness"],
            "comparability": "population_mismatch" if metric_anomalies else "comparable",
            "sampled": False,
            "sample_share": None,
            "unavailable_dimensions": ["page"],
            "unavailable_metrics": unavailable_metrics,
            "metric_anomalies": metric_anomalies,
            "normalizer_version": "webmaster-v1",
        }

    def _base_point_observation(self, effective_at: str) -> dict[str, Any]:
        return {
            "project_id": "__pending__",
            "property_id": self.source.property_id,
            "source": "yandex_webmaster",
            "effective_at": effective_at,
            "source_timezone": self.source.timezone,
            "problem_severity": "__all__",
            "problem_code": "__all__",
            "sitemap_id": "__all__",
        }

    def _sitemap_observation(self, row: dict[str, Any], effective_at: str) -> dict[str, Any]:
        return {
            **self._base_point_observation(effective_at),
            "index_state": "sitemap",
            "sitemap_id": str(row.get("sitemap_id") or "__all__"),
            "sitemap_url": str(row.get("sitemap_url") or ""),
            "sitemap_sources": list(row.get("sources", [])) if isinstance(row.get("sources"), list) else [],
            "sitemap_type": str(row.get("sitemap_type") or "__all__"),
            "searchable_pages": None,
            "excluded_pages": None,
            "submitted_urls": _int_or_none(row.get("urls_count")),
            "indexed_urls": None,
            "problem_count": _int_or_none(row.get("errors_count")),
            "dataset_coverage": "complete",
            "freshness": _freshness(self.source.finalize_after),
            "comparability": "comparable",
            "unavailable_metrics": ["indexed_urls"],
            "normalizer_version": "webmaster-v1",
        }

    def _search_url_event_sample_observation(self, row: dict[str, Any], effective_at: str) -> dict[str, Any]:
        event = str(row.get("event") or "__all__")
        excluded_status = str(row.get("excluded_url_status") or "__all__")
        problem_code = excluded_status if excluded_status != "__all__" else event
        return {
            **self._base_point_observation(effective_at),
            "index_state": event,
            "problem_severity": "removed_from_search" if event == "REMOVED_FROM_SEARCH" else "__all__",
            "problem_code": problem_code,
            "search_url": str(row.get("url") or ""),
            "target_url": str(row.get("target_url") or ""),
            "title": str(row.get("title") or ""),
            "event_date": str(row.get("event_date") or ""),
            "last_access": str(row.get("last_access") or ""),
            "bad_http_status": _int_or_none(row.get("bad_http_status")),
            "searchable_pages": 1 if event == "APPEARED_IN_SEARCH" else None,
            "excluded_pages": 1 if event == "REMOVED_FROM_SEARCH" else None,
            "submitted_urls": None,
            "indexed_urls": None,
            "problem_count": 1 if event == "REMOVED_FROM_SEARCH" else None,
            "dataset_coverage": "complete",
            "freshness": _freshness(self.source.finalize_after),
            "comparability": "comparable",
            "normalizer_version": "webmaster-v1",
        }


def doctor_webmaster_source(
    source_fields: dict[str, Any],
    *,
    bindings: list[dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
    live: bool = False,
) -> dict[str, Any]:
    env = env or {}
    errors: list[str] = []
    user_id = source_fields.get("user_id")
    credential_env = source_fields.get("credential_env")
    config_shape = True
    if not isinstance(user_id, str) or not user_id:
        errors.append("user_id is required")
        config_shape = False
    if credential_env is not None and (not isinstance(credential_env, str) or not credential_env):
        errors.append("credential_env must be a non-empty string")
        config_shape = False
    bindings_valid = True
    for binding in bindings or []:
        remote_id = binding.get("remote_id") if isinstance(binding, dict) else None
        if not isinstance(remote_id, str) or not validate_host_id(remote_id, raise_error=False):
            bindings_valid = False
            errors.append("yandex_webmaster source binding remote_id must be a host-id string")
    token_present = bool(credential_env and env.get(credential_env))
    if not token_present:
        errors.append("credential token is not present")
    return {
        "ok": config_shape and bindings_valid and token_present,
        "live_checked": bool(live and False),
        "checks": {
            "config_shape": config_shape,
            "bindings_valid": bindings_valid,
            "token_present": token_present,
        },
        "errors": errors,
    }


def validate_host_id(value: str, *, raise_error: bool = True) -> bool:
    valid = (
        isinstance(value, str)
        and value.startswith(HOST_ID_PREFIXES)
        and " " not in value
        and value.count(":") >= 2
    )
    if not valid and raise_error:
        raise WebmasterRequestError("Yandex Webmaster host-id must be a remote host-id string.")
    return valid


def _require_period(period: WebmasterPeriod | None) -> WebmasterPeriod:
    if period is None:
        raise WebmasterRequestError(
            "Yandex Webmaster temporal API calls require explicit date_from/date_to."
        )
    period.validate()
    return period


def _rows(pages: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in pages:
        raw_rows = page.get(key, [])
        if not isinstance(raw_rows, list):
            raise WebmasterRequestError(f"Yandex Webmaster response {key} must be a list.")
        rows.extend(row for row in raw_rows if isinstance(row, dict))
    return rows


def _total_rows(pages: list[dict[str, Any]], rows_key: str, total_key: str) -> int:
    totals = [
        int(page[total_key])
        for page in pages
        if isinstance(page.get(total_key), int | str) and str(page[total_key]).isdigit()
    ]
    return max(totals) if totals else len(_rows(pages, rows_key))


def _freshness(finalize_after: str | None) -> str:
    return "provisional" if finalize_after else "final"


def _metadata(
    rows: list[Any],
    total_rows: int,
    *,
    pages_received: int,
    finalize_after: str | None,
) -> dict[str, Any]:
    rows_received = len(rows)
    if rows_received == 0 and total_rows == 0:
        coverage = "empty"
    elif rows_received < total_rows:
        coverage = "partial"
    else:
        coverage = "complete"
    return {
        "dataset_coverage": coverage,
        "freshness": _freshness(finalize_after),
        "comparability": "comparable" if coverage == "complete" else "insufficient_history",
        "rows_received": rows_received,
        "total_rows": total_rows,
        "pages_received": pages_received,
        "finalize_after": finalize_after,
    }


def _point_metadata(
    observations: list[dict[str, Any]],
    *,
    partial: bool,
    finalize_after: str | None,
    pages_received: int = 1,
) -> dict[str, Any]:
    if not observations and partial:
        coverage = "partial"
    elif not observations:
        coverage = "empty"
    elif partial:
        coverage = "partial"
    else:
        coverage = "complete"
    return {
        "dataset_coverage": coverage,
        "freshness": _freshness(finalize_after),
        "comparability": "comparable" if coverage == "complete" else "insufficient_history",
        "rows_received": len(observations),
        "total_rows": len(observations),
        "pages_received": pages_received,
        "finalize_after": finalize_after,
    }


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int_or_none(value: Any) -> int | None:
    number = _number_or_none(value)
    return int(number) if number is not None else None


def _device(device: str) -> str:
    return {
        "ALL": "__all__",
        "DESKTOP": "desktop",
        "MOBILE_AND_TABLET": "mobile_and_tablet",
        "MOBILE": "mobile",
        "TABLET": "tablet",
    }.get(device, device.lower())


def _history_query_rows(
    page: dict[str, Any],
    query_id: str,
    query_text: str | None,
) -> list[dict[str, Any]]:
    queries = page.get("queries")
    if isinstance(queries, list):
        return [row for row in queries if isinstance(row, dict)]
    indicators = page.get("indicators", {})
    if not isinstance(indicators, dict):
        return []
    by_date: dict[str, dict[str, Any]] = {}
    for metric, points in indicators.items():
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict) or point.get("date") is None:
                continue
            row = by_date.setdefault(
                str(point["date"]),
                {"query_id": query_id, "query_text": query_text or "__all__", "indicators": {}},
            )
            row["indicators"][metric] = point.get("value")
    return list(by_date.values())
