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
project (branch) • Fable 5 • 2h15m 73% W100% F8%->Sonnet • $0.00 • CTX▼88%
```

| Segment | Source | Description |
|---------|--------|-------------|
| `project (branch)` | JSON stdin | Working dir + git branch |
| `Fable 5` | JSON stdin | Current model |
| `2h15m 73%` | OAuth API | Time until 5h reset + remaining capacity |
| `W100%` | OAuth API | 7-day weekly capacity remaining |
| `F8%->Sonnet` | OAuth API | Fable 5 weekly scoped capacity is at/below fallback threshold |
| `$0.00` | JSON stdin | Session cost ($0.00 on Max) |
| `CTX▼88%` | JSON stdin | Context window remaining |

## Color Coding

| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| Limits (remaining) | >50% | 20-50% | <20% |
| Context (remaining) | >30% | 10-30% | <10% |

## Fable 5 Fallback Policy

Fable 5 is the preferred expensive model only while the model-scoped weekly capacity
has enough headroom. For non-interactive research/analysis sessions:

- If Fable 5 remaining capacity is above 10%, keep using Fable 5 when quality justifies it.
- If Fable 5 remaining capacity is 1-10%, switch research/analysis to Sonnet before asking an interactive question.
- If Fable 5 remaining capacity is 0%, treat Fable as exhausted and use Sonnet for research/analysis without prompting.

The SessionStart hook prints the policy when Fable is at/below the threshold. The
statusline marks the scoped limit as `F8%->Sonnet` or `F0%->Sonnet` while the policy
is active. The threshold and fallback model can be overridden with
`STATUSLINE_FABLE_FALLBACK_THRESHOLD` and `STATUSLINE_FABLE_FALLBACK_MODEL`.

Deferred scope: this plugin does not cache research results. Research-result caching
belongs to the specific research/analyzer workflow that can identify semantically
equivalent requests and valid reuse boundaries.

## Installation

### 1. Copy script

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-/path/to/installed/statusline}"
cp "$PLUGIN_ROOT/scripts/statusline.sh" ~/.claude/statusline.sh
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

### 3. Optional: customize Keychain account

By default, the statusline looks up the OAuth token using your macOS `$USER`.
Override it with `STATUSLINE_KEYCHAIN_ACCOUNT` or pass the account as the first
script argument:

```json
{
  "statusLine": {
    "type": "command",
    "command": "STATUSLINE_KEYCHAIN_ACCOUNT=your-macos-user bash ~/.claude/statusline.sh"
  }
}
```

Find your account: `security find-generic-password -s "Claude Code-credentials" 2>&1 | grep acct`

## Dependencies

- `jq` — JSON parsing
- `curl` — API calls
- `python3` — math + datetime
- macOS Keychain (`security`) — OAuth token storage

## Data Sources

| Data | Source | Cache |
|------|--------|-------|
| Workspace, model, cost, context | JSON stdin (Claude Code) | None |
| 5h/7d limits, reset time, model-scoped limits, extra usage | `api.anthropic.com/api/oauth/usage` | 60s (`/tmp/claude-usage-cache.json`) |

## OAuth Token

Stored in macOS Keychain:
- Service: `Claude Code-credentials`
- Account: your macOS username

Extract manually:
```bash
security find-generic-password -s "Claude Code-credentials" -a "${STATUSLINE_KEYCHAIN_ACCOUNT:-${USER}}" -w | \
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
