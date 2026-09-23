# seo-hub

`seo-hub` is a read-only SEO intelligence hub. It provides the
installable `seo-hub` CLI, a strict project registry parser, safe secret env-file
loading, local run manifests, read-only evidence adapters, and draft-only
exports.

The MVP does not call providers live, trigger paid tools, deploy services,
publish reports, execute outreach, or expose origins publicly. Runs import
existing SEO/AI-search evidence and preserve source quality in local artifacts.

**Not in v1:** live Topvisor, Yandex/Google Search Console cabinets, Metrica, or
on-demand DataForSEO collection. Bring your own already-collected OpenSEO / Elmo
/ Observer artifacts and credentials in env files. Those connectors are roadmap,
not this release.

## CLI

```bash
uv tool install --force plugins/seo-hub/cli
seo-hub doctor --json
seo-hub --json projects --config plugins/seo-hub/examples/registry.toml
seo-hub status --project example --json --config seo-hub.toml
seo-hub run --project example --mode full --json --config seo-hub.toml
seo-hub report --project example --run run_... --json --config seo-hub.toml
seo-hub opportunities --project example --run run_... --json --config seo-hub.toml
seo-hub outcomes --project example --json --config seo-hub.toml
seo-hub export --project example --run run_... --format observer-actions --json --config seo-hub.toml
```

`--json` and `--config` are accepted before or after subcommands.

For a marketplace installation, use `uv tool install --force "$PLUGIN_ROOT/cli"`
with the installed plugin directory. This copies the package into the tool
environment rather than linking it to a versioned cache directory. Repeat after
plugin updates. Avoid editable installs from plugin caches or temporary clones.

`doctor` without a registry is an installation smoke check only: top-level
`ok: true` can accompany `checks.config.status: "not_ready"`. Project setup
requires a configured registry and `checks.config.status: "ready"`; successful
read-only imports verify provider readiness.

## Registry

Run the local panel with `python -m seo_hub.server --config <registry>
--host 127.0.0.1 --port 8080` and `server.auth_mode = "local_noauth"`.
HTTP serves the same persisted runs as the CLI. `cloudflare_access` mode
requires a verified RS256 Access assertion; the owner audience grants the UI,
while the separate service audience grants the read API. Local mode cannot
bind to a public interface.

Native service credentials may be kept in the optional project field
`provider_credentials_env_file`, separate from Observer credentials. Allowlisted
names are `ELMO_SESSION_COOKIE` or `ELMO_API_KEY`, `OPENSEO_API_KEY`,
`OPENSEO_ACCESS_CLIENT_ID`, and `OPENSEO_ACCESS_CLIENT_SECRET`. Never put their
values in the registry. Local OpenSEO MCP permits anonymous loopback access;
remote imports require configured credentials. A configured URL alone is not
proof of connectivity: provider readiness comes from successful imports.

The installed Elmo 0.3.0 session contract uses its pinned native read functions.
The opportunities endpoint is excluded because it can generate provider work.
Empty native projects do not provide AI visibility or ranking evidence.

Observer snapshot/report imports read existing verified audit artifacts and
read-only SQLite summaries through the installed Observer API. The Observer is
a prerequisite, not a dependency: `baseline` and `full` runs and every
existing-evidence import invoke the separately installed `seo-observer`
console tool (a Python console-script installation exposing the `seo_observer`
API). The CLI package does not install it, and without that executable those
paths fail with `OBSERVER_API_UNAVAILABLE`. The Observer companion is
published as the `seo-observer` plugin in the same marketplace — install it
and run `uv tool install --force "$PLUGIN_ROOT/cli"` from its plugin
directory. A configured project still runs without it — `observer.*`
sources report the failure while Elmo and OpenSEO imports proceed
independently. Original source dates and incomplete coverage
are preserved; report generation time does not make source data fresh. Imports
do not approve baselines or refresh providers.

JSON data and action-draft exports use distinct filenames even when their
profiles share an output directory.

The registry schema is versioned with `schema_version = 1`. Relative paths are
resolved from the registry file directory and must stay under that directory's
parent root. Secrets live outside the registry in project env files with mode
`0600`; symlinks and path escapes fail closed.

Every source is optional and configured per project: `observer_config`,
`credentials_env_file`, the `elmo_base_url` + `elmo_brand_id` pair, and the
`openseo_mcp_url` + `openseo_project_id` pair may each be present or absent.
Partial pairs are rejected at load time, and a run reports unconfigured sources
as `skipped` instead of failing them. The `[server]` section is required only
for the HTTP panel (`python -m seo_hub.server`); `auth_mode = "local_noauth"`
needs only `bind` and `owner_email`, while `cloudflare_access` additionally
requires the Access keys. A minimal external setup is a registry with a single
project that points at an Observer `project.toml` — no server block and no
native-service fields are needed.

## Semantic Smoke

The synthetic full-run fixture covers API, CLI client, observer report loading,
and draft-only export without public exposure or paid calls. The semantic smoke
keeps mention, citation, backlink, and branded query evidence separate. Stale
quality, missing conversion data, and changed keyword baskets are negative
signals; a changed basket is reported as not comparable rather than as a ranking
delta.

## Operations

Setup, add-project, export, backup, auth, update, and rollback are summarized in
`references/commands.md`, `references/project-bootstrap.md`, and
`references/runbook.md`. Owner-only Docker/Tunnel deployment notes live in
the source repository and are not part of the published plugin package. These docs contain no credentials. Owner-only
Tunnel/Access setup and zero-cost native cross-check stay in the post-orchestrate
checklist; worker sessions do not run them.
