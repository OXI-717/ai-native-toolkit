# L1: Project Context Runbook

## Purpose

Make each project self-describing enough that an agent does not need a long
manual briefing at the start of every session.

## Files

```text
<project>/
├── AGENTS.md
└── rules/
    ├── git.md
    ├── testing.md
    ├── no-secrets.md
    └── domain.md
```

## Project AGENTS.md Starter

```markdown
# Project Rules

## Overview

This project does <one paragraph>.

## Common Commands

- Test: `<command>`
- Lint: `<command>`
- Run locally: `<command>`

## Work Rules

@./rules/git.md
@./rules/testing.md
@./rules/no-secrets.md
@./rules/domain.md
```

Replace unknown commands with "unknown, inspect before running". Do not invent
commands.

## Rule Template

```markdown
# Rule: <name>

## Applies When

<scope>

## Rule

<concrete instruction>

## Why

<incident, decision, or constraint>
```

## Done When

- `AGENTS.md` names the project purpose, commands, and imported rules.
- Rules are scoped and concrete.
- A new agent session can answer "how do I test and what must I not touch?"
