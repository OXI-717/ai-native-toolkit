from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Iterator

from seo_observer import __version__
from seo_observer.config import ProjectConfig, observer_home


SCHEMA_VERSION = 1
RESERVED_ALL = "__all__"
COMPETITOR_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS competitor_discovery_runs (
  run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  property_id TEXT NOT NULL DEFAULT '__all__',
  source TEXT NOT NULL,
  request_id TEXT,
  target_domain TEXT NOT NULL,
  provider_mode TEXT NOT NULL CHECK (provider_mode IN ('fixture', 'artifact', 'live')),
  quality TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  observed_at TEXT NOT NULL,
  artifact_manifest_path TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
  FOREIGN KEY (request_id) REFERENCES source_requests(request_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS competitor_candidates (
  candidate_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  property_id TEXT NOT NULL DEFAULT '__all__',
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,
  request_id TEXT,
  target_domain TEXT NOT NULL,
  competitor_domain TEXT NOT NULL,
  intersections INTEGER NOT NULL DEFAULT 0,
  organic_keywords INTEGER NOT NULL DEFAULT 0,
  estimated_traffic REAL NOT NULL DEFAULT 0,
  quality TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  observed_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (run_id, competitor_domain),
  FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
  FOREIGN KEY (run_id) REFERENCES competitor_discovery_runs(run_id) ON DELETE RESTRICT,
  FOREIGN KEY (request_id) REFERENCES source_requests(request_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS keyword_gap_rows (
  gap_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  property_id TEXT NOT NULL DEFAULT '__all__',
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,
  request_id TEXT,
  target_domain TEXT NOT NULL,
  competitor_domain TEXT NOT NULL DEFAULT '__all__',
  competitor_domains_json TEXT NOT NULL DEFAULT '[]',
  keyword_set_id TEXT,
  keyword_set_hash TEXT,
  keyword TEXT NOT NULL,
  normalized_keyword TEXT NOT NULL,
  location_code TEXT,
  location_name TEXT,
  language_code TEXT,
  best_competitor_rank INTEGER,
  representative_competitor_url TEXT,
  search_volume INTEGER NOT NULL DEFAULT 0,
  cpc REAL NOT NULL DEFAULT 0,
  quality TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  observed_at TEXT NOT NULL,
  citation_ids_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE (run_id, normalized_keyword, location_code, location_name, language_code),
  FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
  FOREIGN KEY (run_id) REFERENCES competitor_discovery_runs(run_id) ON DELETE RESTRICT,
  FOREIGN KEY (request_id) REFERENCES source_requests(request_id) ON DELETE RESTRICT
);
"""
CRAWL_PAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS crawl_pages (
  fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  property_id TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source = 'local_crawl'),
  effective_at TEXT NOT NULL,
  source_timezone TEXT NOT NULL,
  request_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  logical_observation_key TEXT NOT NULL,
  collection_attempt_key TEXT NOT NULL,
  crawled_url TEXT NOT NULL,
  final_url TEXT NOT NULL,
  depth INTEGER NOT NULL CHECK (depth >= 0),
  fetch_status INTEGER,
  robots_status TEXT NOT NULL CHECK (robots_status IN ('allowed', 'excluded')),
  content_type TEXT,
  byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
  title TEXT NOT NULL DEFAULT '',
  meta_description TEXT NOT NULL DEFAULT '',
  h1_text TEXT NOT NULL DEFAULT '',
  h1_count INTEGER NOT NULL DEFAULT 0 CHECK (h1_count >= 0),
  canonical_url TEXT,
  header_canonical_url TEXT,
  meta_robots TEXT,
  x_robots_tag TEXT,
  hreflang_json TEXT NOT NULL DEFAULT '[]',
  redirect_chain_json TEXT NOT NULL DEFAULT '[]',
  internal_links_json TEXT NOT NULL DEFAULT '[]',
  error TEXT,
  dataset_coverage TEXT NOT NULL CHECK (dataset_coverage IN ('complete', 'truncated', 'privacy_thresholded', 'unknown', 'unavailable')),
  freshness TEXT NOT NULL CHECK (freshness IN ('provisional', 'final', 'stale')),
  comparability TEXT NOT NULL CHECK (comparability IN ('comparable', 'config_break', 'protocol_break', 'population_mismatch', 'insufficient_history', 'window_mismatch')),
  fact_schema_version INTEGER NOT NULL,
  normalizer_version TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
  supersedes_fact_id INTEGER,
  superseded_by_fact_id INTEGER,
  content_hash TEXT NOT NULL,
  FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
  FOREIGN KEY (request_id) REFERENCES source_requests(request_id) ON DELETE RESTRICT,
  FOREIGN KEY (artifact_id) REFERENCES raw_artifacts(artifact_id) ON DELETE RESTRICT,
  FOREIGN KEY (supersedes_fact_id) REFERENCES crawl_pages(fact_id) ON DELETE RESTRICT,
  FOREIGN KEY (superseded_by_fact_id) REFERENCES crawl_pages(fact_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_crawl_pages_current
  ON crawl_pages(project_id, property_id, logical_observation_key)
  WHERE is_current = 1;
"""


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class CollectionRun:
    run_id: str
    project_id: str
    period_start: str
    period_end: str
    timezone: str
    started_at: str
    finished_at: str | None
    status: str
    config_hash: str
    cli_version: str
    config_schema_version: int


@dataclass(frozen=True)
class SourceRequest:
    request_id: str
    run_id: str
    source: str
    property_id: str
    logical_observation_key: str
    collection_attempt_key: str
    request_descriptor: dict[str, Any]
    attempt: int
    queried_at: str
    completed_at: str | None
    transport_status: str
    freshness: str
    sampled: bool = False
    sample_share: float | None = None
    data_lag_seconds: int | None = None
    row_limit: int | None = None
    rows_received: int | None = None
    pages_expected: int | None = None
    pages_received: int | None = None
    error_code: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True)
class RawArtifact:
    artifact_id: str
    request_id: str
    relative_path: str
    sha256: str
    content_type: str
    compression: str
    redaction_state: str
    byte_size: int


@dataclass(frozen=True)
class SearchPerformanceObservation:
    project_id: str
    property_id: str
    source: str
    effective_start: str
    effective_end: str
    source_timezone: str
    effective_instant_start: str
    effective_instant_end: str
    observed_at: str
    reporting_period_id: str
    request_id: str
    artifact_id: str
    logical_observation_key: str
    collection_attempt_key: str
    query_id: str
    query_text: str
    page_id: str
    page_url: str
    search_engine: str
    impressions: int
    clicks: int
    ctr: float
    average_position: float | None
    dataset_coverage: str
    freshness: str
    comparability: str
    device: str | None = None
    country: str | None = None
    region: str | None = None
    segment_id: str | None = None
    sampled: bool = False
    sample_share: float | None = None
    fact_schema_version: int = 1
    normalizer_version: str = "storage-v1"


@dataclass(frozen=True)
class TrafficMetricObservation:
    project_id: str
    property_id: str
    source: str
    effective_start: str
    effective_end: str
    source_timezone: str
    request_id: str
    artifact_id: str
    logical_observation_key: str
    collection_attempt_key: str
    channel: str
    search_engine: str
    landing_page_id: str
    device: str
    region: str
    attribution_model: str
    visits: int | None
    users: int | None
    pageviews: int | None
    bounce_rate: float | None
    avg_visit_duration_seconds: float | None
    dataset_coverage: str
    freshness: str
    comparability: str
    fact_schema_version: int = 1
    normalizer_version: str = "storage-v1"
    effective_instant_start: str | None = None
    effective_instant_end: str | None = None
    observed_at: str | None = None
    reporting_period_id: str | None = None


@dataclass(frozen=True)
class CrawlPageObservation:
    project_id: str
    property_id: str
    source: str
    effective_at: str
    source_timezone: str
    request_id: str
    artifact_id: str
    logical_observation_key: str
    collection_attempt_key: str
    crawled_url: str
    final_url: str
    depth: int
    fetch_status: int | None
    robots_status: str
    content_type: str | None
    byte_size: int
    title: str
    meta_description: str
    h1_text: str
    h1_count: int
    canonical_url: str | None
    header_canonical_url: str | None
    meta_robots: str | None
    x_robots_tag: str | None
    hreflang_json: str
    redirect_chain_json: str
    internal_links_json: str
    error: str | None
    dataset_coverage: str
    freshness: str
    comparability: str
    fact_schema_version: int = 1
    normalizer_version: str = "crawl-v1"


def default_database_path(project_id: str, home: Path | None = None) -> Path:
    return (home or observer_home()) / "projects" / project_id / "observer.db"


def resolve_raw_artifact_path(
    *,
    observer_home: Path,
    project_id: str,
    relative_path: str,
) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise StorageError("Raw artifact paths must be relative paths under the project raw root.")
    raw_root = observer_home / "projects" / project_id / "raw"
    resolved_root = raw_root.resolve()
    resolved = (raw_root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise StorageError("Raw artifact path escaped the project raw root.")
    return resolved


class SEOStorage:
    def __init__(self, db_path: Path, *, observer_home: Path | None = None) -> None:
        self.db_path = db_path
        self.observer_home = observer_home or self._infer_observer_home(db_path)

    @staticmethod
    def _infer_observer_home(db_path: Path) -> Path:
        try:
            return db_path.parents[2]
        except IndexError:
            return observer_home()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def bootstrap(self) -> None:
        self.db_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        migrations = _load_migrations()
        if len(migrations) != SCHEMA_VERSION:
            raise StorageError(
                f"Expected {SCHEMA_VERSION} storage migration(s), found {len(migrations)}."
            )
        with self.connect() as con:
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA busy_timeout = 5000")
            current = _current_schema_version(con)
            for version, sql in migrations:
                if version <= current:
                    continue
                if version != current + 1:
                    raise StorageError(
                        f"Non-contiguous storage migration: current={current}, next={version}."
                    )
                con.executescript(sql)
                con.execute(
                    """
                    INSERT INTO schema_meta(db_schema_version, applied_at, app_version)
                    VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)
                    ON CONFLICT(db_schema_version) DO UPDATE SET
                      applied_at = schema_meta.applied_at,
                      app_version = schema_meta.app_version
                    """,
                    (version, __version__),
                )
                current = version
            if current != SCHEMA_VERSION:
                raise StorageError(
                    f"Storage schema is at version {current}, expected {SCHEMA_VERSION}."
                )
            _ensure_compatible_v1_schema(con)

    def latest_comparable_serp_audit(self, protocol_hashes: list[str]) -> dict[str, Any] | None:
        """Return a stored comparable SERP audit snapshot when schema support exists.

        Storage v1 does not yet persist competitor audit snapshots as a first-class
        table; the method is an extension point for the CLI baseline lookup order.
        """
        _ = protocol_hashes
        return None

    def upsert_project_config(self, config: ProjectConfig, *, config_hash: str) -> None:
        self.bootstrap()
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO projects(project_id, timezone, config_hash, config_schema_version)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                  timezone = excluded.timezone,
                  config_hash = excluded.config_hash,
                  config_schema_version = excluded.config_schema_version,
                  updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    config.project.namespace,
                    config.project.timezone,
                    config_hash,
                    config.schema_version,
                ),
            )
            for prop in config.properties:
                con.execute(
                    """
                    INSERT INTO properties(project_id, property_id, canonical_url, property_type, enabled)
                    VALUES (?, ?, ?, 'url_prefix', 1)
                    ON CONFLICT(project_id, property_id) DO UPDATE SET
                      canonical_url = excluded.canonical_url,
                      property_type = excluded.property_type,
                      enabled = excluded.enabled
                    """,
                    (config.project.namespace, prop.id, prop.url),
                )
            for source in config.sources.values():
                con.execute(
                    """
                    INSERT INTO sources(project_id, source, enabled, required, source_config_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, source) DO UPDATE SET
                      enabled = excluded.enabled,
                      required = excluded.required,
                      source_config_json = excluded.source_config_json
                    """,
                    (
                        config.project.namespace,
                        source.name,
                        int(source.enabled),
                        int(source.required),
                        _canonical_json(source.fields),
                    ),
                )

    def ingest_search_performance(
        self,
        run: CollectionRun,
        request: SourceRequest,
        artifacts: list[RawArtifact],
        observations: list[SearchPerformanceObservation],
    ) -> None:
        self.bootstrap()
        _validate_batch(run, request, artifacts, observations, self.observer_home)
        with self.connect() as con:
            if any(obs.source == "google_search_console" and obs.normalizer_version == "gsc-v2" for obs in observations):
                legacy = con.execute(
                    "SELECT 1 FROM search_performance WHERE project_id = ? "
                    "AND source = 'google_search_console' AND is_current = 1 "
                    "AND normalizer_version = 'gsc-v1' LIMIT 1", (run.project_id,),
                ).fetchone()
                if legacy:
                    raise StorageError(
                        "GSC_LEGACY_REBUILD_REQUIRED: preserve the existing database and "
                        "recollect into a clean observer home before importing gsc-v2."
                    )
            _insert_run(con, run)
            _insert_request(con, request)
            for artifact in artifacts:
                _insert_artifact(con, artifact)
            for obs in observations:
                _insert_search_observation(con, obs)

    def ingest_traffic_metrics(
        self,
        run: CollectionRun,
        request: SourceRequest,
        artifacts: list[RawArtifact],
        observations: list[TrafficMetricObservation],
    ) -> None:
        self.bootstrap()
        _validate_traffic_batch(run, request, artifacts, observations, self.observer_home)
        with self.connect() as con:
            _insert_run(con, run)
            _insert_request(con, request)
            for artifact in artifacts:
                _insert_artifact(con, artifact)
            for obs in observations:
                _insert_traffic_metric(con, obs)

    def ingest_crawl_pages(
        self,
        run: CollectionRun,
        request: SourceRequest,
        artifacts: list[RawArtifact],
        observations: list[CrawlPageObservation],
    ) -> None:
        self.bootstrap()
        _validate_crawl_batch(run, request, artifacts, observations, self.observer_home)
        with self.connect() as con:
            _insert_run(con, run)
            _insert_request(con, request)
            for artifact in artifacts:
                _insert_artifact(con, artifact)
            for obs in observations:
                _insert_crawl_page(con, obs)

    def update_collection_run_status(self, run_id: str, status: str, *, finished_at: str | None = None) -> None:
        if status not in {"running", "complete", "partial", "failed"}:
            raise StorageError(f"Invalid collection run status: {status}")
        with self.connect() as con:
            con.execute(
                """
                UPDATE collection_runs
                SET status = ?, finished_at = COALESCE(?, finished_at)
                WHERE run_id = ?
                """,
                (status, finished_at, run_id),
            )

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        with self.connect() as con:
            row = con.execute(sql, params).fetchone()
            if row is None:
                raise StorageError("Query returned no rows.")
            return dict(row)

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]


def _load_migrations() -> list[tuple[int, str]]:
    migration_root = resources.files("seo_observer").joinpath("migrations")
    migrations: list[tuple[int, str]] = []
    for item in sorted(migration_root.iterdir(), key=lambda candidate: candidate.name):
        if not item.name.endswith(".sql"):
            continue
        prefix = item.name.split("_", 1)[0]
        if not prefix.isdigit():
            raise StorageError(f"Migration file has no numeric prefix: {item.name}")
        migrations.append((int(prefix), item.read_text(encoding="utf-8")))
    return migrations


def _current_schema_version(con: sqlite3.Connection) -> int:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
    ).fetchone()
    if exists is None:
        return 0
    row = con.execute("SELECT MAX(db_schema_version) AS version FROM schema_meta").fetchone()
    return int(row["version"] or 0)


def _ensure_compatible_v1_schema(con: sqlite3.Connection) -> None:
    con.executescript(COMPETITOR_TABLES_SQL)
    con.executescript(CRAWL_PAGES_TABLE_SQL)
    _ensure_actions_v1_schema(con)
    traffic_columns = _table_columns(con, "traffic_metrics")
    if not traffic_columns:
        return
    for column_name, ddl in {
        "effective_instant_start": "TEXT",
        "effective_instant_end": "TEXT",
        "observed_at": "TEXT",
        "reporting_period_id": "TEXT",
        "supersedes_fact_id": "INTEGER",
        "superseded_by_fact_id": "INTEGER",
    }.items():
        if column_name not in traffic_columns:
            con.execute(f"ALTER TABLE traffic_metrics ADD COLUMN {column_name} {ddl}")


def _replay_v1_migration(con: sqlite3.Connection) -> None:
    """Backfill missing v1 schema tables into a database already marked as version 1."""
    migrations = _load_migrations()
    for version, sql in migrations:
        if version == 1:
            con.executescript(sql)
            return
    raise StorageError("Storage migration 001 not found; cannot repair a v1 database.")


def _ensure_actions_v1_schema(con: sqlite3.Connection) -> None:
    actions_columns = _table_columns(con, "seo_actions")
    if not actions_columns:
        # A version-1 database created before the action journal existed: `bootstrap()`
        # skips the migration (version is already 1), and `actions add` fails with
        # `no such table: seo_actions`. The whole migration is idempotent (all CREATE
        # statements use IF NOT EXISTS), so missing tables are backfilled by the same
        # migration: a separate DDL copy would drift from it.
        _replay_v1_migration(con)
        return
    pk_cols = [
        str(row["name"])
        for row in con.execute("PRAGMA table_info(seo_actions)").fetchall()
        if row["pk"] > 0
    ]
    # Repair proceeds per table INDEPENDENTLY. `executescript()` implicitly commits the
    # current transaction, so each table rebuild is committed separately: an interruption
    # (crash, OOM, timeout) between them leaves `seo_actions` with the new PK while its
    # children keep the old schema. While this whole block hung on a single `seo_actions`
    # PK check, the next `bootstrap()` considered the database repaired and moved on, and
    # `actions add` failed with `no such column: action_revision_hash` — permanently
    # (review comment on #2305).
    con.execute("PRAGMA foreign_keys = OFF")
    if "action_revision_hash" not in pk_cols:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS new_seo_actions (
              action_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              changed_at TEXT NOT NULL,
              action_type TEXT NOT NULL,
              description TEXT NOT NULL,
              hypothesis_id TEXT NOT NULL,
              evidence_ref TEXT NOT NULL,
              lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('planned', 'active', 'done', 'tombstoned')),
              action_revision_hash TEXT NOT NULL,
              supersedes_action_revision_hash TEXT,
              PRIMARY KEY (action_id, action_revision_hash),
              FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
            );

            INSERT INTO new_seo_actions(
              action_id, project_id, changed_at, action_type, description, hypothesis_id,
              evidence_ref, lifecycle_state, action_revision_hash, supersedes_action_revision_hash
            )
            SELECT action_id, project_id, changed_at, action_type, description, hypothesis_id,
                   evidence_ref, lifecycle_state, action_revision_hash, supersedes_action_revision_hash
            FROM seo_actions;

            DROP TABLE seo_actions;
            ALTER TABLE new_seo_actions RENAME TO seo_actions;
            """
        )
    target_columns = _table_columns(con, "action_targets")
    if target_columns and "action_revision_hash" not in target_columns:
        con.executescript(
            """
            CREATE TABLE new_action_targets (
              target_id INTEGER PRIMARY KEY AUTOINCREMENT,
              action_id TEXT NOT NULL,
              action_revision_hash TEXT NOT NULL,
              target_type TEXT NOT NULL CHECK (target_type IN ('property', 'url', 'query', 'keyword_set', 'segment')),
              target_value TEXT NOT NULL,
              target_role TEXT NOT NULL CHECK (target_role IN ('primary', 'guardrail')),
              FOREIGN KEY (action_id, action_revision_hash)
                REFERENCES seo_actions(action_id, action_revision_hash) ON DELETE RESTRICT
            );

            INSERT INTO new_action_targets(target_id, action_id, action_revision_hash, target_type, target_value, target_role)
            SELECT t.target_id, t.action_id, a.action_revision_hash, t.target_type, t.target_value, t.target_role
            FROM action_targets t
            JOIN seo_actions a ON t.action_id = a.action_id;

            DROP TABLE action_targets;
            ALTER TABLE new_action_targets RENAME TO action_targets;
            """
        )
    elif not target_columns:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_targets (
              target_id INTEGER PRIMARY KEY AUTOINCREMENT,
              action_id TEXT NOT NULL,
              action_revision_hash TEXT NOT NULL,
              target_type TEXT NOT NULL CHECK (target_type IN ('property', 'url', 'query', 'keyword_set', 'segment')),
              target_value TEXT NOT NULL,
              target_role TEXT NOT NULL CHECK (target_role IN ('primary', 'guardrail')),
              FOREIGN KEY (action_id, action_revision_hash)
                REFERENCES seo_actions(action_id, action_revision_hash) ON DELETE RESTRICT
            );
            """
        )
    window_columns = _table_columns(con, "measurement_windows")
    if window_columns and "action_revision_hash" not in window_columns:
        con.executescript(
            """
            CREATE TABLE new_measurement_windows (
              window_id TEXT NOT NULL,
              action_id TEXT NOT NULL,
              action_revision_hash TEXT NOT NULL,
              baseline_start TEXT NOT NULL,
              baseline_end TEXT NOT NULL,
              observation_start TEXT NOT NULL,
              observation_end TEXT NOT NULL,
              timezone TEXT NOT NULL,
              comparison_strategy TEXT NOT NULL,
              minimum_denominator INTEGER,
              earliest_evaluation_date TEXT NOT NULL,
              confounder_notes TEXT NOT NULL DEFAULT '',
              PRIMARY KEY (window_id, action_revision_hash),
              FOREIGN KEY (action_id, action_revision_hash)
                REFERENCES seo_actions(action_id, action_revision_hash) ON DELETE RESTRICT
            );

            INSERT INTO new_measurement_windows(
              window_id, action_id, action_revision_hash, baseline_start, baseline_end,
              observation_start, observation_end, timezone, comparison_strategy,
              minimum_denominator, earliest_evaluation_date, confounder_notes
            )
            SELECT w.window_id, w.action_id, a.action_revision_hash, w.baseline_start, w.baseline_end,
                   w.observation_start, w.observation_end, w.timezone, w.comparison_strategy,
                   w.minimum_denominator, w.earliest_evaluation_date, w.confounder_notes
            FROM measurement_windows w
            JOIN seo_actions a ON w.action_id = a.action_id;

            DROP TABLE measurement_windows;
            ALTER TABLE new_measurement_windows RENAME TO measurement_windows;
            """
        )
    elif not window_columns:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS measurement_windows (
              window_id TEXT NOT NULL,
              action_id TEXT NOT NULL,
              action_revision_hash TEXT NOT NULL,
              baseline_start TEXT NOT NULL,
              baseline_end TEXT NOT NULL,
              observation_start TEXT NOT NULL,
              observation_end TEXT NOT NULL,
              timezone TEXT NOT NULL,
              comparison_strategy TEXT NOT NULL,
              minimum_denominator INTEGER,
              earliest_evaluation_date TEXT NOT NULL,
              confounder_notes TEXT NOT NULL DEFAULT '',
              PRIMARY KEY (window_id, action_revision_hash),
              FOREIGN KEY (action_id, action_revision_hash)
                REFERENCES seo_actions(action_id, action_revision_hash) ON DELETE RESTRICT
            );
            """
        )
    verdict_columns = _table_columns(con, "action_verdicts")
    if verdict_columns and "action_revision_hash" not in verdict_columns:
        con.executescript(
            """
            CREATE TABLE new_action_verdicts (
              verdict_id TEXT PRIMARY KEY,
              action_id TEXT NOT NULL,
              action_revision_hash TEXT NOT NULL,
              window_id TEXT NOT NULL,
              verdict TEXT NOT NULL CHECK (verdict IN ('positive', 'negative', 'inconclusive', 'not_ready')),
              absolute_delta REAL,
              relative_delta REAL,
              input_derived_metric_ids_json TEXT NOT NULL DEFAULT '[]',
              input_fact_set_hash TEXT NOT NULL,
              evidence_set_hash TEXT NOT NULL,
              historical_snapshot_hashes_json TEXT NOT NULL DEFAULT '[]',
              comparability_state TEXT NOT NULL,
              evaluation_policy_version TEXT NOT NULL,
              evaluation_as_of TEXT NOT NULL,
              supersedes_verdict_id TEXT,
              explanation TEXT NOT NULL,
              FOREIGN KEY (action_id, action_revision_hash)
                REFERENCES seo_actions(action_id, action_revision_hash) ON DELETE RESTRICT,
              FOREIGN KEY (window_id, action_revision_hash)
                REFERENCES measurement_windows(window_id, action_revision_hash) ON DELETE RESTRICT,
              FOREIGN KEY (supersedes_verdict_id) REFERENCES action_verdicts(verdict_id) ON DELETE RESTRICT
            );

            INSERT INTO new_action_verdicts(
              verdict_id, action_id, action_revision_hash, window_id, verdict,
              absolute_delta, relative_delta, input_derived_metric_ids_json,
              input_fact_set_hash, evidence_set_hash, historical_snapshot_hashes_json,
              comparability_state, evaluation_policy_version, evaluation_as_of,
              supersedes_verdict_id, explanation
            )
            SELECT v.verdict_id, v.action_id, a.action_revision_hash, v.window_id, v.verdict,
                   v.absolute_delta, v.relative_delta, v.input_derived_metric_ids_json,
                   v.input_fact_set_hash, v.evidence_set_hash, v.historical_snapshot_hashes_json,
                   v.comparability_state, v.evaluation_policy_version, v.evaluation_as_of,
                   v.supersedes_verdict_id, v.explanation
            FROM action_verdicts v
            JOIN seo_actions a ON v.action_id = a.action_id;

            DROP TABLE action_verdicts;
            ALTER TABLE new_action_verdicts RENAME TO action_verdicts;
            """
        )
    elif not verdict_columns:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_verdicts (
              verdict_id TEXT PRIMARY KEY,
              action_id TEXT NOT NULL,
              action_revision_hash TEXT NOT NULL,
              window_id TEXT NOT NULL,
              verdict TEXT NOT NULL CHECK (verdict IN ('positive', 'negative', 'inconclusive', 'not_ready')),
              absolute_delta REAL,
              relative_delta REAL,
              input_derived_metric_ids_json TEXT NOT NULL DEFAULT '[]',
              input_fact_set_hash TEXT NOT NULL,
              evidence_set_hash TEXT NOT NULL,
              historical_snapshot_hashes_json TEXT NOT NULL DEFAULT '[]',
              comparability_state TEXT NOT NULL,
              evaluation_policy_version TEXT NOT NULL,
              evaluation_as_of TEXT NOT NULL,
              supersedes_verdict_id TEXT,
              explanation TEXT NOT NULL,
              FOREIGN KEY (action_id, action_revision_hash)
                REFERENCES seo_actions(action_id, action_revision_hash) ON DELETE RESTRICT,
              FOREIGN KEY (window_id, action_revision_hash)
                REFERENCES measurement_windows(window_id, action_revision_hash) ON DELETE RESTRICT,
              FOREIGN KEY (supersedes_verdict_id) REFERENCES action_verdicts(verdict_id) ON DELETE RESTRICT
            );
            """
        )
    con.execute("PRAGMA foreign_keys = ON")


def _table_columns(con: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in con.execute(f"PRAGMA table_info({table_name})")}


def _validate_batch(
    run: CollectionRun,
    request: SourceRequest,
    artifacts: list[RawArtifact],
    observations: list[SearchPerformanceObservation],
    home: Path,
) -> None:
    if request.run_id != run.run_id:
        raise StorageError("Request run_id does not match the collection run.")
    attempt = request.collection_attempt_key
    logical = request.logical_observation_key
    if logical == attempt:
        raise StorageError("Logical observation key and collection attempt key must differ.")
    artifact_ids = {artifact.artifact_id for artifact in artifacts}
    for artifact in artifacts:
        if artifact.request_id != request.request_id:
            raise StorageError("Artifact request_id does not match the source request.")
        resolve_raw_artifact_path(
            observer_home=home,
            project_id=run.project_id,
            relative_path=artifact.relative_path,
        )
    for obs in observations:
        if obs.project_id != run.project_id:
            raise StorageError("Observation project_id does not match the collection run.")
        if obs.request_id != request.request_id:
            raise StorageError("Observation request_id does not match the source request.")
        if obs.collection_attempt_key != attempt:
            raise StorageError("Observation collection_attempt_key does not match the request.")
        if obs.logical_observation_key != logical:
            raise StorageError("Observation logical_observation_key does not match the request.")
        if obs.artifact_id not in artifact_ids:
            raise StorageError("Observation artifact_id was not included in the artifact batch.")
        if obs.impressions < 0 or obs.clicks < 0:
            raise StorageError("Search performance counts must be non-negative.")
        if obs.ctr < 0:
            raise StorageError("CTR must be non-negative.")


def _validate_traffic_batch(
    run: CollectionRun,
    request: SourceRequest,
    artifacts: list[RawArtifact],
    observations: list[TrafficMetricObservation],
    home: Path,
) -> None:
    if request.run_id != run.run_id:
        raise StorageError("Request run_id does not match the collection run.")
    attempt = request.collection_attempt_key
    logical = request.logical_observation_key
    if logical == attempt:
        raise StorageError("Logical observation key and collection attempt key must differ.")
    artifact_ids = {artifact.artifact_id for artifact in artifacts}
    for artifact in artifacts:
        if artifact.request_id != request.request_id:
            raise StorageError("Artifact request_id does not match the source request.")
        resolve_raw_artifact_path(
            observer_home=home,
            project_id=run.project_id,
            relative_path=artifact.relative_path,
        )
    for obs in observations:
        if obs.project_id != run.project_id:
            raise StorageError("Observation project_id does not match the collection run.")
        if obs.request_id != request.request_id:
            raise StorageError("Observation request_id does not match the source request.")
        if obs.collection_attempt_key != attempt:
            raise StorageError("Observation collection_attempt_key does not match the request.")
        if obs.logical_observation_key != logical:
            raise StorageError("Observation logical_observation_key does not match the request.")
        if obs.artifact_id not in artifact_ids:
            raise StorageError("Observation artifact_id was not included in the artifact batch.")
        for metric_name in ("visits", "users", "pageviews"):
            value = getattr(obs, metric_name)
            if value is not None and value < 0:
                raise StorageError("Traffic metric counts must be non-negative.")
        if obs.bounce_rate is not None and obs.bounce_rate < 0:
            raise StorageError("Bounce rate must be non-negative.")
        if obs.avg_visit_duration_seconds is not None and obs.avg_visit_duration_seconds < 0:
            raise StorageError("Average visit duration must be non-negative.")


def _validate_crawl_batch(
    run: CollectionRun,
    request: SourceRequest,
    artifacts: list[RawArtifact],
    observations: list[CrawlPageObservation],
    home: Path,
) -> None:
    if request.run_id != run.run_id:
        raise StorageError("Request run_id does not match the collection run.")
    if request.source != "local_crawl":
        raise StorageError("Crawl page requests must use source=local_crawl.")
    artifact_ids = {artifact.artifact_id for artifact in artifacts}
    for artifact in artifacts:
        if artifact.request_id != request.request_id:
            raise StorageError("Artifact request_id does not match the source request.")
        resolve_raw_artifact_path(
            observer_home=home,
            project_id=run.project_id,
            relative_path=artifact.relative_path,
        )
    for obs in observations:
        if obs.project_id != run.project_id:
            raise StorageError("Observation project_id does not match the collection run.")
        if obs.request_id != request.request_id:
            raise StorageError("Observation request_id does not match the source request.")
        attempt = request.collection_attempt_key
        crawled = obs.crawled_url
        if obs.collection_attempt_key != attempt:
            raise StorageError("Observation collection_attempt_key does not match the request.")
        if obs.logical_observation_key != crawled:
            raise StorageError("Crawl page logical_observation_key must equal the crawled URL.")
        if obs.source != "local_crawl":
            raise StorageError("Crawl page observations must use source=local_crawl.")
        if obs.artifact_id not in artifact_ids:
            raise StorageError("Observation artifact_id was not included in the artifact batch.")
        if obs.depth < 0 or obs.byte_size < 0 or obs.h1_count < 0:
            raise StorageError("Crawl page numeric fields must be non-negative.")


def _insert_run(con: sqlite3.Connection, run: CollectionRun) -> None:
    con.execute(
        """
        INSERT INTO collection_runs(
          run_id, project_id, period_start, period_end, timezone, started_at, finished_at,
          status, config_hash, cli_version, db_schema_version, config_schema_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO NOTHING
        """,
        (
            run.run_id,
            run.project_id,
            run.period_start,
            run.period_end,
            run.timezone,
            run.started_at,
            run.finished_at,
            run.status,
            run.config_hash,
            run.cli_version,
            SCHEMA_VERSION,
            run.config_schema_version,
        ),
    )


def _insert_request(con: sqlite3.Connection, request: SourceRequest) -> None:
    con.execute(
        """
        INSERT INTO source_requests(
          request_id, run_id, source, property_id, logical_observation_key,
          collection_attempt_key, request_descriptor_json, attempt, queried_at,
          completed_at, transport_status, freshness, sampled, sample_share,
          data_lag_seconds, row_limit, rows_received, pages_expected, pages_received,
          error_code, error_summary
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.request_id,
            request.run_id,
            request.source,
            request.property_id or RESERVED_ALL,
            request.logical_observation_key,
            request.collection_attempt_key,
            _canonical_json(request.request_descriptor),
            request.attempt,
            request.queried_at,
            request.completed_at,
            request.transport_status,
            request.freshness,
            int(request.sampled),
            request.sample_share,
            request.data_lag_seconds,
            request.row_limit,
            request.rows_received,
            request.pages_expected,
            request.pages_received,
            request.error_code,
            request.error_summary,
        ),
    )


def _insert_artifact(con: sqlite3.Connection, artifact: RawArtifact) -> None:
    con.execute(
        """
        INSERT INTO raw_artifacts(
          artifact_id, request_id, relative_path, sha256, content_type, compression,
          redaction_state, byte_size
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact.artifact_id,
            artifact.request_id,
            artifact.relative_path,
            artifact.sha256,
            artifact.content_type,
            artifact.compression,
            artifact.redaction_state,
            artifact.byte_size,
        ),
    )


def _insert_search_observation(
    con: sqlite3.Connection,
    obs: SearchPerformanceObservation,
) -> None:
    row = _search_row(obs)
    current = con.execute(
        """
        SELECT fact_id FROM search_performance
        WHERE is_current = 1
          AND logical_observation_key = ?
          AND effective_start = ?
          AND effective_end = ?
          AND query_id = ?
          AND page_id = ?
          AND search_engine = ?
          AND device = ?
          AND country = ?
          AND region = ?
          AND segment_id = ?
        """,
        (
            row["logical_observation_key"],
            row["effective_start"],
            row["effective_end"],
            row["query_id"],
            row["page_id"],
            row["search_engine"],
            row["device"],
            row["country"],
            row["region"],
            row["segment_id"],
        ),
    ).fetchone()
    supersedes_fact_id = int(current["fact_id"]) if current else None
    content_hash = _canonical_hash(row)
    cursor = con.execute(
        """
        INSERT INTO search_performance(
          project_id, property_id, source, effective_start, effective_end, source_timezone,
          effective_instant_start, effective_instant_end, observed_at, reporting_period_id,
          request_id, artifact_id, logical_observation_key, collection_attempt_key,
          query_id, query_text, page_id, page_url, search_engine, device, country, region,
          segment_id, impressions, clicks, ctr, average_position, dataset_coverage,
          sampled, sample_share, freshness, comparability, fact_schema_version,
          normalizer_version, is_current, supersedes_fact_id, content_hash
        )
        VALUES (
          :project_id, :property_id, :source, :effective_start, :effective_end, :source_timezone,
          :effective_instant_start, :effective_instant_end, :observed_at, :reporting_period_id,
          :request_id, :artifact_id, :logical_observation_key, :collection_attempt_key,
          :query_id, :query_text, :page_id, :page_url, :search_engine, :device, :country, :region,
          :segment_id, :impressions, :clicks, :ctr, :average_position, :dataset_coverage,
          :sampled, :sample_share, :freshness, :comparability, :fact_schema_version,
          :normalizer_version, :is_current, :supersedes_fact_id, :content_hash
        )
        """,
        {
            **row,
            "is_current": 0 if supersedes_fact_id is not None else 1,
            "supersedes_fact_id": supersedes_fact_id,
            "content_hash": content_hash,
        },
    )
    new_fact_id = int(cursor.lastrowid)
    if supersedes_fact_id is not None:
        con.execute(
            """
            UPDATE search_performance
            SET is_current = 0, superseded_by_fact_id = ?
            WHERE fact_id = ?
            """,
            (new_fact_id, supersedes_fact_id),
        )
        con.execute(
            "UPDATE search_performance SET is_current = 1 WHERE fact_id = ?",
            (new_fact_id,),
        )


def _insert_traffic_metric(con: sqlite3.Connection, obs: TrafficMetricObservation) -> None:
    row = _traffic_row(obs)
    current = con.execute(
        """
        SELECT fact_id FROM traffic_metrics
        WHERE is_current = 1
          AND logical_observation_key = ?
          AND effective_start = ?
          AND effective_end = ?
          AND channel = ?
          AND search_engine = ?
          AND landing_page_id = ?
          AND device = ?
          AND region = ?
          AND attribution_model = ?
        """,
        (
            row["logical_observation_key"],
            row["effective_start"],
            row["effective_end"],
            row["channel"],
            row["search_engine"],
            row["landing_page_id"],
            row["device"],
            row["region"],
            row["attribution_model"],
        ),
    ).fetchone()
    supersedes_fact_id = int(current["fact_id"]) if current else None
    cursor = con.execute(
        """
        INSERT INTO traffic_metrics(
          project_id, property_id, source, effective_start, effective_end, source_timezone,
          effective_instant_start, effective_instant_end, observed_at, reporting_period_id,
          request_id, artifact_id, logical_observation_key, collection_attempt_key,
          channel, search_engine, landing_page_id, device, region, attribution_model,
          visits, users, pageviews, bounce_rate, avg_visit_duration_seconds,
          dataset_coverage, freshness, comparability, fact_schema_version,
          normalizer_version, is_current, supersedes_fact_id
        )
        VALUES (
          :project_id, :property_id, :source, :effective_start, :effective_end, :source_timezone,
          :effective_instant_start, :effective_instant_end, :observed_at, :reporting_period_id,
          :request_id, :artifact_id, :logical_observation_key, :collection_attempt_key,
          :channel, :search_engine, :landing_page_id, :device, :region, :attribution_model,
          :visits, :users, :pageviews, :bounce_rate, :avg_visit_duration_seconds,
          :dataset_coverage, :freshness, :comparability, :fact_schema_version,
          :normalizer_version, :is_current, :supersedes_fact_id
        )
        """,
        {
            **row,
            "is_current": 0 if supersedes_fact_id is not None else 1,
            "supersedes_fact_id": supersedes_fact_id,
        },
    )
    new_fact_id = int(cursor.lastrowid)
    if supersedes_fact_id is not None:
        con.execute(
            """
            UPDATE traffic_metrics
            SET is_current = 0, superseded_by_fact_id = ?
            WHERE fact_id = ?
            """,
            (new_fact_id, supersedes_fact_id),
        )
        con.execute(
            "UPDATE traffic_metrics SET is_current = 1 WHERE fact_id = ?",
            (new_fact_id,),
        )


def _insert_crawl_page(con: sqlite3.Connection, obs: CrawlPageObservation) -> None:
    row = _crawl_page_row(obs)
    current = con.execute(
        """
        SELECT fact_id FROM crawl_pages
        WHERE is_current = 1
          AND project_id = ?
          AND property_id = ?
          AND logical_observation_key = ?
        """,
        (
            row["project_id"],
            row["property_id"],
            row["logical_observation_key"],
        ),
    ).fetchone()
    supersedes_fact_id = int(current["fact_id"]) if current else None
    content_hash = _canonical_hash(row)
    cursor = con.execute(
        """
        INSERT INTO crawl_pages(
          project_id, property_id, source, effective_at, source_timezone,
          request_id, artifact_id, logical_observation_key, collection_attempt_key,
          crawled_url, final_url, depth, fetch_status, robots_status, content_type,
          byte_size, title, meta_description, h1_text, h1_count, canonical_url,
          header_canonical_url, meta_robots, x_robots_tag, hreflang_json,
          redirect_chain_json, internal_links_json, error, dataset_coverage,
          freshness, comparability, fact_schema_version, normalizer_version,
          is_current, supersedes_fact_id, content_hash
        )
        VALUES (
          :project_id, :property_id, :source, :effective_at, :source_timezone,
          :request_id, :artifact_id, :logical_observation_key, :collection_attempt_key,
          :crawled_url, :final_url, :depth, :fetch_status, :robots_status, :content_type,
          :byte_size, :title, :meta_description, :h1_text, :h1_count, :canonical_url,
          :header_canonical_url, :meta_robots, :x_robots_tag, :hreflang_json,
          :redirect_chain_json, :internal_links_json, :error, :dataset_coverage,
          :freshness, :comparability, :fact_schema_version, :normalizer_version,
          :is_current, :supersedes_fact_id, :content_hash
        )
        """,
        {
            **row,
            "is_current": 0 if supersedes_fact_id is not None else 1,
            "supersedes_fact_id": supersedes_fact_id,
            "content_hash": content_hash,
        },
    )
    new_fact_id = int(cursor.lastrowid)
    if supersedes_fact_id is not None:
        con.execute(
            """
            UPDATE crawl_pages
            SET is_current = 0, superseded_by_fact_id = ?
            WHERE fact_id = ?
            """,
            (new_fact_id, supersedes_fact_id),
        )
        con.execute("UPDATE crawl_pages SET is_current = 1 WHERE fact_id = ?", (new_fact_id,))


def _search_row(obs: SearchPerformanceObservation) -> dict[str, Any]:
    row = asdict(obs)
    for field in ("device", "country", "region", "segment_id"):
        row[field] = row[field] or RESERVED_ALL
    row["sampled"] = int(row["sampled"])
    return row


def _traffic_row(obs: TrafficMetricObservation) -> dict[str, Any]:
    row = asdict(obs)
    for field in ("channel", "search_engine", "landing_page_id", "device", "region"):
        row[field] = row[field] or RESERVED_ALL
    row["effective_instant_start"] = (
        row["effective_instant_start"] or f"{row['effective_start']}T00:00:00Z"
    )
    row["effective_instant_end"] = (
        row["effective_instant_end"] or f"{row['effective_end']}T23:59:59Z"
    )
    row["observed_at"] = row["observed_at"] or row["effective_instant_end"]
    row["reporting_period_id"] = (
        row["reporting_period_id"] or f"{row['effective_start']}..{row['effective_end']}"
    )
    return row


def _crawl_page_row(obs: CrawlPageObservation) -> dict[str, Any]:
    return asdict(obs)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
