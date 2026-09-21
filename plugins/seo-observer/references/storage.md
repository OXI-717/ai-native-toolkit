# seo-observer storage v1

Local storage is a deterministic SQLite evidence store. It does not call SEO
providers, schedule collection, render reports, or create production project
configs.

Default path:

```text
$SEO_OBSERVER_HOME/projects/<project>/observer.db
```

When `SEO_OBSERVER_HOME` is unset, the home is `~/.seo-observer`.
Raw artifact references resolve under:

```text
$SEO_OBSERVER_HOME/projects/<project>/raw/
```

The database stores raw artifact references and hashes, not raw payload blobs.
Live collect also writes the corresponding normalized/redacted raw artifact
files under the project raw root so `raw_artifacts.sha256` can be independently
verified.

Portable deterministic exports are documented separately in
`plugins/seo-observer/references/snapshots.md`. Snapshot v1 reads this
SQLite store and writes self-contained JSON without provider calls or scheduler
side effects.

Deterministic comparison and action measurement behavior is documented in
`plugins/seo-observer/references/comparisons.md` and
`plugins/seo-observer/references/actions.md`.
The local Yandex Metrica adapter emits deterministic intermediate observations
for `traffic_metrics` and `outcome_metrics`; visits/users remain analytics
traffic metrics and are not normalized into search impressions/clicks. The
adapter does not write SQLite rows directly or store live payloads by itself.
Its adapter contract is documented in
`plugins/seo-observer/references/metrica.md`.
The local Yandex Webmaster adapter emits deterministic intermediate
observations for `search_performance` and `index_coverage`; query metrics are
mapped only when Yandex fields match storage semantics, while missing page
dimensions and unsupported counts stay explicit metadata instead of zeros.
Indexing, sitemap, diagnostics, and broken-link records are point observations
with `effective_at` semantics. Its adapter contract is documented in
`plugins/seo-observer/references/webmaster.md`.
The local Google Search Console adapter emits deterministic intermediate
observations for `search_performance` and sitemap-backed `index_coverage`.
Search Analytics rows are marked as top-row coverage rather than complete
population coverage, preserve PT / `America/Los_Angeles` date semantics,
request dimensions, filters, type, aggregation, and dataState fields, plus response aggregation
type, capped/incomplete metadata, and page/query data-loss risk. Sitemap
observations preserve only facts exposed by the Sitemaps API and do not invent
URL Inspection or broader index coverage data. Its adapter contract is
documented in `plugins/seo-observer/references/gsc.md`.
The local Wordstat adapter emits deterministic intermediate observations for
`demand_metrics`, `related_queries`, and `demand_history`. Wordstat demand is
market-volume evidence, not search-console performance: it preserves requested
period, region, locale, language, device support, API family/version, full
request payload metadata, keyword-set hash identity, demand states
(`present`/`zero`/`missing`/`unavailable`), related-query provenance, optional
history buckets, and ambiguity caveats. Its adapter contract is documented in
`plugins/seo-observer/references/wordstat.md`.
The local SERP adapter emits deterministic intermediate observations for
`serp_results` and derived competitor metrics. SERP records are point
observations with `effective_at`; they preserve protocol hashes, keyword-set
hashes, query/region/locale/device/search-engine/API metadata, rank, URL,
normalized host/domain, optional title/snippet/features, source request ID, and
raw-result position provenance. Competitor metrics are derived from point
snapshots and mark insufficient coverage or unconfirmed movement explicitly. Its
adapter contract is documented in
`plugins/seo-observer/references/serp.md`.
The local aggregate outcome adapter emits deterministic intermediate
`outcome_metrics` facts from approved view descriptors only. It preserves
population, attribution, counting unit, dedupe, timestamp/timezone, source
request ID, period, and aggregation grain metadata required for safe
analytics/server reconciliation. Exact local counts may remain in local facts or
storage, while Git/Markdown/Telegram exports must use the privacy policy helpers
documented in `plugins/seo-observer/references/outcomes.md`.

## Migration contract

Migrations live in:

```text
plugins/seo-observer/cli/seo_observer/migrations/
```

`SEOStorage.bootstrap()` applies them in numeric filename order inside a
transaction, records `schema_meta.db_schema_version`, and is idempotent.
`schemas/storage_v1.json` is a schema snapshot used by tests to catch drift
between migrations and the documented contract.

## Evidence identity

Storage keeps two keys with different meanings:

- `logical_observation_key`: stable requested real-world scope.
- `collection_attempt_key`: local execution identity for one request attempt.

Attempts and raw artifacts are append-only. Re-ingesting the same logical
window inserts a new request and artifact record, inserts a new fact revision,
and atomically marks the previous winning fact revision as superseded.
`search_performance`, `keyword_demand`, `traffic_metrics`, and
`outcome_metrics` all carry temporal lineage fields (`observed_at`,
`reporting_period_id`, `effective_instant_start`, `effective_instant_end`) plus
fact supersession links (`supersedes_fact_id`, `superseded_by_fact_id`) so later
adapter writers can use the same revision contract instead of inventing table
specific identity rules.

Missing optional dimensions use the reserved `__all__` member. Fact natural keys
therefore remain non-null and unique in SQLite.

## v1 tables

The v1 migration covers:

- registry: `projects`, `properties`, `sources`;
- attempts: `collection_runs`, `source_requests`, `raw_artifacts`;
- quality and coverage: `metric_coverage`, `content_coverage`;
- observed facts: `search_performance`, `keyword_demand`, `serp_results`,
  `traffic_metrics`, `index_coverage`, `outcome_metrics`;
- derived facts: `competitor_metrics`, `derived_metrics`;
- actions and verdicts: `seo_actions`, `action_targets`,
  `measurement_windows`, `action_verdicts`;
- publication lineage: `snapshot_manifests`.

Foreign keys preserve evidence lineage with `ON DELETE RESTRICT`; parent rows
that would orphan evidence cannot be deleted.

Action revisions are keyed by `(action_id, action_revision_hash)`, not mutable
`action_id` alone. Targets, measurement windows, and verdicts carry the same
`action_revision_hash`, so verdict rows are stored against the exact action
revision that was evaluated. New verdict revisions insert new rows and use
`supersedes_verdict_id` for verdict lineage instead of overwriting prior
verdicts.
