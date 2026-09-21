from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlparse

from seo_observer.config import ConfigError

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]



GSC_SEARCH_ANALYTICS_ENDPOINT = "/webmasters/v3/sites/{site_url}/searchAnalytics/query"
GSC_SITEMAPS_ENDPOINT = "/webmasters/v3/sites/{site_url}/sitemaps"
GSC_SITE_ENDPOINT = "/webmasters/v3/sites/{site_url}"
GSC_SITES_ENDPOINT = "/webmasters/v3/sites"
GSC_TIMEZONE = "America/Los_Angeles"
GSC_DOMAIN_PREFIX = "sc-domain:"
GSC_DAILY_SEARCH_TYPE_CAP = 50000
GSC_MAX_ROW_LIMIT = 25000
GSC_SUPPORTED_DIMENSIONS = {
    "query",
    "page",
    "country",
    "device",
    "searchAppearance",
    "date",
    "hour",
}


class GSCRequestError(ValueError):
    pass


class GSCTransport(Protocol):
    def post_json(
        self,
        endpoint: str,
        *,
        json: dict[str, Any],
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


@dataclass(frozen=True)
class GSCPeriod:
    start_date: str
    end_date: str

    def validate(self) -> None:
        if not self.start_date or not self.end_date:
            raise GSCRequestError(
                "Google Search Console temporal API calls require explicit start_date/end_date."
            )


@dataclass(frozen=True)
class SearchAnalyticsQuery:
    dimensions: tuple[str, ...] = ("query",)
    filters: tuple[dict[str, str], ...] = ()
    search_type: str = "web"
    aggregation_type: str = "auto"
    data_state: str | None = None
    start_row: int = 0


@dataclass(frozen=True)
class GSCSource:
    site_url: str
    credential_file_env: str
    property_id: str
    timezone: str
    access_token: str | None = None
    row_limit: int = GSC_MAX_ROW_LIMIT
    data_state: str = "final"
    finalize_after: str | None = None


class GSCAdapter:
    def __init__(self, source: GSCSource, transport: GSCTransport) -> None:
        validate_gsc_property(source.site_url)
        validate_row_limit(source.row_limit)
        self.source = source
        self.transport = transport

    def fetch_search_performance(
        self,
        period: GSCPeriod | None,
        *,
        query: SearchAnalyticsQuery | None = None,
        max_rows: int = GSC_DAILY_SEARCH_TYPE_CAP,
    ) -> dict[str, Any]:
        period = _require_period(period)
        query = query or SearchAnalyticsQuery()
        _validate_query(query)
        pages = self._fetch_search_pages(period, query, max_rows=max_rows)
        metadata = _search_metadata(
            pages,
            query=query,
            row_limit=self.source.row_limit,
            max_rows=max_rows,
            start_row=query.start_row,
            request_data_state=query.data_state or self.source.data_state,
            finalize_after=self.source.finalize_after,
        )
        observations = [
            self._search_observation(row, period, query, metadata)
            for row in _rows(pages)
        ]
        return {"collection": "search_performance", "metadata": metadata, "observations": observations}

    def fetch_search_appearance_breakdown(
        self,
        period: GSCPeriod | None,
        *,
        detail_dimensions: tuple[str, ...],
        search_type: str = "web",
        max_rows: int = GSC_DAILY_SEARCH_TYPE_CAP,
    ) -> dict[str, Any]:
        period = _require_period(period)
        discovery_query = SearchAnalyticsQuery(
            dimensions=("searchAppearance",),
            search_type=search_type,
            aggregation_type="auto",
        )
        discovery_pages = self._fetch_search_pages(period, discovery_query, max_rows=max_rows)
        appearance_types = [
            str(row.get("keys", [""])[0])
            for row in _rows(discovery_pages)
            if isinstance(row.get("keys"), list) and row.get("keys")
        ]
        observations: list[dict[str, Any]] = []
        detail_pages: list[dict[str, Any]] = []
        for appearance in appearance_types:
            detail_query = SearchAnalyticsQuery(
                dimensions=detail_dimensions,
                filters=(
                    {
                        "dimension": "searchAppearance",
                        "operator": "equals",
                        "expression": appearance,
                    },
                ),
                search_type=search_type,
                aggregation_type="auto",
            )
            pages = self._fetch_search_pages(period, detail_query, max_rows=max_rows)
            detail_pages.extend(pages)
            metadata = _search_metadata(
                pages,
                query=detail_query,
                row_limit=self.source.row_limit,
                max_rows=max_rows,
                start_row=detail_query.start_row,
                request_data_state=detail_query.data_state or self.source.data_state,
                finalize_after=self.source.finalize_after,
            )
            for item in _rows(pages):
                observation = self._search_observation(item, period, detail_query, metadata)
                observation["search_appearance"] = appearance
                observations.append(observation)
        metadata = _search_metadata(
            detail_pages,
            query=SearchAnalyticsQuery(dimensions=detail_dimensions, search_type=search_type),
            row_limit=self.source.row_limit,
            max_rows=max_rows,
            start_row=0,
            request_data_state=self.source.data_state,
            finalize_after=self.source.finalize_after,
        )
        metadata["search_appearance_mode"] = "two_step"
        metadata["search_appearance_types"] = appearance_types
        metadata["pages_received"] = len(discovery_pages) + len(detail_pages)
        return {"collection": "search_performance", "metadata": metadata, "observations": observations}

    def fetch_sitemaps(self, *, effective_at: str) -> dict[str, Any]:
        page = self._get(self._sitemaps_endpoint(), params={})
        rows = page.get("sitemap", [])
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise GSCRequestError("Google Search Console sitemap list response must contain sitemap list.")
        observations = [
            self._sitemap_observation(row, effective_at)
            for row in rows
            if isinstance(row, dict)
        ]
        metadata = _point_metadata(observations, pages_received=1, finalize_after=self.source.finalize_after)
        for observation in observations:
            observation["dataset_coverage"] = metadata["dataset_coverage"]
            observation["freshness"] = metadata["freshness"]
        return {"collection": "index_coverage", "metadata": metadata, "observations": observations}

    def fetch_sitemap(self, feedpath: str, *, effective_at: str) -> dict[str, Any]:
        if not feedpath:
            raise GSCRequestError("Google Search Console sitemap get requires feedpath.")
        page = self._get(f"{self._sitemaps_endpoint()}/{_quote_path(feedpath)}", params={})
        observations = [self._sitemap_observation(page, effective_at)]
        metadata = _point_metadata(observations, pages_received=1, finalize_after=self.source.finalize_after)
        observations[0]["dataset_coverage"] = metadata["dataset_coverage"]
        observations[0]["freshness"] = metadata["freshness"]
        return {"collection": "index_coverage", "metadata": metadata, "observations": observations}

    def get_site_descriptor(self) -> dict[str, Any]:
        return {"endpoint": GSC_SITE_ENDPOINT.format(site_url=_quote_path(self.source.site_url)), "params": {}}

    def list_sites_descriptor(self) -> dict[str, Any]:
        return {"endpoint": GSC_SITES_ENDPOINT, "params": {}}

    def _fetch_search_pages(
        self,
        period: GSCPeriod,
        query: SearchAnalyticsQuery,
        *,
        max_rows: int,
    ) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        start_row = query.start_row
        while start_row < query.start_row + max_rows and start_row < GSC_DAILY_SEARCH_TYPE_CAP:
            remaining_rows = query.start_row + max_rows - start_row
            remaining_daily_cap = GSC_DAILY_SEARCH_TYPE_CAP - start_row
            request_row_limit = min(self.source.row_limit, remaining_rows, remaining_daily_cap)
            body = self._search_body(period, query, start_row=start_row, row_limit=request_row_limit)
            page = self._post(self._search_endpoint(), json=body)
            pages.append(page)
            page_rows = _rows([page])
            if len(page_rows) < request_row_limit:
                break
            start_row += request_row_limit
        return pages

    def _search_body(
        self,
        period: GSCPeriod,
        query: SearchAnalyticsQuery,
        *,
        start_row: int,
        row_limit: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "startDate": period.start_date,
            "endDate": period.end_date,
            "dimensions": list(query.dimensions),
            "type": query.search_type,
            "aggregationType": query.aggregation_type,
            "rowLimit": row_limit,
            "startRow": start_row,
            "dataState": query.data_state or self.source.data_state,
        }
        if query.filters:
            body["dimensionFilterGroups"] = [{"groupType": "and", "filters": [dict(item) for item in query.filters]}]
        return body

    def _search_observation(
        self,
        row: dict[str, Any],
        period: GSCPeriod,
        query: SearchAnalyticsQuery,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        key_values = row.get("keys", [])
        if not isinstance(key_values, list):
            key_values = []
        dimensions = {dimension: _key(key_values, index) for index, dimension in enumerate(query.dimensions)}
        page_url = dimensions.get("page") or "__all__"
        return {
            "project_id": "__pending__",
            "property_id": self.source.property_id,
            "source": "google_search_console",
            "effective_start": period.start_date,
            "effective_end": period.end_date,
            "source_timezone": GSC_TIMEZONE,
            "configured_timezone": self.source.timezone,
            "query_id": ("gsc-query:" + hashlib.sha256(dimensions["query"].encode("utf-8")).hexdigest())
            if dimensions.get("query") else "__all__",
            "query_text": dimensions.get("query") or "__all__",
            "page_id": _page_id(page_url),
            "page_url": page_url,
            "search_engine": "google",
            "device": _device(dimensions.get("device")),
            "country": dimensions.get("country") or "__all__",
            "region": "__all__",
            "segment_id": "__all__",
            "date": dimensions.get("date") or "__all__",
            "hour": dimensions.get("hour") or "__all__",
            "search_appearance": dimensions.get("searchAppearance") or "__all__",
            "impressions": _int_or_none(row.get("impressions")),
            "clicks": _int_or_none(row.get("clicks")),
            "ctr": _number_or_none(row.get("ctr")),
            "average_position": _number_or_none(row.get("position")),
            "dataset_coverage": metadata["dataset_coverage"],
            "freshness": metadata["freshness"],
            "comparability": metadata["comparability"],
            "sampled": False,
            "sample_share": None,
            "request_dimensions": list(query.dimensions),
            "request_filters": [dict(item) for item in query.filters],
            "request_type": query.search_type,
            "request_aggregation_type": query.aggregation_type,
            "request_data_state": query.data_state or self.source.data_state,
            "response_aggregation_type": metadata.get("responseAggregationType"),
            "unavailable_dimensions": _unavailable_dimensions(query),
            "unavailable_metrics": _unavailable_metrics(row),
            "normalizer_version": "gsc-v2",
        }

    def _sitemap_observation(self, row: dict[str, Any], effective_at: str) -> dict[str, Any]:
        contents = row.get("contents", [])
        if not isinstance(contents, list):
            contents = []
        submitted = sum(
            item.get("submitted", 0)
            for item in contents
            if isinstance(item, dict) and isinstance(item.get("submitted"), int | float)
        )
        indexed = sum(
            item.get("indexed", 0)
            for item in contents
            if isinstance(item, dict) and isinstance(item.get("indexed"), int | float)
        )
        sitemap_url = str(row.get("path") or "")
        return {
            "project_id": "__pending__",
            "property_id": self.source.property_id,
            "source": "google_search_console",
            "effective_at": effective_at,
            "source_timezone": GSC_TIMEZONE,
            "configured_timezone": self.source.timezone,
            "index_state": "sitemap",
            "sitemap_id": sitemap_url or "__all__",
            "sitemap_url": sitemap_url,
            "sitemap_type": str(row.get("type") or "__all__"),
            "sitemap_is_pending": bool(row.get("isPending")) if row.get("isPending") is not None else None,
            "sitemap_is_index": bool(row.get("isSitemapsIndex")) if row.get("isSitemapsIndex") is not None else None,
            "sitemap_last_submitted": row.get("lastSubmitted"),
            "sitemap_last_downloaded": row.get("lastDownloaded"),
            "searchable_pages": None,
            "excluded_pages": None,
            "submitted_urls": int(submitted) if contents else None,
            "indexed_urls": int(indexed) if contents else None,
            "problem_count": _problem_count(row),
            "dataset_coverage": "complete",
            "freshness": _freshness(self.source.finalize_after),
            "comparability": "incomplete_api_surface",
            "unavailable_dimensions": ["index_coverage_state"],
            "unavailable_metrics": [],
            "normalizer_version": "gsc-v1",
        }

    def _search_endpoint(self) -> str:
        return GSC_SEARCH_ANALYTICS_ENDPOINT.format(site_url=_quote_path(self.source.site_url))

    def _sitemaps_endpoint(self) -> str:
        return GSC_SITEMAPS_ENDPOINT.format(site_url=_quote_path(self.source.site_url))

    def _post(self, endpoint: str, *, json: dict[str, Any]) -> dict[str, Any]:
        page = self.transport.post_json(endpoint, json=json, headers=self._headers())
        if not isinstance(page, dict):
            raise GSCRequestError("Google Search Console transport returned a non-object response.")
        return page

    def _get(self, endpoint: str, *, params: dict[str, Any]) -> dict[str, Any]:
        page = self.transport.get_json(endpoint, params=params, headers=self._headers())
        if not isinstance(page, dict):
            raise GSCRequestError("Google Search Console transport returned a non-object response.")
        return page

    def _headers(self) -> dict[str, str]:
        if not self.source.access_token:
            return {}
        return {"Authorization": f"Bearer {self.source.access_token}"}


def doctor_gsc_source(
    source_fields: dict[str, Any],
    *,
    bindings: list[dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
    live: bool = False,
) -> dict[str, Any]:
    env = env or {}
    errors: list[str] = []
    credential_file_env = source_fields.get("credential_file_env")
    token_file_env = source_fields.get("token_file_env")
    credential_env = source_fields.get("credential_env")
    config_shape = True
    for field_name, field_value in (
        ("credential_file_env", credential_file_env),
        ("token_file_env", token_file_env),
        ("credential_env", credential_env),
    ):
        if field_value is not None and (not isinstance(field_value, str) or not field_value):
            errors.append(f"{field_name} must be a non-empty string")
            config_shape = False
    if credential_file_env is None and token_file_env is None and credential_env is None:
        # Как у GA4: service account, OAuth-файл или готовый токен — любой из трёх.
        errors.append("one of credential_file_env, token_file_env or credential_env is required")
        config_shape = False
    bindings_valid = True
    for binding in bindings or []:
        remote_id = binding.get("remote_id") if isinstance(binding, dict) else None
        if not isinstance(remote_id, str) or not validate_gsc_property(remote_id, raise_error=False):
            bindings_valid = False
            errors.append(
                "google_search_console source binding remote_id must be a GSC URL-prefix "
                "property URL or a sc-domain: domain property."
            )
    def _file_present(env_name: Any) -> bool:
        if not isinstance(env_name, str) or not env_name:
            return False
        path = env.get(env_name)
        return bool(path and Path(path).is_file())

    credential_file_present = _file_present(credential_file_env)
    token_file_present = bool(
        isinstance(token_file_env, str)
        and token_file_env
        and env.get(token_file_env)
        and _validate_gsc_token_file(env[token_file_env])
    )
    inline_token_present = bool(
        isinstance(credential_env, str) and credential_env and env.get(credential_env)
    )
    credential_present = credential_file_present or token_file_present or inline_token_present
    if not credential_present:
        errors.append("no usable credential: service account file, OAuth token file or token env")
    return {
        "ok": config_shape and bindings_valid and credential_present,
        "live_checked": bool(live and False),
        "credential_file_env": credential_file_env,
        "token_file_env": token_file_env,
        "checks": {
            "config_shape": config_shape,
            "bindings_valid": bindings_valid,
            "credential_file_present": credential_file_present,
            "token_file_present": token_file_present,
            "inline_token_present": inline_token_present,
        },
        "errors": errors,
    }


def validate_url_prefix_property(value: str, *, raise_error: bool = True) -> bool:
    parsed = urlparse(value) if isinstance(value, str) else None
    valid = (
        parsed is not None
        and parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and value.endswith("/")
        and " " not in value
    )
    if not valid and raise_error:
        raise GSCRequestError("Google Search Console property must be a URL-prefix property URL.")
    return valid


def validate_domain_property(value: str, *, raise_error: bool = True) -> bool:
    """Доменный ресурс вида `sc-domain:example.org`.

    Такой ресурс покрывает все поддомены и оба протокола сразу, и во многих
    проектах URL-prefix просто не заводят — тогда доменный ресурс единственный.
    """
    valid = (
        isinstance(value, str)
        and value.startswith(GSC_DOMAIN_PREFIX)
        and len(value) > len(GSC_DOMAIN_PREFIX)
        and " " not in value
        and "/" not in value[len(GSC_DOMAIN_PREFIX) :]
    )
    if not valid and raise_error:
        raise GSCRequestError(
            "Google Search Console domain property must look like sc-domain:example.org."
        )
    return valid


def validate_gsc_property(value: str, *, raise_error: bool = True) -> bool:
    """Ресурс Search Console: URL-prefix либо доменный."""
    if validate_url_prefix_property(value, raise_error=False):
        return True
    if validate_domain_property(value, raise_error=False):
        return True
    if raise_error:
        raise GSCRequestError(
            "Google Search Console property must be a URL-prefix property URL "
            "or a sc-domain: domain property."
        )
    return False


def validate_row_limit(value: int) -> None:
    if not isinstance(value, int) or value < 1 or value > GSC_MAX_ROW_LIMIT:
        raise GSCRequestError("Google Search Console rowLimit must be in the valid range 1-25000.")


def _require_period(period: GSCPeriod | None) -> GSCPeriod:
    if period is None:
        raise GSCRequestError(
            "Google Search Console temporal API calls require explicit start_date/end_date."
        )
    period.validate()
    return period


def _validate_query(query: SearchAnalyticsQuery) -> None:
    if query.start_row < 0:
        raise GSCRequestError("Google Search Console startRow must be zero-based and non-negative.")
    duplicated = len(query.dimensions) != len(set(query.dimensions))
    unsupported = [dimension for dimension in query.dimensions if dimension not in GSC_SUPPORTED_DIMENSIONS]
    if duplicated:
        raise GSCRequestError("Google Search Console dimensions cannot repeat.")
    if unsupported:
        raise GSCRequestError(f"Google Search Console unsupported dimensions: {', '.join(unsupported)}.")
    for item in query.filters:
        if item.get("dimension") not in GSC_SUPPORTED_DIMENSIONS:
            raise GSCRequestError("Google Search Console filters must use supported dimensions.")


def _rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in pages:
        raw_rows = page.get("rows", [])
        if raw_rows is None:
            raw_rows = []
        if not isinstance(raw_rows, list):
            raise GSCRequestError("Google Search Console response rows must be a list.")
        rows.extend(row for row in raw_rows if isinstance(row, dict))
    return rows


def _search_metadata(
    pages: list[dict[str, Any]],
    *,
    query: SearchAnalyticsQuery,
    row_limit: int,
    max_rows: int,
    start_row: int,
    request_data_state: str,
    finalize_after: str | None,
) -> dict[str, Any]:
    rows = _rows(pages)
    response_metadata = _response_metadata(pages)
    coverage = "empty" if not rows else "top_rows"
    freshness = _freshness(finalize_after)
    if response_metadata.get("first_incomplete_date") or response_metadata.get("first_incomplete_hour"):
        freshness = "incomplete"
    capped = bool(rows) and (len(rows) >= max_rows or start_row + len(rows) >= GSC_DAILY_SEARCH_TYPE_CAP)
    metadata = {
        "dataset_coverage": coverage,
        "freshness": freshness,
        "comparability": "insufficient_history" if coverage == "empty" else "incomplete_top_rows",
        "rows_received": len(rows),
        "total_rows": None,
        "pages_received": len(pages),
        "finalize_after": finalize_after,
        "timezone_semantics": GSC_TIMEZONE,
        "request_dimensions": list(query.dimensions),
        "request_filters": [dict(item) for item in query.filters],
        "request_type": query.search_type,
        "request_search_type": query.search_type,
        "request_aggregation_type": query.aggregation_type,
        "request_data_state": request_data_state,
        "responseAggregationType": _response_aggregation_type(pages),
        "top_rows": True,
        "capped": capped,
        "cap_metadata": {
            "row_limit": row_limit,
            "start_row": start_row,
            "max_rows": max_rows,
            "daily_search_type_cap": GSC_DAILY_SEARCH_TYPE_CAP,
            "top_rows": True,
            "capped": capped,
        },
        "coverage_warnings": ["top_rows_not_full_coverage"] if rows else ["empty_response"],
        "data_loss_risk": _data_loss_risk(query.dimensions),
    }
    metadata.update(response_metadata)
    return metadata


def _point_metadata(
    observations: list[dict[str, Any]],
    *,
    pages_received: int,
    finalize_after: str | None,
) -> dict[str, Any]:
    coverage = "empty" if not observations else "complete"
    return {
        "dataset_coverage": coverage,
        "freshness": _freshness(finalize_after),
        "comparability": "comparable" if observations else "insufficient_history",
        "rows_received": len(observations),
        "total_rows": len(observations),
        "pages_received": pages_received,
        "finalize_after": finalize_after,
    }


def _response_metadata(pages: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for page in pages:
        metadata = page.get("metadata", {})
        if isinstance(metadata, dict):
            for key in ("first_incomplete_date", "first_incomplete_hour"):
                if metadata.get(key) and key not in merged:
                    merged[key] = metadata[key]
    return merged


def _response_aggregation_type(pages: list[dict[str, Any]]) -> str | None:
    for page in pages:
        value = page.get("responseAggregationType")
        if value is not None:
            return str(value)
    return None


def _freshness(finalize_after: str | None) -> str:
    return "provisional" if finalize_after else "final"


def _data_loss_risk(dimensions: tuple[str, ...]) -> str:
    if "page" in dimensions or "query" in dimensions:
        return "page_or_query_grouping_may_drop_data"
    return "top_rows_only"


def _unavailable_dimensions(query: SearchAnalyticsQuery) -> list[str]:
    requested_dimensions = set(query.dimensions)
    requested_dimensions.update(
        str(item["dimension"])
        for item in query.filters
        if isinstance(item.get("dimension"), str)
    )
    return [
        dimension
        for dimension in ("query", "page", "country", "device", "searchAppearance", "date", "hour")
        if dimension not in requested_dimensions
    ]


def _unavailable_metrics(row: dict[str, Any]) -> list[str]:
    return [metric for metric in ("clicks", "impressions", "ctr", "position") if _number_or_none(row.get(metric)) is None]


def _key(values: list[Any], index: int) -> str | None:
    if index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    return str(value)


def _device(value: str | None) -> str:
    return {
        "DESKTOP": "desktop",
        "MOBILE": "mobile",
        "TABLET": "tablet",
    }.get(value or "", "__all__" if value in {None, ""} else str(value).lower())


def _page_id(page_url: str) -> str:
    return "__all__" if page_url == "__all__" else f"page:{page_url}"


def _problem_count(row: dict[str, Any]) -> int | None:
    warnings = _int_or_none(row.get("warnings")) or 0
    errors = _int_or_none(row.get("errors")) or 0
    if row.get("warnings") is None and row.get("errors") is None:
        return None
    return warnings + errors


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


def _quote_path(value: str) -> str:
    return quote(value, safe="")


def _validate_gsc_token_file(path: str | Path) -> bool:
    path_obj = Path(path)
    if not path_obj.is_file():
        return False
    try:
        from google.oauth2.credentials import Credentials
        Credentials.from_authorized_user_file(str(path_obj), scopes=GSC_SCOPES)
        return True
    except Exception:
        pass
    try:
        content = path_obj.read_text(encoding="utf-8")
        payload = json.loads(content)
        if isinstance(payload, dict):
            if payload.get("token") or payload.get("access_token"):
                return True
            if payload.get("refresh_token") and payload.get("client_id") and payload.get("client_secret"):
                return True
    except Exception:
        pass
    return False


def _gsc_access_token_from_fields(fields: dict[str, Any], env: dict[str, str]) -> str:
    credential_env = fields.get("credential_env")
    if credential_env and env.get(str(credential_env)):
        return str(env[str(credential_env)])
    token_file_env = fields.get("token_file_env")
    if token_file_env and env.get(str(token_file_env)):
        return _gsc_access_token_from_oauth_file(env[str(token_file_env)])
    credential_file_env = fields.get("credential_file_env")
    if credential_file_env and env.get(str(credential_file_env)):
        return _gsc_access_token(env[str(credential_file_env)])
    raise ConfigError(
        "GSC_AUTH_NOT_READY",
        "Google Search Console source requires credential_env, token_file_env, or credential_file_env.",
        {},
    )


def _gsc_access_token_from_oauth_file(path: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise ConfigError(
            "GSC_AUTH_DEPENDENCY_MISSING",
            "Google Search Console OAuth token file support requires google-auth.",
            {"package": "google-auth"},
        ) from exc
    credentials = Credentials.from_authorized_user_file(str(path), scopes=GSC_SCOPES)
    if not credentials.valid and getattr(credentials, "refresh_token", None):
        credentials.refresh(Request())
    if not credentials.token:
        raise ConfigError(
            "GSC_AUTH_NOT_READY",
            "Google Search Console OAuth token file did not produce an access token.",
            {},
        )
    return str(credentials.token)


def _gsc_access_token(credential_file: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise ConfigError(
            "GSC_AUTH_DEPENDENCY_MISSING",
            "Google Search Console collect requires google-auth.",
            {"package": "google-auth"},
        ) from exc
    credentials = service_account.Credentials.from_service_account_file(
        credential_file,
        scopes=GSC_SCOPES,
    )
    credentials.refresh(Request())
    return str(credentials.token)

