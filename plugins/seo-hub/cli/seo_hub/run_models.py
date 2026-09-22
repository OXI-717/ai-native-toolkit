from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


RunState = Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
SourceState = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]
EvidenceQuality = Literal["complete", "partial", "stale", "not_comparable", "missing"]


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_run_id(now: dt.datetime | None = None) -> str:
    instant = now or dt.datetime.now(dt.UTC)
    stamp = instant.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class ArtifactRef:
    name: str
    path: str
    sha256: str
    bytes: int | None = None
    media_type: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class SourceStatus:
    name: str
    status: SourceState
    quality: EvidenceQuality
    artifacts: list[ArtifactRef] = field(default_factory=list)
    source_status: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    started_at: str | None = None
    finished_at: str | None = None
    command: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "quality": self.quality,
            "artifacts": [artifact.to_json() for artifact in self.artifacts],
            "source_status": self.source_status,
        }
        if self.error is not None:
            payload["error"] = self.error
        if self.started_at is not None:
            payload["started_at"] = self.started_at
        if self.finished_at is not None:
            payload["finished_at"] = self.finished_at
        if self.command:
            payload["command"] = self.command
        return payload


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    project_id: str
    mode: str
    state: RunState
    created_at: str
    updated_at: str
    sources: dict[str, SourceStatus] = field(default_factory=dict)
    schema_version: int = 1

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sources": {name: source.to_json() for name, source in sorted(self.sources.items())},
        }

    def with_state(self, state: RunState, *, updated_at: str | None = None) -> "RunManifest":
        return RunManifest(
            run_id=self.run_id,
            project_id=self.project_id,
            mode=self.mode,
            state=state,
            created_at=self.created_at,
            updated_at=updated_at or utc_now(),
            sources=dict(self.sources),
            schema_version=self.schema_version,
        )

    def with_source(self, source: SourceStatus, *, state: RunState | None = None) -> "RunManifest":
        sources = dict(self.sources)
        sources[source.name] = source
        return RunManifest(
            run_id=self.run_id,
            project_id=self.project_id,
            mode=self.mode,
            state=state or self.state,
            created_at=self.created_at,
            updated_at=utc_now(),
            sources=sources,
            schema_version=self.schema_version,
        )


def aggregate_run_state(sources: dict[str, SourceStatus]) -> RunState:
    if not sources:
        return "failed"
    statuses = {source.status for source in sources.values()}
    if statuses <= {"succeeded", "skipped"} and "succeeded" in statuses:
        return "succeeded"
    if "cancelled" in statuses:
        return "cancelled" if statuses <= {"cancelled", "skipped"} else "partial"
    if "succeeded" in statuses and ("failed" in statuses or "cancelled" in statuses):
        return "partial"
    if "failed" in statuses:
        return "failed"
    if "running" in statuses:
        return "running"
    return "failed"
