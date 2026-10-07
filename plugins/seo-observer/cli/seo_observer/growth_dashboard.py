"""Current growth dashboard: escaped, self-contained presentation of shared facts.

Weekly/PDF/English exports keep the legacy document renderer. All comparison
eligibility and KPI arithmetic remain in growth.py.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from seo_observer import growth_render as legacy
from seo_observer.config import PanelConfig
from seo_observer.money import CURRENCY_EXPONENTS, currency_symbol, format_minor
from seo_observer.keyword_clusters import clean_cluster
from seo_observer.growth_locale import text as _t
from seo_observer.growth import _fully_covered, _kpi_source_sets, _SEARCH_LAG_GAP_DAYS

esc = legacy._esc
ASSETS = Path(__file__).with_name("assets")
PAGES = (
    ("index.html", _t("page_summary")),
    ("positions.html", _t("page_positions")),
    ("demand.html", _t("page_demand")),
    ("traffic.html", _t("page_traffic")),
    ("money.html", _t("page_money")),
    ("status.html", _t("page_status")),
)
SOURCE_NAMES = {
    "google_search_console": "Google",
    "yandex_webmaster": _t("yandex"),
    "ga4": "Google Analytics",
    "yandex_metrica": _t("metrica"),
    "mixpanel": "Mixpanel",
    "outcome_auth": _t("source_auth"),
    "outcome_pay": _t("source_pay"),
}
SOURCE_PURPOSE = {
    "google_search_console": _t("purpose_google"),
    "yandex_webmaster": _t("purpose_yandex"),
    "ga4": _t("purpose_analytics"),
    "yandex_metrica": _t("purpose_metrica"),
    "mixpanel": _t("purpose_mixpanel"),
    "outcome_auth": _t("purpose_auth"),
    "outcome_pay": _t("purpose_pay"),
}
MONTHS = (
    _t("jan"),
    _t("feb"),
    _t("mar"),
    _t("apr"),
    _t("may"),
    _t("jun"),
    _t("jul"),
    _t("aug"),
    _t("sep"),
    _t("oct"),
    _t("nov"),
    _t("dec"),
)
BUCKETS = ("1–3", "4–10", "11–30", "31–100", ">100", _t("not_shown"))


@dataclass(frozen=True)
class Cell:
    html: str
    sort: Any = ""


def number(value: Any, decimals: int = 0, reason: str = _t("missing")) -> Cell:
    if value is None:
        return Cell(f'<span title="{esc(reason)}">—</span>')
    text = (
        legacy._fmt_int(value)
        if not decimals
        else f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    )
    return Cell(esc(text), value)


def label(value: Any, *, title: str | None = None) -> Cell:
    return Cell(
        f'<span class="query-cell" title="{esc(value if title is None else title)}">{esc(value)}</span>',
        str(value),
    )


def delta(
    value: Any, *, decimals: int = 0, suffix: str = "", reason: str = _t("incomparable"),
    display: Cell | None = None,
) -> Cell:
    if value is None:
        return number(None, reason=reason)
    arrow = "▲" if value > 0 else "▼" if value < 0 else ""
    state = "positive" if value > 0 else "negative" if value < 0 else "neutral"
    text = display.html if display is not None else number(abs(value), decimals).html
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return Cell(
        f'<span class="{state}">{arrow} {sign}{text}{esc(suffix)}</span>', value
    )


def transition(before: Any, after: Any) -> Cell:
    if before is None and after is not None:
        return Cell(_t("new_observation").format(number(after).html), after)
    return Cell(
        f"{number(before).html} → {number(after).html}",
        after if after is not None else "",
    )


def human_date(value: str | None) -> str:
    if not value:
        return "—"
    d = date.fromisoformat(value[:10])
    return f"{d.day} {MONTHS[d.month - 1]}"


def date_range(window: dict) -> str:
    if not window.get("start") or not window.get("end"):
        return "—"
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    if start.month == end.month and start.year == end.year:
        return f"{start.day}–{end.day} {MONTHS[end.month - 1]}"
    return f"{human_date(window['start'])} — {human_date(window['end'])}"


def moscow_stamp(value: str) -> str:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        ZoneInfo("Europe/Moscow")
    )
    return _t("updated_at").format(human_date(stamp.date().isoformat()), stamp)


def section(title: str, body: str, cls: str = "") -> str:
    return f'<section class="{esc(cls)}"><h2>{esc(title)}</h2>{body}</section>'


def empty(reason: str = _t("empty_period")) -> str:
    return f'<p class="empty"><span title="{esc(reason)}">—</span> <span class="caption">{esc(reason)}</span></p>'


def table(
    headers: list[str],
    rows: list[list[Any]],
    *,
    limit: int = 15,
    attrs: list[dict[str, str]] | None = None,
    cls: str = "",
) -> str:
    if not rows:
        return empty()
    head = "".join(
        f'<th scope="col"><button type="button" data-sort>{esc(h)}</button></th>'
        for h in headers
    )
    body = []
    for i, row in enumerate(rows):
        cells = []
        for value in row:
            if isinstance(value, Cell):
                cells.append(
                    f'<td data-sort-value="{esc(value.sort)}">{value.html}</td>'
                )
            elif isinstance(value, (int, float)) or value is None:
                cell = number(value)
                cells.append(f'<td data-sort-value="{esc(cell.sort)}">{cell.html}</td>')
            else:
                cells.append(f"<td>{esc(value)}</td>")
        data = "".join(
            f' data-{esc(k)}="{esc(v)}"' for k, v in (attrs[i] if attrs else {}).items()
        )
        body.append(
            f"<tr{data}"
            + (" hidden" if i >= limit else "")
            + ">"
            + "".join(cells)
            + "</tr>"
        )
    return (
        f'<div class="table-wrap"><div class="scroll"><table class="{esc(cls)}" data-row-limit="{limit}">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        '<div class="table-controls"><button type="button" data-expand aria-expanded="false"'
        + (" hidden" if len(rows) <= limit else "")
        + _t("table_controls").format(len(rows), min(limit, len(rows)), len(rows))
    )


def money_number(minor: int, currency: str | None) -> Cell:
    text = format_minor(minor, currency, decimal_separator=",", compact=True)
    if text is None:
        reason = esc(_t("unsupported_currency").format(currency or "—"))
        return Cell(f'<span class="unsupported-money">{reason}</span>')
    return Cell(esc(text), minor)


def money_value(minor: int, currency: str | None) -> Cell:
    amount = money_number(minor, currency)
    if currency not in CURRENCY_EXPONENTS:
        return amount
    unit = currency_symbol(currency)
    return Cell(amount.html + " " + esc(unit), amount.sort)


def chart(
    points: list[tuple[str, Any]],
    *,
    title: str,
    money: bool = False,
    currency: str | None = None,
    percent: bool = False,
    partial_last: bool = False,
) -> str:
    values = [v for _, v in points if v is not None]
    if not values:
        return '<div class="chart">' + empty(_t("empty_chart")) + "</div>"
    if money and currency not in CURRENCY_EXPONENTS:
        return '<div class="chart">' + empty(_t("unsupported_currency").format(currency or "—")) + "</div>"
    maximum = max(values) or 1
    segments, current, circles = [], [], []
    for i, (day, value) in enumerate(points):
        if value is None:
            if current:
                segments.append(current)
            current = []
            continue
        x = 4 + 292 * i / max(1, len(points) - 1)
        y = 62 - 56 * value / maximum
        current.append(f"{x:.2f},{y:.2f}")
        partial = partial_last and i == len(points) - 1
        point_title = human_date(day) + (" · " + _t("partial_window") if partial else "")
        circles.append(
            f'<circle data-date="{esc(day)}" data-value="{esc(value)}" '
            f'cx="{x:.2f}" cy="{y:.2f}" r="2" stroke="currentColor" '
            f'fill="{"var(--surface)" if partial else "currentColor"}">'
            f'<title>{esc(point_title)}</title></circle>'
        )
    if current:
        segments.append(current)
    lines = "".join(
        f'<polyline points="{" ".join(s)}" fill="none" stroke="currentColor" stroke-width="1.6" vector-effect="non-scaling-stroke"/>'
        for s in segments
    )
    max_label = (
        money_number(max(values), currency).html if money
        else number(max(values) * 100, 1).html + "%" if percent
        else number(max(values)).html
    )
    return _t("chart").format(
        esc(title),
        esc(title),
        lines,
        "".join(circles),
        esc(human_date(points[0][0])),
        max_label,
        esc(human_date(points[-1][0]) + (" · " + _t("partial_window") if partial_last else "")),
    )


def kpis(growth: dict, names: tuple[str, ...], *, compact: bool = False) -> str:
    metrics = (growth.get("derived") or {}).get("kpis") or {}
    weeks = (growth.get("derived") or {}).get("trend_12w") or []
    labels = {
        **legacy._strings("ru"), "organic_visits": _t("search_visits"),
        "nonbrand_impressions": _t("nonbrand_impressions"),
        "nonbrand_clicks": _t("nonbrand_clicks"),
    }
    notes = {
        "organic_visits": _t("search_visits_note"),
        "registrations": _t("registrations_note"),
        "payments": _t("payments_note"),
        "revenue_minor": _t("revenue_note"),
        "visit_to_signup": _t("conversion_note"),
    }
    cards = []
    for name in names:
        kpi = metrics.get(name) or {}
        value = kpi.get("value")

        def formatted(v, currency=None):
            if v is None:
                return number(None).html
            if name == "revenue_minor":
                return money_value(v, currency).html
            return legacy._fmt_kpi(name, {**kpi, "value": v}, legacy._strings("ru"))

        display = formatted(value, kpi.get("currency"))
        coverage = ""
        days = int(growth.get("window", {}).get("days") or 0)
        covered = int(kpi.get("days_covered") or 0)
        if kpi.get("coverage") != "complete":
            coverage = _t("coverage_partial").format(covered, days)
        if compact:
            cards.append(
                f'<div><b>{display}</b>{esc(labels[name])}<p class="caption">{coverage}</p></div>'
            )
            continue
        if kpi.get("delta_pct") is not None:
            change = delta(kpi["delta_pct"], decimals=0, suffix="%")
        elif kpi.get("delta_abs") is None:
            change = delta(None)
        elif name == "revenue_minor":
            amount = kpi["delta_abs"]
            currency = kpi.get("currency") or kpi.get("previous_currency")
            change = (
                delta(amount, display=money_value(abs(amount), currency))
                if currency in CURRENCY_EXPONENTS else money_value(amount, currency)
            )
        elif name == "visit_to_signup":
            change = delta(kpi["delta_abs"] * 100, decimals=1, suffix=" " + _t("percentage_points"))
        else:
            change = delta(kpi["delta_abs"])
        comparison = _t("versus_week") if days == 7 else _t("versus_28")
        if change.sort == 0:
            change = Cell(esc(_t("unchanged")), 0)
            comparison = ""
        previous = formatted(
            kpi.get("previous"), kpi.get("previous_currency", kpi.get("currency"))
        )
        link = "traffic.html" if name == "organic_visits" else "money.html"
        # A current 28-day point is an explicitly labelled window total, not a week.
        history = sorted(
            (w for w in weeks if w["week_start"] < growth["window"]["start"]),
            key=lambda w: w["week_start"],
        )[-11:]
        currency = kpi.get("currency")
        if name == "revenue_minor" and value == 0 and not currency:
            currencies = {w.get("revenue_currency") for w in history if w.get("revenue_currency")}
            if len(currencies) == 1:
                currency = next(iter(currencies))
        points = [
            (
                w["week_start"],
                w.get(name)
                if name != "revenue_minor"
                or (currency and w.get("revenue_currency") == currency)
                or (w.get("revenue_currency") is None and w.get(name) == 0)
                else None,
            )
            for w in history
        ]
        points.append((growth["window"]["start"], value))
        spark = chart(
            points,
            title=_t("trend_label").format(labels[name], days),
            money=name == "revenue_minor",
            currency=currency,
            percent=name == "visit_to_signup",
            partial_last=kpi.get("coverage") != "complete",
        )
        cards.append(
            _t("kpi_card").format(
                esc(labels[name]),
                display,
                esc(previous),
                change.html,
                comparison,
                spark,
                esc(notes.get(name, "")),
                coverage,
                link,
            )
        )
    return f'<div class="{"statline" if compact else "cards"}">{"".join(cards)}</div>'


def _source_name(name: str) -> str:
    return SOURCE_NAMES.get(name, name)


def _dashboard_sources(growth: dict) -> set[str]:
    return set().union(*_kpi_source_sets(growth).values()) | (
        set(growth.get("sources") or {}) & {"mixpanel"}
    )


def _dash_through(growth: dict, source: str) -> str | None:
    entry = (growth.get("sources") or {}).get(source) or {}
    search = (growth.get("search") or {}).get(source) or {}
    if search:
        return (
            search.get("details_through")
            or search.get("data_through")
            or entry.get("data_through")
        )
    return max(entry.get("covered_days") or [], default=entry.get("data_through"))


def _detail_comparable(current: dict, previous: dict, source: str) -> bool:
    for window in (current, previous):
        entry = (window.get("sources") or {}).get(source, {})
        dates = entry.get("covered_days") or []
        block = (window.get("search") or {}).get(source) or {}
        days = (window.get("window") or {}).get("days")
        through = block.get("details_through")
        lag = (
            (date.fromisoformat(window["window"]["end"]) - date.fromisoformat(through)).days
            if through else None
        )
        first = date.fromisoformat(window["window"]["start"])
        expected = {(first + timedelta(days=i)).isoformat() for i in range(days or 0)}
        if (
            not days or not expected <= set(dates)
            or entry.get("state") == "stale"
            or lag is None or not 0 <= lag <= _SEARCH_LAG_GAP_DAYS
        ):
            return False
    return True


def _position_bucket(position: Any) -> str:
    if position is None or position <= 0:
        return BUCKETS[-1]
    for boundary, bucket in zip((3, 10, 30, 100), BUCKETS):
        if position <= boundary:
            return bucket
    return ">100"


def position_buckets(queries: list[dict]) -> dict[str, int]:
    result = dict.fromkeys(BUCKETS, 0)
    for row in queries:
        result[_position_bucket(row.get("position"))] += 1
    return result


def filters(clusters: dict[str, dict[str, str | None]], positions: bool) -> str:
    body = _t("query_filter")
    body += _t("engine_filter")
    if positions:
        body += (
            _t("bucket_filter")
            + "".join(f"<option>{esc(b)}</option>" for b in BUCKETS)
            + "</select></label>"
        )
    body += (
        _t("cluster_filter")
        + "".join(
            f"<option>{esc(c)}</option>"
            for c in sorted({clean_cluster(c) for scoped in clusters.values() for c in scoped.values() if c} | {_t("unclustered")})
        )
        + "</select></label></div>"
    )
    return body


def cluster_for(query: str, clusters: dict, source: str) -> str:
    query = " ".join(query.casefold().split())
    fallback = clusters.get("*", {}).get(query)
    name = clusters.get(source, {}).get(query, fallback)
    return clean_cluster(name) if name else _t("unclustered")


def positions(growth: dict, previous: dict, history: list[dict], clusters: dict) -> str:
    search = growth.get("search") or {}
    all_rows, distribution, summaries = [], [], []
    history = sorted(history, key=lambda w: w.get("window", {}).get("start", ""), reverse=True)[:8]
    for source, block in sorted(search.items()):
        queries = block.get("queries") or []
        before = {
            r["query"]: r
            for r in ((previous.get("search") or {}).get(source) or {}).get("queries")
            or []
        }
        # Include previously observed queries so "not shown" has a useful filter.
        observed = {r["query"]: r for r in queries}
        universe = list(queries) + [
            dict(query=q, position=None, impressions=None, clicks=None)
            for q in before
            if q not in observed
        ]
        buckets = position_buckets(universe)
        total = max(1, len(universe))
        stack, legend = [], []
        for tone, (bucket, n) in enumerate(buckets.items()):
            attrs = f'data-tone="{tone}" data-segment="{esc(bucket)}" data-engine="{esc(source)}"'
            if n:
                stack.append(
                    _t("bucket_button").format(
                        attrs,
                        n,
                        esc(bucket),
                        n,
                        esc(bucket),
                        n,
                        n if n / total > 0.06 else "",
                    )
                )
            legend.append(f"<button {attrs}>{esc(bucket)} · {n}</button>")
        distribution.append(
            section(
                _source_name(source),
                '<div class="stack">'
                + "".join(stack)
                + '</div><div class="legend">'
                + "".join(legend)
                + "</div>",
            )
        )
        grouped = {}
        comparable = _detail_comparable(growth, previous, source)
        week_maps = [
            {
                r["query"]: r
                for r in ((w.get("search") or {}).get(source) or {}).get("queries")
                or []
            }
            for w in history
        ]
        for row in universe:
            name = cluster_for(row["query"], clusters, source)
            prior = before.get(row["query"]) or {}
            pos, old = row.get("position"), prior.get("position")
            change = (
                old - pos
                if comparable and old is not None and pos is not None
                else None
            )
            cells = [
                label(row["query"]),
                _source_name(source),
                label(name),
                number(row.get("impressions")),
                number(old, 1, _t("missing_previous_rank")),
                delta(change, decimals=1),
                number(pos, 1, _t("missing_rank")),
            ]
            cells += [
                number(w.get(row["query"], {}).get("position"), 1, _t("missing_rank"))
                for w in week_maps
            ]
            all_rows.append(
                (
                    row.get("impressions") or 0,
                    cells,
                    {
                        "query": row["query"],
                        "engine": source,
                        "bucket": _position_bucket(pos),
                        "cluster": name,
                    },
                )
            )
            group = grouped.setdefault(
                name, {"count": 0, "impressions": 0, "weighted": 0, "weight": 0}
            )
            group["count"] += 1
            group["impressions"] += row.get("impressions") or 0
            if pos is not None and row.get("impressions"):
                group["weighted"] += pos * row["impressions"]
                group["weight"] += row["impressions"]
        for name, group in grouped.items():
            summaries.append(
                [
                    label(name),
                    _source_name(source),
                    number(
                        group["weighted"] / group["weight"]
                        if group["weight"]
                        else None,
                        1,
                    ),
                    group["impressions"],
                    group["count"],
                ]
            )
    from seo_observer import growth_serp
    measurements = growth.get("serp") or []
    body = growth_serp.positions(measurements, growth)
    body += '<div class="columns">' + "".join(distribution) + "</div>"
    if measurements:
        for _, cells, attrs in all_rows:
            cells.insert(7, growth_serp.position_cell(measurements, attrs["query"], attrs["engine"]))
    body += _t("positions_note")
    body += filters(clusters, True)
    all_rows.sort(key=lambda item: -item[0])
    headers = [
        _t("query"),
        _t("engine"),
        _t("cluster"),
        _t("impressions"),
        _t("before"),
        _t("change"),
        _t("after"),
    ] + [human_date(w.get("window", {}).get("start")) for w in history]
    if measurements:
        headers.insert(7, _t('serp_position'))
    note = ""
    for source in search:
        if _detail_comparable(growth, previous, source):
            continue
        reasons = []
        for window, qualifier in ((growth, ""), (previous, _t("previous_period") + ": ")):
            days = window.get("window", {}).get("days") or 0
            covered = len(set(window.get("sources", {}).get(source, {}).get("covered_days") or []))
            if covered < days:
                reasons.append(qualifier + _t("coverage_plain").format(covered, days))
        reason = reasons[0] if reasons else _t("freshness_unconfirmed")
        note += _t("engine_incomparable").format(esc(_source_name(source)), esc(reason))
    body += section(
        _t("weekly_positions"),
        note
        + table(
            headers,
            [r[1] for r in all_rows],
            limit=25,
            attrs=[r[2] for r in all_rows],
            cls="matrix",
        ),
    )
    summaries.sort(key=lambda r: -r[3])
    body += section(
        _t("clusters"),
        table(
            [
                _t("cluster"),
                _t("engine"),
                _t("average_position"),
                _t("impressions"),
                _t("query_count"),
            ],
            summaries,
        ),
    )
    return body


def demand(growth: dict, previous: dict, clusters: dict) -> str:
    opportunities, queries, pages, attrs = [], [], [], []
    for source, block in sorted((growth.get("search") or {}).items()):
        before = {
            r["query"]: r
            for r in ((previous.get("search") or {}).get(source) or {}).get("queries")
            or []
        }
        for row in block.get("queries") or []:
            imp, clicks, pos = (
                row.get("impressions"),
                row.get("clicks"),
                row.get("position"),
            )
            ctr = clicks / imp * 100 if imp and clicks is not None else None
            name = cluster_for(row["query"], clusters, source)
            prior = before.get(row["query"]) or {}
            cells = [
                label(row["query"]),
                _source_name(source),
                transition(prior.get("impressions"), imp),
                transition(prior.get("clicks"), clicks),
                number(ctr, 2),
                number(pos, 1),
            ]
            queries.append(
                (
                    imp or 0,
                    cells,
                    {"query": row["query"], "engine": source, "cluster": name},
                )
            )
            if (
                pos is not None
                and 4 <= pos <= 20
                and imp
                and imp >= 20
                and ctr is not None
                and ctr < 5
            ):
                opportunities.append(
                    (
                        imp,
                        [
                            label(row["query"]),
                            _source_name(source),
                            imp,
                            clicks,
                            number(pos, 1),
                        ],
                    )
                )
        previous_pages = {
            r["page"]: r
            for r in ((previous.get("search") or {}).get(source) or {}).get("pages")
            or []
        }
        page_rows = block.get("pages") or []
        query_index, counts = legacy._page_query_details(block, page_rows)
        for row in page_rows:
            prior = previous_pages.get(row["page"]) or {}
            related = query_index[row["page"]]
            details = (
                table(
                    [_t("query"), _t("impressions"), _t("clicks")],
                    [[r["query"], r["impressions"], r["clicks"]] for r in related],
                )
                if related
                else _t("query_pages_unavailable")
            )
            if counts[row["page"]] > 100:
                details += _t("query_pages_limit").format(counts[row["page"]])
            detail = Cell(
                _t("query_details").format(counts[row["page"]], details),
                counts[row["page"]],
            )
            pages.append(
                (
                    row.get("impressions") or 0,
                    [
                        label(urlsplit(row["page"]).path or "/", title=row["page"]),
                        _source_name(source),
                        transition(prior.get("impressions"), row.get("impressions")),
                        transition(prior.get("clicks"), row.get("clicks")),
                        detail,
                    ],
                )
            )
    opportunities.sort(key=lambda r: -r[0])
    queries.sort(key=lambda r: -r[0])
    pages.sort(key=lambda r: -r[0])
    body = section(
        _t("opportunities"),
        _t("opportunities_note").format(min(10, len(opportunities)))
        + table(
            [
                _t("query"),
                _t("engine"),
                _t("impressions"),
                _t("clicks"),
                _t("position"),
            ],
            [r[1] for r in opportunities[:10]],
        ),
    )
    body += kpis(
        growth, ("nonbrand_impressions", "nonbrand_clicks"), compact=True
    ) + filters(clusters, False)
    body += section(
        _t("queries"),
        _t("sample_note")
        + table(
            [
                _t("query"),
                _t("engine"),
                _t("impressions_change"),
                _t("clicks_change"),
                "CTR, %",
                _t("position"),
            ],
            [r[1] for r in queries],
            attrs=[r[2] for r in queries],
        ),
    )
    body += section(
        _t("pages"),
        table(
            [
                _t("page"),
                _t("engine"),
                _t("impressions_change"),
                _t("clicks_change"),
                _t("page_queries"),
            ],
            [r[1] for r in pages],
        ),
    )
    return body


def channel_label(channel: str) -> str:
    return legacy._strings("ru").get("channel_" + channel, _t("other_channel"))


def channel_totals(growth: dict) -> dict:
    result = {}
    for row in (growth.get("traffic") or {}).get("daily") or []:
        result[row["channel"]] = result.get(row["channel"], 0) + row["visits"]
    return result


def share_cell(value: int | None, total: int) -> Cell:
    if value is None or not total:
        return number(None, reason=_t("unknown_share"))
    pct = value / total * 100
    return Cell(
        f'<span class="share"><span class="bar-track"><span class="bar" style="width:{pct:.2f}%"></span></span>{number(pct, 1).html}%</span>',
        pct,
    )


def channel_bars(growth: dict, previous: dict | None = None) -> str:
    now = channel_totals(growth)
    before = channel_totals(previous or {})
    total = sum(n for ch, n in now.items() if ch != "app")
    complete = _fully_covered(growth, _kpi_source_sets(growth)["organic_visits"])
    prior_complete = _fully_covered(
        previous or {}, _kpi_source_sets(previous or {})["organic_visits"]
    )
    rows = []
    for ch in sorted(set(now) | set(before), key=lambda c: -now.get(c, 0)):
        if ch == "app":
            continue
        value = now.get(ch, 0 if complete else None)
        if previous is None:
            rows.append([channel_label(ch), value, share_cell(value, total)])
        else:
            old = before.get(ch, 0 if prior_complete else None)
            change = (
                value - old
                if complete and prior_complete and old is not None and value is not None
                else None
            )
            rows.append(
                [channel_label(ch), old, value, delta(change), share_cell(value, total)]
            )
    headers = (
        [_t("channel"), _t("visits"), _t("share")]
        if previous is None
        else [_t("channel"), _t("before"), _t("after"), _t("change"), _t("share")]
    )
    return table(headers, rows, cls="channels")


def source_label(raw: str) -> str:
    source, _, medium = raw.partition(" / ")
    if source == "(direct)":
        return _t("direct")
    if source in ("(not set)", "", "(none)"):
        return _t("undefined")
    name = {"google": "Google", "yandex": _t("yandex")}.get(source.casefold(), source)
    kind = {
        "organic": _t("organic"),
        "referral": _t("referral"),
        "cpc": _t("paid"),
        "email": _t("email"),
        "(none)": _t("no_medium"),
        "(not set)": _t("undefined_medium"),
    }.get(medium, medium)
    return f"{name} ({kind})" if kind else name


def traffic(growth: dict, previous: dict) -> str:
    data = growth.get("traffic") or {}
    body = kpis(growth, ("organic_visits",), compact=True)
    body += section(
        _t("channel_heading"), channel_bars(growth, previous) + _t("traffic_note")
    )
    now = channel_totals(growth)
    body += section(_t("app"), _t("app_note").format(number(now.get("app")).html))
    body += section(
        _t("sources"),
        table(
            [_t("source"), _t("channel"), _t("visits")],
            [
                [
                    label(source_label(r["source_medium"]), title=r["source_medium"]),
                    channel_label(r["channel"]),
                    r["visits"],
                ]
                for r in data.get("sources") or []
            ],
        ),
    )
    # Same normalized path is one row even when it appeared under several channels.
    landings = {}
    for row in data.get("landing_pages") or []:
        landings[row["page"]] = landings.get(row["page"], 0) + row["visits"]
    body += section(
        _t("landing_pages"),
        table(
            [_t("page"), _t("visits")],
            [
                [label(path), n]
                for path, n in sorted(landings.items(), key=lambda r: -r[1])
            ],
        )
        + _t("landing_note"),
    )
    noise = (data.get("excluded_noise") or {}).get("visits") or 0
    if noise:
        body += _t("noise").format(number(noise).html)
    return body


def money(growth: dict) -> str:
    outcomes = growth.get("outcomes") or {}
    body = kpis(
        growth, ("registrations", "payments", "revenue_minor", "visit_to_signup")
    )
    body += _t("conversion_warning")
    days = int(growth.get("window", {}).get("days") or 0)
    start = date.fromisoformat(growth["window"]["start"])
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    daily = {}
    rows = []
    server_rows = outcomes.get("server") or []
    show_channel = not server_rows or any(row["channel"] != "unassigned" for row in server_rows)
    for row in server_rows:
        key = (row["date"], row["outcome_id"])
        daily[key] = daily.get(key, 0) + row["count"]
        amount = (
            number(None)
            if row.get("value_minor") is None
            else money_value(row["value_minor"], row.get("currency"))
        )
        rows.append(
            [
                Cell(
                    f'<time datetime="{esc(row["date"])}">{human_date(row["date"])}</time>',
                    row["date"],
                ),
                _t("registrations")
                if row["outcome_id"] == "registration"
                else _t("payments") if row["outcome_id"] == "paid_purchase"
                else _t("other_outcome").format(row["outcome_id"]),
                _t("all_channels")
                if row["channel"] == "__all__"
                else channel_label(row["channel"]),
                row["count"],
                amount,
            ]
        )
    headers = [_t("date"), _t("outcome"), _t("channel"), _t("count"), _t("amount")]
    if not show_channel:
        headers.pop(2)
        for row in rows:
            row.pop(2)
    charts = []
    source_sets = _kpi_source_sets(growth)
    for metric, outcome, title in [
        ("registrations", "registration", _t("registrations_daily")),
        ("payments", "paid_purchase", _t("payments_daily")),
    ]:
        covered = (
            set.intersection(
                *[
                    set(growth.get("sources", {}).get(s, {}).get("covered_days") or [])
                    for s in source_sets[metric]
                ]
            )
            if source_sets[metric]
            else set()
        )
        points = [
            (d, daily.get((d, outcome), 0 if d in covered else None)) for d in dates
        ]
        charts.append(section(title, chart(points, title=title), "wide-chart"))
    body += '<div class="columns">' + "".join(charts) + "</div>"
    body += section(
        _t("server_daily"),
        table(headers, rows)
        + _t("payments_warning"),
    )
    split = (growth.get("derived") or {}).get("signup_channels") or {}
    sample = sum(n for ch, n in split.items() if ch != "sample_of")
    body += section(
        _t("signup_channels"),
        _t("signup_sample").format(
            number(sample).html, number(split.get("sample_of")).html
        )
        + table(
            [_t("channel"), _t("signup_events")],
            [[channel_label(ch), n] for ch, n in split.items() if ch != "sample_of"],
        ),
    )
    return body


def freshness_state(growth: dict, name: str, generated_at: str) -> tuple[bool, str]:
    source = growth.get("sources", {}).get(name) or {}
    through = _dash_through(growth, name)
    days = growth.get("window", {}).get("days") or 0
    covered = source.get("days_covered") or 0
    age = (
        (
            datetime.fromisoformat(generated_at.replace("Z", "+00:00")).date()
            - date.fromisoformat(through)
        ).days
        if through
        else None
    )
    lag = bool(through and through < growth.get("window", {}).get("end", ""))
    warn = (
        source.get("state") != "live" or covered < days or lag or age is None or age > 4
    )
    state = _t("live")
    if not through:
        state = _t("waiting")
    elif source.get("state") == "stale" or (age is not None and age > 4):
        state = _t("stale")
    elif covered < days or source.get("state") != "live":
        state = _t("partial")
    elif lag:
        state = _t("delayed")
    return warn, state


def status(growth: dict, generated_at: str) -> str:
    rows = []
    for name, source in sorted((growth.get("sources") or {}).items()):
        if name not in _dashboard_sources(growth):
            continue
        warn, state = freshness_state(growth, name, generated_at)
        through = _dash_through(growth, name)
        covered, days = (
            source.get("days_covered") or 0,
            growth.get("window", {}).get("days") or 0,
        )
        detail = _t("coverage_detail").format(covered, days) if covered < days else ""
        state_cell = Cell(
            _t("source_state").format(
                "warn" if warn else "positive",
                state,
                esc(human_date(through)),
                esc(detail),
            ),
            state,
        )
        rows.append(
            [
                _source_name(name),
                SOURCE_PURPOSE.get(name, _t("outcome_data")),
                state_cell,
                _t("yes") if source.get("required") else _t("no"),
            ]
        )
    return section(
        _t("data"),
        table(
            [_t("source"), _t("source_purpose"), _t("page_status"), _t("alert")],
            rows,
            cls="status-table",
        ),
    ) + section(_t("site_checks"), _t("site_checks_note"))


def changes(growth: dict) -> str:
    items = (growth.get("derived") or {}).get("what_changed") or []
    if not items:
        return ""
    rows = []
    for item in items[:6]:
        subject = (
            channel_label(item["subject"])
            if item.get("kind") == "channel"
            else item.get("subject")
        )
        rows.append(
            f'<li>{esc(subject)} <span class="neutral">·</span> {transition(item.get("from"), item.get("to")).html}</li>'
        )
    return section(
        _t("what_changed"),
        '<ul class="changes">' + "".join(rows) + "</ul>",
    )


@lru_cache(maxsize=1)
def assets() -> tuple[str, str]:
    font = base64.b64encode((ASSETS / "Manrope.woff2").read_bytes()).decode("ascii")
    css = f'@font-face{{font-family:Manrope;src:url(data:font/woff2;base64,{font}) format("woff2");font-weight:200 800;font-display:swap;}}'
    messages = json.dumps({key: _t(key) for key in ("collapse", "expand", "shown")})
    return css + (ASSETS / "growth.css").read_text(), (
        ASSETS / "growth.js"
    ).read_text().replace("__PANEL_MESSAGES__", messages)


def render_dashboard_pages(
    growth: dict, *, title: str, generated_at: str, panel: PanelConfig | None = None
) -> dict[str, str]:
    theme = panel or PanelConfig(title=title)
    css, script = assets()
    theme_css = (
        ":root{"
        + "".join(
            f"--{name}:{getattr(theme, name)};"
            for name in ("accent", "background", "surface", "text")
        )
        + "}"
    )
    dashboard = growth.get("dashboard") or {}
    history, clusters = (
        dashboard.get("history") or [],
        dashboard.get("keyword_clusters") or {},
    )
    wide = dashboard.get("28d") or {}
    windows = [("", growth, dashboard.get("previous") or {})]
    if wide:
        windows.append(("28/", wide["current"], wide.get("previous") or {}))
    measurements = growth.get("serp") or []
    pages = list(PAGES)
    if measurements:
        pages.insert(2, ("competitors.html", _t('page_competitors')))
    result = {}
    for prefix, window, previous in windows:
        window = {**window, "serp": measurements}
        content = {
            "index.html": kpis(
                window, ("organic_visits", "registrations", "payments", "revenue_minor")
            )
            + _t("summary_note")
            + changes(window)
            + section(_t("channel_heading"), channel_bars(window)),
            "positions.html": positions(window, previous, history, clusters),
            "demand.html": demand(window, previous, clusters),
            "traffic.html": traffic(window, previous),
            "money.html": money(window),
            "status.html": status(window, generated_at),
        }
        if measurements:
            from seo_observer.growth_serp import competitors
            content["competitors.html"] = competitors(measurements)
        chips = []
        for name in sorted(
            _dashboard_sources(window) & set(window.get("sources") or {})
        ):
            warn, state = freshness_state(window, name, generated_at)
            through = _dash_through(window, name)
            short_date = (
                date.fromisoformat(through).strftime("%d.%m") if through else "—"
            )
            chips.append(
                _t("freshness_chip").format(
                    "warn" if warn else "",
                    esc(state),
                    esc(through or ""),
                    esc(_source_name(name)),
                    short_date,
                )
            )
        freshness = '<div class="chips">' + "".join(chips) + "</div>"
        for filename, page_name in pages:
            nav = (
                _t("nav")
                + "".join(
                    f'<a href="{name}"'
                    + (' aria-current="page"' if name == filename else "")
                    + f">{label}</a>"
                    for name, label in pages
                )
                + "</nav>"
            )
            periods = (
                f'<a href="{"../" if prefix else ""}{filename}"'
                + (' aria-current="page"' if not prefix else "")
                + _t("week_link")
            )
            if wide:
                periods += (
                    f'<a href="{"" if prefix else "28/"}{filename}"'
                    + (' aria-current="page"' if prefix else "")
                    + _t("month_link")
                )
            serp_page = filename == "competitors.html"
            display_window = (
                {"start": min(m["check_date"] for m in measurements),
                 "end": max(m["check_date"] for m in measurements)}
                if serp_page else window.get("window", {})
            )
            result[prefix + filename] = _t("document").format(
                esc(theme.title),
                page_name,
                theme_css,
                css,
                esc(theme.title),
                nav,
                _t("serp_all_measurements") if serp_page else periods,
                page_name,
                esc(display_window.get("start", "")),
                esc(display_window.get("end", "")),
                esc(date_range(display_window)),
                esc(_t('serp_comparison') if filename == "competitors.html" else date_range(previous.get("window", {}))),
                esc(generated_at),
                esc(moscow_stamp(generated_at)),
                freshness,
                content[filename],
                script,
                "" if serp_page else _t("comparison_suffix").format(esc(date_range(previous.get("window", {})))),
            )
    return result
