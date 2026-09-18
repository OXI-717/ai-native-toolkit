from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seo_hub.errors import SeoHubError
from seo_hub.registry import Project, ProjectExport, Registry
from seo_hub.run_models import RunManifest
from seo_hub.store import RunStore


class ExportError(SeoHubError, ValueError):
    code = "EXPORT_FAILED"


@dataclass(frozen=True)
class ExportResult:
    path: Path
    format: str
    profile: str

    def to_json(self) -> dict[str, str]:
        return {"path": str(self.path), "format": self.format, "profile": self.profile}


def export_run(
    *,
    registry: Registry,
    project: Project,
    manifest: RunManifest,
    format_name: str,
    profile_name: str | None = None,
    report: dict[str, Any] | None = None,
) -> ExportResult:
    profile = _select_profile(project, format_name, profile_name)
    target_dir = _profile_dir(registry, profile)
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = {"markdown": "md", "json": "json", "observer-actions": "actions.json"}.get(format_name, "json")
    path = target_dir / f"{project.id}-{manifest.run_id}.{suffix}"
    if format_name == "json":
        _atomic_write(path, json.dumps({"run": manifest.to_json(), "report": report or {}}, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    elif format_name == "markdown":
        _atomic_write(path, _markdown_export(project=project, manifest=manifest, report=report or {}))
    elif format_name == "observer-actions":
        _atomic_write(path, json.dumps(_observer_action_drafts(project=project, manifest=manifest, report=report or {}), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    else:
        raise ExportError(f"unsupported export format: {format_name}", format=format_name)
    return ExportResult(path=path, format=format_name, profile=profile.name)


def latest_report_from_manifest(store: RunStore, manifest: RunManifest) -> dict[str, Any]:
    report_dir = store.run_dir(manifest.project_id, manifest.run_id) / "observer"
    for name in ("composite-extract.json", "report.json", "manifest.json"):
        path = report_dir / name
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
    markdown_path = report_dir / "report.md"
    if markdown_path.exists():
        try:
            return {"summary": markdown_path.read_text(encoding="utf-8")}
        except OSError:
            pass
    return {}


def _select_profile(project: Project, format_name: str, profile_name: str | None) -> ProjectExport:
    matches = [
        item
        for item in project.exports
        if item.format == format_name and (profile_name is None or item.name == profile_name)
    ]
    if not matches:
        raise ExportError(
            "configured export profile was not found for project and format",
            project=project.id,
            format=format_name,
            profile=profile_name,
        )
    return matches[0]


def _profile_dir(registry: Registry, profile: ProjectExport) -> Path:
    root = registry.export_dir.resolve()
    target = (root / profile.relative_dir).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ExportError("export profile escapes configured export root", profile=profile.name) from exc
    return target


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _markdown_export(*, project: Project, manifest: RunManifest, report: dict[str, Any]) -> str:
    lines = [
        f"# {project.label} SEO Hub Report",
        "",
        f"- Project: `{project.id}`",
        f"- Run: `{manifest.run_id}`",
        f"- Mode: `{manifest.mode}`",
        f"- State: `{manifest.state}`",
        "",
        "## Source Quality",
    ]
    for source in manifest.sources.values():
        lines.append(f"- `{source.name}`: {source.status}, quality `{source.quality}`")
    summary = report.get("summary") or report.get("decision_report", {}).get("executive_summary")
    if summary:
        lines.extend(["", "## Summary"])
        if isinstance(summary, list):
            lines.extend(f"- {item}" for item in summary)
        else:
            lines.append(str(summary))
    return "\n".join(lines) + "\n"


def _observer_action_drafts(*, project: Project, manifest: RunManifest, report: dict[str, Any]) -> dict[str, Any]:
    opportunities = report.get("opportunities")
    if not isinstance(opportunities, list):
        opportunities = []
    return {
        "schema": "seo-observer.action_drafts.v1",
        "project": project.id,
        "run_id": manifest.run_id,
        "side_effects": "none",
        "drafts": [
            {
                "type": str(item.get("type") or "review_candidate"),
                "title": str(item.get("title") or item.get("reason") or "Review evidence candidate"),
                "source": "seo-hub-export",
                "status": "draft",
            }
            for item in opportunities
            if isinstance(item, dict)
        ],
    }
