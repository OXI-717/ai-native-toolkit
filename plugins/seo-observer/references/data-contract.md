# seo-observer data contract

The source of truth for deterministic SEO evidence is the local CLI package and
SQLite storage. Agents should describe and route this behavior, not recalculate
it.

## Storage

Local storage is SQLite under:

```text
$SEO_OBSERVER_HOME/projects/<project>/observer.db
```

When `SEO_OBSERVER_HOME` is unset, the home is `~/.seo-observer`. Raw
artifact references resolve under `projects/<project>/raw/`; raw payload blobs
are not stored in SQLite by default.

Storage v1 records project registry rows, source request attempts, raw artifact
references, quality/coverage metadata, observed facts, derived metrics,
actions, measurement windows, verdicts, and snapshot manifests. Attempts and
raw artifact references are append-only. Same-window fact re-ingest supersedes
the winning fact revision while preserving lineage.

Use `plugins/seo-observer/references/storage.md` for table-level details.

## Snapshot

Snapshot v1 is a portable JSON export built from local storage. It does not
call providers, schedule jobs, create production configs, or take SEO actions.

The schema lives at:

```text
plugins/seo-observer/cli/seo_observer/schemas/snapshot_v1.json
```

Snapshots include project/config identity, period metadata, schema versions,
privacy policy/redaction receipts, source coverage, evidence descriptors,
formula inputs, lineage, and export-approved evidence rows. Equivalent reruns
over the same facts produce stable hashes and paths.

Use `plugins/seo-observer/references/snapshots.md` for export and rendering
details.

## Quality States

Carry quality states from CLI/artifact metadata into the answer:

- `missing`: required evidence is absent.
- `partial`: only part of the requested source/window/grain is available.
- `stale`: evidence exists but is older than the configured freshness window.
- `local-only`: evidence is local storage/config/artifact state, not a live
  provider confirmation.
- `not_comparable`: populations, counting units, attribution, dedupe,
  timestamp/timezone, period, grain, keyword-set hash, or coverage semantics do
  not match.

When any state blocks a conclusion, state the block explicitly and do not fill
gaps with estimates.
