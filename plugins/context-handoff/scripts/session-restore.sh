#!/bin/bash
#
# SessionStart(compact|clear) Hook: Restore handoff context after compaction.
#
# Session isolation: each session writes to <session-id>.md.
# On restore, looks for own session file first, then pane-scoped handoff, then the
# newest per-session archive with an explicit foreign-session warning.
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
allow_foreign_fallback=1

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
        allow_foreign_fallback=0
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

# --- Freshest usable session archive (explicitly foreign) ---
#
# A new session may have neither the old session id nor a stable tmux pane. Do not use
# HANDOFF.md to guess silently: select the newest durable per-session archive for this
# cwd, ranked by its immutable `saved` header instead of mtime (which enrichment changes).
# Ignore empty, unreadable, and malformed archives; a damaged new save must not mask an
# older usable context. The shell re-reads candidates below so a failure during selection
# also falls through to the next one.
if [ -z "$handoff_file" ] && [ "$allow_foreign_fallback" = 1 ] && [ -d "$HANDOFF_DIR" ]; then
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        if candidate_content=$(cat "$candidate" 2>/dev/null) && [ -n "$candidate_content" ]; then
            handoff_file="$candidate"
            break
        fi
    done < <(python3 - "$HANDOFF_DIR" "$cwd" <<'PY'
import sys
from datetime import datetime
from pathlib import Path

root = Path(sys.argv[1])
cwd = sys.argv[2]
candidates = []


def header_value(content, key):
    prefix = f"- {key}:"
    for line in content.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


try:
    for path in root.glob("*.md"):
        if path.name == "HANDOFF.md" or path.name.startswith("pane-") or not path.is_file():
            continue
        try:
            content = path.read_text(errors="replace")
            saved = header_value(content, "saved")
            if not content.strip() or header_value(content, "cwd") != cwd or not saved:
                continue
            saved_at = datetime.fromisoformat(saved.replace("Z", "+00:00")).timestamp()
            candidates.append((saved_at, path.name, path))
        except (OSError, ValueError):
            continue
except OSError:
    pass

for _, _, path in sorted(candidates, reverse=True):
    print(path)
PY
)
    if [ -n "$handoff_file" ] && [ -f "$handoff_file" ]; then
        foreign_owner=$(basename "$handoff_file" .md)
        handoff_source="foreign-session (${foreign_owner}; requested ${session_id:-unknown})"
        log "No matching handoff; using newest usable foreign session archive: $handoff_file"
    else
        handoff_file=""
    fi
fi

# Compatibility for handoffs written before per-session archives existed (and manual
# handoffs made by older plugin versions). Ownership is still stated as foreign/unknown.
if [ -z "$handoff_file" ] && [ "$allow_foreign_fallback" = 1 ] && [ -f "$HANDOFF_DIR/HANDOFF.md" ]; then
    legacy_owner=$(jq -r '.session_id // "unknown"' "$HANDOFF_DIR/HANDOFF.meta.json" 2>/dev/null)
    legacy_cwd=$(jq -r '.cwd // empty' "$HANDOFF_DIR/HANDOFF.meta.json" 2>/dev/null)
    if [ "$legacy_cwd" = "$cwd" ]; then
        handoff_file="$HANDOFF_DIR/HANDOFF.md"
        handoff_source="foreign-session (${legacy_owner:-unknown}; legacy project fallback; requested ${session_id:-unknown})"
        log "No session archive; using legacy project handoff: $handoff_file"
    else
        log "Ignoring legacy project handoff with missing or mismatched cwd"
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
