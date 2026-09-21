# seo-observer Wordstat demand adapter

`seo_observer.wordstat` is a local, fixture-testable adapter foundation for
Yandex Wordstat demand evidence. It does not read live credentials, call a live
API by itself, automate a browser, scrape the Wordstat UI, bypass captcha, pay
for API calls, schedule collection, write production Demo config, or take
autonomous SEO actions.

Source context:

- Current Yandex AI Studio / Search API Wordstat documentation describes a
  synchronous query-statistics API.
- Yandex Wordstat UI/support describes query popularity by region/time and
  related searches.
- Legacy Yandex Direct Wordstat report concepts are represented only as
  explicit `api_family = "direct_wordstat_report"` metadata when a fixture
  response requires that family.

Third-party scraping libraries are not canonical behavior for this adapter.

## Implemented request scopes

All temporal fetch methods require `WordstatPeriod(date_from, date_to)`.
Passing `None` or an empty boundary raises `WordstatRequestError`; no temporal
or history request silently falls back to provider defaults.

The adapter builds deterministic request descriptors from:

- configured keyword-set ID and normalized keyword contents;
- requested regions as `id:name` values;
- keyword-set locale and source language;
- requested devices when the configured source declares `supports_devices`;
- explicit `date_from` / `date_to` and duplicated `period` metadata;
- explicit `api_family`, `api_version`, and full request payload.

Implemented fixture-backed methods:

- `fetch_demand(...)` returns `collection = "demand_metrics"`;
- `fetch_related(...)` returns `collection = "related_queries"`;
- `fetch_history(...)` returns `collection = "demand_history"`.

## Keyword-set identity and comparability

`keyword_set_hash(...)` computes `kwsha256:<digest>` from normalized keyword
contents: trimmed, whitespace-collapsed, case-folded, deduplicated, and sorted.
The hash is included in request payloads, metadata, observations, and logical
observation keys.

`compare_keyword_set_hashes(...)` returns `comparability =
"keyword_set_changed"` and `comparable = false` when a baseline/current hash
mismatch is detected. Consumers must not compare demand observations across an
unexpected keyword-set content change.

## Normalized observations

Demand observations are market-volume evidence, not Search Console performance.
They use `source = "yandex_wordstat"` and `search_engine = "yandex"` but are not
normalized into impressions, clicks, CTR, or average position.

Demand records preserve:

- keyword-set ID and hash;
- requested period;
- region ID/name;
- locale/language;
- device or `__all__` when unsupported by the configured API family;
- API family/version;
- provider request ID.

Demand state is explicit:

- positive values are `demand_state = "present"`;
- numeric zero is `demand_state = "zero"`;
- provider `status = "missing"` is `demand_state = "missing"`;
- absent unavailable demand is `demand_state = "unavailable"` with
  `unavailable_reason`.

## Related queries

Related/snowball fixture payloads retain:

- `source_keyword`;
- `related_query`;
- provider `relation_type` such as `same-query`, `related`,
  `searched-with`, or `searched-also`;
- requested region;
- source keyword-set hash;
- provenance request ID;
- rank/order when available.

Dedupe uses
`source_keyword+normalized_related_query+relation_type+region+device` and keeps
the first row by provider order.

## History and seasonality

History buckets are emitted only when the provider/fixture payload includes
`bucket_month` or `bucket_start`. Monthly `bucket_month` rows are marked
`yoy_suitable = true`; custom `bucket_start` / `bucket_end` interval rows are
preserved but marked not YoY-friendly by default. Metadata reports whether
history buckets were supported by the fixture payload and whether all emitted
buckets are YoY-suitable.

## Quality metadata

Broad, non-exact phrases and provider ambiguity flags are preserved as quality
metadata. Homonym or ambiguous-query caveats are attached to collection
metadata, and individual observations include:

- `quality.ambiguous_query`;
- `quality.broad_non_exact_phrase`.

Exact phrases are represented by quote-wrapped keywords in fixture/config
payloads.

## Doctor helper

`doctor_wordstat_source(source_fields, env=...)` validates local config shape
and credential environment presence without live API calls. It checks:

- optional `credential_env` shape;
- `api_family` is `search_api_wordstat` or `direct_wordstat_report`;
- optional non-empty `api_version`;
- whether the named token environment variable is present.

`live_checked` is always `false` in the current implementation.
