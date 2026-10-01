# Tenant runtime

One container per tenant: `supercronic` runs `collect`, `export`, and `backup`
on a schedule; `seo-observer serve` exposes the `exports/` directory and
`/health` on the internal network only. The only ingress is a `cloudflared`
tunnel fronted by Cloudflare Access — the runtime service publishes no ports.

## Layout

- `Dockerfile` — Python 3.12 slim + `seo-observer[pdf]` + Chromium +
  supercronic (pinned, checksum-verified) + `age` + `rclone` + `sqlite3`.
  Runs as unprivileged user `observer` (uid 10001).
- `entrypoint.sh` — renders the crontab, starts supercronic and
  `seo-observer serve`, exits if either dies.
- `crontab.default` — default schedule (tenant time via `CRON_TZ`).
  A `/config/crontab` file, when mounted, replaces it entirely.
- `render_crontab.py` — `${VAR}` substitution from the environment. Unknown
  variables abort startup; `SEO_PANEL_URL_FLAG` is derived from
  `SEO_PANEL_URL`.
- `compose.example.yml` — two-service deployment example (`runtime` +
  `cloudflared`). Secrets are split: `runtime.env` holds the tenant
  source/backup variables for `runtime`, `tunnel.env` holds only
  `TUNNEL_TOKEN` for `cloudflared`, so the tunnel token can never reach
  the runtime container. Neither file is committed.

## Environment contract

| Variable | Meaning | Example |
|---|---|---|
| `SEO_OBSERVER_CONFIG` | path to the tenant `project.toml` inside the container | `/config/project.toml` |
| `SEO_OBSERVER_HOME` | data root | `/data` |
| `TZ` (via `CRON_TZ`) | schedule timezone of the tenant | `Europe/Moscow` |
| `SEO_PANEL_URL` | public panel URL embedded into `brief.md` | `https://growth.demo.example/` |
| `BACKUP_AGE_RECIPIENT` | owner `age` public key | `age1…` |
| `BACKUP_REMOTE` | rclone target | `spaces:demo-bucket/observer` |
| `RCLONE_CONFIG_SPACES_*` | rclone S3 settings via env (`TYPE=s3`, `PROVIDER=DigitalOcean`, `ENDPOINT`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`) | — |
| `TUNNEL_TOKEN` | `cloudflared` service only, via `tunnel.env` | — |

Plus every credential env var named by the tenant `project.toml`
(`credential_env` of each enabled provider/source) — names live in the config,
values only in the deploy `runtime.env` (never committed).

## Default schedule (tenant local time)

| Time | Job |
|---|---|
| 04:30 | `backup` — encrypted `observer.db` copy, 3 local + remote |
| 05:00 | `collect --daily --days 3` — re-collects the last finished days |
| 05:30 | `export --kind current` |
| Fri 06:00 | `export --kind weekly` |

`observer.db` is a SQLite database in WAL mode with `busy_timeout`, so backup
and exports read a consistent snapshot while a collect is running. supercronic
never overlaps two instances of the same job.

## Deploy steps (anonymized)

1. Create a Cloudflare Tunnel, point its ingress at `http://runtime:8080`,
   write `TUNNEL_TOKEN=…` into `tunnel.env` (cloudflared only).
2. Add a Cloudflare Access application on the tunnel hostname (email policy +
   service tokens for machine pollers).
3. Put the tenant `project.toml` (and optionally `crontab`) into `./config`,
   tenant secrets into `runtime.env`.
4. `docker compose -f compose.example.yml up -d`.
5. Verify: `GET /health` without credentials redirects to Access; with a
   service token it returns JSON (`200` healthy, `503` with `reasons`).

Full contract details and recovery procedure: `references/runtime.md`.
