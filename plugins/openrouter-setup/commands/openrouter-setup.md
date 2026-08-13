---
description: "Configure OpenRouter as an OpenAI-compatible endpoint (OPENROUTER_API_KEY in env, never in code) for moonshotai/kimi-k3, deepseek/deepseek-v4, z-ai/glm-*"
argument-hint: "[model-slug] — smoke-test a specific model, e.g. z-ai/glm-4.6"
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob"]
---

# OpenRouter Access Setup

Configure OpenRouter as an OpenAI-compatible endpoint for waitlist-gated models
(`moonshotai/kimi-k3`, `deepseek/deepseek-v4`, `z-ai/glm-*`). One key,
`OPENROUTER_API_KEY`, unlocks all of them.

- Endpoint: `https://openrouter.ai/api/v1` (OpenAI Chat Completions compatible)
- **Key lives in env only — never in code or any repo file.**

## Input

**$ARGUMENTS**

Optional model slug to smoke-test; defaults to `moonshotai/kimi-k3`.

## Workflow

This is an abbreviated quick-reference. **The authoritative, full workflow lives
in `skills/openrouter-setup/SKILL.md`** — follow it (it covers the security
contract, client-wiring examples, and the full error table).

### Step 1: Check the key is in the environment (never print the value)

```bash
if [ -n "$OPENROUTER_API_KEY" ]; then
  echo "OPENROUTER_API_KEY is set (length ${#OPENROUTER_API_KEY})"
else
  echo "OPENROUTER_API_KEY is NOT set"
fi
```

### Step 2: If missing — user creates the key interactively

Direct the user to https://openrouter.ai → **Keys** → **Create Key**
(`sk-or-v1-…`). The agent cannot do this step.

### Step 3: Persist in shell profile (env, not code)

Tell the user to add to `~/.zshrc` / `~/.bashrc` (NOT a repo file):

```sh
export OPENROUTER_API_KEY='sk-or-v1-…'
```

`source ~/.zshrc` or start a new shell. Do not edit the user's profile without
confirmation; never write the key under the project tree.

### Step 4: Smoke-test (argument slug or default `moonshotai/kimi-k3`)

```bash
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY is not set — see Step 3}"
curl -sS https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<MODEL_SLUG>",
    "max_tokens": 16,
    "messages": [{"role": "user", "content": "ping"}]
  }'
```

`200` + `choices` = OK. `401` = bad key; `402` = no credits; `404` = wrong/retired
slug (check openrouter.ai/models).

### Step 5: Wire into clients

Point any OpenAI-compatible client at `base_url=https://openrouter.ai/api/v1`
with `api_key` read from `$OPENROUTER_API_KEY`. See SKILL.md for the Python
`openai` SDK example and runtime-provider notes.

### Step 6: Confirm

- `OPENROUTER_API_KEY`: set (length N) / missing
- Smoke test (`<MODEL_SLUG>`): OK / failed (status)
- Key stored: env only — not in any repo file
