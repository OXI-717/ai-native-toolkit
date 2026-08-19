---
name: research-ensemble
description: |
  Multi-agent adversarial research with Exa AI. Two levels: standard (3-5 agents,
  30-60 min) and deep (10-15 agents, full ensemble with 3 cycles).
  Triggers: "research X", "исследуй", "deep research", "find out about",
  "compare options for", "deep dive into", "gather information on",
  "write a report about", "проанализируй", "разберись в", "investigate",
  "analyze topic", "сравни варианты", "изучи тему"
---

# Deep Research — Adversarial Ensemble v2

Conduct autonomous multi-agent research on topic: **$ARGUMENTS**

## Argument Parsing

Format: `/research-ensemble [topic]` or `/research-ensemble [topic] [level] [domain]`

Examples:
- `/research-ensemble kubernetes vs nomad` → standard, auto-domain (tech)
- `/research-ensemble creatine safety deep` → deep, auto-domain (health)
- `/research-ensemble creatine safety deep health` → deep, health
- `/research-ensemble market analysis for SaaS standard business` → standard, business

**Defaults:** level=standard, domain=auto-detect, report_language=English

**Report language:** Use `REPORT_LANGUAGE` if set. Otherwise honor an explicit language request from the user. If neither is present, write reports, headings, progress logs, and user summaries in English. Russian output is only for an explicit Russian request or `REPORT_LANGUAGE=ru`.

### Level Definitions

| Level | Agents | Time | When to use |
|-------|--------|------|-------------|
| **standard** | 6 (3 Scouts + Critic + Synthesizer + Fact-Checker) | 30-60 min | Most research tasks |
| **deep** | 12-15 (full ensemble, 3 cycles with reflections) | 1-4 hours | Serious research with verification, health, architecture decisions |

### Domain Auto-Detection

If domain not specified, detect by keywords in topic:

| Keywords | Domain |
|----------|--------|
| framework, API, library, database, architecture, kubernetes, docker, code, deploy, CI/CD, microservice, react, python, rust | **tech** |
| market, competitors, pricing, startup, revenue, business model, ROI, SaaS, funding, GTM | **business** |
| supplement, dosage, biomarker, sleep, exercise, nutrition, health, vitamin, protocol, longevity, creatine, omega | **health** |
| firewall, SSH, vulnerability, CVE, hardening, pentest, encryption, TLS, WAF, security, DDoS | **security** |
| everything else | **general** |

## Execution

### Step 0: Preparation

1. Parse arguments: extract `{TOPIC}`, `{LEVEL}`, `{DOMAIN}`
2. Create slug from topic: lowercase, replace spaces with underscores, truncate to 50 chars
3. Determine `{REPORT_LANGUAGE}` from `REPORT_LANGUAGE`, then explicit user request, then default English
4. Create output directory structure:

```
research_output/{topic_slug}/
├── _PROGRESS_LOG.md
├── streams/
│   └── data/
├── deep_dives/          # deep level only
├── reviews/
├── figures/
```

5. Initialize `_PROGRESS_LOG.md`:

```markdown
# Research Log

**Topic:** {TOPIC}
**Level:** {LEVEL}
**Domain:** {DOMAIN}
**Report language:** {REPORT_LANGUAGE}
**Started:** {TIMESTAMP}
**Output:** research_output/{topic_slug}/
```

6. Read domain configuration: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/domains.md`
7. Read Exa search guide: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/exa-search-guide.md`
8. If domain=health AND level=deep: check for user profile at `./research_profiles/my_profile.md`. If not found, research runs in universal mode without personalization.

### Step 1: Dispatch Workflow

Based on `{LEVEL}`:

- **standard** → Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/standard-workflow.md`
- **deep** → Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/deep-workflow.md`

### Step 2: Finalization

After workflow completes:

1. Verify all expected files exist (`ls` the output directory)
2. Read the TL;DR section from synthesis.md
3. Generate PDF report (same language as the report files; default English):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/generate-pdf-report.py" \
  "research_output/{topic_slug}" "{TOPIC}" "{LEVEL}" "{DOMAIN}" "{REPORT_LANGUAGE}"
```

The script outputs the absolute path to the generated PDF. Save it as `{PDF_PATH}`.

4. Show user summary:

```
## Research Complete

**Topic:** {TOPIC}
**Level:** {LEVEL} | **Domain:** {DOMAIN}
**Report language:** {REPORT_LANGUAGE}
**Output:** research_output/{topic_slug}/

### Files
- synthesis.md — main report
- reviews/_fact_check.md — fact check
- reviews/_critic_review.md — critic review
- unknowns_and_next.md — open questions and next steps
- **report.pdf** — final PDF report
[+ any additional files depending on level/domain]

### Key Takeaways
[TL;DR section from synthesis.md]

### PDF Report
{PDF_PATH}
```

## Critical Rules

1. **Output language follows `{REPORT_LANGUAGE}`.** All agents write files, reports, conclusions, headings, and comments in the selected report language. Default is English. Use Russian only when the user explicitly asks for Russian or `REPORT_LANGUAGE=ru`. Exa/WebSearch queries may use English for better coverage. Proper names and technical terms may remain in their original language when useful.
2. **Every agent writes to files.** Nothing is kept only in memory. If an agent fails to write, ORCHESTRATOR writes from the agent's output.
3. **Exa is primary search.** WebSearch is a fallback. See exa-search-guide.md for strategies.
4. **All subagents launched via Agent tool** with `subagent_type: "general-purpose"` and `run_in_background: true`.
5. **Parallel launches in ONE message.** Multiple Agent calls in a single message for independent agents.
6. **ORCHESTRATOR reads agent prompt templates** from `references/agent-prompts/`, substitutes variables, and passes as Agent prompt.
7. **Variable substitution:** Replace `{TOPIC}`, `{DOMAIN}`, `{OUTPUT_DIR}`, `{STREAM_FILES}`, `{REASONING_STYLE}`, `{USER_PROFILE}`, `{REPORT_LANGUAGE}` in agent prompts before dispatching.

## Agent Prompt Loading

To dispatch an agent:

1. Read the agent prompt template: `${CLAUDE_PLUGIN_ROOT}/skills/research-ensemble/references/agent-prompts/{role}.md`
2. Read domain-specific config from `domains.md` for the current `{DOMAIN}`
3. Substitute all `{VARIABLES}` in the template with actual values
4. Launch via Agent tool with the substituted prompt
5. After completion, verify output file exists — if not, write from agent's returned output
