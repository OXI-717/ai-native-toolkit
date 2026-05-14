# Getting Started

Get from zero to AI-native development in 5 minutes.

---

## 1. Install the marketplace

```
/plugin marketplace add OXI-717/ai-native-toolkit
```

This registers the toolkit as a plugin source. You only do this once per machine.

---

## 2. Install the ctx plugin

```
/plugin install ctx@ai-native-toolkit
```

`ctx` is the foundation. It gives your project persistent AI memory and enforces conventions across every session.

---

## 3. Scaffold your project

Navigate to your project root, then run:

```
/ctx-init
```

This creates three things in under 10 seconds:

- **`AGENTS.md`** — project memory for every AI agent. Describes what the project is, the tech stack, conventions, and anything AI needs to know at session start.
- **`CLAUDE.md`** — Claude Code-specific settings and rules that apply in this repo.
- **`rules/`** — starter rule files for common problem areas: git hygiene (no force-push to main, meaningful commit messages) and secrets handling (no hardcoded credentials, no `.env` in commits).

Commit all three files. Every teammate and every AI session from this point on starts with the same shared context.

---

## 4. Add code review

```
/plugin install review@ai-native-toolkit
```

Once installed, run it before opening a PR:

```
/review
```

The `/review` command auto-detects scope (staged diff, branch diff, or full repo), runs multiple specialized review agents in parallel, and auto-fixes issues by default. It checks your diff against project rules and posts findings with line references — catching bugs, anti-patterns, and rule violations before a human ever looks at the code.

---

## 5. What to do next

**Add security scanning**

```
/plugin install pentest@ai-native-toolkit
```

Run `/pentest` on any PR touching auth, payments, or external APIs. It looks for injection vectors, exposed secrets, insecure defaults, and auth bypasses.

**Customize your rules**

Edit the files in `rules/` to match your project. Add naming conventions, architecture constraints, or anything the AI should always know. The more specific, the better.

**Read the levels guide**

[docs/levels.md](./levels.md) explains the full AI-native development framework: where you are now, what each level unlocks, and what to tackle next.

---

## Plugins in this toolkit

| Plugin | What it does |
|--------|-------------|
| `ctx` | Project memory, rules scaffolding, context management |
| `review` | Multi-agent code review before merge |
| `pentest` | Security audit for vulnerabilities |
| `context-handoff` | Pass context between sessions and agents |
| `statusline` | Project status and health at a glance |
| `gh-issues` | AI-assisted issue triage and creation |
