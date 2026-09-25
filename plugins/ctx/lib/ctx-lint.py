#!/usr/bin/env python3
"""Validate @-imports and key_people across all ctx AGENTS.md files.

Usage:
  ctx-lint.py                   # auto-scope check (human output)
  ctx-lint.py <path>            # check one specific project
  ctx-lint.py --all             # force-check every discovered project
  ctx-lint.py --here            # check only the project enclosing CWD
  ctx-lint.py --json            # machine-readable JSON output
  ctx-lint.py --fix             # apply safe auto-fixes
  ctx-lint.py --list-projects   # list discovered project paths, one per line
  ctx-lint.py --bootstrap       # regenerate ~/.ctx/config.json and exit

Flags can combine: ctx-lint.py ~/projects --json --fix

Scope resolution (no explicit path, no --all/--here):
  * CWD is inside a discovered project → only that project
  * CWD is ancestor of one or more projects → those projects
  * otherwise → all discovered projects
The chosen scope is reported in both human and JSON output.

Config (~/.ctx/config.json) is auto-generated on first run. It lists the
scan roots (dirs with `.gh-account`) and owned namespaces (git orgs whose
repos are treated as "ours"). Edit `owned_namespaces` to prune false positives
(public repos you happen to have 3+ clones of), or populate `extra_roots` /
`extra_namespaces` / `excluded_paths` — those survive `--bootstrap`.
"""
import os
import re
import sys
import json
import difflib
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from people_matcher import find_person_cards


LIB = Path(__file__).parent
# Плагин лежит на два уровня выше `lib/` — резолв от файла верен на любой машине.
# Раньше здесь стоял путь по личной раскладке `$HOME`, и вне её плагин не находил
# собственные шаблоны.
DEFAULT_PLUGIN_ROOT = LIB.parent
PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or DEFAULT_PLUGIN_ROOT)
PARSE_FM_SCRIPT = LIB / "parse-frontmatter.py"

CACHE_DIR = Path.home() / ".ctx"
CACHE_FILE = CACHE_DIR / "projects.json"
CONFIG_FILE = CACHE_DIR / "config.json"
CLAUDE_JSON = Path.home() / ".claude.json"

# Auto-learn threshold: a namespace is considered "owned" once at least this
# many repos from it have been cloned under user-marked scan_roots. Low enough
# to capture small client orgs (3+ repos); false positives (e.g. public clones
# you happen to have 3 of) are expected — user prunes `owned_namespaces` in
# `~/.ctx/config.json` as needed.
NAMESPACE_LEARN_THRESHOLD = 3

# Directory names to prune during any recursive scan — build outputs, caches,
# VCS internals. These would otherwise hide legitimate projects behind noise.
EXCLUDE_DIR_NAMES = {
    ".git", ".hg", ".svn",
    "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", "target", ".next", ".nuxt", ".turbo",
    "vendor", "site-packages",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    ".cache", "coverage", ".coverage",
}

# Top-level `$HOME` children skipped during bootstrap scan — macOS system
# folders, third-party apps, media mounts. Nothing user-managed lives here.
HOME_SKIP_CHILDREN = {
    "Library", "Applications", "Applications (Parallels)",
    "Downloads", "Desktop", "Documents", "Pictures", "Music", "Movies",
    "Public", "Dropbox", "Tresors",
    "Parallels", "VirtualBox VMs", "Virtual Machines.localized",
    "Yandex.Disk.localized", "Wondershare",
}

def vault_people_dir():
    """Каталог карточек людей в Obsidian-vault.

    Путь берётся из `VAULT_PATH` — так же, как в остальных модулях плагина
    (`ctx-meetings.py`, `ctx-people.py`, `ctx-research.py`). Раньше здесь стоял
    абсолютный путь домашней директории конкретной машины: у любого другого
    пользователя проверка `key_people` молча не находила ни одной карточки и
    выдавала предупреждение на каждое имя.

    Если переменная не задана, vault считается недоступным, и проверка карточек
    пропускается — это осознанная деградация: линтер полезен и без vault, а
    выдумывать чужую раскладку он не должен.
    """
    root = os.environ.get("VAULT_PATH")
    return Path(root).expanduser() / _vault_layout()["people_dir"] if root else None


def _vault_layout() -> dict:
    """Vault folder names come from the plugin's optional
    `config/vault-layout.json`, not from code: a published linter must not
    carry one person's vault layout. Missing/broken config → neutral defaults
    (a warning, not a crash — the linter stays useful without a vault)."""
    layout = {"people_dir": "People", "volatile_segments": ["/meetings/"]}
    cfg = LIB.parent / "config" / "vault-layout.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ctx-lint: {cfg} unreadable ({type(exc).__name__}), using defaults", file=sys.stderr)
            return layout
        if isinstance(data.get("people_dir"), str) and data["people_dir"].strip():
            layout["people_dir"] = data["people_dir"].strip()
        segs = data.get("volatile_segments")
        if isinstance(segs, list) and all(isinstance(x, str) for x in segs):
            layout["volatile_segments"] = segs
    return layout

START_MARKER = "<!-- AUTO-INSERTED BY session-start hook"
END_MARKER = "<!-- END AUTO-INSERTED -->"

VALID_STRATEGIES = {"alpha", "orchestrator", "symlink-consumer"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _load_cache():
    if not CACHE_FILE.exists():
        return []
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return list(data.get("projects", []))
    except (json.JSONDecodeError, OSError):
        return []


def _save_cache(projects):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".json.tmp")
        payload = json.dumps({"projects": projects}, ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass  # cache is advisory


_REMOTE_NAMESPACE_RE = re.compile(
    r"(?:github|bitbucket|gitlab)[^/:]*[/:]([^/]+)/", re.IGNORECASE
)


def _extract_remote_namespace(path: Path):
    """Read `git remote get-url origin` and return the org/user segment,
    lowercased. None if not a git repo, no origin, or URL unparseable."""
    if not (path / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    if not url:
        return None
    m = _REMOTE_NAMESPACE_RE.search(url)
    if not m:
        return None
    return m.group(1).lower()


def _has_gh_account_ancestor(path: Path) -> bool:
    """True if `path` or any ancestor contains a `.gh-account` marker file."""
    current = path
    while True:
        if (current / ".gh-account").exists():
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _enclosing_git_repo(path: Path):
    """Return the nearest ancestor (or self) containing `.git`, or None."""
    current = path
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


# ---------------------------------------------------------------------------
# Config (scan_roots + owned_namespaces)
# ---------------------------------------------------------------------------

def _bootstrap_config():
    """One-time auto-discovery of scan roots and owned namespaces.

    Walks `$HOME` up to depth 3 looking for `.gh-account` marker files. Each
    containing directory becomes a scan root. The file contents are collected
    as initial owned namespaces (typical values: your GitHub user and org names).

    Then sweeps every scan root (up to depth 3) counting git-remote namespaces.
    Any namespace that shows up ≥ `NAMESPACE_LEARN_THRESHOLD` times is added
    to `owned_namespaces` — this is how 3rd-party client orgs (ExampleCorp,
    sub-orgs, etc.) become trusted without hard-coding.

    Returns a config dict; does NOT persist it.
    """
    home = Path.home()
    scan_roots = []
    namespaces = set()

    def _walk(d: Path, depth: int, max_depth: int, on_dir):
        if depth > max_depth:
            return
        on_dir(d, depth)
        try:
            children = list(d.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_symlink():
                continue  # never follow symlinks (e.g. vault mounts)
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if child.name in EXCLUDE_DIR_NAMES:
                continue
            if depth == 0 and child.name in HOME_SKIP_CHILDREN:
                continue
            _walk(child, depth + 1, max_depth, on_dir)

    def _collect_gh_account(d: Path, depth: int):
        gh = d / ".gh-account"
        if gh.exists():
            try:
                content = gh.read_text(encoding="utf-8").strip()
            except OSError:
                content = ""
            if content:
                namespaces.add(content.lower())
            scan_roots.append(str(d))

    _walk(home, 0, 3, _collect_gh_account)

    # Auto-learn namespaces from repos already cloned under scan roots.
    ns_counts = {}

    def _collect_remote(d: Path, depth: int):
        if depth == 0:
            return  # skip scan_root itself; we want children and below
        ns = _extract_remote_namespace(d)
        if ns:
            ns_counts[ns] = ns_counts.get(ns, 0) + 1

    for root in scan_roots:
        rp = Path(root)
        if not rp.exists():
            continue
        _walk(rp, 0, 3, _collect_remote)

    for ns, count in ns_counts.items():
        if count >= NAMESPACE_LEARN_THRESHOLD:
            namespaces.add(ns)

    return {
        "_about": (
            "Auto-generated by ctx. `scan_roots` + `owned_namespaces` are "
            "regenerated by `ctx-lint --bootstrap` (or if this file is deleted). "
            "Edit `owned_namespaces` to prune false positives — or add to "
            "`extra_namespaces` / `extra_roots` / `excluded_paths` (those "
            "survive re-bootstrap)."
        ),
        "scan_roots": sorted(set(scan_roots)),
        "owned_namespaces": sorted(namespaces),
        "extra_roots": [],
        "extra_namespaces": [],
        "excluded_paths": [],
    }


def _load_config_file():
    if not CONFIG_FILE.exists():
        return None
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_config_file(data):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, CONFIG_FILE)
    except OSError:
        pass


def get_config():
    """Return active config, creating and persisting a bootstrap one if absent.

    Config schema (all keys optional in user-provided file):
      {
        "scan_roots":       ["/abs/path", ...],    # where to look for projects
        "owned_namespaces": ["your-org", ...],     # git orgs considered ours
        "extra_roots":      ["/abs/path", ...],    # appended to scan_roots
        "extra_namespaces": ["examplecorp", ...], # appended to owned_namespaces
        "excluded_paths":   ["/abs/path", ...]     # never treated as projects
      }

    `scan_roots`/`owned_namespaces` are auto-populated on first run; user may
    edit ~/.ctx/config.json freely afterwards. `extra_*` is the safe way
    to add entries without losing them on bootstrap regeneration.
    """
    cfg = _load_config_file()
    if cfg is None:
        cfg = _bootstrap_config()
        _save_config_file(cfg)
    # Merge extras
    roots = set(cfg.get("scan_roots") or [])
    roots.update(cfg.get("extra_roots") or [])
    ns = {n.lower() for n in (cfg.get("owned_namespaces") or [])}
    ns.update(n.lower() for n in (cfg.get("extra_namespaces") or []))
    excluded = {str(Path(p).expanduser().resolve()) for p in (cfg.get("excluded_paths") or [])}
    return {
        "scan_roots": sorted(roots),
        "owned_namespaces": ns,
        "excluded_paths": excluded,
    }


def _is_owned(path: Path, owned_namespaces) -> bool:
    """Per-path ownership decision.

    Rules (in order):
      1. Enclosing git repo → its `origin` remote decides. If its namespace is
         in `owned_namespaces` → owned. If the remote exists but namespace is
         unknown → explicit reject (foreign clone, e.g. `openai/openai-agents-
         python` sitting under `~/projects/`).
      2. No enclosing git repo, or origin unreadable → fall back to the
         nearest `.gh-account` ancestor marker as a "user-managed area" hint.
    """
    git_root = _enclosing_git_repo(path)
    if git_root is not None:
        ns = _extract_remote_namespace(git_root)
        if ns is not None:
            return ns in owned_namespaces
        # git repo but no readable origin → treat as unreadable, fall through
    return _has_gh_account_ancestor(path)


def _is_sub_container(path: Path) -> bool:
    """A directory that hosts multiple project dirs but isn't a project itself
    (or is both, like `projects/team/app/`). We detect it via ≥2 immediate children
    with AGENTS.md.
    """
    count = 0
    try:
        for child in path.iterdir():
            if child.is_symlink():
                continue
            if not child.is_dir() or child.name.startswith(".") or child.name in EXCLUDE_DIR_NAMES:
                continue
            if (child / "AGENTS.md").exists():
                count += 1
                if count >= 2:
                    return True
    except OSError:
        pass
    return False


def _scan_configured_roots(cfg):
    """Find owned AGENTS.md projects under configured scan roots.

    Strategy: depth-1 under each root, plus depth-1 under any child that looks
    like a sub-container (auto-detected via `_is_sub_container`). No deep
    recursion — that surfaced too many inner-module AGENTS.md (src/, shared/).
    """
    owned_ns = cfg["owned_namespaces"]
    excluded = cfg["excluded_paths"]
    found = []
    seen = set()

    def _add_if_project(p: Path):
        resolved = p.resolve()
        if str(resolved) in excluded or resolved in seen:
            return
        if (p / "AGENTS.md").exists() and _is_owned(p, owned_ns):
            found.append(p)
            seen.add(resolved)

    stack = []
    for root in cfg["scan_roots"]:
        rp = Path(root)
        if rp.exists():
            stack.append((rp, True))  # (dir, is_root)

    while stack:
        current, is_root = stack.pop()
        if is_root:
            _add_if_project(current)
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_symlink():
                continue
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name in EXCLUDE_DIR_NAMES:
                continue
            _add_if_project(child)
            # Descend into sub-containers to find their projects
            if _is_sub_container(child):
                stack.append((child, False))
    return found


def _scan_claude_json(cfg):
    if not CLAUDE_JSON.exists():
        return []
    try:
        data = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    projects = data.get("projects", {}) or {}
    found = []
    for path, meta in projects.items():
        if not isinstance(meta, dict):
            continue
        if not meta.get("hasClaudeMdExternalIncludesApproved"):
            continue
        p = Path(path)
        resolved = p.resolve()
        if str(resolved) in cfg["excluded_paths"]:
            continue
        if (p / "AGENTS.md").exists() and _is_owned(p, cfg["owned_namespaces"]):
            found.append(p)
    return found


def discover_projects(explicit=None):
    if explicit is not None:
        p = Path(explicit).expanduser().resolve()
        if (p / "AGENTS.md").exists():
            return [p]
        return []

    cfg = get_config()
    merged = set()
    # Cached paths are re-validated — remote may have changed, repo may have
    # been moved out of an owned area, etc.
    for cached in _load_cache():
        cp = Path(cached)
        if not (cp / "AGENTS.md").exists():
            continue
        if str(cp.resolve()) in cfg["excluded_paths"]:
            continue
        if _is_owned(cp, cfg["owned_namespaces"]):
            merged.add(str(cp))
    for p in _scan_configured_roots(cfg):
        merged.add(str(p))
    for p in _scan_claude_json(cfg):
        merged.add(str(p))

    alive = sorted({str(Path(s).resolve()) for s in merged})
    _save_cache(alive)
    return [Path(p) for p in alive]


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------

def _resolve_cwd() -> Path:
    try:
        return Path(os.getcwd()).resolve()
    except (OSError, FileNotFoundError):
        return Path.home().resolve()


def _find_enclosing_project(cwd: Path, projects):
    """Return the deepest project whose path equals or is an ancestor of CWD."""
    cwd_parts = cwd.parts
    best = None
    best_len = -1
    for p in projects:
        p_parts = p.resolve().parts
        if len(p_parts) > len(cwd_parts):
            continue
        if cwd_parts[: len(p_parts)] == p_parts and len(p_parts) > best_len:
            best = p
            best_len = len(p_parts)
    return best


def _projects_under(cwd: Path, projects):
    """Projects strictly below CWD (CWD acts as container)."""
    cwd_parts = cwd.parts
    out = []
    for p in projects:
        p_parts = p.resolve().parts
        if len(p_parts) <= len(cwd_parts):
            continue
        if p_parts[: len(cwd_parts)] == cwd_parts:
            out.append(p)
    return out


def resolve_scope(all_projects, mode, cwd=None):
    """Filter `all_projects` per scope mode.

    mode: 'auto' | 'all' | 'here'
    Returns (scope_info_dict, filtered_projects).
    """
    cwd = cwd or _resolve_cwd()
    cwd_str = str(cwd)

    if mode == "all":
        return (
            {"requested": "all", "kind": "all",
             "cwd": cwd_str, "scope_path": None,
             "project_count": len(all_projects)},
            list(all_projects),
        )

    if mode == "here":
        encl = _find_enclosing_project(cwd, all_projects)
        if encl is None:
            return (
                {"requested": "here", "kind": "none",
                 "cwd": cwd_str, "scope_path": None,
                 "project_count": 0,
                 "reason": "CWD is not inside any known project"},
                [],
            )
        return (
            {"requested": "here", "kind": "project",
             "cwd": cwd_str, "scope_path": str(encl.resolve()),
             "project_count": 1},
            [encl],
        )

    # auto: union of enclosing (if any) + all projects strictly under CWD.
    # Covers nested container-like layouts such as `projects/team/app/` (itself a
    # project, with 11 sub-projects inside) — both root and children are checked.
    encl = _find_enclosing_project(cwd, all_projects)
    under = _projects_under(cwd, all_projects)

    selected = []
    seen = set()
    if encl is not None:
        selected.append(encl)
        seen.add(str(encl.resolve()))
    for p in under:
        key = str(p.resolve())
        if key not in seen:
            selected.append(p)
            seen.add(key)

    if not selected:
        return (
            {"requested": "auto", "kind": "all",
             "cwd": cwd_str, "scope_path": None,
             "project_count": len(all_projects),
             "reason": "CWD is not inside or above any known project"},
            list(all_projects),
        )

    if len(selected) == 1:
        return (
            {"requested": "auto", "kind": "project",
             "cwd": cwd_str, "scope_path": str(selected[0].resolve()),
             "project_count": 1},
            selected,
        )

    # Multiple projects — container-like scope. Prefer the enclosing path as
    # the scope anchor when present (e.g. `~/projects/team/app` rather than same),
    # otherwise the CWD itself serves as the container root.
    scope_path = str(encl.resolve()) if encl is not None else cwd_str
    return (
        {"requested": "auto", "kind": "container",
         "cwd": cwd_str, "scope_path": scope_path,
         "project_count": len(selected),
         "includes_enclosing": encl is not None},
        selected,
    )


# ---------------------------------------------------------------------------
# Frontmatter helper
# ---------------------------------------------------------------------------

def parse_frontmatter(agents_path: Path) -> dict:
    """Parse AGENTS.md frontmatter via the sibling helper script.

    A subprocess/parse FAILURE (missing interpreter dependency, crash, timeout,
    malformed JSON on stdout) is reported as ``{"_parse_error": "<message>"}`` —
    distinct from a *successful* parse that legitimately found no frontmatter
    (``{}``). Collapsing both into the same empty dict made every project look
    like a plain "no load_strategy field" foreign AGENTS.md whenever the helper
    itself couldn't run (e.g. pyyaml missing for whatever `python3` resolves to)
    — a silent false "unmanaged, nothing to check" verdict across the whole
    fleet instead of a visible error (issue 2208).
    """
    try:
        r = subprocess.run(
            ["python3", str(PARSE_FM_SCRIPT), str(agents_path)],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"_parse_error": f"{PARSE_FM_SCRIPT.name} timed out after 10s"}
    except OSError as e:
        return {"_parse_error": f"failed to run {PARSE_FM_SCRIPT.name}: {e}"}
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        try:
            structured = json.loads(detail)
            if isinstance(structured, dict) and "_error" in structured:
                detail = str(structured["_error"])
        except (json.JSONDecodeError, TypeError):
            pass
        return {"_parse_error": detail or f"{PARSE_FM_SCRIPT.name} exited {r.returncode}"}
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError as e:
        return {"_parse_error": f"{PARSE_FM_SCRIPT.name} produced invalid JSON: {e}"}


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------

def resolve_import(raw: str, repo_path: Path) -> Path:
    """Resolve an @-import path string into an absolute Path."""
    s = raw.strip()
    if s.startswith("${CLAUDE_PLUGIN_ROOT}"):
        rest = s[len("${CLAUDE_PLUGIN_ROOT}"):].lstrip("/")
        return (PLUGIN_ROOT / rest).resolve() if rest else PLUGIN_ROOT.resolve()
    if s.startswith("~"):
        return Path(s).expanduser().resolve()
    if s.startswith("/"):
        return Path(s).resolve()
    if s.startswith("./"):
        return (repo_path / s[2:]).resolve()
    # Bare relative path (e.g. "AGENTS.md" or a file under the rules directory)
    return (repo_path / s).resolve()


def find_suggestion(target: Path):
    """Try to find a close match for a missing file. Returns (suggest_name, confidence, ratio)."""
    parent = target.parent
    if not parent.exists():
        return None, "none", 0.0
    try:
        candidates = [p.name for p in parent.iterdir() if p.is_file()]
    except OSError:
        return None, "none", 0.0
    matches = difflib.get_close_matches(target.name, candidates, n=3, cutoff=0.6)
    if not matches:
        return None, "none", 0.0
    best = matches[0]
    ratio = difflib.SequenceMatcher(None, target.name, best).ratio()
    if ratio >= 0.85:
        conf = "high"
    elif ratio >= 0.6:
        conf = "low"
    else:
        conf = "none"
    return best, conf, ratio


def parse_imports(agents_path: Path):
    """Return list of (line_number, raw_rest, full_line) tuples for @-imports.

    raw_rest is the path part after '@'. full_line is the stripped line.
    """
    results = []
    try:
        with agents_path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped.startswith("@"):
                    continue
                rest = stripped[1:].strip()
                if not rest:
                    continue
                results.append((lineno, rest, stripped))
    except OSError:
        pass
    return results


# ---------------------------------------------------------------------------
# Key people check
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Context budget
# ---------------------------------------------------------------------------
#
# Claude Code inlines the whole @-import tree of CLAUDE.md -> AGENTS.md into the
# system prompt at session start AND again after every compact. A single fat
# import (a vault person card that grows with meeting logs) can eat the window
# before the first user message: after compact it refills at once and
# autocompact thrashes. Claude Code warns per file only above 40k chars and
# never reports the tree total. These checks make both visible at lint time
# and at SessionStart (the hook runs ctx-lint --json).

CONTEXT_BUDGET_DEFAULTS = {
    "total_tokens": 40000,
    "per_file_chars": 40000,
    # Own text of AGENTS.md (no frontmatter, @-lines, auto-zone): it is an index.
    "agents_body_chars": 6000,
}
# Claude Code loads only the head of the auto-memory index.
MEMORY_INDEX_MAX_LINES = 200
MEMORY_INDEX_MAX_BYTES = 25000
MAX_IMPORT_TREE_FILES = 500
MAX_IMPORT_DEPTH = 5
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def estimate_tokens(text: str) -> int:
    """Rough token count without a tokenizer.

    Latin text is ~4 UTF-8 bytes per token, Cyrillic ~2.6; blend by the share
    of non-ASCII bytes. Good enough for a budget warning, not for billing.
    """
    data = text.encode("utf-8")
    if not data:
        return 0
    non_ascii = sum(1 for b in data if b >= 0x80) / len(data)
    return int(len(data) / (4.0 - 1.4 * non_ascii))


def _classify_lines(text: str):
    """Yield (line, raw_import_or_None), skipping @-lines inside code fences."""
    fence_char = None
    fence_length = 0
    for line in text.splitlines():
        marker = _FENCE_RE.match(line)
        if marker and fence_char is None:
            fence_char = marker.group(1)[0]
            fence_length = len(marker.group(1))
            yield line, None
            continue
        if fence_char is not None:
            stripped = line.strip()
            if stripped.startswith(fence_char * fence_length):
                remainder = stripped.lstrip(fence_char)
                if not remainder and len(stripped) >= fence_length:
                    fence_char = None
                    fence_length = 0
            yield line, None
            continue
        stripped = line.strip()
        if stripped.startswith("@") and len(stripped) > 1:
            yield line, stripped[1:].split()[0]
        else:
            yield line, None


def _import_targets(text: str):
    """Yield raw @-import paths at line start, skipping fenced code blocks."""
    for _line, raw in _classify_lines(text):
        if raw is not None:
            yield raw


def walk_import_tree(root: Path, *, return_truncated: bool = False):
    """Return every file inlined from ``root`` via recursive @-imports.

    Paths resolve relative to the importing file (as Claude Code does), each
    file is counted once, missing targets are skipped (reported elsewhere).
    """
    seen = set()
    order = []
    stack = [(root.resolve(), 0, None)]
    while stack and len(order) < MAX_IMPORT_TREE_FILES:
        path, depth, parent = stack.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        targets = list(_import_targets(text))
        # Claude Code does not expand ${...} in @-imports, so such an import is
        # never loaded even though resolve_import() can find it.
        env_var = [raw for raw in targets if "${" in raw]
        order.append({
            "path": str(path),
            "chars": len(text),
            "tokens": estimate_tokens(text),
            "depth": depth,
            "parent": parent,
            "env_var_imports": env_var,
        })
        children = []
        for raw in targets:
            if raw in env_var:
                continue
            try:
                children.append(resolve_import(raw, path.parent))
            except (OSError, RuntimeError, ValueError):
                # Symlink loop or unresolvable path: skip, never crash the hook.
                continue
        if depth < MAX_IMPORT_DEPTH:
            for child in reversed(children):
                stack.append((child, depth + 1, str(path)))
    return (order, bool(stack)) if return_truncated else order


def _is_volatile(path: str) -> bool:
    people = vault_people_dir()
    if people is not None:
        try:
            Path(path).relative_to(people.parent.resolve())
            return True
        except ValueError:
            pass
    return any(seg in path for seg in _vault_layout()["volatile_segments"])


def claude_memory_index(repo_path: Path) -> Path:
    """Path of Claude Code's auto-memory index for this repo (may not exist)."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(repo_path.resolve()))
    return base / "projects" / slug / "memory" / "MEMORY.md"


def _budget_limits(fm: dict):
    """Merge frontmatter ``context_budget`` over defaults; return (limits, issue)."""
    limits = dict(CONTEXT_BUDGET_DEFAULTS)
    raw = fm.get("context_budget")
    if raw is None:
        return limits, None
    ok = isinstance(raw, dict)
    if ok:
        for key, value in raw.items():
            if (key not in limits or isinstance(value, bool)
                    or not isinstance(value, int) or value <= 0):
                ok = False
                break
            limits[key] = value
    if ok:
        return limits, None
    return dict(CONTEXT_BUDGET_DEFAULTS), {
        "type": "invalid_context_budget",
        "severity": "warn",
        "description": (
            "frontmatter `context_budget` must map "
            f"{sorted(CONTEXT_BUDGET_DEFAULTS)} to positive ints; defaults used"
        ),
        "raw": repr(raw),
        "confidence": "none",
    }


def _short(path: str) -> str:
    return path.replace(str(Path.home()), "~")


def _agents_body_sections(text: str):
    """Return (body_chars, [(heading, chars)]) of AGENTS.md's own text.

    Frontmatter, @-import lines (classified exactly as the import walker does)
    and the auto-inserted zone (matched by substring, as the hook does) are
    excluded: they are counted elsewhere or regenerated by the hook.
    """
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                lines = lines[i + 1:]
                break
    kept, in_auto = [], False
    for line, raw in _classify_lines("\n".join(lines)):
        if START_MARKER in line:
            in_auto = True
        if in_auto:
            if END_MARKER in line:
                in_auto = False
            continue
        if raw is None:
            kept.append(line)
    sections, heading = {}, "(preamble)"
    for line in kept:
        if line.startswith("## "):
            heading = line[3:].strip()
        sections[heading] = sections.get(heading, 0) + len(line) + 1
    return sum(len(line) + 1 for line in kept), sorted(sections.items(), key=lambda kv: -kv[1])


def _claude_loads_agents(claude: Path, agents: Path) -> bool:
    """True if CLAUDE.md imports AGENTS.md outside HTML comments and fences."""
    try:
        text = strip_html_comments(claude.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    expected = agents.resolve()
    for raw in _import_targets(text):
        try:
            if resolve_import(raw, claude.parent) == expected:
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def check_context_budget(agents: Path, repo_path: Path, fm: dict):
    """Return (issues, budget_info) for the startup context of this project."""
    issues = []
    limits, bad = _budget_limits(fm)
    if bad:
        issues.append(bad)

    claude = repo_path / "CLAUDE.md"
    files, truncated = walk_import_tree(claude, return_truncated=True)
    if truncated:
        issues.append({
            "type": "context_budget_incomplete",
            "severity": "warn",
            "description": (
                f"startup import tree exceeds {MAX_IMPORT_TREE_FILES} files; "
                "budget is a lower bound"
            ),
            "confidence": "none",
        })
    for f in files:
        if f["chars"] > limits["per_file_chars"]:
            issues.append({
                "type": "oversized_import",
                "severity": "warn",
                "path": f["path"],
                "description": (
                    f"{_short(f['path'])}: {f['chars'] / 1000:.1f}k chars "
                    f"(~{f['tokens'] / 1000:.1f}k tokens) > "
                    f"{limits['per_file_chars'] / 1000:.0f}k-char limit; inlined at "
                    "every session start and after every compact"
                ),
                "confidence": "none",
            })
        # AGENTS.md imports are already reported as env_var_in_import.
        for raw in f["env_var_imports"] if Path(f["path"]) != agents.resolve() else ():
            issues.append({
                "type": "nested_env_var_import",
                "severity": "warn",
                "path": f["path"],
                "raw": raw,
                "description": (
                    f"@{raw} in {_short(f['path'])}: Claude Code does not expand "
                    "${...} in @-imports, the file is never loaded; "
                    "vendor it into the repo or use a relative/absolute path"
                ),
                "confidence": "none",
            })
        if f["depth"] > 0 and _is_volatile(f["path"]):
            issues.append({
                "type": "volatile_import",
                "severity": "warn",
                "path": f["path"],
                "description": (
                    f"{_short(f['path'])}: vault/meeting file grows over time; "
                    "summarise it in AGENTS.md and read it on demand instead"
                ),
                "confidence": "none",
            })

    if _claude_loads_agents(claude, agents):
        try:
            body_chars, sections = _agents_body_sections(
                agents.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            body_chars, sections = 0, []
        if body_chars > limits["agents_body_chars"]:
            heaviest = ", ".join(f"«{h}» {c / 1000:.1f}k" for h, c in sections[:3])
            issues.append({
                "type": "agents_md_inline_heavy",
                "severity": "warn",
                "path": str(agents),
                "description": (
                    f"AGENTS.md own text {body_chars / 1000:.1f}k chars > "
                    f"{limits['agents_body_chars'] / 1000:.0f}k: keep it an index of links; "
                    "move sections to reference/<topic>.md (read on demand) or rules/*.md "
                    f"(if needed every session), trigger tables to SKILL.md; heaviest: {heaviest}"
                ),
                "confidence": "none",
            })

    memory = claude_memory_index(repo_path)
    memory_tokens = 0
    if memory.is_file():
        try:
            mtext = memory.read_text(encoding="utf-8", errors="replace")
        except OSError:
            mtext = ""
        loaded_lines = "".join(mtext.splitlines(keepends=True)[:MEMORY_INDEX_MAX_LINES])
        loaded_bytes = loaded_lines.encode("utf-8")[:MEMORY_INDEX_MAX_BYTES]
        memory_tokens = estimate_tokens(loaded_bytes.decode("utf-8", errors="ignore"))
        lines = len(mtext.splitlines())
        size = len(mtext.encode("utf-8"))
        if lines > MEMORY_INDEX_MAX_LINES or size > MEMORY_INDEX_MAX_BYTES:
            issues.append({
                "type": "memory_index_truncated",
                "severity": "warn",
                "path": str(memory),
                "description": (
                    f"MEMORY.md {lines} lines / {size / 1000:.1f} KB > "
                    f"{MEMORY_INDEX_MAX_LINES} lines / {MEMORY_INDEX_MAX_BYTES / 1000:.0f} KB: "
                    "Claude Code loads only the head, entries below are invisible"
                ),
                "confidence": "none",
            })

    total_tokens = sum(f["tokens"] for f in files) + memory_tokens
    top = sorted(files, key=lambda f: -f["tokens"])[:5]
    if total_tokens > limits["total_tokens"]:
        heaviest = ", ".join(
            f"{Path(f['path']).name} ~{f['tokens'] / 1000:.1f}k" for f in top[:3]
        )
        issues.append({
            "type": "context_budget_exceeded",
            "severity": "warn",
            "description": (
                f"startup context ~{total_tokens / 1000:.1f}k tokens across "
                f"{len(files)} files > budget {limits['total_tokens'] / 1000:.0f}k; "
                f"heaviest: {heaviest}"
            ),
            "confidence": "none",
        })

    info = {
        "files": len(files),
        "chars": sum(f["chars"] for f in files),
        "tokens": total_tokens,
        "memory_tokens": memory_tokens,
        "limits": limits,
        "complete": not truncated,
        "top": [{"path": f["path"], "tokens": f["tokens"], "chars": f["chars"]} for f in top],
    }
    return issues, info


def check_key_people(key_people):
    issues = []
    for name in key_people or []:
        if not isinstance(name, str) or not name.strip():
            continue
        people_dir = vault_people_dir()
        if people_dir is None:
            continue
        matches, glob_label = find_person_cards(name, people_dir)
        if len(matches) == 0:
            issues.append({
                "type": "key_people_missing_card",
                "severity": "warn",
                "name": name,
                "searched_glob": glob_label,
                "suggest_fix": None,
            })
        elif len(matches) >= 2:
            issues.append({
                "type": "key_people_ambiguous",
                "severity": "warn",
                "name": name,
                "candidates": [m.name for m in matches],
            })
    return issues


# ---------------------------------------------------------------------------
# Project check
# ---------------------------------------------------------------------------

def _check_symlink_consumer(repo_path: Path, report: dict) -> bool:
    """Validate the `_ecosystem` link for a symlink-consumer project.

    Returns True if the link resolves (directly or via a symlink to an
    existing target), False if a blocking issue was recorded. The caller
    uses the return value to decide whether the orchestrator @-import
    check is meaningful (skipped when link is missing/broken to avoid
    duplicate noise).
    """
    link = repo_path / "_ecosystem"
    if link.is_symlink():
        # Resolve manually so we can distinguish "broken symlink" cleanly.
        try:
            raw_target = os.readlink(str(link))
        except OSError as e:
            report["issues"].append({
                "type": "broken_ecosystem_link",
                "severity": "error",
                "description": f"cannot read _ecosystem symlink: {e}",
                "suggest_fix": None,
            })
            return False
        target = Path(raw_target)
        if not target.is_absolute():
            target = (link.parent / target).resolve(strict=False)
        if not target.exists():
            report["issues"].append({
                "type": "broken_ecosystem_link",
                "severity": "error",
                "target_path": str(target),
                "description": (
                    f"_ecosystem symlink target does not exist: {target}. "
                    "Clone the orchestrator repo at that path "
                    "(e.g. `git clone <orchestrator-repo-url> _ecosystem` from the parent dir)."
                ),
                "suggest_fix": None,
            })
            return False
        return True
    if link.is_dir():
        return True  # plain directory — allowed by design
    if not link.exists():
        report["issues"].append({
            "type": "missing_ecosystem_link",
            "severity": "error",
            "description": (
                "load_strategy: symlink-consumer requires `_ecosystem/` "
                "(symlink or directory) at the project root. Create a symlink "
                "pointing to the sibling-cloned orchestrator repo, "
                "e.g. `ln -s ../_ecosystem _ecosystem`."
            ),
            "suggest_fix": None,
        })
        return False
    report["issues"].append({
        "type": "missing_ecosystem_link",
        "severity": "error",
        "description": f"_ecosystem exists but is neither a symlink nor a directory: {link}",
        "suggest_fix": None,
    })
    return False


def _consumer_has_orchestrator_import(imports, repo_path: Path) -> bool:
    """True if any @-import resolves to `<repo>/_ecosystem/AGENTS.md`.

    Independent of the exact literal path (./_ecosystem/..., _ecosystem/...,
    absolute path, or anything else that resolves to the same file).
    """
    expected = (repo_path / "_ecosystem" / "AGENTS.md").resolve(strict=False)
    for _lineno, rest, _full in imports:
        if "${" in rest:
            continue  # env-var imports are silently ignored anyway
        try:
            target = resolve_import(rest, repo_path)
        except (OSError, ValueError):
            continue
        if target == expected:
            return True
    return False


# `.*?` up to the first `-->`, or to end of input when there is none.
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


def strip_html_comments(text: str) -> str:
    """Remove comments without joining tokens or losing line boundaries.

    A comment is replaced by a space plus its own newlines, never by nothing:
    dropping it outright would glue the text on either side together, and
    `@AGE<!-- x -->NTS.md` would then read as a valid `@AGENTS.md` import.

    Unterminated comments hide text through EOF only at a Markdown block start
    (up to three leading spaces). Unclosed inline openers remain visible.

    Deliberately not fence-aware: comments inside code fences are stripped too,
    and an unterminated one there hides the rest of the file. Parsing fences is
    not worth it for a file that should hold a single import line, and the
    failure mode is a missed warning — never a false one.
    """
    def replace_comment(match: re.Match) -> str:
        comment = match.group()
        if not comment.endswith("-->"):
            line_start = text.rfind("\n", 0, match.start()) + 1
            if not re.fullmatch(r" {0,3}", text[line_start:match.start()]):
                return comment
        return " " + "".join(re.findall(r"[\r\n]", comment))

    return HTML_COMMENT_RE.sub(replace_comment, text)


def check_project(repo_path: Path) -> dict:
    agents = repo_path / "AGENTS.md"
    report = {
        "path": str(repo_path),
        "project": None,
        "status": "ok",
        "info": {
            "import_count": 0,
            "key_people_count": 0,
            "has_end_marker": True,
        },
        "issues": [],
    }
    try:
        if not agents.exists():
            report["status"] = "error"
            report["issues"].append({
                "type": "missing_agents_md",
                "severity": "error",
                "description": f"{agents} does not exist",
            })
            return report

        fm = parse_frontmatter(agents)
        report["project"] = fm.get("project")

        # CLAUDE.md thin-wrapper check — applies to ALL projects with AGENTS.md,
        # regardless of whether they follow the ctx frontmatter schema.
        # For cross-agent portability, CLAUDE.md should be a thin `@AGENTS.md`
        # wrapper so Claude Code and other agents (Codex, Gemini, Cursor, Aider)
        # all read unified rules from AGENTS.md.
        claude_md = repo_path / "CLAUDE.md"
        if claude_md.exists():
            try:
                claude_content = claude_md.read_text(encoding="utf-8")
            except OSError:
                claude_content = ""
            meaningful = [
                ln.strip()
                for ln in strip_html_comments(claude_content).splitlines()
                if ln.strip()
            ]
            has_agents_import = any(ln == "@AGENTS.md" for ln in meaningful)
            extras = [ln for ln in meaningful if ln != "@AGENTS.md"]
            if not has_agents_import:
                report["issues"].append({
                    "type": "claude_md_missing_agents_import",
                    "severity": "warn",
                    "description": (
                        "CLAUDE.md does not import AGENTS.md (`@AGENTS.md`). "
                        "Non-Claude agents (Codex/Gemini/Cursor) read AGENTS.md; "
                        "a thin CLAUDE.md wrapper keeps rules unified across agents."
                    ),
                    "suggest_fix": "replace CLAUDE.md content with single line: @AGENTS.md",
                })
            elif extras:
                report["issues"].append({
                    "type": "claude_md_not_thin_wrapper",
                    "severity": "warn",
                    "description": (
                        f"CLAUDE.md has {len(extras)} content line(s) beyond "
                        "`@AGENTS.md`. Move any project-specific rules into "
                        "AGENTS.md so non-Claude agents see the same context; "
                        "keep CLAUDE.md as a thin `@AGENTS.md` wrapper."
                    ),
                    "suggest_fix": "replace CLAUDE.md content with single line: @AGENTS.md",
                })

        # A parser FAILURE is not the same signal as "genuinely no load_strategy
        # field" — surface it loudly (error) instead of silently falling into the
        # unmanaged branch below, which would make a broken parser look like a
        # clean fleet-wide report (issue 2208).
        if "_parse_error" in fm:
            report["status"] = "error"
            report["project"] = report["project"] or "(frontmatter unreadable)"
            report["issues"].append({
                "type": "frontmatter_parse_error",
                "severity": "error",
                "description": (
                    f"Could not parse AGENTS.md frontmatter: {fm['_parse_error']}. "
                    "Import/key_people/load_strategy checks were skipped for this "
                    "project — this is NOT the same as a clean 'unmanaged' verdict."
                ),
                "suggest_fix": None,
            })
            return report

        # Detect a foreign AGENTS.md: no `load_strategy` field is the canonical marker.
        # These files are typically auto-generated by other plugins (e.g. bk's generate-context)
        # or hand-written without our frontmatter schema. We skip them from further validation
        # to avoid noise (but the CLAUDE.md wrapper check above already ran — that applies
        # universally). --list-projects still lists them so user knows they exist.
        is_managed = "load_strategy" in fm
        if not is_managed:
            # Startup-context size matters to every Claude Code session, managed
            # or not — run the budget check here too, like the wrapper check.
            budget_issues, budget_info = check_context_budget(agents, repo_path, fm)
            report["issues"].extend(budget_issues)
            report["info"]["budget"] = budget_info
            has_issues = bool(report["issues"])
            if has_issues:
                report["status"] = "warn"
            else:
                report["status"] = "unmanaged"
            report["project"] = report["project"] or "(not ctx managed)"
            report["info"]["unmanaged_reason"] = (
                "AGENTS.md has no `load_strategy` field — not ctx managed, skipping import/key_people checks"
            )
            return report

        strategy = fm.get("load_strategy")
        report["info"]["strategy"] = strategy
        if strategy not in VALID_STRATEGIES:
            report["issues"].append({
                "type": "unknown_load_strategy",
                "severity": "warn",
                "description": (
                    f"load_strategy: {strategy!r} is not recognised "
                    f"(expected one of: {', '.join(sorted(VALID_STRATEGIES))}). "
                    "Treating as `alpha` for validation."
                ),
                "suggest_fix": None,
            })

        # Strategy-specific: verify `_ecosystem/` link BEFORE parsing imports,
        # so we can suppress duplicate broken_import noise when link is broken.
        ecosystem_link_ok = True
        if strategy == "symlink-consumer":
            ecosystem_link_ok = _check_symlink_consumer(repo_path, report)

        try:
            content = agents.read_text(encoding="utf-8")
        except OSError as e:
            report["status"] = "error"
            report["issues"].append({
                "type": "read_error",
                "severity": "error",
                "description": f"Cannot read AGENTS.md: {e}",
            })
            return report

        # End marker check
        has_start = START_MARKER in content
        has_end = END_MARKER in content
        report["info"]["has_end_marker"] = (not has_start) or has_end
        if has_start and not has_end:
            report["issues"].append({
                "type": "missing_end_marker",
                "severity": "warn",
                "description": "AGENTS.md has start marker but no <!-- END AUTO-INSERTED -->",
                "suggest_fix": "auto",
            })

        # Imports
        imports = parse_imports(agents)
        count_imports = 0
        for lineno, rest, _full in imports:
            # Skip self-reference special case (AGENTS.md import from CLAUDE.md style)
            if rest == "AGENTS.md":
                continue
            count_imports += 1

            # Claude Code does NOT expand ${VAR} in project-level @-imports —
            # such imports are silently ignored at runtime. Previously ctx-lint
            # simulated the substitution (PLUGIN_ROOT default) and reported OK,
            # masking the failure. Flag these as errors regardless of whether
            # the "resolved" file exists on disk.
            if "${" in rest:
                report["issues"].append({
                    "type": "env_var_in_import",
                    "severity": "error",
                    "line": lineno,
                    "raw": f"@{rest}",
                    "description": (
                        "${...} is not expanded in project-level @-imports; "
                        "Claude Code silently ignores this line. "
                        "Replace with a relative path (@./rules/foo.md) or an absolute path."
                    ),
                    "suggest_fix": None,
                    "confidence": "none",
                })
                continue

            target = resolve_import(rest, repo_path)
            if target.exists():
                continue
            # Suppress duplicate noise for consumer projects whose broken
            # _ecosystem link already failed above — all `_ecosystem/*`
            # imports would otherwise be flagged as broken_import even though
            # the root cause is the single link issue.
            normalized_rest = rest.lstrip("./")
            if (
                strategy == "symlink-consumer"
                and not ecosystem_link_ok
                and normalized_rest.startswith("_ecosystem/")
            ):
                continue
            suggest_name, confidence, _ratio = find_suggestion(target)
            suggest_fix = None
            if suggest_name:
                # Build suggested @-import line, preserving the original "shape".
                suggest_fix = _rebuild_import_line(rest, target, suggest_name, repo_path)
            report["issues"].append({
                "type": "broken_import",
                "severity": "error",
                "line": lineno,
                "raw": f"@{rest}",
                "resolved": str(target),
                "suggest_fix": suggest_fix,
                "confidence": confidence,
            })
        report["info"]["import_count"] = count_imports

        # Consumer-only: require @./_ecosystem/AGENTS.md import
        if strategy == "symlink-consumer" and not _consumer_has_orchestrator_import(imports, repo_path):
            report["issues"].append({
                "type": "consumer_missing_orchestrator_import",
                "severity": "error",
                "description": (
                    "load_strategy: symlink-consumer requires an @-import of "
                    "./_ecosystem/AGENTS.md (the orchestrator entrypoint). Not found."
                ),
                "suggest_fix": "add line: @./_ecosystem/AGENTS.md",
            })

        # key_people — optional for `orchestrator` and `symlink-consumer` by
        # design (orchestrator has no project-level people, consumers defer
        # to hub/orchestrator). For `alpha` it's also optional — check runs
        # only when key_people is declared non-empty.
        kp = fm.get("key_people") or []
        if isinstance(kp, list):
            report["info"]["key_people_count"] = len(kp)
            report["issues"].extend(check_key_people(kp))

        budget_issues, budget_info = check_context_budget(agents, repo_path, fm)
        report["issues"].extend(budget_issues)
        report["info"]["budget"] = budget_info

        # Final status
        severities = {i.get("severity") for i in report["issues"]}
        if "error" in severities:
            report["status"] = "error"
        elif "warn" in severities:
            report["status"] = "warn"
        else:
            report["status"] = "ok"

    except Exception as e:
        report["status"] = "error"
        report["issues"].append({
            "type": "check_crashed",
            "severity": "error",
            "description": f"check_project crashed: {type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        })
    return report


def _rebuild_import_line(rest: str, target: Path, suggest_name: str, repo_path: Path) -> str:
    """Rebuild an @-import line with the replacement file name, preserving the
    original prefix shape (${CLAUDE_PLUGIN_ROOT}/..., ./..., /abs, ~/..., bare)."""
    # Figure out what directory-part of the original `rest` we need to keep
    # by replacing the last path segment.
    if "/" in rest:
        head, _tail = rest.rsplit("/", 1)
        return f"@{head}/{suggest_name}"
    # Bare filename (no slashes) — suggest a plain name
    return f"@{suggest_name}"


# ---------------------------------------------------------------------------
# Fix application
# ---------------------------------------------------------------------------

def apply_fixes(report: dict) -> dict:
    """Apply safe auto-fixes and return the mutated report with a fixed_issues list."""
    fixed = []
    path = Path(report["path"])
    agents = path / "AGENTS.md"
    if not agents.exists():
        report["fixed_issues"] = fixed
        return report

    try:
        original = agents.read_text(encoding="utf-8")
    except OSError:
        report["fixed_issues"] = fixed
        return report

    lines = original.splitlines(keepends=True)
    modified = False

    for issue in report.get("issues", []):
        if issue.get("_fixed"):
            continue
        itype = issue.get("type")

        if itype == "broken_import" and issue.get("confidence") == "high" and issue.get("suggest_fix"):
            lineno = issue.get("line")
            if not isinstance(lineno, int) or lineno < 1 or lineno > len(lines):
                continue
            orig_line = lines[lineno - 1]
            # preserve leading whitespace + trailing newline
            m = re.match(r"^(\s*)(.*?)(\r?\n?)$", orig_line, re.DOTALL)
            leading, middle, eol = m.group(1), m.group(2), m.group(3)
            if middle.strip() != issue.get("raw", "").strip():
                # Line content drifted — skip to be safe
                continue
            nl = eol or "\n"
            new_line = f"{leading}{issue['suggest_fix']}{nl}"
            lines[lineno - 1] = new_line
            issue["_fixed"] = True
            modified = True
            fixed.append(
                f"rewrote line {lineno}: {issue['raw']} -> {issue['suggest_fix']}"
            )

        elif itype == "missing_end_marker":
            # Append marker at end of file; ensure trailing newline before it
            if END_MARKER not in "".join(lines):
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] = lines[-1] + "\n"
                lines.append(f"{END_MARKER}\n")
                issue["_fixed"] = True
                modified = True
                fixed.append("appended <!-- END AUTO-INSERTED --> marker")

    if modified:
        try:
            tmp = agents.with_suffix(".md.tmp")
            tmp.write_text("".join(lines), encoding="utf-8")
            os.replace(tmp, agents)
        except OSError as e:
            fixed.append(f"write failed: {e}")

    report["fixed_issues"] = fixed

    # Recompute status after fixes (fixed issues no longer count)
    remaining = [i for i in report["issues"] if not i.get("_fixed")]
    severities = {i.get("severity") for i in remaining}
    if "error" in severities:
        report["status"] = "error"
    elif "warn" in severities:
        report["status"] = "warn"
    else:
        report["status"] = "ok"
    return report


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

STATUS_EMOJI = {"ok": "✅", "warn": "⚠", "error": "❌", "unmanaged": "➖"}


def _format_scope_header(scope):
    if not scope:
        return None
    kind = scope.get("kind")
    req = scope.get("requested")
    cwd = scope.get("cwd")
    path = scope.get("scope_path")
    count = scope.get("project_count", 0)
    if kind == "explicit":
        return f"Scope: explicit path → {path}"
    if kind == "project":
        label = "auto: enclosing project" if req == "auto" else "--here: enclosing project"
        return f"Scope: {label} → {path}  (cwd={cwd})"
    if kind == "container":
        label = "container+self" if scope.get("includes_enclosing") else "container"
        return f"Scope: auto: {label} → {path}  ({count} projects, cwd={cwd})"
    if kind == "all":
        note = "  (forced)" if req == "all" else ""
        reason = scope.get("reason")
        tail = f"  — {reason}" if reason else ""
        return f"Scope: all discovered{note}  ({count} projects, cwd={cwd}){tail}"
    if kind == "none":
        reason = scope.get("reason", "no projects match scope")
        return f"Scope: empty — {reason}  (cwd={cwd})"
    return None


def format_human(reports, scope=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = [f"# ctx-lint: {now}"]
    header = _format_scope_header(scope)
    if header:
        out.append(header)
    out.append("")
    total_errors = 0
    total_warnings = 0
    total_unmanaged = 0
    for r in reports:
        path = r.get("path", "?")
        project = r.get("project") or "?"
        status = r.get("status", "ok")
        emoji = STATUS_EMOJI.get(status, "?")
        out.append(f"## {path} ({project}) {emoji}")

        # Short-circuit for unmanaged projects — just note and skip
        if status == "unmanaged":
            reason = r.get("info", {}).get("unmanaged_reason", "not ctx managed")
            out.append(f"  ➖ {reason}")
            out.append("")
            total_unmanaged += 1
            continue

        info = r.get("info", {})
        import_count = info.get("import_count", 0)
        kp_count = info.get("key_people_count", 0)
        strategy = info.get("strategy")

        issues = r.get("issues", [])
        broken = [i for i in issues if i.get("type") == "broken_import"]
        env_var = [i for i in issues if i.get("type") == "env_var_in_import"]
        kp_missing = [i for i in issues if i.get("type") == "key_people_missing_card"]
        kp_ambig = [i for i in issues if i.get("type") == "key_people_ambiguous"]
        missing_end = [i for i in issues if i.get("type") == "missing_end_marker"]
        ecosystem_issues = [
            i for i in issues
            if i.get("type") in (
                "missing_ecosystem_link",
                "broken_ecosystem_link",
                "consumer_missing_orchestrator_import",
                "unknown_load_strategy",
            )
        ]
        claude_wrapper = [
            i for i in issues
            if i.get("type") in (
                "claude_md_missing_agents_import",
                "claude_md_not_thin_wrapper",
            )
        ]
        other = [
            i for i in issues
            if i.get("type") not in (
                "broken_import", "env_var_in_import",
                "key_people_missing_card",
                "key_people_ambiguous", "missing_end_marker",
                "missing_ecosystem_link", "broken_ecosystem_link",
                "consumer_missing_orchestrator_import", "unknown_load_strategy",
                "claude_md_missing_agents_import",
                "claude_md_not_thin_wrapper",
            )
        ]

        if strategy and strategy != "alpha":
            out.append(f"  strategy: {strategy}")

        # Imports
        bad_imports = broken + env_var
        if import_count == 0:
            out.append("  0 @-imports")
        elif not bad_imports:
            out.append(f"  {import_count} @-imports OK")
        else:
            ok_imports = import_count - len(bad_imports)
            out.append(f"  {ok_imports}/{import_count} @-imports OK, {len(bad_imports)} issue(s):")
            for i in env_var:
                out.append(f"    ❌ line {i.get('line')}: {i.get('raw')}")
                out.append(f"       → ${{...}} not expanded in project-level @-import (silently ignored at runtime)")
            for i in broken:
                out.append(f"    ❌ line {i.get('line')}: {i.get('raw')}")
                out.append(f"       → file missing: {i.get('resolved')}")
                conf = i.get("confidence", "none")
                sug = i.get("suggest_fix")
                if sug:
                    out.append(f"       → suggestion ({conf} confidence): {sug}")
                elif conf == "none":
                    out.append("       → no close match found")
                if i.get("_fixed"):
                    out.append("       ✅ auto-fixed: rewrote line")

        # key_people
        if kp_count == 0:
            out.append("  0 key_people")
        else:
            found = kp_count - len(kp_missing) - len(kp_ambig)
            if not kp_missing and not kp_ambig:
                out.append(f"  {kp_count} key_people: {kp_count} cards found")
            else:
                parts = [f"{found} cards found"]
                if kp_missing:
                    parts.append(f"{len(kp_missing)} missing")
                if kp_ambig:
                    parts.append(f"{len(kp_ambig)} ambiguous")
                out.append(f"  {kp_count} key_people: " + ", ".join(parts) + ":")
                for i in kp_missing:
                    out.append(
                        f'    ⚠ "{i.get("name")}" — no card matching {i.get("searched_glob")}'
                    )
                for i in kp_ambig:
                    cands = ", ".join(i.get("candidates", []))
                    out.append(
                        f'    ⚠ "{i.get("name")}" — ambiguous match: {cands}'
                    )

        # End marker
        if missing_end:
            msg = "  end marker: missing — will be added by next session-start"
            if missing_end[0].get("_fixed"):
                msg += " ✅ auto-fixed: appended marker"
            out.append(msg)
        else:
            out.append("  end marker: present")

        # Ecosystem-link / consumer issues
        for i in ecosystem_issues:
            sev_emoji = STATUS_EMOJI.get(i.get("severity"), "?")
            itype = i.get("type")
            out.append(f"  {sev_emoji} {itype}: {i.get('description', '')}")
            if itype == "broken_ecosystem_link" and i.get("target_path"):
                out.append(f"     target: {i['target_path']}")
            sug = i.get("suggest_fix")
            if sug:
                out.append(f"     → {sug}")

        budget = info.get("budget")
        if budget:
            out.append(
                f"  startup context: ~{budget['tokens'] / 1000:.1f}k tokens, "
                f"{budget['files']} files (budget {budget['limits']['total_tokens'] / 1000:.0f}k)"
            )

        # CLAUDE.md wrapper
        for i in claude_wrapper:
            out.append(f"  ⚠ CLAUDE.md: {i.get('description', '')}")
            sug = i.get("suggest_fix")
            if sug:
                out.append(f"     → {sug}")

        for i in other:
            out.append(f"  {STATUS_EMOJI.get(i.get('severity'), '?')} {i.get('type')}: {i.get('description', '')}")

        out.append("")

        if status == "error":
            total_errors += 1
        elif status == "warn":
            total_warnings += 1

    out.append("---")
    summary_parts = [f"{len(reports)} projects checked"]
    if total_unmanaged:
        summary_parts.append(f"{total_unmanaged} unmanaged (skipped)")
    summary_parts.append(f"{total_errors} error")
    summary_parts.append(f"{total_warnings} warnings")
    out.append("Summary: " + ", ".join(summary_parts))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    json_mode = "--json" in argv
    fix_mode = "--fix" in argv
    list_mode = "--list-projects" in argv
    bootstrap_mode = "--bootstrap" in argv
    all_flag = "--all" in argv
    here_flag = "--here" in argv
    # Filter flags out, whatever remains is the optional explicit path
    rest = [a for a in argv if not a.startswith("--")]
    explicit = rest[0] if rest else None

    if bootstrap_mode:
        cfg = _bootstrap_config()
        _save_config_file(cfg)
        print(f"ctx-lint: regenerated {CONFIG_FILE}")
        print(f"  scan_roots: {len(cfg['scan_roots'])}")
        print(f"  owned_namespaces: {', '.join(cfg['owned_namespaces']) or '(none)'}")
        return 0

    if all_flag and here_flag:
        print("ctx-lint: --all and --here are mutually exclusive", file=sys.stderr)
        return 2
    if explicit is not None and (all_flag or here_flag):
        print("ctx-lint: explicit path cannot be combined with --all or --here",
              file=sys.stderr)
        return 2

    try:
        projects = discover_projects(explicit)
    except Exception as e:
        print(f"ctx-lint: discovery crashed: {e}", file=sys.stderr)
        return 2

    # Resolve scope (no filtering when explicit path or --list-projects)
    if explicit is not None:
        scope = {"requested": "explicit", "kind": "explicit",
                 "cwd": str(_resolve_cwd()),
                 "scope_path": str(Path(explicit).expanduser().resolve()),
                 "project_count": len(projects)}
    elif list_mode:
        # list-projects reports raw discovery; no scope filtering applied
        scope = {"requested": "list", "kind": "all",
                 "cwd": str(_resolve_cwd()), "scope_path": None,
                 "project_count": len(projects)}
    else:
        mode = "all" if all_flag else ("here" if here_flag else "auto")
        scope, projects = resolve_scope(projects, mode)

    if list_mode:
        for p in projects:
            print(str(p))
        return 0

    if not projects:
        if json_mode:
            print(json.dumps({"scope": scope, "reports": []},
                             indent=2, ensure_ascii=False))
        else:
            header = _format_scope_header(scope)
            if header:
                print(header)
            print("ctx-lint: no projects with AGENTS.md to check")
        return 0

    reports = []
    pre_errors = 0
    for p in projects:
        r = check_project(p)
        if r.get("status") == "error":
            pre_errors += 1
        if fix_mode:
            r = apply_fixes(r)
        reports.append(r)

    if json_mode:
        print(json.dumps({"scope": scope, "reports": reports},
                         indent=2, ensure_ascii=False))
    else:
        print(format_human(reports, scope=scope))

    # Exit code
    remaining_errors = sum(1 for r in reports if r.get("status") == "error")
    if pre_errors > 0 and not fix_mode:
        return 1
    if remaining_errors > 0:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"ctx-lint: uncaught exception: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(2)
