---
name: ctx-lint
description: Validate @-imports across all ctx AGENTS.md files. Finds broken links, missing end markers. Supports auto-fix for trivial issues. Triggers on "/ctx-lint", "validate imports", "check AGENTS.md", "what broke in context".
---

# ctx-lint — context validator

Scans projects with `AGENTS.md`, validates `@`-imports and end-markers. Reports broken links and can fix trivial cases.

## Supported `load_strategy` values

| Value | For what | Checks |
|---|---|---|
| `alpha` | Hub / standalone project with flat @-imports | imports, end-marker |
| `orchestrator` | Shared-pack `AGENTS.md` (e.g. `_ecosystem/AGENTS.md` in an ecosystem repo) | imports (key ones), no end-marker requirement |
| `symlink-consumer` | Project repo importing orchestrator via `_ecosystem/` symlink | imports + **presence of `_ecosystem/` (symlink or dir)** + **required `@./_ecosystem/AGENTS.md` import** |

Projects without `load_strategy` in frontmatter are `unmanaged` (ctx-lint skips import checks, but CLAUDE.md thin-wrapper check still runs).

Separately detectable issue types for the symlink pattern:
- `missing_ecosystem_link` — consumer project has no `_ecosystem/`
- `broken_ecosystem_link` — `_ecosystem` symlink points to a non-existent path
- `consumer_missing_orchestrator_import` — `_ecosystem/` exists but `AGENTS.md` has no `@`-import resolving to `<repo>/_ecosystem/AGENTS.md`

## Subcommands

- `ctx-lint` — check projects in CWD scope (see below)
- `ctx-lint <path>` — check one specific project
- `ctx-lint --all` — force-check all discovered projects
- `ctx-lint --here` — only the current project (enclosing CWD)
- `ctx-lint --json` — machine-readable output for agent parsing
- `ctx-lint --fix` — apply safe auto-fixes (high-confidence only)
- `ctx-lint --list-projects` — show all discovered paths (ignores scope)

## Default scope (auto-scope)

Without explicit path and without `--all`/`--here`, scope is the **union** of the enclosing project (if CWD is inside one) and all projects **directly under** CWD:

- CWD is deep inside a project with no nested sub-projects → just that one project
- CWD is project root but has sub-projects under it → checks **both the project and all sub-projects**
- CWD is a container for projects → all projects under it
- CWD is neither inside nor above any known project → falls back to all discovered

Selected scope is always visible in the output header (human) and in the `scope` field (JSON).
Force flags: `--all` (all), `--here` (strictly enclosing, ignores nested).

## Agent procedure

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/lib/ctx-lint.py --json` → structured report like `{"scope": {...}, "reports": [...]}`.
2. Check `scope.kind` — `all` / `container` / `project` / `explicit` / `none`. If unexpected — show the scope string and suggest `--all`.
3. Show the user a human-readable summary (run again without `--json`).
4. If there are `confidence: high` auto-fixes → suggest `ctx-lint --fix` (same scope flags).
5. For `confidence: low` broken imports — do NOT apply auto-fix, show the user the suggestion and ask for confirmation.

## Examples

- `/ctx-lint` — auto-scope: current project or container, otherwise all
- `/ctx-lint --all` — global run across all projects
- `/ctx-lint --here` — force only enclosing project
- `/ctx-lint --all --fix` — auto-fixes across all projects
- `/ctx-lint ~/myproject` — one explicit project
