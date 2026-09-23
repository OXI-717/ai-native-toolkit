# seo-observer

`seo-observer` is an evidence-first SEO observer. It provides the
installable `seo-observer` CLI plus a shared skill for weekly snapshots,
period comparisons, action outcome measurement, opportunity reports,
competitor intelligence, AI-search readiness checks, and data-quality
guardrails.

All evidence lives in a local SQLite store under the observer home. Live
provider calls happen only when you explicitly ask for collection and have
configured credentials; every provider key is read from environment
variables you supply — nothing is baked in.

## Install

```bash
uv tool install --force plugins/seo-observer/cli
seo-observer doctor --json
```

For a marketplace installation, use `uv tool install --force "$PLUGIN_ROOT/cli"`
with the installed plugin directory. This copies the package into the tool
environment rather than linking it to a versioned cache directory. Repeat
after plugin updates. Avoid editable installs from plugin caches or
temporary clones.

`doctor` without a project config is an installation smoke check: it
reports which sources are configured and which credentials are present,
without live provider calls.

## Quick start

```bash
mkdir -p .seo-observer
seo-observer doctor --config .seo-observer/project.toml --json
seo-observer projects register myproject --config .seo-observer/project.toml --json
seo-observer snapshot --project myproject --json
seo-observer report --project myproject --json
```

A minimal `project.toml` needs `config_schema_version = 1`, top-level
`project = "myproject"` and `timezone = "UTC"`, at least one `[[properties]]` entry with a URL, and a
`[[keyword_sets]]` entry pointing at a keyword file. Provider integrations
(Google Search Console, Yandex Webmaster/Metrica/Wordstat, DataForSEO, Exa,
Topvisor) are optional and configured per source; each declares a
`credential_env` naming the environment variable that holds the credential.
The full schema and per-provider notes live in
`references/project-config.md` and `references/provider-notes.md`.

## Command map

`snapshot`, `weekly`, `report`, `compare`, `actions`, `outcomes`,
`opportunities`, `composite-report`, `ai-readiness`, `provider-audit`,
`competitors discover|audit|research|report`, `content-gap`,
`keyword-research`, `render-report`, `projects`, `doctor`. Flags and
contracts are documented in `references/commands.md`; the evidence model is
in `references/data-contract.md`.

## Companion

`seo-hub` (the SEO hub plugin) imports Observer snapshots and reports
through the `seo-observer` executable installed above — install this package
first when you use the hub's `baseline`/`full` run modes.
