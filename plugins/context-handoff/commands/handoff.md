---
description: Compress current session context into handoff file for preservation across /compact or /clear
allowed-tools: ["Bash", "Read", "Write"]
---

Compress the current session context into a handoff document. This preserves critical information before compaction.

## Instructions

Review the ENTIRE current conversation and create a compressed handoff document.

### What to Extract

1. **Goal** — what user is trying to accomplish (1-2 lines)
2. **Files Modified** — every file path touched, with what changed
3. **Decisions Made** — architectural choices, rejected alternatives, reasons
4. **Key Context** — project structure, conventions, constraints discovered
5. **Current State** — what is done, what remains
6. **Errors/Solutions** — issues encountered and how resolved
7. **Dependencies** — external tools, APIs, configs involved

### Compression Rules (Heavy Density, 60-75% reduction)

- Imperative mood, present tense, active voice
- 3-7 words per statement
- Bullets over paragraphs
- `key: value` for named properties
- Zero redundancy — state each fact once
- Preserve ALL: identifiers, file paths, values, decisions, causal relationships
- Use `→` for flows, `|` for alternatives, `?:` for conditions

### Output Format

Write to `~/.claude/handoff/<project-hash>/HANDOFF.md` where project-hash is cwd with `/` replaced by `-`:

```markdown
# Context Handoff
- session: [session id if available]
- cwd: [working directory]
- branch: [git branch]
- saved: [ISO timestamp]
- method: manual

## Goal
[1-2 lines, compressed]

## Files Modified
- path/to/file — what changed
- ...

## Decisions
- Chose X over Y — reason
- ...

## Key Context
- [compressed bullets of critical information]

## Current State
- Done: [completed items]
- Remaining: [pending items]

## Errors Resolved
- issue → solution
- ...
```

### Steps

1. Run `mkdir -p ~/.claude/handoff/$(pwd | sed 's|/|-|g')`
2. Analyze current conversation context
3. Compress using rules above
4. Write HANDOFF.md
5. Write HANDOFF.meta.json: `{"session_id":"...","timestamp":"...","cwd":"...","method":"manual"}`
6. **MANDATORY: Confirm to user with this exact format:**

```
Context saved to ~/.claude/handoff/<hash>/HANDOFF.md
- Sections: [list of ## sections written]
- Size: N lines, M bytes
- Method: manual

You can now safely run /compact or /clear.
After compaction, context will be auto-restored from this handoff.
```

Do NOT skip the confirmation message. The user needs to see that context was saved before running /compact.
