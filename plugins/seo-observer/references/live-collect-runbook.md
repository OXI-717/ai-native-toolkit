# seo-observer live collect runbook

`seo-observer collect` is the live-provider collection entrypoint for the SEO
observer. It is intentionally separate from `weekly`, `snapshot`, and `report`:
`collect` talks to configured provider APIs and updates the local evidence
store; the reporting commands render from already-approved local evidence.

## Supported live sources

The public collect path currently supports these configured source bindings:

- `google_search_console` → stores normalized search rows in
  `search_performance`.
- `yandex_webmaster` → stores normalized popular-query rows in
  `search_performance`.
- `yandex_metrica` → stores organic traffic rows in `traffic_metrics`.

Configured enabled sources outside this set, such as `serp`, `wordstat`, and
`outcome_*`, are reported as `unsupported` in the JSON payload. They do not
block supported source collection. Do not invent rows for unsupported sources.

## Command shapes

Use one explicit project selector:

```bash
seo-observer collect --project demo --json
seo-observer collect --config .seo-observer/project.toml --json
```

Default period is `30d`, ending yesterday according to the local runtime date:

```bash
seo-observer collect --project demo --period-id 30d --json
```

For reproducible backfills, pass explicit dates:

```bash
seo-observer collect --project demo --start 2026-06-28 --end 2026-07-27 --json
```

`--start` and `--end` must be supplied together as ISO dates. `start > end`
returns `COLLECT_PERIOD_INVALID`.

## Inputs and readiness

Readiness is checked only for supported planned sources. Required unsupported
sources are surfaced as `unsupported` rather than `COLLECT_NOT_READY`.

Credential inputs come from the project config:

- GSC uses `credential_file_env`; the env var must point to an existing service
  account JSON file.
- Yandex Metrica and Yandex Webmaster use `credential_env`; the env var must
  contain the API token.

Missing required supported inputs return structured JSON with exit code 2:

```json
{
  "ok": false,
  "error": {
    "code": "COLLECT_NOT_READY",
    "details": {
      "missing_required_inputs": [
        {
          "source": "google_search_console",
          "env": "GSC_SA_JSON_PATH",
          "reason": "missing environment variable"
        }
      ]
    }
  }
}
```

## Outputs

Successful `collect --json` returns:

- `run_id`: unique identity for this live run.
- `database_path`: SQLite evidence store path.
- `sources`: per-source status, observations received, observations written,
  and optional error details.
- `source_status`: aggregate counts for ok/partial/failed/unsupported sources
  and observations.

Example:

```json
{
  "ok": true,
  "command": "collect",
  "project": "demo",
  "period_id": "30d",
  "period": {
    "start": "2026-06-28",
    "end": "2026-07-27",
    "timezone": "Europe/Moscow"
  },
  "database_path": "~/.seo-observer/projects/demo/observer.db",
  "sources": {
    "google_search_console": {
      "status": "ok",
      "observations": 4,
      "observations_written": 4
    },
    "yandex_metrica": {
      "status": "ok",
      "observations": 3,
      "observations_written": 3
    },
    "yandex_webmaster": {
      "status": "ok",
      "observations": 408,
      "observations_written": 408
    }
  }
}
```

If a required provider fails after collection starts, successful source evidence
is still written when possible and the command returns `COLLECT_PARTIAL`.
Failed attempts are persisted in `collection_runs` / `source_requests` with the
provider error code and summary.

Storage or normalization failures return `COLLECT_STORAGE_ERROR` instead of a
traceback.

Provider rows that are valid API responses but cannot satisfy the target
storage invariants are reported as source-level `partial`, not as generic
storage failures. Example: Yandex Webmaster can return `TOTAL_CLICKS >
TOTAL_SHOWS` for specific rows. Those rows are retained in the raw artifact,
excluded from `search_performance`, and counted in `invalid_observations` with
`NORMALIZATION_INVALID_SEARCH_ROW`; valid rows from the same source are still
written.

## Evidence layout

Default observer home:

```text
~/.seo-observer
```

Override:

```bash
export SEO_OBSERVER_HOME=/path/to/observer-home
```

For project `demo`, live collect writes:

```text
$SEO_OBSERVER_HOME/projects/demo/observer.db
$SEO_OBSERVER_HOME/projects/demo/raw/<run_id>/<source>-<property>-<sha>.json
```

The SQLite `raw_artifacts` row stores `relative_path`, `sha256`, content type,
redaction state, and byte size. The raw artifact file is written under the
project raw root and its sha must match the DB row.

## Re-running the same window

Same-window live collection is allowed and expected.

- Every live run gets a unique `run_id`.
- Every source request gets a unique `collection_attempt_key`.
- Raw artifacts are append-only.
- `search_performance` keeps only the newest matching natural key as
  `is_current = 1` and supersedes the previous current fact.
- `traffic_metrics` marks older matching organic traffic rows
  `is_current = 0` before inserting the newest row.

Use this pattern to verify local state:

```bash
sqlite3 ~/.seo-observer/projects/demo/observer.db \
  "select count(*) from collection_runs;"

sqlite3 ~/.seo-observer/projects/demo/observer.db \
  "select source, count(*) from search_performance where is_current = 1 group by source;"

sqlite3 ~/.seo-observer/projects/demo/observer.db \
  "select source, count(*) from traffic_metrics where is_current = 1 group by source;"
```

## Demo operational baseline

From `demo-infra`, the wrapper can be used when it is available:

```bash
./seo-observer.sh collect --project demo --json
```

Acceptance shape for the current Demo setup:

- command exits 0;
- `ok` is `true`;
- GSC, Yandex Metrica, and Yandex Webmaster report `ok`;
- `observations_received == observations_written`, unless a source explicitly
  reports `partial` with `invalid_observations` and a normalization error;
- current search rows exist in `search_performance`;
- current Metrica organic traffic rows exist in `traffic_metrics`;
- at least one raw artifact file exists and its sha matches `raw_artifacts`.

## Agent interpretation rules

- Do not describe `weekly` as live collection. `weekly` remains a local
  baseline/export report until a separate live weekly workflow is implemented.
- Do not map Metrica visits/users into search impressions/clicks.
- Do not compare windows unless both windows were collected through comparable
  protocols and the artifact metadata says they are comparable.
- Do not expose tokens, service-account JSON, raw private payloads, row-level
  users, or production secrets in chat/report output.
- Treat `NOT_IMPLEMENTED`, `COLLECT_NOT_READY`, `COLLECT_PARTIAL`, and
  `COLLECT_STORAGE_ERROR` as authoritative structured statuses.
