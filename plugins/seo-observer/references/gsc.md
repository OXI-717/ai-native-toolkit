# seo-observer Google Search Console adapter

`seo_observer.gsc` is a local, fixture-testable adapter foundation for the
Google Search Console API. It does not discover live properties, read service
account secrets, schedule collection, call URL Inspection, submit or delete
sitemaps, crawl URLs, or take autonomous SEO actions.

Source context:

- Search Analytics request endpoint:
  `POST /webmasters/v3/sites/{siteUrl}/searchAnalytics/query`.
- Search Analytics requires explicit `startDate` and `endDate`; this adapter
  exposes them as `GSCPeriod(start_date, end_date)` and rejects missing
  temporal boundaries instead of relying on provider defaults.
- Google interprets Search Analytics dates and incomplete-data metadata in
  `America/Los_Angeles` / PT semantics. Adapter observations preserve
  `source_timezone = "America/Los_Angeles"` and keep the configured project
  timezone separately as `configured_timezone`.
- Supported dimensions are preserved exactly from the request:
  `query`, `page`, `country`, `device`, `searchAppearance`, `date`, and `hour`.
- `rowLimit` is validated locally in the documented range `1..25000`.
  Pagination uses zero-based `startRow`.
- Search Analytics exposes top rows, not guaranteed complete row coverage. The
  adapter marks non-empty Search Analytics payloads as `dataset_coverage =
  "top_rows"` and `comparability = "incomplete_top_rows"`.
- The all-your-data guide documents a maximum of 50K rows per day per search
  type and warns that page/query grouping can drop data. The adapter preserves
  this in `cap_metadata`, `top_rows`, `capped`, `coverage_warnings`, and
  `data_loss_risk` metadata.
- `searchAppearance` detail follows Google's two-step pattern: first query only
  `searchAppearance`, then run detail queries filtered by each returned
  appearance type.
- `dataState` values `final`, `all`, and `hourly_all` are passed through.
  Response metadata `first_incomplete_date` and `first_incomplete_hour` are
  preserved and mark freshness as `incomplete`.
- Sitemap reads are descriptor-only here:
  `GET /webmasters/v3/sites/{siteUrl}/sitemaps` and
  `GET /webmasters/v3/sites/{siteUrl}/sitemaps/{feedpath}`.
- Site `get` and `list` descriptors exist for future property binding/access
  validation. Live calls are not performed by doctor checks by default.

## Config binding

Project config binds `source = "google_search_console"` to URL-prefix Search
Console properties:

```toml
[sources.google_search_console]
enabled = true
required = true
credential_file_env = "GSC_SA_JSON_PATH"
finalize_after = "P3D"

[[source_bindings]]
property = "main"
source = "google_search_console"
remote_id = "https://demo.example/"
```

Only URL-prefix property URLs are accepted by local validation. Domain
properties such as `sc-domain:example.com` are intentionally rejected in the
current adapter scope.

`doctor_gsc_source(source_fields, bindings=..., env=...)` validates local source
shape, URL-prefix binding shape, and service-account credential-file env
presence without live API calls. It reports the env var name but does not read
or log credential file contents or the resolved file path.

`live_checked` is always `false` in the current implementation.

## Implemented request scopes

All temporal fetch methods require `GSCPeriod(start_date, end_date)`. Passing
`None` or an empty boundary raises `GSCRequestError`.

Implemented read scopes:

- search performance:
  `GSCAdapter.fetch_search_performance(period, query=SearchAnalyticsQuery(...))`;
- search appearance detail:
  `GSCAdapter.fetch_search_appearance_breakdown(period, detail_dimensions=...)`;
- sitemap list:
  `GSCAdapter.fetch_sitemaps(effective_at=...)`;
- sitemap get:
  `GSCAdapter.fetch_sitemap(feedpath, effective_at=...)`;
- local descriptors for Sites get/list validation:
  `get_site_descriptor()` and `list_sites_descriptor()`.

`SearchAnalyticsQuery` preserves request dimensions, filters, `type`,
`aggregationType`, `dataState`, and `startRow`. The source carries default
`row_limit`, `data_state`, property ID, configured timezone, service-account env
name, and optional injected access token for fixture transports.

## Intermediate observations

Search Analytics methods return `collection = "search_performance"`.

Rows are normalized as:

- `clicks` -> `clicks`;
- `impressions` -> `impressions`;
- `ctr` -> `ctr`;
- `position` -> `average_position`;
- requested `query`, `page`, `country`, `device`, `date`, `hour`, and
  `searchAppearance` keys are mapped by their exact request order.

The adapter sets:

- `source = "google_search_console"`;
- `search_engine = "google"`;
- `source_timezone = "America/Los_Angeles"`;
- missing optional dimensions to `__all__`;
- `unavailable_dimensions` for supported dimensions absent from the request;
- `unavailable_metrics` for absent metrics.

Sitemap methods return `collection = "index_coverage"` point observations with
`effective_at`. They preserve sitemap URL/path, type, pending/index flags,
submission/download timestamps, submitted URL count, indexed URL count when
present in sitemap `contents`, warning/error count, and explicitly record
`unavailable_dimensions = ["index_coverage_state"]`. They do not invent URL
Inspection or broader index coverage facts that the sitemap API does not
provide.

The adapter does not write SQLite rows directly. A collection layer can attach
concrete run/request/artifact IDs before storage ingestion.

## Coverage and freshness

Search Analytics metadata is deterministic:

- `dataset_coverage = "empty"` for explicit empty responses;
- `dataset_coverage = "top_rows"` for non-empty responses;
- `top_rows = true` because Search Analytics is not guaranteed full coverage;
- `capped` when local pagination reaches the configured max rows or documented
  50K daily per-search-type cap;
- `cap_metadata` preserves `row_limit`, `start_row`, `max_rows`,
  `daily_search_type_cap`, `top_rows`, and `capped`;
- `coverage_warnings` keeps comparisons from treating top-row data as complete
  population coverage;
- `data_loss_risk` marks page/query grouping as potentially lossy.

Configured `finalize_after` marks data provisional. Response metadata
`first_incomplete_date` or `first_incomplete_hour` upgrades freshness to
`incomplete` and is copied to both metadata and observations through the shared
metadata fields.
