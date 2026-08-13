---
name: screencast
description: Records a screencast/demo video of a web-app flow (browser chrome, address bar, synthetic cursor, click ripples, narration captions). Use when the user asks to record a demo, capture a walkthrough, make a product video / screencast / демо-ролик, записать видео-демо, снять прохождение или работу приложения, сделать скринкаст. Reconnoiters the live flow via Playwright MCP, generates a declarative YAML scenario, renders mp4 to ./screencast/. NOT for screenshots (use webapp-testing), load testing (loadtest), or security testing (pentest).
---

# screencast

Record a browser e2e flow into a polished demo mp4. Engine lives in `${CLAUDE_PLUGIN_ROOT:-.}/engine`; scenarios and videos are written into the current project under `./screencast/`.

## Workflow

### 1. Doctor / install guard (idempotent)
```bash
ENGINE="${CLAUDE_PLUGIN_ROOT:-.}/engine"
command -v ffmpeg >/dev/null || { echo "ffmpeg required: brew install ffmpeg"; exit 1; }
[ -d "$ENGINE/node_modules" ] || (cd "$ENGINE" && npm install)
ls ~/.cache/ms-playwright/chromium-* >/dev/null 2>&1 || (cd "$ENGINE" && npx playwright install chromium)
```
ffmpeg is reported, never auto-installed. Re-running is safe.

### 2. Recon (default)
Drive the target flow live via **Playwright MCP**: `browser_navigate` → `browser_snapshot` → `browser_click`. Capture:
- exact selectors (prefer role+name; fall back to CSS),
- navigation behaviour per click: same-tab vs popup/new-tab (`followPopup`/`switchToNewTab`),
- iframes (`switchToFrame`), file inputs (`uploadFile`),
- a stable interactive selector to `waitFor` after each `goto` (hydration),
- whether the page has a `position:fixed`/`sticky` top header — if so set `chrome: false`,
- whether any URL or field carries a secret (keep it out of captions; visible env values need `mask: true`).

If MCP is unavailable or the app isn't running, author the scenario from the user's described steps and note the lower confidence. Do not use AskUserQuestion/TaskCreate/Team APIs in scripts.

### 3. Generate scenario
Write `./screencast/<name>-<YYYY-MM-DD>/scenario.yaml` (see `${CLAUDE_PLUGIN_ROOT:-.}/engine/examples/checkout.yaml` and `scenario.schema.json`). Use `env:` for creds (referenced as `$VAR`), `dnsOverride` only if DNS is flaky, `idempotent: false` on side-effecting clicks, `chrome: false` for fixed-header sites.

### 4. Render
```bash
bash "$ENGINE/make-video.sh" ./screencast/<name>-<date>/scenario.yaml
# add --allow-js only if the scenario has reviewed js: steps; --keep to retain webm/frames
```

### 5. Verify
```bash
MP4=./screencast/<name>-<date>/out/<name>.mp4
ffprobe -v error -show_entries format=duration -of csv=p=0 "$MP4"
ffmpeg -y -loglevel error -ss <t> -i "$MP4" -frames:v 1 -update 1 /tmp/sc_frame.png
```
Read the frame, confirm the flow + captions + address-bar transitions look right; iterate on the scenario and re-render.

## Notes
- Output dir is `./screencast/<name>-<date>/`; an existing `scenario.yaml` is preserved (use `--overwrite` to regenerate).
- `resize`/responsive demos are unsupported (fixed recording size).
- macOS-local target; headless-Linux Cyrillic captions may need bundled fonts.
