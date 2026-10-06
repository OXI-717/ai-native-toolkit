"""growth schema v3 panel builder: raw per-day aggregates over a calendar window.

Read-only over SEOStorage. Everything here is a raw window aggregate; derived
KPIs (non-brand totals, deltas, visit-to-signup) live in ``derive_growth``.
The output carries business numbers (registrations, revenue) but never person
identifiers — growth JSON is the private panel format.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from seo_observer.channels import (
    ChannelsConfig,
    _host_matches,
    app_path_match,
    brand_regex,
)
from seo_observer.storage import (
    GA4_ALL_CHANNELS_MODEL,
    RESERVED_ALL,
    SEOStorage,
    StorageError,
    resolve_raw_artifact_path,
)


GROWTH_SCHEMA_VERSION = 3
AGGREGATE_MARKERS = frozenset({RESERVED_ALL, "__aggregate__"})
ACTION_LOOKBACK_DAYS = 90
# ``data_through`` further than this many days before the window end is a
# real collection gap, not the normal search reporting lag.
_SEARCH_LAG_GAP_DAYS = 4
# Fact tables whose day-grain rows count towards days_with_facts. A request
# that succeeded with zero rows still counts in days_covered via the request
# record itself, so an outcome-free day is covered, not missing.
_FACT_DAY_SQL = {
    "search_performance": "effective_start",
    "traffic_metrics": "effective_start",
    "outcome_metrics": "effective_start",
}
# Fact-level dataset_coverage values that un-certify a day for that table.
# For search_performance a "truncated" detail fact is exempt only when it
# is a real query/page row (not an aggregate marker) from a successful,
# non-sampled request whose stored original coverage is top-N — the Search
# Console top-N list shape. Normalization maps top_rows/partial/sampled to
# the same stored value, so the original coverage is read back from the
# request's raw artifact; when it cannot be determined there is no
# exemption (fail closed). Independently of coverage, any current fact
# written by a partial request un-covers its day.
_DAY_GAP_COVERAGE = {
    "search_performance": frozenset(
        {"truncated", "privacy_thresholded", "unknown", "unavailable"}
    ),
    "traffic_metrics": frozenset(
        {"truncated", "privacy_thresholded", "unknown", "unavailable"}
    ),
    "outcome_metrics": frozenset(
        {"truncated", "privacy_thresholded", "unknown", "unavailable"}
    ),
}

def build_growth(
    storage: SEOStorage,
    *,
    project_id: str,
    start: date,
    end: date,
    channels: ChannelsConfig = ChannelsConfig(),
) -> dict[str, Any]:
    start_s, end_s = start.isoformat(), end.isoformat()
    days = (end - start).days + 1
    pattern = brand_regex(channels.brand_terms)
    return {
        "growth_schema_version": GROWTH_SCHEMA_VERSION,
        "project_id": project_id,
        "window": {"start": start_s, "end": end_s, "days": days},
        "search": _build_search(storage, project_id, start_s, end_s, pattern),
        "traffic": _build_traffic(storage, project_id, start_s, end_s, channels),
        "outcomes": _build_outcomes(storage, project_id, start_s, end_s),
        "actions": _build_actions(storage, project_id, start, end),
        "sources": _build_sources(storage, project_id, start, end),
    }


_EXPORT_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_EXPORT_LONG_NUMBER_RE = re.compile(r"\d{7,}")


def _export_text(value: Any) -> str:
    """Redact person-identifying fragments in an exported text value."""
    text = _EXPORT_EMAIL_RE.sub("[redacted]", str(value))
    return _EXPORT_LONG_NUMBER_RE.sub("[redacted]", text)


def _export_path(path: str) -> str:
    """Redact identifying path segments, keeping safe segments as encoded.

    Detection runs on the percent-decoded segment, but a safe segment keeps
    its original encoding so ``/a%2Fb`` and ``/a/b`` stay distinct pages.
    """
    private_segment = re.compile(
        r"(?:[0-9a-fA-F]{16,}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|"
        r"[0-9]{6,})"
    )
    def identifying(value: str, minimum: int = 20) -> bool:
        return bool(
            private_segment.fullmatch(value)
            or (
                re.fullmatch(r"[A-Za-z0-9_+=-]{%d,}" % minimum, value)
                and re.search(r"[A-Za-z]", value)
                and re.search(r"[0-9]", value)
                and not re.search(r"[A-Za-z]{2,}-[A-Za-z]{2,}", value)
            )
        )

    segments = []
    for segment in path.split("/"):
        decoded = unquote(segment)
        parts = decoded.split(".")
        dotted_token = len(parts) > 1 and all(
            re.fullmatch(r"[A-Za-z0-9_+=-]+", part) for part in parts
        ) and (
            any(identifying(part) for part in parts)
            or all(identifying(part, minimum=8) for part in parts)
            or re.fullmatch(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", decoded)
        )
        if identifying(decoded) or dotted_token:
            segments.append(":id")
        elif _export_text(decoded) != decoded:
            segments.append("[redacted]")
        else:
            segments.append(segment)
    return "/".join(segments)


def _export_url(value: Any) -> str:
    """Reduce a stored page/landing URL to scheme + host + path, redacted.

    Query strings and fragments are dropped (they commonly carry identifiers);
    email-shaped or phone-shaped path segments are replaced by ``[redacted]``.
    """
    text = str(value)
    try:
        parts = urlsplit(text)
    except ValueError:
        # Never fall back to the raw value: drop the query/fragment tail so
        # identifiers it may carry cannot leak, then redact what remains.
        head = re.split(r"[?#]", text, maxsplit=1)[0]
        return _export_path(head)
    path = _export_path(parts.path or "/")
    if parts.scheme or parts.netloc:
        return urlunsplit((parts.scheme, _export_text(parts.netloc), path, "", ""))
    return path


def _daily_fact_clause() -> str:
    return (
        "is_current = 1 AND effective_start = effective_end "
        "AND effective_start BETWEEN ? AND ?"
    )


def _build_search(
    storage: SEOStorage,
    project_id: str,
    start_s: str,
    end_s: str,
    pattern: str | None,
) -> dict[str, Any]:
    totals = storage.fetchall(
        f"""
        SELECT source, effective_start AS day, segment_id, impressions, clicks
        FROM search_performance
        WHERE project_id = ? AND {_daily_fact_clause()}
          AND segment_id IN ('total', 'brand')
        """,
        (project_id, start_s, end_s),
    )
    details = storage.fetchall(
        f"""
        SELECT source, effective_start AS day, query_id, query_text, page_id, page_url,
               impressions, clicks, average_position
        FROM search_performance
        WHERE project_id = ? AND {_daily_fact_clause()}
          AND segment_id NOT IN ('total', 'brand')
          AND reporting_period_id = '1d'
        """,
        (project_id, start_s, end_s),
    )

    daily_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for row in totals:
        key = (str(row["source"]), str(row["day"]))
        entry = daily_rows.setdefault(
            key, {"date": key[1], "total": None, "brand": None}
        )
        entry[str(row["segment_id"])] = {
            "impressions": int(row["impressions"]),
            "clicks": int(row["clicks"]),
        }

    query_aggs: dict[tuple[str, str], dict[str, Any]] = {}
    page_aggs: dict[tuple[str, str], dict[str, Any]] = {}
    query_page_aggs: dict[tuple[str, str, str], dict[str, Any]] = {}
    detail_days: dict[str, set[str]] = {}
    for row in details:
        source = str(row["source"])
        if str(row["query_id"]) not in AGGREGATE_MARKERS:
            _accumulate(query_aggs, (source, _export_text(row["query_text"])), row)
        if str(row["page_id"]) not in AGGREGATE_MARKERS:
            _accumulate(page_aggs, (source, _export_url(row["page_url"])), row)
        if (str(row["query_id"]) not in AGGREGATE_MARKERS
                or str(row["page_id"]) not in AGGREGATE_MARKERS):
            detail_days.setdefault(source, set()).add(str(row["day"]))
        if (str(row["query_id"]) not in AGGREGATE_MARKERS
                and str(row["page_id"]) not in AGGREGATE_MARKERS):
            _accumulate(query_page_aggs,
                        (source, _export_text(row["query_text"]), _export_url(row["page_url"])), row)

    enabled_search = {
        str(row["source"])
        for row in storage.fetchall(
            "SELECT source FROM sources WHERE project_id = ? AND enabled = 1",
            (project_id,),
        )
    } & _SEARCH_FACT_SOURCES
    search: dict[str, Any] = {}
    for source in sorted(
        enabled_search
        | {str(r["source"]) for r in totals}
        | {str(r["source"]) for r in details}
    ):
        daily = []
        for (src, _day), entry in sorted(daily_rows.items()):
            if src != source:
                continue
            item = {"date": entry["date"], "total": entry["total"]}
            if entry["brand"] is not None:
                item["brand"] = entry["brand"]
            daily.append(item)
        queries = [
            {
                "query": key[1],
                "is_brand": bool(pattern and re.search(pattern, key[1])),
                "impressions": agg["impressions"],
                "clicks": agg["clicks"],
                "position": _weighted_position(agg),
            }
            for key, agg in sorted(
                query_aggs.items(), key=lambda kv: (-kv[1]["impressions"], kv[0][1])
            )
            if key[0] == source
        ]
        pages = [
            {
                "page": key[1],
                "impressions": agg["impressions"],
                "clicks": agg["clicks"],
            }
            for key, agg in sorted(
                page_aggs.items(), key=lambda kv: (-kv[1]["impressions"], kv[0][1])
            )
            if key[0] == source
        ]
        # The last in-window day with a non-zero ``total`` fact. Search APIs
        # publish days late; trailing days with zero-row totals are
        # provisionally reported, so KPIs compare only up to this date.
        data_through = max(
            (
                item["date"]
                for item in daily
                if item["total"]
                and (
                    int(item["total"].get("impressions") or 0)
                    or int(item["total"].get("clicks") or 0)
                )
            ),
            default=None,
        )
        search[source] = {
            "daily": daily,
            "queries": queries,
            "pages": pages,
            "query_pages": [
                {"query": key[1], "page": key[2],
                 "impressions": agg["impressions"], "clicks": agg["clicks"]}
                for key, agg in sorted(query_page_aggs.items()) if key[0] == source
            ],
            "details_through": max(detail_days.get(source, set()), default=None),
            "data_through": data_through,
            "totals_available": any(item["total"] for item in daily),
            "search_gap": bool(
                data_through
                and (
                    date.fromisoformat(end_s)
                    - date.fromisoformat(str(data_through))
                ).days
                > _SEARCH_LAG_GAP_DAYS
            ),
        }
    return search


def _accumulate(
    aggs: dict[tuple[str, str], dict[str, Any]],
    key: tuple[str, ...],
    row: dict[str, Any],
) -> None:
    agg = aggs.setdefault(key, {"impressions": 0, "clicks": 0, "pos_num": 0.0, "pos_den": 0})
    agg["impressions"] += int(row["impressions"])
    agg["clicks"] += int(row["clicks"])
    if row["average_position"] is not None and int(row["impressions"]) > 0:
        agg["pos_num"] += float(row["average_position"]) * int(row["impressions"])
        agg["pos_den"] += int(row["impressions"])


def _weighted_position(agg: dict[str, Any]) -> float | None:
    if not agg["pos_den"]:
        return None
    return round(agg["pos_num"] / agg["pos_den"], 4)


def _noise_source(search_engine: str) -> str:
    return search_engine.split(" / ", 1)[0].strip().lower()


def _landing_path(landing_page_id: str) -> str:
    """Extract the URL path from a stored ``page:`` landing identifier."""
    page = landing_page_id
    if page.startswith("page:"):
        page = page[len("page:"):]
    try:
        return urlsplit(page).path or "/"
    except ValueError:
        return re.split(r"[?#]", page, maxsplit=1)[0] or "/"


def _build_traffic(
    storage: SEOStorage,
    project_id: str,
    start_s: str,
    end_s: str,
    channels: ChannelsConfig,
) -> dict[str, Any]:
    rows = storage.fetchall(
        f"""
        SELECT effective_start AS day, channel, search_engine, landing_page_id,
               visits, users
        FROM traffic_metrics
        WHERE project_id = ? AND {_daily_fact_clause()}
          AND attribution_model = ?
        """,
        (project_id, start_s, end_s, GA4_ALL_CHANNELS_MODEL),
    )
    # The GA4 channel adapter never emits a landing-page total: its
    # ``__all__`` landing id marks sessions whose landing page is unset
    # (empty ``landingPagePlusQueryString``). Those rows are one more part
    # of the partition, so per (day, channel, source/medium) the visits are
    # the sum of ALL rows. Treating ``__all__`` as a total dropped the page
    # rows (2026-10-02: direct counted 1 visit instead of 763 on one day).
    grouped: dict[tuple[str, str, str], dict[str, list]] = {}
    landing_rows: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row["day"]), str(row["channel"]), str(row["search_engine"]))
        group = grouped.setdefault(key, {"all": [], "pages": []})
        if str(row["landing_page_id"]) == RESERVED_ALL:
            group["all"].append(row)
        else:
            group["pages"].append(row)
            landing_rows.append(row)
    daily: dict[tuple[str, str], dict[str, int]] = {}
    sources: dict[tuple[str, str], int] = {}
    landing: dict[str, dict[str, int]] = {}
    noise_visits = 0
    noise_sources: set[str] = set()
    for (day, channel, source_medium), group in grouped.items():
        is_noise = _host_matches(
            _noise_source(source_medium), channels.noise_referrers
        )
        if is_noise:
            noise_sources.add(source_medium)
        # Distinct users are not additive across landing pages: a user count
        # is trustworthy only when the group has a single row.
        group_rows = group["all"] + group["pages"]
        users_known = len(group_rows) == 1
        for row in group_rows:
            visits = int(row["visits"] or 0)
            if is_noise:
                noise_visits += visits
                continue
            entry = daily.setdefault(
                (day, channel),
                {"visits": 0, "users": 0, "users_known": True},
            )
            entry["visits"] += visits
            if users_known:
                entry["users"] += int(row["users"] or 0)
            else:
                entry["users_known"] = False
            sources[(channel, source_medium)] = (
                sources.get((channel, source_medium), 0) + visits
            )
        if is_noise:
            continue
        # Direct visits landing on product pages are app usage, not
        # acquisition: move their visits to the ``app`` channel. App visits
        # come from page rows of this group, which are part of the group sum,
        # so the residual direct count can never go negative.
        app_visits = 0
        if channel == "direct" and channels.app_paths:
            app_visits = sum(
                int(row["visits"] or 0)
                for row in group["pages"]
                if app_path_match(
                    _landing_path(str(row["landing_page_id"])),
                    channels.app_paths,
                )
            )
        if app_visits:
            entry = daily.get((day, channel))
            if entry is not None:
                entry["visits"] -= app_visits
                # Residual direct users cannot be inferred by subtracting
                # page-level distinct counts — report them as unknown.
                entry["users_known"] = False
            app_entry = daily.setdefault(
                (day, "app"),
                {"visits": 0, "users": 0, "users_known": False},
            )
            app_entry["visits"] += app_visits
            sources[(channel, source_medium)] = (
                sources.get((channel, source_medium), 0) - app_visits
            )
            sources[("app", source_medium)] = (
                sources.get(("app", source_medium), 0) + app_visits
            )
    for row in landing_rows:
        if _host_matches(
            _noise_source(str(row["search_engine"])), channels.noise_referrers
        ):
            continue
        page = str(row["landing_page_id"])
        channel = str(row["channel"])
        if (
            channel == "direct"
            and channels.app_paths
            and app_path_match(_landing_path(page), channels.app_paths)
        ):
            channel = "app"
        if page.startswith("page:"):
            page = page[len("page:"):]
        page = _export_url(page)
        by_channel = landing.setdefault(page, {})
        by_channel[channel] = by_channel.get(channel, 0) + int(row["visits"] or 0)
    return {
        "daily": [
            {
                "date": day,
                "channel": channel,
                "visits": entry["visits"],
                "users": entry["users"] if entry["users_known"] else None,
            }
            for (day, channel), entry in sorted(daily.items())
        ],
        "sources": [
            {"channel": channel, "source_medium": source_medium, "visits": visits}
            for (channel, source_medium), visits in sorted(
                sources.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ],
        "landing_pages": [
            {"page": page, "visits": sum(counts.values()),
             "channels": dict(sorted(counts.items()))}
            for page, counts in sorted(
                landing.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])
            )
        ],
        "excluded_noise": {"visits": noise_visits, "sources": sorted(noise_sources)},
    }


def _build_outcomes(
    storage: SEOStorage, project_id: str, start_s: str, end_s: str
) -> dict[str, Any]:
    rows = storage.fetchall(
        f"""
        SELECT effective_start AS day, outcome_id, evidence_kind, traffic_channel,
               count, unique_actors, value_minor, currency
        FROM outcome_metrics
        WHERE project_id = ? AND {_daily_fact_clause()}
        """,
        (project_id, start_s, end_s),
    )
    server: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    events: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        day = str(row["day"])
        outcome_id = str(row["outcome_id"])
        channel = str(row["traffic_channel"])
        if row["evidence_kind"] == "server_fact":
            key = (day, outcome_id, channel, row["currency"])
            entry = server.setdefault(
                key,
                {"date": day, "outcome_id": outcome_id, "channel": channel,
                 "count": 0, "value_minor": None, "currency": row["currency"]},
            )
            entry["count"] += int(row["count"] or 0)
            if row["value_minor"] is not None:
                entry["value_minor"] = (entry["value_minor"] or 0) + int(row["value_minor"])
        elif row["evidence_kind"] == "analytics_event":
            key = (day, outcome_id, channel)
            entry = events.setdefault(
                key,
                {"date": day, "outcome_id": outcome_id, "channel": channel,
                 "count": 0, "unique_actors": 0},
            )
            entry["count"] += int(row["count"] or 0)
            entry["unique_actors"] += int(row["unique_actors"] or 0)
    return {
        "server": [server[key] for key in sorted(server)],
        "analytics_event": [events[key] for key in sorted(events)],
    }


def _build_actions(
    storage: SEOStorage, project_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    lookback = (start - timedelta(days=ACTION_LOOKBACK_DAYS)).isoformat()
    leaf_actions = storage.fetchall(
        """
        SELECT action_id, changed_at, action_type, description
        FROM seo_actions AS action
        WHERE project_id = ?
          AND substr(changed_at, 1, 10) BETWEEN ? AND ?
          AND lifecycle_state != 'tombstoned'
          AND NOT EXISTS (
            SELECT 1 FROM seo_actions AS child
            WHERE child.supersedes_action_revision_hash = action.action_revision_hash
          )
        ORDER BY action_id
        """,
        (project_id, lookback, end.isoformat()),
    )
    result = []
    for action in leaf_actions:
        verdict_row = storage.fetchall(
            """
            SELECT verdict, relative_delta
            FROM action_verdicts AS verdict
            WHERE action_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM action_verdicts AS child
                WHERE child.supersedes_verdict_id = verdict.verdict_id
              )
            ORDER BY evaluation_as_of DESC, verdict_id ASC
            LIMIT 1
            """,
            (action["action_id"],),
        )
        verdict = verdict_row[0] if verdict_row else {}
        result.append(
            {
                "action_id": action["action_id"],
                "changed_at": action["changed_at"],
                "action_type": action["action_type"],
                "description": action["description"],
                "verdict": verdict.get("verdict"),
                "relative_delta": verdict.get("relative_delta"),
            }
        )
    return result


def _request_window(descriptor_json: str) -> tuple[str, str] | None:
    try:
        descriptor = json.loads(descriptor_json or "{}")
    except json.JSONDecodeError:
        return None
    start, end = descriptor.get("period_start"), descriptor.get("period_end")
    if start and end:
        return str(start)[:10], str(end)[:10]
    return None


def _build_sources(
    storage: SEOStorage, project_id: str, start: date, end: date
) -> dict[str, Any]:
    start_s, end_s = start.isoformat(), end.isoformat()
    window_days = (end - start).days + 1
    enabled = storage.fetchall(
        "SELECT source, required FROM sources WHERE project_id = ? AND enabled = 1",
        (project_id,),
    )
    requests = storage.fetchall(
        """
        SELECT request.source, request.request_id, request.queried_at,
               request.completed_at, request.transport_status, request.freshness,
               request.sampled, request.request_descriptor_json
        FROM source_requests AS request
        JOIN collection_runs AS run ON run.run_id = request.run_id
        WHERE run.project_id = ?
        ORDER BY request.queried_at, request.request_id
        """,
        (project_id,),
    )
    request_status = {
        str(request["request_id"]): {
            "transport_status": request["transport_status"],
            "sampled": bool(request["sampled"]),
        }
        for request in requests
    }
    artifact_paths: dict[str, list[str]] = {}
    for artifact in storage.fetchall(
        """
        SELECT artifact.request_id, artifact.relative_path
        FROM raw_artifacts AS artifact
        JOIN source_requests AS request
          ON request.request_id = artifact.request_id
        JOIN collection_runs AS run ON run.run_id = request.run_id
        WHERE run.project_id = ?
        """,
        (project_id,),
    ):
        artifact_paths.setdefault(str(artifact["request_id"]), []).append(
            str(artifact["relative_path"])
        )
    original_coverage_cache: dict[str, set[str]] = {}

    def _original_coverages(request_id: str) -> set[str]:
        """Pre-normalization coverage stored in the request's raw
        artifact(s) — the only place ``top_rows``/``partial``/``sampled``
        survive, since fact rows keep only the normalized value."""
        if request_id not in original_coverage_cache:
            coverages: set[str] = set()
            for relative_path in artifact_paths.get(request_id, []):
                try:
                    path = resolve_raw_artifact_path(
                        observer_home=storage.observer_home,
                        project_id=project_id,
                        relative_path=relative_path,
                    )
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, StorageError, ValueError):
                    continue
                # Valid JSON of an unexpected shape is unknown provenance, not
                # a crash of the whole export (fail closed: no exemption).
                if not isinstance(payload, dict):
                    continue
                metadata = payload.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                coverage_value = metadata.get("dataset_coverage")
                if coverage_value is not None:
                    coverages.add(str(coverage_value))
            original_coverage_cache[request_id] = coverages
        return original_coverage_cache[request_id]

    fact_days: dict[str, dict[str, Any]] = {}
    source_incomplete_days: dict[str, set[str]] = {}
    request_all_days: dict[str, set[str]] = {}
    request_current_days: dict[str, set[str]] = {}
    request_incomplete_days: dict[str, set[str]] = {}
    for table, day_column in _FACT_DAY_SQL.items():
        detail_columns = (
            "segment_id, query_id, page_id"
            if table == "search_performance"
            else "NULL AS segment_id, NULL AS query_id, NULL AS page_id"
        )
        for row in storage.fetchall(
            f"""
            SELECT source, {day_column} AS day, request_id, source_timezone,
                   is_current, dataset_coverage, {detail_columns}
            FROM {table}
            WHERE project_id = ?
              AND effective_start = effective_end
              AND {day_column} BETWEEN ? AND ?
            """,
            (project_id, start_s, end_s),
        ):
            request_id = str(row["request_id"])
            day = str(row["day"])
            request_all_days.setdefault(request_id, set()).add(day)
            if not row["is_current"]:
                continue
            request_current_days.setdefault(request_id, set()).add(day)
            status = request_status.get(request_id) or {}
            coverage = str(row["dataset_coverage"])
            gap = coverage in _DAY_GAP_COVERAGE[table]
            if (
                gap
                and coverage == "truncated"
                and table == "search_performance"
            ):
                gap = not (
                    (
                        str(row["query_id"] or "") not in AGGREGATE_MARKERS
                        or str(row["page_id"] or "") not in AGGREGATE_MARKERS
                    )
                    and status.get("transport_status") == "success"
                    and not status.get("sampled")
                    and _original_coverages(request_id) == {"top_rows"}
                )
            if status.get("transport_status") == "partial":
                gap = True
            if gap:
                request_incomplete_days.setdefault(request_id, set()).add(day)
                source_incomplete_days.setdefault(
                    str(row["source"]), set()
                ).add(day)
            fact_days.setdefault(str(row["source"]), {}).setdefault(
                day, []
            ).append(row)

    # Latest factual day in the stored history, independent of selected window.
    latest_fact_days: dict[str, str] = {}
    for table in _FACT_DAY_SQL:
        for row in storage.fetchall(
            f"SELECT source, MAX(effective_end) AS day FROM {table} "
            "WHERE project_id = ? AND is_current = 1 AND effective_end <= ? GROUP BY source",
            (project_id, end_s),
        ):
            if row["day"]:
                name, day = str(row["source"]), str(row["day"])
                latest_fact_days[name] = max(latest_fact_days.get(name, day), day)
    result: dict[str, Any] = {}
    for source_row in enabled:
        source = str(source_row["source"])
        covered: set[str] = set()
        last_request: dict[str, Any] | None = None
        collected_at: str | None = None
        for request in requests:
            if str(request["source"]) != source:
                continue
            window = _request_window(str(request["request_descriptor_json"]))
            descriptor_days = (
                set(_days_between(*window, bounds=(start_s, end_s)))
                if window is not None
                else set()
            )
            request_id = str(request["request_id"])
            all_days = request_all_days.get(request_id, set())
            current_days = request_current_days.get(request_id, set())
            if not descriptor_days and not all_days:
                continue
            last_request = request
            collected_at = str(request["completed_at"] or request["queried_at"])
            if (
                request["transport_status"] == "success"
                and request["freshness"] != "stale"
            ):
                # Only the revisions actually used count: a day contributed by
                # this request is covered when it reported the day empty, or
                # when the facts it produced are still current and complete.
                # Days whose facts were superseded belong to the newer
                # request; days with incomplete current facts are not covered.
                incomplete_days = request_incomplete_days.get(request_id, set())
                for day in descriptor_days:
                    if day not in all_days:
                        covered.add(day)
                    elif day in current_days and day not in incomplete_days:
                        covered.add(day)
                covered.update(current_days - incomplete_days)
        # A day is fully covered only when every current fact on it comes
        # from a complete dataset: coverage from an older request cannot
        # certify incomplete facts written by another request.
        covered -= source_incomplete_days.get(source, set())
        days_with_facts = len(fact_days.get(source, {}))
        timezone = None
        days_map = fact_days.get(source, {})
        if days_map:
            latest_day = max(days_map)
            timezone = days_map[latest_day][-1]["source_timezone"]
        state = "live"
        if last_request is None or last_request["transport_status"] != "success":
            state = "partial"
        if len(covered) < window_days:
            state = "partial"
        if (
            state == "live"
            and last_request is not None
            and last_request["freshness"] == "stale"
        ):
            state = "stale"
        result[source] = {
            "state": state,
            "data_through": latest_fact_days.get(source),
            # Transport of the latest request: lets consumers tell a failed
            # request (outage) from a successful provisional lag.
            "last_transport_status": (
                str(last_request["transport_status"]) if last_request is not None else None
            ),
            "required": bool(source_row["required"]),
            "collected_at": collected_at,
            "timezone": timezone,
            "days_covered": len(covered),
            "covered_days": sorted(covered),
            "days_with_facts": days_with_facts,
        }
    return result


def _days_between(
    start_s: str, end_s: str, *, bounds: tuple[str, str]
) -> list[str]:
    first = max(start_s, bounds[0])
    last = min(end_s, bounds[1])
    if first > last:
        return []
    first_day, last_day = date.fromisoformat(first), date.fromisoformat(last)
    return [
        (first_day + timedelta(days=i)).isoformat()
        for i in range((last_day - first_day).days + 1)
    ]


# Derived metrics (Task 5). Pure functions over build_growth output — no SQL.

_SEARCH_FACT_SOURCES = frozenset({"google_search_console", "yandex_webmaster"})
_TRAFFIC_FACT_SOURCES = frozenset({"ga4", "yandex_metrica"})
_SERVER_OUTCOME_PREFIX = "outcome_"
_SIGNUP_EVENTS_SOURCE = "mixpanel"
_KPI_NAMES = (
    "nonbrand_impressions",
    "nonbrand_clicks",
    "organic_visits",
    "registrations",
    "payments",
    "revenue_minor",
    "visit_to_signup",
)
_TREND_METRICS = (
    "nonbrand_impressions",
    "nonbrand_clicks",
    "organic_visits",
    "registrations",
    "payments",
    "revenue_minor",
)
_AVG_WEEKS_MAX = 4
_WHAT_CHANGED_MAX = 3
_QUERY_MIN_IMPRESSIONS = 10
_CHANNEL_MIN_VISITS = 3
# Count KPIs whose comparisons switch to an absolute change on a tiny
# base; visit_to_signup is a ratio and revenue_minor is money.
_COUNT_KPIS = frozenset(
    {
        "nonbrand_impressions",
        "nonbrand_clicks",
        "organic_visits",
        "registrations",
        "payments",
    }
)
_TINY_BASE = 5


def _abs_change_base(name: str, base: float) -> bool:
    """A zero base makes every percentage meaningless; a below-tiny base
    does so for count KPIs. In both cases the comparison carries an
    absolute change instead of a percentage."""
    return base == 0 or (name in _COUNT_KPIS and base < _TINY_BASE)


def derive_growth(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    history: list[dict[str, Any]],
    *,
    extra_windows: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Return the ``derived`` block of growth_v1 for the current window."""
    derived: dict[str, Any] = {
        "kpis": _compute_kpis(current, previous, history),
        "what_changed": _what_changed(current, previous),
        "trend_12w": _trend_12w(history),
        "signup_channels": _signup_channels(current),
    }
    if extra_windows:
        derived["kpis_by_window"] = {
            name: _compute_kpis(window, window_previous, history)
            for name, (window, window_previous) in sorted(extra_windows.items())
        }
    return derived


def _kpi_source_sets(growth: dict[str, Any]) -> dict[str, set[str]]:
    enabled = set(growth.get("sources") or {})
    search = enabled & _SEARCH_FACT_SOURCES
    traffic = enabled & _TRAFFIC_FACT_SOURCES
    server = {s for s in enabled if s.startswith(_SERVER_OUTCOME_PREFIX)}
    return {
        "nonbrand_impressions": search,
        "nonbrand_clicks": search,
        "organic_visits": traffic,
        "registrations": server,
        "payments": server,
        "revenue_minor": server,
        "visit_to_signup": traffic | server,
    }


def _search_day_limits(growth: dict[str, Any]) -> dict[str, int]:
    """Per search source, how many leading window days carry data.

    ``data_through`` is the last day with a non-zero ``total`` fact; the
    trailing provisional days (zero rows, still covered by requests) are
    excluded from KPI sums and from the coverage requirement. A comparison
    window is clipped to the same number of leading days.
    """
    start = (growth.get("window") or {}).get("start")
    limits: dict[str, int] = {}
    for source, block in (growth.get("search") or {}).items():
        through = block.get("data_through")
        if not start or not through:
            continue
        delta = (
            date.fromisoformat(str(through))
            - date.fromisoformat(str(start))
        ).days
        limits[str(source)] = max(delta + 1, 0)
    return limits


def _search_allowed_dates(
    growth: dict[str, Any], limits: dict[str, int]
) -> dict[str, set[str]]:
    """Clipped interval per source as calendar dates generated from the
    window start — never a slice of the sparse daily array: a covered
    zero-row day has no row and must not shift the retained dates."""
    return {
        source: _allowed_dates(growth, source, limits)
        for source in limits
    }


def _search_totals_available(growth: dict[str, Any], source: str) -> bool:
    """True when the source produced at least one ``total`` fact in this
    window. Explicit zero totals count; a covered window with no total
    facts at all is absent data, not a measured zero."""
    block = (growth.get("search") or {}).get(source)
    if not block:
        return False
    if "totals_available" in block:
        return bool(block["totals_available"])
    return any(day.get("total") for day in block.get("daily") or [])


def _kpi_participants(
    growth: dict[str, Any], names: set[str]
) -> set[str]:
    """Sources that may drive a KPI in this window: a search source
    participates only when it supplied total facts here — a source that
    never reports totals must not drag another source's KPI to partial."""
    return {
        name
        for name in names
        if name not in _SEARCH_FACT_SOURCES
        or _search_totals_available(growth, name)
    }


def _search_lag_gap(
    growth: dict[str, Any], sources: set[str]
) -> bool:
    """True when a search source's ``data_through`` sits more than
    ``_SEARCH_LAG_GAP_DAYS`` before the window end — a real gap, not the
    normal reporting lag."""
    end = (growth.get("window") or {}).get("end")
    end_day = date.fromisoformat(str(end)) if end else None
    for source, block in (growth.get("search") or {}).items():
        if str(source) not in sources:
            continue
        gap = block.get("search_gap")
        if gap is None and end_day is not None:
            through = block.get("data_through")
            gap = bool(
                through
                and (end_day - date.fromisoformat(str(through))).days
                > _SEARCH_LAG_GAP_DAYS
            )
        if gap:
            return True
    return False


def _raw_metrics(
    growth: dict[str, Any],
    *,
    search_days: dict[str, set[str]] | None = None,
    search_sources: set[str] | None = None,
) -> dict[str, Any]:
    impressions = clicks = 0
    for source, block in (growth.get("search") or {}).items():
        if search_sources is not None and str(source) not in search_sources:
            continue
        allowed = (search_days or {}).get(str(source))
        for day in block.get("daily") or []:
            if allowed is not None and str(day.get("date")) not in allowed:
                continue
            total = day.get("total") or {}
            brand = day.get("brand") or {}
            impressions += int(total.get("impressions") or 0)
            impressions -= int(brand.get("impressions") or 0)
            clicks += int(total.get("clicks") or 0)
            clicks -= int(brand.get("clicks") or 0)
    organic = visits = 0
    for row in (growth.get("traffic") or {}).get("daily") or []:
        count = int(row.get("visits") or 0)
        channel = str(row.get("channel"))
        if channel == "app":
            # Product usage is excluded from the visit_to_signup
            # denominator; it is not acquisition traffic.
            continue
        visits += count
        if channel == "organic_search":
            organic += count
    registrations = payments = 0
    revenue_minor = 0
    currencies: list[str] = []
    for row in (growth.get("outcomes") or {}).get("server") or []:
        if row.get("outcome_id") == "registration":
            registrations += int(row.get("count") or 0)
        elif row.get("outcome_id") == "paid_purchase":
            payments += int(row.get("count") or 0)
            revenue_minor += int(row.get("value_minor") or 0)
            currency = row.get("currency")
            if currency and currency not in currencies:
                currencies.append(str(currency))
    return {
        "nonbrand_impressions": impressions,
        "nonbrand_clicks": clicks,
        "organic_visits": organic,
        "visits": visits,
        "registrations": registrations,
        "payments": payments,
        "revenue_minor": revenue_minor,
        "currencies": currencies,
    }


def _kpi_value(name: str, metrics: dict[str, Any]) -> int | float | None:
    if name == "revenue_minor":
        if len(metrics["currencies"]) > 1:
            return None
        return metrics["revenue_minor"]
    if name == "visit_to_signup":
        if not metrics["visits"]:
            return None
        return metrics["registrations"] / metrics["visits"]
    return metrics[name]


def _window_days(growth: dict[str, Any]) -> int:
    return int((growth.get("window") or {}).get("days") or 0)


def _days_covered(growth: dict[str, Any], source: str) -> int:
    entry = (growth.get("sources") or {}).get(source)
    if not entry:
        return 0
    return int(entry.get("days_covered") or 0)


def _window_dates(growth: dict[str, Any]) -> list[str]:
    """Every calendar date of the window, from ``window.start``."""
    start = (growth.get("window") or {}).get("start")
    if not start:
        return []
    first = date.fromisoformat(str(start))
    return [
        (first + timedelta(days=i)).isoformat()
        for i in range(_window_days(growth))
    ]


def _allowed_dates(
    growth: dict[str, Any], source: str, limits: dict[str, int]
) -> set[str]:
    """Dates a source must cover: the leading ``limits[source]`` window days
    (the retained interval after search clipping), or the whole window."""
    return set(_window_dates(growth)[: limits.get(source, _window_days(growth))])


def _covered_dates(growth: dict[str, Any], source: str) -> set[str] | None:
    entry = (growth.get("sources") or {}).get(source)
    if not entry:
        return None
    days = entry.get("covered_days")
    if days is None:
        return None
    return {str(day) for day in days}


def _source_days_covered(
    growth: dict[str, Any], source: str, limits: dict[str, int]
) -> int:
    """Covered days inside the source's retained interval."""
    allowed = _allowed_dates(growth, source, limits)
    covered = _covered_dates(growth, source)
    if covered is None:
        return min(_days_covered(growth, source), len(allowed))
    return len(allowed & covered)


def _fully_covered(
    growth: dict[str, Any],
    sources: set[str],
    limits: dict[str, int] | None = None,
) -> bool:
    """Every retained calendar day covered: for a search source with
    ``data_through`` only the leading ``limits[source]`` days must be
    covered; other sources need the full window. The check is by date
    membership, never by bare counts — a covered day outside the retained
    interval cannot compensate for an uncovered day inside it."""
    limits = limits or {}
    if not sources:
        return False
    for source in sources:
        if source in _SEARCH_FACT_SOURCES and not _search_totals_available(
            growth, source
        ):
            return False
        allowed = _allowed_dates(growth, source, limits)
        covered = _covered_dates(growth, source)
        if covered is None:
            if _days_covered(growth, source) < len(allowed):
                return False
        elif not allowed <= covered:
            return False
    return True


def _compute_kpis(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    sources = _kpi_source_sets(current)
    limits = _search_day_limits(current)
    search_sources = _kpi_participants(
        current, sources["nonbrand_impressions"]
    )
    metrics = _raw_metrics(
        current,
        search_days=_search_allowed_dates(current, limits),
        search_sources=search_sources,
    )
    previous_metrics = (
        _raw_metrics(
            previous,
            search_days=_search_allowed_dates(previous, limits),
            search_sources=search_sources,
        )
        if previous is not None
        else None
    )
    kpis: dict[str, Any] = {}
    for name in _KPI_NAMES:
        kpi_sources = _kpi_participants(current, sources[name])
        covered = (
            min(
                _source_days_covered(current, s, limits)
                for s in kpi_sources
            )
            if kpi_sources
            else 0
        )
        has_facts = any(
            int(((current.get("sources") or {}).get(s) or {}).get("days_with_facts") or 0) > 0
            for s in kpi_sources
        )
        if not kpi_sources or (covered == 0 and not has_facts):
            coverage = "none"
        elif _fully_covered(current, kpi_sources, limits) and not (
            _search_lag_gap(current, kpi_sources)
        ):
            coverage = "complete"
        else:
            coverage = "partial"
        value = _kpi_value(name, metrics) if coverage != "none" else None

        prev_value = (
            _kpi_value(name, previous_metrics) if previous_metrics else None
        )
        comparable = (
            coverage == "complete"
            and previous is not None
            # Same source set as now, or a single-engine week would be
            # compared with a multi-engine total.
            and _kpi_participants(previous, kpi_sources) == kpi_sources
            and _fully_covered(previous, kpi_sources, limits)
            and not _search_lag_gap(
                previous, _kpi_participants(previous, kpi_sources)
            )
        )
        if name == "revenue_minor" and previous_metrics is not None:
            comparable = comparable and metrics["currencies"] == previous_metrics["currencies"]
        delta_pct = None
        delta_abs = None
        if comparable and value is not None and prev_value is not None:
            if _abs_change_base(name, float(prev_value)):
                delta_abs = value - prev_value
            elif prev_value:
                delta_pct = (value - prev_value) / prev_value * 100

        week_values = []
        for week in sorted(
            history,
            key=lambda item: str((item.get("window") or {}).get("start") or ""),
            reverse=True,
        ):
            if len(week_values) >= _AVG_WEEKS_MAX:
                break
            week_sources = _kpi_participants(week, kpi_sources)
            if (
                not week_sources
                or week_sources != kpi_sources
                or not _fully_covered(week, week_sources, limits)
                or _search_lag_gap(week, week_sources)
            ):
                continue
            week_metrics = _raw_metrics(
                week,
                search_days=_search_allowed_dates(week, limits),
                search_sources=week_sources & _SEARCH_FACT_SOURCES,
            )
            if name == "revenue_minor" and week_metrics["currencies"] != metrics["currencies"]:
                continue
            week_value = _kpi_value(name, week_metrics)
            if week_value is not None:
                week_values.append(week_value)
        avg4 = (
            sum(week_values) / len(week_values) if week_values else None
        )
        delta_vs_avg4 = None
        delta_vs_avg4_abs = None
        if coverage == "complete" and value is not None and avg4 is not None:
            if _abs_change_base(name, float(avg4)):
                delta_vs_avg4_abs = value - avg4
            elif avg4:
                delta_vs_avg4 = (value - avg4) / avg4 * 100

        kpi: dict[str, Any] = {
            "value": value,
            "previous": prev_value,
            "delta_pct": delta_pct,
            "delta_abs": delta_abs,
            "avg4": avg4,
            "avg_weeks": len(week_values),
            "delta_vs_avg4_pct": delta_vs_avg4,
            "delta_vs_avg4_abs": delta_vs_avg4_abs,
            "coverage": coverage,
            "days_covered": covered,
        }
        if name == "revenue_minor":
            if previous_metrics is not None and len(previous_metrics["currencies"]) == 1:
                kpi["previous_currency"] = previous_metrics["currencies"][0]
            currencies = sorted(metrics["currencies"])
            if len(currencies) == 1:
                kpi["currency"] = currencies[0]
            elif len(currencies) > 1:
                kpi["currencies"] = currencies
        if name == "visit_to_signup":
            kpi["estimate"] = True
        kpis[name] = kpi
    return kpis


def _change_subjects(
    growth: dict[str, Any], search_sources: set[str],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    queries: dict[str, int] = {}
    pages: dict[str, int] = {}
    channels: dict[str, int] = {}
    for name, source in (growth.get("search") or {}).items():
        if name not in search_sources:
            continue
        for query in source.get("queries") or []:
            if query.get("is_brand"):
                continue
            subject = str(query["query"])
            queries[subject] = queries.get(subject, 0) + int(
                query.get("impressions") or 0
            )
        for page in source.get("pages") or []:
            subject = str(page["page"])
            pages[subject] = pages.get(subject, 0) + int(
                page.get("impressions") or 0
            )
    for row in (growth.get("traffic") or {}).get("daily") or []:
        subject = str(row["channel"])
        channels[subject] = channels.get(subject, 0) + int(
            row.get("visits") or 0
        )
    return queries, pages, channels


def _what_changed(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if previous is None:
        return []
    sources = _kpi_source_sets(current)
    involved = sources["nonbrand_impressions"] | sources["organic_visits"]
    if not (
        _fully_covered(current, _kpi_participants(current, involved))
        and _fully_covered(
            previous, _kpi_participants(previous, involved)
        )
    ):
        return []
    # Query/page changes compare only search feeds that carry detail rows in
    # BOTH windows and are fully covered in both; a feed present or complete
    # in one window only would fabricate gains or losses.
    def _detail_sources(window: dict[str, Any]) -> set[str]:
        return {
            str(name)
            for name, block in (window.get("search") or {}).items()
            if (block or {}).get("queries") or (block or {}).get("pages")
        }

    def _days_fully_covered(window: dict[str, Any], name: str) -> bool:
        # Detail feeds need not carry totals (Yandex.Webmaster), so check day
        # coverage directly rather than through the totals-aware KPI rule.
        allowed = _allowed_dates(window, name, {})
        covered = _covered_dates(window, name)
        if covered is None:
            return _days_covered(window, name) >= len(allowed)
        return allowed <= covered

    def _no_lag(window: dict[str, Any], name: str) -> bool:
        # Query/page aggregates span the whole window: compare them only when
        # the feed has data through the window end (no reporting lag, no
        # gap); otherwise lagging days would read as losses.
        block = (window.get("search") or {}).get(name) or {}
        if block.get("search_gap"):
            return False
        through = block.get("data_through")
        end = (window.get("window") or {}).get("end")
        return through is None or str(through) == str(end)

    common = {
        name
        for name in _detail_sources(current) & _detail_sources(previous)
        if _days_fully_covered(current, name) and _days_fully_covered(previous, name)
        and _no_lag(current, name) and _no_lag(previous, name)
    }
    cur_queries, cur_pages, cur_channels = _change_subjects(current, common)
    prev_queries, prev_pages, prev_channels = _change_subjects(previous, common)

    candidates = []
    for kind, cur_map, prev_map, threshold in (
        ("query", cur_queries, prev_queries, _QUERY_MIN_IMPRESSIONS),
        ("page", cur_pages, prev_pages, _QUERY_MIN_IMPRESSIONS),
        ("channel", cur_channels, prev_channels, _CHANNEL_MIN_VISITS),
    ):
        for subject in set(cur_map) | set(prev_map):
            to_value = cur_map.get(subject, 0)
            from_value = prev_map.get(subject, 0)
            if max(to_value, from_value) < threshold:
                continue
            candidates.append(
                {"kind": kind, "subject": subject,
                 "from": from_value, "to": to_value}
            )

    def rank(item: dict[str, Any]) -> tuple:
        change = item["to"] - item["from"]
        relative = change / item["from"] if item["from"] > 0 else None
        return (
            -abs(change),
            -abs(relative or 0),
            item["kind"],
            item["subject"],
        )

    return sorted(candidates, key=rank)[:_WHAT_CHANGED_MAX]


def _trend_12w(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weeks = sorted(
        history,
        key=lambda growth: str((growth.get("window") or {}).get("start") or ""),
    )[-12:]
    trend = []
    for week in weeks:
        sources = _kpi_source_sets(week)
        metrics = _raw_metrics(week)
        entry: dict[str, Any] = {
            "week_start": (week.get("window") or {}).get("start"),
            "week_end": (week.get("window") or {}).get("end"),
        }
        for name in _TREND_METRICS:
            kpi_sources = _kpi_participants(week, sources[name])
            has_data = kpi_sources and any(
                _days_covered(week, s) > 0 for s in kpi_sources
            )
            entry[name] = _kpi_value(name, metrics) if has_data else None
        entry["revenue_currency"] = (
            metrics["currencies"][0] if len(metrics["currencies"]) == 1 else None
        )
        trend.append(entry)
    return trend


def _signup_channels(current: dict[str, Any]) -> dict[str, Any]:
    events = (current.get("outcomes") or {}).get("analytics_event") or []
    by_channel: dict[str, int] = {}
    for row in events:
        if row.get("outcome_id") != "registration":
            continue
        channel = str(row["channel"])
        by_channel[channel] = by_channel.get(channel, 0) + int(
            row.get("count") or 0
        )
    result: dict[str, Any] = {
        channel: by_channel[channel] for channel in sorted(by_channel)
    }
    # The denominator is the coverage-qualified server registration count:
    # when no outcome source produced covered facts, the sample size is
    # unknown, not zero.
    source_entries = current.get("sources") or {}
    server_sources = {
        name for name in source_entries if name.startswith(_SERVER_OUTCOME_PREFIX)
    }
    server_covered = any(
        int((source_entries.get(name) or {}).get("days_covered") or 0) > 0
        or int((source_entries.get(name) or {}).get("days_with_facts") or 0) > 0
        for name in server_sources
    )
    result["sample_of"] = (
        _raw_metrics(current)["registrations"] if server_covered else None
    )
    return result
