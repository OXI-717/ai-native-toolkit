---
name: statusline-setup
description: |
  Use when user asks to set up, configure, or troubleshoot the Claude Code statusline.
  Triggers: "setup statusline", "fix statusline", "statusline not working",
  "show usage limits", "context percentage". NOT for general status questions.
---
# Claude Code Statusline Setup

## What It Shows

```
project (branch) • Opus 4.6 • 2h15m 73% W100% • $0.00 • 23%
```

| Segment | Source | Description |
|---------|--------|-------------|
| `project (branch)` | JSON stdin | Working dir + git branch |
| `Opus 4.6` | JSON stdin | Current model |
| `2h15m 73%` | OAuth API | Time until 5h reset + remaining capacity |
| `W100%` | OAuth API | 7-day weekly capacity remaining |
| `$0.00` | JSON stdin | Session cost ($0.00 on Max) |
| `23%` | JSON stdin | Context window used |

## Color Coding

| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| Limits (remaining) | >50% | 20-50% | <20% |
| Context (used) | <50% | 50-80% | >80% |

## Installation

### 1. Copy script

```bash
cp ~/.claude/plugins/cache/ai-native-toolkit/statusline/*/scripts/statusline.sh ~/.claude/statusline.sh
chmod +x ~/.claude/statusline.sh
```

### 2. Configure settings.json

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash ~/.claude/statusline.sh"
  }
}
```

### 3. Customize Keychain account

Edit `statusline.sh` line with `security find-generic-password`:
- Replace `-a "$(whoami)"` with your macOS username
- Find your account: `security find-generic-password -s "Claude Code-credentials" 2>&1 | grep acct`

## Dependencies

- `jq` — JSON parsing
- `curl` — API calls
- `python3` — math + datetime
- macOS Keychain (`security`) — OAuth token storage

## Data Sources

| Data | Source | Cache |
|------|--------|-------|
| Workspace, model, cost, context | JSON stdin (Claude Code) | None |
| 5h/7d limits, reset time | `api.anthropic.com/api/oauth/usage` | 120s (`/tmp/claude-usage-cache.json`) |

## OAuth Token

Stored in macOS Keychain:
- Service: `Claude Code-credentials`
- Account: your macOS username

Extract manually:
```bash
security find-generic-password -s "Claude Code-credentials" -a "$(whoami)" -w | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])"
```

If expired: restart Claude Code (auto-refreshes) or `claude logout && claude login`.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No limits shown | Check OAuth token: `security find-generic-password -s "Claude Code-credentials" -w` |
| Stale data | Delete cache: `rm /tmp/claude-usage-cache.json` |
| Wrong account | Find correct: `security find-generic-password -s "Claude Code-credentials" 2>&1 \| grep acct` |
| No branch | Ensure cwd is git repo |
| jq not found | `brew install jq` |
