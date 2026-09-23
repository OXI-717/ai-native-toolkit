#!/usr/bin/env python3
"""Doc-reference gate for ai-native-toolkit.

Every path a shipped document points at must resolve inside this repository.
A plugin README or reference that names `docs/...`, `plugins/.../examples/...`,
or `[guide](references/x.md)` is promising a file; if the file is not exported,
the reader hits a dead end. This script fails on any such dangling reference.

Checks, per tracked markdown file:

1. Markdown links/images `[text](target)` — non-URL targets must resolve
   relative to the file's directory (fragments and query strings ignored).
2. Backtick/code-span tokens that start with a repo root segment
   (e.g. `plugins/foo/cli`, `docs/spec.md`, `references/gsc.md`) — must resolve
   relative to the file's directory, the containing plugin directory, or the
   repository root.

Tokens that clearly describe reader-side paths are ignored: `~/...`,
`$VAR/...`, `.env`-style dotfiles without a repo root, bare filenames like
`seo-hub.toml`, and URLs. The allowlist below is for reviewed false positives;
keep entries specific (file + token).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Repo root segments a token may legitimately start with. A token beginning
# with one of these is treated as a promise that the path exists in-repo.
REPO_ROOTS = (
    "plugins/",
    "docs/",
    "scripts/",
    "examples/",
    "references/",
    "skills/",
    ".github/",
    ".claude-plugin/",
    ".agents/",
)

MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)[^)]*\)")
# Backtick spans (single or double) containing a path-like token.
CODE_SPAN_RE = re.compile(r"`{1,2}([^`\n]+)`{1,2}")
# Candidate path token inside a code span: starts with a repo root and has no
# whitespace-free-breaking characters.
PATH_TOKEN_RE = re.compile(r"(?<![\w.-])([A-Za-z0-9_./-]+(?:\.[A-Za-z0-9]+)?)(?![\w.-])")

IGNORE_PREFIXES = ("~", "$", "http://", "https://", "mailto:", "#")
IGNORE_TARGETS = {"URL"}

# Reviewed false positives: (file, token) pairs that are intentionally
# non-resolving (e.g. reader-side config conventions shown in examples).
ALLOWLIST: set[tuple[str, str]] = {
    # Reader-side output convention: briefs are written under the user's
    # workspace docs/, not the repo's.
    ("plugins/deep-interview/skills/deep-interview/SKILL.md", "docs/briefs/"),
    # Report template: filenames of per-finding fix docs that get generated.
    ("plugins/pentest/skills/pentest/references/fix-template.md", "01-auth-date-ttl.md"),
    ("plugins/pentest/skills/pentest/references/fix-template.md", "02-public-members-dir.md"),
    ("plugins/pentest/skills/pentest/references/fix-template.md", "03-story-image-dos.md"),
    ("plugins/pentest/skills/pentest/references/fix-template.md", "04-security-headers.md"),
}

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "build"}


def tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return [p for p in out.stdout.split("\0") if p]


def roots_for(rel: str) -> list[Path]:
    parts = Path(rel).parts
    roots = [ROOT]
    if len(parts) >= 2 and parts[0] == "plugins":
        roots.append(ROOT / parts[0] / parts[1])
    if len(parts) >= 3 and parts[0] == "plugins" and parts[2] == "skills" and len(parts) >= 4:
        roots.append(ROOT / parts[0] / parts[1] / parts[2] / parts[3])
    return roots


def resolves(token: str, file_dir: Path, extra_roots: list[Path]) -> bool:
    token = token.rstrip(".,;:")
    candidates = [file_dir / token, *[root / token for root in extra_roots]]
    return any(c.exists() for c in candidates)


def check_file(rel: str) -> list[str]:
    path = ROOT / rel
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    file_dir = path.parent
    extra_roots = roots_for(rel)
    findings: list[str] = []

    for match in MD_LINK_RE.finditer(text):
        target = match.group(1).split("#", 1)[0].split("?", 1)[0]
        if not target or target in IGNORE_TARGETS or target.startswith(IGNORE_PREFIXES) or "%" in target:
            continue
        if not (file_dir / target).exists() and (rel, target) not in ALLOWLIST:
            findings.append(f"{rel}: dangling markdown link -> {target}")

    for span in CODE_SPAN_RE.finditer(text):
        span_text = span.group(1)
        for tok in PATH_TOKEN_RE.finditer(span_text):
            token = tok.group(1)
            if not token.startswith(REPO_ROOTS):
                continue
            if token.startswith(IGNORE_PREFIXES):
                continue
            if resolves(token, file_dir, extra_roots):
                continue
            if (rel, token) in ALLOWLIST:
                continue
            findings.append(f"{rel}: dangling path reference -> {token}")

    return findings


def main() -> int:
    findings: list[str] = []
    for rel in tracked_markdown():
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        findings.extend(check_file(rel))
    if findings:
        for finding in sorted(set(findings)):
            print(f"FAIL: {finding}", file=sys.stderr)
        return 1
    print("OK: all doc references resolve inside the repo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
