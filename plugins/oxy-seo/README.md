# oxy-seo

`oxy-seo` is a read-only SEO intelligence hub. It provides the
installable `oxy-seo` CLI, a strict project registry parser, safe secret env-file
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
uv tool install --editable plugins/oxy-seo/cli
oxy-seo doctor --json
oxy-seo --json projects --config plugins/oxy-seo/examples/registry.toml
oxy-seo status --project example --json --config config/oxy-seo.toml
oxy-seo run --project example --mode full --json --config config/oxy-seo.toml
oxy-seo report --project example --run run_... --json --config config/oxy-seo.toml
oxy-seo opportunities --project example --run run_... --json --config config/oxy-seo.toml
oxy-seo outcomes --project example --json --config config/oxy-seo.toml
oxy-seo export --project example --run run_... --format observer-actions --json --config config/oxy-seo.toml
```

`--json` and `--config` are accepted before or after subcommands.

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
read-only SQLite summaries through the installed Observer API. Original source
dates and incomplete coverage are preserved; report generation time does not
make source data fresh. Imports do not approve baselines or refresh providers.

JSON data and action-draft exports use distinct filenames even when their
profiles share an output directory.

The registry schema is versioned with `schema_version = 1`. Relative paths are
resolved from the registry file directory and must stay under that directory's
parent root. Secrets live outside the registry in project env files with mode
`0600`; symlinks and path escapes fail closed.

## Semantic Smoke

The synthetic full-run fixture covers API, CLI client, observer report loading,
and draft-only export without public exposure or paid calls. The semantic smoke
keeps mention, citation, backlink, and branded query evidence separate. Stale
quality, missing conversion data, and changed keyword baskets are negative
signals; a changed basket is reported as not comparable rather than as a ranking
delta.

## Operations

Setup, add-project, export, backup, auth, update, and rollback are summarized in
`references/commands.md`, `references/project-bootstrap.md`, `references/runbook.md`,
and `references/deployment.md`. These docs contain no credentials. Owner-only
Tunnel/Access setup and zero-cost native cross-check stay in the post-orchestrate
checklist; worker sessions do not run them.
