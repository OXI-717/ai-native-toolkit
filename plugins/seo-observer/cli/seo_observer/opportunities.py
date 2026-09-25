from __future__ import annotations

import datetime as dt
import gzip
import json
import sqlite3
import statistics
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seo_observer.crawl import _normalize_url
from seo_observer.storage import SEOStorage, StorageError, resolve_raw_artifact_path


EXPECTED_CTR_BY_POSITION: dict[int, float] = {
    1: 0.28,
    2: 0.15,
    3: 0.11,
    4: 0.08,
    5: 0.06,
    6: 0.05,
    7: 0.04,
    8: 0.035,
    9: 0.03,
    10: 0.025,
}
SECOND_PAGE_MIN_POSITION = 11.0
SECOND_PAGE_MAX_POSITION = 20.0
SECOND_PAGE_EXPECTED_CTR = EXPECTED_CTR_BY_POSITION[10]
MIN_IMPRESSIONS = 1
MIN_SAMPLE_SIZE = 3
DECAY_DROP_THRESHOLD = 0.30
QUICK_WIN_MEDIAN_MODE = "strictly_above"
REFRESH_AGE_SCORE_UNIT_DAYS = 30
LAST_CHANGE_FIELDS = ("last_change", "last_changed", "last_modified", "modified_at", "updated_at", "published_at")
URL_FIELDS = ("url", "page_url", "canonical_url")
CRAWL_OPPORTUNITY_TYPES = (
    "crawl_canonical_conflict",
    "crawl_broken_internal_link",
    "crawl_orphan_page",
    "crawl_meta_robots_conflict",
)
CRAWL_HIGH_SCORE = 100.0
CRAWL_MEDIUM_SCORE = 50.0


@dataclass(frozen=True)
class OpportunityOptions:
    project_id: str
    start: str
    end: str
    type_breakdown: bool = False


def build_opportunity_report(storage: SEOStorage, options: OpportunityOptions) -> dict[str, Any]:
    rows = _fetch_rows(storage, options.project_id, options.start, options.end)
    crawl_rows = _fetch_crawl_rows(storage, options.project_id)
    crawl_breakdown = _crawl_breakdown(rows, crawl_rows)
    sample_size = len(rows)
    if sample_size == 0 and not crawl_rows:
        return _payload(options, status="empty_storage", rows=rows, opportunities=[], breakdown=crawl_breakdown)

    previous_rows = _fetch_previous_rows(storage, options, rows)
    page_last_changes = _page_last_changes(storage, options.project_id, rows)
    breakdown = {
        "quick_win": _quick_wins(rows),
        "second_page": _second_page(rows),
        "decay": _decay(rows, previous_rows),
        "cannibalisation": _cannibalisation(rows),
        "refresh": _refresh(rows, page_last_changes, options.end),
        **crawl_breakdown,
    }
    ranked = sorted(
        [item for items in breakdown.values() for item in items if not item.get("rank_excluded")],
        key=lambda item: (-float(item["score"]), str(item["type"]), str(item.get("query") or item.get("page_url") or "")),
    )
    status = _status(sample_size, ranked)
    return _payload(options, status=status, rows=rows, opportunities=ranked, breakdown=breakdown)


def _payload(
    options: OpportunityOptions,
    *,
    status: str,
    rows: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    breakdown: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "command": "opportunities",
        "project": options.project_id,
        "period": {"start": options.start, "end": options.end},
        "status": status,
        "sample_size": len(rows),
        "opportunities": opportunities,
    }
    if options.type_breakdown:
        payload["type_breakdown"] = breakdown
    return payload


def _status(sample_size: int, ranked: list[dict[str, Any]]) -> str:
    if not ranked:
        if sample_size < MIN_SAMPLE_SIZE:
            return "insufficient_storage"
        return "no_findings"
    has_crawl_finding = any(str(item["type"]).startswith("crawl_") for item in ranked)
    if sample_size < MIN_SAMPLE_SIZE and not has_crawl_finding:
        return "insufficient_storage"
    if any(item["evidence_quality"] == "partial" for item in ranked):
        return "partial"
    return "ok"


def _fetch_rows(storage: SEOStorage, project_id: str, start: str, end: str) -> list[dict[str, Any]]:
    try:
        return storage.fetchall(
            """
            SELECT sp.*, ra.relative_path AS artifact_relative_path
            FROM search_performance sp
            LEFT JOIN raw_artifacts ra ON ra.artifact_id = sp.artifact_id
            WHERE sp.project_id = ?
              AND sp.is_current = 1
              AND sp.segment_id NOT IN ('total', 'brand')
              AND sp.effective_start <= ?
              AND sp.effective_end >= ?
            ORDER BY sp.effective_start, sp.query_text, sp.page_url
            """,
            (project_id, end, start),
        )
    except (StorageError, sqlite3.Error):
        return []


def _fetch_crawl_rows(storage: SEOStorage, project_id: str) -> list[dict[str, Any]]:
    try:
        return storage.fetchall(
            """
            SELECT *
            FROM crawl_pages
            WHERE project_id = ?
              AND is_current = 1
            ORDER BY property_id, crawled_url
            """,
            (project_id,),
        )
    except (StorageError, sqlite3.Error):
        return []


def _fetch_previous_rows(
    storage: SEOStorage,
    options: OpportunityOptions,
    latest_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not latest_rows:
        return []
    start = _date(options.start)
    end = _date(options.end)
    days = (end - start).days + 1
    previous_end = start - dt.timedelta(days=1)
    previous_start = previous_end - dt.timedelta(days=days - 1)
    return _fetch_rows(storage, options.project_id, previous_start.isoformat(), previous_end.isoformat())


def _quick_wins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positive_impressions = [int(row["impressions"]) for row in rows if int(row["impressions"]) > 0]
    if not positive_impressions:
        return []
    median_impressions = float(statistics.median(positive_impressions))
    threshold = max(float(MIN_IMPRESSIONS), median_impressions)
    findings = []
    for row in rows:
        position = _position(row)
        impressions = int(row["impressions"])
        if position is None or position < 1 or position > 10:
            continue
        if not impressions > threshold:
            continue
        expected_ctr = _expected_ctr(position)
        if float(row["ctr"]) >= expected_ctr:
            continue
        potential_clicks = max(impressions * expected_ctr - int(row["clicks"]), 0.0)
        findings.append(
            _finding(
                "quick_win",
                score=potential_clicks,
                rows=[row],
                reason=(
                    f"Impressions {impressions} are strictly above median {median_impressions:.1f}; "
                    f"CTR {float(row['ctr']):.3f} is below heuristic expected CTR {expected_ctr:.3f}."
                ),
                query=str(row["query_text"]),
                page_url=str(row["page_url"]),
                expected_ctr=round(expected_ctr, 4),
                observed_ctr=round(float(row["ctr"]), 4),
                average_position=position,
                potential_clicks=round(potential_clicks, 2),
            )
        )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _second_page(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for row in rows:
        position = _position(row)
        impressions = int(row["impressions"])
        if position is None or impressions <= 0:
            continue
        if SECOND_PAGE_MIN_POSITION <= position <= SECOND_PAGE_MAX_POSITION:
            score = impressions * SECOND_PAGE_EXPECTED_CTR
            findings.append(
                _finding(
                    "second_page",
                    score=score,
                    rows=[row],
                    reason=f"Average position {position:.1f} is inside the inclusive second-page window 11-20.",
                    query=str(row["query_text"]),
                    page_url=str(row["page_url"]),
                    average_position=position,
                    impressions=impressions,
                )
            )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _decay(latest_rows: list[dict[str, Any]], previous_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_page = _group_by(latest_rows, "page_url")
    previous_by_page = _group_by(previous_rows, "page_url")
    findings = []
    for page_url, page_rows in latest_by_page.items():
        latest_clicks = sum(int(row["clicks"]) for row in page_rows)
        prev_rows = previous_by_page.get(page_url, [])
        if not prev_rows:
            findings.append(
                _finding(
                    "decay",
                    score=0,
                    rows=page_rows,
                    reason="Latest window exists, but the previous comparable window is missing.",
                    evidence_quality="partial",
                    rank_excluded=True,
                    page_url=page_url,
                    latest_clicks=latest_clicks,
                    previous_clicks=None,
                    drop_percent=None,
                )
            )
            continue
        previous_clicks = sum(int(row["clicks"]) for row in prev_rows)
        if previous_clicks <= 0:
            findings.append(
                _finding(
                    "decay",
                    score=0,
                    rows=[*page_rows, *prev_rows],
                    reason="Previous comparable window has zero clicks, so click decay is not comparable.",
                    evidence_quality="not_comparable",
                    rank_excluded=True,
                    page_url=page_url,
                    latest_clicks=latest_clicks,
                    previous_clicks=previous_clicks,
                    drop_percent=None,
                )
            )
            continue
        drop = (previous_clicks - latest_clicks) / previous_clicks
        if drop >= DECAY_DROP_THRESHOLD:
            findings.append(
                _finding(
                    "decay",
                    score=(previous_clicks - latest_clicks) * drop,
                    rows=[*page_rows, *prev_rows],
                    reason=(
                        f"Clicks dropped from {previous_clicks} to {latest_clicks}, "
                        f"meeting the {DECAY_DROP_THRESHOLD:.0%} decay threshold."
                    ),
                    page_url=page_url,
                    latest_clicks=latest_clicks,
                    previous_clicks=previous_clicks,
                    drop_percent=round(drop, 4),
                )
            )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _cannibalisation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for query, query_rows in _group_by(rows, "query_text").items():
        urls_by_domain: dict[str, set[str]] = {}
        for row in query_rows:
            domain = _domain(str(row["page_url"]))
            if not domain:
                continue
            urls_by_domain.setdefault(domain, set()).add(str(row["page_url"]))
        for domain, urls in urls_by_domain.items():
            if len(urls) < 2:
                continue
            domain_rows = [row for row in query_rows if _domain(str(row["page_url"])) == domain]
            total_impressions = sum(int(row["impressions"]) for row in domain_rows)
            findings.append(
                _finding(
                    "cannibalisation",
                    score=total_impressions * (len(urls) - 1),
                    rows=domain_rows,
                    reason=f"Query alternates between {len(urls)} URLs on {domain} in the selected window.",
                    query=query,
                    domain=domain,
                    urls=sorted(urls),
                    url_count=len(urls),
                    impressions=total_impressions,
                )
            )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _refresh(
    rows: list[dict[str, Any]],
    page_last_changes: dict[str, dt.date],
    period_end: str,
) -> list[dict[str, Any]]:
    by_page = _group_by(rows, "page_url")
    end_date = _date(period_end)
    findings = []
    for page_url, page_rows in by_page.items():
        potential = sum(_potential_clicks(row) for row in page_rows)
        if potential <= 0:
            continue
        last_change = page_last_changes.get(page_url)
        if last_change is None:
            findings.append(
                _finding(
                    "refresh",
                    score=0,
                    rows=page_rows,
                    reason="Refresh potential exists, but no last-change date was found in existing artifacts.",
                    evidence_quality="missing",
                    rank_excluded=True,
                    page_url=page_url,
                    potential_clicks=round(potential, 2),
                    last_change=None,
                    age_days=None,
                )
            )
            continue
        age_days = max((end_date - last_change).days, 0)
        score = potential * (age_days / REFRESH_AGE_SCORE_UNIT_DAYS)
        findings.append(
            _finding(
                "refresh",
                score=score,
                rows=page_rows,
                reason=f"Refresh potential {potential:.2f} is weighted by {age_days} days since last artifact change.",
                page_url=page_url,
                potential_clicks=round(potential, 2),
                last_change=last_change.isoformat(),
                age_days=age_days,
            )
        )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _crawl_breakdown(search_rows: list[dict[str, Any]], crawl_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not crawl_rows:
        return {opportunity_type: [_missing_crawl_finding(opportunity_type)] for opportunity_type in CRAWL_OPPORTUNITY_TYPES}
    return {
        "crawl_canonical_conflict": _crawl_canonical_conflicts(crawl_rows),
        "crawl_broken_internal_link": _crawl_broken_internal_links(crawl_rows),
        "crawl_orphan_page": _crawl_orphan_pages(search_rows, crawl_rows),
        "crawl_meta_robots_conflict": _crawl_meta_robots_conflicts(crawl_rows),
    }


def _missing_crawl_finding(opportunity_type: str) -> dict[str, Any]:
    return _finding(
        opportunity_type,
        score=0,
        rows=[],
        reason="No current local crawl evidence is available for this project.",
        evidence_quality="missing",
        rank_excluded=True,
    )


def _crawl_canonical_conflicts(crawl_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for row in crawl_rows:
        canonical_url = row.get("canonical_url")
        header_canonical_url = row.get("header_canonical_url")
        if not canonical_url or not header_canonical_url or canonical_url == header_canonical_url:
            continue
        findings.append(
            _finding(
                "crawl_canonical_conflict",
                score=CRAWL_HIGH_SCORE,
                rows=[row],
                reason="HTML canonical and Link header canonical disagree.",
                evidence_quality="complete",
                page_url=str(row["crawled_url"]),
                property_id=str(row["property_id"]),
                canonical_url=str(canonical_url),
                header_canonical_url=str(header_canonical_url),
            )
        )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _crawl_meta_robots_conflicts(crawl_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for row in crawl_rows:
        meta_robots = row.get("meta_robots")
        x_robots_tag = row.get("x_robots_tag")
        if not meta_robots or not x_robots_tag:
            continue
        if _robots_directives(str(meta_robots)) == _robots_directives(str(x_robots_tag)):
            continue
        findings.append(
            _finding(
                "crawl_meta_robots_conflict",
                score=CRAWL_HIGH_SCORE,
                rows=[row],
                reason="Meta robots and X-Robots-Tag directives disagree.",
                evidence_quality="complete",
                page_url=str(row["crawled_url"]),
                property_id=str(row["property_id"]),
                meta_robots=str(meta_robots),
                x_robots_tag=str(x_robots_tag),
            )
        )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _crawl_broken_internal_links(crawl_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linked_from: dict[tuple[str, str], set[str]] = {}
    for row in crawl_rows:
        source_url = str(row["crawled_url"])
        property_id = str(row["property_id"])
        for link in _json_list(str(row.get("internal_links_json") or "[]")):
            if isinstance(link, str) and link:
                linked_from.setdefault((property_id, _normalize_url(link)), set()).add(source_url)

    findings = []
    for row in crawl_rows:
        status = row.get("fetch_status")
        if status is None or int(status) < 400:
            continue
        property_id = str(row["property_id"])
        page_url = str(row["crawled_url"])
        source_pages = sorted(linked_from.get((property_id, _normalize_url(page_url)), set()))
        if not source_pages:
            continue
        findings.append(
            _finding(
                "crawl_broken_internal_link",
                score=CRAWL_HIGH_SCORE + len(source_pages),
                rows=[row],
                reason=f"Internal crawl reached HTTP {int(status)} from {len(source_pages)} source page(s).",
                evidence_quality="complete",
                page_url=page_url,
                property_id=property_id,
                fetch_status=int(status),
                source_pages=source_pages,
            )
        )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _crawl_orphan_pages(search_rows: list[dict[str, Any]], crawl_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reachable: set[tuple[str, str]] = set()
    for row in crawl_rows:
        property_id = str(row["property_id"])
        for field in ("crawled_url", "final_url"):
            url = row.get(field)
            if url:
                reachable.add((property_id, _normalize_url(str(url))))
        for link in _json_list(str(row.get("internal_links_json") or "[]")):
            if isinstance(link, str) and link:
                reachable.add((property_id, _normalize_url(link)))

    findings = []
    for page_url, page_rows in _group_by(search_rows, "page_url").items():
        property_id = str(page_rows[0].get("property_id") or "__all__")
        if (property_id, _normalize_url(page_url)) in reachable:
            continue
        impressions = sum(int(row["impressions"]) for row in page_rows)
        findings.append(
            _finding(
                "crawl_orphan_page",
                score=max(float(impressions), CRAWL_MEDIUM_SCORE),
                rows=page_rows,
                reason="Known URL from search_performance was not reached by the latest crawl graph.",
                page_url=page_url,
                property_id=property_id,
                impressions=impressions,
            )
        )
    return sorted(findings, key=lambda item: item["score"], reverse=True)


def _json_list(value: str) -> list[Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return decoded if isinstance(decoded, list) else []


def _robots_directives(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _finding(
    opportunity_type: str,
    *,
    score: float,
    rows: list[dict[str, Any]],
    reason: str,
    evidence_quality: str | None = None,
    rank_excluded: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    quality = evidence_quality or _evidence_quality(rows)
    # Explicit quality is used for crawl facts where one row can be complete evidence.
    if evidence_quality is None and len(rows) < MIN_SAMPLE_SIZE and quality == "complete":
        quality = "partial"
    return {
        "type": opportunity_type,
        "score": round(float(score), 4),
        "reason": reason,
        "evidence_quality": quality,
        "sample_size": len(rows),
        "rank_excluded": rank_excluded,
        **extra,
    }


def _evidence_quality(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "missing"
    if any(str(row.get("freshness") or "") == "stale" for row in rows):
        return "stale"
    if any(str(row.get("comparability") or "comparable") != "comparable" for row in rows):
        return "not_comparable"
    if any(
        str(row.get("dataset_coverage") or "unknown") != "complete"
        or int(row.get("sampled") or 0) == 1
        for row in rows
    ):
        return "partial"
    return "complete"


def _potential_clicks(row: dict[str, Any]) -> float:
    impressions = int(row["impressions"])
    position = _position(row)
    if position is None:
        expected_ctr = SECOND_PAGE_EXPECTED_CTR
    elif 1 <= position <= 10:
        expected_ctr = _expected_ctr(position)
    elif SECOND_PAGE_MIN_POSITION <= position <= SECOND_PAGE_MAX_POSITION:
        expected_ctr = SECOND_PAGE_EXPECTED_CTR
    else:
        expected_ctr = 0.0
    return max(impressions * expected_ctr - int(row["clicks"]), 0.0)


def _expected_ctr(position: float) -> float:
    bucket = min(max(round(position), 1), 10)
    return EXPECTED_CTR_BY_POSITION[int(bucket)]


def _position(row: dict[str, Any]) -> float | None:
    value = row.get("average_position")
    return None if value is None else float(value)


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(row)
    return grouped


def _domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value[:10])


def _page_last_changes(storage: SEOStorage, project_id: str, rows: list[dict[str, Any]]) -> dict[str, dt.date]:
    changes: dict[str, dt.date] = {}
    seen_paths: set[str] = set()
    for row in rows:
        relative_path = str(row.get("artifact_relative_path") or "")
        if not relative_path or relative_path in seen_paths:
            continue
        seen_paths.add(relative_path)
        try:
            artifact_path = resolve_raw_artifact_path(
                observer_home=storage.observer_home,
                project_id=project_id,
                relative_path=relative_path,
            )
            artifact = _read_artifact_json(artifact_path)
        except (OSError, StorageError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        for url, changed_at in _iter_last_change_dates(artifact):
            previous = changes.get(url)
            if previous is None or changed_at > previous:
                changes[url] = changed_at
    return changes


def _read_artifact_json(path: Path) -> Any:
    if path.suffix == ".gz":
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return json.load(handle)
        except gzip.BadGzipFile:
            pass
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_last_change_dates(value: Any) -> list[tuple[str, dt.date]]:
    found: list[tuple[str, dt.date]] = []
    if isinstance(value, dict):
        url = next((str(value[field]) for field in URL_FIELDS if isinstance(value.get(field), str)), None)
        changed = next((_parse_artifact_date(value[field]) for field in LAST_CHANGE_FIELDS if value.get(field)), None)
        if url and changed:
            found.append((url, changed))
        for child in value.values():
            found.extend(_iter_last_change_dates(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_iter_last_change_dates(child))
    return found


def _parse_artifact_date(value: Any) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return _date(value)
    except ValueError:
        return None
