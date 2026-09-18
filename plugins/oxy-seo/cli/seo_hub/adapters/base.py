from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seo_hub.run_models import ArtifactRef, EvidenceQuality, SourceState, SourceStatus


@dataclass(frozen=True)
class AdapterResult:
    name: str
    status: SourceState
    quality: EvidenceQuality
    artifacts: list[ArtifactRef] = field(default_factory=list)
    source_status: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    command: list[str] = field(default_factory=list)

    def to_source_status(self) -> SourceStatus:
        return SourceStatus(
            name=self.name,
            status=self.status,
            quality=self.quality,
            artifacts=list(self.artifacts),
            source_status=dict(self.source_status),
            error=self.error,
            command=list(self.command),
        )
