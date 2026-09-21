# seo-observer competitors

Last verified: 2026-07-30

Provider endpoint family/version notes:

- DataForSEO Labs live endpoints:
  `/v3/dataforseo_labs/google/competitors_domain/live` and
  `/v3/dataforseo_labs/google/domain_intersection/live`.
- DataForSEO Google organic SERP live endpoint:
  `/v3/serp/google/organic/live/advanced`.
- Research provider notes: Exa-style search responses are normalized from
  fixture/artifact/live adapter boundaries without requiring an SDK in this
  task.
- Fixture capture date for provider fixtures: 2026-07-30.

## Deterministic Discovery

```bash
seo-observer competitors discover \
  --project demo \
  --provider-mode artifact \
  --output-dir /tmp/demo-competitors \
  --json
```

`competitors discover` creates local deterministic competitor artifacts:

- `manifest.json`
- `competitor-extract.json`
- `competitor-report.md`

The command resolves the target domain from the selected property, loads
configured competitors from `[competitors]`, filters provider candidates, picks
competitors deterministically, derives keyword gaps from domain-intersection
rows, and writes normalized artifacts. It does not run Exa, LLMs, browser
scraping, SERP audit/share-of-voice, content extraction, or briefs.

## Flags

- `--property <id>` selects a configured property.
- `--target-domain <domain>` overrides the property domain.
- `--limit-competitors <n>` controls competitor candidate request size
  (default `20`).
- `--picked-competitors <n>` controls how many discovered competitors feed
  keyword-gap extraction (default `3`).
- `--gap-limit <n>` controls keyword-gap output size (default `100`).
- `--provider-mode fixture|artifact|live` defaults to `artifact`.
- `--allow-paid` is required for `--provider-mode live`.

## Provider Modes

`fixture` uses injected/local fixtures only. It never reads credentials and never
calls the network.

`artifact` reads a compatible local `competitor-extract.json` when one exists.
When no compatible local artifact exists, it creates local-only artifacts with
configured competitors and empty discovered rows. It never calls provider
transport.

`live` may call DataForSEO only when `[sources.competitor_discovery]` references
ready `[providers.dataforseo]`, `--allow-paid` is present, and `BudgetGuard`
preflight passes. Paid-call failures use structured error codes such as
`PAID_CALL_NOT_CONFIRMED`, `PAID_BUDGET_NOT_CONFIGURED`, and
`PAID_BUDGET_PRECHECK_EXCEEDED`.

## Filtering And Selection

Discovery filters:

- owned domains from `[competitors].owned_domains`;
- the exact target domain;
- default mega-authority domains plus configured
  `mega_authority_domains`;
- malformed domains;
- duplicates.

Picked competitors are sorted deterministically:

1. explicit configured direct competitors with provider evidence;
2. higher `intersections`;
3. higher `organic_keywords`;
4. higher `estimated_traffic`;
5. lexical domain.

Configured competitors without provider evidence appear in
`known_competitors` with `quality = "local-only"` and are not counted as
discovered market competitors.

## Keyword Gaps

Domain-intersection rows are merged by normalized keyword,
`location_code_or_name`, and `language_code`. Merged rows keep all contributing
competitor domains, best rank, representative URL from the best-rank row, max
search volume, max CPC, and citation/source row IDs. Gaps are sorted by
competitor count and then search volume.

Markdown reports include source quality/cost, candidate competitors, picked
competitors, filtered domains, top keyword gaps, caveats, and next actions. They
must not include raw provider payloads, auth headers, or secret values.

## SERP Audit And Share Of Voice

```bash
seo-observer competitors audit \
  --project demo \
  --keyword-set core \
  --start 2026-07-01 \
  --end 2026-07-30 \
  --provider-mode artifact \
  --output-dir /tmp/demo-competitors \
  --json
```

`competitors audit` confirms configured competitors against deterministic SERP
evidence and derives coverage-gated visibility/share-of-voice through
`seo_observer.serp` protocol slots, classification, and metrics. It does
not run Exa, LLM briefs, browser scraping, public SERP scraping, content
extraction, backlink/link gap, or scheduling behavior.

Audit artifacts:

- `manifest.json`
- `serp-extract.json`
- `competitor-metrics.json`
- `competitor-audit.md`

Audit flags:

- `--keyword-set <id>` selects a configured `[[keyword_sets]]` entry.
- `--start YYYY-MM-DD` and `--end YYYY-MM-DD` identify the evidence window.
- `--output-dir <dir>` writes local artifacts.
- `--provider-mode fixture|artifact|live` defaults to `artifact`.
- `--allow-paid` is required for `--provider-mode live`.
- `--baseline-artifact <path>` is preferred over storage baseline lookup.
- `--serp-depth <n>` overrides configured SERP depth.
- repeat `--device desktop|mobile` to override keyword-set devices.

`artifact` mode reads a compatible local `serp-extract.json` when present and
never calls provider transport. Without a compatible artifact it writes
local-only empty audit artifacts and marks movement as `not_comparable`.

`live` mode may call DataForSEO only when `[sources.serp] provider =
"dataforseo_google_organic"` references ready `[providers.dataforseo]`,
`--allow-paid` is present, credentials exist, and `BudgetGuard` preflight
passes. Provider rows are normalized into the existing SERP observation model,
then every URL is classified through `classify_domain(...)`.

Coverage below configured `minimum_weighted_keyword_coverage` returns
`insufficient_coverage` and `not_comparable`; share-of-voice fields are `null`.
When no comparable baseline exists, movement fields stay null and trend claims
remain blocked.

## Research And Content Extraction

```bash
seo-observer competitors research \
  --project demo \
  --keyword-set core \
  --provider-mode artifact \
  --output-dir /tmp/demo-content-intel \
  --json
```

`competitors research` collects research-only rows and page extracts for later
content-gap work. It writes:

- `manifest.json`
- `research-extract.json`
- `content-extract.json`
- `research-report.md`
- `research-report.html`
- `research-report.pdf` when a local Playwright Chromium renderer is available

Research rows include `citation_id`, `quality`, `url`, `title`, and `snippet`.
Page extracts include `citation_id`, headings, `text_excerpt`,
`json_ld_types`, and `quality`. Excerpts in JSON are capped at 2000 characters;
Markdown excerpts are capped at 500 characters per page.

Research flags:

- `--keyword-set <id>` selects a configured `[[keyword_sets]]` entry.
- `--provider-mode fixture|artifact|live` defaults to `artifact`.
- `--allow-paid` is required for `--provider-mode live`.
- `--urls <comma-separated>` extracts direct URLs in fixture/live adapter paths
  without provider search.
- `--confirmed-serp-artifact <path>` is accepted for lineage with deterministic
  evidence, but Task 05 does not derive content-gap scores from it.
- `--max-pages <n>` defaults to `20`.
- `--max-response-bytes <n>` defaults to `1000000`.
- `--timeout-seconds <n>` defaults to `10`.

`artifact` mode reads/writes local artifacts only and never performs provider
search or page fetch. `fixture` mode uses injected/local fixture transports.
`live` mode requires configured local provider readiness and `--allow-paid`
before paid provider search; page fetch uses stdlib HTTP with fixed user agent
`seo-observer/competitor-research`, robots.txt checks, no auth/session
bypass, response byte caps, and isolated per-URL errors.

Reports explicitly separate research-only rows from confirmed SEO facts. In
Task 05 the confirmed facts section remains empty; rank, traffic, share of
voice, and movement claims require deterministic SERP artifacts from
`competitors audit`.

Reporting limitation: as of 2026-07-31, discovery, SERP audit, research, and
provider audit are separate artifact-level reports. They are not yet a
human-grade composite SEO report. Product requirements and real-data handoff
are documented in `reporting-handoff-2026-07-31.md`.

## Content Gap Report And Grounded Briefs

```bash
seo-observer competitors report \
  --project demo \
  --discovery-artifact /tmp/demo-competitors/competitor-extract.json \
  --serp-artifact /tmp/demo-competitors/serp-extract.json \
  --content-artifact /tmp/demo-content-intel/content-extract.json \
  --output-dir /tmp/demo-content-gap \
  --json
```

`competitors report` derives deterministic content gaps from prior local
artifacts and writes:

- `manifest.json`
- `content-gap.json`
- `competitor-report.md`
- `brief.json` only with `--with-brief`

The command accepts `--provider-mode fixture|artifact|live`, defaulting to
`artifact`. Artifact mode never calls provider transports or live LLMs. Optional
brief synthesis uses `--with-brief --llm-fixture <path>` for fixture-backed
acceptance paths; live LLM calls are intentionally outside this task.

Every brief claim must cite known artifact IDs. Research-only page citations may
support content observations and recommendations, but rank, traffic, SOV, and
trend claims require deterministic `live` or `partial` keyword/SERP evidence.
Unknown citation IDs, fabricated URLs, and fabricated rank/volume/SOV numbers
block `brief.json` while preserving deterministic `content-gap.json` and
`competitor-report.md`.

Details are documented in
`plugins/seo-observer/references/content-gap.md`.
