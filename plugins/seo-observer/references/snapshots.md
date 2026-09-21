# seo-observer snapshots v1

Snapshots are deterministic, portable JSON exports built only from local SQLite
storage. They do not call SEO providers, schedule collection, create GitHub
Actions, or take SEO actions.

The Python API is implemented in:

```text
plugins/seo-observer/cli/seo_observer/snapshots.py
```

The schema contract is:

```text
plugins/seo-observer/cli/seo_observer/schemas/snapshot_v1.json
```

The CLI `snapshot`, `report`, and `weekly` commands are safe local/export
surfaces when an export-approved committed baseline exists. They rebuild
disposable SQLite state from the committed JSONL baseline, write deterministic
snapshot/report artifacts, and do not call live SEO providers.

## Export contract

`snapshot_schema_version = 1`.

`build_snapshot(storage, project_id, reporting_period_id, generated_at, ...)`
returns one self-contained JSON object with:

- project identity and config hash;
- period metadata;
- semantic versions for CLI, storage schema, snapshot schema, and renderer;
- privacy/export policy and redaction receipts;
- manifest metadata, source coverage, logical evidence descriptors, and stable
  evidence IDs;
- coverage counters;
- formula inputs;
- lineage rows for collection runs, source requests, and raw artifacts;
- export-approved evidence rows.

Equivalent reruns over the same storage facts produce the same `snapshot_hash`,
stable evidence IDs, and stable Git path. `generated_at` is retained in the
manifest but excluded from the stable snapshot hash.

`write_snapshot(...)` writes canonical JSON with a trailing newline under:

```text
snapshots/v1/<project>/<reporting-period>/snapshot-<hash-prefix>.json
```

The path is intended to be tracked by Git when the caller chooses a tracked
artifact root. The writer enforces the export policy before the JSON file is
created.

## Privacy

The default `ExportPolicy` forbids exact local-only business values in Git
artifacts. For outcome metrics, exact count/actor and monetary fields are
redacted before export in both evidence rows and formula inputs:

- `count`
- `unique_actors`
- `value_minor`
- `currency`

Each redaction is recorded in `privacy.redactions` with a JSON path and reason.
Provider evidence rows such as Google Search Console search performance facts
remain exportable because they are already normalized evidence, not local CRM
business value exports.

## Reports

`render_markdown_report(snapshot)` and `render_telegram_summary(snapshot)` read
only the snapshot object. They do not query SQLite and do not recompute source
metrics independently. Both renderers consume the same `formula_inputs` block.

The Telegram renderer is bounded by `max_chars` and truncates deterministically.

## Rebuild support

`rebuild_storage_from_snapshot(snapshot, storage)` bootstraps a disposable
SQLite database and restores the exported project, lineage, artifacts, and
search performance evidence rows from the snapshot. The public CLI uses the same
principle for baseline exports: read committed/export-approved local evidence,
write disposable state, and keep live provider collection outside these render
commands.
