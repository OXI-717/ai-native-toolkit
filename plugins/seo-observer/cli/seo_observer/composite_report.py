from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seo_observer import __version__
from seo_observer.report_rendering import write_polished_report_artifacts


# These patterns identify credential values, rather than only their surrounding
# field names. They intentionally cover the credential formats this report can
# encounter in imported provider artifacts.
SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:Bearer|Basic|OAuth)\s+[A-Za-z0-9._~+/=_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?=[A-Za-z0-9_-]{32,}\b)(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"),
)

SECRET_FIELD_VALUE_PATTERNS = (
    re.compile(
        r"(?i)\b(?:private_key|authorization|client_secret|refresh_token|access_token|api_key|x-api-key|token|password)\b"
        r"\s*[:=]\s*[^\s,;`\"'<>]+"
    ),
    re.compile(r"(?i)\b(?:cookie|set-cookie)\b\s*:\s*[^\s,;`\"'<>]+"),
)


@dataclass(frozen=True)
class CompositeReportOptions:
    provider_artifact: Path
    research_artifact: Path
    content_artifact: Path
    output_dir: Path
    serp_artifact: Path | None = None
    metrics_artifact: Path | None = None


class CompositeArtifactError(ValueError):
    def __init__(self, *, artifact: str, path: Path, message: str, expected_schema: str | int | None = None) -> None:
        super().__init__(message)
        self.artifact = artifact
        self.path = path
        self.expected_schema = expected_schema
        self.message = message

    def payload(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "path": str(self.path),
            "expected_schema": self.expected_schema,
            "error": self.message,
        }


def build_composite_report(options: CompositeReportOptions) -> dict[str, Any]:
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = _utc_now()
    provider = _read_required_artifact(
        "provider",
        options.provider_artifact,
        validator=lambda value: value.get("schema_version") == 1 and value.get("command") == "provider-audit",
        expected_schema=1,
    )
    research = _read_required_artifact(
        "research",
        options.research_artifact,
        validator=lambda value: value.get("schema") == "seo-observer.research_extract.v1",
        expected_schema="seo-observer.research_extract.v1",
    )
    content = _read_required_artifact(
        "content",
        options.content_artifact,
        validator=lambda value: value.get("schema") == "seo-observer.content_extract.v1",
        expected_schema="seo-observer.content_extract.v1",
    )
    serp = _read_optional_artifact(
        "serp",
        options.serp_artifact,
        expected_schema="seo-observer.serp_extract.v1",
    )
    metrics = _read_optional_artifact(
        "metrics",
        options.metrics_artifact,
        expected_schema="seo-observer.competitor_metrics.v1",
    )
    extract = _compose_extract(
        provider=provider,
        research=research,
        content=content,
        serp=serp,
        metrics=metrics,
        options=options,
        created_at=created_at,
    )
    report_text = render_composite_markdown(extract)
    _assert_no_secret_markers(report_text, artifact_name="composite-report.md")

    extract_path = output_dir / "composite-extract.json"
    report_path = output_dir / "composite-report.md"
    extract_text = _canonical_json(extract, indent=2) + "\n"
    _assert_no_secret_markers(extract_text, artifact_name="composite-extract.json")
    extract_path.write_text(extract_text, encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")

    polished = write_polished_report_artifacts(
        markdown_text=report_text,
        output_dir=output_dir,
        basename="composite-report",
        title=f"SEO decision report: {extract['project']}",
        subtitle=_subtitle(extract),
    )
    _assert_no_secret_markers(polished.html_path.read_text(encoding="utf-8"), artifact_name="composite-report.html")
    if polished.pdf_path is not None:
        _assert_no_secret_markers(polished.pdf_path.read_bytes(), artifact_name="composite-report.pdf")

    source_artifacts = _source_artifact_hashes(options)
    files = {
        "composite-extract.json": _file_manifest_entry(extract_path, "application/json", "redacted-structured-summary"),
        "composite-report.md": _file_manifest_entry(report_path, "text/markdown", "redacted-human-report"),
        "composite-report.html": _file_manifest_entry(polished.html_path, "text/html", "redacted-human-report"),
    }
    artifacts = {
        "composite_extract": {"path": str(extract_path), "sha256": files["composite-extract.json"]["sha256"]},
        "report": {"path": str(report_path), "sha256": files["composite-report.md"]["sha256"]},
        "html_report": {"path": str(polished.html_path), "sha256": files["composite-report.html"]["sha256"]},
        "report_path": str(report_path),
        "html_report_path": str(polished.html_path),
    }
    pdf_error = None
    if polished.pdf_path is not None:
        files["composite-report.pdf"] = _file_manifest_entry(polished.pdf_path, "application/pdf", "redacted-human-report")
        artifacts["pdf_report"] = {"path": str(polished.pdf_path), "sha256": files["composite-report.pdf"]["sha256"]}
        artifacts["pdf_report_path"] = str(polished.pdf_path)
    elif polished.pdf_error:
        pdf_error = {"code": "COMPOSITE_REPORT_PDF_RENDER_FAILED", "message": polished.pdf_error}

    manifest = {
        "schema": "seo-observer.composite_manifest.v1",
        "command": "composite-report",
        "created_at": created_at,
        "cli_version": __version__,
        "project": extract["project"],
        "period": extract.get("period"),
        "source_artifacts": source_artifacts,
        "files": files,
        "limitations": extract["limitations"],
    }
    if pdf_error is not None:
        manifest["pdf_error"] = pdf_error
    manifest_text = _canonical_json(manifest, indent=2) + "\n"
    _assert_no_secret_markers(manifest_text, artifact_name="manifest.json")
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(manifest_text, encoding="utf-8")
    artifacts["manifest"] = {"path": str(manifest_path), "sha256": _sha256_text(manifest_text)}

    result = {
        "ok": True,
        "command": "composite-report",
        "provider_mode": "artifact",
        "project": extract["project"],
        "output_dir": str(output_dir),
        "artifacts": artifacts,
        "report_path": str(report_path),
        "html_report_path": str(polished.html_path),
        "pdf_report_path": str(polished.pdf_path) if polished.pdf_path is not None else None,
        "limitations": extract["limitations"],
    }
    if pdf_error is not None:
        result["pdf_error"] = pdf_error
    return result


def render_composite_markdown(extract: dict[str, Any]) -> str:
    decision = extract.get("decision_report") or _decision_report_model(extract)
    serp = extract["evidence"]["serp"]
    metrics = extract["evidence"]["metrics"]
    lines = [
        f"# SEO decision report: {_safe_text(extract.get('project') or 'unknown')}",
        "",
        "## Above-the-fold summary",
    ]
    lines.extend(f"- {item}" for item in decision["executive_summary"])
    lines.extend(["", "## Source coverage"])
    lines.extend(_source_coverage_lines(decision))
    market_lines = _market_scope_decision_lines(extract.get("market_scope") or {})
    if market_lines:
        lines.extend(market_lines)
    lines.extend(["", "## Prioritized findings"])
    lines.extend(_prioritized_finding_lines(decision))
    lines.extend(["", "## Provider performance"])
    lines.extend(_provider_performance_lines(extract["evidence"]["provider"]))
    lines.extend(["", "## Technical diagnostics"])
    lines.extend(_technical_diagnostic_lines(extract))
    lines.extend(["", "## Competitor observations"])
    lines.extend(_competitor_observation_lines(extract))
    if _has_deterministic_serp(serp) and not _serp_scope_gate_blocks_report(extract):
        lines.extend(["", "## SERP / SOV"])
        lines.extend(_serp_decision_lines(serp, metrics))
    lines.extend(["", "## Limitations and blocked conclusions"])
    lines.extend(_limitation_lines(extract))
    lines.extend(["", "## Appendix: full rows and artifact references"])
    lines.extend(_appendix_lines(extract))
    return "\n".join(lines) + "\n"


def _compose_extract(
    *,
    provider: dict[str, Any],
    research: dict[str, Any],
    content: dict[str, Any],
    serp: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
    options: CompositeReportOptions,
    created_at: str,
) -> dict[str, Any]:
    project = _safe_text(provider.get("project") or "unknown")
    limitations: list[str] = []
    if serp is None:
        limitations.append(
            "No SERP artifact was provided; the report does not include rank, SERP feature, or share-of-voice conclusions."
        )
    if metrics is None:
        limitations.append("No competitor metrics artifact was provided; share-of-voice comparison is unavailable.")
    evidence = {
        "provider": _provider_summary(provider),
        "research": _research_summary(research),
        "content": _content_summary(content),
        "serp": _serp_summary(serp),
        "metrics": _metrics_summary(metrics),
    }
    market_scope = _market_scope_summary(provider)
    claim_gate = _claim_gate(evidence, market_scope=market_scope)
    limitations.extend(_blocked_claim_limitations(claim_gate))
    limitations.extend(_market_scope_limitations(market_scope))
    extract = {
        "schema": "seo-observer.composite_extract.v1",
        "command": "composite-report",
        "created_at": created_at,
        "cli_version": __version__,
        "project": project,
        "period": _safe_mapping(provider.get("period")),
        "inputs": {
            "provider": _input_descriptor(options.provider_artifact, True),
            "research": _input_descriptor(options.research_artifact, True),
            "content": _input_descriptor(options.content_artifact, True),
            "serp": _input_descriptor(options.serp_artifact, serp is not None),
            "metrics": _input_descriptor(options.metrics_artifact, metrics is not None),
        },
        "evidence": evidence,
        "market_scope": market_scope,
        "claim_gate": claim_gate,
        "limitations": limitations,
    }
    extract["decision_report"] = _decision_report_model(extract)
    return extract


def _read_required_artifact(
    artifact: str,
    path: Path,
    *,
    validator: Any,
    expected_schema: str | int,
) -> dict[str, Any]:
    value = _read_json_object(artifact, path, expected_schema=expected_schema)
    if not validator(value):
        raise CompositeArtifactError(
            artifact=artifact,
            path=path,
            expected_schema=expected_schema,
            message="Artifact schema does not match the expected composite-report input.",
        )
    return value


def _read_optional_artifact(artifact: str, path: Path | None, *, expected_schema: str) -> dict[str, Any] | None:
    if path is None:
        return None
    value = _read_json_object(artifact, path, expected_schema=expected_schema)
    if value.get("schema") != expected_schema:
        raise CompositeArtifactError(
            artifact=artifact,
            path=path,
            expected_schema=expected_schema,
            message="Artifact schema does not match the expected composite-report input.",
        )
    return value


def _read_json_object(artifact: str, path: Path, *, expected_schema: str | int | None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CompositeArtifactError(
            artifact=artifact,
            path=path,
            expected_schema=expected_schema,
            message=f"{exc.__class__.__name__}: {exc}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise CompositeArtifactError(
            artifact=artifact,
            path=path,
            expected_schema=expected_schema,
            message=f"JSONDecodeError: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise CompositeArtifactError(
            artifact=artifact,
            path=path,
            expected_schema=expected_schema,
            message="Artifact JSON root must be an object.",
        )
    return value


def _provider_summary(provider: dict[str, Any]) -> dict[str, Any]:
    sources = provider.get("sources") if isinstance(provider.get("sources"), dict) else {}
    source_rows = []
    for name, source in sorted(sources.items()):
        if not isinstance(source, dict):
            continue
        blocks = _provider_property_blocks(source)
        row = {
            "source": _safe_text(name),
            "quality": _safe_text(source.get("quality") or "partial"),
            "message": _safe_text(source.get("message") or ""),
            "finding_count": sum(_provider_finding_count(block) for block in blocks),
            "top_queries": _safe_query_rows(blocks),
            "top_pages": _safe_page_rows(blocks),
        }
        source_rows.append(row)
    qualities = [row["quality"] for row in source_rows]
    return {
        "source_count": len(source_rows),
        "overall_quality": _overall_quality(qualities),
        "sources": source_rows,
    }


def _research_summary(research: dict[str, Any]) -> dict[str, Any]:
    rows = research.get("research_rows") if isinstance(research.get("research_rows"), list) else []
    safe_rows = []
    for row in rows[:10]:
        if not isinstance(row, dict):
            continue
        safe_rows.append(
            {
                "title": _safe_text(row.get("title") or row.get("name") or ""),
                "url": _safe_text(row.get("url") or ""),
                "citation_id": _safe_text(row.get("citation_id") or ""),
                "score": _safe_number(row.get("score")),
            }
        )
    quality_summary = research.get("quality_summary") if isinstance(research.get("quality_summary"), dict) else {}
    return {
        "keyword_set_id": _safe_text(research.get("keyword_set_id") or ""),
        "keyword_set_hash": _safe_text(research.get("keyword_set_hash") or ""),
        "overall_quality": _safe_text(quality_summary.get("overall") or "partial"),
        "row_count": len(rows),
        "rows": safe_rows,
    }


def _content_summary(content: dict[str, Any]) -> dict[str, Any]:
    pages = content.get("page_extracts") if isinstance(content.get("page_extracts"), list) else []
    safe_pages = []
    for page in pages[:10]:
        if not isinstance(page, dict):
            continue
        headings = page.get("headings") if isinstance(page.get("headings"), list) else []
        safe_pages.append(
            {
                "title": _safe_text(page.get("title") or ""),
                "url": _safe_text(page.get("canonical_url") or page.get("url") or ""),
                "citation_id": _safe_text(page.get("citation_id") or ""),
                "word_count": _safe_number(page.get("word_count")),
                "quality": _safe_text(page.get("quality") or ""),
                "error": _safe_mapping(page.get("error")),
                "headings": [_safe_text(item.get("text") or "") for item in headings[:5] if isinstance(item, dict)],
                "excerpt": _safe_text(page.get("text_excerpt") or "")[:500],
            }
        )
    quality_summary = content.get("quality_summary") if isinstance(content.get("quality_summary"), dict) else {}
    return {
        "keyword_set_id": _safe_text(content.get("keyword_set_id") or ""),
        "keyword_set_hash": _safe_text(content.get("keyword_set_hash") or ""),
        "overall_quality": _safe_text(quality_summary.get("overall") or "partial"),
        "page_count": len(pages),
        "pages": safe_pages,
    }


def _serp_summary(serp: dict[str, Any] | None) -> dict[str, Any]:
    if serp is None:
        return {"present": False, "row_count": 0, "coverage": None, "protocol_hashes": [], "rows": [], "display_rows": []}
    rows = serp.get("serp_rows") if isinstance(serp.get("serp_rows"), list) else []
    coverage = serp.get("coverage") if isinstance(serp.get("coverage"), dict) else {}
    summary = {
        "present": True,
        "keyword_set_id": _safe_text(serp.get("keyword_set_id") or ""),
        "keyword_set_hash": _safe_text(serp.get("keyword_set_hash") or ""),
        "row_count": len(rows),
        "coverage": _safe_number(coverage.get("weighted_coverage")),
        "protocol_hashes": [_safe_text(item) for item in (serp.get("protocol_hashes") or []) if item],
        "rows": [
            {
                "property_id": _safe_text(row.get("property_id") or ""),
                "keyword_set_id": _safe_text(row.get("keyword_set_id") or serp.get("keyword_set_id") or ""),
                "keyword_set_hash": _safe_text(row.get("keyword_set_hash") or serp.get("keyword_set_hash") or ""),
                "protocol_hash": _safe_text(row.get("protocol_hash") or ""),
                "logical_observation_key": _safe_text(row.get("logical_observation_key") or ""),
                "search_engine": _safe_text(row.get("search_engine") or ""),
                "region_id": _safe_text(row.get("region_id") or ""),
                "region_name": _safe_text(row.get("region_name") or ""),
                "device": _safe_text(row.get("device") or ""),
                "keyword": _safe_text(row.get("keyword") or ""),
                "rank": _safe_number(row.get("rank")),
                "url": _safe_text(row.get("url") or ""),
                "domain": _safe_text(row.get("domain") or ""),
                "classification": _safe_text(row.get("classification") or ""),
                "competitor_id": _safe_text(row.get("competitor_id") or ""),
                "serp_features": [_safe_text(item) for item in (row.get("serp_features") or [])[:5]],
            }
            for row in rows
            if isinstance(row, dict)
        ],
    }
    summary["display_rows"] = summary["rows"][:10]
    return summary


def _metrics_summary(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if metrics is None:
        return {"present": False}
    baseline = metrics.get("baseline") if isinstance(metrics.get("baseline"), dict) else {}
    return {
        "present": True,
        "keyword_set_id": _safe_text(metrics.get("keyword_set_id") or ""),
        "keyword_set_hash": _safe_text(metrics.get("keyword_set_hash") or ""),
        "protocol_hashes": [_safe_text(item) for item in (metrics.get("protocol_hashes") or []) if item],
        "coverage": _safe_number((metrics.get("coverage") if isinstance(metrics.get("coverage"), dict) else {}).get("weighted_coverage")),
        "comparability": _safe_text(metrics.get("comparability") or ""),
        "conclusion_status": _safe_text(metrics.get("conclusion_status") or ""),
        "baseline": _safe_mapping(baseline),
        "owned": _metric_rows(metrics.get("owned")),
        "competitors": _metric_rows(metrics.get("competitors")),
        "movements": _safe_movements(metrics.get("movements")),
        "quality": _safe_text(metrics.get("quality") or ""),
    }


def _market_scope_summary(provider: dict[str, Any]) -> dict[str, Any]:
    raw_scope = provider.get("market_scope") if isinstance(provider.get("market_scope"), dict) else {}
    raw_markets = raw_scope.get("markets") if isinstance(raw_scope.get("markets"), list) else []
    markets = []
    for item in raw_markets:
        if not isinstance(item, dict):
            continue
        intent = _safe_text(item.get("intent") or "primary")
        raw_roles = item.get("source_roles")
        source_roles = (
            [_safe_text(value) for value in raw_roles if value]
            if isinstance(raw_roles, list)
            else []
        )
        raw_out = item.get("out_of_scope")
        out_of_scope = (
            [_safe_text(value) for value in raw_out if value]
            if isinstance(raw_out, list)
            else []
        )
        raw_regions = item.get("regions")
        regions = (
            [_safe_text(value) for value in raw_regions if value]
            if isinstance(raw_regions, list)
            else []
        )
        market = {
            "id": _safe_text(item.get("id") or ""),
            "search_engine": _safe_text(item.get("search_engine") or ""),
            "provider": _safe_text(item.get("provider") or ""),
            "intent": intent,
            "regions": regions,
            "locale": _safe_text(item.get("locale") or ""),
            "language": _safe_text(item.get("language") or ""),
            "source_roles": source_roles,
            "out_of_scope": out_of_scope,
        }
        if market["id"]:
            markets.append(market)
    intent_order = {"primary": 0, "secondary": 1, "out_of_scope": 2}
    markets.sort(key=lambda m: intent_order.get(m["intent"], 99))
    primary_markets = [market["id"] for market in markets if market["intent"] == "primary"]
    return {"markets": markets, "primary_markets": primary_markets}


def _market_scope_lines(market_scope: dict[str, Any]) -> list[str]:
    lines = []
    for market in market_scope.get("markets") or []:
        lines.append(
            f"- {market['intent']}: `{market['id']}` uses `{market['search_engine']}` via `{market['provider']}`."
        )
    notes = _market_scope_interpretation_notes(market_scope)
    lines.extend(f"- {note}" for note in notes)
    if not lines:
        lines.append("- No project-level search-market scope was supplied.")
    return lines


def _market_scope_interpretation_notes(market_scope: dict[str, Any]) -> list[str]:
    markets = market_scope.get("markets") or []
    notes: list[str] = []
    primary_markets = [m for m in markets if m.get("intent") == "primary"]
    all_out_of_scope = {
        scope
        for market in markets
        for scope in (market.get("out_of_scope") or [])
        if isinstance(market.get("out_of_scope"), list)
    }

    for m in primary_markets:
        engine = m.get("search_engine")
        roles = set(m.get("source_roles") or []) if isinstance(m.get("source_roles"), list) else set()
        if engine == "yandex":
            yandex_parts = [
                label
                for role, label in (
                    ("yandex_webmaster", "Yandex Webmaster"),
                    ("yandex_metrica", "Yandex Metrica"),
                    ("wordstat", "Wordstat"),
                )
                if role in roles
            ]
            if yandex_parts:
                notes.append(f"Primary Yandex sources: {', '.join(yandex_parts)}.")
            if "google_search_console" in roles:
                notes.append("Google GSC can support owned-site performance, but it does not change the default competitor lens.")
        elif engine == "google":
            google_parts = [
                label
                for role, label in (
                    ("dataforseo_google_organic", "Google SERP"),
                    ("google_search_console", "Google GSC"),
                )
                if role in roles
            ]
            if google_parts:
                raw_regions = m.get("regions")
                regions = [r.lower() for r in raw_regions] if isinstance(raw_regions, list) else []
                is_worldwide = m.get("id") in {"global", "worldwide"} or any(r in {"global", "worldwide", "*"} for r in regions) or not regions
                scope = "Google-worldwide" if is_worldwide else "Google"
                notes.append(f"Primary {scope} sources: {', '.join(google_parts)}.")

    if "google_worldwide_serp_competitor_lens" in all_out_of_scope:
        notes.append("Google-worldwide SERP competitor conclusions are intentionally out of scope unless explicitly requested.")
    if "default_ru_yandex_competitor_lens" in all_out_of_scope:
        notes.append("Yandex evidence is secondary unless explicitly requested.")

    for scope in sorted(all_out_of_scope):
        if scope not in {"google_worldwide_serp_competitor_lens", "default_ru_yandex_competitor_lens"}:
            notes.append(f"Exclusion declared: {scope}.")

    return notes


def _market_scope_limitations(market_scope: dict[str, Any]) -> list[str]:
    limitations = []
    for note in _market_scope_interpretation_notes(market_scope):
        if "out of scope" in note or "secondary" in note:
            limitations.append(f"Market scope limitation: {_market_note_ru(note)}")
    return limitations


def _market_is_worldwide(market: dict[str, Any]) -> bool:
    market_id = str(market.get("id") or "").lower()
    if market_id in {"global", "worldwide"}:
        return True
    raw_regions = market.get("regions")
    regions = [str(r).lower() for r in raw_regions] if isinstance(raw_regions, list) else []
    return not regions or any(region in {"global", "worldwide", "*"} for region in regions)


def _serp_engine(serp: dict[str, Any], market_scope: dict[str, Any] | None = None) -> str:
    for row in serp.get("rows") or []:
        engine = str(row.get("search_engine") or "")
        if engine:
            return engine
    if serp.get("present"):
        primary_markets = [m for m in ((market_scope or {}).get("markets") or []) if m.get("intent") == "primary"]
        if primary_markets:
            return str(primary_markets[0].get("search_engine") or "google")
        return "google"
    return ""


def _serp_is_worldwide_google(serp: dict[str, Any], market_scope: dict[str, Any] | None = None) -> bool:
    if _serp_engine(serp, market_scope) != "google":
        return False
    row_regions = {
        str(row.get(field) or "").lower()
        for row in serp.get("rows") or []
        for field in ("region_id", "region_name")
        if row.get(field)
    }
    if row_regions:
        return any(region in {"global", "worldwide", "*"} for region in row_regions)
    google_markets = [
        market
        for market in ((market_scope or {}).get("markets") or [])
        if market.get("search_engine") == "google"
    ]
    if google_markets:
        return any(_market_is_worldwide(market) for market in google_markets)
    return bool(serp.get("present"))


def _serp_out_of_market_scope(serp: dict[str, Any], market_scope: dict[str, Any] | None = None) -> bool:
    all_out_of_scope = {
        scope
        for market in ((market_scope or {}).get("markets") or [])
        for scope in (market.get("out_of_scope") or [])
        if isinstance(market.get("out_of_scope"), list)
    }
    engine = _serp_engine(serp, market_scope)
    return (
        "google_worldwide_serp_competitor_lens" in all_out_of_scope
        and _serp_is_worldwide_google(serp, market_scope)
    ) or (
        "default_ru_yandex_competitor_lens" in all_out_of_scope
        and engine == "yandex"
    )


def _metric_rows(value: Any) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    return [
        {
            "competitor_id": _safe_text(row.get("competitor_id") or ""),
            "share_of_voice": _safe_number(row.get("share_of_voice")),
            "share_of_voice_delta": _safe_number(row.get("share_of_voice_delta")),
            "best_rank": _safe_number(row.get("best_rank")),
            "median_rank": _safe_number(row.get("median_rank")),
        }
        for row in rows[:10]
        if isinstance(row, dict)
    ]


def _claim_gate(evidence: dict[str, Any], market_scope: dict[str, Any] | None = None) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    provider = evidence["provider"]
    research = evidence["research"]
    content = evidence["content"]
    serp = evidence["serp"]
    metrics = evidence["metrics"]
    deterministic_serp = _has_deterministic_serp(serp)

    serp_out_of_scope = _serp_out_of_market_scope(serp, market_scope)

    for source in provider["sources"]:
        query_counts: dict[str, int] = {}
        for row in source["top_queries"][:2]:
            property_id = row.get("property_id") or "__all__"
            query_counts[property_id] = query_counts.get(property_id, 0) + 1
            findings.append(
                _allowed_finding(
                    label="Fact",
                    claim_type="source_performance",
                    text=f"`{source['source']}` query `{row['query']}` recorded {row['clicks']} clicks and {row['impressions']} impressions.",
                    evidence_ids=[f"provider:{source['source']}:{property_id}:query:{query_counts[property_id]}"],
                    quality=source.get("quality"),
                )
            )
        page_counts: dict[str, int] = {}
        for row in source["top_pages"][:2]:
            property_id = row.get("property_id") or "__all__"
            page_counts[property_id] = page_counts.get(property_id, 0) + 1
            findings.append(
                _allowed_finding(
                    label="Fact",
                    claim_type="source_performance",
                    text=f"`{source['source']}` page {row['url']} recorded {row['clicks']} clicks and {row['impressions']} impressions.",
                    evidence_ids=[f"provider:{source['source']}:{property_id}:page:{page_counts[property_id]}"],
                    quality=source.get("quality"),
                )
            )

    for row in research["rows"][:3]:
        citation_id = row.get("citation_id") or f"research:row:{len(findings) + 1}"
        findings.append(
            _allowed_finding(
                label="Hypothesis",
                claim_type="candidate_observation",
                text=f"Research-only row suggests a candidate competitor/topic: {row['title']} - {row['url']}.",
                evidence_ids=[citation_id],
                quality=research.get("overall_quality"),
            )
        )
    for page in content["pages"][:3]:
        citation_id = page.get("citation_id") or f"content:page:{len(findings) + 1}"
        if not _successful_content_page(page):
            findings.append(
                _blocked_finding(
                    claim_type="topic_observation",
                    reason="blocked_topic_without_successful_page_extract",
                    text=f"Content page {page['url'] or citation_id} cannot support topic coverage because extraction failed.",
                    evidence_ids=[citation_id],
                )
            )
            continue
        findings.append(
            _allowed_finding(
                label="Hypothesis",
                claim_type="topic_observation",
                text=f"Research-only content row suggests page/topic coverage: {page['title']}.",
                evidence_ids=[citation_id],
                quality=page.get("quality") or content.get("overall_quality"),
            )
        )

    if serp_out_of_scope:
        if serp.get("present"):
            findings.append(
                _blocked_finding(
                    claim_type="rank",
                    reason="blocked_market_out_of_scope",
                    text="Rank conclusions are intentionally out of market scope.",
                    evidence_ids=["market_scope:out_of_scope"],
                )
            )
    elif deterministic_serp:
        for row in serp["rows"][:5]:
            serp_id = _serp_evidence_id(row)
            findings.append(
                _allowed_finding(
                    label="Fact",
                    claim_type="rank",
                    text=f"`{row['keyword']}` has deterministic SERP rank {row['rank']} for {row['domain']}.",
                    evidence_ids=[serp_id],
                    quality="live",
                )
            )
    elif serp["present"]:
        findings.append(
            _blocked_finding(
                claim_type="rank",
                reason="blocked_rank_without_deterministic_serp",
                text="Rank findings require a SERP artifact with deterministic protocol hashes.",
                evidence_ids=["serp:protocol_hashes"],
            )
        )

    if metrics["present"]:
        if serp_out_of_scope:
            findings.append(
                _blocked_finding(
                    claim_type="sov",
                    reason="blocked_market_out_of_scope",
                    text="SOV conclusions are intentionally out of market scope.",
                    evidence_ids=["market_scope:out_of_scope"],
                )
            )
            findings.append(
                _blocked_finding(
                    claim_type="trend",
                    reason="blocked_market_out_of_scope",
                    text="Trend conclusions are intentionally out of market scope.",
                    evidence_ids=["market_scope:out_of_scope"],
                )
            )
        else:
            metric_rows = [("Competitor", row) for row in metrics.get("competitors") or []]
            metric_rows.extend(("Owned", row) for row in metrics.get("owned") or [])
            for label, row in metric_rows:
                metric_id = f"metrics:{label.lower()}:{row['competitor_id']}"
                identity_matches = deterministic_serp and _serp_metrics_identity_matches(serp, metrics)
                matching_serp_ids = _matching_serp_ids(serp, row) if identity_matches else []
                if not _sov_metric_eligible(metrics, row):
                    findings.append(
                        _blocked_finding(
                            claim_type="sov",
                            reason="blocked_sov_metric_ineligible",
                            text=f"{label} `{row['competitor_id']}` SOV/rank summary requires numeric SOV and eligible coverage conclusion.",
                            evidence_ids=[metric_id],
                        )
                    )
                elif matching_serp_ids:
                    findings.append(
                        _allowed_finding(
                            label="Fact",
                            claim_type="sov",
                            text=f"{label} `{row['competitor_id']}`: SOV {row['share_of_voice']}, best rank {row['best_rank']}.",
                            evidence_ids=[metric_id, *matching_serp_ids],
                            quality=metrics.get("quality"),
                        )
                    )
                else:
                    if not deterministic_serp:
                        reason = "blocked_sov_without_serp"
                    elif not identity_matches:
                        reason = "blocked_sov_identity_mismatch"
                    else:
                        reason = "blocked_sov_without_matching_serp_row"
                    findings.append(
                        _blocked_finding(
                            claim_type="sov",
                            reason=reason,
                            text=f"{label} `{row['competitor_id']}` SOV/rank summary requires a matching deterministic SERP row.",
                            evidence_ids=[metric_id],
                        )
                    )
            findings.extend(_trend_findings(metrics))

    findings.append(
        _allowed_finding(
            label="Action",
            claim_type="action",
            text="Use only cited rows above for external conclusions; collect missing SERP or comparable baseline artifacts before rank/SOV/trend claims.",
            evidence_ids=["claim_gate:policy"],
        )
    )
    return {"policy": "composite-report.claim-gate.v1", "findings": findings}


def _trend_findings(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    comparable = _has_comparable_baseline(metrics)
    rows = [("Competitor", row) for row in metrics.get("competitors") or []]
    rows.extend(("Owned", row) for row in metrics.get("owned") or [])
    output: list[dict[str, Any]] = []
    movements = metrics.get("movements") if isinstance(metrics.get("movements"), dict) else {}
    deltas = [
        (label, row, movements.get(row.get("competitor_id") or ""))
        for label, row in rows
        if isinstance(movements.get(row.get("competitor_id") or ""), dict)
        and _safe_number(movements[row.get("competitor_id") or ""].get("movement")) is not None
    ]
    if not comparable:
        output.append(
            _blocked_finding(
                claim_type="trend",
                reason="blocked_trend_without_comparable_baseline",
                text="Trend language requires comparable baseline metadata.",
                evidence_ids=["metrics:baseline"],
            )
        )
        return output
    for label, row, movement in deltas:
        delta = _safe_number(movement.get("movement"))
        if delta is None:
            continue
        status = str(movement.get("status") or "")
        if status not in {"confirmed", "comparable"}:
            continue
        direction = "improved" if delta < 0 else "declined" if delta > 0 else "held"
        output.append(
            _allowed_finding(
                label="Fact",
                claim_type="trend",
                text=f"{label} `{row['competitor_id']}` median rank {direction} by {abs(delta)}.",
                evidence_ids=[f"metrics:{label.lower()}:{row['competitor_id']}", "metrics:baseline"],
                quality=metrics.get("quality"),
            )
        )
    if comparable and not output:
        output.append(
            _blocked_finding(
                claim_type="trend",
                reason="blocked_trend_without_comparable_baseline",
                text="Trend language requires comparable movement evidence.",
                evidence_ids=["metrics:movements"],
            )
        )
    return output


def _allowed_finding(*, label: str, claim_type: str, text: str, evidence_ids: list[str], quality: str | None = None) -> dict[str, Any]:
    finding = {
        "status": "allowed",
        "label": label,
        "claim_type": claim_type,
        "text": _safe_text(text),
        "evidence_ids": [_safe_text(item) for item in evidence_ids if item],
    }
    if quality:
        finding["quality"] = quality
    return finding


def _blocked_finding(*, claim_type: str, reason: str, text: str, evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "label": "Limitation",
        "claim_type": claim_type,
        "reason": reason,
        "text": _safe_text(text),
        "evidence_ids": [_safe_text(item) for item in evidence_ids if item],
    }


def _blocked_claim_limitations(claim_gate: dict[str, Any]) -> list[str]:
    limitations = []
    for finding in claim_gate.get("findings") or []:
        if finding.get("status") != "blocked":
            continue
        claim_type = str(finding.get("claim_type") or "")
        prefix = "SOV/rank conclusion blocked: " if claim_type == "sov" else "Conclusion blocked: "
        limitations.append(
            f"{prefix}{_russian_claim_text(claim_type, str(finding.get('text') or ''))} "
            f"(reason: `{finding.get('reason')}`; evidence ID: {', '.join(finding.get('evidence_ids') or ['none'])})"
        )
    return limitations


def _matching_serp_ids(serp: dict[str, Any], metric_row: dict[str, Any]) -> list[str]:
    best_rank = metric_row.get("best_rank")
    if best_rank is None:
        return []
    metric_id = str(metric_row.get("competitor_id") or "").lower()
    matches = []
    for row in serp.get("rows") or []:
        if row.get("rank") == best_rank and _serp_row_matches_metric(row, metric_id):
            matches.append(_serp_evidence_id(row))
    return matches


def _serp_evidence_id(row: dict[str, Any]) -> str:
    logical_key = row.get("logical_observation_key")
    if logical_key:
        return str(logical_key)
    identity = {
        "property_id": row.get("property_id"),
        "keyword_set_id": row.get("keyword_set_id"),
        "keyword": row.get("keyword"),
        "rank": row.get("rank"),
        "protocol_hash": row.get("protocol_hash"),
        "region_id": row.get("region_id"),
        "device": row.get("device"),
        "domain": row.get("domain"),
        "url": row.get("url"),
    }
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()[:16]
    return f"serp:{digest}"


def _has_deterministic_serp(serp: dict[str, Any]) -> bool:
    return bool(serp.get("present") and serp.get("protocol_hashes"))


def _serp_row_matches_metric(row: dict[str, Any], metric_id: str) -> bool:
    if not metric_id:
        return False
    row_competitor_id = str(row.get("competitor_id") or "").lower()
    if row_competitor_id and row_competitor_id == metric_id:
        return True
    domain = str(row.get("domain") or "").lower()
    return bool(domain and domain in metric_id)


def _has_comparable_baseline(metrics: dict[str, Any]) -> bool:
    baseline = metrics.get("baseline") if isinstance(metrics.get("baseline"), dict) else {}
    return metrics.get("comparability") == "comparable" and bool(baseline.get("source")) and baseline.get("reason") in {None, "", "comparable"}


def _serp_metrics_identity_matches(serp: dict[str, Any], metrics: dict[str, Any]) -> bool:
    if not _has_deterministic_serp(serp):
        return False
    serp_keyword_hash = serp.get("keyword_set_hash") or ""
    metrics_keyword_hash = metrics.get("keyword_set_hash") or ""
    if not serp_keyword_hash or not metrics_keyword_hash or serp_keyword_hash != metrics_keyword_hash:
        return False
    serp_keyword_id = serp.get("keyword_set_id") or ""
    metrics_keyword_id = metrics.get("keyword_set_id") or ""
    if serp_keyword_id and metrics_keyword_id and serp_keyword_id != metrics_keyword_id:
        return False
    serp_protocols = {str(item) for item in serp.get("protocol_hashes") or [] if item}
    metrics_protocols = {str(item) for item in metrics.get("protocol_hashes") or [] if item}
    return bool(serp_protocols and metrics_protocols and serp_protocols == metrics_protocols)


def _sov_metric_eligible(metrics: dict[str, Any], row: dict[str, Any]) -> bool:
    return (
        metrics.get("conclusion_status") == "available"
        and _safe_number(metrics.get("coverage")) is not None
        and _safe_number(row.get("share_of_voice")) is not None
    )


def _successful_content_page(page: dict[str, Any]) -> bool:
    word_count = _safe_number(page.get("word_count"))
    quality = str(page.get("quality") or "").lower()
    return not page.get("error") and quality not in {"failed", "failure", "error"} and word_count is not None and word_count > 0


def _provider_finding_count(source: dict[str, Any]) -> int:
    diagnostics = source.get("diagnostics") if isinstance(source.get("diagnostics"), dict) else {}
    present = diagnostics.get("present")
    return len(present) if isinstance(present, list) else 0


def _provider_property_blocks(source: dict[str, Any]) -> list[dict[str, Any]]:
    properties = source.get("properties")
    if isinstance(properties, list):
        blocks = [item for item in properties if isinstance(item, dict)]
        return blocks or [source]
    return [source]


def _safe_query_rows(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for block in blocks:
        analytics = block.get("search_analytics") if isinstance(block.get("search_analytics"), dict) else {}
        queries = analytics.get("queries") if isinstance(analytics.get("queries"), dict) else {}
        rows = queries.get("rows") if isinstance(queries.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            output.append(
                {
                    "property_id": _safe_text(block.get("property_id") or "__all__"),
                    "query": _safe_text(row.get("query") or row.get("query_text") or ""),
                    "clicks": _safe_number(row.get("clicks")),
                    "impressions": _safe_number(row.get("impressions")),
                }
            )
    return output[:5]


def _safe_page_rows(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for block in blocks:
        analytics = block.get("search_analytics") if isinstance(block.get("search_analytics"), dict) else {}
        pages = analytics.get("pages") if isinstance(analytics.get("pages"), dict) else {}
        rows = pages.get("rows") if isinstance(pages.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            output.append(
                {
                    "property_id": _safe_text(block.get("property_id") or "__all__"),
                    "url": _safe_text(row.get("page_url") or row.get("url") or ""),
                    "clicks": _safe_number(row.get("clicks")),
                    "impressions": _safe_number(row.get("impressions")),
                }
            )
    return output[:5]


def _safe_movements(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    output = {}
    for competitor_id, movement in value.items():
        if not isinstance(movement, dict):
            continue
        output[_safe_text(competitor_id)] = {
            "baseline_median_rank": _safe_number(movement.get("baseline_median_rank")),
            "current_median_rank": _safe_number(movement.get("current_median_rank")),
            "movement": _safe_number(movement.get("movement")),
            "confirming_observations": _safe_number(movement.get("confirming_observations")),
            "required_confirmation_observations": _safe_number(movement.get("required_confirmation_observations")),
            "status": _safe_text(movement.get("status") or ""),
        }
    return output


def _provider_lines(provider: dict[str, Any]) -> list[str]:
    lines = [f"- Overall quality: `{provider['overall_quality']}`."]
    for source in provider["sources"]:
        detail = f"- `{source['source']}` quality `{source['quality']}`; findings: {source['finding_count']}."
        if source.get("message"):
            detail += f" {source['message']}"
        lines.append(detail)
        for row in source["top_queries"][:3]:
            lines.append(f"  - Query `{row['query']}`: clicks {row['clicks']}, impressions {row['impressions']}.")
        for row in source["top_pages"][:3]:
            lines.append(f"  - Page {row['url']}: clicks {row['clicks']}, impressions {row['impressions']}.")
    return lines


def _finding_lines(claim_gate: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for finding in claim_gate.get("findings") or []:
        evidence = ", ".join(f"`{item}`" for item in finding.get("evidence_ids") or []) or "`none`"
        if finding.get("status") == "blocked":
            lines.append(f"- Limitation: {finding.get('text')} Reason: `{finding.get('reason')}`. Evidence: {evidence}.")
        else:
            lines.append(f"- {finding.get('label')}: {finding.get('text')} Evidence: {evidence}.")
    return lines or ["- No gated findings were generated from the supplied artifacts."]


def _research_lines(research: dict[str, Any]) -> list[str]:
    lines = [
        f"- Keyword set: `{research['keyword_set_id']}` / `{research['keyword_set_hash']}`.",
        f"- Rows: {research['row_count']}; quality `{research['overall_quality']}`.",
    ]
    lines.extend([f"- {row['title']} - {row['url']} ({row['citation_id']})." for row in research["rows"]] or ["- No research rows supplied."])
    return lines


def _content_lines(content: dict[str, Any]) -> list[str]:
    lines = [
        f"- Keyword set: `{content['keyword_set_id']}` / `{content['keyword_set_hash']}`.",
        f"- Pages: {content['page_count']}; quality `{content['overall_quality']}`.",
    ]
    for page in content["pages"]:
        lines.append(f"- {page['title']} - {page['url']} ({page['word_count']} words, {page['citation_id']}).")
        if page["headings"]:
            lines.append(f"  - Headings: {', '.join(page['headings'])}.")
        if page["excerpt"]:
            lines.append(f"  - Excerpt: {page['excerpt']}")
    if not content["pages"]:
        lines.append("- No content extracts supplied.")
    return lines


def _serp_lines(serp: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    if not serp["present"]:
        return ["- SERP artifact was not provided; cannot include rank, SERP feature, or share-of-voice evidence."]
    lines = [
        f"- Rows: {serp['row_count']}; coverage `{serp['coverage']}`.",
        f"- Protocol hashes: {', '.join(serp['protocol_hashes']) or 'none'}.",
    ]
    deterministic_serp = _has_deterministic_serp(serp)
    if deterministic_serp:
        lines.extend([f"- `{row['keyword']}` rank {row['rank']}: {row['domain']} - {row['url']}." for row in serp.get("display_rows") or []] or ["- No SERP rows supplied."])
    else:
        lines.append("- SERP rank rows are withheld because deterministic protocol hashes are missing.")
    if metrics["present"]:
        lines.append(f"- Metrics quality `{metrics['quality']}`; conclusion `{metrics['conclusion_status']}`; comparability `{metrics['comparability']}`.")
        if not deterministic_serp:
            lines.append("- Metrics SOV/rank rows are withheld because deterministic SERP protocol hashes are missing.")
            return lines
        if not _serp_metrics_identity_matches(serp, metrics):
            lines.append("- Metrics SOV/rank rows are withheld because SERP and metrics protocol identity differ.")
            return lines
        for row in metrics["competitors"]:
            if _sov_metric_eligible(metrics, row) and _matching_serp_ids(serp, row):
                lines.append(f"- Competitor `{row['competitor_id']}`: SOV {row['share_of_voice']}, best rank {row['best_rank']}.")
        for row in metrics["owned"]:
            if _sov_metric_eligible(metrics, row) and _matching_serp_ids(serp, row):
                lines.append(f"- Owned `{row['competitor_id']}`: SOV {row['share_of_voice']}, best rank {row['best_rank']}.")
    return lines


def _next_actions(extract: dict[str, Any]) -> list[str]:
    actions = ["- Use citation IDs and source artifact hashes when making claims outside this report."]
    if not extract["evidence"]["serp"]["present"]:
        actions.append("- Add a SERP artifact to qualify ranking and share-of-voice claims.")
    if not extract["evidence"]["metrics"]["present"]:
        actions.append("- Add competitor metrics to compare owned and competitor visibility.")
    return actions


QUALITY_EXPLANATIONS = {
    "live": "live data from the source",
    "partial": "incomplete data: the source is partially available or some blocks were not collected",
    "research-only": "a research sample, not equivalent to confirmed source statistics",
    "unsupported": "the source is not supported by the current configuration",
    "stale": "stale data; cannot be used for fresh conclusions without re-verification",
    "failed": "data collection failed",
    "local-only": "a local snapshot without a fresh network measurement",
    "not_comparable": "data is not comparable due to metric or baseline differences",
    "not_provided": "the optional artifact was not provided",
}


def _decision_report_model(extract: dict[str, Any]) -> dict[str, Any]:
    evidence = extract["evidence"]
    deterministic_serp = _has_deterministic_serp(evidence["serp"])
    serp_scope_blocked = _serp_scope_gate_blocks_report(extract)
    prioritized = _prioritized_findings(extract)
    missing_reasons = []
    if len(prioritized) < 5:
        missing_reasons.extend(_missing_finding_reasons(extract, target=5, actual=len(prioritized)))
    summary = [
        _summary_coverage_sentence(evidence),
        _summary_priority_sentence(prioritized, missing_reasons),
        _summary_serp_sentence(evidence["serp"], evidence["metrics"], deterministic_serp, serp_scope_blocked),
    ]
    return {
        "schema": "seo-observer.decision_report.v1",
        "language": "en",
        "deterministic_serp": deterministic_serp,
        "executive_summary": summary,
        "source_coverage": _source_coverage_model(evidence),
        "prioritized_findings": prioritized,
        "missing_finding_reasons": missing_reasons,
    }


def _source_coverage_model(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    provider = evidence["provider"]
    research = evidence["research"]
    content = evidence["content"]
    serp = evidence["serp"]
    metrics = evidence["metrics"]
    rows = [
        {
            "name": "Statistics providers",
            "quality": provider["overall_quality"],
            "coverage": f"{provider['source_count']} source(s); query and page rows are used as confirmed first-party statistics.",
        },
        {
            "name": "Competitor research",
            "quality": research["overall_quality"],
            "coverage": f"{research['row_count']} candidate row(s); these are hypotheses until independently confirmed.",
        },
        {
            "name": "Page content",
            "quality": content["overall_quality"],
            "coverage": f"{content['page_count']} extracted page(s); usable for topic conclusions only when text extraction succeeded.",
        },
    ]
    if serp["present"]:
        determinism = "deterministic protocol present" if _has_deterministic_serp(serp) else "no protocol_hashes, ranks are withheld from conclusions"
        rows.append(
            {
                "name": "SERP",
                "quality": "live" if _has_deterministic_serp(serp) else "partial",
                "coverage": f"{serp['row_count']} row(s), coverage {serp['coverage']}; {determinism}.",
            }
        )
    else:
        rows.append({"name": "SERP", "quality": "not_provided", "coverage": "artifact not provided; no rank or SOV claims are made."})
    if metrics["present"]:
        rows.append(
            {
                "name": "Competitor metrics",
                "quality": metrics.get("quality") or "partial",
                "coverage": f"status {_status_ru(metrics.get('conclusion_status') or 'none')}; comparability {_status_ru(metrics.get('comparability') or 'none')}.",
            }
        )
    else:
        rows.append({"name": "Competitor metrics", "quality": "not_provided", "coverage": "artifact not provided; visibility comparison is unavailable."})
    return rows


def _is_finding_out_of_scope(finding: dict[str, Any], extract: dict[str, Any]) -> bool:
    market_scope = extract.get("market_scope") or {}
    all_out_of_scope = {
        scope
        for market in market_scope.get("markets") or []
        for scope in market.get("out_of_scope") or []
    }
    if not all_out_of_scope:
        return False

    claim_type = str(finding.get("claim_type") or "")
    serp = extract.get("evidence", {}).get("serp") or {}
    serp_engine = ""
    for row in serp.get("rows") or []:
        serp_engine = str(row.get("search_engine") or "")
        if serp_engine:
            break
    if not serp_engine and serp.get("present"):
        serp_engine = "google"

    if "google_worldwide_serp_competitor_lens" in all_out_of_scope:
        if claim_type in {"rank", "sov", "trend"} and serp_engine == "google":
            return True
    if "default_ru_yandex_competitor_lens" in all_out_of_scope:
        if claim_type in {"rank", "sov", "trend"} and serp_engine == "yandex":
            return True

    return False


def _prioritized_findings(extract: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for finding in extract.get("claim_gate", {}).get("findings") or []:
        if finding.get("status") != "allowed":
            continue
        claim_type = str(finding.get("claim_type") or "")
        if claim_type == "action":
            continue
        if _is_finding_out_of_scope(finding, extract):
            continue
        output.append(_decision_finding_from_claim(finding))
    output.sort(key=lambda item: (_severity_weight(item["severity"]), -len(item["evidence_ids"]), item["title"]))
    return output[:8]


def _decision_finding_from_claim(finding: dict[str, Any]) -> dict[str, Any]:
    claim_type = str(finding.get("claim_type") or "")
    raw_text = str(finding.get("text") or "")
    evidence_ids = [str(item) for item in finding.get("evidence_ids") or []]
    title = _russian_claim_title(claim_type, raw_text)
    quality = finding.get("quality")
    return {
        "severity": _finding_severity(claim_type, raw_text),
        "confidence": _finding_confidence(claim_type, quality=quality),
        "title": title,
        "evidence": _russian_claim_text(claim_type, raw_text),
        "evidence_ids": evidence_ids,
        "why_it_matters": _why_it_matters(claim_type),
        "action": _finding_action(claim_type),
    }


def _russian_claim_title(claim_type: str, text: str) -> str:
    if claim_type == "source_performance":
        if " page " in text:
            return "A page with confirmed organic demand exists"
        return "A query with confirmed organic demand exists"
    if claim_type == "candidate_observation":
        return "A candidate for competitor analysis was found"
    if claim_type == "topic_observation":
        return "A page topic for a content decision was found"
    if claim_type == "rank":
        return "A confirmed SERP rank observation exists"
    if claim_type == "sov":
        return "A confirmed share-of-voice value exists for a competitor"
    if claim_type == "trend":
        return "A confirmed rank change exists"
    return "A verifiable observation exists"


def _russian_claim_text(claim_type: str, text: str) -> str:
    if claim_type == "source_performance":
        source = _match_text(r"`([^`]+)`", text)
        query = _match_text(r"query `([^`]+)`", text)
        page = _match_text(r"page (https?://\S+)", text)
        clicks = _match_text(r"recorded ([0-9.]+) clicks", text)
        impressions = _match_text(r"and ([0-9.]+) impressions", text)
        subject = f"query `{query}`" if query else f"page {page}" if page else "row"
        return f"`{source}`: {subject} recorded {clicks or '?'} clicks and {impressions or '?'} impressions."
    if claim_type == "candidate_observation":
        return text
    if claim_type == "topic_observation":
        return text
    if claim_type == "rank":
        if "out of market scope" in text:
            return "The rank conclusion is intentionally out of market scope."
        if "require" in text:
            return "The rank conclusion requires a SERP artifact with deterministic protocol_hashes."
        keyword = _match_text(r"`([^`]+)`", text)
        rank = _match_text(r"rank ([0-9.]+)", text)
        domain = _match_text(r"for ([^.]+(?:\.[^. ]+)+)", text)
        return f"Query `{keyword}` has deterministic rank {rank} for domain {domain}."
    if claim_type == "sov":
        if "out of market scope" in text:
            return "The share-of-voice conclusion is intentionally out of market scope."
        if "require" in text:
            competitor = _match_text(r"`([^`]+)`", text)
            kind = "Competitor" if text.startswith("Competitor") else "Owned site"
            return f"{kind} `{competitor}`: the share-of-voice conclusion requires a matching deterministic SERP row."
        kind = "Competitor" if text.startswith("Competitor") else "Owned site"
        competitor = _match_text(r"`([^`]+)`", text)
        sov_raw = _match_text(r"SOV ([0-9.]+)", text)
        sov = _safe_number(float(sov_raw)) if sov_raw else None
        best_rank = _match_text(r"best rank ([0-9]+(?:\.[0-9]+)?)", text)
        sov_text = f"{sov * 100:.1f}%" if isinstance(sov, (int, float)) else "?"
        return f"{kind} `{competitor}`: share of voice {sov_text}, best rank {best_rank}."
    if claim_type == "trend":
        if "out of market scope" in text:
            return "The trend conclusion is intentionally out of market scope."
        if "require" in text:
            return "The trend conclusion requires a comparable baseline and confirmed movement data."
        kind = "Competitor" if text.startswith("Competitor") else "Owned site" if text.startswith("Owned") else ""
        competitor = _match_text(r"`([^`]+)`", text)
        delta = _match_text(r"by ([0-9]+(?:\.[0-9]+)?)", text)
        direction = "improved" if "improved" in text else "declined" if "declined" in text else "held"
        prefix = f"{kind} " if kind else ""
        return f"{prefix}`{competitor}`: median rank {direction} by {delta}."
    return text


def _match_text(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _finding_severity(claim_type: str, text: str = "") -> str:
    if claim_type == "source_performance":
        clicks_match = re.search(r"recorded ([0-9]+(?:\.[0-9]+)?) clicks", text)
        impressions_match = re.search(r"and ([0-9]+(?:\.[0-9]+)?) impressions", text)
        clicks = float(clicks_match.group(1)) if clicks_match else 0.0
        impressions = float(impressions_match.group(1)) if impressions_match else 0.0
        if clicks >= 5 or impressions >= 50:
            return "high"
        if clicks > 0 or impressions > 0:
            return "medium"
        return "low"

    if claim_type == "sov":
        sov_match = re.search(r"SOV ([0-9]+(?:\.[0-9]+)?)", text)
        sov = float(sov_match.group(1)) if sov_match else 0.0
        return "high" if sov >= 0.05 else "medium"

    if claim_type == "trend":
        delta_match = re.search(r"by ([0-9]+(?:\.[0-9]+)?)", text)
        delta = float(delta_match.group(1)) if delta_match else 0.0
        return "high" if delta >= 5.0 else "medium"

    if claim_type == "rank":
        rank_match = re.search(r"rank ([0-9]+(?:\.[0-9]+)?)", text)
        rank = float(rank_match.group(1)) if rank_match else 10.0
        return "high" if rank <= 3.0 else "medium"

    return {
        "candidate_observation": "medium",
        "topic_observation": "medium",
    }.get(claim_type, "low")


def _finding_confidence(claim_type: str, quality: str | None = None) -> str:
    base = {
        "source_performance": "high",
        "sov": "high",
        "rank": "high",
        "trend": "medium",
        "candidate_observation": "medium",
        "topic_observation": "medium",
    }.get(claim_type, "low")
    if not quality or quality == "live":
        return base
    if quality in {"partial", "local-only"}:
        return "medium" if base == "high" else base
    if quality in {"stale", "unsupported", "failed", "not_comparable"}:
        return "low"
    return base


def _why_it_matters(claim_type: str) -> str:
    return {
        "source_performance": "This is already confirmed demand or a landing page; a decision can be made without waiting for external SERP measurements.",
        "candidate_observation": "The candidate helps narrow manual competitor review instead of spreading analysis across irrelevant domains.",
        "topic_observation": "The topic shows which content can be compared or reworked first.",
        "rank": "The rank confirms visibility within a specific SERP protocol and is suitable for a targeted action.",
        "sov": "Share of voice shows who currently holds more of the SERP for a comparable keyword set.",
        "trend": "The trend helps distinguish a one-off snapshot from a change worth factoring into the work plan.",
    }.get(claim_type, "The observation is usable only together with the cited evidence.")


def _finding_action(claim_type: str) -> str:
    return {
        "source_performance": "Review the snippet and content of this page/query, then schedule work to grow CTR or expand intent coverage.",
        "candidate_observation": "Confirm the domain with an independent SERP measurement before drawing rank or share-of-voice conclusions.",
        "topic_observation": "Compare the page structure with current landing pages and identify missing blocks.",
        "rank": "Inspect the URL and snippet in this SERP; do not extrapolate the conclusion to other regions or devices without a new protocol.",
        "sov": "Inspect the competitor pages with the highest share and determine which intents they cover better.",
        "trend": "Investigate the causes of the change and refresh the baseline after the next comparable measurement.",
    }.get(claim_type, "Use the conclusion only together with its evidence ID.")


def _severity_weight(value: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value, 3)


def _missing_finding_reasons(extract: dict[str, Any], *, target: int, actual: int) -> list[str]:
    evidence = extract["evidence"]
    reasons = [f"Supported findings: {actual} of {target}; missing items are not synthesized without evidence."]
    if not _has_deterministic_serp(evidence["serp"]):
        reasons.append("No deterministic SERP protocol; rank/SOV conclusions are blocked.")
    if not evidence["metrics"].get("present"):
        reasons.append("No competitor metrics artifact; visibility comparison is unavailable.")
    if evidence["research"]["row_count"] == 0:
        reasons.append("No competitor research rows.")
    if evidence["content"]["page_count"] == 0:
        reasons.append("No extracted content pages.")
    return reasons


def _summary_coverage_sentence(evidence: dict[str, Any]) -> str:
    return (
        f"Coverage: {evidence['provider']['source_count']} provider source(s), "
        f"{evidence['research']['row_count']} research rows, "
        f"{evidence['content']['page_count']} content pages."
    )


def _summary_priority_sentence(prioritized: list[dict[str, Any]], missing_reasons: list[str]) -> str:
    if prioritized:
        return f"Prioritized findings: {len(prioritized)}; each is bound to an evidence ID and an action."
    return "No prioritized findings were formed: " + " ".join(missing_reasons)


def _has_eligible_matching_sov(serp: dict[str, Any], metrics: dict[str, Any]) -> bool:
    if not metrics.get("present") or not _serp_metrics_identity_matches(serp, metrics):
        return False
    rows = list(metrics.get("competitors") or []) + list(metrics.get("owned") or [])
    return any(_sov_metric_eligible(metrics, row) and bool(_matching_serp_ids(serp, row)) for row in rows)


def _serp_scope_gate_blocks_report(extract: dict[str, Any]) -> bool:
    return any(
        item.get("status") == "blocked"
        and item.get("reason") == "blocked_market_out_of_scope"
        and item.get("claim_type") in {"rank", "sov", "trend"}
        for item in extract.get("claim_gate", {}).get("findings") or []
    )


def _summary_serp_sentence(serp: dict[str, Any], metrics: dict[str, Any], deterministic_serp: bool, serp_scope_blocked: bool = False) -> str:
    if serp_scope_blocked:
        return "SERP/SOV are not promoted to a separate section: the market scope gate blocked conclusions for this SERP artifact."
    if deterministic_serp:
        if not metrics.get("present"):
            suffix = "SOV is unavailable without metrics."
        elif _has_eligible_matching_sov(serp, metrics):
            suffix = "SOV is available when SERP and metrics identities match."
        else:
            suffix = "SOV is unavailable: metrics are ineligible or not matched to SERP."
        return f"Deterministic SERP observations are available: {serp['row_count']} row(s). {suffix}"
    if serp.get("present"):
        return "SERP/SOV are not promoted to a separate section: the SERP artifact has no deterministic protocol_hashes."
    return "SERP/SOV are not promoted to a separate section: no SERP artifact was provided."


def _source_coverage_lines(decision: dict[str, Any]) -> list[str]:
    lines = []
    explained: set[str] = set()
    for source in decision["source_coverage"]:
        quality = source["quality"]
        explanation = QUALITY_EXPLANATIONS.get(quality, "quality code without a dedicated dictionary entry; interpret with caution")
        suffix = f" ({explanation})" if quality not in explained else ""
        explained.add(quality)
        lines.append(f"- {source['name']}: {source['coverage']} Quality: `{quality}`{suffix}.")
    return lines


def _market_note_ru(note: str) -> str:
    if note.startswith("Primary Yandex sources: ") and note.endswith("."):
        raw_sources = note.removeprefix("Primary Yandex sources: ").removesuffix(".").split(", ")
        source_map = {
            "Yandex Webmaster": "Yandex Webmaster",
            "Yandex Metrica": "Yandex Metrica",
            "Wordstat": "Wordstat",
        }
        sources = [source_map.get(source, source) for source in raw_sources]
        return _primary_sources_sentence(sources, scope="for the project")
    if note.startswith("Primary Google-worldwide sources: ") and note.endswith("."):
        raw_sources = note.removeprefix("Primary Google-worldwide sources: ").removesuffix(".").split(", ")
        return _primary_sources_sentence(_google_source_labels_ru(raw_sources, worldwide=True), scope="for worldwide promotion")
    if note.startswith("Primary Google sources: ") and note.endswith("."):
        raw_sources = note.removeprefix("Primary Google sources: ").removesuffix(".").split(", ")
        return _primary_sources_sentence(_google_source_labels_ru(raw_sources, worldwide=False), scope="for promotion")
    return note


def _primary_sources_sentence(sources: list[str], *, scope: str) -> str:
    if len(sources) == 1:
        return f"{sources[0]} is the primary source {scope}."
    return f"{_join_ru(sources)} are the primary sources {scope}."


def _join_ru(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _google_source_labels_ru(sources: list[str], *, worldwide: bool) -> list[str]:
    labels = []
    for source in sources:
        if source == "Google SERP":
            labels.append("Google worldwide SERP" if worldwide else "Google SERP")
        elif source == "Google GSC":
            labels.append("GSC")
        else:
            labels.append(source)
    return labels


def _market_scope_decision_lines(market_scope: dict[str, Any]) -> list[str]:
    lines = []
    for market in market_scope.get("markets") or []:
        intent = "primary" if market["intent"] == "primary" else "secondary"
        lines.append(
            f"- Market `{market['id']}`: {intent}; engine `{market['search_engine']}`, provider `{market['provider']}`."
        )
    lines.extend(f"- {_market_note_ru(note)}" for note in _market_scope_interpretation_notes(market_scope))
    return lines


def _prioritized_finding_lines(decision: dict[str, Any]) -> list[str]:
    findings = decision["prioritized_findings"]
    if not findings:
        lines = [f"- {reason}" for reason in decision["missing_finding_reasons"]]
        lines.append("- Recommended action: provide the required SERP and metrics artifacts to form confirmed conclusions.")
        return lines
    lines = []
    for index, finding in enumerate(findings, start=1):
        evidence = ", ".join(f"`{item}`" for item in finding["evidence_ids"]) or "`none`"
        evidence_text = str(finding["evidence"]).rstrip(".")
        lines.append(f"{index}. {finding['title']}")
        lines.append(f"   Severity: {finding['severity']}. Confidence: {finding['confidence']}.")
        lines.append(f"   Evidence: {evidence_text}. Evidence IDs: {evidence}.")
        lines.append(f"   Why it matters: {finding['why_it_matters']}")
        lines.append(f"   Action: {finding['action']}")
    return lines


def _provider_performance_lines(provider: dict[str, Any]) -> list[str]:
    lines = [f"- Overall provider quality rating: `{provider['overall_quality']}`."]
    for source in provider["sources"]:
        msg = f" ({source['message']})" if source.get("message") else ""
        lines.append(
            f"- `{source['source']}`: quality `{source['quality']}`, diagnostics {source['finding_count']}, "
            f"queries in excerpt {len(source['top_queries'])}, pages in excerpt {len(source['top_pages'])}{msg}."
        )
    return lines


def _technical_diagnostic_lines(extract: dict[str, Any]) -> list[str]:
    provider = extract["evidence"]["provider"]
    lines = []
    for source in provider["sources"]:
        msg = f" ({source['message']})" if source.get("message") else ""
        if source["finding_count"]:
            lines.append(f"- `{source['source']}` reported diagnostics: {source['finding_count']}{msg}.")
        elif source["quality"] != "live":
            lines.append(f"- `{source['source']}` has quality `{source['quality']}`; conclusions based on it are limited{msg}.")
    blocked = [item for item in extract.get("claim_gate", {}).get("findings") or [] if item.get("status") == "blocked"]
    lines.extend(
        f"- Blocked: `{item.get('reason')}`; {_russian_claim_text(str(item.get('claim_type') or ''), str(item.get('text') or ''))}"
        for item in blocked[:8]
    )
    return lines or ["- No critical technical diagnostics in the provided artifacts."]


def _competitor_observation_lines(extract: dict[str, Any]) -> list[str]:
    research = extract["evidence"]["research"]
    content = extract["evidence"]["content"]
    lines = []
    for row in research["rows"]:
        lines.append(f"- Research hypothesis: {row['title']} - {row['url']} (`{row['citation_id']}`).")
    for page in content["pages"]:
        if _successful_content_page(page):
            lines.append(f"- Competitor/topic content hypothesis: {page['title']} - {page['url']} (`{page['citation_id']}`, {page['word_count']} words).")
    return lines or ["- Competitor observations were not confirmed by the provided research or content rows."]


def _serp_decision_lines(serp: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    lines = [
        f"- Deterministic SERP observations are available: {serp['row_count']} rows, coverage {serp['coverage']}.",
        f"- Protocols: {', '.join(serp['protocol_hashes'])}.",
    ]
    for row in serp.get("display_rows") or []:
        lines.append(f"- `{row['keyword']}`: rank {row['rank']}, domain {row['domain']}, URL {row['url']}.")
    if metrics.get("present"):
        lines.append(
            f"- Metrics: status `{_status_ru(metrics['conclusion_status'])}`, comparability `{_status_ru(metrics['comparability'])}`, coverage {metrics['coverage']}."
        )
        if _serp_metrics_identity_matches(serp, metrics):
            for row in metrics["competitors"]:
                if _sov_metric_eligible(metrics, row) and _matching_serp_ids(serp, row):
                    lines.append(f"- Competitor `{row['competitor_id']}`: share of voice {row['share_of_voice'] * 100:.1f}%, best rank {row['best_rank']}.")
            for row in metrics["owned"]:
                if _sov_metric_eligible(metrics, row) and _matching_serp_ids(serp, row):
                    lines.append(f"- Owned site `{row['competitor_id']}`: share of voice {row['share_of_voice'] * 100:.1f}%, best rank {row['best_rank']}.")
        else:
            lines.append("- SOV rows are withheld: SERP and metrics identities do not match.")
    return lines


def _limitation_lines(extract: dict[str, Any]) -> list[str]:
    lines = [f"- {item}" for item in extract.get("limitations") or []]
    missing = (extract.get("decision_report") or {}).get("missing_finding_reasons") or []
    lines.extend(f"- {item}" for item in missing)
    return lines or ["- No significant limitations were recorded in the provided artifacts."]


def _appendix_lines(extract: dict[str, Any]) -> list[str]:
    inputs = extract.get("inputs") or {}
    lines = ["### Artifacts"]
    for name in ("provider", "research", "content", "serp", "metrics"):
        item = inputs.get(name) or {}
        state = "provided" if item.get("present") else "not provided"
        path = item.get("path") or "none"
        lines.append(f"- `{name}`: {state}; path: {path}.")
    lines.extend(["", "### Row excerpts in the extract"])
    lines.append("- Provider row excerpts are in `composite-extract.json` -> `evidence.provider.sources` (full data is in the source artifact).")
    lines.append("- Research row excerpts are in `evidence.research.rows`; content excerpts are in `evidence.content.pages` (full data is in the source artifacts).")
    if _has_deterministic_serp(extract["evidence"]["serp"]):
        lines.append("- SERP row excerpts are in `evidence.serp.rows`; SOV metrics are in `evidence.metrics` (full data is in the source artifacts).")
    else:
        lines.append("- SERP rows are stored in the structured artifact but are not used for rank/SOV conclusions without deterministic protocol_hashes.")
    return lines


def _status_ru(value: Any) -> str:
    return {
        "available": "available",
        "comparable": "comparable",
        "not_comparable": "not comparable",
        "insufficient_coverage": "insufficient coverage",
        "none": "none",
    }.get(str(value), str(value))


def _source_artifact_hashes(options: CompositeReportOptions) -> dict[str, Any]:
    entries = {
        "provider": _source_entry(options.provider_artifact, present=True),
        "research": _source_entry(options.research_artifact, present=True),
        "content": _source_entry(options.content_artifact, present=True),
        "serp": _source_entry(options.serp_artifact, present=options.serp_artifact is not None),
        "metrics": _source_entry(options.metrics_artifact, present=options.metrics_artifact is not None),
    }
    return entries


def _source_entry(path: Path | None, *, present: bool) -> dict[str, Any]:
    if not present or path is None:
        return {"present": False, "path": None, "sha256": None}
    return {"present": True, "path": str(path), "sha256": _sha256_bytes(path.read_bytes())}


def _file_manifest_entry(path: Path, content_type: str, redaction_state: str) -> dict[str, str]:
    return {
        "path": str(path),
        "sha256": _sha256_bytes(path.read_bytes()),
        "content_type": content_type,
        "redaction_state": redaction_state,
    }


def _input_descriptor(path: Path | None, present: bool) -> dict[str, Any]:
    return {"present": present, "path": str(path) if path is not None else None}


def _overall_quality(values: list[str]) -> str:
    if not values:
        return "partial"
    if all(value == "live" for value in values):
        return "live"
    if any(value in {"partial", "unsupported", "stale"} for value in values):
        return "partial"
    return values[0]


def _subtitle(extract: dict[str, Any]) -> str:
    period = extract.get("period") if isinstance(extract.get("period"), dict) else {}
    start = period.get("start") or "?"
    end = period.get("end") or "?"
    return f"Local evidence assembly. Period: {start} - {end}."


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _safe_text(item) for key, item in value.items() if isinstance(item, (str, int, float, bool)) or item is None}


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _safe_text(value: Any) -> str:
    text = str(value or "")
    for pattern in SECRET_FIELD_VALUE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    for pattern in SECRET_VALUE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _assert_no_secret_markers(text: str | bytes, *, artifact_name: str) -> None:
    scanned_text = text.decode("latin-1") if isinstance(text, bytes) else text
    for pattern in SECRET_FIELD_VALUE_PATTERNS:
        if pattern.search(scanned_text):
            raise ValueError(f"{artifact_name} contains a credential-bearing field")
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(scanned_text):
            raise ValueError(f"{artifact_name} contains a credential-shaped value")


def _canonical_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ": ") if indent is None else None, indent=indent)


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
