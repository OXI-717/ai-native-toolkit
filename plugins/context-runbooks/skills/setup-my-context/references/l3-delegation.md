# L3: Delegation Runbook

## Purpose

Turn repeatable prompts into named workflows, then delegate only the parts that
have clear inputs, outputs, and stopping conditions.

## Candidates

Good candidates:

- repeated research collection;
- release note drafting;
- issue triage;
- code review checklist;
- report generation;
- environment diagnostics.

Poor candidates:

- vague strategic decisions;
- work requiring private judgment without criteria;
- production mutation without an approval gate;
- tasks where parallel workers will edit the same files.

## Workflow Card

```markdown
# Workflow: <name>

## Trigger

Use when <specific situation>.

## Inputs

- <input>

## Output

- <artifact or decision>

## Stop Conditions

- Stop and ask when <condition>.
- Do not mutate <system> without approval.
```

## Skill Or Command

Use a skill when the agent needs reusable judgment. Use a command when the user
wants a short invocation for a known workflow. Use a script when deterministic
logic matters more than prose.

## Parallelism Check

Parallelize only when all are true:

- tasks can complete independently;
- tasks do not edit the same files or external records;
- each worker has a clear output contract;
- a coordinator will review and integrate results.

## Done When

- Three recurring workflows are captured as cards, skills, commands, or scripts.
- Each workflow has inputs, outputs, and stop conditions.
- The user can invoke at least one workflow by name.
