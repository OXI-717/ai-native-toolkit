# AI Native Toolkit

> Open-source plugins for Claude Code that turn vibe-coding into production-grade AI-native engineering.

## Quick Start

```bash
# Install via Claude Code marketplace
/plugin marketplace add OXI-717/ai-native-toolkit
/plugin install ctx@ai-native-toolkit
```

## Plugins

| Plugin | What it does |
|--------|-------------|
<!-- PLUGINS:BEGIN -->
| ctx | Project context: AGENTS.md, rules, init, lint |
| review | Multi-agent code review with confidence filtering |
| pentest | Black-box security audit (L0-L3) |
| context-handoff | Preserve context across /compact and /clear |
| statusline | Claude Code status bar with usage limits |
| gh-issues | GitHub Issues as AI session memory |
| infocompressor | Dense reference specs from long documents |
| deep-interview | Clarify vague tasks before planning |
<!-- PLUGINS:END -->

## 4 Levels of AI-Native Development

**Level 1: Vibe Coding** — ask ChatGPT, paste code, hope it works

**Level 2: Context & Rules** → `ctx` — AI remembers your project between sessions

**Level 3: Verified Development** → `review`, `pentest` — AI reviews and audits your code

**Level 4: Autonomous Agents** — agents launch agents, auto-recovery, DAG orchestration

## License

MIT
