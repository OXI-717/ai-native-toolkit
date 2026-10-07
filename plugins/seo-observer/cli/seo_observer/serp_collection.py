"""Budgeted weekly submission. Reads and retries never call the paid endpoint.

The durable reservation precedes the network request. An ambiguous response
consumes the week's attempt: retrying a non-idempotent paid API is unsafe.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from seo_observer.config import compute_config_hash
from seo_observer.growth_locale import text as _t
from seo_observer.keyword_clusters import clean_cluster, parse_keyword_file
from seo_observer.serp_storage import ensure_schema, ingest_measurement
from seo_observer.topvisor import device_key, searcher_key


def money(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("SERP budget/price must be a finite non-negative number")
    return float(value)


def weekly_check(
    storage, client, *, tenant, project_id, budget, now, before_submit=None
):
    budget = money(budget)
    week = now.astimezone(ZoneInfo("Europe/Moscow")).strftime("%G-W%V")
    key = (tenant, str(project_id), week)
    if not budget:
        return {"status": "disabled", "week": week}
    with storage.connect() as con:
        if con.execute(
            "SELECT 1 FROM serp_check_runs WHERE tenant=? AND provider_project=? AND iso_week=?",
            key,
        ).fetchone():
            return {"status": "already_attempted", "week": week}
    price = money(client.estimate_price(project_id=project_id, do_snapshots=True))
    if price > budget:
        return {
            "status": "over_budget",
            "price_rub": price,
            "budget_rub": budget,
            "week": week,
        }
    if before_submit is not None:
        refusal = before_submit()
        if refusal:
            return {**refusal, "price_rub": price, "week": week}
    # Unique key also arbitrates two cron/manual processes racing after pricing.
    with storage.connect() as con:
        inserted = con.execute(
            """INSERT OR IGNORE INTO serp_check_runs
            (tenant, provider_project, iso_week, check_date, status, price_rub, attempted_at)
            VALUES (?, ?, ?, ?, 'reserved', ?, ?)""",
            (
                *key,
                now.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat(),
                price,
                now.isoformat(),
            ),
        ).rowcount
    if not inserted:
        return {"status": "already_attempted", "week": week}
    try:
        ids = client.run_check(project_id=project_id, do_snapshots=True)
        if str(project_id) not in ids:
            raise ValueError("TopVisor did not acknowledge the requested project")
    except Exception:
        with storage.connect() as con:
            con.execute(
                "UPDATE serp_check_runs SET status='uncertain' WHERE tenant=? AND provider_project=? AND iso_week=?",
                key,
            )
        raise
    with storage.connect() as con:
        con.execute(
            "UPDATE serp_check_runs SET status='submitted' WHERE tenant=? AND provider_project=? AND iso_week=?",
            key,
        )
    return {"status": "submitted", "price_rub": price, "week": week}


def market_keywords(config, market):
    keywords = {}
    for group in config.keyword_sets:
        if group.market != market.id:
            continue
        for item in parse_keyword_file(group.path):
            query = " ".join(item.keyword.casefold().split())
            if query not in keywords or item.cluster:
                keywords[query] = {
                    "cluster": clean_cluster(item.cluster)
                    if item.cluster
                    else _t("serp_no_cluster"),
                    "keyword_set": group.id,
                }
    return keywords


def budget_for(market):
    return money(market.fields.get("weekly_budget_rub", 0))


def collect_market(storage, config, market, client, *, check_date, historical=False):
    """Validate all configured slots before publishing; incomplete reads stay pending."""
    state = client.project_state(project_id=market.fields["project_id"])
    if (
        str(state.get("status_positions")) != "0"
        or str(state.get("positions_time") or "")[:10] < check_date
    ):
        return {"status": "pending", "date": check_date, "reason": "provider_not_ready"}
    keywords = market_keywords(config, market)
    if not keywords:
        raise ValueError("TopVisor market has no configured keywords")
    devices = market.devices or tuple(
        sorted(
            {d for s in config.keyword_sets if s.market == market.id for d in s.devices}
        )
    )
    if not devices:
        raise ValueError("TopVisor market has no devices")
    batches = []
    for region in market.regions:
        for device in devices:
            snapshots = client.fetch_snapshots(
                project_id=market.fields["project_id"],
                search_engine=market.search_engine,
                region_key=region,
                region_lang=market.language,
                device=device,
                date=check_date,
                require_date=True,
            )
            snapshots = {
                " ".join(q.casefold().split()): rows for q, rows in snapshots.items()
            }
            regions = [
                r
                for s in state.get("searchers", [])
                if str(s.get("key")) == str(searcher_key(market.search_engine))
                for r in s.get("regions", [])
                if str(r.get("key")) == str(region)
                and r.get("lang") == market.language
                and str(r.get("device")) == str(device_key(device))
            ]
            if len(regions) != 1:
                raise ValueError("TopVisor region/device is not configured uniquely")
            positions = client.fetch_positions(
                project_id=market.fields["project_id"],
                region_index=regions[0]["index"],
                date=check_date,
            )
            positions = {
                " ".join(q.casefold().split()): rank for q, rank in positions.items()
            }
            missing = [q for q in keywords if q not in snapshots or q not in positions]
            if missing:
                return {
                    "status": "pending",
                    "date": check_date,
                    "missing_keywords": len(missing),
                    "device": device,
                }
            batches.append(
                dict(
                    date=check_date,
                    market=market.id,
                    engine=market.search_engine,
                    region=region,
                    device=device,
                    source=market.provider,
                    historical=historical,
                    keywords=keywords,
                    owned_positions={q: positions[q] for q in keywords},
                    snapshots={q: snapshots[q] for q in keywords},
                )
            )
    for batch in batches:
        ingest_measurement(storage, config, **batch)
    return {
        "status": "collected",
        "date": check_date,
        "slots": len(batches),
        "keywords": len(keywords),
    }


def run_collection(
    storage, config, client_factory, *, weekly=False, check_date=None, now=None
):
    now = now or datetime.now(timezone.utc)
    storage.bootstrap()
    storage.upsert_project_config(config, config_hash=compute_config_hash(config))
    ensure_schema(storage)
    markets = [m for m in config.markets if m.provider.startswith("topvisor_")]
    results = []
    completed = {}
    for market in markets:
        budget = budget_for(market)
        project_id = str(market.fields.get("project_id") or "")
        with storage.connect() as con:
            con.execute(
                """INSERT INTO serp_budget_state(tenant, market, enabled_at) VALUES(?,?,?)
                ON CONFLICT(tenant, market) DO UPDATE SET enabled_at=
                CASE WHEN excluded.enabled_at IS NULL THEN NULL ELSE COALESCE(serp_budget_state.enabled_at,excluded.enabled_at) END""",
                (
                    config.project.namespace,
                    market.id,
                    now.isoformat() if budget else None,
                ),
            )
        if not weekly and not check_date:
            runs = storage.fetchall(
                "SELECT * FROM serp_check_runs WHERE tenant=? AND provider_project=? AND status IN ('submitted','reserved','uncertain') ORDER BY check_date",
                (config.project.namespace, project_id),
            )
        else:
            runs = []
        # Disabled and no pending reads means credentials are unnecessary.
        if (weekly and not budget) or (not weekly and not check_date and not runs):
            results.append(
                {"market": market.id, "status": "disabled" if not budget else "idle"}
            )
            continue
        if not project_id:
            raise ValueError("Active TopVisor market requires project_id")
        if runs:
            from seo_observer.serp_operations import audit

            active = []
            for run in runs:
                if now - datetime.fromisoformat(run["attempted_at"]) >= timedelta(
                    days=7
                ):
                    with storage.connect() as con:
                        changed = con.execute(
                            "UPDATE serp_check_runs SET status='expired' WHERE tenant=? AND provider_project=? AND iso_week=? AND status IN ('submitted','reserved','uncertain')",
                            (config.project.namespace, project_id, run["iso_week"]),
                        ).rowcount
                        if changed:
                            audit(
                                con,
                                config.project.namespace,
                                project_id,
                                "collection_expired",
                                dict(run),
                            )
                    results.append(
                        {
                            "market": market.id,
                            "status": "expired",
                            "week": run["iso_week"],
                        }
                    )
                else:
                    active.append(run)
            runs = active
            if not runs:
                continue
        client = client_factory(market)
        if weekly:

            def validate_population(
                client=client, project_id=project_id, market=market
            ):
                actual = client.keyword_names(project_id=project_id)
                expected = set(market_keywords(config, market))
                count = len(actual)
                actual = {" ".join(q.casefold().split()) for q in actual}
                duplicates = count - len(actual)
                missing = len(expected - actual)
                extra = len(actual - expected)
                if not expected or missing or extra or duplicates:
                    return {
                        "status": "population_mismatch",
                        "missing_keywords": missing,
                        "extra_keywords": extra,
                        "duplicate_keywords": duplicates,
                    }
                return None

            result = weekly_check(
                storage,
                client,
                tenant=config.project.namespace,
                project_id=project_id,
                budget=budget,
                now=now,
                before_submit=validate_population,
            )
            results.append({"market": market.id, **result})
            continue
        for run in runs or [{"check_date": check_date}]:
            result = collect_market(
                storage,
                config,
                market,
                client,
                check_date=run["check_date"],
                historical=bool(check_date),
            )
            results.append({"market": market.id, **result})
            if "iso_week" in run:
                key = (config.project.namespace, project_id, run["iso_week"])
                completed.setdefault(key, []).append(result["status"] == "collected")
    for key, states in completed.items():
        if all(states):
            with storage.connect() as con:
                con.execute(
                    "UPDATE serp_check_runs SET status='collected' WHERE tenant=? AND provider_project=? AND iso_week=?",
                    key,
                )
    return {"ok": True, "results": results}
