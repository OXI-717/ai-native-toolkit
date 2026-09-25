#!/usr/bin/env python3
"""Public-repo hygiene scan for ai-native-toolkit.

Generic leak classes only — this file is itself publishable, so it must not
embed internal identifiers. The private-side export gate remains the primary
defense; this scan is the tripwire for anything committed directly to the
public repo. Run from the repository root.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key-header", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("generic-api-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("home-relative-path", re.compile(r"/Users/[A-Za-z0-9_-]+|/home/[a-z_][a-z0-9_-]*")),
    # Any sibling repository of this org that is not public is internal.
    ("sibling-repo-ref", re.compile(r"OXI-717/[A-Za-z0-9_.-]+")),
]

PUBLIC_REPO_REFS = {"OXI-717/ai-native-toolkit", "OXI-717/aw-agent-time"}

# Reviewed baseline: (path, rule) pairs that are intentional public content.
ALLOWLIST = {
    ("plugins/context-handoff/skills/context-handoff/SKILL.md", "home-relative-path"),
    ("plugins/pentest/skills/pentest/scanner/secret_patterns.py", "private-key-header"),
}

SECRET_FILENAMES = re.compile(
    r"(^|/)(\.env(\..*)?|[^/]*\.(pem|key|p12|pfx|jks)|id_rsa[^/]*|service-account[^/]*\.json)$"
)

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
SKIP_SELF = "scripts/check_hygiene.py"


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return [p for p in out.stdout.split("\0") if p]


def main() -> int:
    findings: list[str] = []
    for rel in tracked_files():
        if rel == SKIP_SELF:
            continue
        if SECRET_FILENAMES.search(rel):
            findings.append(f"{rel}: secret-like filename")
        path = ROOT / rel
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        for rule, pattern in PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                if rule == "sibling-repo-ref" and value.rstrip(".").removesuffix(".git") in PUBLIC_REPO_REFS:
                    continue
                if (rel, rule) in ALLOWLIST:
                    continue
                findings.append(f"{rel}: [{rule}]")
                break
    if findings:
        for finding in findings:
            print(f"FAIL: {finding}", file=sys.stderr)
        return 1
    print(f"OK: {len(tracked_files())} tracked files clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
