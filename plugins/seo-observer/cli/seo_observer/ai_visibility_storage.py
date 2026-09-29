"""Local normalized AI visibility receipts and conservative action comparability."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

from seo_observer.ai_visibility_import import import_elmo_ai_visibility

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_visibility_imports (
    evidence_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    property_id TEXT NOT NULL,
    reporting_period_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    receipt_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_visibility_period
ON ai_visibility_imports(project_id, reporting_period_id, observed_at);
"""


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def ingest_ai_visibility(
    storage, envelope, *, project_id, property_id, reporting_period_id, observed_at
):
    if not isinstance(envelope, dict) or envelope.get("provider") != "elmo":
        raise ValueError("Expected an Elmo envelope")
    if (
        type(envelope.get("schema_version")) is not int
        or envelope["schema_version"] != 1
    ):
        raise ValueError("Unsupported Elmo source schema version")
    source_updated = (
        envelope.get("endpoints", {})
        .get("analytics", {})
        .get("payload", {})
        .get("updated_at")
    )
    if not isinstance(source_updated, str) or not source_updated.strip():
        raise ValueError("Elmo source updated_at is required")
    if not property_id or not reporting_period_id:
        raise ValueError("Property and reporting period are required")
    instant = dt.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    observed_at = instant.astimezone(dt.timezone.utc).isoformat()
    project = storage.fetchone(
        "SELECT timezone FROM projects WHERE project_id = ?", (project_id,)
    )
    if not project:
        raise ValueError("Project is not registered")
    properties = storage.fetchall(
        "SELECT property_id FROM properties WHERE project_id = ?", (project_id,)
    )
    if property_id not in {row["property_id"] for row in properties}:
        raise ValueError("Property is not registered for the project")
    result = import_elmo_ai_visibility(
        envelope,
        project_id=project_id,
        property_id=property_id,
        observed_at=observed_at,
    )
    window = result["window"]
    start = dt.date.fromisoformat(window["start"])
    end = dt.date.fromisoformat(window["end"])
    if end < start:
        raise ValueError("Invalid measurement window")
    ZoneInfo(window["timezone"])
    # The existing importer synthesizes UTC for missing source timezones. Require
    # an explicit source timezone before a stored result can become comparable.
    result = import_elmo_ai_visibility(
        envelope,
        project_id=project_id,
        property_id=property_id,
        observed_at=observed_at,
        expected_window={**window, "timezone": project["timezone"]},
    )
    updated = dt.datetime.fromisoformat(
        result["freshness"]["updated_at"].replace("Z", "+00:00")
    )
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=dt.timezone.utc)
    if (
        updated > instant
        or end >= instant.astimezone(ZoneInfo(window["timezone"])).date()
    ):
        result["quality"] = "not_comparable"
        result["comparability"] = {
            "state": "not_comparable",
            "reasons": ["future_or_open_window"],
        }
    receipt = {
        key: result[key]
        for key in (
            "provider",
            "project_id",
            "property_id",
            "observed_at",
            "quality",
            "prompt_set_hash",
            "window",
            "locale",
            "surface",
            "model",
            "cohorts",
            "coverage",
            "freshness",
            "comparability",
            "discovery_metrics",
            "platform_metrics",
        )
    }
    request = envelope.get("request") or {}
    receipt.update(
        schema_version=1,
        reporting_period_id=reporting_period_id,
        source_sha256=digest(envelope),
        brand_id=str(request.get("brand_id") or ""),
    )
    receipt["evidence_id"] = "ai-visibility:" + digest(receipt)
    store_receipt(storage, receipt)
    return receipt


def store_receipt(storage, receipt, connection=None):
    payload = {key: value for key, value in receipt.items() if key != "evidence_id"}
    if receipt.get("evidence_id") != "ai-visibility:" + digest(payload):
        raise ValueError("AI receipt hash mismatch")
    values = (
        receipt["evidence_id"],
        receipt["project_id"],
        receipt["property_id"],
        receipt["reporting_period_id"],
        receipt["observed_at"],
        canonical(receipt),
    )
    sql = "INSERT INTO ai_visibility_imports VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(evidence_id) DO NOTHING"
    if connection is not None:
        connection.execute(sql, values)
    else:
        with storage.connect() as con:
            con.execute(sql, values)


def current_receipt(storage, project_id, period_id):
    rows = storage.fetchall(
        "SELECT receipt_json FROM ai_visibility_imports WHERE project_id = ? AND reporting_period_id = ? ORDER BY observed_at DESC, evidence_id DESC",
        (project_id, period_id),
    )
    if not rows:
        return None
    receipts = [json.loads(row["receipt_json"]) for row in rows]
    if len({r["property_id"] for r in receipts}) != 1:
        raise ValueError(
            "AI period has multiple properties; use separate reporting period IDs"
        )
    # Tied but different receipts are ambiguous, never choose a favorable hash.
    if sum(r["observed_at"] == receipts[0]["observed_at"] for r in receipts) > 1:
        raise ValueError("Conflicting AI receipts share an observation timestamp")
    return receipts[0]


def attach_ai_visibility(snapshot, storage):
    receipt = current_receipt(
        storage,
        snapshot["project"]["project_id"],
        snapshot["period"]["reporting_period_id"],
    )
    if receipt is None:
        return
    snapshot["ai_visibility"] = receipt
    snapshot["evidence"]["ai_visibility"] = [
        {
            "evidence_id": receipt["evidence_id"],
            "source": "elmo",
            "dataset_coverage": receipt["coverage"]["state"],
            "freshness": receipt["freshness"]["state"],
            "comparability": receipt["comparability"]["state"],
            "sampled": False,
        }
    ]
    snapshot["coverage"]["ai_visibility"] = {"row_count": 1}
    observation_key = receipt["evidence_id"]
    snapshot["manifest"]["source_coverage"]["elmo"] = {
        "current_evidence_rows": 1,
        "logical_observation_keys": [observation_key],
        "dataset_coverage": {receipt["coverage"]["state"]: 1},
        "freshness": {receipt["freshness"]["state"]: 1},
    }
    snapshot["manifest"]["logical_evidence_descriptors"].append(
        {
            "descriptor_id": f"desc:elmo:{receipt['property_id']}:{observation_key}",
            "source": "elmo",
            "property_id": receipt["property_id"],
            "logical_observation_key": observation_key,
            "request_descriptor_hash": receipt["source_sha256"],
        }
    )
    snapshot["manifest"]["logical_evidence_descriptors"].sort(
        key=lambda row: row["descriptor_id"]
    )
    snapshot["manifest"]["logical_evidence_ids"].append(receipt["evidence_id"])
    snapshot["manifest"]["logical_evidence_ids"].sort()
    if not snapshot["period"]["start"]:
        snapshot["period"].update(receipt["window"])


def action_state(action, window, baseline, observation, metric_path):
    if not metric_path.startswith("ai_visibility.platform_metrics."):
        return None
    receipts = []
    for snapshot, start, end in (
        (baseline, window.baseline_start, window.baseline_end),
        (observation, window.observation_start, window.observation_end),
    ):
        from seo_observer.snapshots import _snapshot_hash

        if not isinstance(snapshot.get("manifest"), dict) or snapshot["manifest"].get(
            "snapshot_hash"
        ) != _snapshot_hash(snapshot):
            return "invalid_snapshot"
        receipt = snapshot.get("ai_visibility")
        if not isinstance(receipt, dict):
            return "missing"
        if receipt.get("schema_version") != 1:
            return "invalid_receipt"
        if receipt.get("evidence_id") != "ai-visibility:" + digest(
            {k: v for k, v in receipt.items() if k != "evidence_id"}
        ):
            return "invalid_receipt"
        ids = {
            r.get("evidence_id")
            for r in snapshot.get("evidence", {}).get("ai_visibility", [])
            if isinstance(r, dict)
        }
        if receipt["evidence_id"] not in ids:
            return "missing"

        if (
            snapshot.get("project", {}).get("project_id") != action.project_id
            or receipt.get("project_id") != action.project_id
        ):
            return "project_mismatch"
        actual = receipt.get("window", {})
        if actual != {"start": start, "end": end, "timezone": window.timezone}:
            return "period_mismatch"
        if receipt.get("coverage", {}).get("state") != "complete":
            return "partial"
        if receipt.get("freshness", {}).get("state") != "complete":
            return "stale"
        if receipt.get("comparability", {}).get("state") != "comparable":
            return "incomparable"
        if receipt.get("quality") != "complete":
            return "partial"
        parts = metric_path.split(".")
        if len(parts) != 4 or parts[3] not in (
            "mention_prompts",
            "negative_mention_prompts",
        ):
            return "metric_mismatch"
        platform = receipt.get("platform_metrics", {}).get(parts[2], {})
        if type(platform.get("prompts")) is not int or platform["prompts"] <= 0:
            return "missing"
        receipts.append(receipt)
    prefix = "elmo-ai-visibility:"
    if (
        action.evidence_ref.startswith(prefix)
        and action.evidence_ref != prefix + receipts[0]["prompt_set_hash"]
    ):
        return "action_evidence_mismatch"
    for field in (
        "provider",
        "property_id",
        "brand_id",
        "prompt_set_hash",
        "locale",
        "surface",
        "model",
        "cohorts",
    ):
        if not receipts[0].get(field) or receipts[0].get(field) != receipts[1].get(
            field
        ):
            return "protocol_mismatch"
    for key in ("expected_prompts", "executed_prompts"):
        counts = [r.get("discovery_metrics", {}).get(key) for r in receipts]
        if any(type(n) is not int or n <= 0 for n in counts) or counts[0] != counts[1]:
            return "prompt_population_mismatch"
    for receipt in receipts:
        if (
            receipt["discovery_metrics"]["expected_prompts"]
            != receipt["discovery_metrics"]["executed_prompts"]
        ):
            return "partial"
    return None
