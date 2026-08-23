# L0: Workspace Runbook

## Purpose

Create a predictable workspace where projects, rules, credentials, generated
artifacts, and temporary work have obvious places.

## Steps

1. Pick the umbrella path.
2. Create a root `AGENTS.md`.
3. Create `rules/` for reusable rules.
4. Add `.gitignore` entries for local-only files.
5. Define the credentials boundary.

## Suggested Layout

```text
<workspace>/
├── AGENTS.md
├── rules/
│   ├── git.md
│   ├── no-secrets.md
│   └── session-hygiene.md
├── _credentials/        # local only, never committed
├── _research/           # optional
└── <project>/
```

Use different names if the user already has a convention. The important part is
that placement rules are written down.

## Root AGENTS.md Starter

```markdown
# Workspace Rules

## Project Placement

- Put active projects directly under this workspace unless a project has an
  existing home.
- Put temporary research in `_research/`.
- Put local credentials only in `_credentials/`; never paste secrets into agent
  context.

## Shared Rules

@./rules/git.md
@./rules/no-secrets.md
@./rules/session-hygiene.md
```

## .gitignore Starter

```gitignore
.worktrees/
.task-runner/
_credentials/
.env
.env.*
!.env.example
node_modules/
.venv/
__pycache__/
.pytest_cache/
dist/
build/
```

## Done When

- A new project has an obvious destination.
- A new agent session can read the root `AGENTS.md` and describe workspace
  boundaries.
- Secrets and temporary work are excluded from git.
