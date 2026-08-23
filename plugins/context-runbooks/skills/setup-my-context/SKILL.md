---
name: setup-my-context
description: "Build a personal or team Claude Code/Codex working structure through an interview: workspace, AGENTS.md, rules, memory habits, delegation, and delivery pipeline. Triggers on setup my context, set up AGENTS.md, собери структуру Claude Code, настрой рабочую директорию, создай правила для агентов, L0-L4 runbooks."
---

# setup-my-context

Use this skill when a user wants to create or improve their own agent-working
structure. The result is not a clone of someone else's setup. The result is a
small, owned system that matches the user's projects, team, tools, and risk
boundaries.

## References

Read only what the current request needs:

- For the whole ladder, read [references/ladder.md](references/ladder.md).
- For the interview flow, read [references/bootstrap-interview.md](references/bootstrap-interview.md).
- For workspace setup, read [references/l0-workspace.md](references/l0-workspace.md).
- For project context files, read [references/l1-project-context.md](references/l1-project-context.md).
- For memory and feedback loops, read [references/l2-memory-feedback.md](references/l2-memory-feedback.md).
- For skills, commands, and delegation, read [references/l3-delegation.md](references/l3-delegation.md).
- For PR/review/acceptance pipelines, read [references/l4-delivery-pipeline.md](references/l4-delivery-pipeline.md).

## Operating Modes

Choose the smallest mode that fits the user's request.

### Bootstrap

Use when the user is starting from scratch or says "set up my context".

1. Read `bootstrap-interview.md` and `ladder.md`.
2. Ask the minimum useful questions. Prefer batching obvious setup facts into one
   concise checklist only when the user explicitly asks you to proceed
   autonomously.
3. Identify the target level:
   - L0-L2 for non-technical users or teams that mainly need repeatable context.
   - L0-L4 for technical users who want delegated implementation and PR flow.
4. Generate concrete files or patches in the target workspace.
5. End with the "done when" checks for every completed level.

### Improve Existing Setup

Use when the user already has `AGENTS.md`, `rules/`, or skills.

1. Inspect the existing files before proposing changes.
2. Read the level reference that matches the missing or weak area.
3. Keep edits narrow. Do not reorganize unrelated projects.
4. Preserve user-specific conventions that are working.

### Teach Or Hand Off

Use when the user asks for runbooks, a workshop artifact, or material for a
mixed technical/non-technical group.

1. Read `ladder.md`.
2. Give the L0-L4 sequence and tell the audience where to stop.
3. Use the relevant level references as the source for exercises and acceptance
   checks.

## Output Contract

When writing files, prefer this minimal structure unless the user has an existing
convention:

```text
<workspace>/
├── AGENTS.md
├── rules/
│   ├── git.md
│   ├── no-secrets.md
│   └── session-hygiene.md
├── MEMORY.md
└── <project>/
    ├── AGENTS.md
    └── rules/
```

For a narrow single-project setup, create only the project `AGENTS.md` and
`rules/` files. Do not create a workspace umbrella when the user explicitly wants
one repository only.

## Safety Boundaries

- Do not write secrets into `AGENTS.md`, `rules/`, `MEMORY.md`, git, or chat.
- Do not add automation that can mutate production, spend money, send messages,
  or merge code without an explicit user approval step.
- Do not use global worktree directories. If a git worktree is needed, create it
  inside the repository, following the repository's local convention.
- Do not turn one user's preference into a universal rule. Capture preferences as
  local rules scoped to the workspace or project.

## Completion

Report:

- files created or changed;
- levels completed;
- remaining levels, if any;
- the next command or workflow the user should try.
