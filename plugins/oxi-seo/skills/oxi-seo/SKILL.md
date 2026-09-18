---
name: oxi-seo
description: Use for the read-only SEO Hub CLI, project registry validation, secret env-file checks, and existing SEO evidence routing.
---

# oxi-seo

Use this skill when the user asks about SEO Hub projects, registry setup, CLI
doctor output, or read-only routing for existing SEO/AI-search evidence. The
MVP is read-only.

Resolve the plugin root through the runtime-provided environment first, then make
sure the `oxi-seo` CLI is installed before calling it — a fresh marketplace
install exposes this skill but does not install the nested Python package:

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-<installed-plugin-root>}}"
uv tool install --force "$PLUGIN_ROOT/cli"
```

Install a regular package, not an editable link: plugin cache directories can
be removed during updates. Reinstall from the current plugin root after each
plugin update; `--force` also replaces earlier editable installations.

Follow this CLI order:

```bash
uv tool install --force "$PLUGIN_ROOT/cli"
oxi-seo doctor --json --config <path>
oxi-seo projects --json --config <path>
oxi-seo status --project <id> --json --config <path>
oxi-seo run --project <id> --mode full --json --config <path>
oxi-seo report --project <id> --run <run_id> --json --config <path>
oxi-seo opportunities --project <id> --run <run_id> --json --config <path>
oxi-seo outcomes --project <id> --json --config <path>
oxi-seo export --project <id> --run <run_id> --format observer-actions --json --config <path>
```

A successful `doctor` without a registry only confirms that the CLI starts.
Require `checks.config.status = "ready"` for project setup; provider connectivity
and evidence availability require a successful read-only import.

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
