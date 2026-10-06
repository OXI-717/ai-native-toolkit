# Growth export (`seo-observer export`)

Publishes `growth_schema_version = 3` bundles (private panel data — business aggregates only,
no person identifiers) as versioned, immutable build directories with atomic
symlink switching. Read-only over the project SQLite database; no live
provider calls.

```bash
seo-observer export --project demo --kind current --out /srv/growth/demo --json
seo-observer export --project demo --kind current --date 2026-09-20 --out /srv/growth/demo
seo-observer export --project demo --kind weekly --out /srv/growth/demo --panel-url https://panel.example/demo
seo-observer export --project demo --kind weekly --week-start 2026-09-14 --out /srv/growth/demo --no-pdf
```

## Kinds and windows

- `current`: 7-day window ending yesterday (`--date` overrides the end), plus
  a 28-day window with the same end; each window gets a previous window of the
  same length. One `growth.json`: the top level is the 7-day window; the 28-day
  KPI column lives in `derived.kpis_by_window["28d"]`.
- `weekly`: Monday–Sunday week. Default is the latest week whose Sunday is at
  least 3 days in the past (source finalization delay). `--week-start` selects
  a specific week and must be a Monday. Trend history is computed in memory
  for the 12 preceding weeks (never written to disk separately).

## Layout

```
DIR/
  builds/current-<YYYY-MM-DD>-<hash12>/
  builds/weekly-<start>_<end>-<hash12>/
  current        -> builds/current-…
  weekly/<start>_<end> -> ../builds/weekly-…
  latest-weekly  -> builds/weekly-…
  failed/<kind>-<window>-<utc>.receipt.json
```

Builds are written to `builds/.tmp-<uuid>/` and moved into place with
`os.rename` once every file is complete; symlinks swap via
`os.symlink` + `os.replace`. A failure anywhere removes the temp directory and
temp links — previously published builds and links are untouched. Re-running
an export whose data version is already built writes nothing and reports
`unchanged: true` (links are verified). For `weekly`, `unchanged` is `false`
when `latest-weekly` had to be (re)created, even if the build already existed
and no new files were written — links are re-pointed in that case.

`--date` applies only to `--kind current` and `--week-start` only to
`--kind weekly`; combining a flag with the wrong kind is a usage error
(`EXPORT_FLAG_KIND_MISMATCH`, exit code 2).

`weekly` files: `growth.json`, `index.html`, `report.pdf`, `brief.md`,
`receipt.json`. Russian `current` exports contain `growth.json`, `receipt.json`
and six pages at the build root and under `28/`: `index.html`,
`positions.html`, `demand.html`, `traffic.html`, `money.html`, `status.html`.
Navigation stays in the selected window; 7/28 links switch the same page.
English `current` exports keep the original single-page format.

The Russian dashboard reuses existing aggregates and KPI eligibility checks.
Its dedicated renderer embeds packaged CSS, JavaScript and Manrope WOFF2. Its `dashboard` block contains `previous`, 12 `history`
windows and `28d.{current,previous}` raw windows (including derived KPIs).
In schema v3, `dashboard.keyword_clusters` maps search sources to maps of
normalized queries and cluster labels, for example
`{"yandex_webmaster": {"shared query": "Local intent"}, "google_search_console": {"shared query": "Global intent"}}`.
The CLI retains each `KeywordSet.market` and resolves it through the configured
market's `search_engine`. Unscoped sets live under `"*"` as a fallback; a scoped
label takes precedence. An unresolved market never becomes a global fallback.
If two markets on one engine disagree on a query, its value is `null` and the
row displays the unclustered label: source-level rows cannot identify its market.
Cluster labels are cleaned before comparison and storage, so different numeric
keyword-count/volume annotations on the same label do not create a conflict.
This replaces the flat query-to-cluster map in schema v2; schema v2 landing
page fields remain unchanged.
Trend points include `revenue_minor` and `revenue_currency`; different currencies
are never connected in a revenue sparkline. All detail/history inputs participate in the immutable data hash;
`dashboard_version` participates in the render identity. No new database,
collector, API or SPA is introduced. Existing runtime static-file routing
serves these paths without changes.

Positions are impression-weighted averages of observed GSC/Webmaster queries,
not fixed SERP rankings. Bucket counts partition the available queries;
missing averages are separate from >100. Position tables initially show 25 rows sorted by impressions, with eight history
weeks. Buckets include current and previously observed queries; absent current
queries belong to «не показывался». Cluster summaries weight positions by impressions. Changes are
withheld unless both full windows have covered dates and fresh detail rows.
Absent rows are never treated as rank losses or zeros. Weekly history is an
observed slice, not a completeness-certified rank history.

Demand uses only stored impressions/clicks and per-query/page rows. Page→query
links appear only when both dimensions actually exist. Opportunities use an
explicit heuristic (≥20 impressions, average position 4–20, CTR <5%). Traffic
shows separate channel, source and landing slices: no unrecorded join between
source and landing is inferred. Paths are normalized before aggregation, hashing and JSON/HTML export: hex
segments of ≥16 characters, UUIDs, opaque alphanumeric segments of ≥20 characters
and numbers of ≥6 digits become `:id`. JWT-like three-part segments and dotted
tokens containing identifying components are also masked as a whole. Long
word-based slugs, `file.html` and `v1.2` remain intact. Detection includes percent-encoded segments;
safe reserved encoding is preserved. Matching templates become one row in both JSON and HTML. Schema v2 landing
rows contain `page`, total `visits`, and a `channels` map preserving the original
channel subtotals; the former scalar `channel` field is replaced. Page-query
details are indexed in one pass and show at most 100 queries with the most
impressions per displayed page, with an explicit truncation count. Traffic
comparisons retain channels that disappeared; absent rows become zero only
when the corresponding traffic period is fully covered.

Money uses server facts and shows Mixpanel signups separately as a sample.
Relative KPI deltas use percent. When relative change is unavailable, absolute
revenue deltas convert minor units to the stated currency; absolute conversion
deltas convert fractions to percentage points. Counts keep their native units;
missing comparisons remain unavailable.
First/repeat purchase attribution, Yandex diagnostics, index
coverage and links are explicitly unavailable in this export. Multiple
currencies are not summed; revenue changes and historical averages exclude
incompatible currencies. Collection timestamps and factual/reporting dates
are separate; provisional search zeros do not overwrite actual search freshness.
Every source that participates in the growth KPI categories and Mixpanel
appears in status and freshness, including Metrica and custom outcome sources;
competitor provider configuration remains outside this view. Aggregate
non-brand search KPIs remain on Demand and visit-to-signup on Money.

`growth_hash` is the sha256 of the canonical growth JSON with build-time
fields (`generated_at`/`produced_at`) removed; `sources.*.collected_at` is
part of the hash, so a re-collected day inside the same week produces a new
build with a new hash12 and the week link switches to it.

`receipt.json` fields: `schema_version`, `tenant`, `kind`, `window`,
`produced_at`, `growth_hash`, `files`, `pdf` (`{ok, error}`), and `sources`
(per-source `state`/`required`/`collected_at`/`timezone`).

## `growth.json` data-availability fields

Each enabled search source has a `search.<source>` block, emitted even when
the source produced no facts in the window:

- `search.<source>.data_through` — `YYYY-MM-DD` or `null`. The last window
  day with a non-zero `total` fact; trailing zero-row days are provisional
  and excluded. Search KPIs sum only the leading days through this date and
  clip comparison windows to the same count of leading days.
- `search.<source>.totals_available` — boolean. True when at least one
  `total` fact exists in the window (explicit zeros count); false means
  absent data, not a measured zero, and search KPIs report coverage `none`.
- `search.<source>.search_gap` — boolean. True when `data_through` sits more
  than 4 days before the window end — a real collection gap, not the normal
  search reporting lag. A gapped source makes its KPIs `partial`, and a
  previous or history window whose own derivation would set this flag is not
  an eligible comparison (null delta / skipped avg4 week).
- `search.<source>.daily` / `queries` / `pages` — lists, empty when there
  are no facts.
- `search.<source>.details_through` — last date with a real detail row.
- `search.<source>.query_pages` — redacted query/page pairs only when both
  dimensions were observed; empty when the source only emits separate lists.
- `sources.<source>.data_through` — latest stored fact date at/before the
  requested end; dashboard reporting freshness also considers successfully
  covered zero-event days for non-search sources.
- Revenue KPIs include `previous_currency` when the previous window has one
  currency, so a previous USD value cannot be labelled with the current RUB.

## `growth.json` KPI delta fields (`derived.kpis.<name>` and
`derived.kpis_by_window.<window>.<name>`)

- `delta_pct`, `delta_vs_avg4_pct` — number or `null`. Percent change versus
  the previous window / the trailing 4-week average. Null when the
  comparison is not eligible (current KPI not `complete`, comparison window
  not fully covered or itself gap-flagged, missing value, zero or tiny
  base).
- `delta_abs`, `delta_vs_avg4_abs` — number or `null`. Absolute change in
  the KPI's own unit (impressions, clicks, visits, registrations, payments),
  filled **instead of** the percentage only when the comparison is eligible
  and the base is zero or, for count KPIs, below the tiny-base threshold of
  5. A null absolute field therefore does **not** prove the comparison was
  withheld — an eligible comparison on a normal base renders `*_pct` and
  leaves `*_abs` null.
- `avg4`, `avg_weeks` — `avg4` is the mean KPI value over up to the 4 most
  recent eligible history weeks (`avg_weeks` reports how many were used);
  `null`/`0` when none are eligible. Weeks that are empty or themselves
  gap-flagged do not count.

## PDF policy

`weekly` renders `report.pdf` from `index.html` via Playwright Chromium
(`report_rendering.render_pdf_from_html`). If PDF rendering fails the week is
**not** published — neither `weekly/<window>` nor `latest-weekly` switches —
and the receipt is written under `failed/`; exit code is non-zero. Pass
`--no-pdf` on hosts without Chromium: the week is published and the receipt
records `pdf: {ok: false, error: "disabled"}`.

## Retention

- `builds/current-*`: the linked build plus the two newest unlinked builds are
  kept; older unlinked builds are removed.
- `builds/weekly-*`: unlinked builds older than 30 days are removed; builds
  referenced by `weekly/*` or `latest-weekly` are kept indefinitely.
- `failed/*.receipt.json`: removed after 30 days.


## Dashboard interaction and dates

All tables initially show 15 rows (positions: 25) with an explicit total and
«Показать все (N)». Headers sort text/numeric data, with missing values last.
Search, engine, cluster and position filters compose before the row limit.
Without JavaScript the first rows remain visible. Wide matrices scroll
horizontally and keep the query column sticky. Source/medium labels are human
readable; raw dimensions appear only in titles. Missing cells show an em dash
with an explanation; absent observations are never invented zeros.

Dates use Russian month abbreviations; build timestamps use Europe/Moscow.
ISO dates remain in JSON and datetime/data attributes. Source chips point to
«Состояние», where source purpose, status and alert participation are explained.
Only incomplete coverage is called out. Mixpanel remains a separate sample
card with its server registration denominator.

### Monetary units

All monetary presentation (current KPI values and comparisons, sparkline maxima,
daily money tables, weekly/English HTML/PDF and Markdown briefs) uses the same
explicit minor-unit exponents. Raw `value_minor` and `revenue_minor` stay unchanged
in JSON and calculations. Formatting uses Decimal, preserving native precision
without routing integer minor values through binary floating point.

| Currency | Exponent | Minor units per unit |
| --- | ---: | ---: |
| RUB / USD / EUR | 2 | 100 |
| XTR / JPY | 0 | 1 |
| TON | 9 | 1,000,000,000 |
| KWD | 3 | 1,000 |

Unknown or absent currency codes produce an explicit localized unsupported-currency
message instead of assuming cents. A money chart with an unknown series currency
is withheld. Current cards omit decimals for integral amounts; legacy reports
retain fixed native precision. This is unit scaling, not currency conversion.
