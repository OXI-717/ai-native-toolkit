#!/bin/bash
#
# SessionStart(compact|clear) Hook: Restore handoff context after compaction.
#
# Session isolation: each session writes to <session-id>.md.
# On restore, looks for own session file first, falls back to HANDOFF.md
# only with staleness check (prevents cross-session contamination).
#

LOG="/tmp/context-handoff.log"
log() { echo "$(date +%Y-%m-%dT%H:%M:%S) SessionRestore: $*" >> "$LOG" 2>/dev/null; }

# Read hook input JSON from stdin
input=$(cat)

# Log raw input keys for debugging
log "Raw input keys: $(echo "$input" | jq -r 'keys | join(", ")' 2>/dev/null)"

# Extract session_id (try multiple field names)
session_id=$(echo "$input" | jq -r '(.session_id // .sessionId // .session.id // "") | select(. != "")' 2>/dev/null)

# Extract cwd (try multiple field names)
cwd=$(echo "$input" | jq -r '(.cwd // .workingDirectory // .workspace.current_dir // "") | select(. != "")' 2>/dev/null)

if [ -z "$cwd" ]; then
    cwd=$(pwd)
    log "No cwd in input, using pwd: $cwd"
fi

# Build project hash: /home/user/project -> -home-user-project
project_hash=$(echo "$cwd" | sed 's|/|-|g')
HANDOFF_DIR="$HOME/.claude/handoff/${project_hash}"

log "session_id=$session_id cwd=$cwd project_hash=$project_hash"

# --- Session-specific file (best match: same session after compact) ---
handoff_file=""
handoff_source=""

if [ -n "$session_id" ] && [ -f "$HANDOFF_DIR/${session_id}.md" ]; then
    handoff_file="$HANDOFF_DIR/${session_id}.md"
    handoff_source="session-specific (${session_id})"
    log "Found session-specific handoff: $handoff_file"
fi

# --- Fallback: HANDOFF.md (for /clear or unknown session_id) ---
if [ -z "$handoff_file" ] && [ -f "$HANDOFF_DIR/HANDOFF.md" ]; then
    META_FILE="$HANDOFF_DIR/HANDOFF.meta.json"

    # Check staleness: skip if older than 1 hour (prevents stale cross-session restore)
    skip_fallback=false
    if [ -f "$META_FILE" ]; then
        saved_ts=$(jq -r '.timestamp // ""' "$META_FILE" 2>/dev/null)
        if [ -n "$saved_ts" ]; then
            saved_epoch=$(python3 -c "
from datetime import datetime
try:
    dt = datetime.fromisoformat('$saved_ts')
    print(int(dt.timestamp()))
except:
    print(0)
" 2>/dev/null)
            now_epoch=$(date +%s)
            age=$((now_epoch - saved_epoch))
            if [ "$age" -gt 3600 ]; then
                log "Fallback HANDOFF.md too old (${age}s > 3600s), skipping"
                skip_fallback=true
            fi
        fi
    fi

    if [ "$skip_fallback" = false ]; then
        handoff_file="$HANDOFF_DIR/HANDOFF.md"
        handoff_source="latest fallback (HANDOFF.md)"
        log "Using fallback: $handoff_file"
    fi
fi

# --- No handoff found ---
if [ -z "$handoff_file" ]; then
    log "No handoff file found for project $project_hash, skipping"
    exit 0
fi

# Read handoff content
content=$(cat "$handoff_file")

if [ -z "$content" ]; then
    log "Handoff file empty, skipping"
    exit 0
fi

byte_count=$(wc -c < "$handoff_file" | tr -d ' ')
line_count=$(wc -l < "$handoff_file" | tr -d ' ')
log "Restoring handoff: $handoff_source ($byte_count bytes, $line_count lines)"

# Escape for JSON embedding
escaped=$(python3 -c "
import sys, json
content = sys.stdin.read()
print(json.dumps(content)[1:-1])
" <<< "$content")

# Build restore message with explicit instructions for Claude
restore_header="⚡ SESSION CONTEXT RESTORED ⚡\n\nSource: ${handoff_source}\nSize: ${line_count} lines, ${byte_count} bytes\n\nIMPORTANT: You MUST immediately tell the user that context was restored. Show:\n1. That context was restored (source and size)\n2. Brief summary: Goal, Current State, Files Modified count\n3. Ask what to work on next (or continue pending work if session continuation)\n\n--- HANDOFF CONTENT ---\n\n"

# Output additionalContext JSON
cat <<ENDJSON
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${restore_header}${escaped}"
  }
}
ENDJSON

exit 0
