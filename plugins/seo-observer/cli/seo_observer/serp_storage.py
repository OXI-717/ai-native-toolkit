"""SERP facts and immutable measured populations, separate from console averages."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date as Date
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from seo_observer import __version__
from seo_observer.config import compute_config_hash
from seo_observer.growth_locale import text as _t
from seo_observer.serp import _visibility_score, normalized_host
from seo_observer.storage import (
    CollectionRun,
    RawArtifact,
    SourceRequest,
    _insert_artifact,
    _insert_request,
    _insert_run,
)

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS serp_check_runs (
 tenant TEXT NOT NULL, provider_project TEXT NOT NULL, iso_week TEXT NOT NULL,
 check_date TEXT NOT NULL, status TEXT NOT NULL, price_rub REAL NOT NULL,
 attempted_at TEXT NOT NULL, PRIMARY KEY(tenant, provider_project, iso_week));
CREATE TABLE IF NOT EXISTS serp_audit (
 id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, provider_project TEXT NOT NULL,
 event TEXT NOT NULL, recorded_at TEXT NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS serp_budget_state (
 tenant TEXT NOT NULL, market TEXT NOT NULL, enabled_at TEXT, PRIMARY KEY(tenant, market));
CREATE TABLE IF NOT EXISTS serp_measurements (
 measurement_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, check_date TEXT NOT NULL,
 market TEXT NOT NULL, search_engine TEXT NOT NULL, region TEXT NOT NULL,
 device TEXT NOT NULL, source TEXT NOT NULL, historical INTEGER NOT NULL,
 comparability TEXT NOT NULL, population_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 FOREIGN KEY(project_id) REFERENCES projects(project_id));
CREATE INDEX IF NOT EXISTS idx_serp_measurements_project ON serp_measurements(project_id, check_date);
"""


def ensure_schema(storage):
    with storage.connect() as con:
        con.executescript(TABLE_SQL)
        serp_columns = {r[1] for r in con.execute("PRAGMA table_info(serp_results)")}
        if "url_known" not in serp_columns:
            con.execute(
                "ALTER TABLE serp_results ADD COLUMN url_known INTEGER NOT NULL DEFAULT 1"
            )
        columns = {r[1] for r in con.execute("PRAGMA table_info(competitor_metrics)")}
        for name, ddl in {
            "measurement_id": "TEXT",
            "search_engine": "TEXT",
            "device": "TEXT",
            "cluster": "TEXT",
            "share_of_voice": "REAL",
            "keyword_count": "INTEGER",
        }.items():
            if name not in columns:
                con.execute(f"ALTER TABLE competitor_metrics ADD COLUMN {name} {ddl}")


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _domain_match(host, patterns):
    return any(
        host == p or (p.startswith("*.") and host.endswith(p[1:])) for p in patterns
    )


def domain_catalog(config, market=None):
    from seo_observer.serp_markets import competitor_scopes

    scopes = competitor_scopes(config, market)
    owned = [p for p in config.competitors.owned_domains if not p.startswith("*.")]
    if not owned:
        owned = [normalized_host(config.properties[0].url)]
    catalog = [
        {
            "domain": owned[0],
            "name": owned[0],
            "owned": True,
            "patterns": list(config.competitors.owned_domains) or owned,
        }
    ]
    for c in config.competitors.items:
        if scopes is not None and c.markets and not scopes.intersection(c.markets):
            continue
        domain = next(
            (p for p in c.domain_patterns if not p.startswith("*.")),
            c.domain_patterns[0].removeprefix("*."),
        )
        catalog.append(
            {
                "domain": domain,
                "name": c.name,
                "owned": False,
                "patterns": list(c.domain_patterns),
            }
        )
    return catalog


def calculate_metrics(keywords, snapshots, catalog):
    """Best rank per keyword/domain; SOV across the configured domain roster.

    Linear top-10 weights match serp._visibility_score. The population includes
    measured keywords where none of the configured domains ranks.
    """
    groups = {"__all__": list(keywords)}
    for q, meta in keywords.items():
        group = groups.setdefault(meta["cluster"], [])
        group.append(q)
    rows = []
    for cluster, queries in groups.items():
        metrics = []
        for domain in catalog:
            ranks = [
                min(
                    (
                        r["position"]
                        for r in snapshots[q]
                        if _domain_match(r["domain"], domain["patterns"])
                    ),
                    default=None,
                )
                for q in queries
            ]
            score = sum(_visibility_score(r or 0, 10) for r in ranks)
            metrics.append(
                {
                    "domain": domain["domain"],
                    "name": domain["name"],
                    "owned": domain["owned"],
                    "cluster": cluster,
                    "visibility": score / len(queries) if queries else 0.0,
                    "top3": sum(r is not None and r <= 3 for r in ranks),
                    "top10": sum(r is not None for r in ranks),
                    "keywords": len(queries),
                }
            )
        total = sum(m["visibility"] for m in metrics)
        for metric in metrics:
            metric["sov"] = metric["visibility"] / total if total else 0.0
        rows.extend(metrics)
    return rows


def ingest_measurement(
    storage,
    config,
    *,
    date,
    market,
    engine,
    region,
    device,
    source,
    historical,
    keywords,
    snapshots,
    owned_positions=None,
):
    Date.fromisoformat(date)
    if not keywords or set(keywords) != set(snapshots):
        raise ValueError(
            "SERP population and snapshot keywords must match and be non-empty"
        )
    clean = {}
    for q, rows in snapshots.items():
        dedup = {}
        for row in rows:
            rank = row.get("position", row.get("rank"))
            url = row.get("url", "")
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise ValueError("Invalid SERP rank")
            if rank > 10:
                continue
            if (
                urlsplit(url).scheme not in ("http", "https")
                or not urlsplit(url).hostname
            ):
                raise ValueError("Invalid SERP URL")
            dedup[(rank, url)] = {
                "position": rank,
                "url": url,
                "domain": normalized_host(url),
                **({"url_known": False} if row.get("url_known") is False else {}),
            }
        clean[q] = sorted(dedup.values(), key=lambda r: (r["position"], r["url"]))
    from seo_observer.serp_markets import resolve_market

    market = resolve_market(
        config,
        market_id=market,
        engine=engine,
        region=region,
        source=source,
        keyword_set_id=next(iter(keywords.values()))["keyword_set"],
    )
    catalog = domain_catalog(config, market)
    metrics = calculate_metrics(keywords, clean, catalog)
    payload = {
        "keywords": keywords,
        "snapshots": clean,
        "catalog": catalog,
        "metrics": metrics,
        "owned_positions": owned_positions or {},
    }
    population_hash = _hash(
        {
            "keywords": keywords,
            "catalog": catalog,
            "depth": 10,
            "formula": "linear-top10-v2-including-empty",
        }
    )
    tenant = config.project.namespace
    measurement_id = "serp-" + _hash(
        [tenant, date, market, engine, region, device, source, historical, payload]
    )
    if storage.fetchall(
        "SELECT measurement_id FROM serp_measurements WHERE measurement_id=?",
        (measurement_id,),
    ):
        return measurement_id
    stamp = datetime.now(timezone.utc).isoformat()
    comparable = "insufficient_history" if historical else "comparable"
    artifact_data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    relative = f"raw/{tenant}/serp/{measurement_id}.json"
    target = storage.observer_home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(artifact_data)
    with storage.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute(
            "SELECT 1 FROM serp_measurements WHERE measurement_id=?", (measurement_id,)
        ).fetchone():
            return measurement_id
        # A changed read replaces the same logical slot, never doubles counts.
        old = [
            r[0]
            for r in con.execute(
                """SELECT measurement_id FROM serp_measurements WHERE
            project_id=? AND check_date=? AND market=? AND search_engine=? AND region=? AND device=? AND source=? AND historical=?""",
                (tenant, date, market, engine, region, device, source, int(historical)),
            )
        ]
        for prior in old:
            con.execute(
                "UPDATE serp_results SET is_current=0 WHERE reporting_period_id=?",
                (prior,),
            )
            con.execute(
                "DELETE FROM competitor_metrics WHERE measurement_id=?", (prior,)
            )
            con.execute(
                "DELETE FROM serp_measurements WHERE measurement_id=?", (prior,)
            )
        _insert_run(
            con,
            CollectionRun(
                measurement_id,
                tenant,
                date,
                date,
                config.project.timezone,
                stamp,
                stamp,
                "complete",
                compute_config_hash(config),
                __version__,
                config.schema_version,
            ),
        )
        request_id = measurement_id + "-request"
        artifact_id = measurement_id + "-artifact"
        # Reverting to a previously observed payload reuses its immutable provenance.
        if con.execute(
            "SELECT 1 FROM source_requests WHERE request_id=?", (request_id,)
        ).fetchone():
            con.execute(
                "DELETE FROM serp_results WHERE reporting_period_id=?",
                (measurement_id,),
            )
        else:
            _insert_request(
                con,
                SourceRequest(
                    request_id,
                    measurement_id,
                    source,
                    "__all__",
                    measurement_id,
                    measurement_id,
                    {
                        "date": date,
                        "market": market,
                        "engine": engine,
                        "device": device,
                        "region": region,
                    },
                    1,
                    stamp,
                    stamp,
                    "success",
                    "final",
                ),
            )
            _insert_artifact(
                con,
                RawArtifact(
                    artifact_id,
                    request_id,
                    relative,
                    hashlib.sha256(artifact_data).hexdigest(),
                    "application/json",
                    "none",
                    "redacted",
                    len(artifact_data),
                ),
            )
        con.execute(
            "INSERT INTO serp_measurements VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                measurement_id,
                tenant,
                date,
                market,
                engine,
                region,
                device,
                source,
                int(historical),
                comparable,
                population_hash,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        fact_ids = defaultdict(list)
        for q, rows in clean.items():
            for row in rows:
                fact = dict(
                    project_id=tenant,
                    source=source,
                    effective_at=date + "T00:00:00",
                    source_timezone=config.project.timezone,
                    observed_at=stamp,
                    reporting_period_id=measurement_id,
                    request_id=request_id,
                    artifact_id=artifact_id,
                    logical_observation_key=_hash(
                        [date, market, region, device, q, row]
                    ),
                    collection_attempt_key=measurement_id,
                    protocol_slot=_hash([market, region, device, q]),
                    keyword_set_id=keywords[q]["keyword_set"],
                    keyword_id=q,
                    region=region,
                    device=device,
                    search_engine=engine,
                    rank=row["position"],
                    result_url=row["url"],
                    url_known=int(row.get("url_known", True)),
                    normalized_domain=row["domain"],
                    belongs_to_project=int(
                        _domain_match(row["domain"], catalog[0]["patterns"])
                    ),
                    dataset_coverage="unknown" if historical else "complete",
                    freshness="final",
                    comparability=comparable,
                    fact_schema_version=1,
                    normalizer_version="growth-serp-v1",
                )
                cur = con.execute(
                    f"INSERT INTO serp_results({','.join(fact)}) VALUES({','.join('?' for _ in fact)})",
                    tuple(fact.values()),
                )
                for domain in catalog:
                    if _domain_match(row["domain"], domain["patterns"]):
                        fact_ids[(domain["domain"], "__all__")].append(cur.lastrowid)
                        fact_ids[(domain["domain"], keywords[q]["cluster"])].append(
                            cur.lastrowid
                        )
        for m in metrics:
            con.execute(
                """INSERT INTO competitor_metrics(metric_id,project_id,reporting_period_id,normalized_domain,keyword_set_id,
                visibility_score,formula_version,top3_count,top10_count,input_fact_ids_json,calculated_at,measurement_id,
                search_engine,device,cluster,share_of_voice,keyword_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _hash([measurement_id, m["domain"], m["cluster"]]),
                    tenant,
                    measurement_id,
                    m["domain"],
                    "__all__",
                    m["visibility"],
                    "linear-top10-roster-v2-including-empty",
                    m["top3"],
                    m["top10"],
                    json.dumps(fact_ids[(m["domain"], m["cluster"])]),
                    stamp,
                    measurement_id,
                    engine,
                    device,
                    m["cluster"],
                    m["sov"],
                    m["keywords"],
                ),
            )
    return measurement_id


def load_measurements(storage, tenant, *, through=None):
    # Old databases with no SERP bootstrap still render without the new page.
    if not storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='serp_measurements'"
    ):
        return []
    measurements = storage.fetchall(
        "SELECT * FROM serp_measurements WHERE project_id=? AND check_date<=? ORDER BY check_date, measurement_id",
        (tenant, through or "9999-12-31"),
    )
    for m in measurements:
        m.update(json.loads(m.pop("payload_json")))
    return measurements


def import_history(storage, config, paths):
    from seo_observer.keyword_clusters import (
        clean_cluster,
        parse_keyword_file,
        split_keyword_comment,
    )
    from seo_observer.serp_markets import resolve_market

    storage.bootstrap()
    storage.upsert_project_config(config, config_hash=compute_config_hash(config))
    ensure_schema(storage)
    clusters = {
        s.id: {
            " ".join(k.keyword.casefold().split()): clean_cluster(k.cluster)
            if k.cluster
            else _t("serp_no_cluster")
            for k in parse_keyword_file(s.path)
        }
        for s in config.keyword_sets
    }
    groups = {}
    for path in paths:
        path = Path(path)
        manifest_path = path.with_name("manifest.json")
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("project") != config.project.namespace:
                raise ValueError("Historical artifact belongs to another project")
        data = json.loads(path.read_text())
        if data.get("schema") != "seo-observer.serp_extract.v1" or not isinstance(
            data.get("serp_rows"), list
        ):
            raise ValueError("Unsupported historical SERP artifact")
        for row in data["serp_rows"]:
            if row.get("quality") not in ("live", "artifact"):
                continue
            market_id = resolve_market(
                config,
                market_id=row.get("market"),
                keyword_set_id=row["keyword_set_id"],
                engine=row["search_engine"],
                region=str(row["region_id"]),
                source=row["provider"],
            )
            key = (
                market_id,
                row["effective_at"][:10],
                row["search_engine"],
                str(row["region_id"]),
                row["device"],
                row["provider"],
            )
            group = groups.setdefault(
                key, {"keywords": {}, "snapshots": defaultdict(list)}
            )
            q = " ".join(row["keyword"].casefold().split())
            set_id = row["keyword_set_id"]
            cluster = clusters.get(set_id, {}).get(
                split_keyword_comment(q)[0], _t("serp_no_cluster")
            )
            if q not in group["keywords"] or cluster != _t("serp_no_cluster"):
                group["keywords"][q] = {"cluster": cluster, "keyword_set": set_id}
            group["snapshots"][q].append({"position": row["rank"], "url": row["url"]})
    ids = []
    for (market_id, date, engine, region, device, source), group in groups.items():
        ids.append(
            ingest_measurement(
                storage,
                config,
                date=date,
                market=market_id,
                engine=engine,
                region=region,
                device=device,
                source=source,
                historical=True,
                **group,
            )
        )
    return {"ok": True, "measurements": len(ids), "ids": ids, "historical": True}


def rebuild_measurements(storage, config):
    """Reassign legacy contours and recalculate rosters from saved facts, without API.

    Resolve every population before writing anything. Ambiguous lineage refuses
    the rebuild rather than assigning an arbitrary market. Immutable raw artifacts
    and collection provenance remain available after retiring old measurements.
    """
    from seo_observer.serp_markets import resolve_market

    storage.bootstrap()
    storage.upsert_project_config(config, config_hash=compute_config_hash(config))
    ensure_schema(storage)
    old = load_measurements(storage, config.project.namespace)
    groups = {}
    for m in old:
        for q, meta in m["keywords"].items():
            market = resolve_market(
                config,
                market_id=m["market"],
                keyword_set_id=meta["keyword_set"],
                engine=m["search_engine"],
                region=m["region"],
                source=m["source"],
            )
            key = (
                m["check_date"],
                market,
                m["search_engine"],
                m["region"],
                m["device"],
                m["source"],
                bool(m["historical"]),
            )
            group = groups.setdefault(
                key, {"keywords": {}, "snapshots": {}, "owned_positions": {}}
            )
            # Refuse conflicting legacy populations instead of silently overwriting.
            if q in group["keywords"] and (
                group["keywords"][q] != meta
                or group["snapshots"][q] != m["snapshots"][q]
                or group["owned_positions"].get(q)
                != m.get("owned_positions", {}).get(q)
            ):
                raise ValueError("Conflicting legacy SERP populations")
            group["keywords"][q] = meta
            group["snapshots"][q] = m["snapshots"][q]
            if q in m.get("owned_positions", {}):
                group["owned_positions"][q] = m["owned_positions"][q]
    ids = []
    for (
        date,
        market,
        engine,
        region,
        device,
        source,
        historical,
    ), group in groups.items():
        ids.append(
            ingest_measurement(
                storage,
                config,
                date=date,
                market=market,
                engine=engine,
                region=region,
                device=device,
                source=source,
                historical=historical,
                **group,
            )
        )
    retired = {m["measurement_id"] for m in old} - set(ids)
    with storage.connect() as con:
        for prior in retired:
            con.execute(
                "UPDATE serp_results SET is_current=0 WHERE reporting_period_id=?",
                (prior,),
            )
            con.execute(
                "DELETE FROM competitor_metrics WHERE measurement_id=?", (prior,)
            )
            con.execute(
                "DELETE FROM serp_measurements WHERE measurement_id=?", (prior,)
            )
        con.execute(
            "INSERT INTO serp_audit(tenant,provider_project,event,recorded_at,details_json) VALUES(?,?,?,?,?)",
            (
                config.project.namespace,
                "local",
                "measurements_rebuilt",
                datetime.now(timezone.utc).isoformat(),
                json.dumps({"measurements": len(ids), "retired": len(retired)}),
            ),
        )
    return {"ok": True, "measurements": len(ids), "retired": len(retired), "ids": ids}
