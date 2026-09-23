---
name: agent-time
description: Use when installing or diagnosing aw-agent-time, or reporting ActivityWatch physical work, AI engagement, and autonomous agent time.
---

# Agent time

Use [aw-agent-time](https://github.com/OXI-717/aw-agent-time) for local time tracking and reports. This plugin supplies agent instructions; the tracker, dependencies, configuration schema, and installation scripts live in that separate repository.

## Establish the installed interface

Locate the user's tracker checkout and read its README before running commands. Check the installed revision and the relevant script's `--help`; follow that revision's configuration and installation instructions. Do not invent a packaged CLI, flags, config keys, paths, or launchd labels. If the repository or requested capability is unavailable, state that limitation rather than substituting a private checkout or guessing an interface.

For a new installation, read the public README and inspect its installer first. Follow the user's authorized installation scope. Configure local category rules and paths from the user's environment and the documented example configuration. Keep personal mappings outside the public checkout's tracked files. Installing this plugin alone does not install ActivityWatch, watchers, or the tracker.

## Report time correctly

Establish the requested date range and timezone. Check the installed tracker's day boundary; if it uses UTC, label that explicitly and do not silently describe a UTC day as a local calendar day.

Keep three measures separate:

| Layer | Meaning | Interpretation |
|---|---|---|
| 1 | Physical work | Active computer time attributed from observed activity |
| 2 | AI engagement | Interaction with AI sessions according to the tracker's engagement rules |
| 2.5 | Autonomous agent work | Agent execution recorded by the tracker, possibly concurrent |

These measures overlap. Never sum them into total human working hours. Concurrent agent time can exceed elapsed wall time; do not infer productivity gains or hours saved without a defined comparison. State source coverage, missing watchers, unmapped projects, stale data, and whether the current day is partial.

Prefer existing reports or a read-only report path. In versions that expose the Python scripts, inspect `aggregate.py --help`: aggregation normally reruns trackers and may write report files. Where supported, `--skip-trackers --no-obsidian` reads existing buckets and prints the report. Verify these flags against the installed version before using them. A flag named `--dry-run` is not proof that every side effect is disabled; inspect the relevant execution path before relying on it.

Present the interval and timezone, then each layer separately, followed by project breakdowns and coverage limitations. Missing observations mean unknown coverage, not proven zero work. Do not publish raw window titles, prompts, file paths, or session logs as part of an ordinary summary.

## Diagnose missing or unexpected time

Start with read-only checks, using the installed README and actual configured paths:

1. Determine whether ActivityWatch is reachable and whether the required watcher buckets have recent observations.
2. Check the configured source and output locations, category mappings, local timezone assumptions, and report freshness.
3. Inspect the relevant tracker or category validator's help and source before running it. Prefer documented non-mutating validation and redact sensitive log excerpts.
4. Compare the requested interval with source coverage before proposing a classification or scheduling fix.

Distinguish an absent watcher, unreachable server, stale derived bucket, missing AI source, and unmatched category. Report the observed evidence and the smallest appropriate correction. A reporting or diagnosis request does not authorize installing services, restarting watchers, rebuilding buckets, reindexing history, or changing collection settings. If the user already requested a specific repair, complete the authorized repair and verify that observations resume.
