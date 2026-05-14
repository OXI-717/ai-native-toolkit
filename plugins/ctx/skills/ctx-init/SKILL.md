---
name: ctx-init
description: Initialize AGENTS.md + CLAUDE.md + rules/ in a code repo (fresh mode). Triggers on "/ctx-init", "init context", "scaffold AGENTS.md", or when user asks to set up project context in a new repo.
---

# ctx-init — context initialization in a repo

Creates the `ctx` structure in the current repo:
- `AGENTS.md` from template with frontmatter (all `@`-imports are relative `@./rules/...`)
- `CLAUDE.md` with `@AGENTS.md` import
- `rules/` with copies of shared plugin rules (`language`, `dates`, `git`, `no-secrets`, `no-public-names`) **plus** project-specific stubs (`stack`, `testing`, `boundaries`, `focus`)

Shared rules are copied rather than imported via `${CLAUDE_PLUGIN_ROOT}` because Claude Code **does not expand** env variables in project-level `@`-imports (and this is needed for cross-agent compatibility: Codex/Gemini/Cursor all read the same `AGENTS.md`).

## Input (ask the user before running)

1. **project** — short name (e.g. `MYAPP`, `API`, `DOCS`).
2. **description** — one line describing the project topic.

## Procedure

**Pre-flight checks:**
1. `pwd` must be the root of the repo being initialized.
2. If `AGENTS.md` already exists — **stop and tell the user** that migration is not supported. Ask the user to rename the old file manually (`mv AGENTS.md AGENTS.md.legacy`) and call `ctx-init` again.

**Actions:**
1. Copy `${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md.template` to `./AGENTS.md`.
2. Substitute `{{PROJECT}}`, `{{DESCRIPTION}}` via `sed`.
3. Copy `${CLAUDE_PLUGIN_ROOT}/templates/CLAUDE.md.template` to `./CLAUDE.md`.
4. Create `./rules/`.
5. Copy **shared rules**: `${CLAUDE_PLUGIN_ROOT}/rules/*.md` to `./rules/`.
6. Copy **project stubs**: `${CLAUDE_PLUGIN_ROOT}/templates/stubs-repo-rules/*.md` to `./rules/` (without overwriting shared files).
7. Run `python3 ${CLAUDE_PLUGIN_ROOT}/lib/ctx-lint.py <repo>` — verify all `@`-imports resolve (0 errors).
8. Show the user what was created, and ask them to fill in the `rules/stack.md`, `rules/testing.md`, `rules/boundaries.md`, `rules/focus.md` files.
9. Do not commit automatically — user reviews and commits themselves.

## Example invocation

```
User: /ctx-init
Agent: "Ready to initialize context. Answer 2 questions:
  1. project name (MYAPP/API/DOCS/...)?
  2. description (short project topic)?"
```

After answers — executes steps 1–7 and shows the diff.
