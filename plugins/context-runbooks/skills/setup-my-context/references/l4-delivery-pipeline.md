# L4: Delivery Pipeline Runbook

## Purpose

Make agent work deliverable: task, branch/worktree, implementation, verification,
PR, review, acceptance, and merge.

## Minimal Pipeline

```text
request/issue
  -> claim
  -> repo-local worktree or branch
  -> implementation
  -> verification evidence
  -> PR
  -> review
  -> acceptance
  -> merge or close with reason
```

## Task Definition

Every task should state:

- problem;
- scope;
- files or areas likely affected;
- acceptance criteria;
- verification command;
- who accepts the work.

## Claim

Use a visible claim when multiple people or agents may work in the same queue:

```markdown
claimed: <owner/session> @ <UTC time>
```

Use labels or tracker state when available. A hidden local claim is not enough
for shared queues.

## Verification Evidence

Record exact commands and outcomes:

```markdown
Verification:
- `pytest tests/foo_test.py -q` PASS
- `npm run lint` PASS
```

If a full test suite is too expensive, state what was run and what risk remains.

## Acceptance

The author should not be the only acceptance signal for risky work. Use one of:

- human owner review;
- independent AI review plus tests;
- CI plus a clear low-risk policy.

Merge rules must be explicit. For example:

```markdown
Class B/C work is merged by the acceptor, not by the worker.
```

## Done When

- One small task has gone through the full path.
- The user can point to the verification evidence.
- The merge/acceptance actor is explicit.
- Paused work has handoff notes.
