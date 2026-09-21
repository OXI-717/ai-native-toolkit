from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from seo_observer import __version__
from seo_observer.storage import SCHEMA_VERSION, SEOStorage


SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_RENDERER_VERSION = "snapshot-renderer-v1"
REDACTED = "[redacted]"


@dataclass(frozen=True)
class ExportPolicy:
    allow_exact_local_business_values: bool = False


@dataclass(frozen=True)
class WrittenSnapshot:
    snapshot: dict[str, Any]
    path: Path
    relative_path: str


def build_snapshot(
    storage: SEOStorage,
    *,
    project_id: str,
    reporting_period_id: str,
    generated_at: str,
    export_policy: ExportPolicy | None = None,
) -> dict[str, Any]:
    policy = export_policy or ExportPolicy()
    project = storage.fetchone(
        """
        SELECT project_id, timezone, config_hash, config_schema_version
        FROM projects
        WHERE project_id = ?
        """,
        (project_id,),
    )
    search_rows = _search_performance_rows(storage, project_id, reporting_period_id)
    outcome_rows = _outcome_metric_rows(storage, project_id, reporting_period_id)
    lineage = _lineage(storage, search_rows + outcome_rows)
    source_coverage = _source_coverage(search_rows + outcome_rows)
    descriptors = _logical_descriptors(lineage["source_requests"])
    evidence = {
        "search_performance": search_rows,
        "outcome_metrics": outcome_rows,
    }
    coverage = {
        "search_performance": _coverage_block(search_rows),
        "outcome_metrics": _coverage_block(outcome_rows),
    }
    formula_inputs = {
        "search_performance_totals": _search_totals(search_rows),
        "outcome_metric_inputs": _outcome_formula_inputs(outcome_rows),
    }
    snapshot: dict[str, Any] = {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "project": {
            "project_id": project["project_id"],
            "timezone": project["timezone"],
            "config_hash": project["config_hash"],
            "config_schema_version": project["config_schema_version"],
        },
        "period": _period(search_rows + outcome_rows, reporting_period_id, project["timezone"]),
        "versions": {
            "cli": __version__,
            "storage_schema": SCHEMA_VERSION,
            "snapshot_schema": SNAPSHOT_SCHEMA_VERSION,
            "renderer": SNAPSHOT_RENDERER_VERSION,
        },
        "privacy": {
            "policy": {
                "allow_exact_local_business_values": policy.allow_exact_local_business_values,
            },
            "redactions": [],
        },
        "manifest": {
            "manifest_id": "",
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "project_id": project_id,
            "reporting_period_id": reporting_period_id,
            "period_start": "",
            "period_end": "",
            "timezone": project["timezone"],
            "generated_at": generated_at,
            "source_coverage": source_coverage,
            "logical_evidence_descriptors": descriptors,
            "logical_evidence_ids": _logical_evidence_ids(search_rows + outcome_rows),
            "local_attempt_receipt": lineage["source_requests"],
            "snapshot_hash": "",
            "renderer_version": SNAPSHOT_RENDERER_VERSION,
        },
        "coverage": coverage,
        "formula_inputs": formula_inputs,
        "lineage": lineage,
        "evidence": evidence,
    }
    snapshot["privacy"]["redactions"] = _apply_export_policy(snapshot, policy)
    snapshot["manifest"]["period_start"] = snapshot["period"]["start"]
    snapshot["manifest"]["period_end"] = snapshot["period"]["end"]
    snapshot_hash = _snapshot_hash(snapshot)
    snapshot["manifest"]["snapshot_hash"] = snapshot_hash
    snapshot["manifest"]["manifest_id"] = f"manifest:{project_id}:{reporting_period_id}:{snapshot_hash[:16]}"
    return snapshot


def snapshot_git_path(snapshot: dict[str, Any]) -> str:
    manifest = snapshot["manifest"]
    return (
        "snapshots/v1/"
        f"{manifest['project_id']}/"
        f"{manifest['reporting_period_id']}/"
        f"snapshot-{manifest['snapshot_hash'][:16]}.json"
    )


def write_snapshot(
    storage: SEOStorage,
    *,
    output_root: Path,
    project_id: str,
    reporting_period_id: str,
    generated_at: str,
    export_policy: ExportPolicy | None = None,
) -> WrittenSnapshot:
    snapshot = build_snapshot(
        storage,
        project_id=project_id,
        reporting_period_id=reporting_period_id,
        generated_at=generated_at,
        export_policy=export_policy,
    )
    relative_path = snapshot_git_path(snapshot)
    path = output_root / relative_path
    _assert_no_forbidden_values(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(snapshot, indent=2) + "\n", encoding="utf-8")
    _record_manifest(storage, snapshot)
    return WrittenSnapshot(snapshot=snapshot, path=path, relative_path=relative_path)


def render_markdown_report(snapshot: dict[str, Any]) -> str:
    totals = snapshot["formula_inputs"]["search_performance_totals"]
    manifest = snapshot["manifest"]
    lines = [
        f"# SEO snapshot: {manifest['project_id']} / {manifest['reporting_period_id']}",
        "",
        f"- Snapshot hash: `{manifest['snapshot_hash']}`",
        f"- Period: {snapshot['period']['start']}..{snapshot['period']['end']} ({snapshot['period']['timezone']})",
        f"- Search performance rows: {snapshot['coverage']['search_performance']['row_count']}",
        f"- Clicks: {totals['clicks']}",
        f"- Impressions: {totals['impressions']}",
        f"- CTR: {totals['ctr']}",
        "",
        "## Source Coverage",
    ]
    for source, coverage in sorted(manifest["source_coverage"].items()):
        lines.append(
            f"- {source}: {coverage['current_evidence_rows']} rows, "
            f"{len(coverage['logical_observation_keys'])} logical observations"
        )
    if snapshot["privacy"]["redactions"]:
        lines.extend(["", "## Privacy", f"- Redactions: {len(snapshot['privacy']['redactions'])}"])
    return "\n".join(lines) + "\n"


def render_telegram_summary(snapshot: dict[str, Any], *, max_chars: int = 900) -> str:
    totals = snapshot["formula_inputs"]["search_performance_totals"]
    manifest = snapshot["manifest"]
    text = (
        f"{manifest['project_id']} {manifest['reporting_period_id']}: "
        f"Clicks {totals['clicks']}, impressions {totals['impressions']}, "
        f"CTR {totals['ctr']}. "
        f"Evidence rows {snapshot['coverage']['search_performance']['row_count']}. "
        f"Snapshot {manifest['snapshot_hash'][:12]}."
    )
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return text[: max_chars - 1].rstrip() + "…"


def rebuild_storage_from_snapshot(snapshot: dict[str, Any], storage: SEOStorage) -> None:
    storage.bootstrap()
    project = snapshot["project"]
    with storage.connect() as con:
        con.execute(
            """
            INSERT INTO projects(project_id, timezone, config_hash, config_schema_version)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO NOTHING
            """,
            (
                project["project_id"],
                project["timezone"],
                project["config_hash"],
                project["config_schema_version"],
            ),
        )
        for run in snapshot["lineage"]["collection_runs"]:
            con.execute(
                """
                INSERT INTO collection_runs(
                  run_id, project_id, period_start, period_end, timezone, started_at,
                  finished_at, status, config_hash, cli_version, db_schema_version,
                  config_schema_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (
                    run["run_id"],
                    run["project_id"],
                    run["period_start"],
                    run["period_end"],
                    run["timezone"],
                    run["started_at"],
                    run["finished_at"],
                    run["status"],
                    run["config_hash"],
                    run["cli_version"],
                    run["db_schema_version"],
                    run["config_schema_version"],
                ),
            )
        for request in snapshot["lineage"]["source_requests"]:
            con.execute(
                """
                INSERT INTO source_requests(
                  request_id, run_id, source, property_id, logical_observation_key,
                  collection_attempt_key, request_descriptor_json, attempt, queried_at,
                  completed_at, transport_status, freshness, sampled, sample_share,
                  data_lag_seconds, row_limit, rows_received, pages_expected, pages_received,
                  error_code, error_summary
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO NOTHING
                """,
                (
                    request["request_id"],
                    request["run_id"],
                    request["source"],
                    request["property_id"],
                    request["logical_observation_key"],
                    request["collection_attempt_key"],
                    _canonical_json(request["request_descriptor"]),
                    request["attempt"],
                    request["queried_at"],
                    request["completed_at"],
                    request["transport_status"],
                    request["freshness"],
                    int(request["sampled"]),
                    request["sample_share"],
                    request["data_lag_seconds"],
                    request["row_limit"],
                    request["rows_received"],
                    request["pages_expected"],
                    request["pages_received"],
                    request["error_code"],
                    request["error_summary"],
                ),
            )
        for artifact in snapshot["lineage"]["raw_artifacts"]:
            con.execute(
                """
                INSERT INTO raw_artifacts(
                  artifact_id, request_id, relative_path, sha256, content_type,
                  compression, redaction_state, byte_size
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO NOTHING
                """,
                (
                    artifact["artifact_id"],
                    artifact["request_id"],
                    artifact["relative_path"],
                    artifact["sha256"],
                    artifact["content_type"],
                    artifact["compression"],
                    artifact["redaction_state"],
                    artifact["byte_size"],
                ),
            )
        for row in snapshot["evidence"]["search_performance"]:
            con.execute(
                """
                INSERT INTO search_performance(
                  project_id, property_id, source, effective_start, effective_end,
                  source_timezone, effective_instant_start, effective_instant_end,
                  observed_at, reporting_period_id, request_id, artifact_id,
                  logical_observation_key, collection_attempt_key, query_id, query_text,
                  page_id, page_url, search_engine, device, country, region, segment_id,
                  impressions, clicks, ctr, average_position, dataset_coverage, sampled,
                  sample_share, freshness, comparability, fact_schema_version,
                  normalizer_version, is_current, supersedes_fact_id, superseded_by_fact_id,
                  content_hash
                )
                VALUES (
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """
                ,
                (
                    row["project_id"],
                    row["property_id"],
                    row["source"],
                    row["effective_start"],
                    row["effective_end"],
                    row["source_timezone"],
                    row["effective_instant_start"],
                    row["effective_instant_end"],
                    row["observed_at"],
                    row["reporting_period_id"],
                    row["request_id"],
                    row["artifact_id"],
                    row["logical_observation_key"],
                    row["collection_attempt_key"],
                    row["query_id"],
                    row["query_text"],
                    row["page_id"],
                    row["page_url"],
                    row["search_engine"],
                    row["device"],
                    row["country"],
                    row["region"],
                    row["segment_id"],
                    row["impressions"],
                    row["clicks"],
                    row["ctr"],
                    row["average_position"],
                    row["dataset_coverage"],
                    int(row["sampled"]),
                    row["sample_share"],
                    row["freshness"],
                    row["comparability"],
                    row["fact_schema_version"],
                    row["normalizer_version"],
                    1,
                    None,
                    None,
                    row["content_hash"],
                ),
            )


def _search_performance_rows(
    storage: SEOStorage,
    project_id: str,
    reporting_period_id: str,
) -> list[dict[str, Any]]:
    rows = storage.fetchall(
        """
        SELECT *
        FROM search_performance
        WHERE project_id = ? AND reporting_period_id = ? AND is_current = 1
        ORDER BY logical_observation_key, effective_start, effective_end, query_id,
                 page_id, search_engine, device, country, region, segment_id
        """,
        (project_id, reporting_period_id),
    )
    return [_export_search_row(row) for row in rows]


def _outcome_metric_rows(
    storage: SEOStorage,
    project_id: str,
    reporting_period_id: str,
) -> list[dict[str, Any]]:
    bounds = _reporting_period_bounds(storage, project_id, reporting_period_id)
    if bounds is None:
        return []
    period_start, period_end = bounds
    rows = storage.fetchall(
        """
        SELECT *
        FROM outcome_metrics
        WHERE project_id = ? AND is_current = 1
          AND effective_start <= ?
          AND effective_end >= ?
        ORDER BY logical_observation_key, outcome_id, property_id, source
        """,
        (project_id, period_end, period_start),
    )
    return [_export_outcome_row(row) for row in rows]


def _reporting_period_bounds(
    storage: SEOStorage,
    project_id: str,
    reporting_period_id: str,
) -> tuple[str, str] | None:
    search_bounds = storage.fetchone(
        """
        SELECT MIN(effective_start) AS period_start, MAX(effective_end) AS period_end
        FROM search_performance
        WHERE project_id = ? AND reporting_period_id = ? AND is_current = 1
        """,
        (project_id, reporting_period_id),
    )
    if search_bounds and search_bounds["period_start"] and search_bounds["period_end"]:
        return (search_bounds["period_start"], search_bounds["period_end"])

    run_bounds = _matching_collection_run_bounds(storage, project_id, reporting_period_id)
    if run_bounds is not None:
        return run_bounds

    iso_week_bounds = _iso_week_bounds(reporting_period_id)
    if iso_week_bounds is not None:
        return iso_week_bounds
    return None


def _matching_collection_run_bounds(
    storage: SEOStorage,
    project_id: str,
    reporting_period_id: str,
) -> tuple[str, str] | None:
    run_bounds = storage.fetchone(
        """
        SELECT MIN(period_start) AS period_start, MAX(period_end) AS period_end
        FROM collection_runs AS runs
        WHERE runs.project_id = ?
          AND EXISTS (
            SELECT 1
            FROM source_requests AS requests
            WHERE requests.run_id = runs.run_id
              AND (
                json_extract(requests.request_descriptor_json, '$.period_id') = ?
                OR json_extract(requests.request_descriptor_json, '$.reporting_period_id') = ?
              )
          )
        """,
        (project_id, reporting_period_id, reporting_period_id),
    )
    if run_bounds and run_bounds["period_start"] and run_bounds["period_end"]:
        return (run_bounds["period_start"], run_bounds["period_end"])
    return None


def _iso_week_bounds(reporting_period_id: str) -> tuple[str, str] | None:
    try:
        year_text, week_text = reporting_period_id.split("-W", 1)
        week_start = date.fromisocalendar(int(year_text), int(week_text), 1)
    except (TypeError, ValueError):
        return None
    week_end = week_start + timedelta(days=6)
    return (week_start.isoformat(), week_end.isoformat())


def _export_search_row(row: dict[str, Any]) -> dict[str, Any]:
    exported = {
        key: row[key]
        for key in (
            "project_id",
            "property_id",
            "source",
            "effective_start",
            "effective_end",
            "source_timezone",
            "effective_instant_start",
            "effective_instant_end",
            "observed_at",
            "reporting_period_id",
            "request_id",
            "artifact_id",
            "logical_observation_key",
            "collection_attempt_key",
            "query_id",
            "query_text",
            "page_id",
            "page_url",
            "search_engine",
            "device",
            "country",
            "region",
            "segment_id",
            "impressions",
            "clicks",
            "ctr",
            "average_position",
            "dataset_coverage",
            "sampled",
            "sample_share",
            "freshness",
            "comparability",
            "fact_schema_version",
            "normalizer_version",
            "content_hash",
        )
    }
    exported["sampled"] = bool(exported["sampled"])
    exported["evidence_id"] = "ev:search_performance:" + _hash(
        {
            key: exported[key]
            for key in (
                "logical_observation_key",
                "effective_start",
                "effective_end",
                "query_id",
                "page_id",
                "search_engine",
                "device",
                "country",
                "region",
                "segment_id",
            )
        }
    )[:16]
    return _sort_dict(exported)


def _export_outcome_row(row: dict[str, Any]) -> dict[str, Any]:
    exported = {
        key: row[key]
        for key in (
            "project_id",
            "property_id",
            "source",
            "effective_start",
            "effective_end",
            "source_timezone",
            "request_id",
            "artifact_id",
            "logical_observation_key",
            "collection_attempt_key",
            "outcome_id",
            "evidence_kind",
            "counting_unit",
            "deduplication_rule",
            "population_scope",
            "attribution_model",
            "attribution_scope",
            "traffic_channel",
            "search_engine",
            "landing_page_id",
            "device",
            "attribution_level",
            "count",
            "unique_actors",
            "value_minor",
            "currency",
            "dataset_coverage",
            "freshness",
            "comparability",
            "fact_schema_version",
            "normalizer_version",
        )
    }
    exported["evidence_id"] = "ev:outcome_metrics:" + _hash(
        {
            key: exported[key]
            for key in (
                "logical_observation_key",
                "effective_start",
                "effective_end",
                "outcome_id",
                "property_id",
                "source",
                "attribution_scope",
                "traffic_channel",
                "search_engine",
                "landing_page_id",
                "device",
            )
        }
    )[:16]
    return _sort_dict(exported)


_LOCAL_OUTCOME_EXACT_FIELDS = ("count", "unique_actors", "value_minor", "currency")


def _apply_export_policy(snapshot: dict[str, Any], policy: ExportPolicy) -> list[dict[str, str]]:
    if policy.allow_exact_local_business_values:
        return []
    redactions: list[dict[str, str]] = []
    surfaces = (
        ("evidence.outcome_metrics", snapshot["evidence"]["outcome_metrics"]),
        ("formula_inputs.outcome_metric_inputs", snapshot["formula_inputs"]["outcome_metric_inputs"]),
    )
    for surface_path, rows in surfaces:
        for index, row in enumerate(rows):
            for key in _LOCAL_OUTCOME_EXACT_FIELDS:
                if row.get(key) is None:
                    continue
                row[key] = REDACTED
                redactions.append(
                    {
                        "path": f"{surface_path}[{index}].{key}",
                        "reason": "exact_local_business_value",
                    }
                )
    return redactions


def _lineage(storage: SEOStorage, evidence_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    request_ids = sorted({row["request_id"] for row in evidence_rows})
    artifact_ids = sorted({row["artifact_id"] for row in evidence_rows})
    requests = _rows_by_ids(
        storage,
        "source_requests",
        "request_id",
        request_ids,
        "request_id",
    )
    for request in requests:
        request["request_descriptor"] = json.loads(request.pop("request_descriptor_json"))
        request["sampled"] = bool(request["sampled"])
    run_ids = sorted({request["run_id"] for request in requests})
    return {
        "collection_runs": _rows_by_ids(storage, "collection_runs", "run_id", run_ids, "run_id"),
        "source_requests": requests,
        "raw_artifacts": _rows_by_ids(
            storage,
            "raw_artifacts",
            "artifact_id",
            artifact_ids,
            "relative_path",
        ),
    }


def _rows_by_ids(
    storage: SEOStorage,
    table: str,
    id_column: str,
    ids: list[str],
    order_column: str,
) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return storage.fetchall(
        f"SELECT * FROM {table} WHERE {id_column} IN ({placeholders}) ORDER BY {order_column}",
        tuple(ids),
    )


def _source_coverage(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = row["source"]
        item = coverage.setdefault(
            source,
            {
                "current_evidence_rows": 0,
                "logical_observation_keys": [],
                "dataset_coverage": {},
                "freshness": {},
            },
        )
        item["current_evidence_rows"] += 1
        if row["logical_observation_key"] not in item["logical_observation_keys"]:
            item["logical_observation_keys"].append(row["logical_observation_key"])
        item["dataset_coverage"][row["dataset_coverage"]] = (
            item["dataset_coverage"].get(row["dataset_coverage"], 0) + 1
        )
        item["freshness"][row["freshness"]] = item["freshness"].get(row["freshness"], 0) + 1
    for item in coverage.values():
        item["logical_observation_keys"].sort()
    return dict(sorted(coverage.items()))


def _logical_descriptors(requests: list[dict[str, Any]]) -> list[dict[str, str]]:
    descriptors = []
    seen = set()
    for request in requests:
        key = (
            request["source"],
            request["property_id"],
            request["logical_observation_key"],
        )
        if key in seen:
            continue
        seen.add(key)
        descriptors.append(
            {
                "descriptor_id": ":".join(("desc", *key)),
                "source": request["source"],
                "property_id": request["property_id"],
                "logical_observation_key": request["logical_observation_key"],
                "request_descriptor_hash": _hash(request["request_descriptor"]),
            }
        )
    return sorted(descriptors, key=lambda item: item["descriptor_id"])


def _logical_evidence_ids(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(row["evidence_id"] for row in rows)


def _coverage_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "row_count": len(rows),
        "dataset_coverage": _count_values(rows, "dataset_coverage"),
        "freshness": _count_values(rows, "freshness"),
        "comparability": _count_values(rows, "comparability"),
        "sampled_rows": sum(1 for row in rows if row.get("sampled")),
    }


def _count_values(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row[key])
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _search_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    impressions = sum(int(row["impressions"]) for row in rows)
    clicks = sum(int(row["clicks"]) for row in rows)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions, 6) if impressions else None,
    }


def _outcome_formula_inputs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": row["evidence_id"],
            "outcome_id": row["outcome_id"],
            "count": row["count"],
            "unique_actors": row["unique_actors"],
            "value_minor": row["value_minor"],
            "currency": row["currency"],
        }
        for row in rows
    ]


def _period(
    rows: list[dict[str, Any]],
    reporting_period_id: str,
    timezone: str,
) -> dict[str, str]:
    if rows:
        start = min(row["effective_start"] for row in rows)
        end = max(row["effective_end"] for row in rows)
    else:
        start = ""
        end = ""
    return {
        "reporting_period_id": reporting_period_id,
        "start": start,
        "end": end,
        "timezone": timezone,
    }


def _record_manifest(storage: SEOStorage, snapshot: dict[str, Any]) -> None:
    manifest = snapshot["manifest"]
    with storage.connect() as con:
        con.execute(
            """
            INSERT INTO snapshot_manifests(
              manifest_id, project_id, reporting_period_id, snapshot_schema_version,
              period_start, period_end, timezone, generated_at, source_quality_json,
              snapshot_hash, report_hash, logical_evidence_ids_json,
              local_attempt_receipt_json, renderer_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(manifest_id) DO NOTHING
            """,
            (
                manifest["manifest_id"],
                manifest["project_id"],
                manifest["reporting_period_id"],
                manifest["snapshot_schema_version"],
                manifest["period_start"],
                manifest["period_end"],
                manifest["timezone"],
                manifest["generated_at"],
                _canonical_json(manifest["source_coverage"]),
                manifest["snapshot_hash"],
                None,
                _canonical_json(manifest["logical_evidence_ids"]),
                _canonical_json(manifest["local_attempt_receipt"]),
                manifest["renderer_version"],
            ),
        )


def _assert_no_forbidden_values(snapshot: dict[str, Any]) -> None:
    policy = snapshot["privacy"]["policy"]
    if policy["allow_exact_local_business_values"]:
        return
    surfaces = (
        ("evidence.outcome_metrics", snapshot["evidence"]["outcome_metrics"]),
        ("formula_inputs.outcome_metric_inputs", snapshot["formula_inputs"]["outcome_metric_inputs"]),
    )
    for surface_path, rows in surfaces:
        for index, row in enumerate(rows):
            for key in _LOCAL_OUTCOME_EXACT_FIELDS:
                if row.get(key) is None:
                    continue
                if row.get(key) != REDACTED:
                    path = f"{surface_path}[{index}].{key}"
                    raise ValueError(
                        f"Exact local business value at {path} must be redacted before export."
                    )


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    material = json.loads(_canonical_json(snapshot))
    material["manifest"]["generated_at"] = "<generated_at>"
    material["manifest"]["snapshot_hash"] = ""
    material["manifest"]["manifest_id"] = ""
    return _hash(material)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if indent is None else None,
        indent=indent,
    )


def _sort_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}
