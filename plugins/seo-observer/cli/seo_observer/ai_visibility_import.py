from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any


def import_elmo_ai_visibility(
    envelope: dict[str, Any],
    *,
    project_id: str,
    property_id: str,
    observed_at: str,
    expected_window: dict[str, str] | None = None,
    max_age_days: int = 14,
) -> dict[str, Any]:
    endpoints = envelope.get("endpoints") if isinstance(envelope.get("endpoints"), dict) else {}
    analytics = _payload(endpoints, "analytics")
    fan_out = _payload(endpoints, "query-fan-out")
    prompt_performance = _payload(endpoints, "prompt-performance")
    citation_domains = _payload(endpoints, "citation-domains")
    prompts = _prompts(fan_out)
    analytics_window = analytics.get("window") if isinstance(analytics.get("window"), dict) else {}
    request = envelope.get("request") if isinstance(envelope.get("request"), dict) else {}
    window = {
        "start": str(analytics_window.get("start") or _nested(request, "window", "start") or ""),
        "end": str(analytics_window.get("end") or _nested(request, "window", "end") or ""),
        "timezone": str(analytics_window.get("timezone") or "UTC"),
    }
    prompt_rows = sorted((_prompt_row(prompt) for prompt in prompts), key=lambda row: row["prompt_id"])
    observations = _observations(
        prompt_rows,
        analytics=analytics,
        project_id=project_id,
        property_id=property_id,
        window=window,
        observed_at=observed_at,
    )
    coverage = _coverage_state(analytics, prompt_rows, endpoints)
    freshness = _freshness_state(str(analytics.get("updated_at") or observed_at), observed_at, max_age_days=max_age_days)
    # The source timezone, exactly as the provider sent it: `window` carries a
    # synthesized "UTC" default for downstream consumers, and comparing against
    # that default would let an unknown source timezone pass as a match.
    comparability = _comparability_state(
        window, expected_window, source_timezone=analytics_window.get("timezone")
    )
    quality = _quality(coverage["state"], freshness["state"], comparability["state"])
    return {
        "provider": "elmo",
        "project_id": project_id,
        "property_id": property_id,
        "observed_at": observed_at,
        "quality": quality,
        "prompt_set_hash": _prompt_set_hash(prompt_rows),
        "window": window,
        "locale": str(analytics.get("locale") or request.get("locale") or ""),
        "surface": str(analytics.get("surface") or _first(prompt_performance.get("prompts"), "surface") or ""),
        "model": str(analytics.get("model") or _first(prompt_performance.get("prompts"), "model") or ""),
        "cohorts": sorted({row["cohort"] for row in prompt_rows if row["cohort"]}),
        "coverage": coverage,
        "freshness": freshness,
        "comparability": comparability,
        "prompt_rows": prompt_rows,
        "observations": observations,
        "discovery_metrics": _discovery_metrics(prompt_rows),
        "conversion_proxy": _conversion_proxy(analytics),
        "cited_domains": _cited_domains(citation_domains),
        "opportunity_candidates": _opportunity_candidates(citation_domains),
    }


def _payload(endpoints: dict[str, Any], name: str) -> dict[str, Any]:
    item = endpoints.get(name)
    if not isinstance(item, dict) or item.get("status") != "succeeded":
        return {}
    payload = item.get("payload")
    return payload if isinstance(payload, dict) else {}


def _prompts(fan_out: dict[str, Any]) -> list[dict[str, Any]]:
    rows = fan_out.get("prompts")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _prompt_row(prompt: dict[str, Any]) -> dict[str, Any]:
    cohort = str(prompt.get("cohort") or "")
    control = str(prompt.get("control") or "")
    mentions = prompt.get("mentions") if isinstance(prompt.get("mentions"), list) else []
    citations = prompt.get("citations") if isinstance(prompt.get("citations"), list) else []
    return {
        "prompt_id": str(prompt.get("prompt_id") or ""),
        "text": str(prompt.get("text") or ""),
        "cohort": cohort,
        "control": control,
        "executed": bool(prompt.get("executed")),
        "mention_count": len(mentions),
        "citation_count": len(citations),
        "citation_domains": sorted(
            {str(item.get("domain")) for item in citations if isinstance(item, dict) and item.get("domain")}
        ),
        "discovery_eligible": cohort != "branded_control" and control != "branded",
    }


def _observations(
    prompt_rows: list[dict[str, Any]],
    *,
    analytics: dict[str, Any],
    project_id: str,
    property_id: str,
    window: dict[str, str],
    observed_at: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for prompt in sorted(prompt_rows, key=lambda item: item["prompt_id"]):
        if not prompt["executed"]:
            continue
        base = {
            "project_id": project_id,
            "property_id": property_id,
            "source": "elmo",
            "prompt_id": prompt["prompt_id"],
            "cohort": prompt["cohort"],
            "surface": str(analytics.get("surface") or ""),
            "model": str(analytics.get("model") or ""),
            "locale": str(analytics.get("locale") or ""),
            "window": window,
            "observed_at": observed_at,
            "discovery_eligible": prompt["discovery_eligible"],
        }
        rows.append({**base, "metric": "mention", "value": prompt["mention_count"]})
        rows.append(
            {
                **base,
                "metric": "citation",
                "value": prompt["citation_count"],
                "citation_domain": prompt["citation_domains"][0] if len(prompt["citation_domains"]) == 1 else None,
                "citation_domains": prompt["citation_domains"],
            }
        )
    return rows


def _coverage_state(
    analytics: dict[str, Any],
    prompt_rows: list[dict[str, Any]],
    endpoints: dict[str, Any],
) -> dict[str, Any]:
    required_missing = [
        name for name in ("analytics", "query-fan-out", "prompt-performance") if not _payload(endpoints, name)
    ]
    expected = _int(analytics.get("expected_prompts"), len(prompt_rows))
    executed_rows = sum(1 for row in prompt_rows if row["executed"])
    executed = min(_int(analytics.get("executed_prompts"), executed_rows), executed_rows)
    if required_missing:
        state = "missing"
    elif expected == 0 or executed == 0:
        state = "missing"
    elif executed < expected or len(prompt_rows) < expected:
        state = "partial"
    else:
        state = "complete"
    return {
        "state": state,
        "expected_prompts": expected,
        "executed_prompts": executed,
        "missing_endpoints": required_missing,
    }


def _freshness_state(updated_at: str, observed_at: str, *, max_age_days: int) -> dict[str, Any]:
    updated = _parse_instant(updated_at)
    observed = _parse_instant(observed_at)
    stale = updated is None or observed is None or observed - updated > dt.timedelta(days=max_age_days)
    return {"state": "stale" if stale else "complete", "updated_at": updated_at, "max_age_days": max_age_days}


_UNSET = object()


def _comparability_state(
    window: dict[str, str],
    expected_window: dict[str, str] | None,
    *,
    source_timezone: object = _UNSET,
) -> dict[str, Any]:
    if expected_window is None:
        return {"state": "comparable", "reasons": []}
    expected = {"start": expected_window.get("start"), "end": expected_window.get("end")}
    actual = {"start": window.get("start"), "end": window.get("end")}
    expected_timezone = expected_window.get("timezone")
    if expected_timezone is not None:
        expected["timezone"] = expected_timezone
        # An unreported source timezone is unknown, not "the one you expected":
        # at this gate uncertainty must fail towards not_comparable.
        actual["timezone"] = (
            window.get("timezone") if source_timezone is _UNSET else source_timezone
        )
    if actual != expected:
        return {"state": "not_comparable", "reasons": ["window_mismatch"]}
    return {"state": "comparable", "reasons": []}


def _quality(coverage: str, freshness: str, comparability: str) -> str:
    if comparability == "not_comparable":
        return "not_comparable"
    if coverage == "missing":
        return "missing"
    if freshness == "stale":
        return "stale"
    if coverage == "partial":
        return "partial"
    return "complete"


def _prompt_set_hash(prompt_rows: list[dict[str, Any]]) -> str:
    stable = [
        {
            "prompt_id": row["prompt_id"],
            "text": row["text"],
            "cohort": row["cohort"],
            "control": row["control"],
        }
        for row in sorted(prompt_rows, key=lambda item: item["prompt_id"])
    ]
    body = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _discovery_metrics(prompt_rows: list[dict[str, Any]]) -> dict[str, int]:
    rows = [row for row in prompt_rows if row["discovery_eligible"]]
    return {
        "expected_prompts": len(rows),
        "executed_prompts": sum(1 for row in rows if row["executed"]),
        "mention_prompts": sum(1 for row in rows if row["executed"] and row["mention_count"] > 0),
        "citation_prompts": sum(1 for row in rows if row["executed"] and row["citation_count"] > 0),
    }


def _conversion_proxy(analytics: dict[str, Any]) -> dict[str, Any]:
    value = analytics.get("conversion_proxy")
    if not isinstance(value, dict):
        return {"state": "missing"}
    event_count = value.get("event_count")
    denominator = value.get("denominator")
    if not isinstance(event_count, (int, float)) or isinstance(event_count, bool):
        return {"state": "missing"}
    if not isinstance(denominator, (int, float)) or isinstance(denominator, bool) or denominator <= 0:
        return {"state": "missing"}
    return {
        "state": "complete",
        "event_count": event_count,
        "denominator": denominator,
        "rate": event_count / denominator,
    }


def _cited_domains(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domains = payload.get("domains") if isinstance(payload.get("domains"), list) else []
    rows = []
    for item in domains:
        if not isinstance(item, dict) or not item.get("domain"):
            continue
        rows.append(
            {
                "domain": str(item["domain"]),
                "citations": _int(item.get("citations"), 0),
                "prompts": _int(item.get("prompts"), 0),
                "role": "opportunity_candidate",
            }
        )
    return rows


def _opportunity_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"domain": row["domain"], "source": "elmo_citation_domains"} for row in _cited_domains(payload)]


def _nested(data: dict[str, Any], outer: str, inner: str) -> Any:
    value = data.get(outer)
    return value.get(inner) if isinstance(value, dict) else None


def _first(rows: object, key: str) -> Any:
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get(key):
            return row[key]
    return None


def _int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _parse_instant(value: str) -> dt.datetime | None:
    try:
        instant = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if instant.tzinfo is None:
        return instant.replace(tzinfo=dt.timezone.utc)
    return instant.astimezone(dt.timezone.utc)
