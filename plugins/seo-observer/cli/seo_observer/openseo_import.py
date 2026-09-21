from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_WINDOW_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class WrittenOpenSEOEvidence:
    path: Path
    relative_path: str
    sha256: str


def import_openseo_evidence(
    envelope: dict[str, Any],
    *,
    project_id: str,
    previous_keyword_basket_hash: str | None = None,
) -> dict[str, Any]:
    window = _window(envelope.get("window"))
    keywords = _keywords(envelope.get("saved_keywords"))
    basket_hash = keyword_basket_hash(keywords)
    rank_tracker = envelope.get("rank_tracker") if isinstance(envelope.get("rank_tracker"), dict) else {}
    rank_evidence, errors = _rank_evidence(rank_tracker)
    keyword_evidence = _keyword_evidence(envelope.get("saved_keywords"))
    text_fallback = _text_fallback(rank_tracker)
    has_previous_basket = previous_keyword_basket_hash is not None
    comparability = _comparability(basket_hash, previous_keyword_basket_hash, has_previous_basket)
    quality = _quality(envelope, rank_evidence, keyword_evidence, errors, text_fallback, comparability)
    mcp_meta = envelope.get("mcp") if isinstance(envelope.get("mcp"), dict) else {}

    imported: dict[str, Any] = {
        "schema_version": 1,
        "source": "openseo_mcp",
        "project_id": project_id,
        "provider_project": dict(envelope.get("project")) if isinstance(envelope.get("project"), dict) else {},
        "mcp": {
            "server_version": mcp_meta.get("server_version"),
            "commit": mcp_meta.get("commit"),
        },
        "rank_tracker_id": rank_tracker.get("id") if isinstance(rank_tracker.get("id"), str) else None,
        "window": window,
        "keyword_basket": {"hash": basket_hash, "keywords": keywords},
        "comparability": comparability,
        "quality": quality,
        "rank_evidence": rank_evidence,
        "keyword_evidence": keyword_evidence,
        "errors": errors,
    }
    if text_fallback:
        imported["raw_text"] = text_fallback
    imported["artifact"] = {
        "sha256": _sha256_json({key: value for key, value in imported.items() if key != "artifact"}),
    }
    return imported


def write_openseo_evidence(imported: dict[str, Any], *, output_dir: Path) -> WrittenOpenSEOEvidence:
    window = imported["window"]
    start = _window_path_component(window["start"])
    end = _window_path_component(window["end"])
    project_id = _path_component(imported["project_id"], label="project_id")
    relative_path = (
        "openseo/v1/"
        f"{project_id}/"
        f"{start}_{end}/"
        f"evidence-{imported['artifact']['sha256'][:16]}.json"
    )
    output_root = output_dir.resolve()
    path = (output_dir / relative_path).resolve()
    if output_root != path and output_root not in path.parents:
        raise ValueError("OpenSEO evidence path escapes output_dir.")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _canonical_json(imported) + "\n"
    path.write_text(text, encoding="utf-8")
    return WrittenOpenSEOEvidence(
        path=path,
        relative_path=relative_path,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def keyword_basket_hash(keywords: list[str]) -> str:
    canonical = {_canonicalize_keyword(keyword) for keyword in keywords}
    return "kwbasket:" + _sha256_json({"keywords": sorted(canonical)})


def _canonicalize_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _window(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "start": str(value.get("start") or ""),
            "end": str(value.get("end") or ""),
            "timezone": str(value.get("timezone") or "UTC"),
        }
    return {"start": "", "end": "", "timezone": "UTC"}


def _keywords(value: Any) -> list[str]:
    keywords: list[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            keyword = str(item.get("keyword") or "").strip()
        else:
            keyword = str(item).strip()
        if keyword:
            keywords.append(keyword)
    return list(dict.fromkeys(keywords))


def _keyword_evidence(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        keyword = str(item.get("keyword") or "").strip()
        if not keyword:
            continue
        tags = item.get("tags")
        rows.append(
            {
                "keyword": keyword,
                "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
            }
        )
    return rows


def _rank_evidence(rank_tracker: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if rank_tracker.get("structured") is False:
        return [], []
    rows = rank_tracker.get("rank_rows")
    if not isinstance(rows, list):
        return [], [{"code": "OPENSEO_MISSING_RANK_ROWS"}]
    engine = str(rank_tracker.get("engine") or "")
    locale = str(rank_tracker.get("locale") or "")
    device = str(rank_tracker.get("device") or "")
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        keyword = str(row.get("keyword") or "").strip() if isinstance(row, dict) else ""
        position = row.get("position") if isinstance(row, dict) else None
        valid_position = isinstance(position, int) and not isinstance(position, bool) and position > 0
        if not isinstance(row, dict) or not keyword or not valid_position:
            errors.append({"code": "OPENSEO_MALFORMED_RANK_ROW", "index": index})
            continue
        observations.append(
            {
                "keyword": keyword,
                "position": int(position),
                "url": str(row.get("url") or ""),
                "engine": engine,
                "locale": locale,
                "device": device,
            }
        )
    return observations, errors


def _text_fallback(rank_tracker: dict[str, Any]) -> str:
    if rank_tracker.get("structured") is False:
        return str(rank_tracker.get("text") or "")
    return ""


def _comparability(
    keyword_basket_hash_value: str,
    previous_keyword_basket_hash: str | None,
    has_previous_basket: bool,
) -> dict[str, Any]:
    if not has_previous_basket:
        return {"status": "insufficient_history", "reason": "no_previous_basket"}
    if previous_keyword_basket_hash != keyword_basket_hash_value:
        return {
            "status": "not_comparable",
            "reason": "keyword_basket_changed",
            "previous_keyword_basket_hash": previous_keyword_basket_hash,
        }
    return {"status": "comparable", "reason": "same_keyword_basket"}


def _quality(
    envelope: dict[str, Any],
    rank_evidence: list[dict[str, Any]],
    keyword_evidence: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    text_fallback: str,
    comparability: dict[str, Any],
) -> str:
    if comparability["status"] in ("not_comparable", "insufficient_history"):
        return comparability["status"]
    if text_fallback or errors or not rank_evidence or not keyword_evidence:
        return "partial"
    quality = str(envelope.get("quality") or "complete")
    return quality if quality in {"complete", "partial", "stale", "missing"} else "partial"


def _window_path_component(value: str) -> str:
    if not _WINDOW_DATE_RE.fullmatch(value):
        raise ValueError("Invalid OpenSEO evidence window date for filesystem path.")
    return value


def _path_component(value: str, *, label: str) -> str:
    if not value or "/" in value or "\\" in value or value in (".", "..") or "\x00" in value:
        raise ValueError(f"Invalid OpenSEO evidence {label} for filesystem path.")
    return value


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
