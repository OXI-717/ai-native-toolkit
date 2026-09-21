# seo-observer Yandex Metrica adapter

`seo_observer.metrica` is a local, fixture-testable adapter foundation for
Yandex Metrica. It does not discover live counters, read production secrets,
schedule collection, or call the network unless a caller injects a transport and
explicitly invokes fetch methods.

Source context:

- Reports API table endpoint: `GET https://api-metrika.yandex.net/stat/v1/data`
- Reports API requests are built from explicit `date1`/`date2`, `metrics`,
  optional `dimensions`, `filters`, `limit`, `offset`, and `accuracy`.
- Management API owns counters and goals; this adapter only carries configured
  goal IDs and does not call Management API by default.
- Sampling is represented from response metadata and marks normalized coverage
  as `sampled`.
- Quota failures, including Reports API `420 Too Many Requests`, are transport
  concerns for the future live collector; the current adapter keeps fixture
  responses deterministic.

## Implemented request scopes

All temporal fetch methods require a `Period(date1, date2)`. Passing `None` or
an empty boundary raises `MetricaRequestError`; provider metrics cannot be
queried with implicit dates.

Implemented scopes:

- organic total traffic: `ym:s:visits,ym:s:users` with
  `ym:s:trafficSource=='organic'`;
- organic landing pages: the same metrics/filter plus
  `dimensions=ym:s:startURLPathFull`;
- organic device breakdown: the same metrics/filter plus
  `dimensions=ym:s:deviceCategory`;
- configured goal totals: one `ym:s:goal<id>reaches` metric per configured
  `MetricaGoal`.

Pagination uses 1-based `offset` and configured `limit`, fetching additional
pages until received rows reach `total_rows` or a short page is returned.

## Normalized observations

The adapter returns deterministic intermediate payloads suitable for storage
ingestion:

- traffic scopes return `collection = "traffic_metrics"`;
- goal scopes return `collection = "outcome_metrics"`.

Traffic observations preserve Yandex Metrica analytics semantics and do not map
`ym:s:visits` / `ym:s:users` into search impressions, clicks, CTR, or average
position. Traffic observations include:

- `channel = "organic"`;
- `search_engine = "__all__"` because the organic filter is a Metrica traffic
  source aggregate, not a dedicated search-engine dimension;
- `landing_page_id`;
- `device`;
- `region`;
- `attribution_model = "metrica_default"`;
- `visits`;
- `users`;
- optional unknown traffic fields as null: `pageviews`, `bounce_rate`,
  `avg_visit_duration_seconds`.

The payloads preserve provider population and attribution semantics:

- `source = "yandex_metrica"`;
- `source_request_id` is deterministic from counter, outcome, and period until a
  collection layer attaches concrete request IDs;
- `period_start` and `period_end`;
- `traffic_channel = "organic"` for goal totals;
- `search_engine = "__all__"` for goal totals because the configured goal
  totals use the same organic traffic aggregate;
- `population_id` and `population_name`;
- `population_scope = "organic_visits"`;
- `attribution_model = "metrica_default"`;
- `attribution_window` and `attribution_source`;
- `attribution_scope = "metrica_visit"`;
- `dedupe_key` and `dedupe_source`;
- `timestamp_field`;
- `timezone`;
- `aggregation_grain = "period"`;
- `attribution_level = "channel_aggregate"` for goal totals.

The adapter does not write SQLite rows directly. A collection layer can combine
these intermediate observations with concrete run/request/artifact IDs before
calling storage ingestion.

## Coverage, lag, and empty data

Response metadata is normalized into:

- `dataset_coverage = "complete"` when received rows cover `total_rows`;
- `dataset_coverage = "sampled"` when a page reports sampling;
- `dataset_coverage = "partial"` when fewer rows were received than
  `total_rows`;
- `dataset_coverage = "empty"` when the response is explicitly empty.

`sample_share`, `data_lag_seconds`, `rows_received`, `total_rows`,
`pages_received`, `freshness`, `comparability`, and configured
`finalize_after` are preserved. Lagged data is marked `freshness =
"provisional"`.

Zero goal totals are guarded: a zero result for a configured goal raises
`MetricaRequestError` unless that goal mapping has `zero_confirmed = true`.

## Doctor helper

`doctor_metrica_source(source_fields, env=...)` validates local config shape and
credential environment presence without live API calls. It checks:

- `counter_id`;
- `credential_env` shape;
- optional configured goal mapping shape;
- whether the named token environment variable is present.

`live_checked` is always `false` in the current implementation.

## Demo mapping placeholder

`DEMO_TEN_GOAL_MAPPING_FIXTURE` is a config/fixture-driven placeholder with ten
non-secret goal mappings. The IDs are not production secrets and do not imply a
live Demo configuration.
