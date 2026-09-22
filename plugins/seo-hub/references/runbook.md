# SEO Hub Runbook

SEO Hub MVP is read-only. Operators may validate local config, import existing
evidence, inspect reports, and export local handoff artifacts. They must not
start live provider collection, run paid prompts/tools, expose origins publicly,
publish reports, or execute generated actions from this MVP flow.

## Daily Smoke

```bash
seo-hub doctor --json --config config/seo-hub.toml
seo-hub projects --json --config config/seo-hub.toml
seo-hub status --project example --json --config config/seo-hub.toml
```

If config readiness is not green, fix paths or file permissions first. Secret
files must stay mode `0600`; do not commit credentials.

## Read-Only Full Run

```bash
seo-hub run --project example --mode full --json --config config/seo-hub.toml
seo-hub report --project example --run run_... --json --config config/seo-hub.toml
seo-hub export --project example --run run_... --format observer-actions --json --config config/seo-hub.toml
```

The expected result is a local run manifest, local observer artifacts, and
draft-only exports. Semantic review keeps mention, citation, backlink, and
branded query evidence separate. Stale evidence, missing conversion data, and a
changed keyword basket are negative quality signals; a changed basket makes the
run not comparable to the previous basket.

## Post-Orchestrate Checklist

Owner-only Tunnel/Access setup is intentionally outside worker execution. The
worker does not run real Tunnel provisioning, Access policy changes, public DNS
changes, live provider checks, or paid native cross-checks.

After acceptance, the owner can run these on the deployment host:

```bash
docker compose -f compose.yaml ps
seo-hub doctor --json --config config/seo-hub.toml
```

Then verify Cloudflare Access in the browser and with a service token from the
owning host. Keep native cross-checks zero-cost unless a separate paid run is
explicitly approved.
