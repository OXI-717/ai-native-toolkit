#!/usr/bin/env python3
"""Render a supercronic crontab template with environment substitution.

Substitutes ``${NAME}`` placeholders from the process environment plus the
derived ``SEO_PANEL_URL_FLAG`` (``--panel-url <SEO_PANEL_URL>`` when
``SEO_PANEL_URL`` is set, otherwise empty). An unknown variable aborts the
render with the variable name; no literal ``${`` survives into the output.

supercronic runs every command through ``/bin/sh -c``, so every substituted
value is emitted with ``shlex.quote`` — a value with spaces or shell
metacharacters stays a single argument. Values containing a newline,
carriage return or NUL are rejected (a crontab line break would inject a
new command); the error names the variable, never the value. ``TZ`` is not
shell-interpreted (it lands on the ``CRON_TZ=`` line), so it is validated
as an IANA name and emitted bare. ``SEO_PANEL_URL_FLAG`` is a pre-quoted
composite fragment and is emitted verbatim.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Already shell-quoted composite fragments, not single values.
_RAW_VARS = frozenset({"SEO_PANEL_URL_FLAG"})
_UNSAFE = ("\n", "\r", "\0")


class UnknownVariableError(Exception):
    """A template placeholder has no matching environment variable."""


class UnsafeValueError(Exception):
    """A value cannot be safely embedded in a crontab line."""


def _checked(name: str, value: str) -> str:
    if any(c in value for c in _UNSAFE):
        raise UnsafeValueError(name)
    return value


def render(template: str, env: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in env:
            raise UnknownVariableError(name)
        value = _checked(name, env[name])
        return value if name in _RAW_VARS else shlex.quote(value)

    return _PLACEHOLDER.sub(replace, template)


def build_env(environ: dict[str, str]) -> dict[str, str]:
    env = dict(environ)
    tz = environ.get("TZ")
    if tz is not None:
        try:
            ZoneInfo(_checked("TZ", tz))
        except (KeyError, ValueError) as exc:
            raise UnsafeValueError("TZ") from exc
    panel_url = _checked("SEO_PANEL_URL", environ.get("SEO_PANEL_URL", ""))
    env["SEO_PANEL_URL_FLAG"] = f"--panel-url {shlex.quote(panel_url)}" if panel_url else ""
    return env


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: render_crontab.py <template> <output>", file=sys.stderr)
        return 64
    template_path, output_path = Path(argv[1]), Path(argv[2])
    template = template_path.read_text(encoding="utf-8")
    try:
        rendered = render(template, build_env(os.environ))
    except UnknownVariableError as exc:
        print(f"crontab template references unknown variable: {exc}", file=sys.stderr)
        return 64
    except UnsafeValueError as exc:
        print(f"environment variable {exc} cannot be embedded in a crontab line", file=sys.stderr)
        return 64
    if "${" in rendered:
        print("crontab render left a literal ${ behind; refusing", file=sys.stderr)
        return 64
    output_path.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
