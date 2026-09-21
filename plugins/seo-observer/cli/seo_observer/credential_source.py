"""Declared source of a project's credentials.

Before this existed, the only thing tying a project to its secrets was tribal
knowledge. Three live projects had three different arrangements - a flat
`.env.seo` at the repo root, that same file plus a shell wrapper that derives a
token from an OAuth JSON, and a `.secrets/seo.env` inside a directory holding
forty such files - and nothing in the config said which. Working on a project
began with an archaeology session.

The standard is therefore not a fixed path. Each repository keeps its own
storage layout, and the config declares where the credentials come from::

    [credentials]
    env_file = "../.env.seo"                    # loaded by the CLI
    loader = "scripts/seo-observer-env.sh"      # named, never executed

The asymmetry between the two is deliberate.

`env_file` is data: a flat list of KEY=VALUE lines, so the CLI can read it. It
never overrides a variable that is already set - an explicitly exported value
wins over the file, which keeps one-off overrides working and makes the file a
default rather than an authority.

`loader` is code. A config file is not a trust boundary: it travels in a PR, is
edited by agents, and is read from directories the CLI merely walks into. So a
declared loader is only ever *reported* - `doctor` prints the command for a
human to run. If executing it were ever added, `project.toml` would become a way
to run arbitrary shell from anything that loads a config.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CredentialSource:
    """Where a project's credentials come from, as declared by its config."""

    env_file: Path | None = None
    loader: Path | None = None

    @property
    def declared(self) -> bool:
        return self.env_file is not None or self.loader is not None


class DotenvError(ValueError):
    """The declared env file exists but cannot be read as KEY=VALUE lines."""


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse the KEY=VALUE subset that env files in this repo actually use.

    Deliberately not a shell parser: this reads data, it does not evaluate it.
    Substitution, command expansion and multi-line values are unsupported, and a
    file needing them belongs behind `loader` instead.
    """

    values: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            raise DotenvError(f"line {number} is not KEY=VALUE: {raw_line!r}")
        key = key.strip()
        if not key or not all(char.isalnum() or char == "_" for char in key):
            raise DotenvError(f"line {number} has an invalid variable name: {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DotenvError(f"{path} could not be read: {exc}") from exc
    return parse_dotenv(text)


def apply_credential_source(source: CredentialSource, env: dict[str, str]) -> dict[str, Any]:
    """Fill in variables the declared env file provides and `env` lacks.

    Returns a report rather than raising on a missing file: a project may
    legitimately have its variables exported already, and refusing to run in
    that case would make the declaration a liability instead of a convenience.
    """

    report: dict[str, Any] = {
        "declared": source.declared,
        "env_file": str(source.env_file) if source.env_file else None,
        "loader": str(source.loader) if source.loader else None,
        "env_file_exists": None,
        "loaded": [],
        "already_set": [],
        "error": None,
    }
    if source.env_file is None:
        return report
    report["env_file_exists"] = source.env_file.exists()
    if not report["env_file_exists"]:
        return report
    try:
        values = load_env_file(source.env_file)
    except DotenvError as exc:
        report["error"] = str(exc)
        return report
    for key, value in values.items():
        if env.get(key):
            report["already_set"].append(key)
            continue
        env[key] = value
        report["loaded"].append(key)
    return report


def doctor_credential_source(
    source: CredentialSource,
    *,
    required_env: list[str],
    env: dict[str, str],
) -> dict[str, Any]:
    """Report the credential source and name the command for what is missing."""

    missing = sorted({name for name in required_env if not env.get(name)})
    check: dict[str, Any] = {
        "ok": not missing,
        "declared": source.declared,
        "env_file": str(source.env_file) if source.env_file else None,
        "loader": str(source.loader) if source.loader else None,
        "missing_env": missing,
        "hint": None,
    }
    if source.env_file is not None and not source.env_file.exists():
        check["ok"] = False
        check["hint"] = f"declared env_file does not exist: {source.env_file}"
        return check
    if missing:
        if source.loader is not None:
            # Named, not run: see the module docstring on why a config may not
            # execute code.
            check["hint"] = f"source {source.loader}"
        elif source.env_file is not None:
            check["hint"] = f"add the missing variables to {source.env_file}"
        else:
            check["hint"] = (
                "no [credentials] section: declare env_file or loader so the "
                "source of these variables is discoverable"
            )
    return check
