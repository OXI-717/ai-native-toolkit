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

## `growth.json` data-availability fields

Each enabled search source has a `search.<source>` block, emitted even when
the source produced no facts in the window:

- `search.<source>.data_through` — `YYYY-MM-DD` or `null`. The last window
  day with a non-zero `total` fact; trailing zero-row days are provisional
  and excluded. Search KPIs sum only the leading days through this date and
  clip comparison windows to the same count of leading days.
- `search.<source>.totals_available` — boolean. True when at least one
  `total` fact exists in the window (explicit zeros count); false means
  absent data, not a measured zero, and search KPIs report coverage `none`.
- `search.<source>.search_gap` — boolean. True when `data_through` sits more
  than 4 days before the window end — a real collection gap, not the normal
  search reporting lag. A gapped source makes its KPIs `partial`, and a
  previous or history window whose own derivation would set this flag is not
  an eligible comparison (null delta / skipped avg4 week).
- `search.<source>.daily` / `queries` / `pages` — lists, empty when there
  are no facts.

## `growth.json` KPI delta fields (`derived.kpis.<name>` and
`derived.kpis_by_window.<window>.<name>`)

- `delta_pct`, `delta_vs_avg4_pct` — number or `null`. Percent change versus
  the previous window / the trailing 4-week average. Null when the
  comparison is not eligible (current KPI not `complete`, comparison window
  not fully covered or itself gap-flagged, missing value, zero or tiny
  base).
- `delta_abs`, `delta_vs_avg4_abs` — number or `null`. Absolute change in
  the KPI's own unit (impressions, clicks, visits, registrations, payments),
  filled **instead of** the percentage only when the comparison is eligible
  and the base is zero or, for count KPIs, below the tiny-base threshold of
  5. A null absolute field therefore does **not** prove the comparison was
  withheld — an eligible comparison on a normal base renders `*_pct` and
  leaves `*_abs` null.
- `avg4`, `avg_weeks` — `avg4` is the mean KPI value over up to the 4 most
  recent eligible history weeks (`avg_weeks` reports how many were used);
  `null`/`0` when none are eligible. Weeks that are empty or themselves
  gap-flagged do not count.

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
