#!/bin/bash
export LC_ALL=C
#
# Claude Code Statusline
# ======================
#
# Output format:
#   project (branch) • Opus 4.6 • 2h15m 73% W6d12h 100% F99% • $0.00 • CTX▼42%
#
#   project (branch)  - working directory + git branch (blue/dim)
#   Opus 4.6          - current model (cyan)
#   2h15m             - time until 5-hour cycle reset
#   73%               - remaining 5-hour cycle capacity
#   W6d12h            - time until 7-day cycle reset
#   100%              - remaining 7-day weekly capacity
#   F99%              - remaining model-scoped weekly capacity (one per scoped
#                       limit in API "limits", letter = first letter of model
#                       display_name, e.g. F = Fable). Fable 5 at or below
#                       fallback threshold is marked F8%->Sonnet.
#   $0.00             - session cost (green); always $0.00 on Max subscription
#   CTX▼4%            - context remaining until auto-compact
#
# Color coding:
#   Limits (remaining %):  green >50% | yellow 20-50% | red <20%
#   Context (remaining %): green >30% | yellow 10-30% | red <10%
#
# Usage data sources (priority order):
#   1. Anthropic OAuth API (cached, file-locked — max 1 req/min across all sessions)
#   2. Fallback: rate_limits from Claude Code stdin (if API unavailable)
#
# API: https://api.anthropic.com/api/oauth/usage
#   Returns five_hour.utilization, seven_day.utilization, five_hour.resets_at
#   Example: { "five_hour": { "utilization": 27.0, "resets_at": "2026-02-09T12:59:59+00:00" },
#              "seven_day": { "utilization": 0.0,  "resets_at": "2026-02-16T11:59:59+00:00" } }
#
# OAuth token: macOS Keychain, service "Claude Code-credentials", account "$USER"
# Cache: /tmp/claude-usage-cache.json (TTL 60s, file-locked across sessions)
# Dependencies: jq, curl, python3, macOS Keychain (security)
# Based on: https://github.com/serejaris/ris-claude-code/tree/main/statusline
#

input=$(cat)

# Extract all fields from JSON in a single jq call (6→1 jq invocations)
IFS=$'\t' read -r dir_name model cost ctx_remaining ctx_used cwd < <(
    echo "$input" | jq -r '[
        (.workspace.current_dir | split("/") | last // "?"),
        (.model.display_name // "?"),
        (.cost.total_cost_usd // 0 | tostring),
        (.context_window.remaining_percentage // "" | tostring),
        (.context_window.used_percentage // 0 | tostring),
        (.workspace.current_dir // ".")
    ] | @tsv' 2>/dev/null || echo "?\t?\t0\t\t0\t."
)
branch=$(cd "$cwd" 2>/dev/null && git branch --show-current 2>/dev/null || echo "")

# ── Usage limits ─────────────────────────────────────────────────────
# Primary: OAuth API (cached, file-locked)
# Fallback: stdin rate_limits from Claude Code

CACHE_FILE="${STATUSLINE_USAGE_CACHE:-/tmp/claude-usage-cache.json}"
LOCK_FILE="${STATUSLINE_USAGE_LOCK:-${CACHE_FILE}.lock}"
CACHE_TTL=60
CACHE_MAX_AGE=600
FABLE_FALLBACK_THRESHOLD="${STATUSLINE_FABLE_FALLBACK_THRESHOLD:-10}"
FABLE_FALLBACK_MODEL="${STATUSLINE_FABLE_FALLBACK_MODEL:-Sonnet}"
KEYCHAIN_ACCOUNT="${STATUSLINE_KEYCHAIN_ACCOUNT:-${1:-${USER:-}}}"
case "$FABLE_FALLBACK_THRESHOLD" in
    ''|*[!0-9]*) FABLE_FALLBACK_THRESHOLD=10 ;;
esac
export STATUSLINE_FABLE_FALLBACK_THRESHOLD="$FABLE_FALLBACK_THRESHOLD"
export STATUSLINE_FABLE_FALLBACK_MODEL="$FABLE_FALLBACK_MODEL"

file_mtime() {
    local file="$1"
    local ts
    ts=$(stat -c %Y "$file" 2>/dev/null || stat -f %m "$file" 2>/dev/null || true)
    case "$ts" in
        ''|*[!0-9]*) echo 0 ;;
        *) echo "$ts" ;;
    esac
}

fetch_usage() {
    local account_args=()
    if [ -n "$KEYCHAIN_ACCOUNT" ]; then
        account_args=(-a "$KEYCHAIN_ACCOUNT")
    fi

    TOKEN=$(security find-generic-password -s "Claude Code-credentials" "${account_args[@]}" -w 2>/dev/null | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
try:
    d = json.loads(raw)
    print(d['claudeAiOauth']['accessToken'])
except:
    try:
        decoded = bytes.fromhex(raw).decode('utf-8', errors='ignore')
        idx = decoded.find('{')
        if idx >= 0:
            depth = 0
            for i, c in enumerate(decoded[idx:]):
                if c == '{': depth += 1
                elif c == '}': depth -= 1
                if depth == 0:
                    d = json.loads(decoded[idx:idx+i+1])
                    print(d.get('accessToken', ''))
                    break
    except:
        pass
" 2>/dev/null)
    if [ -n "$TOKEN" ]; then
        curl -s --max-time 5 "https://api.anthropic.com/api/oauth/usage" \
            -H "Authorization: Bearer $TOKEN" \
            -H "anthropic-beta: oauth-2025-04-20" \
            -H "Accept: application/json" 2>/dev/null
    fi
}

get_usage_api() {
    local now=$(date +%s)
    local cache_time=0

    if [ -f "$CACHE_FILE" ]; then
        cache_time=$(file_mtime "$CACHE_FILE")
    fi

    if [ $((now - cache_time)) -gt $CACHE_TTL ]; then
        # Remove stale lock (older than 30s = stuck/crashed process)
        if [ -f "$LOCK_FILE" ]; then
            local lock_time=$(file_mtime "$LOCK_FILE")
            [ $((now - lock_time)) -gt 30 ] && rm -f "$LOCK_FILE"
        fi
        # File lock: only one session fetches, others use cache
        if ( set -o noclobber; echo $$ > "$LOCK_FILE" ) 2>/dev/null; then
            trap 'rm -f "$LOCK_FILE"' EXIT
            local data=$(fetch_usage)
            if [ -n "$data" ] && echo "$data" | jq -e '.five_hour' >/dev/null 2>&1; then
                echo "$data" > "$CACHE_FILE"
            elif [ -f "$CACHE_FILE" ] && [ $((now - cache_time)) -gt $CACHE_MAX_AGE ]; then
                rm -f "$CACHE_FILE"
            fi
            rm -f "$LOCK_FILE"
            trap - EXIT
        fi
    fi

    if [ -f "$CACHE_FILE" ]; then
        cat "$CACHE_FILE"
    fi
}

# Try API first
api_data=$(get_usage_api)

five_h_left=""
week_left=""
time_left=""
week_days=""
scoped_left=""

# Parse usage data: single python3 call handles API + fallback + all math
IFS=$'\t' read -r five_h_left week_left time_left week_days scoped_left < <(
    python3 -c "
import json, os, sys, time, math
from datetime import datetime, timezone

api_raw = sys.argv[1]
input_raw = sys.argv[2]
try:
    fable_fallback_threshold = max(0, int(os.environ.get('STATUSLINE_FABLE_FALLBACK_THRESHOLD', '10')))
except Exception:
    fable_fallback_threshold = 10
fable_fallback_model = os.environ.get('STATUSLINE_FABLE_FALLBACK_MODEL', 'Sonnet').strip() or 'Sonnet'

five_h_left = week_left = time_left = week_days = scoped_left = ''

def dh_until(seconds):
    if seconds <= 0:
        return '0h'
    d, rem = divmod(int(seconds), 86400)
    h = rem // 3600
    if d > 0:
        return f'{d}d{h}h' if h > 0 else f'{d}d'
    return f'{h}h' if h > 0 else f'{max(1, rem // 60)}m'

try:
    api = json.loads(api_raw) if api_raw else {}
except: api = {}

if 'five_hour' in api:
    five_h_left = str(max(0, int(100 - float(api['five_hour'].get('utilization', 0)))))
    week_left = str(max(0, int(100 - float(api.get('seven_day', {}).get('utilization', 0)))))
    reset_str = api['five_hour'].get('resets_at', '')
    if reset_str:
        try:
            reset = datetime.fromisoformat(reset_str.replace('Z', '+00:00'))
            diff = max(0, (reset - datetime.now(timezone.utc)).total_seconds())
            h, m = int(diff // 3600), int((diff % 3600) // 60)
            time_left = f'{h}h{m}m' if h > 0 else f'{m}m'
        except: pass
    week_reset_str = api.get('seven_day', {}).get('resets_at', '')
    if week_reset_str:
        try:
            wreset = datetime.fromisoformat(week_reset_str.replace('Z', '+00:00'))
            wdiff = (wreset - datetime.now(timezone.utc)).total_seconds()
            week_days = dh_until(wdiff)
        except: pass
    # Model-scoped weekly limits (e.g. Fable): 'limits' entries with a model scope
    try:
        parts = []
        for lim in (api.get('limits') or []):
            if lim.get('kind') != 'weekly_scoped':
                continue
            scope = lim.get('scope') or {}
            name = ((scope.get('model') or {}).get('display_name') or '').strip()
            if not name:
                continue
            left = max(0, int(100 - float(lim.get('percent', 0))))
            suffix = ''
            if name.lower() == 'fable 5' and left <= fable_fallback_threshold:
                suffix = f'->{fable_fallback_model}'
            parts.append(f'{name[0].upper()}{left}{suffix}')
        scoped_left = ','.join(parts)
    except Exception:
        scoped_left = ''
else:
    try:
        inp = json.loads(input_raw) if input_raw else {}
    except: inp = {}
    rl = inp.get('rate_limits', {})
    fh = rl.get('five_hour', {})
    sd = rl.get('seven_day', {})
    if 'used_percentage' in fh:
        five_h_left = str(max(0, int(100 - float(fh['used_percentage']))))
    if 'used_percentage' in sd:
        week_left = str(max(0, int(100 - float(sd['used_percentage']))))
    reset_epoch = fh.get('resets_at')
    if reset_epoch:
        try:
            diff = max(0, int(reset_epoch) - int(time.time()))
            h, m = diff // 3600, (diff % 3600) // 60
            time_left = f'{h}h{m}m' if h > 0 else f'{m}m'
        except: pass
    week_reset_epoch = sd.get('resets_at')
    if week_reset_epoch:
        try:
            wdiff = int(week_reset_epoch) - int(time.time())
            week_days = dh_until(wdiff)
        except: pass

print(f'{five_h_left}\t{week_left}\t{time_left}\t{week_days}\t{scoped_left}')
" "$api_data" "$input" 2>/dev/null || echo -e "\t\t\t\t"
)

# Format cost
cost_fmt=$(printf "%.2f" "$cost" 2>/dev/null || echo "0.00")

# Context: prefer remaining_percentage (accurate), fallback to used_percentage
if [ -n "$ctx_remaining" ] && [ "$ctx_remaining" != "null" ]; then
    ctx_pct=$(printf "%.0f" "$ctx_remaining" 2>/dev/null || echo "0")
    ctx_label="CTX▼${ctx_pct}%"
    if [ "$ctx_pct" -gt 30 ]; then
        ctx_color="\033[32m"
    elif [ "$ctx_pct" -gt 10 ]; then
        ctx_color="\033[33m"
    else
        ctx_color="\033[31m"
    fi
else
    ctx_pct=$(printf "%.0f" "$ctx_used" 2>/dev/null || echo "0")
    ctx_label="${ctx_pct}%"
    if [ "$ctx_pct" -lt 50 ]; then
        ctx_color="\033[32m"
    elif [ "$ctx_pct" -lt 80 ]; then
        ctx_color="\033[33m"
    else
        ctx_color="\033[31m"
    fi
fi

# Colors for usage limits (remaining %)
usage_color() {
    local val=$1
    if [ "$val" -gt 50 ]; then
        echo "\033[32m"  # green
    elif [ "$val" -gt 20 ]; then
        echo "\033[33m"  # yellow
    else
        echo "\033[31m"  # red
    fi
}

# Build output
out=""

# Dir and branch
if [ -n "$branch" ]; then
    out="\033[34m${dir_name}\033[0m \033[2m(${branch})\033[0m"
else
    out="\033[34m${dir_name}\033[0m"
fi

# Model
out="${out} • \033[36m${model}\033[0m"

# Usage limits
if [ -n "$five_h_left" ] && [ -n "$week_left" ]; then
    five_color=$(usage_color "$five_h_left")
    week_color=$(usage_color "$week_left")
    if [ -n "$week_days" ]; then
        week_fmt="W${week_days} ${week_left}%"
    else
        week_fmt="W${week_left}%"
    fi
    if [ -n "$time_left" ]; then
        out="${out} • ${five_color}${time_left} ${five_h_left}%\033[0m ${week_color}${week_fmt}\033[0m"
    else
        out="${out} • ${five_color}5h ${five_h_left}%\033[0m ${week_color}${week_fmt}\033[0m"
    fi
    # Model-scoped weekly limits (e.g. F99 = Fable 99% left), comma-separated
    for scoped in ${scoped_left//,/ }; do
        scoped_letter="${scoped%%[0-9]*}"
        scoped_tail="${scoped#"$scoped_letter"}"
        scoped_val="${scoped_tail%%[!0-9]*}"
        scoped_suffix="${scoped_tail#"$scoped_val"}"
        case "$scoped_val" in
            ''|*[!0-9]*) continue ;;
        esac
        scoped_color=$(usage_color "$scoped_val")
        out="${out} ${scoped_color}${scoped_letter}${scoped_val}%${scoped_suffix}\033[0m"
    done
fi

# Cost and context
out="${out} • \033[32m\$${cost_fmt}\033[0m • ${ctx_color}${ctx_label}\033[0m"

printf '%b' "$out"
