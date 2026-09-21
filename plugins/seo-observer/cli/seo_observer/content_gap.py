from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from seo_observer.config import ProjectConfig, compute_config_hash, observer_home


REPORT_COMMAND = "competitors.report"
DETERMINISTIC_QUALITIES = {"live", "partial"}
RANK_CLAIM_TYPES = {"rank", "traffic", "sov", "trend"}
URL_RE = re.compile(r"https?://[^\s)>,`\"']+")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9:/.-])-?\d+(?:\.\d+)?(?![A-Za-z0-9:/.-])")


class BriefAdapter(Protocol):
    def synthesize(self, evidence_packet: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclasses.dataclass(frozen=True)
class ContentGapOptions:
    provider_mode: str = "artifact"
    output_dir: Path | None = None
    discovery_artifact: Path | None = None
    serp_artifact: Path | None = None
    content_artifact: Path | None = None
    allow_paid: bool = False
    with_brief: bool = False
    llm_fixture: Path | None = None


@dataclasses.dataclass(frozen=True)
class Citation:
    citation_id: str
    source_type: str
    quality: str
    url: str | None
    row: dict[str, Any]
    deterministic: bool


@dataclasses.dataclass(frozen=True)
class BriefValidation:
    ok: bool
    errors: list[dict[str, Any]]


@dataclasses.dataclass(frozen=True)
class BriefFixtureAdapter:
    fixture_path: Path

    def synthesize(self, evidence_packet: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(Path(self.fixture_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "claims": [],
                "errors": [_error("LLM_FIXTURE_INVALID", "Brief fixture could not be read.", {"error_type": exc.__class__.__name__})],
            }
        return value if isinstance(value, dict) else {"claims": []}


class _CitationIndexBuilder:
    def __call__(
        self,
        discovery: dict[str, Any],
        serp: dict[str, Any],
        content: dict[str, Any],
    ) -> dict[str, Citation]:
        citations: dict[str, Citation] = {}
        for gap in _dict_rows(discovery.get("keyword_gaps")):
            quality = str(gap.get("quality") or discovery.get("quality_summary", {}).get("overall") or "unknown")
            for citation_id in _citation_ids(gap):
                citations[citation_id] = Citation(
                    citation_id=citation_id,
                    source_type="keyword_gap",
                    quality=quality,
                    url=_clean_url(gap.get("representative_competitor_url")),
                    row=gap,
                    deterministic=quality in DETERMINISTIC_QUALITIES,
                )
        for row in _dict_rows(serp.get("serp_rows")):
            citation_id = _serp_citation_id(row)
            quality = str(row.get("quality") or serp.get("quality_summary", {}).get("overall") or "unknown")
            citations[citation_id] = Citation(
                citation_id=citation_id,
                source_type="serp",
                quality=quality,
                url=_clean_url(row.get("url")),
                row=row,
                deterministic=quality in DETERMINISTIC_QUALITIES,
            )
        for page in _dict_rows(content.get("page_extracts")):
            citation_id = str(page.get("citation_id") or f"page:{_hash(str(page.get('url') or 'page'))}")
            quality = str(page.get("quality") or "research-only")
            citations[citation_id] = Citation(
                citation_id=citation_id,
                source_type="page",
                quality=quality,
                url=_clean_url(page.get("url") or page.get("canonical_url")),
                row=page,
                deterministic=quality in DETERMINISTIC_QUALITIES,
            )
        return citations

    def from_artifact_paths(self, discovery_path: Path, serp_path: Path, content_path: Path) -> dict[str, Citation]:
        return self(_read_json(Path(discovery_path)), _read_json(Path(serp_path)), _read_json(Path(content_path)))


build_citation_index = _CitationIndexBuilder()


def create_content_gap_report(
    config: ProjectConfig,
    options: ContentGapOptions,
    *,
    brief_adapter: BriefAdapter | None = None,
) -> dict[str, Any]:
    provider_mode = options.provider_mode or "artifact"
    output_dir = options.output_dir or _default_output_dir(config.project.namespace)
    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, Any]] = []
    if provider_mode == "live" and not options.allow_paid:
        return _error_payload(
            config=config,
            provider_mode=provider_mode,
            output_dir=output_dir,
            errors=[_error("PAID_CALL_NOT_CONFIRMED", "Live report synthesis requires --allow-paid.", {})],
        )
    if provider_mode not in {"fixture", "artifact", "live"}:
        return _error_payload(
            config=config,
            provider_mode=provider_mode,
            output_dir=output_dir,
            errors=[_error("PROVIDER_MODE_INVALID", f"Unsupported provider mode: {provider_mode}", {"provider_mode": provider_mode})],
        )

    resolved = _resolve_inputs(config.project.namespace, output_dir, options)
    if resolved["errors"]:
        return _error_payload(config=config, provider_mode=provider_mode, output_dir=output_dir, errors=resolved["errors"])
    discovery = _read_json(resolved["discovery"])
    serp = _read_json(resolved["serp"])
    content = _read_json(resolved["content"])
    schema_errors = _schema_errors(discovery=discovery, serp=serp, content=content)
    if schema_errors:
        return _error_payload(config=config, provider_mode=provider_mode, output_dir=output_dir, errors=schema_errors)

    derived = derive_content_gaps(discovery, serp, content)
    citation_index = build_citation_index(discovery, serp, content)
    created_at = _utc_now()
    gap = {
        "schema": "seo-observer.content_gap.v1",
        "project": config.project.namespace,
        "created_at": created_at,
        "input_artifacts": {key: str(path) for key, path in resolved.items() if key in {"discovery", "serp", "content"}},
        **derived,
        "quality_summary": _quality_summary(discovery, serp, content, derived),
    }

    gap_path = output_dir / "content-gap.json"
    report_path = output_dir / "competitor-report.md"
    gap_path.write_text(_json_text(gap), encoding="utf-8")

    brief: dict[str, Any] | None = None
    if options.with_brief:
        brief = _brief_output(
            gap,
            citation_index,
            adapter=brief_adapter or (BriefFixtureAdapter(options.llm_fixture) if options.llm_fixture else None),
        )
        (output_dir / "brief.json").write_text(_json_text(brief), encoding="utf-8")

    report_path.write_text(_render_markdown(config, gap, brief=brief), encoding="utf-8")
    artifact_entries = [
        _artifact_entry(gap_path, output_dir=output_dir, artifact_type="content_gap", privacy_class="private_structured_artifact", created_at=created_at),
        _artifact_entry(report_path, output_dir=output_dir, artifact_type="competitor_report", privacy_class="public_report_artifact", created_at=created_at),
    ]
    if brief is not None:
        artifact_entries.append(_artifact_entry(output_dir / "brief.json", output_dir=output_dir, artifact_type="content_brief", privacy_class="private_structured_artifact", created_at=created_at))
    manifest_path = output_dir / "manifest.json"
    manifest = {
        "schema": "seo-observer.competitor_manifest.v1",
        "project": config.project.namespace,
        "command": REPORT_COMMAND,
        "created_at": created_at,
        "config_hash": f"sha256:{compute_config_hash(config)}",
        "protocol_hashes": [str(item) for item in serp.get("protocol_hashes") or []],
        "budget": {"provider_mode": provider_mode, "allow_paid": options.allow_paid, "planned_calls": 1 if options.with_brief else 0, "actual_calls": 0},
        "artifacts": artifact_entries,
        "source_request_ids": [],
    }
    manifest_path.write_text(_json_text(manifest), encoding="utf-8")
    response_artifacts = [
        _artifact_entry(manifest_path, output_dir=output_dir, artifact_type="competitor_manifest", privacy_class="public_report_artifact", created_at=created_at),
        *artifact_entries,
    ]
    return {
        "ok": True,
        "project": config.project.namespace,
        "command": REPORT_COMMAND,
        "provider_mode": provider_mode,
        "sources": {
            "content_gap": {
                "quality": gap["quality_summary"]["overall"],
                "source_artifacts": gap["input_artifacts"],
                "citation_count": len(citation_index),
            }
        },
        "artifacts": response_artifacts,
        "cost": {"provider_cost_usd": 0.0, "currency": "USD"},
        "report_path": str(report_path),
        "errors": errors,
    }


def derive_content_gaps(discovery: dict[str, Any], serp: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    serp_by_url = _rows_by_url(serp.get("serp_rows"))
    pages_by_url = _rows_by_url(content.get("page_extracts"))
    page_patterns = _page_patterns(content.get("page_extracts"))
    gaps: list[dict[str, Any]] = []
    for gap in _dict_rows(discovery.get("keyword_gaps")):
        url = _clean_url(gap.get("representative_competitor_url"))
        matching_serp = serp_by_url.get(url or "", [])
        matching_pages = pages_by_url.get(url or "", [])
        opportunity = compute_opportunity(gap)
        citation_ids = list(_citation_ids(gap))
        citation_ids.extend(_serp_citation_id(row) for row in matching_serp)
        citation_ids.extend(str(page.get("citation_id") or f"page:{_hash(str(page.get('url') or 'page'))}") for page in matching_pages)
        row = {
            "keyword": str(gap.get("keyword") or ""),
            "search_volume": opportunity["search_volume"],
            "difficulty": opportunity["difficulty"],
            "competitor_count": len(gap.get("competitor_domains") or []),
            "competitor_domains": list(gap.get("competitor_domains") or []),
            "owned_url_present": bool(gap.get("owned_url_present", gap.get("our_domain_present", False))),
            "intent": str(gap.get("intent") or _infer_intent(str(gap.get("keyword") or ""))),
            "opportunity_score": opportunity["opportunity_score"],
            "opportunity_score_reason": opportunity["opportunity_score_reason"],
            "best_competitor_rank": gap.get("best_competitor_rank"),
            "representative_competitor_url": url,
            "confirmed_serp_pages": [
                {
                    "url": item.get("url"),
                    "rank": item.get("rank"),
                    "domain": item.get("domain") or item.get("host"),
                    "citation_id": _serp_citation_id(item),
                    "quality": item.get("quality") or "unknown",
                }
                for item in matching_serp
            ],
            "outline_patterns": [
                {
                    "url": page.get("url"),
                    "title": page.get("title"),
                    "headings": [heading.get("text") for heading in _dict_rows(page.get("headings"))[:6]],
                    "word_count": page.get("word_count"),
                    "citation_id": page.get("citation_id"),
                    "quality": page.get("quality") or "research-only",
                }
                for page in matching_pages
            ],
            "citation_ids": _dedupe(citation_ids),
            "quality": _row_quality(gap, matching_serp),
        }
        gaps.append(row)
    gaps.sort(
        key=lambda row: (
            row["opportunity_score"] is None,
            -row["opportunity_score"] if row["opportunity_score"] is not None else 0.0,
            -int(row.get("search_volume") or 0),
            str(row.get("keyword")),
        )
    )
    return {
        "content_gaps": gaps,
        "competitor_page_patterns": page_patterns,
        "table_stakes": _table_stakes(page_patterns),
        "differentiation_opportunities": _differentiation(gaps, page_patterns),
        "serp_feature_opportunities": _feature_opportunities(serp.get("serp_rows")),
        "citation_ids": sorted(build_citation_index(discovery, serp, content).keys()),
    }


def compute_opportunity(row: dict[str, Any]) -> dict[str, Any]:
    volume = _optional_float(row.get("search_volume"))
    difficulty = _optional_float(row.get("difficulty", row.get("keyword_difficulty")))
    if volume is None:
        return {"search_volume": None, "difficulty": difficulty, "opportunity_score": None, "opportunity_score_reason": "missing_search_volume"}
    if difficulty is None:
        return {"search_volume": volume if not volume.is_integer() else int(volume), "difficulty": None, "opportunity_score": None, "opportunity_score_reason": "missing_difficulty"}
    score = volume * (1 - difficulty / 100)
    return {
        "search_volume": volume if not volume.is_integer() else int(volume),
        "difficulty": difficulty if not difficulty.is_integer() else int(difficulty),
        "opportunity_score": round(score, 6),
        "opportunity_score_reason": None,
    }


def validate_brief(brief: dict[str, Any], citation_index: dict[str, Citation]) -> BriefValidation:
    errors: list[dict[str, Any]] = []
    for index, claim in enumerate(_dict_rows(brief.get("claims"))):
        text = str(claim.get("claim") or "").strip()
        if not text:
            continue
        citation_ids = [str(item) for item in claim.get("citation_ids") or [] if str(item).strip()]
        if not citation_ids:
            errors.append(_validation_error("CLAIM_MISSING_CITATION", index, "Substantive brief claim has no citations.", {}))
            continue
        citations = []
        for citation_id in citation_ids:
            citation = citation_index.get(citation_id)
            if citation is None:
                errors.append(_validation_error("UNKNOWN_CITATION_ID", index, "Brief claim cites an unknown artifact row.", {"citation_id": citation_id}))
            else:
                citations.append(citation)
        if not citations:
            continue
        claim_type = str(claim.get("type") or "").lower()
        numbers_in_text = NUMBER_RE.findall(text)
        is_metric_claim = claim_type in RANK_CLAIM_TYPES or bool(numbers_in_text)

        if is_metric_claim and not any(citation.deterministic for citation in citations):
            errors.append(_validation_error("RESEARCH_ONLY_UNSUPPORTED_CLAIM", index, "Research-only citations cannot support rank, traffic, SOV, or trend claims.", {"type": claim_type}))

        urls_in_text = {_clean_url(url) for url in URL_RE.findall(text)} - {None}
        for normalized in sorted(urls_in_text):
            if normalized not in {citation.url for citation in citations if citation.url}:
                errors.append(_validation_error("FABRICATED_URL", index, "Brief claim contains a URL not present in cited evidence.", {"url": normalized}))

        if is_metric_claim and numbers_in_text:
            for number in numbers_in_text:
                norm_num = _normalize_number(number)
                matched = False
                for citation in citations:
                    if not citation.deterministic:
                        continue
                    cit_numbers = _citation_numbers(citation)
                    if norm_num not in cit_numbers:
                        continue
                    if urls_in_text:
                        clean_cit_url = _clean_url(citation.url)
                        if clean_cit_url and clean_cit_url in urls_in_text:
                            matched = True
                            break
                    else:
                        matched = True
                        break
                if not matched:
                    errors.append(_validation_error("FABRICATED_NUMBER", index, "Brief claim contains a numeric rank/volume/SOV value not present in cited deterministic evidence.", {"number": number}))
    return BriefValidation(ok=not errors, errors=errors)


def _brief_output(
    gap: dict[str, Any],
    citation_index: dict[str, Citation],
    *,
    adapter: BriefAdapter | None,
) -> dict[str, Any]:
    evidence = _evidence_status(gap, citation_index)
    if not evidence["ok"]:
        return {
            "schema": "seo-observer.content_brief.v1",
            "ok": False,
            "brief_status": "blocked_low_evidence",
            "evidence": evidence,
            "claims": [],
            "errors": [_error("BRIEF_LOW_EVIDENCE", "Evidence thresholds were not met for grounded LLM brief synthesis.", evidence)],
        }
    if adapter is None:
        return {
            "schema": "seo-observer.content_brief.v1",
            "ok": False,
            "brief_status": "blocked_fixture_missing",
            "evidence": evidence,
            "claims": [],
            "errors": [_error("LLM_FIXTURE_MISSING", "Brief synthesis requires --llm-fixture in fixture/artifact acceptance paths.", {})],
        }
    packet = _compact_evidence_packet(gap)
    synthesized = adapter.synthesize(packet)
    adapter_errors = _dict_rows(synthesized.get("errors"))
    if adapter_errors or synthesized.get("ok") is False:
        return {
            "schema": "seo-observer.content_brief.v1",
            "ok": False,
            "brief_status": "blocked_adapter_failed",
            "evidence": evidence,
            "claims": _dict_rows(synthesized.get("claims")),
            "errors": adapter_errors or [_error("LLM_SYNTHESIS_FAILED", "LLM brief synthesis failed.", {})],
        }
    validation = validate_brief(synthesized, citation_index)
    if not validation.ok:
        return {
            "schema": "seo-observer.content_brief.v1",
            "ok": False,
            "brief_status": "blocked_validation_failed",
            "evidence": evidence,
            "claims": _dict_rows(synthesized.get("claims")),
            "errors": validation.errors,
        }
    return {
        "schema": "seo-observer.content_brief.v1",
        "ok": True,
        "brief_status": "ok",
        "evidence": evidence,
        "recommended_title": synthesized.get("recommended_title"),
        "target_intent": synthesized.get("target_intent"),
        "suggested_outline": list(synthesized.get("suggested_outline") or []),
        "key_points": list(synthesized.get("key_points") or []),
        "internal_linking_candidates": list(synthesized.get("internal_linking_candidates") or []),
        "claims": _dict_rows(synthesized.get("claims")),
        "errors": [],
    }


def _render_markdown(config: ProjectConfig, gap: dict[str, Any], *, brief: dict[str, Any] | None) -> str:
    quality = gap.get("quality_summary", {})
    lines = [
        f"# Competitor Content Gap Report: {config.project.namespace}",
        "",
        "## Evidence Summary And Quality",
        f"- Quality: `{quality.get('overall')}`",
        f"- Content gaps: `{quality.get('content_gap_count')}`",
        f"- Citation count: `{quality.get('citation_count')}`",
        "- Artifact: `content-gap.json`",
        "",
        "## Top Content Gaps",
    ]
    for row in gap.get("content_gaps", [])[:20]:
        citations = ", ".join(f"`{item}`" for item in row.get("citation_ids") or [])
        lines.append(
            f"- `{row.get('keyword')}` score `{row.get('opportunity_score')}` volume `{row.get('search_volume')}` "
            f"difficulty `{row.get('difficulty')}` competitors `{row.get('competitor_count')}` citations {citations}"
        )
    if not gap.get("content_gaps"):
        lines.append("- None")
    lines.extend(["", "## Competitor Pages And Outline Patterns"])
    for pattern in gap.get("competitor_page_patterns", [])[:20]:
        headings = "; ".join(str(item) for item in pattern.get("headings") or [])
        lines.append(f"- `{pattern.get('citation_id')}` {pattern.get('url')} headings: {headings or 'none'}")
    if not gap.get("competitor_page_patterns"):
        lines.append("- None")
    lines.extend(["", "## Table Stakes"])
    lines.extend(f"- {item}" for item in gap.get("table_stakes") or ["No repeated outline pattern was detected."])
    lines.extend(["", "## Differentiation Opportunities"])
    lines.extend(f"- {item}" for item in gap.get("differentiation_opportunities") or ["Prioritize owned evidence and examples competitors do not cover."])
    lines.extend(["", "## SERP Feature Opportunities"])
    for item in gap.get("serp_feature_opportunities") or []:
        citations = ", ".join(f"`{cid}`" for cid in item.get("citation_ids") or [])
        lines.append(f"- `{item.get('feature')}` for `{item.get('keyword')}` citations {citations}")
    if not gap.get("serp_feature_opportunities"):
        lines.append("- None")
    lines.extend(["", "## Brief Recommendations"])
    if brief is None:
        lines.append("- Brief synthesis was not requested.")
    elif brief.get("ok"):
        lines.append(f"- Title: {brief.get('recommended_title') or 'not supplied'}")
        lines.append(f"- Intent: {brief.get('target_intent') or 'not supplied'}")
        for claim in brief.get("claims") or []:
            citations = ", ".join(f"`{cid}`" for cid in claim.get("citation_ids") or [])
            lines.append(f"- {claim.get('claim')} citations {citations}")
    else:
        lines.append(f"- Brief blocked: `{brief.get('brief_status')}`")
        for error in brief.get("errors") or []:
            lines.append(f"- `{error.get('code')}`: {error.get('safe_message')}")
    lines.append("")
    return "\n".join(lines)


def _resolve_inputs(project: str, output_dir: Path, options: ContentGapOptions) -> dict[str, Any]:
    explicit = {
        "discovery": options.discovery_artifact,
        "serp": options.serp_artifact,
        "content": options.content_artifact,
    }
    by_name = {
        "discovery": output_dir / "competitor-extract.json",
        "serp": output_dir / "serp-extract.json",
        "content": output_dir / "content-extract.json",
    }
    from_manifest = _paths_from_manifest(output_dir / "manifest.json", output_dir)
    latest = observer_home() / "projects" / project / "competitors" / "latest"
    latest_by_name = {
        "discovery": latest / "competitor-extract.json",
        "serp": latest / "serp-extract.json",
        "content": latest / "content-extract.json",
    }
    if all(path.is_file() for path in by_name.values()):
        resolved = dict(by_name)
    elif from_manifest and all(path.is_file() for path in {**by_name, **from_manifest}.values()):
        resolved = {**by_name, **from_manifest}
    elif all(path.is_file() for path in latest_by_name.values()):
        resolved = dict(latest_by_name)
    else:
        resolved = dict(by_name)

    for key, path in explicit.items():
        if path is not None:
            resolved[key] = Path(path).expanduser()

    missing = [key for key, path in resolved.items() if not Path(path).is_file()]
    if missing:
        return {"errors": [_error("CONTENT_GAP_INPUT_MISSING", "Required content-gap input artifacts were not found.", {"missing": sorted(set(missing))})]}
    return {**resolved, "errors": []}


def _paths_from_manifest(path: Path, base_dir: Path) -> dict[str, Path]:
    manifest = _read_json(path)
    mapping = {"competitor_extract": "discovery", "serp_extract": "serp", "content_extract": "content"}
    output: dict[str, Path] = {}
    for item in _dict_rows(manifest.get("artifacts")):
        artifact_type = str(item.get("artifact_type") or "")
        key = mapping.get(artifact_type)
        if key and item.get("path"):
            output[key] = base_dir / str(item["path"])
    return output


def _schema_errors(*, discovery: dict[str, Any], serp: dict[str, Any], content: dict[str, Any]) -> list[dict[str, Any]]:
    expected = {
        "discovery": (discovery, "seo-observer.competitor_extract.v1"),
        "serp": (serp, "seo-observer.serp_extract.v1"),
        "content": (content, "seo-observer.content_extract.v1"),
    }
    errors = []
    for name, (artifact, schema) in expected.items():
        if artifact.get("schema") != schema:
            errors.append(_error("CONTENT_GAP_INPUT_SCHEMA_INVALID", "Input artifact schema is invalid.", {"artifact": name, "expected_schema": schema, "actual_schema": artifact.get("schema")}))

    artifacts = {"discovery": discovery, "serp": serp, "content": content}
    ids = {name: art.get("keyword_set_id") for name, art in artifacts.items() if art.get("keyword_set_id") is not None}
    if len(set(ids.values())) > 1:
        errors.append(_error("KEYWORD_SET_MISMATCH", "Input artifacts have mismatched keyword_set_id.", ids))

    hashes = {name: art.get("keyword_set_hash") for name, art in artifacts.items() if art.get("keyword_set_hash") is not None}
    if len(set(hashes.values())) > 1:
        errors.append(_error("KEYWORD_SET_HASH_MISMATCH", "Input artifacts have mismatched keyword_set_hash.", hashes))

    return errors


def _quality_summary(discovery: dict[str, Any], serp: dict[str, Any], content: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    qualities = [
        str(discovery.get("quality_summary", {}).get("overall") or "local-only"),
        str(serp.get("quality_summary", {}).get("overall") or "local-only"),
        str(content.get("quality_summary", {}).get("overall") or "research-only"),
    ]
    overall = "partial" if "partial" in qualities else "live" if any(item == "live" for item in qualities[:2]) else "local-only"
    return {
        "overall": overall,
        "input_quality": {"discovery": qualities[0], "serp": qualities[1], "content": qualities[2]},
        "content_gap_count": len(derived.get("content_gaps") or []),
        "citation_count": len(derived.get("citation_ids") or []),
    }


def _evidence_status(gap: dict[str, Any], citation_index: dict[str, Citation]) -> dict[str, Any]:
    deterministic_gaps = [row for row in gap.get("content_gaps") or [] if any(citation_index.get(cid) and citation_index[cid].source_type == "keyword_gap" and citation_index[cid].deterministic for cid in row.get("citation_ids") or [])]
    confirmed_pages = {
        citation.url
        for citation in citation_index.values()
        if citation.source_type == "serp"
        and citation.deterministic
        and citation.url
        and str(citation.row.get("classification") or "").lower() == "competitor"
    }
    deterministic_domains = {
        str(domain)
        for row in deterministic_gaps
        for domain in row.get("competitor_domains") or []
        if domain
    }
    for citation in citation_index.values():
        if citation.source_type == "serp" and citation.deterministic and str(citation.row.get("classification") or "").lower() == "competitor":
            domain = citation.row.get("domain") or citation.row.get("host")
            if not domain and citation.url:
                parsed = urlparse(citation.url)
                domain = parsed.netloc
            if domain:
                deterministic_domains.add(str(domain))

    valid_citations = len(citation_index)
    ok = (len(deterministic_gaps) >= 5 or len(confirmed_pages) >= 3) and len(deterministic_domains) >= 2 and valid_citations >= 5
    return {
        "ok": ok,
        "deterministic_keyword_gap_count": len(deterministic_gaps),
        "confirmed_serp_page_count": len(confirmed_pages),
        "deterministic_competitor_domain_count": len(deterministic_domains),
        "valid_citation_count": valid_citations,
    }


def _compact_evidence_packet(gap: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_gaps": gap.get("content_gaps", [])[:10],
        "table_stakes": gap.get("table_stakes", [])[:10],
        "serp_feature_opportunities": gap.get("serp_feature_opportunities", [])[:10],
        "citation_ids": gap.get("citation_ids", [])[:100],
    }


def _page_patterns(pages: Any) -> list[dict[str, Any]]:
    output = []
    for page in _dict_rows(pages):
        output.append(
            {
                "url": page.get("url"),
                "title": page.get("title"),
                "headings": [heading.get("text") for heading in _dict_rows(page.get("headings"))[:8]],
                "word_count": page.get("word_count"),
                "json_ld_types": list(page.get("json_ld_types") or []),
                "citation_id": page.get("citation_id"),
                "quality": page.get("quality") or "research-only",
            }
        )
    return output


def _table_stakes(patterns: list[dict[str, Any]]) -> list[str]:
    headings: dict[str, int] = {}
    for pattern in patterns:
        for heading in pattern.get("headings") or []:
            text = str(heading).strip()
            if text:
                headings[text] = headings.get(text, 0) + 1
    return [f"Cover `{heading}` because it appears in {count} competitor outline(s)." for heading, count in sorted(headings.items(), key=lambda item: (-item[1], item[0]))[:8]]


def _differentiation(gaps: list[dict[str, Any]], patterns: list[dict[str, Any]]) -> list[str]:
    output = []
    if any(row.get("owned_url_present") is False for row in gaps):
        output.append("Create or improve owned URLs for gaps where no owned URL is present.")
    if patterns:
        output.append("Add first-party examples, comparison criteria, and stronger proof than competitor outlines show.")
    return output


def _feature_opportunities(rows: Any) -> list[dict[str, Any]]:
    output = []
    seen: set[tuple[str, str]] = set()
    for row in _dict_rows(rows):
        for feature in row.get("serp_features") or []:
            feature_text = str(feature)
            if feature_text == "organic":
                continue
            key = (str(row.get("keyword") or ""), feature_text)
            if key in seen:
                continue
            seen.add(key)
            output.append({"keyword": key[0], "feature": key[1], "citation_ids": [_serp_citation_id(row)], "quality": row.get("quality") or "unknown"})
    return output


def _citation_numbers(citation: Citation) -> set[str]:
    fields = {
        "rank",
        "rank_absolute",
        "best_competitor_rank",
        "search_volume",
        "difficulty",
        "keyword_difficulty",
        "share_of_voice",
        "visibility",
        "estimated_traffic",
        "traffic",
        "opportunity_score",
    }
    numbers: set[str] = set()
    if not citation.deterministic:
        return numbers
    for key in fields:
        value = citation.row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numbers.add(_normalize_number(str(value)))
    return numbers


def _validation_error(code: str, claim_index: int, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return _error(code, message, {"claim_index": claim_index, **details})


def _error_payload(*, config: ProjectConfig, provider_mode: str, output_dir: Path, errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": False,
        "project": config.project.namespace,
        "command": REPORT_COMMAND,
        "provider_mode": provider_mode,
        "sources": {},
        "artifacts": [],
        "cost": {"provider_cost_usd": 0.0, "currency": "USD"},
        "report_path": str(output_dir / "competitor-report.md"),
        "errors": errors,
    }


def _rows_by_url(rows: Any) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for row in _dict_rows(rows):
        url = _clean_url(row.get("url") or row.get("canonical_url"))
        if not url:
            continue
        output.setdefault(url, []).append(row)
    return output


def _row_quality(gap: dict[str, Any], serp_rows: list[dict[str, Any]]) -> str:
    qualities = [str(gap.get("quality") or "unknown"), *(str(row.get("quality") or "unknown") for row in serp_rows)]
    return "partial" if "partial" in qualities else qualities[0]


def _serp_citation_id(row: dict[str, Any]) -> str:
    seed = str(row.get("citation_id") or row.get("logical_observation_key") or row.get("provenance_request_id") or row.get("url") or "serp")
    return seed if seed.startswith("serp:") else f"serp:{seed}"


def _citation_ids(row: dict[str, Any]) -> list[str]:
    values = row.get("citation_ids")
    if isinstance(values, list):
        return [str(item) for item in values if str(item).strip()]
    for key in ("source_row_id", "citation_id"):
        if row.get(key):
            return [str(row[key])]
    keyword = str(row.get("keyword") or "gap")
    return [f"gap:{_hash(keyword)}"]


def _infer_intent(keyword: str) -> str:
    lowered = keyword.casefold()
    if any(token in lowered for token in ("buy", "price", "стоимость", "купить")):
        return "commercial"
    if any(token in lowered for token in ("how", "guide", "что", "как")):
        return "informational"
    return "unknown"


def _clean_url(value: Any) -> str | None:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text.rstrip(".,")


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]


def _dedupe(values: list[str]) -> list[str]:
    output = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _artifact_entry(
    path: Path,
    *,
    output_dir: Path,
    artifact_type: str,
    privacy_class: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(output_dir)),
        "sha256": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
        "artifact_type": artifact_type,
        "privacy_class": privacy_class,
        "created_at": created_at,
        "source_request_ids": [],
    }


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_number(value: str) -> str:
    parsed = float(value)
    return str(int(parsed)) if parsed.is_integer() else str(parsed).rstrip("0").rstrip(".")


def _error(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"ok": False, "code": code, "safe_message": message, "retryable": False, "details": details}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _default_output_dir(project: str) -> Path:
    return observer_home() / "projects" / project / "competitors" / "latest"


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
