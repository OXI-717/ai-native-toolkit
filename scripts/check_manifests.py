#!/usr/bin/env python3
"""Marketplace integrity check for ai-native-toolkit.

Validates that .claude-plugin/marketplace.json and per-plugin manifests are
mutually consistent: every listed plugin exists on disk, every plugin dir is
listed, and names/versions agree. Run from the repository root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path.relative_to(ROOT)}: unreadable JSON: {exc}")


def main() -> int:
    marketplace_path = ROOT / ".claude-plugin" / "marketplace.json"
    if not marketplace_path.is_file():
        fail("missing .claude-plugin/marketplace.json")
    marketplace = load_json(marketplace_path)
    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or not entries:
        fail("marketplace.json: plugins must be a non-empty list")

    listed_dirs: set[str] = set()
    errors: list[str] = []
    for entry in entries:
        name = entry.get("name")
        source = entry.get("source")
        version = entry.get("version")
        if not name or not source or not version:
            errors.append(f"marketplace entry missing name/source/version: {entry!r}")
            continue
        plugin_dir = (ROOT / source).resolve()
        try:
            rel_dir = plugin_dir.relative_to(ROOT)
        except ValueError:
            errors.append(f"{name}: source escapes repo root: {source}")
            continue
        if rel_dir.parts[:1] != ("plugins",):
            errors.append(f"{name}: source outside plugins/: {source}")
            continue
        if name in listed_dirs:
            errors.append(f"{name}: duplicate marketplace entry")
        listed_dirs.add(name)
        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.is_file():
            errors.append(f"{name}: missing {rel_dir}/plugin.json")
            continue
        manifest = load_json(manifest_path)
        if manifest.get("name") != name:
            errors.append(f"{name}: plugin.json name is {manifest.get('name')!r}")
        if manifest.get("version") != version:
            errors.append(
                f"{name}: plugin.json version {manifest.get('version')!r} != marketplace {version!r}"
            )
        codex_manifest = plugin_dir / ".codex-plugin" / "plugin.json"
        if codex_manifest.is_file():
            codex = load_json(codex_manifest)
            if codex.get("version") != version:
                errors.append(
                    f"{name}: .codex-plugin version {codex.get('version')!r} != marketplace {version!r}"
                )
        skill_dirs = list((plugin_dir / "skills").glob("*/SKILL.md")) if (plugin_dir / "skills").is_dir() else []
        if not skill_dirs:
            errors.append(f"{name}: no skills/*/SKILL.md found")

    plugins_root = ROOT / "plugins"
    for plugin_dir in sorted(plugins_root.iterdir()):
        if plugin_dir.is_dir() and plugin_dir.name not in listed_dirs:
            errors.append(f"{plugin_dir.name}: plugin dir has no marketplace entry")

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"OK: {len(listed_dirs)} plugins consistent with marketplace.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
