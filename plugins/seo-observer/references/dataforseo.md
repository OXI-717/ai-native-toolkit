# DataForSEO Provider Boundary

Last verified: 2026-07-30.

Fixture capture date: 2026-07-30. Synthetic provider-shaped fixtures live in
the CLI test suite (`cli/tests/fixtures/dataforseo` in the source repository).

## Endpoint Family

The adapter targets DataForSEO API v3 live endpoints:

- `POST /v3/dataforseo_labs/google/competitors_domain/live`
- `POST /v3/dataforseo_labs/google/domain_intersection/live`
- `POST /v3/dataforseo_labs/google/ranked_keywords/live`
- `POST /v3/serp/google/organic/live/advanced`
- `GET /v3/user_data`

`/v3/user_data` is available only for explicit paid live preflight flows. The
doctor command must keep using local readiness checks and must not call this
endpoint.

## Adapter Contract

`seo_observer.dataforseo.DataForSEOAdapter` is unit-testable with an injected
transport. It returns dictionaries containing `collection`, `provider`,
`endpoint`, sanitized `request`, `rows`, `metadata`, `cost`, `quality`, and
`errors`.

The sanitized request envelope records method, endpoint path, payload shape, and
redacted payload values where a caller supplied secret-shaped fields. It does
not serialize transport headers.

Provider request IDs, provider status codes, response-level cost, task-level
cost, malformed-row counts, duplicate-row counts, and no-result task counts are
preserved in response metadata.

## Live Paid Guard

Live DataForSEO calls require all of:

- `provider_mode = live`
- caller-provided paid-call confirmation
- configured per-run budget
- valid local credential pair in the configured credential environment variable

`BudgetGuard` fails closed before the first transport call when live mode,
paid-call confirmation, or budget config is missing. It records provider-reported
runtime cost and stops later calls when observed cost would exceed the run
budget.

## Response Quality

Normal provider rows return `quality = live`. Missing task lists, task-level
provider errors, no-result tasks, or malformed rows return `quality = partial`
with structured errors or metadata counts while preserving usable rows.

Authentication readiness is different: missing or invalid local credentials
produce `DATAFORSEO_AUTH_NOT_READY` and no transport call.
