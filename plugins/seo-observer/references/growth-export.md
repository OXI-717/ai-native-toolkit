# Growth export (`seo-observer export`)

Publishes `growth_v1` bundles (private panel data — business aggregates only,
no person identifiers) as versioned, immutable build directories with atomic
symlink switching. Read-only over the project SQLite database; no live
provider calls.

```bash
seo-observer export --project demo --kind current --out /srv/growth/demo --json
seo-observer export --project demo --kind current --date 2026-09-20 --out /srv/growth/demo
seo-observer export --project demo --kind weekly --out /srv/growth/demo --panel-url https://panel.example/demo
seo-observer export --project demo --kind weekly --week-start 2026-09-14 --out /srv/growth/demo --no-pdf
```

## Kinds and windows

- `current`: 7-day window ending yesterday (`--date` overrides the end), plus
  a 28-day window with the same end; each window gets a previous window of the
  same length. One `growth.json`: the top level is the 7-day window; the 28-day
  KPI column lives in `derived.kpis_by_window["28d"]`.
- `weekly`: Monday–Sunday week. Default is the latest week whose Sunday is at
  least 3 days in the past (source finalization delay). `--week-start` selects
  a specific week and must be a Monday. Trend history is computed in memory
  for the 12 preceding weeks (never written to disk separately).

## Layout

```
DIR/
  builds/current-<YYYY-MM-DD>-<hash12>/
  builds/weekly-<start>_<end>-<hash12>/
  current        -> builds/current-…
  weekly/<start>_<end> -> ../builds/weekly-…
  latest-weekly  -> builds/weekly-…
  failed/<kind>-<window>-<utc>.receipt.json
```

Builds are written to `builds/.tmp-<uuid>/` and moved into place with
`os.rename` once every file is complete; symlinks swap via
`os.symlink` + `os.replace`. A failure anywhere removes the temp directory and
temp links — previously published builds and links are untouched. Re-running
an export whose data version is already built writes nothing and reports
`unchanged: true` (links are verified). For `weekly`, `unchanged` is `false`
when `latest-weekly` had to be (re)created, even if the build already existed
and no new files were written — links are re-pointed in that case.

`--date` applies only to `--kind current` and `--week-start` only to
`--kind weekly`; combining a flag with the wrong kind is a usage error
(`EXPORT_FLAG_KIND_MISMATCH`, exit code 2).

`weekly` files: `growth.json`, `index.html`, `report.pdf`, `brief.md`,
`receipt.json`. `current` files: `growth.json`, `index.html`, `receipt.json`.

`growth_hash` is the sha256 of the canonical `growth_v1` JSON with build-time
fields (`generated_at`/`produced_at`) removed; `sources.*.collected_at` is
part of the hash, so a re-collected day inside the same week produces a new
build with a new hash12 and the week link switches to it.

`receipt.json` fields: `schema_version`, `tenant`, `kind`, `window`,
`produced_at`, `growth_hash`, `files`, `pdf` (`{ok, error}`), and `sources`
(per-source `state`/`required`/`collected_at`/`timezone`).

## PDF policy

`weekly` renders `report.pdf` from `index.html` via Playwright Chromium
(`report_rendering.render_pdf_from_html`). If PDF rendering fails the week is
**not** published — neither `weekly/<window>` nor `latest-weekly` switches —
and the receipt is written under `failed/`; exit code is non-zero. Pass
`--no-pdf` on hosts without Chromium: the week is published and the receipt
records `pdf: {ok: false, error: "disabled"}`.

## Retention

- `builds/current-*`: the linked build plus the two newest unlinked builds are
  kept; older unlinked builds are removed.
- `builds/weekly-*`: unlinked builds older than 30 days are removed; builds
  referenced by `weekly/*` or `latest-weekly` are kept indefinitely.
- `failed/*.receipt.json`: removed after 30 days.
