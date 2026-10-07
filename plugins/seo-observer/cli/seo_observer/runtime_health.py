"""Runtime health journal: collect run journal and ``/health`` evaluation.

``collect --daily --state-file PATH`` appends one record per run to a small
JSON journal (atomically, last ``STATE_KEEP`` records). ``evaluate_health``
turns that journal plus the export ``receipt.json`` files into an
``(http_status, body)`` pair for the runtime server's ``/health`` endpoint —
it never opens the SQLite database, so it cannot block on a running collect.
The body carries no paths, provider error texts, or secrets.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATE_SCHEMA_VERSION = 1
STATE_KEEP = 5
MAX_AGE = timedelta(hours=26)
_OK_STATUSES = frozenset({"ok"})  # cli._write_collect_results success status


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO timestamp; naive or unparsable values raise ValueError."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed


def _day_statuses(day_payload: Any) -> dict[str, str]:
    """Map source name to its status for one day of `collect --daily`.

    A `COLLECT_PARTIAL` day carries the real per-source statuses under
    `error.details.sources`. Other error days (`{"ok": false, "error": …}`
    with no sources anywhere) yield no live source.
    """
    if not isinstance(day_payload, dict):
        return {}
    sources = day_payload.get("sources")
    if not isinstance(sources, dict):
        error = day_payload.get("error")
        details = error.get("details") if isinstance(error, dict) else None
        if isinstance(details, dict):
            sources = details.get("sources")
    if not isinstance(sources, dict):
        return {}
    return {str(name): str(block.get("status")) for name, block in sources.items() if isinstance(block, dict)}


def _collect_record(payload: dict[str, Any], required: dict[str, bool], finished_at: datetime) -> dict[str, Any]:
    days = payload.get("days") if isinstance(payload.get("days"), list) else []
    per_source: dict[str, bool] = {name: bool(days) for name in required}
    for day_payload in days:
        statuses = _day_statuses(day_payload)
        for name in required:
            if statuses.get(name) not in _OK_STATUSES:
                per_source[name] = False
    return {
        "finished_at": _iso(finished_at),
        "ok": bool(payload.get("ok")) and bool(days),
        "failed_days": list(payload.get("failed_days") or []),
        "sources": {name: {"live": per_source[name], "required": bool(required[name])} for name in sorted(required)},
    }


def record_collect(state_file: Path, payload: dict[str, Any], *, required: dict[str, bool], finished_at: datetime) -> None:
    collects: list[dict[str, Any]] = []
    try:
        existing = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("collects"), list):
            collects = [c for c in existing["collects"] if isinstance(c, dict)]
    except (OSError, ValueError):
        collects = []  # an unreadable journal is replaced, not fatal for collect
    collects.append(_collect_record(payload, required, finished_at))
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_name(f".{state_file.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps({"schema_version": STATE_SCHEMA_VERSION, "collects": collects[-STATE_KEEP:]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, state_file)


def _read_collects(state_file: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return ``(collects, reason)``; ``reason`` is set when reading failed."""
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no_collect_recorded"
    except (OSError, ValueError):
        return None, "collect_state_unreadable"
    if not isinstance(raw, dict):
        return None, "collect_state_unreadable"
    records = raw.get("collects")
    if records is None:
        records = []
    if not isinstance(records, list):
        return None, "collect_state_unreadable"
    collects: list[dict[str, Any]] = []
    for entry in records:
        # A record that is not a dict or has no parseable finished_at corrupts
        # the whole journal: partial reads would hide a broken writer.
        if not isinstance(entry, dict) or not isinstance(entry.get("finished_at"), str):
            return None, "collect_state_unreadable"
        try:
            _parse_iso(entry["finished_at"])
        except ValueError:
            return None, "collect_state_unreadable"
        collects.append(entry)
    if not collects:
        return None, "no_collect_recorded"
    return collects, None


def _receipt_produced_at(exports_dir: Path, name: str) -> datetime | None:
    try:
        receipt = json.loads((exports_dir / name / "receipt.json").read_text(encoding="utf-8"))
        produced_at = receipt.get("produced_at")
        if not isinstance(produced_at, str):
            return None
        return _parse_iso(produced_at)
    except (OSError, ValueError, AttributeError):
        return None


def evaluate_health(*, state_file: Path, exports_dir: Path, now: datetime) -> tuple[int, dict]:
    try:
        return _evaluate_health(state_file=state_file, exports_dir=exports_dir, now=now)
    except Exception:
        # The /health endpoint must never answer 500; the exception text is
        # deliberately not included (it may carry paths or other internals).
        return 503, {
            "status": "unhealthy",
            "reasons": ["health_evaluation_error"],
            "last_collect": None,
            "last_successful_collect_at": None,
            "current_export_at": None,
            "latest_weekly_export_at": None,
            "sources": {},
        }


def _evaluate_health(*, state_file: Path, exports_dir: Path, now: datetime) -> tuple[int, dict]:
    now_utc = now.astimezone(timezone.utc)
    reasons: list[str] = []
    collects, read_reason = _read_collects(state_file)
    if read_reason is not None:
        reasons.append(read_reason)

    last_collect: dict[str, Any] | None = None
    last_successful_at: datetime | None = None
    sources: dict[str, Any] = {}
    if collects:
        last_collect = collects[-1]
        for entry in collects:
            if entry.get("ok") is True:
                finished = _parse_iso(entry["finished_at"])
                if last_successful_at is None or finished > last_successful_at:
                    last_successful_at = finished
        if last_successful_at is None or now_utc - last_successful_at > MAX_AGE:
            reasons.append("collect_stale")
        raw_sources = last_collect.get("sources")
        if isinstance(raw_sources, dict):
            for name in sorted(raw_sources):
                block = raw_sources[name]
                if isinstance(block, dict):
                    sources[str(name)] = {"live": bool(block.get("live")), "required": bool(block.get("required"))}
        if len(collects) >= 2:
            previous, latest = collects[-2], collects[-1]
            previous_sources = previous.get("sources") if isinstance(previous.get("sources"), dict) else {}
            latest_sources = latest.get("sources") if isinstance(latest.get("sources"), dict) else {}
            for name in sorted(set(previous_sources) | set(latest_sources)):
                before = previous_sources.get(name)
                after = latest_sources.get(name)
                required = (isinstance(before, dict) and before.get("required") is True) and (
                    isinstance(after, dict) and after.get("required") is True
                )
                live = (isinstance(before, dict) and before.get("live") is True) or (
                    isinstance(after, dict) and after.get("live") is True
                )
                if required and not live:
                    reasons.append(f"required_source_down:{name}")

    current_export_at = _receipt_produced_at(exports_dir, "current")
    if current_export_at is None or now_utc - current_export_at > MAX_AGE:
        reasons.append("current_export_stale")
    latest_weekly_export_at = _receipt_produced_at(exports_dir, "latest-weekly")

    body = {
        "status": "ok" if not reasons else "unhealthy",
        "reasons": reasons,
        "last_collect": (
            {"finished_at": last_collect["finished_at"], "ok": bool(last_collect.get("ok"))}
            if last_collect is not None
            else None
        ),
        "last_successful_collect_at": _iso(last_successful_at) if last_successful_at is not None else None,
        "current_export_at": _iso(current_export_at) if current_export_at is not None else None,
        "latest_weekly_export_at": _iso(latest_weekly_export_at) if latest_weekly_export_at is not None else None,
        "sources": sources,
    }
    http_status = 200 if not reasons else 503
    # Optional SERP freshness must never take the core runtime down.
    try:
        serp = json.loads(state_file.with_name("serp.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        serp = {"markets": []}
    except (OSError, ValueError):
        serp = None
    try:
        if not isinstance(serp, dict) or not isinstance(serp.get("markets"), list):
            raise ValueError("Invalid SERP markets structure")
        if any(
            not isinstance(m, dict) or not isinstance(m.get("enabled"), bool)
            for m in serp["markets"]
        ):
            raise ValueError("Invalid SERP market structure")
        enabled = [m for m in serp["markets"] if m["enabled"]]
        if enabled:
            stale = False
            for market in enabled:
                timestamps = []
                if market.get("latest"):
                    timestamps.append(_parse_iso(market["latest"] + "T00:00:00+03:00"))
                if market.get("enabled_at"):
                    timestamps.append(_parse_iso(market["enabled_at"]))
                if not timestamps:
                    raise ValueError("Enabled SERP market has no timestamp")
                if now_utc - max(timestamps) > timedelta(days=10):
                    stale = True
            body["sources"]["serp"] = {"live": not stale, "required": False}
            if stale:
                body["reasons"].append("serp_stale")
                if http_status == 200:
                    body["status"] = "degraded"
    except (AttributeError, TypeError, ValueError):
        body["reasons"].append("serp_state_unreadable")
        body["sources"]["serp"] = {"live": False, "required": False}
        if http_status == 200:
            body["status"] = "degraded"
    return http_status, body
