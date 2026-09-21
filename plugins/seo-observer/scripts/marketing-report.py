#!/usr/bin/env python3
"""Маркетинговый SEO-отчёт: кластеры и разрывы, на русском.

Отличие от `competitors audit`: тот отвечает на вопрос «какая сейчас доля
видимости», а этот — на вопрос «куда вкладываться в первую очередь». Поэтому
здесь появляются два измерения, которых нет в аудите:

* **кластер** — тема целиком, взвешенная по спросу, а не по числу фраз;
* **разрыв** — запрос с высоким спросом, где нас нет, а прямой конкурент в топ-3.

Запуск:

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
# Классы, за которыми имеет смысл гнаться: reference/marketplace занимают выдачу,
# но отобрать у них место продуктовой страницей нельзя.
CONTESTABLE = {"direct", "indirect"}


class HttpTransport:
    """Транспорт по контракту плагина: относительный endpoint, базовый URL внутри."""

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
    parser.add_argument("--limit", type=int, default=0, help="0 = весь набор")
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--pause", type=float, default=0.25)
    args = parser.parse_args()

    config = load_project_config(Path(args.config).expanduser())
    market = next((m for m in config.markets if m.id == args.market), None)
    if market is None:
        print(f"рынок {args.market!r} не найден в конфиге", file=sys.stderr)
        return 2
    token = os.environ.get(market.credential_env)
    if not token:
        print(f"нет переменной {market.credential_env}", file=sys.stderr)
        return 2

    selected = next((k for k in config.keyword_sets if k.id == args.keyword_set), None)
    if selected is None:
        print(f"набор ключей {args.keyword_set!r} не найден", file=sys.stderr)
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
        # DataForSEO отвергает location_name, когда регион задан числовым
        # location_code (40501 Invalid Field). Наши регионы — коды, поэтому None.
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
    print(f"отчёт: {out}  (запросов {len(rows)}, ошибок {failures})")
    return 0


def _build_adapter(config, market, token: str):
    """Адаптер выбирается по движку рынка.

    Без этого `--market en` молча строил бы яндексовый адаптер с кредами
    DataForSEO: все запросы падали бы с 401, а отчёт выглядел бы просто пустым
    вместо явной ошибки.
    """
    if market.search_engine == "yandex":
        return YandexSerpProviderAdapter(
            HttpTransport(DEFAULT_BASE_URL), token=token, language_code=market.language
        )
    if market.search_engine == "google":
        provider = config.providers.get("dataforseo")
        if provider is None or not provider.enabled:
            raise ValueError(
                f"рынок {market.id!r} требует включённого провайдера dataforseo в конфиге"
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
        f"движок {market.search_engine!r} не поддерживается маркетинговым отчётом"
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
            # DataForSEO отдаёт и строки без url (часть типов сниппетов),
            # поэтому классифицировать можно не всё, что пришло.
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
            "cluster": item.cluster or "без кластера",
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
        f"# SEO-отчёт: {project}",
        "",
        f"Контур: **{market.id.upper()}**, поиск {market.search_engine}, регион {market.regions[0] if market.regions else '—'}.",
        f"Набор ключей: `{keyword_set_id}`. Дата: {generated}.",
        "",
        "## Коротко",
        "",
        f"- Проверено запросов: **{len(per_keyword)}**"
        + (f" (не удалось собрать: {failures})" if failures else ""),
        f"- Видны в топ-{VISIBLE_DEPTH}: **{len(visible)}** ({len(visible) * 100 // max(len(per_keyword), 1)}%)",
        f"- Из них в топ-10: **{len(top10)}**",
    ]
    if demand_known:
        lines.append(
            f"- Спрос, который мы охватываем: **{covered_yws * 100 // total_yws}%** "
            f"({covered_yws} из {total_yws} показов Wordstat)"
        )
    else:
        lines.append(
            "- Спрос по запросам **не размечен**: в наборе ключей нет частотностей "
            "Wordstat, поэтому приоритизировать темы по объёму сейчас нельзя "
            "(см. раздел «Чего не хватает»)."
        )
    lines.append("")

    if not visible:
        lines += [
            "> Сайт не найден ни по одному проверенному запросу в пределах топ-"
            f"{VISIBLE_DEPTH}. Это означает, что органический поиск сейчас трафика "
            "не приносит, и любая цифра роста будет считаться от нуля.",
            "",
        ]

    if demand_known:
        lines += ["## Темы: где мы и где спрос", "", _cluster_table(cluster_rows), ""]
    lines += ["## Куда вкладываться в первую очередь", "", _gap_section(per_keyword), ""]
    lines += ["## Кто занимает выдачу", "", _competitor_table(competitor_hits), ""]
    lines += _method_section(market, len(per_keyword))
    if not demand_known:
        lines += _missing_data_section()
    return "\n".join(lines) + "\n"


def _missing_data_section() -> list[str]:
    return [
        "",
        "## Чего не хватает для полноценного отчёта",
        "",
        "Набор ключей не размечен: нет ни кластеров, ни частотностей Wordstat. "
        "Из-за этого отчёт может показать, **где** мы проигрываем, но не может "
        "ответить, **что из этого дороже всего стоит** — все запросы выглядят "
        "равнозначными, хотя различаются по спросу в сотни раз.",
        "",
        "Чтобы это починить, нужен один разовый шаг: собрать частотности по "
        "имеющимся запросам и сгруппировать их по темам, после чего разметить файл "
        "ключей — кластер строкой `# cluster: <тема>`, частотность комментарием "
        "`# yws=<число>`. Дальше отчёт считает приоритеты сам.",
        "",
        "Когда разметка есть, отчёт сразу показывает очередь работ "
        "по убыванию неохваченного спроса.",
    ]


def _cluster_table(cluster_rows) -> str:
    ordered = sorted(
        cluster_rows.items(), key=lambda kv: -sum(e["yws"] for e in kv[1])
    )
    out = [
        "Кластеры отсортированы по спросу. «Наша лучшая» — минимальная позиция внутри темы.",
        "",
        "| Тема | Спрос | Фраз | Наша лучшая | Видно фраз |",
        "|---|---:|---:|---|---:|",
    ]
    for name, entries in ordered[:15]:
        demand = sum(e["yws"] for e in entries)
        ranks = [e["rank"] for e in entries if e["rank"]]
        best = min(ranks) if ranks else None
        seen = len([e for e in entries if e["rank"] and e["rank"] <= VISIBLE_DEPTH])
        out.append(
            f"| {name} | {demand} | {len(entries)} | "
            f"{best if best else '**нет в топ-20**'} | {seen}/{len(entries)} |"
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
        return "Разрывов не найдено: по проверенным запросам прямые конкуренты топ-3 не занимают."
    out = [
        "Запросы, где есть спрос, нас нет в топ-20, а прямой конкурент стоит в топ-3.",
        "Это и есть очередь работ — сверху вниз.",
        "",
        "| Запрос | Спрос | Кто занимает топ-3 |",
        "|---|---:|---|",
    ]
    for entry in gaps[:20]:
        who = ", ".join(f"{cid} ({rank})" for rank, cid in entry["leaders"])
        out.append(f"| {entry['keyword']} | {entry['yws']} | {who} |")
    lost = sum(e["yws"] for e in gaps)
    out += [
        "",
        f"Суммарный неохваченный спрос по этим запросам — **{lost}** показов в месяц.",
    ]
    return "\n".join(out)


def _competitor_table(competitor_hits) -> str:
    if not competitor_hits:
        return "Ни один из заведённых конкурентов в выдаче не встретился."
    contestable = {k: v for k, v in competitor_hits.items() if v["cls"] in CONTESTABLE}
    other = {k: v for k, v in competitor_hits.items() if v["cls"] not in CONTESTABLE}
    out = ["**За кого можно бороться** — прямые и косвенные конкуренты:", "",
           "| Конкурент | Запросов | Лучшая позиция |", "|---|---:|---:|"]
    for cid, hit in sorted(contestable.items(), key=lambda kv: -kv[1]["n"])[:10]:
        out.append(f"| {cid} | {hit['n']} | {hit['best']} |")
    if other:
        out += [
            "",
            "**Занимают выдачу, но бороться за их места бессмысленно** — обзоры, витрины, "
            "первоисточники. Сюда нужно попадать, а не вытеснять:",
            "",
            "| Площадка | Запросов | Лучшая позиция | Тип |",
            "|---|---:|---:|---|",
        ]
        for cid, hit in sorted(other.items(), key=lambda kv: -kv[1]["n"])[:10]:
            out.append(f"| {cid} | {hit['n']} | {hit['best']} | {hit['cls']} |")
    return "\n".join(out)


def _method_section(market, checked: int) -> list[str]:
    return [
        "## Как это измерено",
        "",
        f"Живой съём выдачи {market.search_engine} по каждому запросу "
        f"(регион {market.regions[0] if market.regions else '—'}), {checked} запросов, "
        "глубина 20 позиций.",
        "",
        "Важно про источники: **Яндекс.Вебмастер показывает среднюю позицию по "
        "фактически состоявшимся показам** — персонализированным, региональным, по "
        "хвостовым переформулировкам, — и поэтому систематически выглядит оптимистичнее "
        "чистой выдачи. Цифры выше сняты из самой выдачи и годятся для планирования; "
        "цифры Вебмастера годятся для отслеживания собственной динамики.",
        "",
        "«Спрос» — базовая частотность Wordstat из семантического ядра проекта: "
        "сколько раз в месяц запрос вводят в Яндексе.",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
