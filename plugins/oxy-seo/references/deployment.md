# SEO Hub Deployment

This deployment keeps the hub, Elmo, OpenSEO, and databases on Docker private
networks. Cloudflare Tunnel is the only public ingress. Do not publish origin
ports for `hub`, `elmo-web`, `openseo`, or `postgres`.

## Layout

Run `deploy/scripts/init-layout.sh /srv/oxy-seo` on the target host and copy the
tracked `deploy/compose.yaml` plus edited examples into that directory:

```text
oxy-seo/
  compose.yaml
  config/oxy-seo.toml
  projects/<project>/project.toml
  secrets/<project>.env
  data/hub/
  exports/
  cloudflared/config.yml
```

Secret files stay under `secrets/` with host permissions of `0700` or stricter.

## Cloudflare Access

Create separate Access applications for `hub.example.com`, `elmo.example.com`,
and `openseo.example.com`, all backed by one identity provider for browser SSO.
Each application uses its own audience value. Policies must allow only the
exact owner email for browser access and exact service token identities for
automation. Do not use broad account-wide grants or wildcard service token
matching.

For Elmo, set `ELMO_ACCESS_ALLOWED_EMAIL`, `ELMO_ACCESS_AUD`, and
`ELMO_ACCESS_TEAM_DOMAIN`. The patched Elmo image requires the
`Cf-Access-Jwt-Assertion` header on every application request, even when the
browser already has a Better Auth cookie. A direct-origin request with only a
`CF_Authorization` browser cookie is rejected.

Owner-only real Tunnel/Access setup is a post-orchestrate checklist item. The
worker does not run Cloudflare provisioning, public DNS changes, live native
cross-checks, or paid provider checks. Keep origin ports private and verify
Access from the owning host after deployment.

## Backup And Move

Use service-native export paths:

```bash
docker compose -f compose.yaml exec -T postgres pg_dump -U elmo -d elmo > backups/elmo.sql
docker compose -f compose.yaml cp hub:/app/exports backups/hub-exports
```

Stop services before moving host-local bind data with `docker compose down`:

```bash
docker compose -f compose.yaml down
```

Do not perform a live volume copy while services are writing. Prefer `pg_dump`
for PostgreSQL and each service-native export for application artifacts.

## Update And Rollback

For update, deploy the new source, reinstall the CLI/plugin package, regenerate
Codex manifests from source of truth, and run `oxy-seo doctor --json` before a
read-only smoke. For rollback, stop services, restore the last config/data/export
backup, reinstall the previous package version, and re-run `doctor`. Do not
publish origin ports and do not disable Access as a rollback shortcut.
