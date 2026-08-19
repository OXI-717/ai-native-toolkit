# Deep Workflow — Full Adversarial Ensemble (3 Cycles)

**Agents:** 12-15 total
**Time:** 1-4 hours
**Cycles:** 3 (Broad Search → Deep Dives → Synthesis + Verification)

This workflow is executed by the ORCHESTRATOR (you) after SKILL.md completes Step 0 preparation.

> **LANGUAGE:** All agents and the orchestrator write output files in `{REPORT_LANGUAGE}`. Default is English. Use Russian only when the user explicitly asks for Russian or `REPORT_LANGUAGE=ru`. Search queries may use English. Keep proper names and technical terms in their original language when useful.

## Step 0: Extended Preparation

### Check Existing Consensus References

1. Check if `research_output/` contains any existing `consensus_reference*.md` files for related topics
2. If a matching consensus exists:
   - Load it as context for scouts — they focus on GAPS and UPDATES, not re-collecting
3. Record in `_PROGRESS_LOG.md` which consensus references were loaded

### User Profile (health domain only)

1. Check for user profile at `./research_profiles/my_profile.md`
2. If found — read and assess profile depth:

| Depth | Criteria | Impact |
|-------|---------|--------|
| **RICH** | Demographics + labs + ≥2 of (genetics, profiles, supplements, training) | Full personalization |
| **BASIC** | Demographics + goals + some labs OR supplements | Moderate personalization |
| **MINIMAL** | Only demographics or goals | Light personalization |
| **NONE** | No profile found | Universal research, no personalization |

3. Adapt Stream C based on depth:
   - RICH: "Personalization: genetic modifiers, lab-based dosing, interaction with current stack"
   - BASIC: "Subgroup analysis: what changes for [age/sex/conditions]?"
   - MINIMAL: "Practical implementation: adherence strategies, cost-effectiveness, barriers"
   - NONE: "Applications: real-world use cases, common mistakes, implementation frameworks"

4. Record profile assessment in `_PROGRESS_LOG.md`

---

## Cycle 1: Broad Search (25-35% of time)

### Step 1: Launch Scouts (4-5 parallel)

#### Preparation

1. Read scout prompt template: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/agent-prompts/scout.md`
2. Read domain config from `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/domains.md`
3. Get stream assignments (5 streams for deep level)

#### Assignments

| Scout | Stream ID | Reasoning Style | Stream Focus |
|-------|-----------|----------------|--------------|
| A | a | Analytical | [1st stream from domain] |
| B | b | Contrarian | [2nd stream from domain] |
| C | c | Mechanistic | [3rd stream from domain — or adapted by profile depth for health] |
| D | d | Systems-thinking | [4th stream from domain] |
| E | e | Pragmatic | [5th stream from domain] |

#### Launch

Substitute variables in scout prompt, including `{REPORT_LANGUAGE}`. **Launch ALL 4-5 scouts in ONE message** with `run_in_background: true`.

#### After Scouts Complete

Same verification as standard workflow. Update `_PROGRESS_LOG.md`.

### Step 2: Launch Critic + Statistician (2 parallel)

#### When to Launch Statistician

| Domain | Launch Statistician? |
|--------|---------------------|
| health | ALWAYS |
| security | ALWAYS |
| tech | if ≥10 quantitative claims in streams |
| business | if ≥10 quantitative claims in streams |
| general | if ≥10 quantitative claims in streams |

To check: scan stream files for numerical claims (percentages, dollar amounts, sample sizes, etc.)

#### Preparation

1. Read critic prompt: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/agent-prompts/critic.md`
2. Read statistician prompt: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/agent-prompts/statistician.md`
3. Set `{STREAM_FILES}` to all stream file paths
4. Set `{REPORT_LANGUAGE}` to the selected report language

#### Launch

Launch BOTH in ONE message (both with `run_in_background: true`).
If statistician is not needed, launch only critic.

#### After Both Complete

Verify both output files exist. Update `_PROGRESS_LOG.md`.

### Step 3: Reflection 1 (ORCHESTRATOR)

Read all streams + critic review + methods review (if exists).

Write to `_PROGRESS_LOG.md`:

```markdown
## Reflection 1

### Stream Takeaways
[Brief summary of each stream]

### Top 5 Critic Findings
1. [finding]
...

### Top 5 Methodological Issues (from Statistician)
1. [issue]
...

### Studies to Trust vs Ignore
- Trust: [list of A-B graded studies]
- Ignore: [list of C-D graded studies]

### Deep Dive Directions (2-3)
1. **[Direction]** — Reason: [gap from critic], Expected value: [HIGH/MEDIUM]
2. **[Direction]** — Reason: [methodological concern], Expected value: [HIGH/MEDIUM]
3. **[Direction]** — Reason: [missing angle], Expected value: [MEDIUM]
```

---

## Cycle 2: Deep Dives (25-35% of time)

### Step 4: Launch Deep Divers (2-3 parallel)

#### Preparation

1. Read deep-diver prompt: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/agent-prompts/deep-diver.md`
2. From Reflection 1, select 2-3 gaps with highest expected value
3. Prepare `{GAP_DESCRIPTION}` for each deep diver
4. Set `{CONTEXT_FILES}` to relevant streams + critic review + methods review

#### Launch

Substitute variables for each deep diver, including `{REPORT_LANGUAGE}`. **Launch ALL in ONE message** with `run_in_background: true`.

#### After Deep Divers Complete

Verify files exist in `{OUTPUT_DIR}/deep_dives/`. Update `_PROGRESS_LOG.md`.

### Step 5: Reflection 2 + Convergence Check (ORCHESTRATOR)

#### Reflection

Read all files (streams + deep dives + reviews). Write reflection to `_PROGRESS_LOG.md`.

#### Convergence Check (MANDATORY)

1. Extract 5-10 key conclusions from ALL research (streams + deep dives)
2. For each conclusion: count how many independent sources support it

```markdown
## Convergence Check

| # | Conclusion | Supports | Refutes | Not mentioned | Status |
|---|-------|-------------|------------|--------------|--------|
| 1 | [claim] | A, B, DD-1 | - | C, D | CONVERGES |
| 2 | [claim] | A, D | C | B, DD-2 | CONTESTED |
| 3 | [claim] | DD-1 | - | all others | SINGLE SOURCE |

**Agreement level:** X/Y = Z%
```

3. Decision:
   - `≥ 0.70` → **Convergence sufficient.** Proceed to Cycle 3.
   - `0.50 - 0.70` → **Partial convergence.** Launch 1 additional deep dive on the most contested question. Then proceed.
   - `< 0.50` → **Weak convergence.** Flag as genuinely uncertain topic. Proceed anyway but synthesis must reflect uncertainty prominently.

4. For CONTESTED claims: record both sides with evidence grades. Do NOT resolve by force.
5. For SINGLE SOURCE claims: mark confidence LOW.

---

## Cycle 3: Synthesize + Verify (20-30% of time)

### Step 6: Launch Synthesizer (1 agent)

#### Preparation

1. Read synthesizer prompt: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/agent-prompts/synthesizer.md`
2. Collect `{ALL_FILES}`: ALL streams + deep dives + critic + statistician + progress log

#### Launch

Set `{LEVEL}` = deep (10 sections) and pass `{REPORT_LANGUAGE}`. Launch 1 Agent. Wait for completion.

Verify `{OUTPUT_DIR}/synthesis.md` exists.

### Step 7: Launch Domain Reviewer (conditional)

#### When to Launch

| Domain | Launch? |
|--------|---------|
| health | MANDATORY |
| security | MANDATORY |
| tech | OPTIONAL (if synthesis contains infrastructure recommendations) |
| business | OPTIONAL (if synthesis contains regulatory claims) |
| general | SKIP |

#### Preparation

Read domain-reviewer prompt: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/agent-prompts/domain-reviewer.md`

Set `{USER_PROFILE}` if health domain and profile exists. Pass `{REPORT_LANGUAGE}`.

#### Launch

Launch 1 Agent. Wait for completion. Verify `{OUTPUT_DIR}/reviews/_domain_review.md` exists.

### Step 8: Launch Fact-Checker (MANDATORY ALWAYS)

Same as standard workflow but with `{NUM_CLAIMS}` = 15 and `{REPORT_LANGUAGE}` set.

Apply corrections to synthesis.md if needed.

### Step 9: Launch Interaction Mapper (conditional)

#### When to Launch

| Domain | Launch? |
|--------|---------|
| health | MANDATORY |
| security | OPTIONAL (if multi-layer architecture) |
| tech | SKIP |
| business | SKIP |
| general | SKIP |

#### Preparation

Read interaction-mapper prompt: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/agent-prompts/interaction-mapper.md`

Set `{DEEP_DIVE_FILES}` to all deep dive file paths. Pass `{REPORT_LANGUAGE}`.

#### Launch

Launch 1 Agent. Wait for completion. Verify `{OUTPUT_DIR}/interaction_map.md` exists.

### Step 10: Finalization (ORCHESTRATOR)

#### Write unknowns_and_next.md

More detailed than standard level:

```markdown
# Unknowns and Next Steps

## Known Unknowns (by impact)
1. [Gap] — Impact: CRITICAL — Reason: [explanation]
2. [Gap] — Impact: HIGH
3. ...

## Unexpected Findings
- [Unexpected discovery]

## Unresolved Critic Findings
- [Critic findings that were not fully resolved]

## Suggested Follow-up Research (at least 3)
1. **[Topic]** — Method: [approach], Priority: [HIGH/MEDIUM], Effort estimate: [hours]
2. ...
3. ...
```

#### Write consensus_reference.md (if applicable)

If the topic warrants a reusable knowledge base (health protocols, tech comparisons, security baselines):

Create a universal, non-personalized reference organized by OUTCOMES (what it affects):

```markdown
# Consensus Reference: {TOPIC}

## [Outcome 1]

**Bottom line:** [1 sentence]

| Parameter | Value |
|----------|----------|
| Evidence grade | A / B / C |
| Effect size | [specific number with CI] |
| Key studies | [Author Year (n=X, design)] |
| Population | [who it applies to] |

**Limitations:** [limitations]

## [Outcome 2]
...
```

Write to: `{OUTPUT_DIR}/consensus_reference.md`

#### Update Progress Log

```markdown
## Final Status
- **Completed:** {TIMESTAMP}
- **Total agents:** [count]
- **Cycles:** 3
- **Convergence level:** [X%]
- **Files created:** [list all]
- **Corrections applied:** [from fact-checker]
- **Domain review:** [summary]
- **Status:** COMPLETE
```

#### Return to SKILL.md

Return control to SKILL.md for Step 2 (user summary).
