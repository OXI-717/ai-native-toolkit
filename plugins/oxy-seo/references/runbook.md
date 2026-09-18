# SEO Hub Runbook

SEO Hub MVP is read-only. Operators may validate local config, import existing
evidence, inspect reports, and export local handoff artifacts. They must not
start live provider collection, run paid prompts/tools, expose origins publicly,
publish reports, or execute generated actions from this MVP flow.

## Daily Smoke

```bash
oxy-seo doctor --json --config config/oxy-seo.toml
oxy-seo projects --json --config config/oxy-seo.toml
oxy-seo status --project example --json --config config/oxy-seo.toml
```

If config readiness is not green, fix paths or file permissions first. Secret
files must stay mode `0600`; do not commit credentials.

## Read-Only Full Run

```bash
oxy-seo run --project example --mode full --json --config config/oxy-seo.toml
oxy-seo report --project example --run run_... --json --config config/oxy-seo.toml
oxy-seo export --project example --run run_... --format observer-actions --json --config config/oxy-seo.toml
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
oxy-seo doctor --json --config config/oxy-seo.toml
```

Then verify Cloudflare Access in the browser and with a service token from the
owning host. Keep native cross-checks zero-cost unless a separate paid run is
explicitly approved.
