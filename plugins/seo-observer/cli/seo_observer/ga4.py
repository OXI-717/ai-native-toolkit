from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from seo_observer.channels import channel_slug


GA4_RUN_REPORT_ENDPOINT = "/v1beta/{property_resource}:runReport"
GA4_MAX_LIMIT = 250000
DEFAULT_LIMIT = 10000
ORGANIC_CHANNEL_GROUP = "Organic Search"
GA4_TIMEZONE = "property_timezone"
GA4_TRAFFIC_METRICS = (
    "sessions",
    "activeUsers",
    "screenPageViews",
    "bounceRate",
    "averageSessionDuration",
)


class GA4RequestError(ValueError):
    pass


class GA4Transport(Protocol):
    def post_json(
        self,
        endpoint: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class GA4Period:
    start_date: str
    end_date: str

    def validate(self) -> None:
        if not self.start_date or not self.end_date:
            raise GA4RequestError("GA4 Data API calls require explicit start_date/end_date.")


@dataclass(frozen=True)
class GA4Source:
    property_resource: str
    token: str
    property_id: str
    timezone: str
    limit: int = DEFAULT_LIMIT
    finalize_after: str | None = None
    channel_timezone: str = GA4_TIMEZONE


@dataclass(frozen=True)
class GA4ReportSpec:
    name: str
    dimensions: tuple[str, ...]
    metrics: tuple[str, ...]
    organic_only: bool = False


GA4_AUDIT_REPORTS: tuple[GA4ReportSpec, ...] = (
    GA4ReportSpec("totals", (), GA4_TRAFFIC_METRICS, organic_only=True),
    GA4ReportSpec("source_medium", ("sessionSourceMedium", "sessionDefaultChannelGroup"), GA4_TRAFFIC_METRICS, organic_only=True),
    GA4ReportSpec("landing_pages", ("landingPagePlusQueryString",), GA4_TRAFFIC_METRICS, organic_only=True),
    GA4ReportSpec("landing_source_medium", ("landingPagePlusQueryString", "sessionSourceMedium"), GA4_TRAFFIC_METRICS, organic_only=True),
    GA4ReportSpec("device", ("deviceCategory",), GA4_TRAFFIC_METRICS, organic_only=True),
    GA4ReportSpec("geo", ("country", "city"), GA4_TRAFFIC_METRICS, organic_only=True),
    GA4ReportSpec("top_events", ("eventName",), ("eventCount",), organic_only=True),
)


class GA4Adapter:
    def __init__(self, source: GA4Source, transport: GA4Transport) -> None:
        validate_property_resource(source.property_resource)
        validate_limit(source.limit)
        self.source = source
        self.transport = transport

    def run_report(self, period: GA4Period | None, spec: GA4ReportSpec) -> dict[str, Any]:
        period = _require_period(period)
        pages: list[dict[str, Any]] = []
        offset = 0
        while True:
            body = self._report_body(period, spec, offset=offset)
            page = self.transport.post_json(self._endpoint(), json=body, headers=self._headers())
            if not isinstance(page, dict):
                raise GA4RequestError("GA4 transport returned a non-object response.")
            pages.append(page)
            page_rows = _rows([page])
            total_rows = _total_rows(pages)
            if offset + len(page_rows) >= total_rows or len(page_rows) < self.source.limit:
                break
            offset += self.source.limit
        metadata = _metadata(pages, self.source.finalize_after)
        return {
            "report": spec.name,
            "dimensions": list(spec.dimensions),
            "metrics": list(spec.metrics),
            "organic_only": spec.organic_only,
            "metadata": metadata,
            "rows": [_structured_row(row, spec) for row in _rows(pages)],
        }

    def fetch_audit_reports(self, period: GA4Period | None) -> dict[str, Any]:
        reports = {spec.name: self.run_report(period, spec) for spec in GA4_AUDIT_REPORTS}
        return {
            "quality": "live",
            "property_id": self.source.property_id,
            "remote_id": self.source.property_resource,
            "reports": reports,
        }

    def fetch_organic_traffic_bundle(self, period: GA4Period | None) -> dict[str, Any]:
        period = _require_period(period)
        specs = (
            GA4ReportSpec("organic_totals", (), GA4_TRAFFIC_METRICS, organic_only=True),
            GA4ReportSpec("organic_landing_pages", ("landingPagePlusQueryString",), GA4_TRAFFIC_METRICS, organic_only=True),
            GA4ReportSpec("organic_source_medium", ("sessionSourceMedium",), GA4_TRAFFIC_METRICS, organic_only=True),
            GA4ReportSpec("organic_landing_source_medium", ("landingPagePlusQueryString", "sessionSourceMedium"), GA4_TRAFFIC_METRICS, organic_only=True),
            GA4ReportSpec("organic_device", ("deviceCategory",), GA4_TRAFFIC_METRICS, organic_only=True),
            GA4ReportSpec("organic_geo", ("country", "city"), GA4_TRAFFIC_METRICS, organic_only=True),
        )
        reports = [self.run_report(period, spec) for spec in specs]
        observations = []
        for report in reports:
            observations.extend(_traffic_observations(self.source, period, report))
        metadata = _combined_metadata([report["metadata"] for report in reports], self.source.finalize_after)
        return {"collection": "traffic_metrics", "metadata": metadata, "observations": observations}

    def fetch_channel_traffic_bundle(self, period: GA4Period | None) -> dict[str, Any]:
        """Sessions by default channel group, source/medium and landing page, all channels."""
        period = _require_period(period)
        specs = (
            GA4ReportSpec(
                "channel_landing_source_medium",
                ("date", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium",
                 "landingPagePlusQueryString"),
                GA4_TRAFFIC_METRICS,
            ),
        )
        reports = [self.run_report(period, spec) for spec in specs]
        observations: list[dict[str, Any]] = []
        for report in reports:
            observations.extend(_channel_traffic_observations(self.source, period, report))
        metadata = _combined_metadata([report["metadata"] for report in reports], self.source.finalize_after)
        return {"collection": "traffic_metrics", "metadata": metadata, "observations": observations}

    def _report_body(self, period: GA4Period, spec: GA4ReportSpec, *, offset: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": period.start_date, "endDate": period.end_date}],
            "dimensions": [{"name": dimension} for dimension in spec.dimensions],
            "metrics": [{"name": metric} for metric in spec.metrics],
            "offset": str(offset),
            "limit": str(self.source.limit),
            "returnPropertyQuota": True,
        }
        if spec.organic_only:
            body["dimensionFilter"] = {
                "filter": {
                    "fieldName": "sessionDefaultChannelGroup",
                    "stringFilter": {"matchType": "EXACT", "value": ORGANIC_CHANNEL_GROUP},
                }
            }
        return body

    def _endpoint(self) -> str:
        return GA4_RUN_REPORT_ENDPOINT.format(property_resource=self.source.property_resource)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.source.token}"} if self.source.token else {}


def doctor_ga4_source(
    source_fields: dict[str, Any],
    *,
    bindings: list[dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
    live: bool = False,
) -> dict[str, Any]:
    env = env or {}
    errors: list[str] = []
    config_shape = True
    credential_env = source_fields.get("credential_env")
    credential_file_env = source_fields.get("credential_file_env")
    token_file_env = source_fields.get("token_file_env")
    configured_credentials = [item for item in (credential_env, credential_file_env, token_file_env) if item is not None]
    for field_name, value in (
        ("credential_env", credential_env),
        ("credential_file_env", credential_file_env),
        ("token_file_env", token_file_env),
    ):
        if value is not None and (not isinstance(value, str) or not value):
            errors.append(f"{field_name} must be a non-empty string")
            config_shape = False
    if not configured_credentials:
        errors.append("one of credential_env, credential_file_env, or token_file_env is required")
        config_shape = False
    bindings_valid = True
    for binding in bindings or []:
        remote_id = binding.get("remote_id") if isinstance(binding, dict) else None
        if not isinstance(remote_id, str) or not validate_property_resource(remote_id, raise_error=False):
            bindings_valid = False
            errors.append("ga4 source binding remote_id must look like properties/1234.")
    credential_present = False
    if isinstance(credential_env, str) and credential_env:
        credential_present = bool(env.get(credential_env))
    if not credential_present and isinstance(credential_file_env, str) and credential_file_env:
        path = env.get(credential_file_env)
        credential_present = bool(path and Path(path).is_file())
    if not credential_present and isinstance(token_file_env, str) and token_file_env:
        path = env.get(token_file_env)
        credential_present = bool(path and Path(path).is_file() and _read_token_file(path))
    if not credential_present:
        errors.append("credential token or credential file is not present")
    return {
        "ok": config_shape and bindings_valid and credential_present,
        "live_checked": bool(live and False),
        "credential_env": credential_env,
        "credential_file_env": credential_file_env,
        "token_file_env": token_file_env,
        "checks": {
            "config_shape": config_shape,
            "bindings_valid": bindings_valid,
            "credential_present": credential_present,
        },
        "errors": errors,
    }


def validate_property_resource(value: str, *, raise_error: bool = True) -> bool:
    valid = isinstance(value, str) and value.startswith("properties/") and value.removeprefix("properties/").isdigit()
    if not valid and raise_error:
        raise GA4RequestError("GA4 property resource must look like properties/1234.")
    return valid


def validate_limit(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > GA4_MAX_LIMIT:
        raise GA4RequestError("GA4 limit must be in the valid range 1-250000.")


def _require_period(period: GA4Period | None) -> GA4Period:
    if period is None:
        raise GA4RequestError("GA4 Data API calls require explicit start_date/end_date.")
    period.validate()
    return period


def _rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in pages:
        raw_rows = page.get("rows", [])
        if raw_rows is None:
            raw_rows = []
        if not isinstance(raw_rows, list):
            raise GA4RequestError("GA4 response rows must be a list.")
        rows.extend(row for row in raw_rows if isinstance(row, dict))
    return rows


def _structured_row(row: dict[str, Any], spec: GA4ReportSpec) -> dict[str, Any]:
    result: dict[str, Any] = {}
    dimensions = row.get("dimensionValues", [])
    metrics = row.get("metricValues", [])
    for index, dimension in enumerate(spec.dimensions):
        result[dimension] = _cell_value(dimensions, index)
    for index, metric in enumerate(spec.metrics):
        result[metric] = _number_or_none(_cell_value(metrics, index))
    return result


def _traffic_observations(source: GA4Source, period: GA4Period, report: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    rows = report.get("rows") if isinstance(report.get("rows"), list) else []
    observations = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        landing_page = _clean_text(row.get("landingPagePlusQueryString")) or "__all__"
        source_medium = _clean_text(row.get("sessionSourceMedium")) or "__all__"
        device = _clean_text(row.get("deviceCategory")) or "__all__"
        country = _clean_text(row.get("country"))
        city = _clean_text(row.get("city"))
        observations.append(
            {
                "project_id": "__pending__",
                "property_id": source.property_id,
                "source": "ga4",
                "effective_start": period.start_date,
                "effective_end": period.end_date,
                "source_timezone": source.timezone,
                "channel": "organic",
                "search_engine": source_medium,
                "landing_page_id": _page_id(landing_page),
                "device": device.lower() if device != "__all__" else "__all__",
                "region": _region(country, city),
                "attribution_model": "ga4_session",
                "visits": _int_or_none(row.get("sessions")),
                "users": _int_or_none(row.get("activeUsers")),
                "pageviews": _int_or_none(row.get("screenPageViews")),
                "bounce_rate": _number_or_none(row.get("bounceRate")),
                "avg_visit_duration_seconds": _number_or_none(row.get("averageSessionDuration")),
                "dataset_coverage": metadata.get("dataset_coverage") or "unknown",
                "freshness": metadata.get("freshness") or "provisional",
                "comparability": metadata.get("comparability") or "comparable",
                "sampled": False,
                "sample_share": None,
                "normalizer_version": "ga4-v1",
            }
        )
    return observations


def _channel_traffic_observations(source: GA4Source, period: GA4Period, report: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    rows = report.get("rows") if isinstance(report.get("rows"), list) else []
    observations = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        group = _clean_text(row.get("sessionDefaultChannelGroup")) or "Unassigned"
        session_source = _clean_text(row.get("sessionSource"))
        session_medium = _clean_text(row.get("sessionMedium"))
        source_medium = f"{session_source} / {session_medium}" if session_source or session_medium else "__all__"
        landing_page = _clean_text(row.get("landingPagePlusQueryString")) or "__all__"
        day = _ga4_date(row.get("date"))
        observations.append(
            {
                "project_id": "__pending__",
                "property_id": source.property_id,
                "source": "ga4",
                "effective_start": day or period.start_date,
                "effective_end": day or period.end_date,
                "source_timezone": source.channel_timezone,
                "channel": channel_slug(group),
                "search_engine": source_medium,
                "landing_page_id": _page_id(landing_page),
                "device": "__all__",
                "region": "__all__",
                "attribution_model": "ga4_session_all_channels",
                "visits": _int_or_none(row.get("sessions")),
                "users": _int_or_none(row.get("activeUsers")),
                "pageviews": _int_or_none(row.get("screenPageViews")),
                "bounce_rate": _number_or_none(row.get("bounceRate")),
                "avg_visit_duration_seconds": _number_or_none(row.get("averageSessionDuration")),
                "dataset_coverage": metadata.get("dataset_coverage") or "unknown",
                "freshness": metadata.get("freshness") or "provisional",
                "comparability": metadata.get("comparability") or "comparable",
                "sampled": False,
                "sample_share": None,
                "normalizer_version": "ga4-channels-v1",
            }
        )
    return observations


def _metadata(pages: list[dict[str, Any]], finalize_after: str | None) -> dict[str, Any]:
    rows_received = len(_rows(pages))
    total_rows = _total_rows(pages)
    if rows_received == 0 and total_rows == 0:
        coverage = "empty"
    elif rows_received < total_rows:
        coverage = "partial"
    else:
        coverage = "complete"
    return {
        "dataset_coverage": coverage,
        "comparability": "partial" if coverage == "partial" else ("insufficient_history" if coverage == "empty" else "comparable"),
        "freshness": "provisional" if finalize_after else "final",
        "sampled": False,
        "sample_share": None,
        "rows_received": rows_received,
        "total_rows": total_rows,
        "pages_received": len(pages),
        "finalize_after": finalize_after,
        "quota": _quota(pages),
    }


def _combined_metadata(items: list[dict[str, Any]], finalize_after: str | None) -> dict[str, Any]:
    rows_received = sum(int(item.get("rows_received") or 0) for item in items)
    total_rows = sum(int(item.get("total_rows") or 0) for item in items)
    if rows_received == 0 and total_rows == 0:
        coverage = "empty"
    elif rows_received < total_rows or any(item.get("dataset_coverage") == "partial" for item in items):
        coverage = "partial"
    else:
        coverage = "complete"
    return {
        "dataset_coverage": coverage,
        "comparability": "partial" if coverage == "partial" else ("insufficient_history" if coverage == "empty" else "comparable"),
        "freshness": "provisional" if finalize_after else "final",
        "sampled": False,
        "sample_share": None,
        "rows_received": rows_received,
        "total_rows": total_rows,
        "pages_received": sum(int(item.get("pages_received") or 0) for item in items),
        "finalize_after": finalize_after,
    }


def _total_rows(pages: list[dict[str, Any]]) -> int:
    totals = [
        int(page["rowCount"])
        for page in pages
        if isinstance(page.get("rowCount"), int) and not isinstance(page.get("rowCount"), bool)
    ]
    return max(totals) if totals else len(_rows(pages))


def _quota(pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for page in pages:
        quota = page.get("propertyQuota")
        if isinstance(quota, dict):
            return quota
    return None


def _cell_value(cells: Any, index: int) -> str | None:
    if not isinstance(cells, list) or index >= len(cells):
        return None
    cell = cells[index]
    if not isinstance(cell, dict):
        return None
    value = cell.get("value")
    return str(value) if value is not None else None


def _page_id(page_url: str) -> str:
    return "__all__" if page_url == "__all__" else f"page:{page_url or '/'}"


def _region(country: str | None, city: str | None) -> str:
    if country and city:
        return f"{country} / {city}"
    return country or city or "__all__"


def _read_token_file(path: str | Path) -> str | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = payload.get("access_token") or payload.get("token") if isinstance(payload, dict) else None
    return str(token) if token else None


def google_oauth_access_token_from_file(path: str | Path) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise GA4RequestError("GA4 OAuth token file support requires google-auth[requests].") from exc
    try:
        credentials = Credentials.from_authorized_user_file(
            str(path),
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
        )
    except ValueError:
        token = _read_token_file(path)
        if token:
            return token
        raise
    if not credentials.valid and getattr(credentials, "refresh_token", None):
        credentials.refresh(Request())
    if not credentials.token:
        raise GA4RequestError("GA4 OAuth token file did not produce an access token.")
    return str(credentials.token)


def _ga4_date(value: Any) -> str | None:
    text = _clean_text(value)
    if text and len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
