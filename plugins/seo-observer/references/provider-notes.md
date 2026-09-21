# seo-observer provider notes

Last verified: 2026-07-30.

Provider adapters are local, fixture-testable foundations. They build request
descriptors and normalize observations through injected boundaries; they do not
make live calls by default.

## DataForSEO

Reference: `plugins/seo-observer/references/dataforseo.md`.

DataForSEO is the deterministic provider boundary for competitor discovery,
keyword gaps, ranked-keyword evidence, and Google organic SERP rows. Live calls
must pass explicit live mode, paid-call confirmation, local credentials, and a
per-run budget guard before transport execution. Request envelopes stored by the
adapter are sanitized and omit headers.

## Google Search Console

Reference: `plugins/seo-observer/references/gsc.md`.

Search Analytics rows are top-row data, not complete population coverage. Keep
`top_rows`, `capped`, `incomplete`, provider timezone, request dimensions,
filters, type, aggregation, and data-state metadata in the interpretation.

## Yandex Metrica

Reference: `plugins/seo-observer/references/metrica.md`.

Visits/users are traffic metrics, not Search Console impressions/clicks. Goal
totals and outcome mappings need explicit configured semantics; zero goal
totals are rejected unless the mapping marks them confirmed.

## Yandex Webmaster

Reference: `plugins/seo-observer/references/webmaster.md`.

Search query observations preserve missing page dimensions and unavailable
indicators as metadata, not zeros. Index coverage records are point-in-time
facts with `effective_at`.

## Wordstat

Reference: `plugins/seo-observer/references/wordstat.md`.

Wordstat demand is market-volume evidence, not search performance. Preserve
keyword-set hash, region, locale, language, device/API metadata, demand state,
related-query provenance, and ambiguity caveats. Keyword-set hash changes make
demand comparisons not comparable.

## Topvisor

Reference: `plugins/seo-observer/references/markets.md`.

Topvisor is the RU-contour provider for Google, and can also serve Yandex. Two
vendor properties leak into the adapter and must stay visible.

The API is **stateful**: a check runs against a stored project, not against a
query, so one paid run answers every keyword at once. Reads are served from a
snapshot fetched once per engine/region/device/date; triggering the paid run is
an explicit call guarded by budget and live-mode confirmation, never a side
effect of reading.

Errors arrive with **HTTP 200** and a body of `{"result": null, "errors": [...]}`.
The transport raises on them. A failed collection that degrades into an empty
result set does not read as "no data" downstream - it reads as "no competitors".

Snapshots expose rank, URL and domain, but no titles, snippets or SERP features.
`serp_features_supported` is therefore `false` for this provider; that is a
recorded property, not a degraded run.

Region identity has two forms in the same API: `region_index` (ordinal within
the project) for edits, and the quadruple searcher/key/lang/device for snapshot
reads. Sending the wrong one returns a 2001/2003 error rather than wrong data.

## SERP

Reference: `plugins/seo-observer/references/serp.md`.

SERP observations are point samples. Competitor/share-of-voice conclusions need
configured competitor identity and enough weighted keyword coverage; otherwise
mark insufficient coverage or `not_comparable`.

## Aggregate Outcomes

Reference: `plugins/seo-observer/references/outcomes.md`.

Outcome adapters accept approved view descriptors only. Reconciliation with
analytics goals is `not_comparable` when population, counting unit,
attribution, dedupe, timestamp/timezone, period, or grain semantics differ.
Exports must band or suppress exact local-only counts.
