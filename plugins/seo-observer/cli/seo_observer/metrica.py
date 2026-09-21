from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse


REPORTS_DATA_ENDPOINT = "/stat/v1/data"
ORGANIC_FILTER = "ym:s:trafficSource=='organic'"
DEFAULT_LIMIT = 10000


class MetricaRequestError(ValueError):
    pass


class MetricaTransport(Protocol):
    def get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class Period:
    date1: str
    date2: str

    def validate(self) -> None:
        if not self.date1 or not self.date2:
            raise MetricaRequestError("Metrica temporal API calls require explicit date1/date2.")


@dataclass(frozen=True)
class MetricaGoal:
    outcome_id: str
    goal_id: int
    zero_confirmed: bool = False
    counting_unit: str = "goal_reach"
    population_scope: str = "organic_visits"
    attribution_model: str = "metrica_default"
    attribution_scope: str = "metrica_visit"
    population_id: str = "organic_visits"
    population_name: str = "Organic visits"
    attribution_window: str = "metrica_default"
    attribution_source: str = "yandex_metrica"
    dedupe_key: str = "metrica_goal_reach"
    dedupe_source: str = "yandex_metrica"
    timestamp_field: str = "ym:s:date"


@dataclass(frozen=True)
class MetricaSource:
    counter_id: str
    token: str
    property_id: str
    timezone: str
    goals: tuple[MetricaGoal, ...] = ()
    accuracy: str = "full"
    limit: int = DEFAULT_LIMIT
    finalize_after: str | None = None


DEMO_TEN_GOAL_MAPPING_FIXTURE: tuple[MetricaGoal, ...] = (
    MetricaGoal("demo_registration_start", 1001),
    MetricaGoal("demo_registration_complete", 1002),
    MetricaGoal("demo_lms_signup", 1003),
    MetricaGoal("demo_trial_started", 1004),
    MetricaGoal("demo_payment_intent", 1005),
    MetricaGoal("demo_payment_success", 1006),
    MetricaGoal("demo_contact_form", 1007),
    MetricaGoal("demo_demo_request", 1008),
    MetricaGoal("demo_course_view", 1009),
    MetricaGoal("demo_partner_lead", 1010),
)


class MetricaAdapter:
    def __init__(self, source: MetricaSource, transport: MetricaTransport) -> None:
        self.source = source
        self.transport = transport

    def fetch_organic_traffic(self, period: Period | None) -> dict[str, Any]:
        period = _require_period(period)
        request = self._request(
            period,
            metrics=("ym:s:visits", "ym:s:users"),
            dimensions=(),
        )
        pages = self._fetch_pages(request)
        metadata = _metadata(pages, self.source.finalize_after)
        observations = [
            {
                **_base_traffic_observation(self.source, period, metadata),
                "visits": int(_metric(row, 0)),
                "users": int(_metric(row, 1)),
            }
            for row in _rows(pages)
        ]
        return {"collection": "traffic_metrics", "metadata": metadata, "observations": observations}

    def fetch_landing_pages(self, period: Period | None) -> dict[str, Any]:
        period = _require_period(period)
        request = self._request(
            period,
            metrics=("ym:s:visits", "ym:s:users"),
            dimensions=("ym:s:startURLPathFull",),
        )
        pages = self._fetch_pages(request)
        metadata = _metadata(pages, self.source.finalize_after)
        observations = []
        for row in _rows(pages):
            page_url = _dimension(row, 0) or "__all__"
            observations.append(
                {
                    **_base_traffic_observation(self.source, period, metadata),
                    "landing_page_id": _page_id(page_url),
                    "visits": int(_metric(row, 0)),
                    "users": int(_metric(row, 1)),
                }
            )
        return {"collection": "traffic_metrics", "metadata": metadata, "observations": observations}

    def fetch_device_breakdown(self, period: Period | None) -> dict[str, Any]:
        period = _require_period(period)
        request = self._request(
            period,
            metrics=("ym:s:visits", "ym:s:users"),
            dimensions=("ym:s:deviceCategory",),
        )
        pages = self._fetch_pages(request)
        metadata = _metadata(pages, self.source.finalize_after)
        observations = []
        for row in _rows(pages):
            observations.append(
                {
                    **_base_traffic_observation(self.source, period, metadata),
                    "device": _dimension(row, 0) or "__all__",
                    "visits": int(_metric(row, 0)),
                    "users": int(_metric(row, 1)),
                }
            )
        return {"collection": "traffic_metrics", "metadata": metadata, "observations": observations}

    def fetch_goal_totals(self, period: Period | None) -> dict[str, Any]:
        period = _require_period(period)
        if not self.source.goals:
            raise MetricaRequestError("Metrica goal totals require configured goals.")
        request = self._request(
            period,
            metrics=tuple(f"ym:s:goal{goal.goal_id}reaches" for goal in self.source.goals),
            dimensions=(),
        )
        pages = self._fetch_pages(request)
        metadata = _metadata(pages, self.source.finalize_after)
        totals = _rows(pages)[0].get("metrics", []) if _rows(pages) else []
        observations = []
        for index, goal in enumerate(self.source.goals):
            count = int(totals[index] if index < len(totals) and totals[index] is not None else 0)
            dedupe = goal.dedupe_key
            if count == 0 and not goal.zero_confirmed:
                raise MetricaRequestError(
                    f"Metrica goal {goal.outcome_id} returned zero; set zero_confirmed=true "
                    "in the mapping fixture/config to accept it."
                )
            observations.append(
                {
                    "project_id": "__pending__",
                    "property_id": self.source.property_id,
                    "source": "yandex_metrica",
                    "effective_start": period.date1,
                    "effective_end": period.date2,
                    "period_start": period.date1,
                    "period_end": period.date2,
                    "source_timezone": self.source.timezone,
                    "timezone": self.source.timezone,
                    "source_request_id": (
                        f"metrica:{self.source.counter_id}:{goal.outcome_id}:{period.date1}:{period.date2}"
                    ),
                    "outcome_id": goal.outcome_id,
                    "evidence_kind": "analytics_event",
                    "counting_unit": goal.counting_unit,
                    "population_id": goal.population_id,
                    "population_name": goal.population_name,
                    "deduplication_rule": "metrica_goal_reaches",
                    "dedupe_key": dedupe,
                    "dedupe_source": goal.dedupe_source,
                    "population_scope": goal.population_scope,
                    "attribution_model": goal.attribution_model,
                    "attribution_window": goal.attribution_window,
                    "attribution_source": goal.attribution_source,
                    "attribution_scope": goal.attribution_scope,
                    "timestamp_field": goal.timestamp_field,
                    "aggregation_grain": "period",
                    "traffic_channel": "organic",
                    "search_engine": "__all__",
                    "landing_page_id": "__all__",
                    "device": "__all__",
                    "attribution_level": "channel_aggregate",
                    "count": count,
                    "unique_actors": None,
                    "value_minor": None,
                    "currency": None,
                    "dataset_coverage": metadata["dataset_coverage"],
                    "freshness": metadata["freshness"],
                    "comparability": metadata["comparability"],
                    "sampled": metadata["sampled"],
                    "sample_share": metadata["sample_share"],
                    "zero_confirmed": goal.zero_confirmed,
                    "normalizer_version": "metrica-v1",
                }
            )
        return {"collection": "outcome_metrics", "metadata": metadata, "observations": observations}

    def _request(
        self,
        period: Period,
        *,
        metrics: tuple[str, ...],
        dimensions: tuple[str, ...],
        offset: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "ids": self.source.counter_id,
            "date1": period.date1,
            "date2": period.date2,
            "metrics": ",".join(metrics),
            "filters": ORGANIC_FILTER,
            "accuracy": self.source.accuracy,
            "limit": self.source.limit,
            "offset": offset,
        }
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
        return {"endpoint": REPORTS_DATA_ENDPOINT, "params": params}

    def _fetch_pages(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        offset = int(request["params"]["offset"])
        limit = int(request["params"]["limit"])
        while True:
            params = {**request["params"], "offset": offset}
            page = self.transport.get_json(
                request["endpoint"],
                params=params,
                headers={"Authorization": f"OAuth {self.source.token}"},
            )
            if not isinstance(page, dict):
                raise MetricaRequestError("Metrica transport returned a non-object response.")
            pages.append(page)
            received = sum(len(page_item.get("data", [])) for page_item in pages)
            total_rows = _total_rows(pages)
            if received >= total_rows or len(page.get("data", [])) < limit:
                break
            offset += limit
        return pages


def doctor_metrica_source(
    source_fields: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    live: bool = False,
) -> dict[str, Any]:
    env = env or {}
    errors: list[str] = []
    counter_id = source_fields.get("counter_id")
    credential_env = source_fields.get("credential_env")
    goals = source_fields.get("goals", [])
    config_shape = True
    if not isinstance(counter_id, str) or not counter_id:
        errors.append("counter_id is required")
        config_shape = False
    if credential_env is not None and (not isinstance(credential_env, str) or not credential_env):
        errors.append("credential_env must be a non-empty string")
        config_shape = False
    if goals and (
        not isinstance(goals, list)
        or any(
            not isinstance(goal, dict)
            or not isinstance(goal.get("outcome_id"), str)
            or not isinstance(goal.get("goal_id"), int)
            or isinstance(goal.get("goal_id"), bool)
            for goal in goals
        )
    ):
        errors.append("goals must contain outcome_id strings and integer goal_id values")
        config_shape = False
    token_present = bool(credential_env and env.get(credential_env))
    if not token_present:
        errors.append("credential token is not present")
    return {
        "ok": config_shape and token_present,
        "live_checked": bool(live and False),
        "checks": {
            "config_shape": config_shape,
            "token_present": token_present,
        },
        "errors": errors,
    }


def _require_period(period: Period | None) -> Period:
    if period is None:
        raise MetricaRequestError("Metrica temporal API calls require explicit date1/date2.")
    period.validate()
    return period


def _rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in pages:
        data = page.get("data", [])
        if not isinstance(data, list):
            raise MetricaRequestError("Metrica response data must be a list.")
        rows.extend(row for row in data if isinstance(row, dict))
    return rows


def _metadata(pages: list[dict[str, Any]], finalize_after: str | None) -> dict[str, Any]:
    rows_received = len(_rows(pages))
    total_rows = _total_rows(pages)
    sampled = any(bool(page.get("sampled")) for page in pages)
    sample_share = next(
        (
            float(page["sample_share"])
            for page in pages
            if isinstance(page.get("sample_share"), int | float)
        ),
        None,
    )
    data_lag_seconds = next(
        (
            int(page["data_lag_seconds"])
            for page in pages
            if isinstance(page.get("data_lag_seconds"), int)
            and not isinstance(page.get("data_lag_seconds"), bool)
        ),
        None,
    )
    if rows_received == 0 and total_rows == 0:
        coverage = "empty"
    elif sampled:
        coverage = "sampled"
    elif rows_received < total_rows:
        coverage = "partial"
    else:
        coverage = "complete"
    freshness = "provisional" if data_lag_seconds else "final"
    return {
        "dataset_coverage": coverage,
        "comparability": "partial" if coverage in {"sampled", "partial"} else "comparable",
        "freshness": freshness,
        "sampled": sampled,
        "sample_share": sample_share,
        "data_lag_seconds": data_lag_seconds,
        "rows_received": rows_received,
        "total_rows": total_rows,
        "pages_received": len(pages),
        "finalize_after": finalize_after,
    }


def _total_rows(pages: list[dict[str, Any]]) -> int:
    totals = [
        int(page["total_rows"])
        for page in pages
        if isinstance(page.get("total_rows"), int) and not isinstance(page.get("total_rows"), bool)
    ]
    return max(totals) if totals else len(_rows(pages))


def _base_traffic_observation(
    source: MetricaSource,
    period: Period,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project_id": "__pending__",
        "property_id": source.property_id,
        "source": "yandex_metrica",
        "effective_start": period.date1,
        "effective_end": period.date2,
        "source_timezone": source.timezone,
        "channel": "organic",
        "search_engine": "__all__",
        "landing_page_id": "__all__",
        "device": "__all__",
        "region": "__all__",
        "attribution_model": "metrica_default",
        "pageviews": None,
        "bounce_rate": None,
        "avg_visit_duration_seconds": None,
        "dataset_coverage": metadata["dataset_coverage"],
        "freshness": metadata["freshness"],
        "comparability": metadata["comparability"],
        "sampled": metadata["sampled"],
        "sample_share": metadata["sample_share"],
        "normalizer_version": "metrica-v1",
    }


def _dimension(row: dict[str, Any], index: int) -> str | None:
    dimensions = row.get("dimensions", [])
    if not isinstance(dimensions, list) or index >= len(dimensions):
        return None
    dimension = dimensions[index]
    if not isinstance(dimension, dict):
        return None
    value = dimension.get("name") or dimension.get("id")
    return str(value) if value is not None else None


def _metric(row: dict[str, Any], index: int) -> float:
    metrics = row.get("metrics", [])
    if not isinstance(metrics, list) or index >= len(metrics) or metrics[index] is None:
        return 0.0
    return float(metrics[index])


def _page_id(page_url: str) -> str:
    parsed = urlparse(page_url)
    path = parsed.path if parsed.scheme or parsed.netloc else page_url
    return f"page:{path or '/'}"
