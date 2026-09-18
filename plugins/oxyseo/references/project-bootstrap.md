# SEO Hub Project Bootstrap

This file documents the host-only setup shape for an SEO Hub project. It
intentionally contains no credentials, real provider payloads, or absolute user
paths.

## Layout

Use one portable root and keep every registry path relative to
`config/oxyseo.toml`:

```text
oxyseo/
  config/oxyseo.toml
  projects/example/project.toml
  secrets/example.env
  data/
  exports/
```

Secret env files must be regular files with mode `0600`. The registry may point
to them, but their values are loaded only through explicit allowlists and are not
written to run manifests.

## Registry Entries

Create one `[[projects]]` table per site using relative paths:

```toml
[[projects]]
id = "example"
label = "Example"
observer_config = "../projects/example/project.toml"
credentials_env_file = "../secrets/example.env"
elmo_base_url = "http://elmo:3000"
elmo_brand_id = "example"
openseo_mcp_url = "https://openseo.example.invalid/mcp"
openseo_project_id = "example"
```

Replace placeholder provider IDs on the host. Do not commit real service URLs
when they reveal private deployment details.

## Add-Project Checklist

1. Create `projects/<id>/project.toml` for observer config.
2. Create `secrets/<id>.env` on the host with mode `0600`.
3. Add one `[[projects]]` table and optional export profiles.
4. Run `oxyseo doctor --json --config config/oxyseo.toml`.
5. Run `oxyseo projects --json --config config/oxyseo.toml`.

Do not commit credentials, private service tokens, or real provider payloads.

## Secret Env Loading

Each project uses the same `credentials_env_file` flow. Put only the env names
required by the observer project config in `secrets/<id>.env`, then verify with
`oxyseo doctor --config config/oxyseo.toml --json`. Do not export these values
globally for hub runs.

## Relative-Path Repair

If a project was previously registered with paths that only worked from one
checkout, rewrite them to the portable layout above. The observer project file
must resolve relative to `projects/<id>/`, while the hub registry resolves its
path from `config/oxyseo.toml`.

## Read-Only Run Check

After native collectors have existing evidence, run a zero-cost import:

```bash
oxyseo run --project example --mode baseline --json
```

The run must create `<data_dir>/example/runs/<run_id>/manifest.json` with
source statuses and artifact hashes. It must not trigger Elmo prompt execution,
OpenSEO paid tools, outreach, publishing, or any provider-side mutation.
