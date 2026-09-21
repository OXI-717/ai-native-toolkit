from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


COMPARISON_KINDS = {"wow", "mom", "yoy", "matched_period"}
READY_STATE = "ready"


@dataclass(frozen=True)
class ComparisonResult:
    state: str
    comparison_kind: str
    metric_path: str
    current_value: float | None = None
    baseline_value: float | None = None
    absolute_delta: float | None = None
    relative_delta: float | None = None
    period_ratio: float | None = None
    period_ratio_percent: float | None = None
    period_ratio_label: str = "period_ratio"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "comparison_kind": self.comparison_kind,
            "metric_path": self.metric_path,
            "current_value": self.current_value,
            "baseline_value": self.baseline_value,
            "absolute_delta": self.absolute_delta,
            "relative_delta": self.relative_delta,
            "period_ratio": self.period_ratio,
            "period_ratio_percent": self.period_ratio_percent,
            "period_ratio_label": self.period_ratio_label,
            "reason": self.reason,
        }


def compare_periods(
    current_snapshot: dict[str, Any],
    baseline_snapshot: dict[str, Any],
    *,
    metric_path: str,
    comparison_kind: str = "wow",
    minimum_denominator: float = 1,
) -> ComparisonResult:
    if comparison_kind not in COMPARISON_KINDS:
        raise ValueError(f"Unsupported comparison kind: {comparison_kind}")

    current_value = _path_value(current_snapshot, metric_path)
    baseline_value = _path_value(baseline_snapshot, metric_path)
    state = _comparison_identity_state(
        current_snapshot, baseline_snapshot, comparison_kind, metric_path=metric_path
    )
    state = state or _outcome_identity_state(current_snapshot, baseline_snapshot, metric_path)
    state = state or _bad_data_state(
        current_snapshot, baseline_snapshot, current_value, baseline_value, metric_path=metric_path
    )
    if state is not None:
        return ComparisonResult(
            state=state,
            comparison_kind=comparison_kind,
            metric_path=metric_path,
            current_value=_as_float(current_value),
            baseline_value=_as_float(baseline_value),
            reason=state,
        )

    current_number = _as_float(current_value)
    baseline_number = _as_float(baseline_value)
    if current_number is None or baseline_number is None:
        return ComparisonResult(
            state="missing",
            comparison_kind=comparison_kind,
            metric_path=metric_path,
            current_value=current_number,
            baseline_value=baseline_number,
            reason="metric_missing",
        )
    if baseline_number == 0:
        return ComparisonResult(
            state="not_ready",
            comparison_kind=comparison_kind,
            metric_path=metric_path,
            current_value=current_number,
            baseline_value=baseline_number,
            reason="baseline_denominator_is_zero",
        )
    if abs(baseline_number) < minimum_denominator:
        return ComparisonResult(
            state="denominator_too_small",
            comparison_kind=comparison_kind,
            metric_path=metric_path,
            current_value=current_number,
            baseline_value=baseline_number,
            reason="baseline_denominator_below_minimum",
        )

    period_ratio = current_number / baseline_number
    return ComparisonResult(
        state=READY_STATE,
        comparison_kind=comparison_kind,
        metric_path=metric_path,
        current_value=current_number,
        baseline_value=baseline_number,
        absolute_delta=current_number - baseline_number,
        relative_delta=period_ratio - 1,
        period_ratio=period_ratio,
        period_ratio_percent=period_ratio * 100,
    )


def _path_value(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in _path_parts(path):
        if isinstance(value, dict):
            if part not in value:
                return None
            value = value[part]
        elif isinstance(value, list):
            index_text = part[1:-1] if part.startswith("[") and part.endswith("]") else part
            if not index_text.isdigit() or int(index_text) >= len(value):
                return None
            value = value[int(index_text)]
        else:
            return None
    return value


def _path_parts(path: str) -> list[str]:
    return [part for part in path.replace("[", ".[").split(".") if part]


def _bad_data_state(
    current_snapshot: dict[str, Any],
    baseline_snapshot: dict[str, Any],
    current_value: Any,
    baseline_value: Any,
    *,
    metric_path: str | None = None,
) -> str | None:
    if current_value is None or baseline_value is None:
        return "missing"
    evidence_group = _evidence_group(metric_path)
    rows = _all_evidence_rows(current_snapshot, evidence_group) + _all_evidence_rows(baseline_snapshot, evidence_group)
    if any(row.get("freshness") == "stale" for row in rows):
        return "stale"
    if any(row.get("sampled") is True for row in rows):
        return "sampled"
    if any(row.get("comparability") not in (None, "comparable") for row in rows):
        return "incomparable"
    if any(row.get("dataset_coverage") not in (None, "complete") for row in rows):
        return "partial"
    if _row_count(current_snapshot, evidence_group) == 0 or _row_count(baseline_snapshot, evidence_group) == 0:
        return "partial"
    return None


def _all_evidence_rows(snapshot: dict[str, Any], evidence_group: str | None = None) -> list[dict[str, Any]]:
    evidence = snapshot.get("evidence", {})
    if not isinstance(evidence, dict):
        return []
    rows: list[dict[str, Any]] = []
    values = [evidence.get(evidence_group)] if evidence_group else evidence.values()
    for value in values:
        if not isinstance(value, list):
            continue
        rows.extend(row for row in value if isinstance(row, dict))
    return rows


def _row_count(snapshot: dict[str, Any], evidence_group: str | None = None) -> int:
    coverage = snapshot.get("coverage", {})
    if not isinstance(coverage, dict):
        return 0
    blocks = [coverage.get(evidence_group)] if evidence_group else coverage.values()
    counts = [int(block.get("row_count", 0)) for block in blocks if isinstance(block, dict)]
    return sum(counts)


def _evidence_group(metric_path: str | None) -> str | None:
    if not metric_path:
        return None
    if metric_path.startswith("formula_inputs.search_performance_totals."):
        return "search_performance"
    if metric_path.startswith("formula_inputs.outcome_metric_inputs"):
        return "outcome_metrics"
    return None


def _comparison_identity_state(
    current_snapshot: dict[str, Any],
    baseline_snapshot: dict[str, Any],
    comparison_kind: str,
    *,
    metric_path: str,
) -> str | None:
    current_project = _nested_value(current_snapshot, "project", "project_id")
    baseline_project = _nested_value(baseline_snapshot, "project", "project_id")
    if not current_project or current_project != baseline_project:
        return "project_mismatch"
    current_timezone = _nested_value(current_snapshot, "period", "timezone")
    baseline_timezone = _nested_value(baseline_snapshot, "period", "timezone")
    if not current_timezone or current_timezone != baseline_timezone:
        return "timezone_mismatch"
    try:
        current_start, current_end = _period_dates(current_snapshot, metric_path=metric_path)
        baseline_start, baseline_end = _period_dates(baseline_snapshot, metric_path=metric_path)
    except (KeyError, TypeError, ValueError):
        return "period_mismatch"
    if current_end < current_start or baseline_end < baseline_start:
        return "period_mismatch"
    period_days = (current_end - current_start).days
    if (baseline_end - baseline_start).days != period_days:
        return "period_mismatch"
    if comparison_kind == "wow":
        return None if (current_start - baseline_start).days == 7 else "period_mismatch"
    if comparison_kind == "mom":
        return None if _shift_month(baseline_start, 1) == current_start else "period_mismatch"
    if comparison_kind == "yoy":
        return None if _shift_year(baseline_start, 1) == current_start else "period_mismatch"
    return None if baseline_end < current_start else "period_mismatch"


def _nested_value(snapshot: dict[str, Any], section: str, field: str) -> Any:
    value = snapshot.get(section)
    return value.get(field) if isinstance(value, dict) else None


def _outcome_identity_state(
    current_snapshot: dict[str, Any], baseline_snapshot: dict[str, Any], metric_path: str
) -> str | None:
    parts = _path_parts(metric_path)
    try:
        index = parts.index("outcome_metric_inputs") + 1
        index_text = parts[index]
    except (ValueError, IndexError):
        return None
    if index_text.startswith("[") and index_text.endswith("]"):
        index_text = index_text[1:-1]
    if not index_text.isdigit():
        return None
    outcome_index = int(index_text)
    current_id = _outcome_id_at(current_snapshot, outcome_index)
    baseline_id = _outcome_id_at(baseline_snapshot, outcome_index)
    if current_id is not None and baseline_id is not None and current_id != baseline_id:
        return "outcome_mismatch"
    return None


def _outcome_id_at(snapshot: dict[str, Any], index: int) -> str | None:
    inputs = _nested_value(snapshot, "formula_inputs", "outcome_metric_inputs")
    if not isinstance(inputs, list) or index >= len(inputs) or not isinstance(inputs[index], dict):
        return None
    outcome_id = inputs[index].get("outcome_id")
    return outcome_id if isinstance(outcome_id, str) else None


def _period_dates(snapshot: dict[str, Any], *, metric_path: str) -> tuple[date, date]:
    evidence_group = _evidence_group(metric_path)
    rows = _all_evidence_rows(snapshot, evidence_group)
    starts = [row.get("effective_start") for row in rows]
    ends = [row.get("effective_end") for row in rows]
    if rows and all(isinstance(value, str) for value in starts + ends):
        return date.fromisoformat(min(starts)), date.fromisoformat(max(ends))
    period = snapshot["period"]
    if not isinstance(period, dict):
        raise TypeError("period must be an object")
    return date.fromisoformat(str(period["start"])), date.fromisoformat(str(period["end"]))


def _shift_month(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    month_end = (date(year + (month == 12), month % 12 + 1, 1) - date.resolution).day
    return date(year, month, min(value.day, month_end))


def _shift_year(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
