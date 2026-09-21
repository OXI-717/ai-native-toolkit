# AI Readiness

`seo-observer ai-readiness --project <name> --json` runs deterministic local checks for
AI-crawler readiness. It does not call LLMs or paid providers. The only network access is
plain HTTP GET requests to public URLs under each configured project property.

## JSON Contract

Top-level output contains:

- `llms_txt`: audit status, per-property HTTP status for `llms.txt`, `llms-full.txt`,
  sitemap evidence, findings, and a local `draft_artifact`.
- `ai_referrals`: recognized AI referral sources, unknown sources, the observed data
  window, and `evidence_quality`.

`llms_txt.status` is one of:

- `ok`: no error or warning findings.
- `warning`: the file is reachable and parseable, but sitemap comparison or secondary
  checks found drift.
- `invalid`: `llms.txt` exists but has broken structure or broken links.
- `missing`: `llms.txt` returned 404. This is a finding, not a command error.

The draft artifact is written locally as `llms-draft.txt` under the command output
directory. It is generated from already known URLs: configured property roots, sitemap
URLs when available, and URLs already declared in `llms.txt`. The command never publishes
or modifies the site.

## AI Referral Evidence

AI referrals are calculated only from current local `traffic_metrics` rows collected by
GA4 or Yandex Metrica. The current storage schema preserves GA4 `sessionSourceMedium` and
Metrica source/search labels in `traffic_metrics.search_engine`; the audit treats that
field as the local referrer/source evidence.

All AI-source recognition is heuristic and is marked with `heuristic: true`.
Unrecognized non-empty referrer/source values are emitted under `unknown`; they are not
silently classified as non-AI traffic.

`evidence_quality` is:

- `empty`: no current local traffic rows were available for the project.
- `heuristic`: at least one current local traffic source/referrer row was evaluated.

## Recognized Sources

Last checked: `2026-08-18`

The named registry lives in code as `AI_REFERRAL_SOURCES` with companion constant
`AI_REFERRAL_SOURCE_LAST_CHECKED`.

Recognized source IDs and matching domains:

- `chatgpt`: `chatgpt.com`, `chat.openai.com`, `openai.com`
- `claude`: `claude.ai`
- `copilot`: `copilot.microsoft.com`, `bing.com/chat`
- `gemini`: `gemini.google.com`, `bard.google.com`
- `perplexity`: `perplexity.ai`
- `phind`: `phind.com`
- `poe`: `poe.com`
- `you`: `you.com`
