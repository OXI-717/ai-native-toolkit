# seo-observer actions v1

The public CLI exposes a local action journal:

```bash
seo-observer actions add \
  --project demo \
  --changed-at 2026-07-08 \
  --type content_update \
  --url https://demo.example/landing/ \
  --query "agentic seo" \
  --description "Updated landing copy" \
  --artifact-ref pr:123 \
  --json

seo-observer actions list --project demo --json
seo-observer actions show action:abc123 --project demo --json
```

`actions add` records a minimal local change record: generated or supplied ID,
change date (`--changed-at` formatted as `YYYY-MM-DD`, without time or timezone), change type,
affected URLs and/or queries, description, and an artifact/PR reference.
Alternatively, `actions add --file <path>` or `actions ingest [--file <path>]` ingests declarative
`.seo-observer/actions/*.toml` files directly into `observer.db`. At least one URL or query is
required for CLI-created actions. The data is stored next to the rest of the project evidence
in `SEO_OBSERVER_HOME` SQLite storage, so it survives CLI restarts. `actions list` returns latest
action revisions for the selected project; `actions show` returns one action. This command does not edit
SEO assets, open pull requests, call live providers, or schedule autonomous work.

Deterministic action parsing, verdict computation, and local persistence are
implemented in:

```text
plugins/seo-observer/cli/seo_observer/actions.py
```

This layer is local only. It parses TOML action models, computes verdicts from
provided evidence values, and writes metadata/verdict rows to storage v1. It
does not call SEO providers, schedule evaluations, open pull requests, or take
autonomous SEO actions.

## Action files

Action files live under:

```text
.seo-observer/actions/<action-id>.toml
```

The parser uses Python stdlib `tomllib`. The implemented model requires:

- `action_id`
- `project_id`
- `changed_at`
- `action_type`
- `description`
- `hypothesis_id`
- `evidence_ref`
- `lifecycle_state`
- `[[targets]]`
- `[[windows]]`
- `[[expected_signals]]`

Supported lifecycle states are `planned`, `active`, `done`, and
`tombstoned`. Tombstoned actions compute non-actionable `not_ready` verdicts
with `comparability_state = "tombstoned"`.

The action revision hash is a stable SHA-256 over the normalized TOML model.
Formatting and TOML comments do not affect the hash.

## Verdicts

`action_evidence_from_snapshots(action, window, baseline_snapshot,
observation_snapshot, *, as_of, ...)` derives the explicit `ActionEvidence`
object from committed snapshot JSON. By default it reads the first expected
signal's `metric_path`, using the same dotted path semantics as period
comparisons. It reads manifest `snapshot_hash` values into
`historical_snapshot_hashes`, carries logical evidence IDs and evidence row IDs
into deterministic evidence hashes, and derives `comparability_state` from the
snapshot evidence quality (`missing`, `stale`, `sampled`, `incomparable`,
`partial`, or `comparable`). The denominator is the baseline metric value,
matching period-comparison denominator semantics.

`compute_action_verdict(action, window, evidence)` is deterministic. Inputs are
the parsed action, one measurement window, and an explicit `ActionEvidence`
object containing baseline/observation values, denominator, observation age,
snapshot hashes, and evidence hashes. Verdict inputs should be reproducible
from committed snapshot JSON through `action_evidence_from_snapshots`; callers
should not manually assemble hashes when snapshot evidence is available.

Verdict readiness is blocked by:

- tombstoned lifecycle state;
- denominator below the window minimum;
- evaluation before `earliest_evaluation_date`;
- fewer than the expected signal minimum observation days;
- active confounders;
- non-comparable evidence state.

Ready evidence is compared against the first expected signal's practical-effect
thresholds. Current verdict values are `positive`, `negative`,
`inconclusive`, and `not_ready`.

## Storage

`persist_action_verdict(storage, action, window, verdict)` writes to existing
storage v1 tables:

- `seo_actions`
- `action_targets`
- `measurement_windows`
- `action_verdicts`

Action metadata and measurement windows are idempotently recorded. Verdict rows
are append-only: each new verdict has its own deterministic `verdict_id` and
does not overwrite prior rows. Supersession is represented by
`supersedes_verdict_id`.
