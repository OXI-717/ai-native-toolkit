# L2: Memory From Feedback Runbook

## Purpose

Stop paying twice for the same mistake. Convert repeated feedback, review
findings, and decisions into maintained context.

## Lightweight Memory Index

Use a simple `MEMORY.md` when no richer memory system exists:

```markdown
# Memory Index

## Active Rules To Remember

| Date | Scope | Lesson | Rule File |
|---|---|---|---|
| 2026-08-23 | git | Worktrees stay inside the repo | rules/git.md |

## Decisions

- 2026-08-23: <decision> because <reason>.

## Retired

- <date>: <old rule> removed because <reason>.
```

## Feedback To Rule

When a user, reviewer, or incident says "this should not happen again":

1. Identify the scope: workspace, project, tool, team, or task type.
2. Write the smallest rule that prevents recurrence.
3. Add the source and reason.
4. Add a deletion condition when the rule may become stale.

## Bad Rule

```markdown
Always be careful with git.
```

## Better Rule

```markdown
# Rule: Worktree Placement

## Applies When

Creating git worktrees in this repository.

## Rule

Create worktrees only under `<repo>/.worktrees/<branch>/`.

## Why

Global worktree directories make cleanup and repo-local context unreliable.
```

## Done When

- Three real feedback items are represented as rules or decisions.
- `MEMORY.md` links to rule files instead of duplicating them.
- The user can explain how a new mistake becomes a rule.
