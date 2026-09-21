# seo-observer competitor research

Last verified: 2026-07-30

Provider endpoint family/version notes:

- Exa-style search response normalization is implemented through transport
  injection. Task 05 does not require the Exa SDK and does not add live
  model-web-search SDKs.
- Generic web-search adapter interfaces are fixture-only extension points for
  future Perplexity/OpenAI/Claude web-search adapters.
- Fixture capture date for provider fixtures: 2026-07-30.

## Command

```bash
seo-observer competitors research \
  --project demo \
  --keyword-set core \
  --provider-mode artifact \
  --output-dir /tmp/demo-content-intel \
  --json
```

Artifacts:

- `manifest.json`
- `research-extract.json`
- `content-extract.json`
- `research-report.md`
- `research-report.html`
- `research-report.pdf` when a local Playwright Chromium renderer is available

The CLI JSON response follows the shared top-level contract:
`ok`, `project`, `command = "competitors.research"`, `provider_mode`,
`sources`, `artifacts`, `cost`, `report_path`, `html_report_path`,
`pdf_report_path`, and `errors`.

## Provider Modes

`artifact` is the default. It reads/writes local artifacts only and never
performs provider search or page fetch, including when `--urls` is present.

`fixture` uses injected/local fixture transports and files. It never reads
credentials and never calls live provider transports.

`live` requires `--allow-paid`, configured local provider readiness, and a
passing `BudgetGuard` before paid provider search. Page fetching remains a
research edge layer and does not produce confirmed SEO facts.

## Direct URL Extraction

Use `--urls <comma-separated>` when URLs come from a deterministic SERP
artifact, a manual review list, or fixture tests. Direct URL mode skips provider
search. In fixture/live adapter paths it extracts page structure with the
stdlib HTML parser.

Page extraction policy:

- fixed user agent `seo-observer/competitor-research`;
- robots.txt checked when reachable;
- timeout defaults to 10 seconds;
- response byte cap defaults to 1000000;
- no auth, no session state, no login/paywall bypass;
- no retry on 401, 403, or 429;
- failed extraction is isolated per URL and marks the report partial.

Quality labels include `research-only`, `partial`, `blocked_by_robots`,
`forbidden`, `paywalled`, and `too_large`.

## Evidence Boundaries

Research rows are suggestions, not confirmed market facts. Reports always keep
research-only rows separate from confirmed SEO facts. In Task 05, confirmed
facts are intentionally empty; rank, traffic, share of voice, and movement
claims require deterministic SERP evidence from `competitors audit`.

The generated research report is still an artifact report. It is useful for
reviewing candidate competitors and page extracts, but it is not a complete
SEO report for a stakeholder. A composite report must merge this evidence with
provider audit and optional SERP artifacts before making prioritized findings
or recommendations. See `reporting-handoff-2026-07-31.md`.

Task 06 can consume these fields:

- `research_rows[].citation_id`
- `research_rows[].quality`
- `research_rows[].url`
- `research_rows[].title`
- `research_rows[].snippet`
- `page_extracts[].citation_id`
- `page_extracts[].headings`
- `page_extracts[].text_excerpt`
- `page_extracts[].json_ld_types`
- `page_extracts[].quality`
