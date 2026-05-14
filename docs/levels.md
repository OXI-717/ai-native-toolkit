# 4 Levels of AI-Native Development

A framework for understanding where you are and where you can go.

---

## Level 1 — Vibe Coding

**What it means**

You open a chat, describe what you need, paste the response into your editor, and ship. No setup, no conventions, no overhead. Just AI + clipboard.

This is where everyone starts — and for good reason. It works.

**What problems it solves**

- Getting unstuck fast
- Prototyping in hours instead of days
- MVP velocity without a big team

**What breaks at scale**

- AI has no memory between sessions — you re-explain context every time
- No consistency across files, teammates, or time
- No safety net: generated code gets merged without review
- Security holes slip through because nobody specifically asked the AI to look for them
- The codebase grows faster than your ability to understand it

**Plugin**: none yet — you're flying blind

**Before / After**

Before: You ask the AI to add auth. It generates code that stores passwords in plaintext. You paste it in. Three months later, a security audit finds it.

After: You add a `pentest` scan before merge. It flags the plaintext storage on day one.

---

## Level 2 — Context & Rules

**What it means**

You give the AI permanent memory of your project. An `AGENTS.md` file tells every AI session who you are, what the project is, and how things should be done. Rules files enforce naming conventions, git hygiene, and secrets handling automatically.

**What problems it solves**

- Re-explaining context on every session — gone
- Inconsistent code style between teammates — gone
- AI suggesting `git add .` or hardcoded credentials — caught by rules
- New contributors breaking conventions — prevented by scaffolding

**Plugin**: `ctx`

`ctx-init` creates the full scaffold in under 10 seconds: `AGENTS.md`, `CLAUDE.md`, and a `rules/` directory with starter rules for git and secrets. Run it once per project, commit the result, and every subsequent AI session inherits the same context.

**Before / After**

Before:
```
You: "Add a new API endpoint for user profiles"
AI: "Sure! Here's a route in Express..."
# AI doesn't know: you use Fastify, not Express. You use camelCase, not snake_case.
# You spend 10 minutes cleaning up the output.
```

After (with ctx):
```
# AGENTS.md tells AI: Fastify, TypeScript, camelCase, no console.log in production
You: "Add a new API endpoint for user profiles"
AI: Generates a Fastify route in TypeScript, camelCase, with proper error handling.
# Zero cleanup.
```

---

## Level 3 — Verified Development

**What it means**

AI writes code, and AI checks code. Two separate agents: one generates, one reviews. The reviewer looks for bugs, anti-patterns, and security issues before the code ever reaches a human reviewer or production.

**What problems it solves**

- Bugs that slip through because the author is also the reviewer
- Security vulnerabilities that only show up in production
- Code review bottlenecks when the team is small
- Inconsistent review depth depending on who's available

**Plugins**: `review` + `pentest`

`review` runs a multi-agent code review pass on your changes. It reads the diff, checks it against your project rules, and surfaces issues with specific line references.

`pentest` runs a security-focused audit: injection vectors, auth bypasses, exposed secrets, insecure defaults. It's designed to find what a distracted human reviewer misses.

**Before / After**

Before:
```bash
git push origin feature/payment-flow
# PR merged after 15-minute review
# Two weeks later: SQL injection in the order endpoint
```

After:
```bash
git push origin feature/payment-flow
# review: "Line 47 — parameterized query missing, input passed directly to DB"
# pentest: "Line 83 — API key logged in error handler"
# Both fixed before the PR is even opened
```

---

## Level 4 — Autonomous Agents

**What it means**

Agents launch agents. A task comes in, an orchestrator breaks it into subtasks, specialized agents handle each one in parallel, results are aggregated, failures trigger retries. The system self-heals and self-coordinates.

**What problems it solves**

- Tasks too large for a single context window
- Parallel workstreams that a human would have to coordinate manually
- Repetitive multi-step processes that need to run on a schedule
- Systems that need to respond to events without human intervention

**Plugin**: none yet

This level describes patterns not yet packaged in this toolkit. The plugins above (ctx, review, pentest) provide the foundation.

**Before / After**

Before: A codebase migration requires a human to coordinate 8 steps across 4 repositories over 3 days.

After: An orchestrator agent breaks the migration into a dependency graph, spawns agents for each repo in parallel, detects failures, reruns failed subtasks, and posts a summary when done.

---

## Where to start

Most teams are at Level 1 or 2. The jump from 1 to 2 takes 10 minutes. The jump from 2 to 3 takes an afternoon.

Start with `ctx-init`. Everything else builds on top of it.
