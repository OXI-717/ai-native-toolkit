# ai-native-toolkit

Open-source plugins for AI coding agents: context management, code review, security audit,
and developer utilities. Claude Code is the reference runtime; most plugins are
runtime-agnostic and load in opencode through `skills.paths`.

## Structure

- `plugins/` — 8 standalone plugins, each installable independently
- `docs/` — guides on AI-native development levels
- `.claude-plugin/marketplace.json` — plugin registry for Claude Code marketplace install

## Runtimes

See the runtime support table in `README.md` before assuming a plugin works outside
Claude Code. Plugin hooks are not exported to this repo, so anything whose behaviour
depends on lifecycle hooks (`ctx` auto-loading, `context-handoff` auto-restore,
`statusline`) is reduced or unavailable in other runtimes.
