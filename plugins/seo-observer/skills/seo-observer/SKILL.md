---
name: seo-observer
description: Use when producing evidence-first SEO snapshots, comparisons, outcome reports, opportunity research, or AI-search readiness checks.
---

# seo-observer

Use when the user asks for SEO observer work: project-scoped SEO snapshots,
weekly reports, period compare, action outcomes, opportunity/priority reports
(quick wins, second-page, decay, cannibalisation, refresh, crawl findings), AI-search/llms.txt
readiness, competitors/share of voice, keyword demand/research, or config/doctor troubleshooting.

This skill is a thin orchestration and interpretation layer over the local
`seo-observer` CLI and Python package. Do not duplicate deterministic
calculations, formulas, hashes, comparability logic, storage rules, privacy
redaction, or provider normalization in the agent prompt.

## Runtime root

Resolve references through the installed plugin root instead of assuming a repo
checkout-relative path:

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-<installed-plugin-root>}}"
```

Project config schema is in `$PLUGIN_ROOT/references/project-config.md`: it uses
`config_schema_version = 1`, top-level `project` as storage namespace, `timezone`,
`[[properties]]`, `[sources.*]`, `[[source_bindings]]`, and `[[keyword_sets]]`.

## Workflow

1. Orient: identify project, requested window, artifact target, and whether the user asks for collect/live data, weekly, snapshot, report, compare, actions,
   outcomes, opportunities (quick wins, second-page, decay, cannibalisation, refresh, crawl findings),
   ai-readiness (llms.txt audit, AI-referral traffic), competitors, or keyword research. Do not make live provider calls unless the user
   explicitly asks and valid local credentials/config already exist.
2. Doctor/config: run `seo-observer doctor --json`, adding `--config` or
   `--project` when supplied. If config is invalid or absent, stop with the
   exact CLI status and say what data is missing.
3. Execute the narrow CLI path from `$PLUGIN_ROOT/references/commands.md`. Use
   `collect` only for explicit live-provider collection; use `provider-audit`
   for explicit source-level live exports that produce local manifest/extract
   and Markdown artifacts without sending Telegram. Use `competitors discover`,
   `competitors audit`, `competitors research`, and `competitors report` for
   competitor intelligence. Use `composite-report` to merge existing provider,
   research, content, optional SERP, and optional metrics artifacts into one
   evidence-aware local report. Use public `weekly`, `snapshot`, and `report`
   commands for export-approved local baselines. If another public CLI command
   returns `NOT_IMPLEMENTED`, report that status instead of inventing results;
   use documented Python APIs only when the user asked for local artifact work.
4. Interpret quality before conclusions. Label evidence as `missing`,
   `partial`, `stale`, `local-only`, or `not comparable` / `not_comparable`
   when the CLI/artifact metadata says so. Explain what can and cannot be
   inferred from that state.
5. Cite artifacts: include generated snapshot/report paths, hashes, config path,
   and source/status metadata. Never expose credentials, raw private payloads,
   row-level users, exact local-only business values, or production secrets.

## Progressive Disclosure

Load only the references needed for the current request:

- Project config: `$PLUGIN_ROOT/references/project-config.md`
- Data contract, storage, snapshot: `$PLUGIN_ROOT/references/data-contract.md`
- Live provider collection: `$PLUGIN_ROOT/references/live-collect-runbook.md`
- Provider notes: `$PLUGIN_ROOT/references/provider-notes.md`
- Report interpretation: `$PLUGIN_ROOT/references/report-interpretation.md`
- Command routing: `$PLUGIN_ROOT/references/commands.md`
- Opportunity reports (quick wins, second-page, decay, cannibalisation, refresh, crawl findings):
  `$PLUGIN_ROOT/references/opportunities.md`
- AI-search readiness (llms.txt audit, AI-referral traffic):
  `$PLUGIN_ROOT/references/ai-readiness.md`
- Local change journal (`actions`): `$PLUGIN_ROOT/references/actions.md`

Provider-specific details remain in `gsc.md`, `metrica.md`, `webmaster.md`,
`wordstat.md`, `serp.md`, and `outcomes.md`; load them only when that source is
in scope.

## Forward Test Prompts

Use these prompts to validate routing, invariants, and guardrails without
encoding final SEO answers:

- `$PLUGIN_ROOT/skills/seo-observer/forward-tests/weekly-partial-sources.md`
- `$PLUGIN_ROOT/skills/seo-observer/forward-tests/seo-action-not-comparable.md`
