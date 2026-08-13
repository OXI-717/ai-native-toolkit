#!/bin/bash
#
# SessionStart(compact|clear) Hook: Restore handoff context after compaction.
#
# Session isolation: each session writes to <session-id>.md.
# On restore, looks for own session file first, then pane-scoped handoff.
# HANDOFF.md is not restored because it is shared by concurrent sessions.
# LFG_ROTATION_MARKER_V1: supports .lfg-rotation-request.env handoff_file.
#

LOG="/tmp/context-handoff.log"
log() { echo "$(date +%Y-%m-%dT%H:%M:%S) SessionRestore: $*" >> "$LOG" 2>/dev/null; }
die() {
    echo "SessionRestore: $*" >&2
    log "$*"
    exit 1
}

if [ -z "${HOME:-}" ]; then
    die "HOME is not set; cannot resolve ~/.claude/handoff"
fi

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

# Build the Claude project slug by replacing path separators with dashes.
project_hash=$(echo "$cwd" | sed 's|/|-|g')
HANDOFF_DIR="$HOME/.claude/handoff/${project_hash}"

log "session_id=$session_id cwd=$cwd project_hash=$project_hash"

# --- Session-specific file (best match: same session after compact) ---
handoff_file=""
handoff_source=""

read_marker_value() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 1
    awk -F= -v k="$key" '$1 == k {print substr($0, length(k) + 2); exit}' "$file" 2>/dev/null
}

# --- LFG rotation file (old tmux pane, new Claude session id) ---
rotation_marker="$HANDOFF_DIR/.lfg-rotation-request.env"
if [ -f "$rotation_marker" ]; then
    marker_cwd=$(read_marker_value "$rotation_marker" "cwd")
    marker_session=$(read_marker_value "$rotation_marker" "session")
    marker_handoff=$(read_marker_value "$rotation_marker" "handoff_file")
    marker_basename=$(basename "$marker_handoff" 2>/dev/null)
    if [ "$marker_cwd" = "$cwd" ] && [ -n "$marker_session" ] \
        && [ -n "$marker_handoff" ] && [ "$marker_handoff" = "$marker_basename" ] \
        && [ -f "$HANDOFF_DIR/$marker_handoff" ]; then
        handoff_file="$HANDOFF_DIR/$marker_handoff"
        handoff_source="lfg-rotation (${marker_session})"
        rm -f "$rotation_marker" 2>/dev/null || true
        log "Found LFG rotation handoff: $handoff_file"
    else
        log "Ignoring LFG rotation marker: no matching session snapshot"
    fi
fi

if [ -z "$handoff_file" ] && [ -n "$session_id" ] && [ -f "$HANDOFF_DIR/${session_id}.md" ]; then
    handoff_file="$HANDOFF_DIR/${session_id}.md"
    handoff_source="session-specific (${session_id})"
    log "Found session-specific handoff: $handoff_file"
fi

# --- Pane-scoped handoff (the /clear case) ---
#
# /clear mints a new session id, so the lookup above can never match. The tmux pane,
# however, is the same one the operator is sitting in, and its handoff belongs to the
# conversation that just ran here.
#
# The project-wide HANDOFF.md is deliberately NOT used as a fallback: two live sessions
# in one directory is routine, so they all overwrite
# that single file, and restoring a neighbour's handoff is worse than restoring nothing —
# an empty context announces itself, a plausible wrong one does not.
if [ -z "$handoff_file" ] && [ -n "${TMUX_PANE:-}" ]; then
    pane_slug=$(printf '%s' "${TMUX_PANE}" | tr '%' 'p' | tr -cd 'A-Za-z0-9_-' | cut -c1-32)
    if [ -n "$pane_slug" ] && [ -f "$HANDOFF_DIR/pane-${pane_slug}.md" ]; then
        handoff_file="$HANDOFF_DIR/pane-${pane_slug}.md"
        handoff_source="pane-scoped (${TMUX_PANE})"
        log "Found pane handoff: $handoff_file"
    else
        log "No pane handoff for ${TMUX_PANE} in $HANDOFF_DIR"
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
