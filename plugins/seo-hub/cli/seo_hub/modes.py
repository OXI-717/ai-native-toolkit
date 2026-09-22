from __future__ import annotations

from dataclasses import dataclass

from seo_hub.errors import SeoHubError


class ModeError(SeoHubError, ValueError):
    code = "MODE_INVALID"


@dataclass(frozen=True)
class ModePlan:
    mode: str
    observer_commands: tuple[str, ...]
    import_elmo: bool = False
    import_openseo: bool = False


MODE_PLANS: dict[str, ModePlan] = {
    "baseline": ModePlan("baseline", ("doctor", "snapshot", "report")),
    "ai-visibility": ModePlan("ai-visibility", ("doctor", "ai-readiness"), import_elmo=True),
    "competitors": ModePlan("competitors", ("doctor",), import_openseo=True),
    "full": ModePlan("full", ("doctor", "snapshot", "ai-readiness", "report"), import_elmo=True, import_openseo=True),
}


def mode_plan(mode: str) -> ModePlan:
    try:
        return MODE_PLANS[mode]
    except KeyError as exc:
        raise ModeError(f"unsupported run mode: {mode}", mode=mode, allowed=sorted(MODE_PLANS)) from exc


def allowed_modes() -> tuple[str, ...]:
    return tuple(MODE_PLANS)
