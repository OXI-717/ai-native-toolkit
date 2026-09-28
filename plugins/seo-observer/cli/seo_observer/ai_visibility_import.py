from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from seo_observer.ai_prompts import compute_prompt_set_hash
from seo_observer.citation_platforms import classify_platform_domain, normalize_citation_domain


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
    prompt_set_hash = _prompt_set_hash(prompt_rows)
    presence_stats = _presence_stats(prompts)
    cited_domains = _cited_domains(citation_domains, presence_stats)
    return {
        "provider": "elmo",
        "project_id": project_id,
        "property_id": property_id,
        "observed_at": observed_at,
        "quality": quality,
        "prompt_set_hash": prompt_set_hash,
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
        "cited_domains": cited_domains,
        "opportunity_candidates": _opportunity_candidates(cited_domains),
        "platform_summary": _platform_summary(cited_domains),
        "platform_metrics": _platform_metrics(cited_domains, presence_stats),
        "draft_actions": _draft_actions(
            cited_domains,
            project_id=project_id,
            window=window,
            observed_at=observed_at,
            prompt_set_hash=prompt_set_hash,
        ),
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


def _discovery_eligible(prompt: dict[str, Any]) -> bool:
    return (
        str(prompt.get("cohort") or "") != "branded_control"
        and str(prompt.get("control") or "") != "branded"
    )


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
        "discovery_eligible": _discovery_eligible(prompt),
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
                "platform_types": {
                    domain: classify_platform_domain(domain) for domain in prompt["citation_domains"]
                },
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


_prompt_set_hash = compute_prompt_set_hash


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


def _cited_domains(
    payload: dict[str, Any],
    presence_stats: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    domains = payload.get("domains") if isinstance(payload.get("domains"), list) else []
    rows = []
    for item in domains:
        if not isinstance(item, dict) or not item.get("domain"):
            continue
        if not normalize_citation_domain(item["domain"]):
            continue
        rows.append(
            {
                "domain": str(item["domain"]),
                "platform_type": classify_platform_domain(item["domain"]),
                "presence": _presence_state(
                    presence_stats.get(normalize_citation_domain(item["domain"]))
                ),
                "citations": _int(item.get("citations"), 0),
                "prompts": _int(item.get("prompts"), 0),
                "role": "opportunity_candidate",
            }
        )
    return rows


def _opportunity_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "domain": row["domain"],
            "platform_type": row["platform_type"],
            "source": "elmo_citation_domains",
        }
        for row in rows
    ]


def _presence_stats(prompts: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per-domain prompt-level stats used to derive brand presence.

    Presence is computed only from the imported envelope: a domain is "present"
    when at least one executed prompt citing it also reported a brand mention,
    and "present_negative" when such a mention carries negative sentiment. No
    live crawl of the platform is performed or implied.

    Branded-control prompts are excluded, matching ``discovery_metrics``: their
    expected brand mention is not evidence that the brand is present on the
    cited platform.
    """
    stats: dict[str, dict[str, int]] = {}
    for prompt in prompts:
        if not prompt.get("executed") or not _discovery_eligible(prompt):
            continue
        citations = prompt.get("citations")
        domains = {
            normalized
            for item in (citations if isinstance(citations, list) else [])
            if isinstance(item, dict)
            for normalized in [normalize_citation_domain(item.get("domain"))]
            if normalized
        }
        if not domains:
            continue
        raw_mentions = prompt.get("mentions")
        mentions = (
            [item for item in raw_mentions if isinstance(item, dict)]
            if isinstance(raw_mentions, list)
            else []
        )
        negative = any(
            str(item.get("sentiment") or "").strip().lower() == "negative" for item in mentions
        )
        for domain in domains:
            row = stats.setdefault(
                domain, {"prompts": 0, "mention_prompts": 0, "negative_mention_prompts": 0}
            )
            row["prompts"] += 1
            if mentions:
                row["mention_prompts"] += 1
            if negative:
                row["negative_mention_prompts"] += 1
    return stats


PRESENCE_STATES = ("present", "present_negative", "absent", "unknown")


def _presence_state(row: dict[str, int] | None) -> str:
    # A domain listed only by the aggregate endpoint has no prompt-level
    # mention context; presence there is honestly unknown, not absent.
    if not row or not row["prompts"]:
        return "unknown"
    if row["negative_mention_prompts"]:
        return "present_negative"
    if row["mention_prompts"]:
        return "present"
    return "absent"


def _platform_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for row in rows:
        bucket = summary.setdefault(
            row["platform_type"], {"domains": 0, "citations": 0, "presence": {}}
        )
        bucket["domains"] += 1
        bucket["citations"] += row["citations"]
        bucket["presence"][row["presence"]] = bucket["presence"].get(row["presence"], 0) + 1
    return {key: summary[key] for key in sorted(summary)}


def _platform_metrics(
    rows: list[dict[str, Any]],
    stats: dict[str, dict[str, int]],
) -> dict[str, dict[str, Any]]:
    """Per-domain discovery-prompt metrics for action signal metric paths.

    Keyed by ``_domain_key`` so each draft action measures its own platform via
    ``ai_visibility.platform_metrics.<key>``; domains listed only by the
    aggregate endpoint are emitted with zero counts so the metric path resolves
    in the baseline snapshot as well.
    """
    metrics: dict[str, dict[str, Any]] = {}
    for row in rows:
        domain = normalize_citation_domain(row["domain"])
        if not domain:
            continue
        stat = stats.get(domain) or {}
        metrics[_domain_key(domain)] = {
            "domain": domain,
            "prompts": stat.get("prompts", 0),
            "mention_prompts": stat.get("mention_prompts", 0),
            # Missing brand exposure cannot prove that negative sentiment improved.
            # The outcome evaluator treats null metrics as missing evidence.
            "negative_mention_prompts": (
                stat.get("negative_mention_prompts", 0)
                if stat.get("mention_prompts", 0) else None
            ),
        }
    return metrics


_DRAFT_ACTION_TYPE = "external_platform_presence"
_DRAFT_MINIMUM_ABSOLUTE_DELTA = 1
_DRAFT_SIGNALS = {
    "absent": ("mention_prompts", "increase"),
    "unknown": ("mention_prompts", "increase"),
    "present_negative": ("negative_mention_prompts", "decrease"),
}
_DRAFT_DESCRIPTIONS = {
    "absent": (
        "Establish brand presence on {domain} ({platform_type}): AI answers cite "
        "the platform without mentioning the brand."
    ),
    "present_negative": (
        "Repair brand presence on {domain} ({platform_type}): AI answers citing "
        "the platform mention the brand with negative sentiment."
    ),
    "unknown": (
        "Audit brand presence on {domain} ({platform_type}): the platform is cited "
        "in AI answers, but prompt-level evidence is insufficient to determine "
        "presence."
    ),
}


def _draft_actions(
    rows: list[dict[str, Any]],
    *,
    project_id: str,
    window: dict[str, str],
    observed_at: str,
    prompt_set_hash: str,
) -> list[dict[str, Any]]:
    """Export cited platforms as draft observer-actions.

    Drafts follow the existing action contract (`action_from_dict`): lifecycle
    `planned`, a platform URL target and a matched-period measurement window.
    They are export payloads only — nothing is persisted, crawled, posted or
    sent anywhere.
    """
    changed_at, changed_date = _draft_changed_at(observed_at, window)
    if changed_at is None or changed_date is None:
        return []
    actions = []
    for row in rows:
        presence = row["presence"]
        if presence == "present":
            continue
        domain = row["domain"]
        key = _domain_key(domain)
        action_id = f"citation-platform-{key}"
        draft_window, observation_days = _draft_window(action_id, window, changed_date)
        metric_field, direction = _DRAFT_SIGNALS[presence]
        actions.append(
            {
                "action_id": action_id,
                "project_id": project_id,
                "changed_at": changed_at,
                "action_type": _DRAFT_ACTION_TYPE,
                "description": _DRAFT_DESCRIPTIONS[presence].format(
                    domain=domain, platform_type=row["platform_type"]
                ),
                "hypothesis_id": f"geo-citation-presence-{key}",
                "evidence_ref": f"elmo-ai-visibility:{prompt_set_hash}",
                "lifecycle_state": "planned",
                "targets": [
                    {
                        "target_type": "url",
                        "target_value": f"https://{normalize_citation_domain(domain) or domain}/",
                        "target_role": "primary",
                    }
                ],
                "windows": [draft_window],
                "expected_signals": [
                    {
                        "metric_path": f"ai_visibility.platform_metrics.{key}.{metric_field}",
                        "direction": direction,
                        "minimum_absolute_delta": _DRAFT_MINIMUM_ABSOLUTE_DELTA,
                        "minimum_observation_days": observation_days,
                    }
                ],
            }
        )
    return actions


def _draft_changed_at(
    observed_at: str, window: dict[str, str]
) -> tuple[str | None, dt.date | None]:
    instant = _parse_instant(observed_at)
    if instant is not None:
        return observed_at, instant.date()
    for key in ("end", "start"):
        day = _window_date(window.get(key))
        if day is not None:
            return day.isoformat(), day
    return None, None


def _draft_window(
    action_id: str, window: dict[str, str], changed_date: dt.date
) -> tuple[dict[str, Any], int]:
    baseline_start = _window_date(window.get("start")) or changed_date
    baseline_end = _window_date(window.get("end")) or changed_date
    if baseline_end < baseline_start:
        baseline_start, baseline_end = baseline_end, baseline_start
    observation_start = max(baseline_end + dt.timedelta(days=1), changed_date)
    observation_end = observation_start + dt.timedelta(days=(baseline_end - baseline_start).days)
    observation_days = (observation_end - observation_start).days + 1
    return (
        {
            "window_id": f"{action_id}:{baseline_start.isoformat()}:{observation_end.isoformat()}",
            "baseline_start": baseline_start.isoformat(),
            "baseline_end": baseline_end.isoformat(),
            "observation_start": observation_start.isoformat(),
            "observation_end": observation_end.isoformat(),
            "timezone": str(window.get("timezone") or "UTC"),
            "comparison_strategy": "matched_period",
            "earliest_evaluation_date": observation_end.isoformat(),
        },
        observation_days,
    )


def _window_date(value: object) -> dt.date | None:
    instant = _parse_instant(str(value or ""))
    return instant.date() if instant is not None else None


def _domain_key(domain: object) -> str:
    """Reversible, dot-free encoding: distinct normalized hosts cannot collide."""
    normalized = normalize_citation_domain(domain)
    return "host-" + normalized.encode("utf-8").hex()


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
