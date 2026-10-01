"""
Configuration for NotebookLM Skill.

Keep runtime state outside the plugin directory so the skill works from a repo
checkout, a Claude install, and a Codex install/cache.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:              # dotenv is a runtime dep; without it .env is simply ignored
    load_dotenv = None


def resolve_runtime_root() -> Path:
    override = os.getenv("NOTEBOOKLM_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "notebooklm"


def _load_env_file() -> None:
    """Read the optional `.env`, preferring the runtime root over the skill directory.

    The skill lives in a plugin cache that is replaced on every update (and is
    read-only in the canonical checkout), so the runtime root is the place a user
    can actually keep settings. The skill directory stays supported for the layout
    documented earlier. Real environment variables always win over the file.
    """
    if load_dotenv is None:
        return
    for candidate in (resolve_runtime_root() / ".env", Path(__file__).parent.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


_load_env_file()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default          # a typo in .env must not crash the skill


# Paths
SKILL_DIR = Path(__file__).parent.parent
RUNTIME_ROOT = resolve_runtime_root()
DATA_DIR = Path(os.getenv("NOTEBOOKLM_DATA_DIR", str(RUNTIME_ROOT / "data"))).expanduser()
VENV_DIR = Path(os.getenv("NOTEBOOKLM_VENV_DIR", str(RUNTIME_ROOT / ".venv"))).expanduser()
BROWSER_STATE_DIR = DATA_DIR / "browser_state"
BROWSER_PROFILE_DIR = BROWSER_STATE_DIR / "browser_profile"
STATE_FILE = BROWSER_STATE_DIR / "state.json"
AUTH_INFO_FILE = DATA_DIR / "auth_info.json"
LIBRARY_FILE = DATA_DIR / "library.json"

# NotebookLM Selectors
QUERY_INPUT_SELECTORS = [
    "textarea.query-box-input",  # Primary
    'textarea[aria-label="Feld für Anfragen"]',  # Fallback German
    'textarea[aria-label="Input for queries"]',  # Fallback English
]

RESPONSE_SELECTORS = [
    ".to-user-container .message-text-content",  # Primary
    "[data-message-author='bot']",
    "[data-message-author='assistant']",
]

# Browser Configuration
BROWSER_ARGS = [
    '--disable-blink-features=AutomationControlled',  # Patches navigator.webdriver
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--no-first-run',
    '--no-default-browser-check'
]

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

# Timeouts
LOGIN_TIMEOUT_MINUTES = 10
QUERY_TIMEOUT_SECONDS = 120
PAGE_LOAD_TIMEOUT = 30000

# Optional settings (`.env` or real environment variables)
# HEADLESS=false and SHOW_BROWSER=true both mean "show the window"; the
# `--show-browser` flag still overrides whatever is configured here.
SHOW_BROWSER = _env_flag("SHOW_BROWSER", False) or not _env_flag("HEADLESS", True)
STEALTH_ENABLED = _env_flag("STEALTH_ENABLED", True)
TYPING_WPM_MIN = _env_int("TYPING_WPM_MIN", 320)
TYPING_WPM_MAX = _env_int("TYPING_WPM_MAX", 480)
DEFAULT_NOTEBOOK_ID = (os.getenv("DEFAULT_NOTEBOOK_ID") or "").strip() or None
