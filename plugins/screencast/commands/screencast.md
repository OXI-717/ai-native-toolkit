---
description: Record a screencast video of a web-app flow. Reconnoiters via Playwright MCP, generates a YAML scenario, renders mp4 to ./screencast/.
argument-hint: "<url-or-flow-description>"
allowed-tools: ["Bash", "Glob", "Grep", "Read", "Edit", "Write"]
---

Invoke the **screencast** skill to record a demo video of the flow described by: $ARGUMENTS

Follow the skill workflow: doctor/install guard → recon the flow via Playwright MCP → generate `./screencast/<name>-<date>/scenario.yaml` → render with `engine/make-video.sh` → verify frames.
