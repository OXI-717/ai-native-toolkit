# SEO Hub Commands

The CLI follows a fixed order: install, doctor, list projects, inspect status,
run a read-only import, read reports, inspect opportunities/outcomes, then
export local handoff files.

```bash
uv tool install --force plugins/oxi-seo/cli
oxi-seo doctor --json
oxi-seo projects --json --config config/oxi-seo.toml
oxi-seo status --project example --json --config config/oxi-seo.toml
oxi-seo run --project example --mode full --json --config config/oxi-seo.toml
oxi-seo report --project example --run run_... --json --config config/oxi-seo.toml
oxi-seo opportunities --project example --run run_... --json --config config/oxi-seo.toml
oxi-seo outcomes --project example --json --config config/oxi-seo.toml
oxi-seo export --project example --run run_... --format observer-actions --json --config config/oxi-seo.toml
```

`--json` and `--config` are accepted before or after subcommands. `run` imports
existing read-only evidence only; it does not pass `--live`, trigger paid tools,
publish content, send outreach, or mutate provider state.

## Setup

Create the portable layout from `references/project-bootstrap.md`, install the
CLI package, then run `oxi-seo doctor --json --config config/oxi-seo.toml`.
Secret env files must be regular files with mode `0600`; do not commit
credentials or paste credential values into reports.

## Add-Project

Add one `[[projects]]` table with relative paths for the observer config and
secret env file. Add optional `[[projects.exports]]` tables for `json`,
`markdown`, or `observer-actions` profiles. Re-run `doctor` and `projects` to
validate registry parsing before any read-only import.

## Export

Exports write under the configured `export_dir` and reject path escapes. The
`observer-actions` format creates draft-only action JSON with
`side_effects: none`; it is a handoff artifact, not an execution command.

## Backup

Back up `config/`, `projects/`, `data/`, and `exports/` with host-native file
backup. Back up Elmo/OpenSEO databases with their service-native dump commands.
Do not copy live database volumes while services are writing.

## Auth

Local CLI runs use local no-auth scopes against the local registry. Browser or
service access for a real deployment is Cloudflare Access only and remains an
owner-only operational step.

## Update

Update source, reinstall the plugin from the marketplace, reinstall the CLI
package with `uv tool install --force "$PLUGIN_ROOT/cli"`, run `doctor`, then
run a zero-cost read-only smoke against existing evidence.

## Rollback

Stop services, restore the previous config/data/export backup, reinstall the
previous CLI/plugin version, and run `oxi-seo doctor --json`. Do not roll back
by opening origin ports or disabling Access policy.
