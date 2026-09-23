from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from statistics import median
from typing import Any, Protocol
from urllib.parse import urlparse


DEFAULT_ENDPOINT = "/search-api/v2/web/search"
DEFAULT_API_FAMILY = "yandex_search_api"
DEFAULT_API_VERSION = "v2"
SUPPORTED_PROVIDERS = frozenset(
    {
        "yandex_search",
        "fixture_yandex_search",
        "dataforseo_google_organic",
        "topvisor_google_organic",
        "topvisor_yandex_organic",
    }
)
DOCTOR_SUPPORTED_PROVIDERS = SUPPORTED_PROVIDERS
SUPPORTED_API_FAMILIES = frozenset(
    {"yandex_search_api", "dataforseo_serp_api", "topvisor_api"}
)

# A claim about a specific provider, not about the fact of its support.
# Previously `no_browser_automation` was computed as "provider is in the
# supported list", i.e. it answered true by construction and could never become
# false for anyone. The first provider that actually drives a browser would have
# made this field a lie, with no way to notice it.
PROVIDER_DRIVES_BROWSER = {
    "yandex_search": False,
    "fixture_yandex_search": False,
    "dataforseo_google_organic": False,
    "topvisor_google_organic": False,
    "topvisor_yandex_organic": False,
}


class SerpRequestError(ValueError):
    pass


class SerpTransport(Protocol):
    def post_json(
        self,
        endpoint: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class SerpSource:
    token: str
    property_id: str
    provider: str = "yandex_search"
    api_family: str = DEFAULT_API_FAMILY
    api_version: str = DEFAULT_API_VERSION
    endpoint: str = DEFAULT_ENDPOINT
    method: str = "POST"
    response_format: str = "XML"
    # Yandex Cloud Search API v2 accepts Api-Key (or an IAM token), but NOT OAuth:
    # the same key yields HTTP 200 with "Api-Key" and HTTP 401 with "OAuth" (verified 2026-07-31).
    auth_scheme: str = "Api-Key"
    search_engine: str = "yandex"
    locale: str = "ru-RU"
    language: str = "ru"
    supports_devices: bool = False
    result_depth: int = 10
    observations_per_protocol_slot: int = 1
    confirmation_observations: int = 1
    minimum_weighted_keyword_coverage: float = 0.9
    classification_config_hash: str = "sha256:unconfigured"
    date_bucket: str = "__all__"


@dataclass(frozen=True)
class SerpKeywordSet:
    id: str
    keywords: tuple[str, ...]
    locale: str
    regions: tuple[str, ...]
    devices: tuple[str, ...] = ("__all__",)
    weight: float = 1.0
    # The market the set belongs to. Needed so the audit picks the adapter
    # and checks the credentials of that market rather than the universal
    # DataForSEO ones.
    market: str | None = None


@dataclass(frozen=True)
class SerpProtocolSlot:
    keyword_set_id: str
    keyword_set_hash: str
    keyword: str
    region_id: str
    region_name: str
    locale: str
    language: str
    device: str
    search_engine: str
    depth: int
    observation_count: int
    observation_slot: int
    provider: str
    api_family: str
    api_version: str
    endpoint: str
    method: str
    response_format: str
    device_supported: bool
    property_id: str
    weight: float = 1.0
    classification_config_hash: str = "sha256:unconfigured"
    date_bucket: str = "__all__"
    # The market of the slot. Needed by SERP classification (#1677) and
    # intentionally NOT part of normalized_inputs: protocol_hash must stay
    # comparable with snapshots taken before markets existed.
    market: str | None = None

    @property
    def protocol_hash(self) -> str:
        return protocol_hash(self.normalized_inputs())

    def normalized_inputs(self) -> dict[str, Any]:
        return {
            "keyword_set_id": self.keyword_set_id,
            "keyword_set_hash": self.keyword_set_hash,
            "keyword": self.keyword,
            "region_id": self.region_id,
            "locale": self.locale,
            "language": self.language,
            "device": self.device,
            "search_engine": self.search_engine,
            "depth": self.depth,
            "observation_count": self.observation_count,
            "observation_slot": self.observation_slot,
            "provider": self.provider,
            "api_family": self.api_family,
            "api_version": self.api_version,
            "classification_config_hash": self.classification_config_hash,
            "date_bucket": self.date_bucket,
        }


@dataclass(frozen=True)
class Competitor:
    id: str
    name: str
    domain_patterns: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    # The class determines whether the domain counts toward competitor SOV:
    # a reference (regulator, primary source) or a marketplace (a storefront
    # hosting our own app) cannot have its SERP slot taken by a product page.
    competitor_class: str = "unknown"
    # An empty tuple means "in all markets".
    markets: tuple[str, ...] = ()


# Classes whose SERP slot cannot be taken by our own page. We exclude only
# these, not "everything except direct/indirect": an unmarked config (class
# `unknown`) would otherwise collapse SOV to zero, i.e. uncertainty would
# silently corrupt the metric instead of preserving the previous behavior
# (#1676).
NON_CONTESTABLE_CLASSES = frozenset({"reference", "marketplace"})


@dataclass(frozen=True)
class CompetitorConfig:
    owned_domains: tuple[str, ...]
    competitors: tuple[Competitor, ...]


class SerpAdapter:
    def __init__(
        self,
        source: SerpSource,
        transport: SerpTransport,
        *,
        competitor_config: CompetitorConfig | None = None,
    ) -> None:
        _validate_source(source)
        self.source = source
        self.transport = transport
        self.competitor_config = competitor_config

    def request_descriptor(self, slot: SerpProtocolSlot) -> dict[str, Any]:
        payload = {
            "query_text": slot.keyword,
            "region": {"id": slot.region_id, "name": slot.region_name},
            "locale": slot.locale,
            "language": slot.language,
            "depth": slot.depth,
            "response_format": slot.response_format,
            "search_engine": slot.search_engine,
            "device": {"value": slot.device, "supported": slot.device_supported},
            "api_family": slot.api_family,
            "api_version": slot.api_version,
            "keyword_set_id": slot.keyword_set_id,
            "keyword_set_hash": slot.keyword_set_hash,
            "protocol_hash": slot.protocol_hash,
            "observation_count": slot.observation_count,
            "observation_slot": slot.observation_slot,
        }
        return {"endpoint": slot.endpoint, "method": slot.method, "payload": payload}

    def fetch_slot(
        self,
        slot: SerpProtocolSlot,
        *,
        effective_at: str,
        competitor_config: CompetitorConfig | None = None,
    ) -> dict[str, Any]:
        request = self.request_descriptor(slot)
        page = self._post(request)
        results = _rows(page)
        metadata = self._metadata(slot, request, page, results, effective_at)
        config = competitor_config or self.competitor_config
        observations = [
            self._rank_observation(slot, metadata, row, raw_position=index, competitor_config=config)
            for index, row in enumerate(results, start=1)
        ]
        metadata["rows_received"] = len(observations)
        return {"collection": "serp_results", "metadata": metadata, "observations": observations}

    def _post(self, request: dict[str, Any]) -> dict[str, Any]:
        page = self.transport.post_json(
            str(request["endpoint"]),
            json=dict(request["payload"]),
            headers={"Authorization": f"{self.source.auth_scheme} {self.source.token}"},
        )
        if not isinstance(page, dict):
            raise SerpRequestError("SERP transport returned a non-object response.")
        return page

    def _metadata(
        self,
        slot: SerpProtocolSlot,
        request: dict[str, Any],
        page: dict[str, Any],
        rows: list[dict[str, Any]],
        effective_at: str,
    ) -> dict[str, Any]:
        features_supported = any(_feature_labels(row) for row in rows)
        unsupported: dict[str, str] = {}
        if not slot.device_supported:
            unsupported["device"] = "provider_not_supported"
        if not features_supported:
            unsupported["serp_features"] = "not_exposed_by_fixture_or_api"
        return {
            "source": "serp",
            "provider": slot.provider,
            "property_id": slot.property_id,
            "effective_at": effective_at,
            "collection_timestamp": effective_at,
            "api_family": slot.api_family,
            "api_version": slot.api_version,
            "endpoint": request["endpoint"],
            "method": request["method"],
            "request_payload": dict(request["payload"]),
            "response_format": slot.response_format,
            "query_text": slot.keyword,
            "region": {"id": slot.region_id, "name": slot.region_name},
            "locale": slot.locale,
            "language": slot.language,
            "device": slot.device,
            "device_supported": slot.device_supported,
            "depth": slot.depth,
            "observation_count": slot.observation_count,
            "observation_slot": slot.observation_slot,
            "keyword_set_id": slot.keyword_set_id,
            "keyword_set_hash": slot.keyword_set_hash,
            "protocol_hash": slot.protocol_hash,
            "request_id": str(page.get("request_id") or "__all__"),
            "serp_features_supported": features_supported,
            "unsupported_metadata": unsupported,
            "quality": {
                "protocol_hash": slot.protocol_hash,
                "repeated_observation": slot.observation_count > 1,
            },
            "normalizer_version": "serp-v1",
        }

    def _rank_observation(
        self,
        slot: SerpProtocolSlot,
        metadata: dict[str, Any],
        row: dict[str, Any],
        *,
        raw_position: int,
        competitor_config: CompetitorConfig | None,
    ) -> dict[str, Any]:
        url = str(row.get("url") or "")
        host = normalized_host(url)
        competitor_metadata = (
            classify_domain(url, competitor_config, market_id=slot.market)
            if competitor_config is not None
            else _missing_competitor_identity()
        )
        return {
            "project_id": "__pending__",
            "property_id": slot.property_id,
            "source": "serp",
            "provider": slot.provider,
            "search_engine": slot.search_engine,
            "effective_at": metadata["effective_at"],
            "keyword_set_id": slot.keyword_set_id,
            "keyword_set_hash": slot.keyword_set_hash,
            "keyword": slot.keyword,
            "region_id": slot.region_id,
            "region_name": slot.region_name,
            "locale": slot.locale,
            "language": slot.language,
            "device": slot.device,
            "depth": slot.depth,
            "observation_count": slot.observation_count,
            "observation_slot": slot.observation_slot,
            "api_family": slot.api_family,
            "api_version": slot.api_version,
            "protocol_hash": slot.protocol_hash,
            "logical_observation_key": _logical_key(slot, host, _int_or_none(row.get("position"))),
            "rank": _int_or_none(row.get("position")),
            "url": url,
            "host": host,
            "domain": host,
            "title": _optional_str(row.get("title")),
            "snippet": _optional_str(row.get("snippet")),
            "serp_features": _feature_labels(row),
            "classification": competitor_metadata["classification"],
            "competitor_id": competitor_metadata["competitor_id"],
            "competitor_name": competitor_metadata["competitor_name"],
            "match_reason": competitor_metadata["match_reason"],
            "matched_pattern": competitor_metadata["matched_pattern"],
            "competitor_class": competitor_metadata["competitor_class"],
            "market": slot.market,
            "out_of_market": competitor_metadata["out_of_market"],
            "provenance_request_id": metadata["request_id"],
            "raw_result_position": raw_position,
            "slot_weight": slot.weight,
            "normalizer_version": "serp-v1",
        }


def generate_protocol_slots(keyword_set: SerpKeywordSet, source: SerpSource) -> list[SerpProtocolSlot]:
    _validate_source(source)
    keywords = normalized_keywords(keyword_set.keywords)
    regions = [_parse_region(region) for region in keyword_set.regions]
    devices = _normalized_devices(keyword_set.devices) if source.supports_devices else ["__all__"]
    count = source.observations_per_protocol_slot
    slots: list[SerpProtocolSlot] = []
    for keyword in keywords:
        for region in regions:
            for device in devices:
                for observation_slot in range(1, count + 1):
                    slots.append(
                        SerpProtocolSlot(
                            keyword_set_id=keyword_set.id,
                            keyword_set_hash=keyword_set_hash(keywords),
                            keyword=keyword,
                            region_id=region["id"],
                            region_name=region["name"],
                            locale=keyword_set.locale or source.locale,
                            language=source.language,
                            device=device,
                            search_engine=source.search_engine,
                            depth=source.result_depth,
                            observation_count=count,
                            observation_slot=observation_slot,
                            provider=source.provider,
                            api_family=source.api_family,
                            api_version=source.api_version,
                            endpoint=source.endpoint,
                            method=source.method,
                            response_format=source.response_format,
                            device_supported=source.supports_devices,
                            property_id=source.property_id,
                            weight=keyword_set.weight,
                            classification_config_hash=source.classification_config_hash,
                            date_bucket=source.date_bucket,
                            market=keyword_set.market,
                        )
                    )
    return slots


def derive_competitor_metrics(
    snapshots: list[list[dict[str, Any]]],
    *,
    competitor_config: CompetitorConfig,
    expected_slots: list[SerpProtocolSlot],
    minimum_weighted_coverage: float,
    depth: int,
    confirmation_observations: int = 1,
) -> dict[str, Any]:
    current = snapshots[-1] if snapshots else []
    expected_by_hash = {slot.protocol_hash: slot for slot in expected_slots}
    observed_hashes = {str(item.get("protocol_hash")) for item in current if item.get("protocol_hash")}
    total_weight = sum(slot.weight for slot in expected_slots) or 1.0
    observed_weight = sum(slot.weight for slot in expected_slots if slot.protocol_hash in observed_hashes)
    weighted_coverage = observed_weight / total_weight
    coverage = {
        "expected_slots": len(expected_slots),
        "observed_slots": len(observed_hashes & set(expected_by_hash)),
        "weighted_coverage": weighted_coverage,
        "coverage": (len(observed_hashes & set(expected_by_hash)) / len(expected_slots)) if expected_slots else 0.0,
    }
    below_threshold = weighted_coverage < minimum_weighted_coverage
    class_by_competitor_id = {
        competitor.id: competitor.competitor_class for competitor in competitor_config.competitors
    }
    competitor_metrics = _snapshot_competitor_metrics(
        current,
        depth,
        expected_slots=expected_slots,
        class_by_competitor_id=class_by_competitor_id,
    )
    movements = {}
    if len(snapshots) >= 2:
        baseline_metrics = _snapshot_competitor_metrics(
            snapshots[-2],
            depth,
            expected_slots=expected_slots,
            class_by_competitor_id=class_by_competitor_id,
        )
        movements = _rank_movements(
            baseline_metrics,
            competitor_metrics,
            current,
            confirmation_observations=confirmation_observations,
        )
    if below_threshold:
        for item in competitor_metrics.values():
            item["share_of_voice"] = None
            item["occupancy_share"] = None
        conclusion_status = "insufficient_coverage"
        comparability = "not_comparable"
    else:
        conclusion_status = "provisional"
        comparability = "comparable"
    return {
        "coverage": coverage,
        "conclusion_status": conclusion_status,
        "comparability": comparability,
        "competitors": competitor_metrics,
        "movements": movements,
        "quality": {
            "minimum_weighted_keyword_coverage": minimum_weighted_coverage,
            "repeated_observations": any(
                int(item.get("observation_count") or 1) > 1 for item in current
            ),
            "unstable_ranks": [
                competitor_id
                for competitor_id, item in competitor_metrics.items()
                if float(item["rank_stability"]) < 1.0
            ],
            "competitor_config": {
                "owned_domains": list(competitor_config.owned_domains),
                "competitors": [
                    {
                        "id": competitor.id,
                        "name": competitor.name,
                        "domain_patterns": list(competitor.domain_patterns),
                        "aliases": list(competitor.aliases),
                    }
                    for competitor in competitor_config.competitors
                ],
            },
        },
    }


def classify_domain(
    url_or_host: str, config: CompetitorConfig, *, market_id: str | None = None
) -> dict[str, Any]:
    """Classifies a domain from the SERP.

    Binding a competitor to markets (`markets`) does NOT filter classification:
    a domain found in a foreign market is marked `out_of_market` but stays in
    the report. Silent filtering would hide the more common case — a wrong
    binding in the config when the competitor actually works in both markets
    (#1677).
    """
    host = normalized_host(url_or_host)
    for pattern in config.owned_domains:
        if _domain_matches(host, pattern):
            return {
                "classification": "owned",
                "competitor_id": "__owned__",
                "competitor_name": "Owned",
                "match_reason": "owned_domain_pattern",
                "matched_pattern": pattern,
                "competitor_class": "owned",
                "out_of_market": False,
            }
    for competitor in config.competitors:
        for pattern in competitor.domain_patterns:
            if _domain_matches(host, pattern):
                return {
                    "classification": "competitor",
                    "competitor_id": competitor.id,
                    "competitor_name": competitor.name,
                    "match_reason": "competitor_domain_pattern",
                    "matched_pattern": pattern.lstrip("*.") if pattern.startswith("*.") else pattern,
                    "competitor_class": competitor.competitor_class,
                    "out_of_market": bool(
                        market_id and competitor.markets and market_id not in competitor.markets
                    ),
                }
    return {
        "classification": "unclassified",
        "competitor_id": "__unclassified__",
        "competitor_name": None,
        "match_reason": "no_configured_match",
        "matched_pattern": None,
        "competitor_class": "unknown",
        "out_of_market": False,
    }


def doctor_serp_source(
    source_fields: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    live: bool = False,
) -> dict[str, Any]:
    env = env or {}
    errors: list[str] = []
    provider = source_fields.get("provider")
    api_family = source_fields.get("api_family", DEFAULT_API_FAMILY)
    api_version = source_fields.get("api_version", DEFAULT_API_VERSION)
    credential_env = source_fields.get("credential_env")
    depth = source_fields.get("result_depth", 10)
    config_shape = True
    if provider not in DOCTOR_SUPPORTED_PROVIDERS:
        errors.append(
            "provider must be one of: " + ", ".join(sorted(DOCTOR_SUPPORTED_PROVIDERS))
        )
        config_shape = False
    if api_family not in SUPPORTED_API_FAMILIES:
        errors.append("api_family must be one of: " + ", ".join(sorted(SUPPORTED_API_FAMILIES)))
        config_shape = False
    if not isinstance(api_version, str) or not api_version:
        errors.append("api_version must be a non-empty string")
        config_shape = False
    if credential_env is not None and (not isinstance(credential_env, str) or not credential_env):
        errors.append("credential_env must be a non-empty string")
        config_shape = False
    if not isinstance(depth, int) or isinstance(depth, bool) or depth <= 0:
        errors.append("result_depth must be a positive integer")
        config_shape = False
    token_present = bool(credential_env and env.get(credential_env))
    if credential_env and not token_present:
        errors.append("credential token is not present")
    return {
        "ok": config_shape and (token_present if credential_env else True),
        "live_checked": bool(live and False),
        "checks": {
            "config_shape": config_shape,
            "token_present": token_present,
            "no_browser_automation": not PROVIDER_DRIVES_BROWSER.get(str(provider), True),
            "no_public_search_scraping": api_family in SUPPORTED_API_FAMILIES,
            "no_captcha_bypass": True,
            "no_paid_api_call": True,
        },
        "errors": errors,
    }


def keyword_set_hash(keywords: tuple[str, ...] | list[str]) -> str:
    normalized = normalized_keywords(keywords)
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"kwsha256:{hashlib.sha256(encoded).hexdigest()}"


def protocol_hash(inputs: dict[str, Any]) -> str:
    payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"serpproto:{hashlib.sha256(payload).hexdigest()}"


def normalized_keywords(keywords: tuple[str, ...] | list[str]) -> list[str]:
    unique = {_normalize_keyword(keyword) for keyword in keywords if _normalize_keyword(keyword)}
    return sorted(unique)


def normalized_host(url_or_host: str) -> str:
    value = url_or_host.strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").casefold().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _snapshot_competitor_metrics(
    observations: list[dict[str, Any]],
    depth: int,
    *,
    expected_slots: list[SerpProtocolSlot],
    class_by_competitor_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    expected_slot_count = len(expected_slots)
    expected_weight_by_hash = {slot.protocol_hash: float(slot.weight) for slot in expected_slots}
    total_expected_weight = sum(expected_weight_by_hash.values()) or 1.0
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        competitor_id = str(item.get("competitor_id") or "__unclassified__")
        grouped.setdefault(competitor_id, []).append(item)
    metrics: dict[str, Any] = {}
    for competitor_id, items in grouped.items():
        ranks = [rank for rank in (_int_or_none(item.get("rank")) for item in items) if rank is not None]
        scores = [
            _visibility_score(_int_or_none(item.get("rank")) or 0, depth)
            * expected_weight_by_hash.get(str(item.get("protocol_hash")), 0.0)
            for item in items
        ]
        classification = str(items[0].get("classification") or "unclassified")
        competitor_class = _resolve_competitor_class(
            competitor_id, items, classification, class_by_competitor_id
        )
        score_sum = sum(scores)
        visibility = score_sum / total_expected_weight if total_expected_weight else 0.0
        rank_stability = _rank_stability(ranks)
        metrics[competitor_id] = {
            "competitor_id": competitor_id,
            "competitor_name": items[0].get("competitor_name"),
            "classification": classification,
            "competitor_class": competitor_class,
            "contestable": _is_contestable(classification, competitor_class),
            "out_of_market": any(bool(item.get("out_of_market")) for item in items),
            "out_of_market_observation_count": sum(
                1 for item in items if item.get("out_of_market")
            ),
            "coverage": (len({str(item.get("protocol_hash")) for item in items}) / expected_slot_count)
            if expected_slot_count
            else 0.0,
            "weighted_coverage": _weighted_item_coverage(
                items,
                expected_weight_by_hash=expected_weight_by_hash,
                total_expected_weight=total_expected_weight,
            ),
            "visibility": visibility,
            "share_of_voice": visibility,
            "best_rank": min(ranks) if ranks else None,
            "median_rank": float(median(ranks)) if ranks else None,
            "rank_stability": rank_stability,
            "rank_stability_label": "stable" if rank_stability == 1.0 else "unstable",
            "observation_count": len(items),
            "match_reasons": sorted(
                {
                    f"{item.get('match_reason') or 'unknown'}:{item.get('matched_pattern') or '__none__'}"
                    for item in items
                }
            ),
            "_visibility_score_sum": score_sum,
        }
    score_sums = {
        competitor_id: float(item.pop("_visibility_score_sum", 0.0))
        for competitor_id, item in metrics.items()
    }
    # Two quantities answer two different questions and therefore have
    # different denominators: share_of_voice — "whose traffic can we take",
    # occupancy_share — "who occupies the SERP at all" (#1676).
    contestable_score = sum(
        score for competitor_id, score in score_sums.items() if metrics[competitor_id]["contestable"]
    )
    occupied_score = sum(
        score
        for competitor_id, score in score_sums.items()
        if metrics[competitor_id].get("classification") in {"owned", "competitor"}
    )
    for item in metrics.values():
        visibility_sum = score_sums[str(item["competitor_id"])]
        item["share_of_voice"] = (
            visibility_sum / contestable_score if contestable_score and item["contestable"] else 0.0
        )
        item["occupancy_share"] = (
            visibility_sum / occupied_score
            if occupied_score and item.get("classification") in {"owned", "competitor"}
            else 0.0
        )
    return metrics


def _is_contestable(classification: str, competitor_class: str) -> bool:
    if classification == "owned":
        return True
    return classification == "competitor" and competitor_class not in NON_CONTESTABLE_CLASSES


def _resolve_competitor_class(
    competitor_id: str,
    items: list[dict[str, Any]],
    classification: str,
    class_by_competitor_id: dict[str, str] | None,
) -> str:
    """The class is taken from the config, not from the observation.

    Snapshots taken before the field existed carry no class, but recomputing
    them against the current config is correct: the class is a property of the
    competitor, not of the measurement.
    """
    if classification == "owned":
        return "owned"
    if class_by_competitor_id and competitor_id in class_by_competitor_id:
        return class_by_competitor_id[competitor_id]
    recorded = items[0].get("competitor_class")
    return str(recorded) if recorded else "unknown"


def _rank_movements(
    baseline: dict[str, Any],
    current: dict[str, Any],
    current_observations: list[dict[str, Any]],
    *,
    confirmation_observations: int,
) -> dict[str, Any]:
    movements: dict[str, Any] = {}
    for competitor_id, current_item in current.items():
        baseline_item = baseline.get(competitor_id)
        if not baseline_item:
            movements[competitor_id] = {"status": "new", "movement": None, "confirming_observations": 0}
            continue
        baseline_rank = baseline_item.get("median_rank")
        current_rank = current_item.get("median_rank")
        if baseline_rank is None or current_rank is None:
            continue
        movement = float(current_rank) - float(baseline_rank)
        confirming = _confirming_observations(
            competitor_id,
            current_observations,
            baseline_rank=float(baseline_rank),
            movement=movement,
        )
        status = "confirmed" if confirming >= confirmation_observations else "requires_confirmation"
        if movement == 0:
            status = "unchanged"
        movements[competitor_id] = {
            "baseline_median_rank": baseline_rank,
            "current_median_rank": current_rank,
            "movement": movement,
            "confirming_observations": confirming,
            "required_confirmation_observations": confirmation_observations,
            "status": status,
        }
    for competitor_id in sorted(set(baseline) - set(current)):
        movements[competitor_id] = {
            "status": "disappeared",
            "movement": None,
            "confirming_observations": 0,
        }
    return movements


def _confirming_observations(
    competitor_id: str,
    observations: list[dict[str, Any]],
    *,
    baseline_rank: float,
    movement: float,
) -> int:
    if movement == 0:
        return 0
    confirming = 0
    for item in observations:
        if str(item.get("competitor_id") or "__unclassified__") != competitor_id:
            continue
        rank = _int_or_none(item.get("rank"))
        if rank is None:
            continue
        if movement < 0 and rank < baseline_rank:
            confirming += 1
        if movement > 0 and rank > baseline_rank:
            confirming += 1
    return confirming


def _weighted_item_coverage(
    items: list[dict[str, Any]],
    *,
    expected_weight_by_hash: dict[str, float],
    total_expected_weight: float,
) -> float:
    observed_hashes = {str(item.get("protocol_hash")) for item in items if item.get("protocol_hash")}
    observed_weight = sum(
        weight
        for protocol_hash, weight in expected_weight_by_hash.items()
        if protocol_hash in observed_hashes
    )
    return observed_weight / total_expected_weight if total_expected_weight else 0.0


def _rank_stability(ranks: list[int]) -> float:
    if not ranks:
        return 1.0
    return 1 - (len(set(ranks)) - 1) / max(1, len(ranks) - 1)


def _missing_competitor_identity() -> dict[str, Any]:
    return {
        "classification": "unclassified",
        "competitor_id": "__unclassified__",
        "competitor_name": None,
        "match_reason": "competitor_config_missing",
        "matched_pattern": None,
        "competitor_class": "unknown",
        "out_of_market": False,
    }


def _visibility_score(rank: int, depth: int) -> float:
    if rank <= 0 or rank > depth:
        return 0.0
    return (depth - rank + 1) / depth


def _validate_source(source: SerpSource) -> None:
    if source.provider not in SUPPORTED_PROVIDERS:
        raise SerpRequestError("SERP provider is not supported.")
    if source.api_family not in SUPPORTED_API_FAMILIES:
        raise SerpRequestError("SERP api_family is not supported.")
    if source.result_depth <= 0:
        raise SerpRequestError("SERP result_depth must be positive.")
    if source.observations_per_protocol_slot <= 0:
        raise SerpRequestError("SERP observations_per_protocol_slot must be positive.")


def _normalize_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _parse_region(value: str) -> dict[str, str]:
    region_id, separator, name = value.partition(":")
    if separator:
        return {"id": region_id, "name": name}
    return {"id": value, "name": value}


def _normalized_devices(devices: tuple[str, ...]) -> list[str]:
    values = {device.strip().casefold() for device in devices if device.strip()}
    return sorted(values or {"__all__"})


def _rows(page: dict[str, Any]) -> list[dict[str, Any]]:
    rows = page.get("results", [])
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise SerpRequestError("SERP response results must be a list.")
    return [row for row in rows if isinstance(row, dict)]


def _feature_labels(row: dict[str, Any]) -> list[str]:
    features = row.get("features", row.get("serp_features", []))
    if not isinstance(features, list):
        return []
    return [str(item) for item in features if str(item)]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _logical_key(slot: SerpProtocolSlot, host: str, rank: int | None) -> str:
    payload = "|".join(
        [
            slot.property_id,
            slot.keyword_set_id,
            slot.keyword_set_hash,
            slot.protocol_hash,
            host,
            str(rank or "__all__"),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"serp:{slot.property_id}:{slot.keyword_set_id}:{digest}"


def _domain_matches(host: str, pattern: str) -> bool:
    normalized_pattern = pattern.casefold().strip()
    if normalized_pattern.startswith("*."):
        suffix = normalized_pattern[2:]
        return host.endswith(f".{suffix}") and host != suffix
    return host == normalized_pattern or fnmatch.fnmatch(host, normalized_pattern)
