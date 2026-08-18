---
name: deep-interview
description: |
  Focused clarification interview for vague tasks. Asks questions one at a time,
  explores codebase for context, outputs structured brief for planning/execution.
  Triggers: "interview", "clarify task", "deep interview",
  "define requirements", "scope this out", "what exactly do you need",
  "let's clarify", "gather requirements"
---

# Deep Interview — Task Clarification

Clarify a vague task through focused one-at-a-time questions, then produce a structured brief.

**Input:** $ARGUMENTS

## When to use

- Task is too vague for brainstorming ("make it better", "fix the architecture", "add notifications")
- Scope is unclear — could be 1 hour or 1 week
- Multiple interpretations possible
- User hasn't thought through non-goals or constraints yet

## When NOT to use

- Task is already clear (specific file, specific bug, specific feature)
- User provided a spec or detailed description
- Already in brainstorming/planning phase

---

## Process

### Phase 1: Context Scan (silent, no output to user)

Before asking any questions:

1. For the project root and every directory scoped to the task, discover and read each applicable `AGENTS.md`; where a scope has no `AGENTS.md`, read its `CLAUDE.md` fallback. Recursively read the relative files referenced by `@` imports in each manifest or imported rule before proceeding.
2. Check git log for recent work context (last 10 commits)
3. Scan project structure (top-level dirs, key config files)
4. If task mentions specific areas — read those files

This gives you context to ask **informed** questions, not generic ones.

### Phase 2: Interview (interactive)

**RULES:**
- Ask **ONE question at a time**. Never dump multiple questions.
- Wait for answer before asking next question.
- Questions must be **specific and actionable**, not philosophical.
- Use context from Phase 1 to ask informed questions (reference files, patterns, existing code).
- Skip questions whose answers are obvious from the codebase.
- Maximum **7 questions** total. Stop earlier if picture is clear.
- After each answer, briefly acknowledge and move to next question.

**Question flow** (adapt order based on responses, skip what's already clear):

#### 1. Intent
"What's the end result you want? Not how — what should be different when this is done?"

#### 2. Trigger
"What prompted this? A bug, a user request, technical debt, new requirement?"

#### 3. Scope boundary
"What's IN scope and what's explicitly OUT? For example: [give 1-2 concrete examples based on codebase context]"

#### 4. Users / consumers
"Who uses this? End users, other developers, CI/CD, another service?"

#### 5. Constraints
"Any hard constraints? Timeline, compatibility, performance, specific tech stack, must not break X?"

#### 6. Success criteria
"How will you know this is done? What's the minimum that makes it shippable?"

#### 7. Known unknowns
"Anything you're unsure about or worried might be harder than it looks?"

### Phase 3: Brief Generation

After all questions answered (or after 7 questions max), generate a structured brief:

```markdown
# Task Brief: [concise title]

**Generated:** [date]
**Source:** deep-interview session

## Intent
[1-2 sentences: what should change]

## Trigger
[Why now — bug, request, debt, opportunity]

## Scope
**In scope:**
- [concrete item 1]
- [concrete item 2]

**Out of scope:**
- [concrete exclusion 1]
- [concrete exclusion 2]

## Constraints
- [constraint 1]
- [constraint 2]

## Success Criteria
- [ ] [measurable criterion 1]
- [ ] [measurable criterion 2]

## Open Questions
- [anything unresolved]

## Relevant Code
- `path/to/file.ts` — [why relevant]
- `path/to/other.py` — [why relevant]

## Suggested Next Step
[One of: brainstorming, writing-plans, background implementation workflow, direct implementation]
```

### Phase 4: Handoff

Present the brief to user. Then suggest next step based on complexity:

| Complexity | Suggested next step |
|-----------|-------------------|
| Simple (1-2 files, clear path) | "Ready to implement directly" |
| Medium (clear but multi-file) | "Ready for `/writing-plans` — skip brainstorming" |
| Complex (architectural decisions needed) | "Ready for brainstorming → writing-plans" |
| Large (multi-day, parallel work) | "Ready for brainstorming → writing-plans → parallel implementation" |

**Do NOT auto-invoke the next step.** Present the brief, suggest the step, let the user decide.

If user confirms, save the brief to the project:
- Save to `docs/briefs/[date]-[slug].md` (create dir if needed)
- Or wherever the user prefers

---

## Example Session

```
User: /deep-interview add notifications to the app

Claude: [reads codebase, finds a web app with no notification system]

Claude: "What kind of notifications? Push notifications to mobile,
        in-app toasts, email, or a chat integration?"

User: "in-app + email"

Claude: "Got it. What events should trigger a notification? I can see
        sign-up, checkout, and password reset flows in the codebase."

[... continues one question at a time ...]

Claude: [generates brief, suggests writing-plans as next step]
```

---

## Anti-patterns

- Asking 5 questions at once — defeats the purpose
- Generic questions not grounded in codebase context — waste of time
- Going beyond 7 questions — diminishing returns, user gets annoyed
- Auto-starting brainstorming/planning without user confirmation
- Writing code or making changes during interview
