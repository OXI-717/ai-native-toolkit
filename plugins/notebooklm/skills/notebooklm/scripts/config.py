"""
Configuration for NotebookLM Skill.

Keep runtime state outside the plugin directory so the skill works from a repo
checkout, a Claude install, and a Codex install/cache.
"""

import os
from pathlib import Path


def resolve_runtime_root() -> Path:
    override = os.getenv("NOTEBOOKLM_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "notebooklm"


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
