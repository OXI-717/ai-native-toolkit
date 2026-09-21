from __future__ import annotations

import hashlib
import json
import tomllib
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from seo_observer.comparisons import _as_float, _bad_data_state, _path_value
from seo_observer.storage import SEOStorage


ACTION_POLICY_VERSION = "action-verdict-v1"
LIFECYCLE_STATES = {"planned", "active", "done", "tombstoned"}
TARGET_TYPES = {"url", "query"}
TARGET_ROLES = {"primary", "guardrail"}
DIRECTIONS = {"increase", "decrease"}


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class ActionTarget:
    target_type: str
    target_value: str
    target_role: str


@dataclass(frozen=True)
class MeasurementWindow:
    window_id: str
    baseline_start: str
    baseline_end: str
    observation_start: str
    observation_end: str
    timezone: str
    comparison_strategy: str
    minimum_denominator: int | None
    earliest_evaluation_date: str
    confounder_notes: str = ""


@dataclass(frozen=True)
class ExpectedSignal:
    metric_path: str
    direction: str
    minimum_absolute_delta: float | None = None
    minimum_relative_delta: float | None = None
    minimum_observation_days: int = 0


@dataclass(frozen=True)
class SEOAction:
    action_id: str
    project_id: str
    changed_at: str
    action_type: str
    description: str
    hypothesis_id: str
    evidence_ref: str
    lifecycle_state: str
    targets: tuple[ActionTarget, ...]
    windows: tuple[MeasurementWindow, ...]
    expected_signals: tuple[ExpectedSignal, ...]
    confounders: tuple[str, ...]
    revision_hash: str
    supersedes_action_revision_hash: str | None = None


@dataclass(frozen=True)
class ActionEvidence:
    baseline_value: float
    observation_value: float
    denominator: float
    observation_days: int
    as_of: str
    input_fact_set_hash: str
    evidence_set_hash: str
    snapshot_hashes: list[str]
    input_derived_metric_ids: list[str] | None = None
    comparability_state: str = "comparable"
    confounders: list[str] | None = None


@dataclass(frozen=True)
class ActionVerdict:
    verdict_id: str
    action_id: str
    window_id: str
    verdict: str
    actionable: bool
    absolute_delta: float | None
    relative_delta: float | None
    input_derived_metric_ids: tuple[str, ...]
    input_fact_set_hash: str
    evidence_set_hash: str
    historical_snapshot_hashes: tuple[str, ...]
    comparability_state: str
    evaluation_policy_version: str
    evaluation_as_of: str
    supersedes_verdict_id: str | None
    explanation: str


def action_evidence_from_snapshots(
    action: SEOAction,
    window: MeasurementWindow,
    baseline_snapshot: dict[str, Any],
    observation_snapshot: dict[str, Any],
    *,
    as_of: str,
    metric_path: str | None = None,
    observation_days: int | None = None,
    confounders: list[str] | None = None,
) -> ActionEvidence:
    selected_metric_path = metric_path or _default_metric_path(action)
    baseline_raw = _path_value(baseline_snapshot, selected_metric_path)
    observation_raw = _path_value(observation_snapshot, selected_metric_path)
    state = (
        _bad_data_state(
            observation_snapshot,
            baseline_snapshot,
            observation_raw,
            baseline_raw,
            metric_path=selected_metric_path,
        )
        or "comparable"
    )
    baseline_value = _as_float(baseline_raw)
    observation_value = _as_float(observation_raw)
    observed_days = observation_days if observation_days is not None else _window_observation_days(window)
    snapshot_hashes = [
        _snapshot_hash_from_manifest(baseline_snapshot),
        _snapshot_hash_from_manifest(observation_snapshot),
    ]
    identity_payload = {
        "action_revision_hash": action.revision_hash,
        "action_id": action.action_id,
        "window_id": window.window_id,
        "metric_path": selected_metric_path,
        "baseline": _snapshot_identity(baseline_snapshot, baseline_value),
        "observation": _snapshot_identity(observation_snapshot, observation_value),
    }
    fact_payload = identity_payload | {
        "baseline_value": baseline_value,
        "observation_value": observation_value,
    }
    evidence_payload = fact_payload | {
        "snapshot_hashes": snapshot_hashes,
        "comparability_state": state,
        "observation_days": observed_days,
        "confounders": confounders or [],
    }

    # The denominator is the baseline metric value: action verdict thresholds compare the
    # observation against that committed baseline, matching compare_periods' denominator.
    denominator = baseline_value if baseline_value is not None else 0.0
    return ActionEvidence(
        baseline_value=baseline_value if baseline_value is not None else 0.0,
        observation_value=observation_value if observation_value is not None else 0.0,
        denominator=denominator,
        observation_days=observed_days,
        as_of=as_of,
        input_fact_set_hash=_canonical_hash(fact_payload),
        evidence_set_hash=_canonical_hash(evidence_payload),
        snapshot_hashes=snapshot_hashes,
        input_derived_metric_ids=[selected_metric_path],
        comparability_state=state,
        confounders=confounders,
    )


def load_action(path: Path) -> SEOAction:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ActionError(f"Could not read action file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ActionError(f"Invalid TOML in action file {path}: {exc}") from exc
    return action_from_dict(raw)


def action_from_dict(raw: dict[str, Any]) -> SEOAction:
    required = [
        "action_id",
        "project_id",
        "changed_at",
        "action_type",
        "description",
        "hypothesis_id",
        "evidence_ref",
        "lifecycle_state",
    ]
    for field in required:
        if not str(raw.get(field, "")).strip():
            raise ActionError(f"Action field is required: {field}")
    lifecycle_state = str(raw["lifecycle_state"])
    if lifecycle_state not in LIFECYCLE_STATES:
        raise ActionError(f"Unsupported lifecycle_state: {lifecycle_state}")

    try:
        s = str(raw["changed_at"]).strip()
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        if len(s) == 10:
            date.fromisoformat(s)
        else:
            datetime.fromisoformat(s)
    except ValueError as exc:
        raise ActionError(f"Invalid changed_at date format '{raw['changed_at']}': {exc}") from exc

    targets = tuple(_target(item) for item in _required_list(raw, "targets"))
    windows = tuple(_window(item) for item in _required_list(raw, "windows"))
    expected_signals = tuple(_expected_signal(item) for item in _required_list(raw, "expected_signals"))
    confounders = tuple(str(item) for item in raw.get("confounders", []))
    supersedes = raw.get("supersedes_action_revision_hash")
    normalized_required = {key: str(raw[key]) for key in required}
    revision_payload = normalized_required | {
        "supersedes_action_revision_hash": supersedes,
        "targets": [asdict(item) for item in targets],
        "windows": [asdict(item) for item in windows],
        "expected_signals": [asdict(item) for item in expected_signals],
        "confounders": list(confounders),
    }
    revision_hash = _canonical_hash(revision_payload)
    return SEOAction(
        action_id=normalized_required["action_id"],
        project_id=normalized_required["project_id"],
        changed_at=normalized_required["changed_at"],
        action_type=normalized_required["action_type"],
        description=normalized_required["description"],
        hypothesis_id=normalized_required["hypothesis_id"],
        evidence_ref=normalized_required["evidence_ref"],
        lifecycle_state=lifecycle_state,
        targets=targets,
        windows=windows,
        expected_signals=expected_signals,
        confounders=confounders,
        revision_hash=revision_hash,
        supersedes_action_revision_hash=str(supersedes) if supersedes else None,
    )


def compute_action_verdict(
    action: SEOAction,
    window: MeasurementWindow,
    evidence: ActionEvidence,
    *,
    supersedes_verdict_id: str | None = None,
) -> ActionVerdict:
    absolute_delta = evidence.observation_value - evidence.baseline_value
    relative_delta = None if evidence.baseline_value == 0 else absolute_delta / evidence.baseline_value
    verdict = "inconclusive"
    actionable = True
    state = evidence.comparability_state
    explanation = "Evidence did not meet practical-effect thresholds."

    minimum_days = max((signal.minimum_observation_days for signal in action.expected_signals), default=0)
    if action.lifecycle_state == "tombstoned":
        verdict = "not_ready"
        actionable = False
        state = "tombstoned"
        explanation = "Action is tombstoned and is not actionable."
        absolute_delta = None
        relative_delta = None
    elif state != "comparable":
        verdict = "not_ready"
        actionable = False
        explanation = "Evidence is not comparable."
        absolute_delta = None
        relative_delta = None
    elif window.minimum_denominator is not None and evidence.denominator < window.minimum_denominator:
        verdict = "not_ready"
        actionable = False
        state = "denominator_too_small"
        explanation = "Evidence denominator is below the configured minimum."
        absolute_delta = None
        relative_delta = None
    elif _date(evidence.as_of) < _date(window.earliest_evaluation_date) or evidence.observation_days < minimum_days:
        verdict = "not_ready"
        actionable = False
        state = "minimum_window_not_met"
        explanation = "Minimum observation window has not elapsed."
        absolute_delta = None
        relative_delta = None
    elif evidence.confounders:
        verdict = "not_ready"
        actionable = False
        state = "confounded"
        explanation = "Evidence has active confounders."
        absolute_delta = None
        relative_delta = None
    else:
        verdict = _threshold_verdict(action.expected_signals, absolute_delta, relative_delta)
        explanation = f"Primary signal verdict is {verdict}."

    verdict_payload = {
        "action_revision_hash": action.revision_hash,
        "action_id": action.action_id,
        "window_id": window.window_id,
        "verdict": verdict,
        "absolute_delta": absolute_delta,
        "relative_delta": relative_delta,
        "input_fact_set_hash": evidence.input_fact_set_hash,
        "evidence_set_hash": evidence.evidence_set_hash,
        "input_derived_metric_ids": list(evidence.input_derived_metric_ids or []),
        "snapshot_hashes": sorted(evidence.snapshot_hashes),
        "comparability_state": state,
        "evaluation_policy_version": ACTION_POLICY_VERSION,
        "evaluation_as_of": evidence.as_of,
        "supersedes_verdict_id": supersedes_verdict_id,
    }
    return ActionVerdict(
        verdict_id=f"verdict:{_canonical_hash(verdict_payload)[:24]}",
        action_id=action.action_id,
        window_id=window.window_id,
        verdict=verdict,
        actionable=actionable,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        input_derived_metric_ids=tuple(evidence.input_derived_metric_ids or []),
        input_fact_set_hash=evidence.input_fact_set_hash,
        evidence_set_hash=evidence.evidence_set_hash,
        historical_snapshot_hashes=tuple(sorted(evidence.snapshot_hashes)),
        comparability_state=state,
        evaluation_policy_version=ACTION_POLICY_VERSION,
        evaluation_as_of=evidence.as_of,
        supersedes_verdict_id=supersedes_verdict_id,
        explanation=explanation,
    )


def persist_action(storage: SEOStorage, action: SEOAction) -> None:
    persist_actions(storage, [action])


def persist_actions(storage: SEOStorage, actions: list[SEOAction]) -> None:
    storage.bootstrap()
    with storage.connect() as con:
        for action in actions:
            _persist_action_con(con, action)


def _persist_action_con(con: sqlite3.Connection, action: SEOAction) -> None:
    con.execute(
        """
        INSERT INTO seo_actions(
          action_id, project_id, changed_at, action_type, description, hypothesis_id,
          evidence_ref, lifecycle_state, action_revision_hash, supersedes_action_revision_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(action_id, action_revision_hash) DO UPDATE SET
          changed_at = excluded.changed_at,
          action_type = excluded.action_type,
          description = excluded.description,
          hypothesis_id = excluded.hypothesis_id,
          evidence_ref = excluded.evidence_ref,
          lifecycle_state = excluded.lifecycle_state,
          action_revision_hash = excluded.action_revision_hash,
          supersedes_action_revision_hash = excluded.supersedes_action_revision_hash
        """,
        (
            action.action_id,
            action.project_id,
            action.changed_at,
            action.action_type,
            action.description,
            action.hypothesis_id,
            action.evidence_ref,
            action.lifecycle_state,
            action.revision_hash,
            action.supersedes_action_revision_hash,
        ),
    )
    con.execute(
        "DELETE FROM action_targets WHERE action_id = ? AND action_revision_hash = ?",
        (action.action_id, action.revision_hash),
    )
    for target in action.targets:
        con.execute(
            """
            INSERT INTO action_targets(
              action_id, action_revision_hash, target_type, target_value, target_role
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                action.action_id,
                action.revision_hash,
                target.target_type,
                target.target_value,
                target.target_role,
            ),
        )
    for window in action.windows:
        existing_window = con.execute(
            """
            SELECT action_id
            FROM measurement_windows
            WHERE window_id = ? AND action_revision_hash = ?
            """,
            (window.window_id, action.revision_hash),
        ).fetchone()
        if existing_window is not None and existing_window[0] != action.action_id:
            raise ActionError(f"measurement window belongs to another action: {window.window_id}")
        con.execute(
            """
            INSERT INTO measurement_windows(
              window_id, action_id, action_revision_hash, baseline_start, baseline_end,
              observation_start, observation_end, timezone, comparison_strategy,
              minimum_denominator, earliest_evaluation_date, confounder_notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(window_id, action_revision_hash) DO UPDATE SET
              baseline_start = excluded.baseline_start,
              baseline_end = excluded.baseline_end,
              observation_start = excluded.observation_start,
              observation_end = excluded.observation_end,
              timezone = excluded.timezone,
              comparison_strategy = excluded.comparison_strategy,
              minimum_denominator = excluded.minimum_denominator,
              earliest_evaluation_date = excluded.earliest_evaluation_date,
              confounder_notes = excluded.confounder_notes
            """,
            (
                window.window_id,
                action.action_id,
                action.revision_hash,
                window.baseline_start,
                window.baseline_end,
                window.observation_start,
                window.observation_end,
                window.timezone,
                window.comparison_strategy,
                window.minimum_denominator,
                window.earliest_evaluation_date,
                window.confounder_notes,
            ),
        )


def persist_action_verdict(
    storage: SEOStorage,
    action: SEOAction,
    window: MeasurementWindow,
    verdict: ActionVerdict,
) -> None:
    storage.bootstrap()
    with storage.connect() as con:
        _persist_action_con(con, action)
        existing_window = con.execute(
            """
            SELECT action_id
            FROM measurement_windows
            WHERE window_id = ? AND action_revision_hash = ?
            """,
            (window.window_id, action.revision_hash),
        ).fetchone()
        if existing_window is not None and existing_window[0] != action.action_id:
            raise ActionError(f"measurement window belongs to another action: {window.window_id}")
        con.execute(
            """
            INSERT INTO measurement_windows(
              window_id, action_id, action_revision_hash, baseline_start, baseline_end,
              observation_start, observation_end, timezone, comparison_strategy,
              minimum_denominator, earliest_evaluation_date, confounder_notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(window_id, action_revision_hash) DO UPDATE SET
              baseline_start = excluded.baseline_start,
              baseline_end = excluded.baseline_end,
              observation_start = excluded.observation_start,
              observation_end = excluded.observation_end,
              timezone = excluded.timezone,
              comparison_strategy = excluded.comparison_strategy,
              minimum_denominator = excluded.minimum_denominator,
              earliest_evaluation_date = excluded.earliest_evaluation_date,
              confounder_notes = excluded.confounder_notes
            """,
            (
                window.window_id,
                action.action_id,
                action.revision_hash,
                window.baseline_start,
                window.baseline_end,
                window.observation_start,
                window.observation_end,
                window.timezone,
                window.comparison_strategy,
                window.minimum_denominator,
                window.earliest_evaluation_date,
                window.confounder_notes,
            ),
        )
        con.execute(
            """
            INSERT INTO action_verdicts(
              verdict_id, action_id, action_revision_hash, window_id, verdict,
              absolute_delta, relative_delta, input_derived_metric_ids_json,
              input_fact_set_hash, evidence_set_hash, historical_snapshot_hashes_json,
              comparability_state, evaluation_policy_version, evaluation_as_of,
              supersedes_verdict_id, explanation
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verdict.verdict_id,
                verdict.action_id,
                action.revision_hash,
                verdict.window_id,
                verdict.verdict,
                verdict.absolute_delta,
                verdict.relative_delta,
                _canonical_json(list(verdict.input_derived_metric_ids)),
                verdict.input_fact_set_hash,
                verdict.evidence_set_hash,
                _canonical_json(list(verdict.historical_snapshot_hashes)),
                verdict.comparability_state,
                verdict.evaluation_policy_version,
                verdict.evaluation_as_of,
                verdict.supersedes_verdict_id,
                verdict.explanation,
            ),
        )


def _target(raw: dict[str, Any]) -> ActionTarget:
    target_type = str(raw.get("target_type", ""))
    target_role = str(raw.get("target_role", ""))
    if target_type not in TARGET_TYPES:
        raise ActionError(f"Unsupported target_type: {target_type}")
    if target_role not in TARGET_ROLES:
        raise ActionError(f"Unsupported target_role: {target_role}")
    target_value = str(raw.get("target_value", "")).strip()
    if not target_value:
        raise ActionError("target_value is required")
    return ActionTarget(target_type=target_type, target_value=target_value, target_role=target_role)


def _window(raw: dict[str, Any]) -> MeasurementWindow:
    required = [
        "window_id",
        "baseline_start",
        "baseline_end",
        "observation_start",
        "observation_end",
        "timezone",
        "comparison_strategy",
        "earliest_evaluation_date",
    ]
    for field in required:
        if not str(raw.get(field, "")).strip():
            raise ActionError(f"Window field is required: {field}")
    minimum_denominator = raw.get("minimum_denominator")
    return MeasurementWindow(
        window_id=str(raw["window_id"]),
        baseline_start=str(raw["baseline_start"]),
        baseline_end=str(raw["baseline_end"]),
        observation_start=str(raw["observation_start"]),
        observation_end=str(raw["observation_end"]),
        timezone=str(raw["timezone"]),
        comparison_strategy=str(raw["comparison_strategy"]),
        minimum_denominator=int(minimum_denominator) if minimum_denominator is not None else None,
        earliest_evaluation_date=str(raw["earliest_evaluation_date"]),
        confounder_notes=str(raw.get("confounder_notes", "")),
    )


def _expected_signal(raw: dict[str, Any]) -> ExpectedSignal:
    metric_path = str(raw.get("metric_path", "")).strip()
    direction = str(raw.get("direction", ""))
    if not metric_path:
        raise ActionError("expected_signals.metric_path is required")
    if direction not in DIRECTIONS:
        raise ActionError(f"Unsupported expected signal direction: {direction}")
    return ExpectedSignal(
        metric_path=metric_path,
        direction=direction,
        minimum_absolute_delta=_optional_float(raw.get("minimum_absolute_delta")),
        minimum_relative_delta=_optional_float(raw.get("minimum_relative_delta")),
        minimum_observation_days=int(raw.get("minimum_observation_days", 0)),
    )


def _threshold_verdict(
    signals: tuple[ExpectedSignal, ...],
    absolute_delta: float,
    relative_delta: float | None,
) -> str:
    signal_verdicts = tuple(_signal_threshold_verdict(signal, absolute_delta, relative_delta) for signal in signals)
    if "negative" in signal_verdicts:
        return "negative"
    if all(verdict == "positive" for verdict in signal_verdicts):
        return "positive"
    return "inconclusive"


def _signal_threshold_verdict(
    signal: ExpectedSignal,
    absolute_delta: float,
    relative_delta: float | None,
) -> str:
    directional_delta = absolute_delta if signal.direction == "increase" else -absolute_delta
    relative_directional_delta = (
        None
        if relative_delta is None
        else relative_delta if signal.direction == "increase" else -relative_delta
    )
    if directional_delta < 0:
        return "negative"
    absolute_ok = (
        signal.minimum_absolute_delta is None
        or directional_delta >= signal.minimum_absolute_delta
    )
    relative_ok = (
        signal.minimum_relative_delta is None
        or (
            relative_directional_delta is not None
            and relative_directional_delta >= signal.minimum_relative_delta
        )
    )
    if absolute_ok and relative_ok:
        return "positive"
    return "inconclusive"


def _required_list(raw: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = raw.get(field)
    if not isinstance(value, list) or not value:
        raise ActionError(f"Action field must be a non-empty array: {field}")
    if not all(isinstance(item, dict) for item in value):
        raise ActionError(f"Action field must contain tables: {field}")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActionError(f"Expected numeric threshold, got {value!r}")
    return float(value)


def _default_metric_path(action: SEOAction) -> str:
    if not action.expected_signals:
        raise ActionError("Action has no expected signals to derive metric_path")
    return action.expected_signals[0].metric_path


def _snapshot_hash_from_manifest(snapshot: dict[str, Any]) -> str:
    manifest = snapshot.get("manifest", {})
    if not isinstance(manifest, dict):
        return ""
    snapshot_hash = manifest.get("snapshot_hash")
    return str(snapshot_hash) if snapshot_hash is not None else ""


def _snapshot_identity(snapshot: dict[str, Any], value: float | None) -> dict[str, Any]:
    manifest = snapshot.get("manifest", {})
    if not isinstance(manifest, dict):
        manifest = {}
    return {
        "manifest_id": manifest.get("manifest_id"),
        "snapshot_hash": manifest.get("snapshot_hash"),
        "logical_evidence_ids": sorted(str(item) for item in manifest.get("logical_evidence_ids", [])),
        "evidence_ids": _snapshot_evidence_ids(snapshot),
        "value": value,
    }


def _snapshot_evidence_ids(snapshot: dict[str, Any]) -> list[str]:
    evidence = snapshot.get("evidence", {})
    if not isinstance(evidence, dict):
        return []
    ids: list[str] = []
    for value in evidence.values():
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, dict) and row.get("evidence_id") is not None:
                ids.append(str(row["evidence_id"]))
    return sorted(ids)


def _window_observation_days(window: MeasurementWindow) -> int:
    return (_date(window.observation_end) - _date(window.observation_start)).days + 1


def _date(value: str) -> date:
    s = value.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        return date.fromisoformat(s)
    except ValueError:
        return datetime.fromisoformat(s).date()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
