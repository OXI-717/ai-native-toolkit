from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from http.cookies import SimpleCookie, CookieError

from seo_hub.adapters.observer import ObserverAdapter
from seo_hub.errors import SeoHubError
from seo_hub.registry import Project, Registry
from seo_hub.run_models import RunManifest, aggregate_run_state, new_run_id, utc_now
from seo_hub.store import RunStore, StoreError
from seo_hub.modes import mode_plan
from seo_hub.secrets import load_secret_env, redact_json


PROVIDER_ENV_NAMES = frozenset({
    "ELMO_SESSION_COOKIE", "ELMO_API_KEY", "OPENSEO_API_KEY",
    "OPENSEO_ACCESS_CLIENT_ID", "OPENSEO_ACCESS_CLIENT_SECRET",
})


class HubRunner:
    def __init__(
        self,
        *,
        registry: Registry,
        store: RunStore | None = None,
        observer: ObserverAdapter | None = None,
        elmo: Any | None = None,
        openseo: Any | None = None,
    ) -> None:
        self.registry = registry
        self.store = store or RunStore(registry.data_dir)
        self.observer = observer or ObserverAdapter()
        self.elmo = elmo
        self.openseo = openseo

    async def run(self, *, project_id: str, mode: str, idempotency_key: str | None = None) -> RunManifest:
        project = self._project(project_id)
        plan = mode_plan(mode)
        now = utc_now()
        run_id = idempotency_key if idempotency_key and idempotency_key.startswith("run_") else new_run_id()
        if idempotency_key:
            existing = self._idempotent_manifest(project.id, idempotency_key, run_id)
            if existing is not None:
                return existing
        manifest = RunManifest(
            run_id=run_id,
            project_id=project.id,
            mode=mode,
            state="queued",
            created_at=now,
            updated_at=now,
        )
        with self.store.project_lock(project.id):
            if idempotency_key:
                existing = self._idempotent_manifest(project.id, idempotency_key, run_id)
                if existing is not None:
                    return existing
            self.store.save_manifest(manifest)
            if idempotency_key and not idempotency_key.startswith("run_"):
                self.store.save_idempotent_run_id(project.id, idempotency_key, manifest.run_id)
            manifest = manifest.with_state("running")
            self.store.save_manifest(manifest)
            output_dir = self.store.run_dir(project.id, manifest.run_id) / "observer"
            output_dir.mkdir(parents=True, exist_ok=True)
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=30)
            if plan.import_elmo:
                credentials = {}
                try:
                    credentials = self._provider_env(project)
                    elmo = self.elmo or self._provider(project, "elmo", credentials)
                    result = await elmo.read_ai_visibility(
                        brand_id=project.elmo_brand_id,
                        window_start=start.isoformat(),
                        window_end=end.isoformat(),
                        locale="",
                    )
                except Exception as exc:
                    from seo_hub.adapters.base import AdapterResult

                    result = AdapterResult(
                        name="elmo.ai_visibility", status="failed", quality="missing",
                        error={"code": getattr(exc, "code", "ADAPTER_ERROR"),
                               "message": "Elmo import failed; check native service, project ID and credentials."},
                    )
                manifest = manifest.with_source(self._redact_result(result, credentials).to_source_status(), state="running")
                self.store.save_manifest(manifest)
            if plan.import_openseo:
                credentials = {}
                try:
                    credentials = self._provider_env(project)
                    openseo = self.openseo or self._provider(project, "openseo", credentials)
                    evidence = await openseo.collect_project_evidence(
                        project_id=project.openseo_project_id,
                        rank_tracker_id=project.openseo_rank_tracker_id,
                        window={"start": start.isoformat(), "end": end.isoformat(), "timezone": "UTC"},
                    )
                    from seo_hub.adapters.base import AdapterResult

                    result = AdapterResult(
                        name="openseo.evidence",
                        status="succeeded",
                        quality=evidence.get("quality", "partial"),
                        source_status=evidence,
                    )
                except Exception as exc:
                    from seo_hub.adapters.base import AdapterResult

                    result = AdapterResult(
                        name="openseo.evidence",
                        status="failed",
                        quality="missing",
                        error={"code": getattr(exc, "code", "ADAPTER_ERROR"),
                               "message": "OpenSEO import failed; check native service, project ID and credentials."},
                    )
                manifest = manifest.with_source(self._redact_result(result, credentials).to_source_status(), state="running")
                self.store.save_manifest(manifest)
            for command in plan.observer_commands:
                try:
                    result = await self.observer.read(
                        command,
                        config=project.observer_config,
                        output_dir=output_dir,
                    )
                except SeoHubError as exc:  # adapter failures are source failures, not run crashes
                    from seo_hub.adapters.base import AdapterResult

                    result = AdapterResult(
                        name=f"observer.{command}",
                        status="failed",
                        quality="missing",
                        error={"code": exc.code, "message": exc.message, "details": exc.details},
                    )
                except Exception as exc:
                    from seo_hub.adapters.base import AdapterResult

                    result = AdapterResult(
                        name=f"observer.{command}",
                        status="failed",
                        quality="missing",
                        error={"code": "ADAPTER_ERROR", "message": str(exc)},
                    )
                manifest = manifest.with_source(result.to_source_status(), state="running")
                self.store.save_manifest(manifest)
            manifest = manifest.with_state(aggregate_run_state(manifest.sources))
            self.store.save_manifest(manifest)
            return manifest

    def _provider_env(self, project: Project):
        secret_path = project.provider_credentials_env_file
        return load_secret_env(
            secret_path, allowed_names=PROVIDER_ENV_NAMES,
            allowed_root=self.registry.path.parent.parent,
        ) if secret_path else {}

    def _provider(self, project: Project, name: str, env: dict[str, str]):
        def _text(name: str) -> str:
            return env.get(name) or ""

        if name == "elmo":
            from seo_hub.adapters.elmo import ElmoAdapter
            return ElmoAdapter(
                base_url=project.elmo_base_url,
                session_cookie=_text("ELMO_SESSION_COOKIE"),
                credential_value=_text("ELMO_API_KEY"),
            )
        from seo_hub.adapters.openseo import OpenSEOAdapter
        return OpenSEOAdapter(
            project.openseo_mcp_url,
            None,
            _text("OPENSEO_ACCESS_CLIENT_ID"),
            _text("OPENSEO_ACCESS_CLIENT_SECRET"),
            30,
            _text("OPENSEO_API_KEY"),
        )

    def _redact_result(self, result, credentials: dict[str, str]):
        values = dict(credentials)
        try:
            cookie = SimpleCookie()
            cookie.load(values.get("ELMO_SESSION_COOKIE", ""))
            values.update({f"cookie:{name}": morsel.value for name, morsel in cookie.items()})
        except CookieError:
            pass
        return replace(result, source_status=redact_json(result.source_status, values),
                       error=redact_json(result.error, values),
                       command=redact_json(result.command, values))

    def _idempotent_manifest(self, project_id: str, idempotency_key: str, run_id: str) -> RunManifest | None:
        try:
            if idempotency_key.startswith("run_"):
                return self.store.load_manifest(project_id, run_id)
            existing_run_id = self.store.load_idempotent_run_id(project_id, idempotency_key)
            return self.store.load_manifest(project_id, existing_run_id)
        except StoreError:
            return None

    def _project(self, project_id: str) -> Project:
        for project in self.registry.projects:
            if project.id == project_id:
                return project
        raise ValueError(f"unknown project: {project_id}")
