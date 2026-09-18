"""Read existing Observer evidence; never collect, bootstrap, or approve exports.

Executed with the installed Observer interpreter so Hub need not vendor its API.
Only allowlisted summary fields cross this boundary, never provider/query rows.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any


MAX_JSON_BYTES = 32_000_000
STALE_AFTER_DAYS = 14


def _schema_matches(value: object, public_name: str) -> bool:
    text = str(value or "")
    return text == public_name or text.endswith(public_name)


def _json(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("evidence file exceeds size limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("evidence must be an object")
    return value


def readonly_storage(path: Path):
    from seo_observer.storage import SEOStorage

    class ReadOnlyStorage(SEOStorage):
        @contextmanager
        def connect(self):
            con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only = ON")
            try:
                yield con
            finally:
                con.close()

        def bootstrap(self):
            raise RuntimeError("existing evidence storage is read-only")

    return ReadOnlyStorage(path)


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _stamp(value: Any) -> str | None:
    parsed = _date(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def _numbers(value: Any, keys: tuple[str, ...]) -> dict[str, float | int]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if type(value.get(key)) in (int, float) and math.isfinite(value[key])}


def _quality(entry: dict[str, Any], now: datetime) -> None:
    dates = [_date(entry.get(key)) for key in ("observed_at", "effective_end")]
    ages = [(now.date() - value.date()).days for value in dates if value]
    entry["age_days"] = max(ages) if ages else None
    if entry.get("source_quality") in {"missing", "unsupported"} or not ages:
        entry["quality"] = "missing"
        entry["freshness_state"] = "unknown"
    elif max(ages) > STALE_AFTER_DAYS or entry.get("source_quality") == "stale" or "stale" in entry.get("freshness", []):
        entry["quality"] = "stale"
        entry["freshness_state"] = "stale"
    else:
        entry["quality"] = "partial"
        entry["freshness_state"] = "dated"


def _verified(manifest_path: Path, descriptor: dict[str, Any]) -> Path:
    name, digest = descriptor.get("path"), descriptor.get("sha256")
    if not isinstance(name, str) or not isinstance(digest, str):
        raise ValueError("artifact requires path and hash")
    path = (manifest_path.parent / name).resolve()
    if not path.is_relative_to(manifest_path.parent.resolve()):
        raise ValueError("artifact escapes manifest directory")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("artifact exceeds size limit")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest.removeprefix("sha256:"):
        raise ValueError("artifact hash mismatch")
    return path


def _manifest_entries(path: Path, project: str) -> list[dict[str, Any]]:
    manifest = _json(path)
    if manifest.get("project") != project:
        return []
    files = manifest.get("files")
    artifacts = manifest.get("artifacts", [])
    descriptors = list(files.values()) if isinstance(files, dict) else artifacts
    if not isinstance(descriptors, list):
        raise ValueError("invalid artifact manifest")
    by_name = {Path(item["path"]).name: item for item in descriptors
               if isinstance(item, dict) and isinstance(item.get("path"), str)}
    provenance = {"manifest_path": str(path), "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if "serp-extract.json" in by_name:
        serp = _json(_verified(path, by_name["serp-extract.json"]))
        if not _schema_matches(serp.get("schema"), "seo-observer.serp_extract.v1"):
            raise ValueError("unsupported SERP schema")
        rows = serp.get("serp_rows", [])
        # Observer adapters persist __pending__ in normalized SERP rows; the
        # hash-verified manifest carries the actual project binding.
        if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("project_id") not in {project, "__pending__"} for row in rows):
            raise ValueError("SERP project mismatch")
        observed = max((stamp for row in rows if (stamp := _stamp(row.get("effective_at")))), default=None)
        serp_quality = serp.get("quality_summary", {})
        serp_quality = serp_quality.get("overall") if isinstance(serp_quality, dict) else None
        if serp_quality not in {"live", "partial", "stale", "missing", "unsupported", "local-only", "not_comparable"}:
            serp_quality = "unknown"
        metrics, public_metrics = {}, False
        if "competitor-metrics.json" in by_name:
            descriptor = by_name["competitor-metrics.json"]
            metrics = _json(_verified(path, descriptor))
            if not _schema_matches(metrics.get("schema"), "seo-observer.competitor_metrics.v1"):
                raise ValueError("unsupported competitor metrics schema")
            privacy = manifest.get("privacy", {})
            public_metrics = (descriptor.get("privacy_class") == "public_report_artifact"
                              and isinstance(privacy, dict) and not privacy.get("local_only", False))
        return [{
            "kind": "serp", "source": "serp", "keyword_set_id": str(serp.get("keyword_set_id", "")),
            "observed_at": observed, "created_at": _stamp(manifest.get("created_at")),
            "coverage": _numbers(serp.get("coverage"), ("coverage", "weighted_coverage", "expected_slots", "observed_slots")),
            "comparability": "comparable" if metrics.get("comparability") == "comparable" else "not_comparable",
            "conclusion_status": metrics.get("conclusion_status") if metrics.get("conclusion_status") in {"provisional", "insufficient_coverage", "confirmed"} else "unknown",
            "owned": [_numbers(row, ("best_rank", "median_rank", "visibility", "share_of_voice", "coverage", "weighted_coverage"))
                      for row in metrics.get("owned", []) if isinstance(row, dict)] if public_metrics else [],
            "source_quality": serp_quality,
            "private_rows_omitted": True, **provenance,
        }]
    if "provider-extract.json" in by_name:
        provider = _json(_verified(path, by_name["provider-extract.json"]))
        if provider.get("schema_version") != 1 or provider.get("command") != "provider-audit" or provider.get("project") != project:
            raise ValueError("unsupported provider extract")
        sources = provider.get("sources", {})
        if not isinstance(sources, dict):
            raise ValueError("invalid provider sources")
        entries = []
        for source in ("google_search_console", "yandex_metrica", "yandex_webmaster", "ga4", "serp", "competitor_research"):
            item = sources.get(source)
            if not isinstance(item, dict):
                continue
            quality = item.get("quality")
            if quality not in {"live", "partial", "stale", "missing", "unsupported", "local-only", "not_comparable"}:
                quality = "unknown"
            period = provider.get("period", {})
            if not isinstance(period, dict):
                raise ValueError("invalid provider period")
            entries.append({"kind": "provider_audit", "source": source,
                            "observed_at": _stamp(item.get("observed_at")),
                            "created_at": _stamp(manifest.get("created_at")),
                            "effective_start": _stamp(item.get("effective_start")),
                            "effective_end": _stamp(item.get("effective_end")),
                            "requested_period": {"start": _stamp(period.get("start")), "end": _stamp(period.get("end"))},
                            "source_quality": quality, "local_only": True,
                            "private_rows_omitted": True, **provenance})
        return entries
    return []


def _database_entries(path: Path, project: str, now: datetime) -> list[dict[str, Any]]:
    from seo_observer.snapshots import build_snapshot

    storage = readonly_storage(path)
    periods = storage.fetchall("SELECT DISTINCT reporting_period_id FROM search_performance WHERE project_id = ? AND is_current = 1", (project,))
    entries = []
    for period in periods:
        snapshot = build_snapshot(storage, project_id=project, reporting_period_id=period["reporting_period_id"], generated_at=now.isoformat())
        # The Observer export policy runs first. No lineage or evidence rows leave
        # this process, including redacted outcome rows and request descriptors.
        rows = snapshot["evidence"]["search_performance"]
        for source in sorted({row["source"] for row in rows}):
            selected = [row for row in rows if row["source"] == source]
            entries.append({
                "kind": "sqlite", "source": source, "database_path": str(path),
                "reporting_period_id": period["reporting_period_id"],
                "effective_start": min(row["effective_start"] for row in selected),
                "effective_end": max(row["effective_end"] for row in selected),
                "observed_at": max(row["observed_at"] for row in selected),
                "row_count": len(selected), "snapshot_hash": snapshot["manifest"]["snapshot_hash"],
                "freshness": sorted({row["freshness"] for row in selected}),
                "dataset_coverage": sorted({row["dataset_coverage"] for row in selected}),
                "private_rows_omitted": True,
            })
    traffic = storage.fetchall("""SELECT source, MIN(effective_start) AS effective_start,
        MAX(effective_end) AS effective_end, MAX(observed_at) AS observed_at, COUNT(*) AS row_count
        FROM traffic_metrics WHERE project_id = ? AND is_current = 1 GROUP BY source""", (project,))
    entries.extend({"kind": "sqlite", "database_path": str(path), "private_rows_omitted": True, **row} for row in traffic)
    return entries


def _manifests(config: Path, project: str, home: Path, roots: tuple[Path, ...]) -> list[Path]:
    repo = config.parent.parent
    search_roots = [repo / "audits", config.parent / "generated", home / "reports",
                    home / "projects" / project / "competitors", *roots]
    # Operator audits are local artifacts, often outside the configured repo.
    search_roots.extend(Path("/tmp").glob(f"{project}-*audit*"))
    search_roots.extend((repo.parent / "_tmp").glob("seo-audit*"))
    paths = set()
    for root in search_roots:
        if not root.is_dir():
            continue
        for pattern in ("manifest.json", "*/manifest.json", "*/*/manifest.json", "*/*/*/manifest.json", "*/*/*/*/manifest.json"):
            paths.update(p.resolve() for p in root.glob(pattern) if p.is_file())
            if len(paths) > 1000:
                raise ValueError("too many evidence manifests")
    return sorted(paths)


def read_existing_evidence(config: Path, *, home: Path | None = None,
                           roots: tuple[Path, ...] = (), now: datetime | None = None) -> dict[str, Any]:
    from seo_observer.config import load_project_config, observer_home

    config = Path(config).resolve()
    loaded = load_project_config(config)
    project = loaded.project.namespace
    home = home or observer_home()
    now = now or datetime.now(timezone.utc)
    entries, rejected = [], 0
    for manifest in _manifests(config, project, home, roots):
        try:
            entries.extend(_manifest_entries(manifest, project))
        except (OSError, ValueError, TypeError, KeyError):
            rejected += 1
    database = home / "projects" / project / "observer.db"
    if database.is_file():
        try:
            entries.extend(_database_entries(database, project, now))
        except (OSError, sqlite3.Error, ValueError, KeyError):
            rejected += 1
    for entry in entries:
        _quality(entry, now)
    if not entries:
        return {"ok": False, "quality": "missing", "error": {"code": "EXISTING_EVIDENCE_NOT_FOUND", "message": "No readable existing Observer evidence was found."}, "source_status": {"rejected_artifacts": rejected}}
    # Keep the latest artifact per source/keyword set, retaining SQLite as a
    # separate evidence family instead of pretending its history was refreshed.
    latest = {}
    for entry in entries:
        key = (entry["kind"], entry["source"], entry.get("keyword_set_id"), entry.get("reporting_period_id"))
        if key not in latest or (entry.get("observed_at") or entry.get("created_at") or "") > (latest[key].get("observed_at") or latest[key].get("created_at") or ""):
            latest[key] = entry
    entries = sorted(latest.values(), key=lambda item: (item["kind"], item["source"], item.get("keyword_set_id", ""), item.get("reporting_period_id", "")))
    dated = [item for item in entries if item["quality"] != "missing"]
    quality = "missing" if not dated else "stale" if all(item["quality"] == "stale" for item in dated) else "partial"
    return {"ok": True, "schema": "oxy-seo.observer_evidence.v1", "project": project,
            "quality": quality, "evidence": entries,
            "privacy": {"private_rows_omitted": True, "exact_local_business_values": False},
            "source_status": {"mode": "existing_evidence", "stale_after_days": STALE_AFTER_DAYS,
                              "rejected_artifacts": rejected, "evidence": entries},
            "summary": "Existing Observer evidence; source dates and coverage are preserved. No new collection or baseline approval."}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", action="append", default=[], type=Path)
    args = parser.parse_args()
    try:
        payload = read_existing_evidence(args.config, roots=tuple(args.root))
    except Exception as exc:
        # Exception text may contain config values or SQL; never forward it.
        payload = {"ok": False, "quality": "missing", "error": {"code": "EXISTING_EVIDENCE_UNAVAILABLE", "message": "Existing Observer evidence could not be read.", "type": type(exc).__name__}}
    print(json.dumps(payload, ensure_ascii=True, allow_nan=False))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
