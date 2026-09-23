# seo-observer command routing

Last verified: 2026-07-30

Install/update from the repo checkout when needed:

```bash
uv tool install --editable plugins/seo-observer/cli/
```

Use `--json` for machine-readable output. Global flags are accepted before or
after subcommands.

## Orient and Config

```bash
seo-observer doctor --json
seo-observer doctor --config .seo-observer/project.toml --json
seo-observer doctor --project demo --json
seo-observer projects register demo --config .seo-observer/project.toml --json
seo-observer projects list --json
seo-observer projects remove demo --json
```

`--config` and `--project` are mutually exclusive. Surface selector/config
errors exactly: `SELECTOR_CONFLICT`, `PROJECT_REGISTERED`, `CONFIG_NOT_FOUND`,
`CONFIG_INVALID`, or `PROJECT_NAMESPACE_COLLISION`.

`doctor` validates competitor-intelligence config shape and local credential
readiness without live provider calls. Canonical competitor provider config uses
provider account sections plus semantic sources:

```toml
[providers.dataforseo]
enabled = true
credential_env = "DATAFORSEO_AUTH"
per_run_budget_usd = 3.00
monthly_budget_usd = 50.00

[providers.exa]
enabled = false
credential_env = "EXA_API_KEY"
per_run_budget_usd = 1.00

[sources.competitor_discovery]
enabled = true
required = false
provider = "dataforseo"

[sources.competitor_research]
enabled = false
required = false
provider = "exa"
```

The JSON readiness check for each competitor source includes `source`,
`provider`, `enabled`, `required`, `ok`, `live_checked`, `quality`,
`validation_code`, `credential_env`, and `errors`. It prints environment
variable names, never credential values. Exit code is `0` for valid config with
no missing required source readiness, `2` for invalid config shape, and `3` when
an enabled required source is missing local readiness.

## Operational Commands

These are the public CLI routes agents should choose for user intent:

```bash
seo-observer collect --project demo --json
seo-observer provider-audit --project demo --start 2026-06-28 --end 2026-07-27 --output-dir /tmp/demo-provider-audit --json
seo-observer weekly --project demo --json
seo-observer snapshot --project demo --json
seo-observer report --project demo --json
seo-observer compare --project demo --start 2026-07-08 --end 2026-07-14 --baseline-start 2026-07-01 --baseline-end 2026-07-07 --json
seo-observer actions add --project demo --changed-at 2026-07-08 --type content_update --url https://demo.example/landing/ --query "agentic seo" --description "Updated landing copy" --artifact-ref pr:123 --json
seo-observer actions list --project demo --json
seo-observer actions show action:abc123 --project demo --json
seo-observer competitors discover --project demo --provider-mode artifact --output-dir /tmp/demo-competitors --json
seo-observer competitors audit --project demo --keyword-set core --start 2026-07-01 --end 2026-07-30 --provider-mode artifact --output-dir /tmp/demo-competitors --json
seo-observer competitors research --project demo --keyword-set core --provider-mode artifact --output-dir /tmp/demo-content-intel --json
seo-observer competitors report --project demo --discovery-artifact /tmp/demo-competitors/competitor-extract.json --serp-artifact /tmp/demo-competitors/serp-extract.json --content-artifact /tmp/demo-content-intel/content-extract.json --output-dir /tmp/demo-content-gap --json
seo-observer composite-report --provider-artifact /tmp/demo-provider-audit/provider-extract.json --research-artifact /tmp/demo-content-intel/research-extract.json --content-artifact /tmp/demo-content-intel/content-extract.json --serp-artifact /tmp/demo-competitors/serp-extract.json --metrics-artifact /tmp/demo-competitors/competitor-metrics.json --output-dir /tmp/demo-composite-report --json
seo-observer outcomes --project demo --action-id action:abc123 --start 2026-07-08 --end 2026-07-14 --json
seo-observer opportunities --project demo --start 2026-07-01 --end 2026-07-07 --json
seo-observer ai-readiness --project demo --output-dir /tmp/demo-ai-readiness --json
```

`collect` is the live-provider entrypoint. It requires enabled supported
sources, valid credentials from the project config, and an explicit selector.
The current public collect path stores Google Search Console and Yandex
Webmaster search rows in `search_performance`, and Yandex Metrica organic
traffic plus GA4 organic traffic rows in `traffic_metrics`. Unsupported enabled
sources are reported in the JSON payload as `unsupported` instead of blocking
supported live collection.
It writes local SQLite evidence under `SEO_OBSERVER_HOME`; it does not
publish Telegram or commit Git artifacts.
Operational details, evidence layout, repeat-run semantics, and Demo acceptance
checks are documented in
`plugins/seo-observer/references/live-collect-runbook.md`.

`provider-audit` is a reusable live provider export for debugging source-level
SEO evidence before writing client reports. It is config-driven, requires
explicit `--start`/`--end`, skips live calls when local credentials are missing,
and writes local artifacts only:

- `manifest.json`
- `provider-extract.json`
- `provider-audit.md`
- `provider-audit.html`
- `provider-audit.pdf` when a local Playwright Chromium renderer is available

The human report includes safe structured fields such as GSC query/page rows,
GA4 organic traffic analytics by landing page/source/device/geo/event, Yandex
Webmaster PRESENT diagnostics with explanations, popular queries, sitemaps, and
broken-link samples when the provider endpoint returns them.
It must not include service-account JSON, OAuth tokens, auth headers, or raw
private provider payload dumps. Unsupported enabled sources are reported as
`unsupported` and do not block supported sources.
For human delivery, prefer `provider-audit.html` or `provider-audit.pdf`.
Do not paste the Markdown artifact into Telegram as the primary report.

`compare` is the deterministic local period comparison entrypoint. It reads
only the project SQLite database under `SEO_OBSERVER_HOME`, using current
facts from `search_performance` and `traffic_metrics`; it does not call live
providers, write Telegram output, or change storage schema. The current window
is explicit via `--start` and `--end`. The baseline window is explicit via
`--baseline-start` and `--baseline-end`; when omitted, it defaults to the
previous adjacent window with the same inclusive day count.

The JSON payload includes `windows`, `comparability`, `evidence_quality`, and
`slices`. Search comparison slices are `total`, `queries`, and `pages`, each
with clicks, impressions, CTR, and average position. Traffic totals are emitted
when local `traffic_metrics` rows exist. Deltas include absolute and percentage
forms. Percentage delta is `null` with `percent_reason = "baseline_is_zero"`
when the baseline value is zero.

Comparability is a first-class gate. If window lengths differ, source sets
differ, collection days are missing inside either window, or the existing
period comparison contract reports non-comparable evidence, the payload sets
`comparability.state = "not_comparable"` with reason codes. In that state,
numeric deltas are marked `comparable = false` and are not suitable for
directional business claims.

`actions` is the local change journal. `actions add` records what changed,
when (`--changed-at` formatted as `YYYY-MM-DD`), affected URLs and/or queries, a free-form description,
and an optional artifact/PR reference in the project SQLite database. Alternatively, `actions add --file <path>`
or `actions ingest [--file <path>]` ingests declarative `.seo-observer/actions/*.toml` files into `observer.db`.
`actions list` returns the latest revision of every local action for the project. `actions show <id>`
returns one recorded action. It does not open PRs, edit pages, schedule work, or call live providers.

`outcomes` measures one recorded action by running the compare logic on the
affected URL/query slice and on a control slice that excludes those targets.
The after window is explicit via `--start`/`--end`; the before window is
explicit via `--baseline-start`/`--baseline-end` or defaults to the previous
adjacent equal-length window. The payload includes `affected`, `control`,
`windows`, `maturation`, and `status` (`ok`, `too_early`, `not_comparable`, or
`partial`). It reports observed change, not causality. If the control slice
moves the same way as the affected slice, both deltas remain visible in JSON.
If the after window is shorter than the named maturation threshold, status is
`too_early` and effect deltas are suppressed instead of reported as numbers.

`opportunities` ranks prioritized findings from already collected
`search_performance` and `crawl_pages` rows: quick wins, second-page (position
11-20), decaying pages, cannibalisation, refresh priorities, canonical/robots
crawl conflicts, broken internal links, and orphan pages. It does not call live
providers or crawl URLs. Default output is one ranked list in `opportunities`
with `score`, `reason`, and `evidence_quality` per finding; `--type-breakdown`
adds source groups for debugging, not as the primary output. Details, formulas,
and the heuristic CTR table are documented in
`plugins/seo-observer/references/opportunities.md`.

`ai-readiness` runs deterministic local AI-crawler-readiness checks: an
`llms.txt`/`llms-full.txt` audit (reachability, format, sitemap drift, broken
declared links) against each configured project property, plus AI-referral
traffic recognized from already collected `traffic_metrics`. The only network
access is plain HTTP GET to the project's own public property URLs; it does
not call LLMs or paid providers. Unrecognized referrers are reported in
`unknown`, not silently dropped. Details are documented in
`plugins/seo-observer/references/ai-readiness.md`.

## PDF rendering prerequisites

PDF artifacts are rendered through Playwright and need a separately installed
browser binary:

```bash
uv tool install --editable "plugins/seo-observer/cli[pdf]"
playwright install chromium
```

Without it no command fails: the HTML artifact is still written and the reason
is recorded in `pdf_error` of the manifest/payload. Tests assert that
degradation contract, not the presence of a browser.

`competitors discover` is the deterministic competitor discovery entrypoint.
It writes `manifest.json`, `competitor-extract.json`, and
`competitor-report.md`, defaults to `--provider-mode artifact`, and requires
`--provider-mode live --allow-paid` plus DataForSEO readiness before any paid
provider transport is called.

`competitors audit` is the deterministic SERP confirmation/share-of-voice
entrypoint. It writes `manifest.json`, `serp-extract.json`,
`competitor-metrics.json`, `competitor-audit.md`, `competitor-audit.html`,
and `competitor-audit.pdf` when a local Playwright Chromium renderer is
available. It defaults to
`--provider-mode artifact`, and requires `--provider-mode live --allow-paid`
plus `[sources.serp] provider = "dataforseo_google_organic"`, ready
`[providers.dataforseo]`, credentials, and a passing `BudgetGuard` before any
paid provider transport is called. Details are documented in
`plugins/seo-observer/references/competitors.md`.

`render-report` converts an already approved Markdown report into polished
HTML/PDF without live provider calls:

```bash
seo-observer render-report \
  --input /tmp/demo-seo-audit.md \
  --output-dir /tmp/demo-seo-audit-rendered \
  --basename demo-seo-audit \
  --title "SEO audit demo.example" \
  --subtitle "Period: 2026-06-28 - 2026-07-27" \
  --json
```

Use it for ad-hoc human reports and Telegram file delivery when the source data
has already been collected/exported.

`competitors research` is the research/content extraction entrypoint. It writes
`manifest.json`, `research-extract.json`, `content-extract.json`,
`research-report.md`, `research-report.html`, and `research-report.pdf` when a
local Playwright Chromium renderer is available. It defaults to
`--provider-mode artifact`, and keeps all Exa/web-search/page evidence marked
as `research-only` unless deterministic SERP artifacts confirm it in later
tasks. Details are documented in
`plugins/seo-observer/references/research.md`.

`composite-report` is the deterministic local merger for provider-audit,
competitor research/content, optional SERP audit, and optional competitor metrics
artifacts. It does not accept `--project` and does not read project config or
call providers; the project/period comes from the provider artifact. It writes
`manifest.json`, `composite-extract.json`, `composite-report.md`,
`composite-report.html`, and `composite-report.pdf` when a local Playwright
Chromium renderer is available. If `--serp-artifact` is omitted, the command
still succeeds and records a limitation that rank, SERP feature, and
share-of-voice evidence is unavailable. The Markdown/HTML/PDF report is rendered
from safe summaries only, not raw provider payloads.

`competitors report` is the deterministic content-gap and optional grounded
brief entrypoint. It reads prior local competitor, SERP, and content artifacts;
writes `manifest.json`, `content-gap.json`, `competitor-report.md`, and
`brief.json` only when `--with-brief` is supplied; and defaults to
`--provider-mode artifact`. Brief claims must cite known artifact IDs and are
blocked when citations fail validation. Details are documented in
`plugins/seo-observer/references/content-gap.md`.

`weekly`, `snapshot`, `report`, `compare`, `actions`, and `outcomes` are local/export surfaces: with
the required local evidence they create deterministic payloads without live
provider calls. Other operational commands may still return structured
`NOT_IMPLEMENTED` with exit code 2. Treat that as authoritative status, not a
failure to work around with invented SEO output.

Before live competitor discovery, use `doctor --project demo --json` to verify
`[providers.dataforseo]`, `[providers.exa]`,
`[sources.competitor_discovery]`, `[sources.competitor_research]`, market
defaults, and configured competitors. `doctor` does not spend provider credits
or call external services.

## Keyword Research

Keyword research routes through configured `[[keyword_sets]]`, Wordstat demand
evidence, and SERP protocol slots:

```bash
seo-observer doctor --project demo --json
seo-observer collect --project demo --json
seo-observer snapshot --project demo --json
seo-observer report --project demo --json
```

If only local fixtures/artifacts exist, label the result `local-only`. If
Wordstat or SERP coverage is missing, partial, stale, or keyword-set hashes
differ, do not compare demand or share-of-voice across windows.

## Local Python APIs

Use Python APIs only for explicitly local artifact/test work:

- snapshots/reports: `seo_observer.snapshots`
- period comparisons: `seo_observer.comparisons`
- actions/verdicts: `seo_observer.actions`
- keyword demand: `seo_observer.wordstat`
- competitors/SERP: `seo_observer.serp`
- aggregate outcomes: `seo_observer.outcomes`

The agent still cites generated artifact paths/hashes and does not duplicate the
deterministic calculations.


## Marketing report and markets

- `scripts/marketing-report.py` — clusters and gap analysis for marketing.
  See `references/marketing-report.md`.
- RU/EN search-market contours and competitor binding — `references/markets.md`.
