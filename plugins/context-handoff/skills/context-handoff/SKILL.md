---
name: context-handoff
description: |
  Use when user asks about context handoff, preserving context across compaction,
  restoring session state, or mentions HANDOFF.md. Also activates automatically
  after compact/clear when additionalContext contains "CONTEXT RESTORED FROM PRE-COMPACTION HANDOFF".
  Triggers: "handoff", "save context", "preserve context", "restore after compact",
  "context lost", "session continuity".
  NOT for general text compression—use infocompressor for that.
---
# Context Handoff System

Preserve session context across `/compact` and `/clear` transitions.

## Architecture

```
StatusLine (every turn)
│ shows CTX▼N% (remaining until auto-compact)
│
├─ User sees CTX▼<10% → runs /handoff (manual)
│  └─ Claude compresses context → HANDOFF.md
│  └─ Claude confirms: "Context saved. N sections, M bytes."
│  └─ User runs /compact
│
├─ Auto-compact fires (CTX▼~0%)
│  └─ PreCompact hook → auto-saves HANDOFF.md (safety net)
│
└─ After compact/clear
   └─ SessionStart hook → injects HANDOFF.md as additionalContext
   └─ Claude MUST follow "On Restore" protocol below
```

## Three Mechanisms

| Mechanism | Trigger | Quality | Speed |
|-----------|---------|---------|-------|
| `/handoff` | User types the command | High — Claude compresses | ~10s |
| PreCompact hook | Before `/compact` or auto-compact | Medium — script extracts | <5s |
| SessionStart hook | After compact/clear | N/A — restores saved | <1s |

## File Locations

| File | Purpose |
|------|---------|
| `~/.claude/handoff/<project-hash>/HANDOFF.md` | Latest compressed context |
| `~/.claude/handoff/<project-hash>/HANDOFF.meta.json` | Metadata (session, timestamp, cwd) |
| `~/.claude/handoff/<project-hash>/<session-id>.md` | Per-session archive |

Project hash: cwd with `/` replaced by `-`. Example: `/home/alex/work/repo` -> `-home-alex-work-repo`

## Handoff Format

```markdown
# Context Handoff
- session: <id>
- cwd: <path>
- branch: <branch>
- saved: <timestamp>
- method: manual|auto

## Goal
## Files Modified
## Decisions
## Key Context
## Current State
## Errors Resolved
## Tool Usage
```

## Workflow

1. Work normally. StatusLine shows context % used.
2. Context fills up (>70% in statusline)
3. Options:
   - `/handoff` → Claude compresses manually (recommended for critical work)
   - `/compact` → PreCompact hook auto-saves, then compaction runs
   - Auto-compact triggers → PreCompact hook fires automatically
4. After compaction: SessionStart(compact) hook restores HANDOFF.md
5. Claude follows "On Restore" protocol

## Recommended Thresholds

StatusLine shows `CTX▼N%` — remaining context until auto-compact.

| CTX▼ remaining | Action |
|----------------|--------|
| >30% | Work normally |
| 10-30% | Consider `/handoff` if critical work |
| <10% | Run `/handoff` then `/compact` |
| ~0% | Auto-compact fires — PreCompact hook is safety net |

Note: `used_percentage` from Claude Code JSON is inaccurate (counts only input tokens).
`remaining_percentage` is the real "Context left until auto-compact" value.

Optional: `export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` to compact earlier (at 70% used).

---

## On Save (`/handoff`)

After writing HANDOFF.md, Claude MUST confirm to user:

```
Context saved to ~/.claude/handoff/<hash>/HANDOFF.md
- Sections: Goal, Files Modified, Decisions, Key Context, Current State, Errors Resolved
- Size: N lines, M bytes
- Method: manual

You can now safely run /compact or /clear.
After compaction, context will be auto-restored from this handoff.
```

---

## On Restore (CRITICAL — follow exactly)

When context is restored after compact/clear (additionalContext contains handoff),
Claude MUST do the following:

### Step 1: Acknowledge restoration

Print clearly:

```
Context restored from handoff.
- Saved: <timestamp from handoff>
- Method: <manual|auto>
- Project: <cwd>
```

### Step 2: Summarize restored state

Read the handoff sections and provide a brief summary:

```
Previous session summary:
- Goal: <from ## Goal>
- State: <from ## Current State>
- Files touched: <count from ## Files Modified>
- Key decisions: <1-2 most important from ## Decisions>
```

### Step 3: Ask for direction

```
Ready to continue. What would you like to work on next?
```

### Step 3 alternative (if session continuation message present)

If the restore happens alongside a "This session is being continued" message,
skip asking and continue with the pending task from the handoff's "Current State > Remaining".

---

## Compression Rules

Heavy density (60-75% reduction). Follow @infocompressor principles:

- Imperative mood, present tense, active voice
- 3-7 words per statement
- Bullets > paragraphs, key-value > bullets
- Zero redundancy
- Preserve ALL: identifiers, file paths, values, decisions, entities

## Hook Setup

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/pre-compact.py",
            "timeout": 30
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/handoff-on-clear.py",
            "timeout": 150
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/handoff-autosave.py",
            "timeout": 90
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact|clear",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/session-restore.sh"
          }
        ]
      }
    ]
  }
}
```

`Stop` is what makes `/clear` safe. `PreCompact` fires **only** for `/compact`; without
the autosave hook a cleared session leaves no handoff at all and the conversation is
simply lost. Throttled to one save per `HANDOFF_MIN_INTERVAL` seconds (default 120).

The `timeout` here must stay above the script's own budget (`BUILDER_TIMEOUT` 30s +
enrich 60s + margin). If the harness kills the hook mid-enrichment, the orphaned child
keeps running and can overwrite the NEXT session's handoff with a stale enrichment.

`UserPromptSubmit` closes the remaining gap: the throttle means work done since the last
save would still be lost, so typing `/clear` flushes a handoff immediately, bypassing it.
(Measured: handoff at 22:24:43, `/clear` at 22:26:55 — everything between was gone.)
The same hook also watches context usage and, at `HANDOFF_AUTO_CLEAR_PCT` (default
90% used), writes a fresh handoff and injects an instruction to clear before any more
task work. `HANDOFF_AUTO_CLEAR_BUILDER_TIMEOUT` controls the builder timeout
(default 30s); invalid env values fall back to defaults and log to `/tmp/context-handoff.log`.

Copy scripts:
```bash
cp <plugin-cache>/scripts/_handoff_common.py ~/.claude/hooks/
cp <plugin-cache>/scripts/pre-compact.py ~/.claude/hooks/
cp <plugin-cache>/scripts/handoff-autosave.py ~/.claude/hooks/
cp <plugin-cache>/scripts/handoff-on-clear.py ~/.claude/hooks/
cp <plugin-cache>/scripts/handoff-enrich.py ~/.claude/hooks/
cp <plugin-cache>/scripts/session-restore.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/pre-compact.py ~/.claude/hooks/handoff-autosave.py \
  ~/.claude/hooks/handoff-on-clear.py ~/.claude/hooks/handoff-enrich.py ~/.claude/hooks/session-restore.sh
```

## Which handoff gets restored

Saved per session (`<session-id>.md`), per tmux pane (`pane-<pane>.md`) and per project
(`HANDOFF.md`). Restore order:

1. `<session-id>.md` — the `/compact` case, where the id is preserved;
2. `pane-<TMUX_PANE>.md` — the `/clear` case: the id is regenerated, but the operator is
   still sitting in the same pane, so that pane's handoff is the one that belongs to them;
3. nothing.

`HANDOFF.md` is written for convenience but is deliberately **not** restored: several
sessions routinely run in one directory at once and all overwrite that single file.
Inheriting a neighbour's handoff is worse than inheriting none — an empty context
announces itself, a plausible wrong one silently sends the session down another task.

Claude-only by design (`~/.claude` paths, Claude hook events); excluded from Codex.

## Debug

Log: `/tmp/context-handoff.log`
Check handoff: `cat ~/.claude/handoff/<project-hash>/HANDOFF.md`
Check meta: `cat ~/.claude/handoff/<project-hash>/HANDOFF.meta.json`
