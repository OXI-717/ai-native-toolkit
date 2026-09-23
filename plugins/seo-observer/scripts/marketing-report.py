#!/usr/bin/env python3
"""Marketing SEO report: clusters and gaps.

Difference from `competitors audit`: that one answers "what is our current
share of voice", while this one answers "where to invest first". That is why
two dimensions appear here that the audit does not have:

* **cluster** — a whole topic, weighted by demand rather than phrase count;
* **gap** — a high-demand query where we are absent while a direct competitor
  sits in the top-3.

Usage:

    python3 scripts/marketing-report.py \
        --config <path/to/project.toml> --keyword-set <id> \
        --market ru --output <report.md> [--limit 60]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))

from seo_observer.config import load_project_config  # noqa: E402
from seo_observer.keyword_clusters import parse_keyword_file  # noqa: E402
from seo_observer.serp import CompetitorConfig, Competitor, classify_domain  # noqa: E402
from seo_observer.dataforseo import (  # noqa: E402
    DATAFORSEO_DEFAULT_BASE_URL,
    DataForSEOAdapter,
    DataForSEOSource,
)
from seo_observer.yandex_search import (  # noqa: E402
    DEFAULT_BASE_URL,
    YandexSerpProviderAdapter,
)

TOP_LEADER = 3
VISIBLE_DEPTH = 20
# Classes worth competing with: reference/marketplace occupy the SERP, but a
# product page cannot take their slot.
CONTESTABLE = {"direct", "indirect"}


class HttpTransport:
    """Transport following the plugin contract: relative endpoint, base URL held inside."""

    def __init__(self, base_url: str, timeout: int = 40) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def post_json(self, endpoint: str, *, json: dict, headers: dict) -> dict:
        request = urllib.request.Request(
            f"{self._base_url}{endpoint}",
            data=json_dumps(json),
            headers={**headers, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json_loads(response.read())


def json_dumps(payload: dict) -> bytes:
    import json as _json

    return _json.dumps(payload).encode("utf-8")


def json_loads(raw: bytes) -> dict:
    import json as _json

    return _json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--keyword-set", required=True)
    parser.add_argument("--market", default="ru")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 = the whole set")
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--pause", type=float, default=0.25)
    args = parser.parse_args()

    config = load_project_config(Path(args.config).expanduser())
    market = next((m for m in config.markets if m.id == args.market), None)
    if market is None:
        print(f"market {args.market!r} not found in the config", file=sys.stderr)
        return 2
    token = os.environ.get(market.credential_env)
    if not token:
        print(f"env variable {market.credential_env} is not set", file=sys.stderr)
        return 2

    selected = next((k for k in config.keyword_sets if k.id == args.keyword_set), None)
    if selected is None:
        print(f"keyword set {args.keyword_set!r} not found", file=sys.stderr)
        return 2

    keywords = parse_keyword_file(selected.path)
    keywords.sort(key=lambda k: -(k.yws or 0))
    if args.limit:
        keywords = keywords[: args.limit]

    competitor_config = _competitor_config(config, market.id)
    class_by_id = {c.id: c.competitor_class for c in config.competitors.items}
    try:
        adapter = _build_adapter(config, market, token)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    region = market.regions[0] if market.regions else None

    is_google = market.search_engine == "google"
    rows, failures = _collect(
        adapter,
        keywords,
        region,
        args.depth,
        args.pause,
        # DataForSEO rejects location_name when the region is given as a numeric
        # location_code (40501 Invalid Field). Our regions are codes, so None.
        location_name=None,
        language=market.language if is_google else None,
        device="desktop" if is_google else "__all__",
    )
    report = _render(
        config=config,
        market=market,
        keyword_set_id=args.keyword_set,
        rows=rows,
        failures=failures,
        competitor_config=competitor_config,
        class_by_id=class_by_id,
    )
    out = Path(args.output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"report: {out}  (queries {len(rows)}, failures {failures})")
    return 0


def _build_adapter(config, market, token: str):
    """The adapter is chosen by the market's engine.

    Without this, `--market en` would silently build the Yandex adapter with
    DataForSEO credentials: every request would fail with 401 and the report
    would just look empty instead of showing an explicit error.
    """
    if market.search_engine == "yandex":
        return YandexSerpProviderAdapter(
            HttpTransport(DEFAULT_BASE_URL), token=token, language_code=market.language
        )
    if market.search_engine == "google":
        provider = config.providers.get("dataforseo")
        if provider is None or not provider.enabled:
            raise ValueError(
                f"market {market.id!r} requires an enabled dataforseo provider in the config"
            )
        endpoint = provider.endpoint or DATAFORSEO_DEFAULT_BASE_URL
        return DataForSEOAdapter(
            DataForSEOSource(
                credential_env=market.credential_env,
                endpoint=endpoint,
                provider_mode="live",
                allow_paid=True,
                per_run_budget_usd=provider.per_run_budget_usd,
                monthly_budget_usd=provider.monthly_budget_usd,
            ),
            HttpTransport(endpoint),
            env=dict(os.environ),
        )
    raise ValueError(
        f"engine {market.search_engine!r} is not supported by the marketing report"
    )


def _competitor_config(config, market_id: str) -> CompetitorConfig:
    return CompetitorConfig(
        owned_domains=tuple(config.competitors.owned_domains),
        competitors=tuple(
            Competitor(
                id=item.id,
                name=item.name,
                domain_patterns=tuple(item.domain_patterns),
                aliases=tuple(item.aliases),
            )
            for item in config.competitors.items
        ),
    )


def _collect(adapter, keywords, region, depth, pause, *, location_name=None, language=None, device="__all__"):
    rows, failures = [], 0
    for index, item in enumerate(keywords, start=1):
        result = adapter.fetch_organic_serp(
            item.keyword, region, location_name, language, device, depth
        )
        if result.get("errors"):
            failures += 1
            continue
        rows.append((item, result.get("rows") or []))
        if index % 20 == 0:
            print(f"  …{index}/{len(keywords)}", file=sys.stderr)
        time.sleep(pause)
    return rows, failures


def _render(*, config, market, keyword_set_id, rows, failures, competitor_config, class_by_id):
    project = getattr(config.project, "namespace", None) or str(config.project)
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

    per_keyword = []
    cluster_rows = defaultdict(list)
    competitor_hits = defaultdict(lambda: {"n": 0, "best": 999, "cls": "unknown"})
    for item, serp in rows:
        our_rank = None
        leaders = []
        for row in serp:
            # DataForSEO also returns rows without a url (some snippet types),
            # so not everything that arrives can be classified.
            url = row.get("url")
            rank_raw = row.get("rank_absolute")
            if not url or rank_raw is None:
                continue
            verdict = classify_domain(url, competitor_config)
            rank = int(rank_raw)
            if verdict["classification"] == "owned":
                our_rank = our_rank or rank
                continue
            cid = verdict.get("competitor_id")
            if cid and cid != "__unclassified__":
                cls = class_by_id.get(cid, "unknown")
                hit = competitor_hits[cid]
                hit["n"] += 1
                hit["best"] = min(hit["best"], rank)
                hit["cls"] = cls
                if rank <= TOP_LEADER and cls in CONTESTABLE:
                    leaders.append((rank, cid))
        entry = {
            "keyword": item.keyword,
            "cluster": item.cluster or "no cluster",
            "yws": item.yws or 0,
            "rank": our_rank,
            "leaders": sorted(leaders)[:2],
        }
        per_keyword.append(entry)
        cluster_rows[entry["cluster"]].append(entry)

    demand_known = any(e["yws"] for e in per_keyword)
    total_yws = sum(e["yws"] for e in per_keyword) or 1
    visible = [e for e in per_keyword if e["rank"] and e["rank"] <= VISIBLE_DEPTH]
    top10 = [e for e in per_keyword if e["rank"] and e["rank"] <= 10]
    covered_yws = sum(e["yws"] for e in visible)

    lines = [
        f"# SEO report: {project}",
        "",
        f"Market: **{market.id.upper()}**, engine {market.search_engine}, region {market.regions[0] if market.regions else '—'}.",
        f"Keyword set: `{keyword_set_id}`. Date: {generated}.",
        "",
        "## In short",
        "",
        f"- Queries checked: **{len(per_keyword)}**"
        + (f" (failed to collect: {failures})" if failures else ""),
        f"- Visible in top-{VISIBLE_DEPTH}: **{len(visible)}** ({len(visible) * 100 // max(len(per_keyword), 1)}%)",
        f"- Of those in top-10: **{len(top10)}**",
    ]
    if demand_known:
        lines.append(
            f"- Demand we cover: **{covered_yws * 100 // total_yws}%** "
            f"({covered_yws} of {total_yws} Wordstat impressions)"
        )
    else:
        lines.append(
            "- Query demand is **not marked up**: the keyword set has no "
            "Wordstat volumes, so topics cannot be prioritized by volume right "
            "now (see the \"What is missing\" section)."
        )
    lines.append("")

    if not visible:
        lines += [
            "> The site was not found for any checked query within the top-"
            f"{VISIBLE_DEPTH}. This means organic search currently brings no "
            "traffic, and any growth figure will be measured from zero.",
            "",
        ]

    if demand_known:
        lines += ["## Topics: where we stand and where the demand is", "", _cluster_table(cluster_rows), ""]
    lines += ["## Where to invest first", "", _gap_section(per_keyword), ""]
    lines += ["## Who occupies the SERP", "", _competitor_table(competitor_hits), ""]
    lines += _method_section(market, len(per_keyword))
    if not demand_known:
        lines += _missing_data_section()
    return "\n".join(lines) + "\n"


def _missing_data_section() -> list[str]:
    return [
        "",
        "## What is missing for a complete report",
        "",
        "The keyword set is not marked up: it has neither clusters nor Wordstat "
        "volumes. Because of that the report can show **where** we lose, but "
        "cannot answer **which of it is worth the most** — all queries look "
        "equal even though they differ in demand by hundreds of times.",
        "",
        "Fixing this takes one one-off step: collect volumes for the existing "
        "queries and group them by topic, then mark up the keyword file — a "
        "cluster via a `# cluster: <topic>` line, a volume via a `# yws=<number>` "
        "comment. After that the report computes priorities itself.",
        "",
        "Once the markup exists, the report immediately shows the work queue "
        "ordered by uncovered demand.",
    ]


def _cluster_table(cluster_rows) -> str:
    ordered = sorted(
        cluster_rows.items(), key=lambda kv: -sum(e["yws"] for e in kv[1])
    )
    out = [
        "Clusters are sorted by demand. \"Our best\" is the minimal rank within the topic.",
        "",
        "| Topic | Demand | Phrases | Our best | Phrases visible |",
        "|---|---:|---:|---|---:|",
    ]
    for name, entries in ordered[:15]:
        demand = sum(e["yws"] for e in entries)
        ranks = [e["rank"] for e in entries if e["rank"]]
        best = min(ranks) if ranks else None
        seen = len([e for e in entries if e["rank"] and e["rank"] <= VISIBLE_DEPTH])
        out.append(
            f"| {name} | {demand} | {len(entries)} | "
            f"{best if best else '**not in top-20**'} | {seen}/{len(entries)} |"
        )
    return "\n".join(out)


def _gap_section(per_keyword) -> str:
    gaps = [
        e
        for e in per_keyword
        if e["leaders"] and (e["rank"] is None or e["rank"] > VISIBLE_DEPTH)
    ]
    gaps.sort(key=lambda e: -e["yws"])
    if not gaps:
        return "No gaps found: no checked query has a direct competitor holding a top-3 slot."
    out = [
        "Queries with demand where we are absent from the top-20 while a direct competitor sits in the top-3.",
        "This is the work queue — top to bottom.",
        "",
        "| Query | Demand | Top-3 holders |",
        "|---|---:|---|",
    ]
    for entry in gaps[:20]:
        who = ", ".join(f"{cid} ({rank})" for rank, cid in entry["leaders"])
        out.append(f"| {entry['keyword']} | {entry['yws']} | {who} |")
    lost = sum(e["yws"] for e in gaps)
    out += [
        "",
        f"Total uncovered demand for these queries is **{lost}** impressions per month.",
    ]
    return "\n".join(out)


def _competitor_table(competitor_hits) -> str:
    if not competitor_hits:
        return "None of the configured competitors appeared in the SERP."
    contestable = {k: v for k, v in competitor_hits.items() if v["cls"] in CONTESTABLE}
    other = {k: v for k, v in competitor_hits.items() if v["cls"] not in CONTESTABLE}
    out = ["**Worth competing with** — direct and indirect competitors:", "",
           "| Competitor | Queries | Best rank |", "|---|---:|---:|"]
    for cid, hit in sorted(contestable.items(), key=lambda kv: -kv[1]["n"])[:10]:
        out.append(f"| {cid} | {hit['n']} | {hit['best']} |")
    if other:
        out += [
            "",
            "**Occupy the SERP, but competing for their slots is pointless** — reviews, marketplaces, "
            "primary sources. The goal is to be featured there, not to displace them:",
            "",
            "| Site | Queries | Best rank | Type |",
            "|---|---:|---:|---|",
        ]
        for cid, hit in sorted(other.items(), key=lambda kv: -kv[1]["n"])[:10]:
            out.append(f"| {cid} | {hit['n']} | {hit['best']} | {hit['cls']} |")
    return "\n".join(out)


def _method_section(market, checked: int) -> list[str]:
    return [
        "## How this is measured",
        "",
        f"A live {market.search_engine} SERP fetch per query "
        f"(region {market.regions[0] if market.regions else '—'}), {checked} queries, "
        "depth 20 positions.",
        "",
        "A note on sources: **Yandex Webmaster shows the average position over "
        "impressions that actually happened** — personalized, regional, long-tail "
        "reformulations — and therefore systematically looks more optimistic than "
        "the raw SERP. The figures above are taken from the SERP itself and are "
        "suitable for planning; Webmaster figures are suitable for tracking your "
        "own dynamics.",
        "",
        "\"Demand\" is the base Wordstat volume from the project's semantic core: "
        "how many times per month the query is entered into Yandex.",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
