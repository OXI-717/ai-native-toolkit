# seo-observer report interpretation

Reports and summaries are interpretations of local artifacts. Do not infer
provider truth beyond the snapshot/report metadata.

## Required Answer Shape

State:

- project and period requested;
- command or Python API path used;
- artifact paths and hashes when generated;
- source coverage and quality states;
- conclusions supported by evidence;
- conclusions blocked by `missing`, `partial`, `stale`, `local-only`, or
  `not_comparable` state.

## Human Report Quality Gate

Do not treat generated HTML/PDF as product-complete just because the renderer
created files. A human-facing SEO report must answer, in Russian for owner-facing
work:

- what changed or what was found;
- why it matters;
- what evidence supports it;
- what cannot be concluded from the current coverage;
- what to do next.

Artifact-level tables, raw provider rows, raw diagnostics, and page-title lists
are appendices, not the report body. The first page must contain a useful
summary and prioritized findings, not only a table of contents.

As of 2026-08-03 this is delivered by `composite-report` (epic #1663). The
Russian decision report has a fixed section order, and each section carries a
contract:

| Section | Contract |
|---|---|
| First-screen summary | coverage counts, number of prioritized findings, and the explicit reason when SERP/SOV is absent |
| Source coverage | per-source quality in words; a raw quality code is glossed at first use |
| Prioritized findings | each finding: severity, confidence, evidence text, evidence ID, why it matters, action |
| Provider efficiency | per-provider quality, diagnostics count, extract sizes |
| Technical diagnostics | provider diagnostics plus up to eight blocked claims with their reason codes; the complete list is in limitations |
| Competitor observations | research candidates and content topics, labelled as hypotheses |
| SERP / share of visibility | rendered **only** when a deterministic SERP artifact exists |
| Limitations and blocked conclusions | blocked conclusion, reason code, evidence ID |
| Appendix | input artifact paths and pointers to excerpts in the structured artifacts |

Two invariants matter more than layout:

- a claim is emitted only when its evidence class supports it (`#1665`);
  research-only rows never support rank, share-of-voice, or traffic claims;
- blocked claims are never dropped silently — they move to the limitations
  section with a reason, so the reader sees what could not be concluded.

Golden fixtures for both project shapes live under
`cli/tests/fixtures/reports/` in the source checkout (Russia-first and
global-first variants).
Change them only together with the renderer: they are the readable record of
what the report is supposed to look like.

## Comparisons

Period comparisons and action verdicts come from CLI/Python deterministic
logic. Use the emitted deltas, denominators, confidence/comparability states,
and evidence IDs. Do not recompute CTR, position movement, practical-effect
thresholds, outcome reconciliation, or competitor share of voice by hand.

`seo-observer compare` compares explicit current and baseline windows from
local `search_performance` and `traffic_metrics` evidence. If
`--baseline-start/--baseline-end` are omitted, the command uses the previous
adjacent window with the same inclusive length. Treat `comparability` as the
primary result: `not_comparable` blocks trend claims when windows differ in
length, source sets differ, either window has collection gaps, or evidence
quality is incompatible with the shared period comparison contract.

For comparable metrics, use both emitted delta forms: absolute change and
percentage change. When the baseline value is zero, the percentage is `null`
with `percent_reason = "baseline_is_zero"`; describe the absolute change only
and do not replace the missing percentage with 0 or infinity.

When the command says data is not comparable, explain the reason category and
stop before giving a directional business claim.

## Partial Sources

Partial evidence can support narrow statements about the available slice, such
as "local artifact contains GSC rows for this period." It cannot support broad
claims about total SEO performance, all channels, all devices, conversion lift,
or competitor movement unless coverage metadata says those scopes are covered.

## Market Scope

Composite reports must preserve project-level search-market intent from
provider artifacts. For Russia-first reports, Yandex
Webmaster/Metrica/Wordstat diagnostics are the primary SEO lens; Google Search
Console can support owned-site performance, but Google-worldwide SERP competitor
conclusions are intentionally out of scope unless requested. For global-first
promotion, Google-worldwide SERP/GSC interpretation is primary; Yandex evidence
is secondary unless explicitly requested.

## Privacy

Do not publish credentials, raw private payloads, row-level users, report-time
SQL text, unapproved table names, or exact local-only business counts. Use the
snapshot/report export policy output when present.
