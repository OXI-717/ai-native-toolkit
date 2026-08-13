---
name: openrouter-setup
description: "Configure OpenRouter as an OpenAI-compatible endpoint (OPENROUTER_API_KEY in env, never in code) for waitlist-gated models: moonshotai/kimi-k3, deepseek/deepseek-v4, z-ai/glm-*"
argument-hint: "[model-slug] — smoke-test a specific model, e.g. z-ai/glm-4.6"
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob"]
---

# OpenRouter Access Setup (OpenAI-compatible endpoint)

Configure access to models that are gated by waitlists at their native providers
(Moonshot, DeepSeek, Zhipu) through OpenRouter's OpenAI-compatible API. **One key
unlocks all listed models — no per-provider waitlists.**

OpenRouter exposes a drop-in OpenAI Chat Completions endpoint:

```
BASE_URL = https://openrouter.ai/api/v1
AUTH     = Bearer $OPENROUTER_API_KEY
```

Any OpenAI-compatible client works by pointing `base_url` at OpenRouter and
reading the key from the environment.

## Target models

| OpenRouter slug | Native provider | Notes |
|-----------------|-----------------|-------|
| `moonshotai/kimi-k3` | Moonshot (Kimi) | Kimi-K3, often waitlisted at platform.moonshot.cn |
| `deepseek/deepseek-v4` | DeepSeek | DeepSeek-V4; verify current slug on openrouter.ai/models |
| `z-ai/glm-4.6`, `z-ai/glm-4.5` | Zhipu (GLM/Z.AI) | GLM family; `z-ai/*` is the OpenRouter namespace for Zhipu |

Slugs evolve. Confirm live availability and pricing at
https://openrouter.ai/models before relying on one.

## Security contract (hard rule)

- **The key lives ONLY in an environment variable: `OPENROUTER_API_KEY`.**
- **Never** write the key into a repo file, a committed `.env`, a command, or
  chat output. Do NOT `echo "$OPENROUTER_API_KEY"`.
- Persist it in the user's shell profile (`~/.zshrc` / `~/.bashrc`) or a
  secret manager that exports it into the shell environment — never inside the
  project tree (the project tree is git-tracked and would leak the secret).
- This skill never creates a per-project key file (unlike `.gh-account` /
  `.gcloud-account`). There is exactly one OpenRouter key, and it is global.

## Input

**$ARGUMENTS**

Optional model slug to smoke-test (e.g. `z-ai/glm-4.6`). If omitted, the smoke
test uses `moonshotai/kimi-k3`.

## Workflow

### Step 1: Check whether the key is already in the environment

```bash
if [ -n "$OPENROUTER_API_KEY" ]; then
  echo "OPENROUTER_API_KEY is set (length ${#OPENROUTER_API_KEY})"
else
  echo "OPENROUTER_API_KEY is NOT set"
fi
```

Report only whether it is set and its length — never the value.

### Step 2: Obtain a key (user does this interactively) if missing

Direct the user to:

1. Sign in at https://openrouter.ai
2. Open **Keys** → **Create Key**
3. Copy the key (starts with `sk-or-v1-…`)

The agent cannot create the key on the user's behalf — it is an interactive,
account-bound action.

### Step 3: Persist the key in the shell profile (env, not code)

Tell the user to add to `~/.zshrc` (or `~/.bashrc`):

```sh
export OPENROUTER_API_KEY='sk-or-v1-…'
```

Then `source ~/.zshrc` (or start a new shell). The agent must not edit the
user's shell profile without explicit confirmation, and must never write the key
into any file under the project tree.

Verify it landed (new shell or after `source`):

```bash
[ -n "$OPENROUTER_API_KEY" ] && echo "set (length ${#OPENROUTER_API_KEY})" || echo "missing"
```

### Step 4: Smoke-test the endpoint

Resolve the model to test: use the argument if given, otherwise default to
`moonshotai/kimi-k3`.

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

A `200` with a JSON `choices` array means access works. A `401` means the key is
wrong/revoked; `402` means insufficient credits; `404` means the slug is wrong
or unavailable (verify on openrouter.ai/models).

### Step 5: Wire into OpenAI-compatible clients

The key is read from env at runtime; nothing is hardcoded.

**Python (openai SDK):**

```python
from openai import OpenAI
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=__import__("os").environ["OPENROUTER_API_KEY"],  # env, not code
)
resp = client.chat.completions.create(
    model="z-ai/glm-4.6",
    messages=[{"role": "user", "content": "ping"}],
)
```

**curl / any HTTP client:** `Authorization: Bearer $OPENROUTER_API_KEY` against
`https://openrouter.ai/api/v1/chat/completions` (Step 4 command is the template).

**opencode / Codex / other agent runtimes:** configure a provider with
`base_url: https://openrouter.ai/api/v1` and `api_key` resolved from the
`OPENROUTER_API_KEY` env var (not a literal). Runtime-specific config lives in
`~/.config/...`, outside this repo.

### Step 6: Confirm

Show summary:
- `OPENROUTER_API_KEY`: set (length N) / missing
- Smoke test (`<MODEL_SLUG>`): OK / failed (status)
- Endpoint: `https://openrouter.ai/api/v1` (OpenAI-compatible)
- Key stored: env only (shell profile) — not in any repo file

## Common errors

| Error | Fix |
|-------|-----|
| `401 Invalid API key` | Key wrong/revoked — recreate at openrouter.ai/keys |
| `402 You have insufficient credits` | Top up credits at openrouter.ai; keys are prepaid-balance, not unlimited |
| `404 model not found` | Slug wrong/retired — confirm at openrouter.ai/models; slugs change (e.g. `-latest` suffixes) |
| `429 Rate limit` | Slow down; check openrouter.ai dashboard for per-model RPM limits |
| `OPENROUTER_API_KEY: parameter null or not set` | Key not exported into the current shell — `source ~/.zshrc` or start a new shell |
| Key accidentally committed | Rotate it immediately at openrouter.ai/keys (old key is compromised); never re-paste into repo files |

## When NOT to use

- **You already have direct API access** to the provider (Moonshot/DeepSeek/Zhipu native keys) and don't mind waitlists — native endpoints are fine.
- **Cost-sensitive batch jobs** — OpenRouter adds a margin; native provider keys are cheaper at volume.
- **Streaming/low-latency prod workloads** with strict SLAs — OpenRouter is a router and may add latency / failover hops; pin a native endpoint instead.

## Notes

- OpenRouter keys are prepaid-balance (credits), not subscriptions. Monitor spend at https://openrouter.ai/credits.
- Optional headers (`HTTP-Referer`, `X-Title`) make the app recognizable in openrouter.ai/logs but are not required.
