# seo-observer SERP adapter

`seo_observer.serp` is a local, fixture-testable SERP adapter foundation for
Yandex-oriented rank observations and competitor metrics. It does not read live
credentials by itself, call a live provider without an injected transport,
automate a browser, scrape public search result pages, bypass captcha, make paid
API calls, schedule collection, write production Demo config, or take autonomous
SEO actions.

Source context:

- Current Yandex AI Studio / Search API documentation describes web search
  through REST API, gRPC API, or Yandex Cloud ML SDK.
- Yandex Search API text search returns XML or HTML search results depending on
  request settings.
- The adapter records unsupported metadata when a provider/API/fixture does not
  expose a requested dimension such as device-specific SERP or SERP features.

Third-party scraping APIs and public HTML scraping behavior are not canonical
behavior for this adapter.

## Protocol slots

`generate_protocol_slots(...)` builds deterministic point-observation protocol
slots from:

- keyword-set ID;
- normalized keyword-set contents and `keyword_set_hash`;
- normalized keyword;
- region ID/name;
- locale and language;
- device, or `__all__` when the configured provider does not support devices;
- search engine;
- result depth;
- observation count and observation slot;
- provider, API family, and API version.

`protocol_hash(...)` computes `serpproto:<sha256>` from the normalized protocol
inputs. The hash is included in request payloads, metadata, logical observation
keys, and quality metadata. Region/location, locale/language, device,
search-engine, API-family/version, depth, keyword-set hash, repeated
observation-slot, and competitor classification config hash differences
intentionally change the hash.

SERP observations are point observations. They use `effective_at` and
`collection_timestamp`, not interval start/end performance semantics.

## Request and response normalization

`SerpAdapter.fetch_slot(...)` accepts one generated slot, an explicit
`effective_at` timestamp, and optional `CompetitorConfig`. The same config may
also be provided to `SerpAdapter(...)` construction. It posts a deterministic
request descriptor through an injected `SerpTransport` fixture/live boundary.

Request metadata preserves:

- provider/API family/version;
- endpoint and method;
- full request payload;
- response format (`XML` by default);
- query text;
- region;
- locale/language;
- device support metadata;
- depth;
- observation count/slot;
- provider request ID.

Rank observations preserve:

- rank;
- URL;
- normalized host/domain;
- auditable competitor metadata (`classification`, `competitor_id`,
  `competitor_name`, `match_reason`, `matched_pattern`);
- title and snippet when present;
- SERP feature labels when fixture payload exposes them;
- source provider request ID;
- raw-result position provenance;
- protocol hash and logical observation key.

When fixture payloads do not expose SERP features, metadata sets
`serp_features_supported = false` and records
`unsupported_metadata.serp_features = "not_exposed_by_fixture_or_api"`.
When a configured source does not support device-specific requests, metadata
sets `device_supported = false`, uses `device = "__all__"`, and records
`unsupported_metadata.device = "provider_not_supported"`.

## Competitor classification

Owned-domain and named-competitor classification is config-driven and auditable
through `CompetitorConfig`:

- `owned_domains`;
- competitor IDs and names;
- competitor domain patterns;
- aliases retained in metadata for auditability.

`classify_domain(...)` returns explicit classification metadata:

- `classification`: `owned`, `competitor`, or `unclassified`;
- `competitor_id`: configured ID, `__owned__`, or `__unclassified__`;
- `competitor_name`;
- `match_reason`;
- `matched_pattern`.

Missing competitor identity is represented as `__unclassified__`; it is not
silently folded into another competitor.

When no `CompetitorConfig` is available for an adapter fetch, rank observations
still carry explicit missing-identity metadata:
`classification = "unclassified"`, `competitor_id = "__unclassified__"`,
`competitor_name = null`, `match_reason = "competitor_config_missing"`, and
`matched_pattern = null`.

## Derived competitor metrics

`derive_competitor_metrics(...)` consumes one or more point snapshots and
expected protocol slots. It computes:

- observed slot coverage;
- weighted coverage;
- visibility/share-of-voice from rank depth;
- best rank;
- median rank;
- rank-stability metadata for repeated observations;
- rank movement between two snapshots;
- two-snapshot confirmation status when configured confirmation observations are
  required.

Visibility follows the shared competitor-intelligence contract:

- `slot_visibility = max(0, depth + 1 - rank_absolute) / depth`;
- competitor and owned visibility divide weighted slot visibility by the total
  expected protocol-slot weight;
- share of voice divides each owned/competitor visibility by the sum of owned
  and competitor visibility when weighted coverage is sufficient; unclassified
  domains do not enter the SOV denominator;
- repeated-observation rank stability is numeric:
  `1 - (distinct_rank_values - 1) / max(1, observation_count - 1)`.

Coverage below `minimum_weighted_keyword_coverage` blocks overconfident
share-of-voice conclusions. In that case the result uses
`conclusion_status = "insufficient_coverage"`, `comparability =
"not_comparable"`, and competitor `share_of_voice = null`.

Per-competitor weighted coverage is computed from unique observed expected
protocol slots for that competitor divided by total expected protocol-slot
weight. Multiple results in the same protocol slot do not increase competitor
weighted coverage. Global weighted coverage keeps the same expected-slot
denominator and counts unique observed protocol slots across the snapshot.

With enough coverage, conclusions remain `provisional` by default because SERP
observations are point samples. Rank movement is `confirmed` only when the
configured number of current repeated observations support the same movement
direction; otherwise it is `requires_confirmation`.

## Doctor helper

`doctor_serp_source(source_fields, env=...)` validates local config shape and
credential environment presence without live API calls. It checks:

- provider is `yandex_search` or `fixture_yandex_search`;
- API family is `yandex_search_api`;
- API version is a non-empty string;
- optional `credential_env` shape and token presence;
- positive `result_depth`;
- no browser automation, public search scraping, captcha bypass, or paid API
  calls are part of the local doctor path.

`live_checked` is always `false` in the current implementation.
