from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol


DEFAULT_WORDSTAT_ENDPOINT = "/search-api/v1/wordstat"
DEFAULT_API_FAMILY = "search_api_wordstat"
DEFAULT_API_VERSION = "v1"
SUPPORTED_API_FAMILIES = frozenset({"search_api_wordstat", "direct_wordstat_report"})


class WordstatRequestError(ValueError):
    pass


class WordstatTransport(Protocol):
    def post_json(
        self,
        endpoint: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class WordstatPeriod:
    date_from: str
    date_to: str

    def validate(self) -> None:
        if not self.date_from or not self.date_to:
            raise WordstatRequestError("Wordstat temporal API calls require explicit date_from/date_to.")


@dataclass(frozen=True)
class KeywordSetPayload:
    id: str
    keywords: tuple[str, ...]
    locale: str
    regions: tuple[str, ...]
    devices: tuple[str, ...] = ("__all__",)


@dataclass(frozen=True)
class WordstatSource:
    token: str
    property_id: str
    api_family: str = DEFAULT_API_FAMILY
    api_version: str = DEFAULT_API_VERSION
    locale: str = "ru_RU"
    language: str = "ru"
    endpoint: str = DEFAULT_WORDSTAT_ENDPOINT
    supports_devices: bool = False
    finalize_after: str | None = None


class WordstatAdapter:
    def __init__(self, source: WordstatSource, transport: WordstatTransport) -> None:
        if source.api_family not in SUPPORTED_API_FAMILIES:
            raise WordstatRequestError("Wordstat api_family is not supported.")
        self.source = source
        self.transport = transport

    def fetch_demand(
        self,
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod | None,
    ) -> dict[str, Any]:
        period = _require_period(period)
        request = self.request_descriptor(keyword_set, period)
        page = self._post(request)
        rows = _rows(page, "rows")
        metadata = self._metadata(keyword_set, period, request, page, rows, collection="demand_metrics")
        observations = [self._demand_observation(row, keyword_set, period, metadata) for row in rows]
        _attach_quality_summary(metadata, observations)
        return {"collection": "demand_metrics", "metadata": metadata, "observations": observations}

    def fetch_related(
        self,
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod | None,
    ) -> dict[str, Any]:
        period = _require_period(period)
        request = self.request_descriptor(keyword_set, period)
        page = self._post(request)
        rows = _rows(page, "related")
        deduped_rows, deduped_count = _dedupe_related(rows)
        metadata = self._metadata(keyword_set, period, request, page, deduped_rows, collection="related_queries")
        metadata["dedupe_rule"] = "source_keyword+normalized_related_query+relation_type+region+device"
        metadata["deduped_rows"] = deduped_count
        observations = [
            self._related_observation(row, keyword_set, period, metadata) for row in deduped_rows
        ]
        return {"collection": "related_queries", "metadata": metadata, "observations": observations}

    def fetch_history(
        self,
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod | None,
    ) -> dict[str, Any]:
        period = _require_period(period)
        request = self.request_descriptor(keyword_set, period)
        page = self._post(request)
        rows = [
            row
            for row in _rows(page, "history")
            if isinstance(row.get("bucket_month") or row.get("bucket_start"), str)
        ]
        metadata = self._metadata(keyword_set, period, request, page, rows, collection="demand_history")
        metadata["history_bucket_supported"] = bool(rows)
        observations = [self._history_observation(row, keyword_set, period, metadata) for row in rows]
        metadata["yoy_suitable"] = bool(observations) and all(
            bool(item["yoy_suitable"]) for item in observations
        )
        return {"collection": "demand_history", "metadata": metadata, "observations": observations}

    def request_descriptor(
        self,
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod,
    ) -> dict[str, Any]:
        period.validate()
        keywords = normalized_keywords(keyword_set.keywords)
        regions = [_parse_region(region) for region in keyword_set.regions]
        payload: dict[str, Any] = {
            "keywords": keywords,
            "regions": regions,
            "locale": keyword_set.locale or self.source.locale,
            "language": self.source.language,
            "date_from": period.date_from,
            "date_to": period.date_to,
            "period": {"date_from": period.date_from, "date_to": period.date_to},
            "api_family": self.source.api_family,
            "api_version": self.source.api_version,
            "keyword_set_id": keyword_set.id,
            "keyword_set_hash": keyword_set_hash(keywords),
        }
        if self.source.supports_devices:
            payload["devices"] = list(keyword_set.devices)
        return {"endpoint": self.source.endpoint, "payload": payload}

    def _post(self, request: dict[str, Any]) -> dict[str, Any]:
        page = self.transport.post_json(
            str(request["endpoint"]),
            json=dict(request["payload"]),
            headers={"Authorization": f"OAuth {self.source.token}"},
        )
        if not isinstance(page, dict):
            raise WordstatRequestError("Wordstat transport returned a non-object response.")
        return page

    def _metadata(
        self,
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod,
        request: dict[str, Any],
        page: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        collection: str,
    ) -> dict[str, Any]:
        payload = dict(request["payload"])
        return {
            "source": "yandex_wordstat",
            "collection": collection,
            "property_id": self.source.property_id,
            "effective_start": period.date_from,
            "effective_end": period.date_to,
            "requested_period": {"date_from": period.date_from, "date_to": period.date_to},
            "requested_regions": payload["regions"],
            "locale": payload["locale"],
            "language": payload["language"],
            "requested_devices": list(keyword_set.devices),
            "device_supported": self.source.supports_devices,
            "api_family": self.source.api_family,
            "api_version": self.source.api_version,
            "request_payload": payload,
            "request_id": str(page.get("request_id") or "__all__"),
            "keyword_set_id": keyword_set.id,
            "keyword_set_hash": payload["keyword_set_hash"],
            "keyword_set_hash_source": "normalized_keyword_contents",
            "comparability": "comparable",
            "dataset_coverage": "empty" if not rows else "complete",
            "freshness": "provisional" if self.source.finalize_after else "final",
            "rows_received": len(rows),
            "finalize_after": self.source.finalize_after,
            "normalizer_version": "wordstat-v1",
            "quality": {"ambiguity_caveats": []},
        }

    def _demand_observation(
        self,
        row: dict[str, Any],
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        demand = _int_or_none(row.get("demand", row.get("shows", row.get("count"))))
        status = str(row.get("status") or "").lower()
        if demand == 0:
            demand_state = "zero"
        elif demand is not None:
            demand_state = "present"
        elif status == "missing":
            demand_state = "missing"
        else:
            demand_state = "unavailable"
        keyword = _normalize_keyword(str(row.get("keyword") or row.get("query") or ""))
        region = _region_from_row(row)
        device = _device_from_row(row, metadata)
        quality = _quality_for_keyword(keyword, row)
        return {
            **self._base_observation(keyword, keyword_set, period, metadata, region, device),
            "demand": demand,
            "demand_state": demand_state,
            "unavailable_reason": None if demand_state != "unavailable" else str(row.get("reason") or "__all__"),
            "quality": quality,
            "logical_key": _logical_key(
                self.source.property_id,
                keyword_set.id,
                metadata["keyword_set_hash"],
                keyword,
                region["id"],
                metadata["locale"],
                device,
            ),
        }

    def _related_observation(
        self,
        row: dict[str, Any],
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        source_keyword = _normalize_keyword(str(row.get("source_keyword") or row.get("keyword") or ""))
        related_query = _normalize_keyword(str(row.get("query") or row.get("related_query") or ""))
        region = _region_from_row(row)
        device = _device_from_row(row, metadata)
        return {
            **self._base_observation(source_keyword, keyword_set, period, metadata, region, device),
            "source_keyword": source_keyword,
            "related_query": related_query,
            "relation_type": str(row.get("relation_type") or "related"),
            "requested_region": region,
            "source_keyword_set_hash": metadata["keyword_set_hash"],
            "provenance_request_id": metadata["request_id"],
            "rank": _int_or_none(row.get("rank") or row.get("order")),
            "dedupe_rule": metadata["dedupe_rule"],
        }

    def _history_observation(
        self,
        row: dict[str, Any],
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        keyword = _normalize_keyword(str(row.get("keyword") or row.get("query") or ""))
        region = _region_from_row(row)
        device = _device_from_row(row, metadata)
        bucket_month = row.get("bucket_month")
        bucket_start = row.get("bucket_start")
        bucket_end = row.get("bucket_end")
        return {
            **self._base_observation(keyword, keyword_set, period, metadata, region, device),
            "bucket_month": str(bucket_month) if bucket_month is not None else None,
            "bucket_start": str(bucket_start) if bucket_start is not None else None,
            "bucket_end": str(bucket_end) if bucket_end is not None else None,
            "demand": _int_or_none(row.get("demand", row.get("shows", row.get("count")))),
            "yoy_suitable": bool(bucket_month),
        }

    def _base_observation(
        self,
        keyword: str,
        keyword_set: KeywordSetPayload,
        period: WordstatPeriod,
        metadata: dict[str, Any],
        region: dict[str, str],
        device: str,
    ) -> dict[str, Any]:
        return {
            "project_id": "__pending__",
            "property_id": self.source.property_id,
            "source": "yandex_wordstat",
            "search_engine": "yandex",
            "effective_start": period.date_from,
            "effective_end": period.date_to,
            "keyword_set_id": keyword_set.id,
            "keyword_set_hash": metadata["keyword_set_hash"],
            "keyword": keyword,
            "region_id": region["id"],
            "region_name": region["name"],
            "locale": metadata["locale"],
            "language": metadata["language"],
            "device": device,
            "api_family": metadata["api_family"],
            "api_version": metadata["api_version"],
            "provenance_request_id": metadata["request_id"],
            "dataset_coverage": metadata["dataset_coverage"],
            "freshness": metadata["freshness"],
            "comparability": metadata["comparability"],
            "normalizer_version": "wordstat-v1",
        }


def doctor_wordstat_source(
    source_fields: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    live: bool = False,
) -> dict[str, Any]:
    env = env or {}
    errors: list[str] = []
    credential_env = source_fields.get("credential_env")
    api_family = source_fields.get("api_family", DEFAULT_API_FAMILY)
    api_version = source_fields.get("api_version", DEFAULT_API_VERSION)
    config_shape = True
    if credential_env is not None and (not isinstance(credential_env, str) or not credential_env):
        errors.append("credential_env must be a non-empty string")
        config_shape = False
    if not isinstance(api_family, str) or api_family not in SUPPORTED_API_FAMILIES:
        errors.append("api_family must be search_api_wordstat or direct_wordstat_report")
        config_shape = False
    if not isinstance(api_version, str) or not api_version:
        errors.append("api_version must be a non-empty string")
        config_shape = False
    token_present = bool(credential_env and env.get(credential_env))
    if not token_present:
        errors.append("credential token is not present")
    return {
        "ok": config_shape and token_present,
        "live_checked": bool(live and False),
        "checks": {
            "config_shape": config_shape,
            "token_present": token_present,
        },
        "errors": errors,
    }


def keyword_set_hash(keywords: tuple[str, ...] | list[str]) -> str:
    normalized = normalized_keywords(keywords)
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"kwsha256:{hashlib.sha256(encoded).hexdigest()}"


def compare_keyword_set_hashes(baseline_hash: str, current_hash: str) -> dict[str, Any]:
    if baseline_hash == current_hash:
        return {
            "comparable": True,
            "comparability": "comparable",
            "baseline_keyword_set_hash": baseline_hash,
            "current_keyword_set_hash": current_hash,
        }
    return {
        "comparable": False,
        "comparability": "keyword_set_changed",
        "baseline_keyword_set_hash": baseline_hash,
        "current_keyword_set_hash": current_hash,
    }


def normalized_keywords(keywords: tuple[str, ...] | list[str]) -> list[str]:
    unique = {_normalize_keyword(keyword) for keyword in keywords if _normalize_keyword(keyword)}
    return sorted(unique)


def _require_period(period: WordstatPeriod | None) -> WordstatPeriod:
    if period is None:
        raise WordstatRequestError("Wordstat temporal API calls require explicit date_from/date_to.")
    period.validate()
    return period


def _normalize_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _display_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _parse_region(value: str) -> dict[str, str]:
    region_id, separator, name = value.partition(":")
    if separator:
        return {"id": region_id, "name": name}
    return {"id": value, "name": value}


def _region_from_row(row: dict[str, Any]) -> dict[str, str]:
    region_id = row.get("region_id")
    region_name = row.get("region_name")
    if region_id is None and isinstance(row.get("region"), dict):
        region = row["region"]
        region_id = region.get("id")
        region_name = region.get("name")
    return {"id": str(region_id or "__all__"), "name": str(region_name or region_id or "__all__")}


def _device_from_row(row: dict[str, Any], metadata: dict[str, Any]) -> str:
    if not metadata["device_supported"]:
        return "__all__"
    return str(row.get("device") or "__all__").lower()


def _rows(page: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = page.get(key, [])
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise WordstatRequestError(f"Wordstat response {key} must be a list.")
    return [row for row in rows if isinstance(row, dict)]


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


def _quality_for_keyword(keyword: str, row: dict[str, Any]) -> dict[str, bool]:
    ambiguity = str(row.get("ambiguity") or "").lower()
    provider_flags = row.get("ambiguity_flags", [])
    provider_ambiguous = bool(ambiguity and ambiguity != "none") or (
        isinstance(provider_flags, list) and bool(provider_flags)
    )
    return {
        "ambiguous_query": provider_ambiguous,
        "broad_non_exact_phrase": not _is_exact_phrase(keyword),
    }


def _is_exact_phrase(keyword: str) -> bool:
    return len(keyword) >= 2 and keyword.startswith('"') and keyword.endswith('"')


def _attach_quality_summary(metadata: dict[str, Any], observations: list[dict[str, Any]]) -> None:
    caveats: list[str] = []
    if any(item.get("quality", {}).get("ambiguous_query") for item in observations):
        caveats.append("homonym_or_provider_ambiguous")
    if any(item.get("quality", {}).get("broad_non_exact_phrase") for item in observations):
        caveats.append("broad_non_exact_phrase")
    metadata["quality"]["ambiguity_caveats"] = caveats


def _dedupe_related(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[tuple[str, str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        region = _region_from_row(row)
        key = (
            _normalize_keyword(str(row.get("source_keyword") or row.get("keyword") or "")),
            _normalize_keyword(str(row.get("query") or row.get("related_query") or "")),
            str(row.get("relation_type") or "related"),
            region["id"],
            str(row.get("device") or "__all__").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped, len(rows) - len(deduped)


def _logical_key(
    property_id: str,
    keyword_set_id: str,
    keyword_hash: str,
    keyword: str,
    region_id: str,
    locale: str,
    device: str,
) -> str:
    payload = "|".join([property_id, keyword_set_id, keyword_hash, keyword, region_id, locale, device])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"wordstat:{property_id}:{keyword_set_id}:{digest}"
