# Standard Workflow — Adversarial Ensemble (1 Cycle)

**Agents:** 6 total (3 Scouts + Critic + Synthesizer + Fact-Checker)
**Time:** 30-60 minutes
**Cycles:** 1

This workflow is executed by the ORCHESTRATOR (you) after SKILL.md completes Step 0 preparation.

> **LANGUAGE:** All agents and the orchestrator write output files in `{REPORT_LANGUAGE}`. Default is English. Use Russian only when the user explicitly asks for Russian or `REPORT_LANGUAGE=ru`. Search queries may use English. Keep proper names and technical terms in their original language when useful.

## Step 1: Launch Scouts (3 parallel)

### Preparation

1. Read scout prompt template: `${CLAUDE_PLUGIN_ROOT}/skills/deep-research/references/agent-prompts/scout.md`
2. Read domain config from `${CLAUDE_PLUGIN_ROOT}/skills/deep-research/references/domains.md` for current {DOMAIN}
3. Get stream assignments for the domain (3 streams for standard level)

### Assignments

| Scout | Stream ID | Reasoning Style | Stream Focus (from domains.md) |
|-------|-----------|----------------|-------------------------------|
| A | a | Analytical | [1st stream from domain config] |
| B | b | Contrarian | [2nd stream from domain config] |
| C | c | Pragmatic | [3rd stream from domain config] |

### Launch

Substitute variables in scout prompt template for each scout:
- `{TOPIC}` → research topic
- `{DOMAIN}` → detected/specified domain
- `{STREAM_FOCUS}` → stream assignment from domains.md
- `{REASONING_STYLE}` → assigned style (Analytical/Contrarian/Pragmatic)
- `{STREAM_ID}` → a/b/c
- `{OUTPUT_DIR}` → research output directory path
- `{DOMAIN_RULES}` → additional agent rules from domains.md
- `{REPORT_LANGUAGE}` → selected report language

**Launch ALL 3 scouts in ONE message** using Agent tool with:
- `subagent_type: "general-purpose"`
- `run_in_background: true`

### After All Scouts Complete

1. Verify files exist:
```bash
ls {OUTPUT_DIR}/streams/
```
Expected: `stream_a_*.md`, `stream_b_*.md`, `stream_c_*.md`

2. If any scout didn't write a file — write from the agent's returned output
3. Update `_PROGRESS_LOG.md`:
```markdown
## Cycle 1: Scouts Complete

### Stream A (Analytical): {stream_focus}
- Sources found: [count]
- Key findings: [brief list]

### Stream B (Contrarian): {stream_focus}
- Sources found: [count]
- Key findings: [brief list]

### Stream C (Pragmatic): {stream_focus}
- Sources found: [count]
- Key findings: [brief list]
```

## Step 2: Launch Critic (1 agent)

### Preparation

1. Read critic prompt template: `${CLAUDE_PLUGIN_ROOT}/skills/deep-research/references/agent-prompts/critic.md`
2. Collect list of all stream files: `{STREAM_FILES}`

### Launch

Substitute variables:
- `{STREAM_FILES}` → list of all stream file paths
- `{OUTPUT_DIR}` → output directory
- `{REPORT_LANGUAGE}` → selected report language

Launch 1 Agent (subagent_type: "general-purpose"). Wait for completion.

### After Critic Completes

1. Verify `{OUTPUT_DIR}/reviews/_critic_review.md` exists
2. Update `_PROGRESS_LOG.md` with critic summary

## Step 3: Reflection (ORCHESTRATOR)

Read all stream files and critic review. Write reflection to `_PROGRESS_LOG.md`:

```markdown
## Reflection

### Stream Takeaways
- Stream A: [key takeaway]
- Stream B: [key takeaway]
- Stream C: [key takeaway]

### Main Critic Findings
1. [Most important finding from critic]
2. [Second most important]
3. [Third]

### Unexpected Findings
- [anything unexpected]

### Assessment
Data is sufficient for synthesis: [YES/NO]
[If NO: what is missing and why the standard-level workflow continues]
```

For standard level, almost always proceed to synthesis. Only abort if scouts returned essentially nothing.

## Step 4: Launch Synthesizer (1 agent)

### Preparation

1. Read synthesizer prompt template: `${CLAUDE_PLUGIN_ROOT}/skills/deep-research/references/agent-prompts/synthesizer.md`
2. Collect `{ALL_FILES}`: all stream files + critic review + progress log

### Launch

Substitute variables:
- `{ALL_FILES}` → list of ALL file paths
- `{TOPIC}`, `{DOMAIN}`, `{LEVEL}` → research parameters (LEVEL=standard)
- `{OUTPUT_DIR}` → output directory
- `{REPORT_LANGUAGE}` → selected report language

Instruct synthesizer to use **7 sections** (standard format).

Launch 1 Agent. Wait for completion.

### After Synthesizer Completes

Verify `{OUTPUT_DIR}/synthesis.md` exists and is non-empty.

## Step 5: Launch Fact-Checker (1 agent — MANDATORY)

### Preparation

1. Read fact-checker prompt template: `${CLAUDE_PLUGIN_ROOT}/skills/deep-research/references/agent-prompts/fact-checker.md`

### Launch

Substitute variables:
- `{OUTPUT_DIR}` → output directory
- `{NUM_CLAIMS}` → 10 (standard level)
- `{REPORT_LANGUAGE}` → selected report language

Launch 1 Agent. Wait for completion.

### After Fact-Checker Completes

1. Read `{OUTPUT_DIR}/reviews/_fact_check.md`
2. If corrections needed:
   - Read `{OUTPUT_DIR}/synthesis.md`
   - Apply each correction listed in _fact_check.md
   - Write updated synthesis.md
3. Update `_PROGRESS_LOG.md`:
```markdown
## Fact Check
- Claims checked: [count]
- Corrections applied: [count]
- Status: [ALL VERIFIED / CORRECTIONS APPLIED]
```

## Step 6: Finalization (ORCHESTRATOR)

### Write unknowns_and_next.md

Based on critic review, reflection, and gaps from streams, write:

```markdown
# Unknowns and Next Steps

## Known Unknowns
1. [Gap] — Impact: HIGH
2. [Gap] — Impact: MEDIUM
3. ...

## Unexpected Findings
- [What was surprising during the research]

## Suggested Follow-up Research
1. [Direction 1] — Reason: [reason], Method: [how to research it]
2. [Direction 2] — Reason: [reason], Method: [how]
3. [Direction 3] — Reason: [reason], Method: [how]
```

Write to: `{OUTPUT_DIR}/unknowns_and_next.md`

### Update Progress Log

```markdown
## Final Status
- **Completed:** {TIMESTAMP}
- **Total agents:** 6
- **Files created:** [list]
- **Status:** COMPLETE
```

### Return to SKILL.md

Return control to SKILL.md for Step 2 (user summary).
