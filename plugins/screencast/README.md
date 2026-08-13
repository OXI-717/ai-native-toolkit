# screencast

Record a browser e2e flow into a polished demo mp4 — fake browser chrome with a live address bar, a synthetic cursor, click ripples, and narration captions.

**Triggers:** "запиши видео-демо", "сделай скринкаст", "снять прохождение флоу", "record a demo", "capture a walkthrough", "make a product video".

**Output:** `./screencast/<name>-<YYYY-MM-DD>/scenario.yaml` + `out/<name>.mp4` in the current project.

**Scope:** browser flows only. NOT screenshots (webapp-testing), load (loadtest), or security (pentest). macOS-local target.

**How it works:** Claude reconnoiters the flow via Playwright MCP, generates a declarative YAML scenario (`goto`/`click`/`caption`/`waitFor`/…), and the bundled `engine/` (Playwright + ffmpeg) renders the video. See `engine/scenario.schema.json` and `engine/examples/checkout.yaml`.
