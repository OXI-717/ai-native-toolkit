---
name: oxyseo
description: Use for the read-only SEO Hub CLI, project registry validation, secret env-file checks, and existing SEO evidence routing.
---

# oxyseo

Use this skill when the user asks about SEO Hub projects, registry setup, CLI
doctor output, or read-only routing for existing SEO/AI-search evidence. The
MVP is read-only.

Resolve the plugin root through the runtime-provided environment first, then make
sure the `seo-hub` CLI is installed before calling it — a fresh marketplace
install exposes this skill but does not install the nested Python package:

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-<installed-plugin-root>}}"
command -v seo-hub >/dev/null 2>&1 || uv tool install --editable "$PLUGIN_ROOT/cli"
```

Follow this CLI order:

```bash
uv tool install --editable "$PLUGIN_ROOT/cli"
seo-hub doctor --json --config <path>
seo-hub projects --json --config <path>
seo-hub status --project <id> --json --config <path>
seo-hub run --project <id> --mode full --json --config <path>
seo-hub report --project <id> --run <run_id> --json --config <path>
seo-hub opportunities --project <id> --run <run_id> --json --config <path>
seo-hub outcomes --project <id> --json --config <path>
seo-hub export --project <id> --run <run_id> --format observer-actions --json --config <path>
```

Do not pass `--live`. Do not trigger paid tools, deploy services, publish
reports, execute generated actions, or invent report data. Do not claim live
Topvisor, Yandex/Google cabinet, Metrica, or DataForSEO collection: v1 only
imports existing local evidence. `run` imports
existing evidence through read-only adapters and local files only. Keep mention,
citation, backlink, and branded query evidence as separate dimensions; treat
stale evidence, missing conversion data, and changed keyword baskets as quality
negatives, with changed baskets marked not comparable.

Owner-only Tunnel/Access setup and zero-cost native cross-check remain a
post-orchestrate checklist item. Worker sessions do not run them.
