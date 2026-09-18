from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from seo_hub.auth import Principal, require_scope
from seo_hub.errors import SeoHubError
from seo_hub.exports import export_run, latest_report_from_manifest
from seo_hub.registry import Registry
from seo_hub.runner import HubRunner
from seo_hub.store import RunStore


class APIError(SeoHubError, ValueError):
    code = "API_ERROR"


class HubAPI:
    def __init__(self, *, registry: Registry, runner: HubRunner | None = None, store: RunStore | None = None) -> None:
        self.registry = registry
        self.store = store or RunStore(registry.data_dir)
        self.runner = runner or HubRunner(registry=registry, store=self.store)

    def projects(self, principal: Principal) -> dict[str, Any]:
        require_scope(principal, "read")
        return {
            "ok": True,
            "projects": [
                {
                    "id": project.id,
                    "label": project.label,
                    "readiness": self.project_status(project.id, principal)["readiness"],
                    "deep_links": _deep_links(project),
                    "exports": [
                        {"name": export.name, "format": export.format}
                        for export in project.exports
                    ],
                }
                for project in self.registry.projects
            ],
        }

    def project_status(self, project_id: str, principal: Principal) -> dict[str, Any]:
        require_scope(principal, "read")
        project = self._project(project_id)
        runs = self.store.list_manifests(project.id)
        latest = runs[0].to_json() if runs else None
        readiness = {
            "observer": project.observer_config.exists(),
            "credentials": project.credentials_env_file.exists(),
            "elmo": _source_ready([run.to_json() for run in runs], "elmo.ai_visibility"),
            "openseo": _source_ready([run.to_json() for run in runs], "openseo.evidence"),
        }
        return {
            "ok": True,
            "project": {"id": project.id, "label": project.label, "deep_links": _deep_links(project)},
            "readiness": readiness,
            "latest_run": latest,
            "history": [run.to_json() for run in runs[:20]],
        }

    def run(self, project_id: str, mode: str, principal: Principal, *, idempotency_key: str | None = None) -> dict[str, Any]:
        require_scope(principal, "read-only-run")
        self._project(project_id)
        manifest = asyncio.run(self.runner.run(project_id=project_id, mode=mode, idempotency_key=idempotency_key))
        report = latest_report_from_manifest(self.store, manifest)
        return {"ok": True, "run": manifest.to_json(), "report": report}

    def report(self, project_id: str, principal: Principal, *, run_id: str | None = None) -> dict[str, Any]:
        require_scope(principal, "read")
        manifest = self._run(project_id, run_id)
        return {"ok": True, "run": manifest.to_json(), "report": latest_report_from_manifest(self.store, manifest)}

    def opportunities(self, project_id: str, principal: Principal, *, run_id: str | None = None) -> dict[str, Any]:
        payload = self.report(project_id, principal, run_id=run_id)
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        opportunities = report.get("opportunities") if isinstance(report, dict) else None
        payload["opportunities"] = opportunities if isinstance(opportunities, list) else []
        return payload

    def outcomes(self, project_id: str, principal: Principal) -> dict[str, Any]:
        status = self.project_status(project_id, principal)
        return {"ok": True, "project": status["project"], "outcomes": _outcomes_from_history(status["history"])}

    def export(
        self,
        project_id: str,
        run_id: str,
        format_name: str,
        principal: Principal,
        *,
        profile_name: str | None = None,
    ) -> dict[str, Any]:
        require_scope(principal, "read")
        project = self._project(project_id)
        manifest = self._run(project_id, run_id)
        result = export_run(
            registry=self.registry,
            project=project,
            manifest=manifest,
            format_name=format_name,
            profile_name=profile_name,
            report=latest_report_from_manifest(self.store, manifest),
        )
        return {"ok": True, "export": result.to_json(), "run": manifest.to_json()}

    def _project(self, project_id: str):
        for project in self.registry.projects:
            if project.id == project_id:
                return project
        raise APIError(f"unknown project: {project_id}", project=project_id)

    def _run(self, project_id: str, run_id: str | None):
        runs = self.store.list_manifests(project_id)
        if not runs:
            raise APIError(f"no runs found for project: {project_id}", project=project_id)
        if run_id is None:
            return runs[0]
        for manifest in runs:
            if manifest.run_id == run_id:
                return manifest
        raise APIError(f"unknown run: {run_id}", project=project_id, run_id=run_id)


class LocalHubClient:
    def __init__(self, *, api: HubAPI, principal: Principal) -> None:
        self.api = api
        self.principal = principal

    def request(self, command: str, **kwargs: Any) -> dict[str, Any]:
        if command == "projects":
            return self.api.projects(self.principal)
        if command == "status":
            project_id = kwargs.get("project_id")
            if project_id:
                return self.api.project_status(str(project_id), self.principal)
            return self.api.projects(self.principal)
        if command == "run":
            return self.api.run(str(kwargs["project_id"]), str(kwargs["mode"]), self.principal, idempotency_key=kwargs.get("idempotency_key"))
        if command == "report":
            return self.api.report(str(kwargs["project_id"]), self.principal, run_id=kwargs.get("run_id"))
        if command == "opportunities":
            return self.api.opportunities(str(kwargs["project_id"]), self.principal, run_id=kwargs.get("run_id"))
        if command == "outcomes":
            return self.api.outcomes(str(kwargs["project_id"]), self.principal)
        if command == "export":
            return self.api.export(str(kwargs["project_id"]), str(kwargs["run_id"]), str(kwargs["format_name"]), self.principal, profile_name=kwargs.get("profile_name"))
        raise APIError(f"unsupported API command: {command}", command=command)


def _deep_links(project: Any) -> dict[str, str]:
    openseo_base = project.openseo_mcp_url.removesuffix("/mcp").rstrip("/")
    elmo_path = "/app"
    if project.elmo_organization_slug:
        elmo_path += f"/org/{quote(project.elmo_organization_slug, safe='')}/brand/{quote(project.elmo_brand_id, safe='')}"
    return {
        "elmo": f"{project.elmo_base_url.rstrip('/')}{elmo_path}",
        "openseo": f"{openseo_base}/p/{quote(project.openseo_project_id, safe='')}",
    }


def _source_ready(history: list[dict[str, Any]], name: str) -> bool:
    for run in history:
        if name in run.get("sources", {}):
            source = run["sources"][name]
            return source.get("status") == "succeeded" and source.get("quality") != "missing"
    return False


def _outcomes_from_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"run_id": run["run_id"], "state": run["state"], "source_quality": {k: v["quality"] for k, v in run.get("sources", {}).items()}}
        for run in history
    ]
