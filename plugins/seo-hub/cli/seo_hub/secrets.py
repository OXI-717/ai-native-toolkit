from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from seo_hub.errors import SeoHubError


class SecretFileError(SeoHubError, ValueError):
    code = "SECRET_FILE_INVALID"


def _ensure_safe_file(path: Path, allowed_root: Path) -> Path:
    if path.is_symlink():
        raise SecretFileError(f"secret env file must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SecretFileError(f"secret env file not found: {path}") from exc
    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError as exc:
        raise SecretFileError(f"secret env file escapes allowed root: {path}") from exc
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise SecretFileError(f"secret env file must be a regular file: {path}")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise SecretFileError(f"secret env file must have mode 0600: {path}")
    return resolved


def _parse_env_text(text: str, path: Path) -> dict[str, str]:
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SecretFileError(f"invalid JSON secret env file {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise SecretFileError(f"JSON secret env file must contain an object: {path}")
        return {str(key): str(value) for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}

    parsed: dict[str, str] = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise SecretFileError(f"invalid env assignment in {path}:{line_no}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            raise SecretFileError(f"invalid env name in {path}:{line_no}: {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        parsed[key] = value
    return parsed


def load_secret_env(
    path: str | Path,
    *,
    allowed_names: set[str] | frozenset[str],
    allowed_root: str | Path,
    target_env: dict[str, str] | None = None,
) -> dict[str, str]:
    secret_path = _ensure_safe_file(Path(path).expanduser(), Path(allowed_root).expanduser())
    parsed = _parse_env_text(secret_path.read_text(encoding="utf-8"), secret_path)
    loaded = {key: value for key, value in parsed.items() if key in allowed_names}
    if target_env is not None:
        target_env.update(loaded)
    return loaded


def redact_text(text: str, values: Mapping[str, str]) -> str:
    redacted = text
    for value in sorted(set(values.values()), key=len, reverse=True):
        if value:
            redacted = redacted.replace(value, "<redacted>")
    return redacted


def redact_json(payload: Any, values: Mapping[str, str]) -> Any:
    if isinstance(payload, str):
        return redact_text(payload, values)
    if isinstance(payload, list):
        return [redact_json(item, values) for item in payload]
    if isinstance(payload, dict):
        return {key: redact_json(value, values) for key, value in payload.items()}
    return payload
