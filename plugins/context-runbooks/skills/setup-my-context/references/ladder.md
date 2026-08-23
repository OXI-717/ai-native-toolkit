# L0-L4 Context Runbook Ladder

This ladder is one path with different stopping points.

- Non-technical users usually stop after L2.
- Technical users continue to L3-L4.
- Each level should fit into one evening of focused work.

## L0: Workspace

Create the umbrella where work lives and define the first boundaries.

Done when:

- there is a root `AGENTS.md`;
- new projects have an obvious place to go;
- `.gitignore` excludes local worktrees, credentials, caches, and generated output;
- credentials have a storage rule and are not pasted into context files.

## L1: Project Context

Give each project enough local context that an agent can start without a long
prompt.

Done when:

- the project has `AGENTS.md`;
- reusable rules live in `rules/*.md`;
- the project file imports only the rules it needs;
- a new session can explain the project structure from files, not from memory.

## L2: Memory From Feedback

Turn repeated mistakes, decisions, and review findings into maintained rules.

Done when:

- there is a lightweight `MEMORY.md` or memory index;
- at least three real "we should not repeat this" items became rules;
- rules include scope and source, not just advice;
- stale rules have an owner or deletion path.

## L3: Delegation

Extract repeatable work into skills, commands, or small scripts.

Done when:

- three recurring workflows can be invoked by name;
- each workflow has clear inputs, outputs, and stopping conditions;
- parallel work is used only when tasks do not share mutable state;
- handoff notes make a cold-start session useful.

## L4: Delivery Pipeline

Make agent work deliverable through review and acceptance.

Done when:

- a task can move from issue/request to branch/worktree, PR, review, and acceptance;
- the merge actor is explicit;
- verification evidence is recorded;
- blocked or paused work has a handoff.

## Recommended Demonstrations

Use three short demos for a live group:

1. L1: open a fresh session and show that the agent reads project rules.
2. L3: call a custom workflow instead of pasting a long prompt.
3. L4: send one small task through branch, PR, review, and acceptance.
