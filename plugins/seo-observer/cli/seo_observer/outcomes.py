from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


FORBIDDEN_QUERY_FIELDS = frozenset(
    {
        "sql",
        "query",
        "table",
        "where",
        "where_clause",
        "sql_fragment",
        "report_query",
    }
)
ARTIFACT_EXPORT_CHANNELS = frozenset({"git", "markdown", "telegram"})


class AggregateOutcomeError(ValueError):
    pass


class AggregateOutcomeTransport(Protocol):
    def fetch_aggregate_view(
        self,
        descriptor: "AggregateViewDescriptor",
        *,
        params: dict[str, str],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class Period:
    start: str
    end: str

    def validate(self) -> None:
        if not self.start or not self.end:
            raise AggregateOutcomeError("Aggregate outcome calls require explicit period_start/period_end.")


@dataclass(frozen=True, init=False)
class AggregateViewDescriptor:
    view_id: str
    outcome_id: str
    population_id: str
    population_name: str
    attribution_model: str
    attribution_window: str
    attribution_source: str
    counting_unit: str
    dedupe_key: str
    dedupe_source: str
    timestamp_field: str
    timezone: str
    aggregation_grain: str
    parameter_names: tuple[str, ...]
    traffic_channel: str
    attribution_scope: str
    request_id_field: str
    count_field: str
    period_start_field: str
    period_end_field: str
    grain_start_field: str | None

    def __init__(
        self,
        *,
        view_id: str,
        outcome_id: str,
        population_id: str,
        population_name: str,
        attribution_model: str,
        attribution_window: str,
        attribution_source: str,
        counting_unit: str,
        dedupe_key: str,
        dedupe_source: str,
        timestamp_field: str,
        timezone: str,
        aggregation_grain: str,
        parameter_names: tuple[str, ...] = ("period_start", "period_end"),
        traffic_channel: str = "__all__",
        attribution_scope: str = "__all__",
        request_id_field: str = "request_id",
        count_field: str = "count",
        period_start_field: str = "period_start",
        period_end_field: str = "period_end",
        grain_start_field: str | None = "grain_start",
        **extra: Any,
    ) -> None:
        forbidden = FORBIDDEN_QUERY_FIELDS.intersection(extra)
        if forbidden:
            raise AggregateOutcomeError(
                "Aggregate outcome sources accept approved view IDs only, not SQL/query/table text."
            )
        values = {
            "view_id": view_id,
            "outcome_id": outcome_id,
            "population_id": population_id,
            "population_name": population_name,
            "attribution_model": attribution_model,
            "attribution_window": attribution_window,
            "attribution_source": attribution_source,
            "counting_unit": counting_unit,
            "dedupe_key": dedupe_key,
            "dedupe_source": dedupe_source,
            "timestamp_field": timestamp_field,
            "timezone": timezone,
            "aggregation_grain": aggregation_grain,
            "traffic_channel": traffic_channel,
            "attribution_scope": attribution_scope,
            "request_id_field": request_id_field,
            "count_field": count_field,
            "period_start_field": period_start_field,
            "period_end_field": period_end_field,
        }
        if extra:
            raise AggregateOutcomeError(f"Unknown aggregate view descriptor field(s): {sorted(extra)}")
        for name, value in values.items():
            if not isinstance(value, str) or not value:
                raise AggregateOutcomeError(f"{name} must be a non-empty string.")
        if not isinstance(parameter_names, tuple):
            raise AggregateOutcomeError("parameter_names must be a tuple containing period_start and period_end.")
        if not all(isinstance(item, str) and item for item in parameter_names):
            raise AggregateOutcomeError("parameter_names must contain non-empty strings.")
        if set(parameter_names) != {"period_start", "period_end"} or len(parameter_names) != 2:
            raise AggregateOutcomeError("parameter_names must contain exactly period_start and period_end.")
        object.__setattr__(self, "parameter_names", parameter_names)
        object.__setattr__(self, "grain_start_field", grain_start_field)
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class AggregateOutcomeSource:
    source_id: str
    adapter: str
    approved_views: tuple[AggregateViewDescriptor, ...]

    def __post_init__(self) -> None:
        if not self.source_id:
            raise AggregateOutcomeError("source_id is required.")
        if self.adapter not in {"fixture_aggregate", "postgres_aggregate"}:
            raise AggregateOutcomeError("Aggregate outcome adapter must be fixture_aggregate or postgres_aggregate.")
        if not self.approved_views:
            raise AggregateOutcomeError("Aggregate outcome source requires approved views.")


@dataclass(frozen=True)
class OutcomeFact:
    project_id: str
    property_id: str
    source: str
    source_request_id: str
    outcome_id: str
    evidence_kind: str
    count: int
    period_start: str
    period_end: str
    population_id: str
    population_name: str
    attribution_model: str
    attribution_window: str
    attribution_source: str
    counting_unit: str
    dedupe_key: str
    dedupe_source: str
    timestamp_field: str
    timezone: str
    aggregation_grain: str
    traffic_channel: str = "__all__"
    attribution_scope: str = "__all__"
    dataset_coverage: str = "unknown"
    freshness: str = "provisional"
    sampled: bool = False
    lineage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconciliationInput:
    analytics_fact: OutcomeFact
    server_fact: OutcomeFact


@dataclass(frozen=True)
class ArtifactExportPolicy:
    suppression_threshold: int = 5
    bands: tuple[int, ...] = (0, 5, 10, 25, 50, 100)
    total_count: int | None = None
    previous_export_count: int | None = None


class AggregateOutcomeAdapter:
    def __init__(
        self,
        source: AggregateOutcomeSource,
        transport: AggregateOutcomeTransport,
        *,
        project_id: str = "__pending__",
        property_id: str = "__all__",
    ) -> None:
        self.source = source
        self.transport = transport
        self.project_id = project_id
        self.property_id = property_id

    def fetch_outcome_facts(self, period: Period | None, *, view_id: str) -> dict[str, Any]:
        period = _require_period(period)
        descriptor = self._approved_view(view_id)
        params = _params_for_period(descriptor, period)
        response = self.transport.fetch_aggregate_view(descriptor, params=params)
        if not isinstance(response, dict):
            raise AggregateOutcomeError("Aggregate outcome transport returned a non-object response.")
        rows = response.get("rows", [])
        if not isinstance(rows, list):
            raise AggregateOutcomeError("Aggregate outcome response rows must be a list.")
        observations = [
            self._fact_from_row(
                descriptor,
                row,
                source_label=str(response.get("source", self.source.adapter)),
                quality=_outcome_quality(response),
            )
            for row in rows
            if isinstance(row, dict)
        ]
        return {
            "collection": "outcome_metrics",
            "metadata": {
                **_outcome_quality(response),
                "rows_received": len(observations),
                "approved_view_id": descriptor.view_id,
                "source": self.source.source_id,
            },
            "observations": observations,
        }

    def _approved_view(self, view_id: str) -> AggregateViewDescriptor:
        for descriptor in self.source.approved_views:
            if descriptor.view_id == view_id:
                return descriptor
        raise AggregateOutcomeError(f"Aggregate view {view_id!r} is not approved for {self.source.source_id}.")

    def _fact_from_row(
        self,
        descriptor: AggregateViewDescriptor,
        row: dict[str, Any],
        *,
        source_label: str,
        quality: dict[str, Any],
    ) -> OutcomeFact:
        count = _non_negative_count(row, descriptor.count_field)
        period_start = _required_row_value(row, descriptor.period_start_field)
        period_end = _required_row_value(row, descriptor.period_end_field)
        grain_start = (
            _required_row_value(row, descriptor.grain_start_field)
            if descriptor.grain_start_field
            else None
        )
        request_id = row.get(descriptor.request_id_field)
        dedupe = descriptor.dedupe_key
        return OutcomeFact(
            project_id=self.project_id,
            property_id=self.property_id,
            source=self.source.source_id,
            source_request_id=str(
                request_id
                or f"{self.source.source_id}:{descriptor.view_id}:{period_start}:{period_end}"
            ),
            outcome_id=descriptor.outcome_id,
            evidence_kind="server_fact",
            count=count,
            period_start=period_start,
            period_end=period_end,
            population_id=descriptor.population_id,
            population_name=descriptor.population_name,
            attribution_model=descriptor.attribution_model,
            attribution_window=descriptor.attribution_window,
            attribution_source=descriptor.attribution_source,
            counting_unit=descriptor.counting_unit,
            dedupe_key=dedupe,
            dedupe_source=descriptor.dedupe_source,
            timestamp_field=descriptor.timestamp_field,
            timezone=descriptor.timezone,
            aggregation_grain=descriptor.aggregation_grain,
            traffic_channel=descriptor.traffic_channel,
            attribution_scope=descriptor.attribution_scope,
            dataset_coverage=quality["dataset_coverage"],
            freshness=quality["freshness"],
            sampled=quality["sampled"],
            lineage={
                "approved_view_id": descriptor.view_id,
                "provider_source": source_label,
                "aggregation_grain": descriptor.aggregation_grain,
                "grain_start": grain_start,
                **quality,
            },
        )


def build_demo_outcome_sources() -> dict[str, AggregateOutcomeSource]:
    registration = AggregateViewDescriptor(
        view_id="demo_auth.registration_outcomes_daily_v1",
        outcome_id="registration",
        population_id="demo_lms_visitors",
        population_name="Demo LMS visitors",
        attribution_model="server_first_touch",
        attribution_window="P30D",
        attribution_source="auth_server",
        counting_unit="account",
        dedupe_key="user_id",
        dedupe_source="auth_db",
        timestamp_field="registered_at",
        timezone="Europe/Moscow",
        aggregation_grain="period",
    )
    paid_purchase = AggregateViewDescriptor(
        view_id="demo_pay.paid_purchase_outcomes_daily_v1",
        outcome_id="paid_purchase",
        population_id="demo_lms_registered_users",
        population_name="Demo LMS registered users",
        attribution_model="server_first_touch",
        attribution_window="P30D",
        attribution_source="payment_server",
        counting_unit="paid_purchase",
        dedupe_key="payment_id",
        dedupe_source="payment_db",
        timestamp_field="paid_at",
        timezone="Europe/Moscow",
        aggregation_grain="period",
    )
    return {
        "outcome_auth": AggregateOutcomeSource(
            source_id="outcome_auth",
            adapter="fixture_aggregate",
            approved_views=(registration,),
        ),
        "outcome_pay": AggregateOutcomeSource(
            source_id="outcome_pay",
            adapter="fixture_aggregate",
            approved_views=(paid_purchase,),
        ),
    }


def reconcile_outcomes(item: ReconciliationInput) -> dict[str, Any]:
    analytics = item.analytics_fact
    server = item.server_fact
    reasons = [
        field_name
        for field_name in (
            "project_id",
            "property_id",
            "outcome_id",
            "period_start",
            "period_end",
            "population_id",
            "population_name",
            "counting_unit",
            "attribution_model",
            "attribution_window",
            "attribution_source",
            "dedupe_key",
            "dedupe_source",
            "timestamp_field",
            "timezone",
            "aggregation_grain",
            "traffic_channel",
            "attribution_scope",
        )
        if getattr(analytics, field_name) != getattr(server, field_name)
    ]
    reasons.extend(
        field_name
        for field_name in ("dataset_coverage", "freshness", "sampled")
        if getattr(analytics, field_name) != getattr(server, field_name)
        or (field_name == "dataset_coverage" and getattr(analytics, field_name) != "complete")
        or (field_name == "freshness" and getattr(analytics, field_name) != "final")
        or (field_name == "sampled" and getattr(analytics, field_name))
    )
    lineage = {
        "analytics": {
            "source": analytics.source,
            "source_request_id": analytics.source_request_id,
        },
        "server": {
            "source": server.source,
            "source_request_id": server.source_request_id,
        },
    }
    if reasons:
        return {
            "status": "not_comparable",
            "reasons": reasons,
            "lineage": lineage,
        }
    delta = server.count - analytics.count
    return {
        "status": "comparable",
        "delta": delta,
        "ratio": None if analytics.count == 0 else server.count / analytics.count,
        "analytics_count": analytics.count,
        "server_count": server.count,
        "lineage": lineage,
    }


def export_outcome_value(
    fact: OutcomeFact,
    *,
    policy: ArtifactExportPolicy,
    channel: str,
) -> dict[str, Any]:
    if channel == "local":
        return {"value": fact.count, "suppressed": False, "exact": True}
    if channel not in ARTIFACT_EXPORT_CHANNELS:
        raise AggregateOutcomeError(f"Unsupported outcome export channel: {channel}")
    _validate_artifact_bands(policy.bands)
    if fact.count < policy.suppression_threshold:
        return {
            "value": f"<{policy.suppression_threshold}",
            "suppressed": True,
            "exact": False,
            "reason": "below_threshold",
        }
    if (
        policy.total_count is not None
        and policy.total_count >= fact.count
        and policy.total_count - fact.count < policy.suppression_threshold
    ):
        return {
            "value": "suppressed",
            "suppressed": True,
            "exact": False,
            "reason": "complementary_suppression",
        }
    if (
        policy.previous_export_count is not None
        and abs(fact.count - policy.previous_export_count) < policy.suppression_threshold
    ):
        return {
            "value": "suppressed",
            "suppressed": True,
            "exact": False,
            "reason": "differencing_risk",
        }
    return {"value": _band(fact.count, policy.bands), "suppressed": False, "exact": False}


def doctor_outcome_source(
    source_fields: dict[str, Any],
    *,
    live: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    config_shape = True
    for key in FORBIDDEN_QUERY_FIELDS:
        if key in source_fields:
            errors.append(f"{key} is forbidden; use approved view IDs")
            config_shape = False
    adapter = source_fields.get("adapter")
    if adapter is not None and adapter not in {"fixture_aggregate", "postgres_aggregate"}:
        errors.append("adapter must be fixture_aggregate or postgres_aggregate")
        config_shape = False
    approved_views = source_fields.get("approved_views")
    if (
        not isinstance(approved_views, list)
        or not approved_views
        or not all(isinstance(item, str) and item for item in approved_views)
    ):
        errors.append("approved_views must contain non-empty approved view IDs")
        config_shape = False
    parameter_names = source_fields.get("parameter_names")
    if parameter_names is not None and (
        not isinstance(parameter_names, list)
        or set(parameter_names) != {"period_start", "period_end"}
        or len(parameter_names) != 2
    ):
        errors.append("parameter_names may only contain period_start/period_end")
        config_shape = False
    return {
        "ok": config_shape,
        "live_checked": bool(live and False),
        "checks": {
            "config_shape": config_shape,
            "approved_views_present": approved_views is not None,
            "query_text_absent": not any(key in source_fields for key in FORBIDDEN_QUERY_FIELDS),
        },
        "errors": errors,
    }


def _require_period(period: Period | None) -> Period:
    if period is None:
        raise AggregateOutcomeError("Aggregate outcome calls require explicit period_start/period_end.")
    period.validate()
    return period


def _params_for_period(descriptor: AggregateViewDescriptor, period: Period) -> dict[str, str]:
    values = {"period_start": period.start, "period_end": period.end}
    return {name: values[name] for name in descriptor.parameter_names}


def _outcome_quality(response: dict[str, Any]) -> dict[str, Any]:
    metadata = response.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    def value(name: str, default: Any) -> Any:
        return response.get(name, metadata.get(name, default))

    return {
        "dataset_coverage": str(value("dataset_coverage", "unknown")),
        "freshness": str(value("freshness", "provisional")),
        "sampled": value("sampled", False) is True,
        "sample_share": value("sample_share", None),
    }


def _required_row_value(row: dict[str, Any], field_name: str) -> str:
    value = row.get(field_name)
    if value is None or value == "":
        raise AggregateOutcomeError(f"Aggregate outcome row requires non-empty {field_name}.")
    return str(value)


def _non_negative_count(row: dict[str, Any], field_name: str) -> int:
    value = row.get(field_name)
    if isinstance(value, bool):
        raise AggregateOutcomeError("Aggregate outcome count must be a non-negative integer.")
    if isinstance(value, int):
        count = value
    elif isinstance(value, str) and value.isdecimal():
        count = int(value)
    else:
        raise AggregateOutcomeError("Aggregate outcome count must be a non-negative integer.")
    if count < 0:
        raise AggregateOutcomeError("Aggregate outcome count must be a non-negative integer.")
    return count


def _band(value: int, bands: tuple[int, ...]) -> str:
    sorted_bands = tuple(sorted(set(bands)))
    if len(sorted_bands) < 2:
        raise AggregateOutcomeError("Artifact export bands must contain at least two thresholds.")
    for index, lower in enumerate(sorted_bands[:-1]):
        upper = sorted_bands[index + 1]
        if lower <= value < upper:
            return f"{lower}-{upper - 1}"
    return f"{sorted_bands[-1]}+"


def _validate_artifact_bands(bands: tuple[int, ...]) -> None:
    sorted_bands = tuple(sorted(set(bands)))
    if len(sorted_bands) < 2 or any(
        upper - lower < 2 for lower, upper in zip(sorted_bands, sorted_bands[1:])
    ):
        raise AggregateOutcomeError(
            "Artifact export bands must not create singleton intervals."
        )
