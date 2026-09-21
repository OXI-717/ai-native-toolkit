# seo-observer Yandex Webmaster adapter

`seo_observer.webmaster` is a local, fixture-testable adapter foundation for
Yandex Webmaster API v4. It does not discover live hosts, read production
credentials, schedule collection, submit recrawl requests, mutate sitemaps, or
take autonomous SEO actions.

Source context:

- API base resources are under `https://api.webmaster.yandex.net/v4`.
- `GET /v4/user` returns the user ID required by other resources.
- Host-scoped resources use `user-id` and a remote `host-id` string from
  `GET /v4/user/{user-id}/hosts`.
- Search query history endpoints are under
  `/user/{user-id}/hosts/{host-id}/search-queries/.../history`.
- Popular query, search query history, indexing history, SQI history, and
  recrawl queue endpoints have provider defaults when dates are omitted. This
  adapter rejects missing temporal boundaries instead of relying on defaults.
- Sitemap list resources are read-only here. User-added sitemap mutation
  endpoints are intentionally out of scope.
- Diagnostics and broken internal link resources are represented only when a
  fixture/live caller provides supported payloads; unsupported responses are
  marked unavailable.

## Config binding

Project config binds `source = "yandex_webmaster"` to properties with the
Yandex remote `host-id`, for example:

```toml
[sources.yandex_webmaster]
enabled = true
required = false
user_id = "42"
credential_env = "YANDEX_WEBMASTER_TOKEN"
finalize_after = "P3D"

[[source_bindings]]
property = "main"
source = "yandex_webmaster"
remote_id = "https:demo.example:443"
```

The config loader validates that Webmaster `remote_id` values look like
Yandex host IDs (`http:...:<port>` or `https:...:<port>`), not property URLs.
`doctor_webmaster_source(source_fields, bindings=..., env=...)` validates local
source shape, binding shape, and token environment presence without live calls.
`live_checked` is always `false` in the current implementation.

## Implemented request scopes

All temporal fetch methods require `WebmasterPeriod(date_from, date_to)`.
Passing `None` or an empty boundary raises `WebmasterRequestError`.

Implemented read scopes:

- popular query performance:
  `GET /v4/user/{user-id}/hosts/{host-id}/search-queries/popular`;
- query history:
  `GET /v4/user/{user-id}/hosts/{host-id}/search-queries/{query-id}/history`;
- indexing history:
  `GET /v4/user/{user-id}/hosts/{host-id}/indexing/history`;
- sitemap list:
  `GET /v4/user/{user-id}/hosts/{host-id}/sitemaps`;
- user-added sitemap list:
  `GET /v4/user/{user-id}/hosts/{host-id}/user-added-sitemaps`;
- diagnostics:
  `GET /v4/user/{user-id}/hosts/{host-id}/diagnostics`;
- broken internal link samples:
  `GET /v4/user/{user-id}/hosts/{host-id}/links/internal/broken/samples`;
- search URL event samples:
  `GET /v4/user/{user-id}/hosts/{host-id}/search-urls/events/samples`.

Popular query pagination uses 0-based `offset` and configured `limit` until
received rows reach response `count` or a short page is returned.
Search URL event sample pagination follows the same policy and clamps provider
page size to the documented 1-100 range.

## Intermediate observations

Search query methods return `collection = "search_performance"`. Fields are
mapped only when Yandex Webmaster semantics match storage semantics:

- `TOTAL_SHOWS` -> `impressions`;
- `TOTAL_CLICKS` -> `clicks`;
- `AVG_SHOW_POSITION` -> `average_position`;
- `ctr` is derived only when both impressions and clicks are present.

Yandex Webmaster query responses do not provide a landing page dimension in the
implemented scopes. The adapter therefore sets `page_id = "__all__"`,
`page_url = "__all__"`, and records `unavailable_dimensions = ["page"]`.
Missing indicators are represented in `unavailable_metrics`; they are not
silently converted to zero.

Indexing, sitemap, diagnostics, and broken-link methods return
`collection = "index_coverage"`. They are point-in-time observations with
`effective_at`; they do not use `effective_start`/`effective_end` interval
fields.

Sitemap observations preserve submitted URL and error counts when present.
Unsupported metrics such as sitemap indexed URL counts are `None` and listed in
`unavailable_metrics`, not represented as zero. Unsupported diagnostics or
broken-link fixture responses return `dataset_coverage = "unavailable"` and no
invented observations.

Search URL event samples preserve page-level evidence from Yandex Webmaster:
`event`, `excluded_url_status`, `bad_http_status`, `url`, `target_url`,
`event_date`, and `last_access`. The adapter maps `event` to `index_state` and
maps `excluded_url_status` to `problem_code` for removed-from-search samples, so
page exclusion reasons stay queryable without inventing aggregate counts.

## Coverage and freshness

The adapter emits deterministic metadata:

- `dataset_coverage = "complete"` when received rows cover the declared total;
- `dataset_coverage = "partial"` when rows are missing or malformed;
- `dataset_coverage = "empty"` for explicit empty payloads;
- `dataset_coverage = "unavailable"` for unsupported fixture responses.

Configured `finalize_after` marks search/index observations provisional for
future collectors. The current adapter does not write SQLite rows directly; a
collection layer can attach concrete run/request/artifact IDs before ingestion.
