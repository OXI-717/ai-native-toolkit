"""Presentation of measured SERPs; console averages never enter SERP metrics."""

from collections import defaultdict
from hashlib import sha256
from html import escape

from seo_observer.growth_locale import text as _t
from seo_observer.serp_storage import _domain_match

ENGINES = {"google": "Google", "yandex": _t("yandex")}
DEVICES = {
    "desktop": _t("serp_desktop"),
    "mobile": _t("serp_mobile"),
    "__all__": _t("serp_any_device"),
}
COLORS = ("#07ff81", "#69a8ff", "#e0b4ff", "#ffcf65", "#ff8f9d", "#52d7de")


def esc(value):
    return escape(str(value), quote=True)


def grouped(measurements):
    groups = defaultdict(list)
    for m in measurements:
        groups[(m["search_engine"], m["device"], m["region"], m["market"])].append(m)
    return {
        key: sorted(ms, key=lambda m: (m["check_date"], not m["historical"]))
        for key, ms in sorted(groups.items())
    }


def comparable(a, b):
    return (
        not a["historical"]
        and not b["historical"]
        and a["source"] == b["source"]
        and a["market"] == b["market"]
        and a["population_hash"] == b["population_hash"]
    )


def top_metrics(m):
    return sorted(
        (r for r in m["metrics"] if r["cluster"] == "__all__"),
        key=lambda r: (not r["owned"], -r["sov"], r["domain"]),
    )


def rank_for(m, query, domain=None):
    catalog = m["catalog"]
    patterns = next(
        (d["patterns"] for d in catalog if d["domain"] == domain),
        catalog[0]["patterns"],
    )
    q = " ".join(query.casefold().split())
    if domain is None and q in m.get("owned_positions", {}):
        return m["owned_positions"][q]
    if q not in m["snapshots"]:
        return None
    return min(
        (
            r["position"]
            for r in m["snapshots"][q]
            if _domain_match(r["domain"], patterns)
        ),
        default="outside_top10",
    )


def rank_label(rank):
    return (
        "—"
        if rank is None
        else _t("serp_outside")
        if rank == "outside_top10" or rank > 10
        else str(rank)
    )


def position_cell(measurements, query, source):
    from seo_observer.growth_dashboard import Cell

    engine = {"google_search_console": "google", "yandex_webmaster": "yandex"}.get(
        source
    )
    values = []
    for (eng, device, region, market), ms in grouped(measurements).items():
        m = ms[-1]
        if eng != engine or not m["source"].startswith("topvisor_"):
            continue
        rank = rank_for(m, query)
        values.append(
            f'<span class="source-detail" title="{esc(m["check_date"])} · {esc(region)}">'
            f"{esc(market)} · {esc(DEVICES.get(device, device))}: {rank_label(rank)}</span>"
        )
    return Cell("".join(values) or "—", "")


def _filters(groups):
    initial = dict(zip(("engine", "device", "region", "market"), next(iter(groups))))
    engines = sorted({k[0] for k in groups})
    devices = sorted({k[1] for k in groups})
    regions = sorted({k[2] for k in groups})

    def select(name, label, values, labels):
        return (
            f"<label>{label}<select data-serp-{name}>"
            + "".join(
                f'<option value="{esc(v)}"{" selected" if v == initial[name] else ""}>{esc(labels.get(v, v))}</option>'
                for v in values
            )
            + "</select></label>"
        )

    return (
        '<div class="filters">'
        + select("engine", _t("engine"), engines, ENGINES)
        + select("device", _t("serp_device"), devices, DEVICES)
        + select("region", _t("serp_region"), regions, {})
        + select("market", _t("serp_market"), sorted({k[3] for k in groups}), {})
        + "</div>"
    )


def _open(key, first):
    engine, device, region, market = key
    return (
        f'<div data-serp-panel data-serp-market="{esc(market)}" data-serp-engine="{esc(engine)}" data-serp-device="{esc(device)}" data-serp-region="{esc(region)}"'
        + ("" if first else " hidden")
        + ">"
    )


def _caption(m):
    return (
        _t("serp_caption").format(
            esc(m["check_date"]),
            esc(ENGINES.get(m["search_engine"], m["search_engine"])),
            esc(DEVICES.get(m["device"], m["device"])),
            esc(m["region"]),
            len(m["keywords"]),
            esc(
                "TopVisor"
                if m["source"].startswith("topvisor_")
                else "DataForSEO"
                if m["source"].startswith("dataforseo_")
                else ENGINES.get(m["search_engine"], m["source"])
            ),
            sum(bool(v) for v in m["snapshots"].values()),
            len(m["keywords"]),
        )
        + (
            _t("serp_annotated_warning")
            if any(" #" in q for q in m["keywords"])
            else ""
        )
        + (_t("serp_historical_warning") if m["historical"] else "")
        + "</p>"
    )


def positions(measurements, growth):
    from seo_observer.growth_dashboard import label, number, section, table

    groups = grouped(measurements)
    if not groups:
        return ""
    body = '<div class="serp-view">' + _filters(groups)
    for index, (key, ms) in enumerate(groups.items()):
        m = ms[-1]
        body += _open(key, index == 0) + _caption(m)
        ranks = {q: rank_for(m, q) for q in m["keywords"]}
        buckets = [
            (
                _t("serp_top3"),
                sum(isinstance(r, int) and r <= 3 for r in ranks.values()),
            ),
            ("4–10", sum(isinstance(r, int) and 4 <= r <= 10 for r in ranks.values())),
            (
                _t("serp_outside"),
                sum(
                    r == "outside_top10" or isinstance(r, int) and r > 10
                    for r in ranks.values()
                ),
            ),
        ]
        stack = "".join(
            f'<span data-tone="{i}" style="flex:{n}" title="{esc(name)}: {n}">{n}</span>'
            for i, (name, n) in enumerate(buckets)
            if n
        )
        body += (
            '<div class="stack serp-stack">'
            + stack
            + '</div><div class="legend">'
            + "".join(f"<span>{name} · {n}</span>" for name, n in buckets)
            + "</div>"
        )
        source = {"google": "google_search_console", "yandex": "yandex_webmaster"}.get(
            m["search_engine"]
        )
        cabinet = {
            " ".join(r["query"].casefold().split()): r.get("position")
            for r in growth.get("search", {}).get(source, {}).get("queries", [])
        }
        body += table(
            [
                _t("query"),
                _t("cluster"),
                _t("serp_console_position"),
                _t("serp_position")
                if m["source"].startswith("topvisor_")
                else _t("serp_historical_position"),
            ],
            [
                [
                    label(q),
                    label(m["keywords"][q]["cluster"]),
                    number(cabinet.get(q), 1),
                    rank_label(rank),
                ]
                for q, rank in sorted(
                    ranks.items(),
                    key=lambda i: (i[1] if isinstance(i[1], int) else 999, i[0]),
                )
            ],
            limit=15,
        )
        body += "</div>"
    return section(
        _t("serp_distribution_title"),
        _t("serp_distribution_note") + body + _t("serp_empty_filter"),
    )


def trend(ms, domains):
    # Fixed 0–100% scale across all domains. Historical points stand alone;
    # lines connect only equal measured populations from the same provider.
    lines, legend = [], []
    for n, domain in enumerate(domains):
        points = []
        color = COLORS[n]
        for i, m in enumerate(ms):
            metric = next((r for r in top_metrics(m) if r["domain"] == domain), None)
            if metric is None:
                points.append(None)
                continue
            x, y = 30 + 600 * i / max(1, len(ms) - 1), 180 - 150 * metric["sov"]
            if i and points[-1] is not None and comparable(ms[i - 1], m):
                px, py = points[-1]
                lines.append(
                    f'<line x1="{px:.2f}" y1="{py:.2f}" x2="{x:.2f}" y2="{y:.2f}" stroke="{color}" stroke-width="2"/>'
                )
            points.append((x, y))
            lines.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}"><title>{esc(domain)} · {esc(m["check_date"])} · {metric["sov"] * 100:.1f}%</title></circle>'
            )
        legend.append(f'<span style="color:{color}">{esc(domain)}</span>')
    labels = "".join(
        f'<text x="{30 + 600 * i / max(1, len(ms) - 1):.1f}" y="207" text-anchor="middle">{esc(m["check_date"])}</text>'
        for i, m in enumerate(ms)
    )
    return (
        _t("serp_trend_svg")
        + "".join(lines)
        + labels
        + '</svg><div class="legend">'
        + "".join(legend)
        + _t("serp_trend_note")
    )


def competitors(measurements):
    from seo_observer.growth_dashboard import Cell, label, number, section, table

    groups = grouped(measurements)
    body = '<div class="serp-view">' + _filters(groups)
    for index, (key, ms) in enumerate(groups.items()):
        m = ms[-1]
        current = top_metrics(m)
        previous = next(
            (prior for prior in reversed(ms[:-1]) if comparable(prior, m)), None
        )
        before = (
            {r["domain"]: r for r in top_metrics(previous)}
            if previous and comparable(previous, m)
            else {}
        )
        body += _open(key, index == 0) + _caption(m)
        ids = {
            r["domain"]: "domain-"
            + sha256(str((key, r["domain"])).encode()).hexdigest()[:16]
            for r in current
        }
        rows = []
        for row in current:
            name = esc(row["name"]) + (_t("serp_owned_badge") if row["owned"] else "")
            domain = row["domain"]
            diff = (
                (row["sov"] - before[domain]["sov"]) * 100 if domain in before else None
            )
            rows.append(
                [
                    Cell(
                        f'<a href="#{ids[domain]}" data-serp-domain>{name}<span class="source-detail">{esc(domain)}</span></a>',
                        domain,
                    ),
                    number(row["sov"] * 100, 1),
                    number(row["visibility"] * 100, 1),
                    row["top10"],
                    Cell(
                        _t("serp_delta_points").format(diff)
                        if diff is not None
                        else _t("serp_incomparable_delta"),
                        diff if diff is not None else "",
                    ),
                ]
            )
        body += section(
            _t("serp_visibility_title"),
            _t("serp_visibility_formula")
            + table(
                [
                    _t("serp_domain"),
                    "SOV, %",
                    _t("serp_visibility_pct"),
                    _t("serp_top10_keywords"),
                    _t("serp_sov_change"),
                ],
                rows,
                limit=30,
                attrs=[{"owned": str(r["owned"]).lower()} for r in current],
            ),
        )
        owned = [r["domain"] for r in current if r["owned"]]
        leaders = owned + [r["domain"] for r in current if not r["owned"]][:5]
        body += section(_t("serp_trend_title"), trend(ms, leaders))
        domains = [r["domain"] for r in current]
        clusters = sorted({r["cluster"] for r in m["metrics"]} - {"__all__"})
        heat = {(r["cluster"], r["domain"]): r for r in m["metrics"]}
        heat_rows = []
        for cluster in clusters:
            cells = [label(cluster)]
            for domain in domains:
                metric = heat[(cluster, domain)]
                share = (
                    metric["top10"] / metric["keywords"] if metric["keywords"] else 0.0
                )
                cells.append(
                    Cell(
                        _t("serp_heat_cell").format(
                            share * 0.6,
                            metric["top10"],
                            metric["keywords"],
                            share * 100,
                        ),
                        share,
                    )
                )
            heat_rows.append(cells)
        body += section(
            _t("serp_heat_title"),
            table([_t("cluster")] + domains, heat_rows, limit=50, cls="serp-heat"),
        )
        details = []
        for domain in domains:
            matches = []
            catalog = next(d for d in m["catalog"] if d["domain"] == domain)
            for q, rows in m["snapshots"].items():
                for row in rows:
                    if _domain_match(row["domain"], catalog["patterns"]):
                        matches.append(
                            (
                                row["position"],
                                q,
                                row["url"] if row.get("url_known", True) else "",
                            )
                        )
            matches.sort()
            url_rows = defaultdict(list)
            for rank, query, url in matches:
                if url:
                    url_rows[url].append((rank, query))
            urls = [
                [label(url), len({q for _, q in ranks}), min(r for r, _ in ranks)]
                for url, ranks in sorted(
                    url_rows.items(), key=lambda i: (-len(i[1]), i[0])
                )
            ]
            details.append(
                _t("serp_domain_details").format(
                    ids[domain], esc(catalog["name"]), esc(domain)
                )
                + (
                    _t("serp_best_urls_heading")
                    + table(
                        ["URL", _t("serp_top10_keywords"), _t("serp_best_position")],
                        urls,
                        limit=10,
                    )
                    if urls
                    else ""
                )
                + _t("serp_best_keywords_heading")
                + table(
                    [_t("serp_keyword"), _t("position"), "URL"],
                    [
                        [label(q), rank, label(url or _t("serp_url_unknown"))]
                        for rank, q, url in matches
                    ],
                    limit=15,
                )
                + "</details>"
            )
        body += section(_t("serp_details_title"), "".join(details)) + "</div>"
    return body + _t("serp_empty_filter")
