# Tenant runtime

One container per tenant. Inside: `supercronic` (collect/export/backup on a
schedule) and `seo-observer serve` (read-only static exports + `/health`).
Outside: `cloudflared` is the only ingress; Cloudflare Access authorizes.
There is no authentication inside the runtime — never publish its port.

```
            ┌──────────────────────────── container ───────────────────────────┐
            │  supercronic ──► collect --daily ──► observer.db (WAL)           │
            │              ──► export --kind current|weekly ──► /data/exports  │
            │              ──► backup ──► observer-*.db.age ──► rclone remote  │
            │  seo-observer serve ──► :8080 /health + /data/exports            │
            └──────────────────────────────┬───────────────────────────────────┘
   internal network (no published ports)   │
            ┌──────────────┐               │
            │ cloudflared  ◄───────────────┘  http://runtime:8080
            └──────┬───────┘
                   ▼  Cloudflare Tunnel → Access (email + service tokens)
```

Assets live in `plugins/seo-observer/runtime/`; see its `README.md` for
the file inventory.

## Environment contract

| Variable | Meaning | Example |
|---|---|---|
| `SEO_OBSERVER_CONFIG` | tenant `project.toml` path in the container | `/config/project.toml` |
| `SEO_OBSERVER_HOME` | data root | `/data` |
| `TZ` / `CRON_TZ` | schedule timezone of the tenant | `Europe/Moscow` |
| `SEO_PANEL_URL` | public panel URL for `brief.md` | `https://growth.demo.example/` |
| `BACKUP_AGE_RECIPIENT` | owner `age` public key | `age1…` |
| `BACKUP_REMOTE` | rclone target | `spaces:demo-bucket/observer` |
| `RCLONE_CONFIG_SPACES_*` | rclone S3 settings via env (`TYPE=s3`, `PROVIDER=DigitalOcean`, `ENDPOINT`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`) | — |
| `TUNNEL_TOKEN` | only on the `cloudflared` service, via `tunnel.env` | — |

Plus every `credential_env` named by the tenant `project.toml`. Secret values
are split between two uncommitted files: `runtime.env` (tenant source and
backup variables, loaded by the `runtime` service) and `tunnel.env` (only
`TUNNEL_TOKEN`, loaded by `cloudflared`) — the tunnel token never reaches the
runtime container. Neither file reaches `/health`, logs, state files,
or receipts.

The tenant namespace (database path component) is read from the config by
`entrypoint.sh`, not from env. A mounted `/config/crontab` replaces
`crontab.default` entirely — this is where a tenant adds provider-specific
paid jobs (rank tracking spends credits, so it is opt-in per tenant).

## Default schedule (tenant local time)

- 04:30 `backup --db /data/projects/<namespace>/observer.db` — encrypted
  snapshot, 3 local copies + remote upload.
- 05:00 `collect --daily --days 3 --state-file /data/state/collect.json` —
  re-collects the last 3 finished days; revisions supersede.
- 05:30 `export --kind current --out /data/exports`
- Fri 06:00 `export --kind weekly --out /data/exports [--panel-url …]`

SQLite runs in WAL with `busy_timeout = 5000`, so the 04:30 backup and 05:30
export read consistent snapshots while a collect is in flight. supercronic
never runs a second instance of a job while the first is still running.

## `/health`

`GET /health` answers from the collect state journal and export receipts; it
never opens the database.

- `200` — healthy.
- `503` — unhealthy; `reasons` lists every triggered cause:
  `no_collect_recorded` (no collect yet), `collect_state_unreadable`,
  `collect_stale` (last successful collect older than 26 h),
  `required_source_down:<name>` (a required source not `live` in two
  consecutive collects), `current_export_stale`.

```json
{"status": "ok", "reasons": [],
 "last_collect": {"finished_at": "2026-09-25T02:05:00Z", "ok": true},
 "last_successful_collect_at": "2026-09-25T02:05:00Z",
 "current_export_at": "2026-09-25T05:35:00Z",
 "latest_weekly_export_at": "2026-09-19T06:10:00Z",
 "sources": {"google_search_console": {"live": true, "required": true}}}
```

The container `HEALTHCHECK` accepts `200` **and** `503`: it checks that the
server process is alive, not that the data is fresh. Data freshness alerting
belongs to the external poller (n8n), which treats `503` as the alert.

## Backup

Daily at 04:30: `sqlite3` online backup API → `age` encryption
(`BACKUP_AGE_RECIPIENT`) → `rclone copyto` to `BACKUP_REMOTE`. Artifacts are
`observer-<UTC timestamp>.db.age`; the 3 newest are kept locally. A plaintext
snapshot never survives a run, whatever the outcome.

## Recovery (step by step)

1. Stop the runtime container (`docker compose stop runtime`) so nothing
   writes `observer.db` during restore.
2. Fetch the wanted copy:
   `rclone copy spaces:demo-bucket/observer/observer-20260925T043000Z.db.age /tmp/`
   (or pick it from `/data/backups` if still local).
3. Decrypt with the owner private key:
   `age -d -i key.txt -o /tmp/observer.db /tmp/observer-….db.age`
4. Sanity-check the restored file on a test bench first:
   `sqlite3 /tmp/observer.db 'PRAGMA integrity_check;'` and a row count on
   `search_performance`.
5. Replace `/data/projects/<namespace>/observer.db` with the restored file
   (remove stale `-wal`/`-shm` sidecars), owner `observer`.
6. Start the container and confirm `/health` returns to `200` after the next
   scheduled collect.

## First deploy

1. Stop any local collect/export jobs for the tenant — `observer.db` must
   have exactly one writer.
2. Copy the existing `observer.db` into the volume at
   `/data/projects/<namespace>/observer.db` (or skip to start fresh).
3. Mount `project.toml` (and optional `crontab`) at `/config`, write
   `runtime.env` with the contract above and `tunnel.env` with only
   `TUNNEL_TOKEN`, `docker compose -f compose.example.yml up -d`.
4. Verify through the tunnel: `GET /health` without credentials redirects to
   Cloudflare Access; with a service token (`CF-Access-Client-Id/Secret`
   headers) it returns the JSON above. `GET /current/` serves the panel once
   the first `export --kind current` has run.
