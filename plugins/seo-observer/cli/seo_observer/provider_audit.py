from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from seo_observer import __version__
from seo_observer.config import ConfigError, compute_config_hash, observer_home
from seo_observer.ga4 import GA4Adapter, GA4Period, GA4Source, _read_token_file, google_oauth_access_token_from_file
from seo_observer.gsc import GSCAdapter, GSCPeriod, GSCSource, SearchAnalyticsQuery, _gsc_access_token_from_fields
from seo_observer.metrica import MetricaAdapter, MetricaSource, Period as MetricaPeriod
from seo_observer.report_rendering import write_polished_report_artifacts
from seo_observer.webmaster import (
    BROKEN_INTERNAL_LINKS_LIMIT_MAX,
    WebmasterAdapter,
    WebmasterPeriod,
    WebmasterSource,
)


SUPPORTED_SOURCES = frozenset({"google_search_console", "yandex_webmaster", "yandex_metrica", "ga4"})
GSC_AUDIT_DIMENSIONS = {
    "totals": (),
    "queries": ("query",),
    "pages": ("page",),
    "query_pages": ("query", "page"),
}
FORBIDDEN_TEXT_MARKERS = (
    "private_key",
    "Authorization",
    "OAuth ",
    "Bearer ",
    "refresh_token",
    "access_token",
)
YANDEX_FINDING_EXPLANATIONS = {
    "NO_ROBOTS_TXT": (
        "Яндекс не видит robots.txt. Проверить, что файл реально отдаётся для конкретного хоста "
        "и доступен Яндекс-боту."
    ),
    "FAVICON_ERROR": "Проблема с favicon. Проверить ссылку, формат, размер, HTTP-статус.",
    "BIG_FAVICON_ABSENT": "Нет большой иконки для поиска/сниппета. Добавить/проверить крупную иконку.",
    "NO_METRIKA_COUNTER_CRAWL_ENABLED": (
        "Вебмастер считает, что обход/использование данных счётчика Метрики для диагностики "
        "не включён или недоступен. Проверить настройки счётчика."
    ),
    "NO_METRIKA_COUNTER_BINDING": (
        "Счётчик Метрики не привязан к Вебмастеру. Привязать счётчик или явно оставить другой "
        "источник трафика основным."
    ),
    "NOT_IN_SPRAV": "Сайт не связан/не представлен в Яндекс Бизнес/Справочнике.",
    "NO_REGIONS": "Регион сайта не задан в Вебмастере.",
    "DUPLICATE_PAGES": (
        "Яндекс видит несколько страниц с одинаковым или слишком похожим содержимым. Проверить "
        "canonical, редиректы и URL-параметры."
    ),
    "DUPLICATE_CONTENT_ATTRS": (
        "Яндекс видит повторяющиеся title/description или другие атрибуты контента. Проверить "
        "шаблоны мета-тегов."
    ),
}


def build_provider_audit_payload(
    *,
    config: Any,
    start: str,
    end: str,
    output_dir: Path | None,
    env: dict[str, str],
    transport_factory: Callable[[str], Any],
    collector: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _validate_period(start, end)
    plan = provider_audit_source_plan(config)
    unsupported = provider_audit_unsupported_sources(config)
    missing = provider_audit_missing_required_inputs(plan, env)
    if missing:
        return {
            "ok": False,
            "error": {
                "code": "PROVIDER_AUDIT_NOT_READY",
                "message": "Required provider-audit inputs are missing.",
                "details": {"missing_required_inputs": missing},
            },
        }
    collect = collector or collect_provider_audit_sources
    sources = collect(
        config=config,
        plan=plan,
        unsupported=unsupported,
        start=start,
        end=end,
        env=env,
        transport_factory=transport_factory,
    )
    extract = {
        "schema_version": 1,
        "command": "provider-audit",
        "cli_version": __version__,
        "project": config.project.namespace,
        "period": {"start": start, "end": end, "timezone": config.project.timezone},
        "config": {
            "path": str(config.path),
            "hash": compute_config_hash(config),
        },
        "market_scope": _market_scope_extract(config),
        "quality_labels": ["live", "partial", "unsupported", "stale", "local-only", "not_comparable"],
        "sources": sources,
    }
    report_text = render_provider_audit_markdown(extract)
    root = output_dir or default_provider_audit_output_dir(config.project.namespace, start, end)
    artifacts = write_provider_audit_artifacts(root, extract, report_text)
    return {
        "ok": True,
        "command": "provider-audit",
        "project": config.project.namespace,
        "period": extract["period"],
        "output_dir": str(root),
        "market_scope": extract["market_scope"],
        "sources": summarize_source_quality(sources),
        "artifacts": artifacts,
        "report_path": artifacts["report"]["path"],
    }


def _market_scope_extract(config: Any) -> dict[str, Any]:
    markets = []
    for market in getattr(config, "markets", ()) or ():
        markets.append(
            {
                "id": str(getattr(market, "id", "")),
                "search_engine": str(getattr(market, "search_engine", "")),
                "provider": str(getattr(market, "provider", "")),
                "regions": [str(item) for item in getattr(market, "regions", ())],
                "locale": str(getattr(market, "locale", "")),
                "language": str(getattr(market, "language", "")),
                "devices": [str(item) for item in getattr(market, "devices", ())],
                "intent": str(getattr(market, "intent", "primary") or "primary"),
                "source_roles": [str(item) for item in getattr(market, "source_roles", ())],
                "out_of_scope": [str(item) for item in getattr(market, "out_of_scope", ())],
            }
        )
    primary_markets = [item["id"] for item in markets if item["intent"] == "primary"]
    return {"markets": markets, "primary_markets": primary_markets}


def provider_audit_source_plan(config: Any) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for binding in config.source_bindings:
        source = config.sources.get(binding.source)
        if source is None or not source.enabled or binding.source not in SUPPORTED_SOURCES:
            continue
        for property_id in binding.properties:
            plan.append(
                {
                    "source": binding.source,
                    "property_id": property_id,
                    "remote_id": binding.remote_id,
                    "required": source.required,
                    "fields": source.fields,
                }
            )
    return plan


def provider_audit_unsupported_sources(config: Any) -> list[dict[str, Any]]:
    unsupported: list[dict[str, Any]] = []
    for source_name, source in sorted(config.sources.items()):
        if not source.enabled or source_name in SUPPORTED_SOURCES:
            continue
        unsupported.append(
            {
                "source": source_name,
                "quality": "unsupported",
                "required": source.required,
                "message": _unsupported_message(source_name),
            }
        )
    return unsupported


def provider_audit_missing_required_inputs(plan: list[dict[str, Any]], env: dict[str, str]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    checked: set[tuple[str, str]] = set()
    for item in plan:
        if not item["required"]:
            continue
        source_name = str(item["source"])
        fields = item["fields"]
        credential_file_env = fields.get("credential_file_env")
        token_file_env = fields.get("token_file_env")
        credential_env = fields.get("credential_env")
        env_name = credential_file_env or token_file_env or credential_env
        key = (source_name, str(env_name or ""))
        if key in checked:
            continue
        checked.add(key)
        if not env_name:
            missing.append({"source": source_name, "reason": "required source has no credential env configured"})
            continue
        value = env.get(str(env_name), "")
        if not value:
            missing.append({"source": source_name, "env": str(env_name), "reason": "missing environment variable"})
            continue
        if (credential_file_env or token_file_env) and not Path(value).is_file():
            missing.append({"source": source_name, "env": str(env_name), "reason": "credential file does not exist"})
    return missing


def collect_provider_audit_sources(
    *,
    config: Any,
    plan: list[dict[str, Any]],
    unsupported: list[dict[str, Any]],
    start: str,
    end: str,
    env: dict[str, str],
    transport_factory: Callable[[str], Any],
) -> dict[str, Any]:
    sources: dict[str, Any] = {item["source"]: item for item in unsupported}
    for item in plan:
        source_name = str(item["source"])
        try:
            if not _optional_source_ready(item, env):
                result = {
                    "quality": "partial",
                    "property_id": str(item["property_id"]),
                    "remote_id": str(item["remote_id"]),
                    "required": bool(item["required"]),
                    "message": "Source is configured but local credentials are missing; live calls were skipped.",
                }
            elif source_name == "google_search_console":
                result = _collect_gsc_source(config, item, start, end, env, transport_factory)
            elif source_name == "yandex_webmaster":
                result = _collect_webmaster_source(config, item, start, end, env, transport_factory)
            elif source_name == "yandex_metrica":
                result = _collect_metrica_source(config, item, start, end, env, transport_factory)
            elif source_name == "ga4":
                result = _collect_ga4_source(config, item, start, end, env, transport_factory)
            else:
                result = {"quality": "unsupported", "required": bool(item["required"]), "message": _unsupported_message(source_name)}
        except Exception as exc:
            result = {
                "quality": "partial",
                "property_id": str(item["property_id"]),
                "remote_id": str(item["remote_id"]),
                "required": bool(item["required"]),
                "message": "Provider audit source failed; other sources were preserved.",
                "error": _safe_error(exc),
            }
        sources[source_name] = _append_source_extract(sources.get(source_name), result)
    return sources


def collect_gsc_extract(
    adapter: GSCAdapter,
    *,
    period: GSCPeriod,
    property_id: str,
    remote_id: str,
    max_rows: int = 50000,
) -> dict[str, Any]:
    search_analytics: dict[str, Any] = {}
    quality = "live"
    for section, dimensions in GSC_AUDIT_DIMENSIONS.items():
        result = adapter.fetch_search_performance(
            period,
            query=SearchAnalyticsQuery(dimensions=dimensions),
            max_rows=max_rows,
        )
        metadata = result.get("metadata") or {}
        if metadata.get("dataset_coverage") not in {None, "complete", "top_rows"}:
            quality = "partial"
        search_analytics[section] = {
            "dimensions": list(dimensions),
            "metadata": _safe_metadata(metadata),
            "rows": [_gsc_audit_row(row, dimensions) for row in result.get("observations") or [] if isinstance(row, dict)],
        }
    return {
        "quality": quality,
        "property_id": property_id,
        "remote_id": remote_id,
        "coverage_note": "GSC Search Analytics returns top rows; this is not a complete universe of all queries/pages.",
        "search_analytics": search_analytics,
    }


def parse_yandex_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("problems", payload.get("diagnostics", []))
    problems: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        iterable = []
        for code, item in raw.items():
            if isinstance(item, dict):
                iterable.append({"code": code, **item})
            else:
                iterable.append({"code": code})
    elif isinstance(raw, list):
        iterable = [item for item in raw if isinstance(item, dict)]
    else:
        iterable = []
    for item in iterable:
        code = _clean_text(item.get("name") or item.get("code") or item.get("problem_code"))
        if not code:
            continue
        state = _clean_text(item.get("state")) or "UNKNOWN"
        problems.append(
            {
                "code": code,
                "severity": _clean_text(item.get("severity") or item.get("type")) or "__all__",
                "state": state,
                "last_state_update": _clean_text(item.get("last_state_update") or item.get("lastStateUpdate")),
                "explanation": YANDEX_FINDING_EXPLANATIONS.get(code),
            }
        )
    state_counts: dict[str, int] = {}
    for item in problems:
        state = str(item["state"])
        state_counts[state] = state_counts.get(state, 0) + 1
    return {
        "all": problems,
        "present": [item for item in problems if item["state"] == "PRESENT"],
        "state_counts": dict(sorted(state_counts.items())),
    }


def parse_yandex_popular_queries(rows: list[Any]) -> list[dict[str, Any]]:
    return parse_yandex_popular_queries_with_stats(rows)["rows"]


def parse_yandex_popular_queries_with_stats(rows: list[Any]) -> dict[str, Any]:
    parsed: list[dict[str, Any]] = []
    invalid_count = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid_count += 1
            continue
        query = _clean_text(row.get("query") or row.get("query_text"))
        indicators = row.get("indicators")
        if not isinstance(indicators, dict):
            indicators = row
        impressions = _int_or_none(row.get("impressions", indicators.get("TOTAL_SHOWS")))
        clicks = _int_or_none(row.get("clicks", indicators.get("TOTAL_CLICKS")))
        position = _number_or_none(row.get("average_position", indicators.get("AVG_SHOW_POSITION")))
        if not query or impressions is None or impressions < 0:
            invalid_count += 1
            continue
        if clicks is not None and clicks < 0:
            invalid_count += 1
            continue
        parsed_row = {
            "query": query,
            "impressions": impressions,
            "clicks": clicks,
            "average_position": position,
        }
        if clicks is not None and clicks > impressions:
            parsed_row["metric_anomalies"] = ["clicks_exceed_impressions"]
        parsed.append(parsed_row)
    return {"rows": parsed, "invalid_count": invalid_count}


def render_provider_audit_markdown(extract: dict[str, Any]) -> str:
    lines: list[str] = []
    project = str(extract.get("project") or "unknown")
    period = extract.get("period") if isinstance(extract.get("period"), dict) else {}
    lines.append(f"# Provider audit: {project}")
    lines.append("")
    lines.append(f"Период: {period.get('start', '?')} - {period.get('end', '?')}")
    lines.append("")
    lines.append("## Качество источников")
    sources = extract.get("sources") if isinstance(extract.get("sources"), dict) else {}
    for source_name, source in sources.items():
        if not isinstance(source, dict):
            continue
        quality = source.get("quality") or "partial"
        message = source.get("message")
        lines.append(f"- {source_name}: {quality}" + (f" - {message}" if message else ""))
    lines.append("")
    _render_gsc(lines, sources.get("google_search_console") if isinstance(sources, dict) else None)
    _render_ga4(lines, sources.get("ga4") if isinstance(sources, dict) else None)
    _render_webmaster(lines, sources.get("yandex_webmaster") if isinstance(sources, dict) else None)
    _render_metrica(lines, sources.get("yandex_metrica") if isinstance(sources, dict) else None)
    text = "\n".join(lines).rstrip() + "\n"
    for marker in FORBIDDEN_TEXT_MARKERS:
        if marker in text:
            raise ConfigError(
                "PROVIDER_AUDIT_REDACTION_FAILED",
                "Provider audit Markdown contains forbidden secret marker.",
                {"marker": marker},
            )
    return text


def write_provider_audit_artifacts(root: Path, extract: dict[str, Any], report_text: str) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    extract_text = _canonical_json(extract, indent=2) + "\n"
    _assert_no_secret_markers(extract_text, artifact_name="provider-extract.json")
    _assert_no_secret_markers(report_text, artifact_name="provider-audit.md")
    extract_path = root / "provider-extract.json"
    report_path = root / "provider-audit.md"
    extract_path.write_text(extract_text, encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")
    polished = write_polished_report_artifacts(
        markdown_text=report_text,
        output_dir=root,
        basename="provider-audit",
        title=f"SEO-аудит источников: {extract.get('project') or 'unknown'}",
        subtitle=_provider_audit_subtitle(extract),
    )
    extract_sha = _sha256_text(extract_text)
    report_sha = _sha256_text(report_text)
    html_text = polished.html_path.read_text(encoding="utf-8")
    _assert_no_secret_markers(html_text, artifact_name="provider-audit.html")
    html_sha = _sha256_text(html_text)
    manifest = {
        "schema_version": 1,
        "command": "provider-audit",
        "project": extract.get("project"),
        "period": extract.get("period"),
        "created_at": _utc_now(),
        "privacy": {
            "human_report_redacted": True,
            "raw_provider_payload_in_human_report": False,
            "local_only": True,
        },
        "files": {
            "provider-extract.json": {
                "path": str(extract_path),
                "sha256": extract_sha,
                "content_type": "application/json",
                "redaction_state": "structured-safe-extract",
            },
            "provider-audit.md": {
                "path": str(report_path),
                "sha256": report_sha,
                "content_type": "text/markdown",
                "redaction_state": "redacted-human-report",
            },
            "provider-audit.html": {
                "path": str(polished.html_path),
                "sha256": html_sha,
                "content_type": "text/html",
                "redaction_state": "redacted-human-report",
            },
        },
    }
    pdf_artifact: dict[str, Any] | None = None
    if polished.pdf_path is not None:
        pdf_sha = _sha256_bytes(polished.pdf_path.read_bytes())
        manifest["files"]["provider-audit.pdf"] = {
            "path": str(polished.pdf_path),
            "sha256": pdf_sha,
            "content_type": "application/pdf",
            "redaction_state": "redacted-human-report",
        }
        pdf_artifact = {"path": str(polished.pdf_path), "sha256": pdf_sha}
    elif polished.pdf_error:
        manifest["pdf_error"] = {
            "code": "PROVIDER_AUDIT_PDF_RENDER_FAILED",
            "message": polished.pdf_error,
        }
    manifest_text = _canonical_json(manifest, indent=2) + "\n"
    _assert_no_secret_markers(manifest_text, artifact_name="manifest.json")
    manifest_path = root / "manifest.json"
    manifest_path.write_text(manifest_text, encoding="utf-8")
    result = {
        "manifest": {"path": str(manifest_path), "sha256": _sha256_text(manifest_text)},
        "provider_extract": {"path": str(extract_path), "sha256": extract_sha},
        "report": {"path": str(report_path), "sha256": report_sha},
        "html_report": {"path": str(polished.html_path), "sha256": html_sha},
        "report_path": str(report_path),
        "html_report_path": str(polished.html_path),
    }
    if pdf_artifact is not None:
        result["pdf_report"] = pdf_artifact
        result["pdf_report_path"] = str(polished.pdf_path)
    if polished.pdf_error:
        result["pdf_error"] = manifest["pdf_error"]
    return result


def _provider_audit_subtitle(extract: dict[str, Any]) -> str:
    period = extract.get("period") if isinstance(extract.get("period"), dict) else {}
    start = period.get("start") or "?"
    end = period.get("end") or "?"
    timezone = period.get("timezone") or "локальный часовой пояс проекта"
    return f"Период: {start} - {end}. Часовой пояс: {timezone}."


def summarize_source_quality(sources: dict[str, Any]) -> dict[str, Any]:
    return {
        source_name: {
            "quality": source.get("quality") if isinstance(source, dict) else "partial",
            "message": source.get("message") if isinstance(source, dict) else None,
        }
        for source_name, source in sorted(sources.items())
    }


def default_provider_audit_output_dir(project: str, start: str, end: str) -> Path:
    return observer_home() / "projects" / project / "provider-audits" / f"{start}_{end}"


def _collect_gsc_source(
    config: Any,
    item: dict[str, Any],
    start: str,
    end: str,
    env: dict[str, str],
    transport_factory: Callable[[str], Any],
) -> dict[str, Any]:
    fields = item["fields"]
    adapter = GSCAdapter(
        GSCSource(
            site_url=str(item["remote_id"]),
            credential_file_env=str(fields.get("credential_file_env") or ""),
            property_id=str(item["property_id"]),
            timezone=config.project.timezone,
            access_token=_gsc_access_token_from_fields(fields, env),
            row_limit=int(fields.get("row_limit") or 500),
            data_state=str(fields.get("data_state") or "final"),
            finalize_after=fields.get("finalize_after"),
        ),
        transport_factory("https://www.googleapis.com"),
    )
    return collect_gsc_extract(
        adapter,
        period=GSCPeriod(start, end),
        property_id=str(item["property_id"]),
        remote_id=str(item["remote_id"]),
        max_rows=int(fields.get("audit_row_limit") or 5000),
    )


def _collect_webmaster_source(
    config: Any,
    item: dict[str, Any],
    start: str,
    end: str,
    env: dict[str, str],
    transport_factory: Callable[[str], Any],
) -> dict[str, Any]:
    fields = item["fields"]
    adapter = WebmasterAdapter(
        WebmasterSource(
            user_id=str(fields["user_id"]),
            host_id=str(item["remote_id"]),
            token=env[str(fields["credential_env"])],
            property_id=str(item["property_id"]),
            timezone=config.project.timezone,
            limit=int(fields.get("limit") or 500),
            finalize_after=fields.get("finalize_after"),
        ),
        transport_factory("https://api.webmaster.yandex.net"),
    )
    quality = "live"
    diagnostics: dict[str, Any] = {"all": [], "present": [], "state_counts": {}}
    popular_queries: list[dict[str, Any]] = []
    sitemaps: list[dict[str, Any]] = []
    broken_links: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        diagnostics = parse_yandex_diagnostics(adapter._fetch(adapter._request("diagnostics", params={})))
    except Exception as exc:
        quality = "partial"
        errors.append({"scope": "diagnostics", **_safe_error(exc)})
    try:
        popular = adapter.fetch_popular_queries(WebmasterPeriod(start, end))
        popular_stats = parse_yandex_popular_queries_with_stats(popular.get("observations") or [])
        popular_queries = popular_stats["rows"]
        if popular_stats["invalid_count"]:
            quality = "partial"
            errors.append(
                {
                    "scope": "popular_queries",
                    "code": "NORMALIZATION_INVALID_SEARCH_ROW",
                    "message": f"{popular_stats['invalid_count']} popular query row(s) failed normalization validation.",
                }
            )
    except Exception as exc:
        quality = "partial"
        errors.append({"scope": "popular_queries", **_safe_error(exc)})
    try:
        sitemap_result = adapter.fetch_sitemaps(effective_at=end)
        sitemaps = [_webmaster_sitemap_row(row) for row in sitemap_result.get("observations") or [] if isinstance(row, dict)]
    except Exception as exc:
        quality = "partial"
        errors.append({"scope": "sitemaps", **_safe_error(exc)})
    try:
        limit = max(1, min(int(adapter.source.limit), BROKEN_INTERNAL_LINKS_LIMIT_MAX))
        page = adapter._fetch(adapter._request("links/internal/broken/samples", params={"offset": 0, "limit": limit}))
        broken_links = parse_broken_internal_links(page)
    except Exception as exc:
        quality = "partial"
        errors.append({"scope": "broken_internal_links", **_safe_error(exc)})
    result: dict[str, Any] = {
        "quality": quality,
        "property_id": str(item["property_id"]),
        "remote_id": str(item["remote_id"]),
        "diagnostics": diagnostics,
        "popular_queries": popular_queries,
        "sitemaps": sitemaps,
        "broken_internal_links": broken_links,
        "affected_pages_note": (
            "Yandex Webmaster diagnostics do not include affected pages by themselves; adjacent endpoints "
            "such as broken internal link samples may include concrete URLs."
        ),
    }
    if errors:
        result["errors"] = errors
    return result


def _collect_ga4_source(
    config: Any,
    item: dict[str, Any],
    start: str,
    end: str,
    env: dict[str, str],
    transport_factory: Callable[[str], Any],
) -> dict[str, Any]:
    fields = item["fields"]
    adapter = GA4Adapter(
        GA4Source(
            property_resource=str(item["remote_id"]),
            token=_ga4_access_token(fields, env),
            property_id=str(item["property_id"]),
            timezone=config.project.timezone,
            limit=int(fields.get("limit") or 10000),
            finalize_after=fields.get("finalize_after"),
        ),
        transport_factory("https://analyticsdata.googleapis.com"),
    )
    return adapter.fetch_audit_reports(GA4Period(start, end))


def _collect_metrica_source(
    config: Any,
    item: dict[str, Any],
    start: str,
    end: str,
    env: dict[str, str],
    transport_factory: Callable[[str], Any],
) -> dict[str, Any]:
    fields = item["fields"]
    result = MetricaAdapter(
        MetricaSource(
            counter_id=str(fields["counter_id"]),
            token=env[str(fields["credential_env"])],
            property_id=str(item["property_id"]),
            timezone=config.project.timezone,
            accuracy=str(fields.get("accuracy") or "full"),
            finalize_after=fields.get("finalize_after"),
        ),
        transport_factory("https://api-metrika.yandex.net"),
    ).fetch_organic_traffic(MetricaPeriod(start, end))
    observations = result.get("observations") or []
    return {
        "quality": "live",
        "property_id": str(item["property_id"]),
        "remote_id": str(item["remote_id"]),
        "metadata": _safe_metadata(result.get("metadata") or {}),
        "organic_traffic": [
            {
                "visits": _int_or_none(row.get("visits")),
                "users": _int_or_none(row.get("users")),
                "search_engine": _clean_text(row.get("search_engine")) or "__all__",
                "landing_page_id": _clean_text(row.get("landing_page_id")) or "__all__",
            }
            for row in observations
            if isinstance(row, dict)
        ],
    }


def parse_broken_internal_links(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("links", payload.get("samples", []))
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_url = _clean_text(row.get("source_url") or row.get("from_url") or row.get("page_url") or row.get("url_from"))
        target_url = _clean_text(
            row.get("target_url")
            or row.get("destination_url")
            or row.get("link_url")
            or row.get("broken_url")
            or row.get("url")
            or row.get("href")
        )
        if not source_url and not target_url:
            continue
        parsed.append(
            {
                "source_url": source_url,
                "target_url": target_url,
                "status": _clean_text(row.get("status") or row.get("error_code") or row.get("indicator")),
            }
        )
    return parsed


def _render_gsc(lines: list[str], source: Any) -> None:
    if not isinstance(source, dict):
        return
    lines.append("## Google Search Console")
    lines.append("Важно: Search Analytics отдаёт top rows, а не полный universe всех запросов и URL.")
    for block in _source_property_blocks(source):
        _render_property_heading(lines, block)
        search = block.get("search_analytics") if isinstance(block.get("search_analytics"), dict) else {}
        totals = _rows_for(search, "totals")
        if totals:
            row = totals[0]
            lines.append(f"- Totals: clicks={row.get('clicks')}, impressions={row.get('impressions')}, position={row.get('average_position')}")
        _table(lines, "Top queries", ["query", "clicks", "impressions", "average_position"], _rows_for(search, "queries"))
        _table(lines, "Pages", ["page_url", "clicks", "impressions", "average_position"], _rows_for(search, "pages"))
        _table(lines, "Query x page", ["query", "page_url", "clicks", "impressions", "average_position"], _rows_for(search, "query_pages"))
    lines.append("")


def _render_ga4(lines: list[str], source: Any) -> None:
    if not isinstance(source, dict):
        return
    lines.append("## GA4")
    lines.append("Важно: GA4 показывает analytics traffic, а не поисковые показы/позиции; SEO-срез фильтруется по Organic Search.")
    for block in _source_property_blocks(source):
        _render_property_heading(lines, block)
        reports = block.get("reports") if isinstance(block.get("reports"), dict) else {}
        _table(
            lines,
            "Organic traffic totals",
            ["sessions", "activeUsers", "screenPageViews", "bounceRate", "averageSessionDuration"],
            _report_rows(reports, "totals"),
        )
        _table(
            lines,
            "Organic landing pages",
            ["landingPagePlusQueryString", "sessions", "activeUsers", "screenPageViews", "bounceRate"],
            _report_rows(reports, "landing_pages"),
        )
        _table(
            lines,
            "Organic source / medium",
            ["sessionSourceMedium", "sessionDefaultChannelGroup", "sessions", "activeUsers"],
            _report_rows(reports, "source_medium"),
        )
        _table(
            lines,
            "Organic landing page x source / medium",
            ["landingPagePlusQueryString", "sessionSourceMedium", "sessions", "activeUsers"],
            _report_rows(reports, "landing_source_medium"),
        )
        _table(lines, "Organic device", ["deviceCategory", "sessions", "activeUsers"], _report_rows(reports, "device"))
        _table(lines, "Organic geography", ["country", "city", "sessions", "activeUsers"], _report_rows(reports, "geo"))
        _table(lines, "Organic top events", ["eventName", "eventCount"], _report_rows(reports, "top_events"))
    lines.append("")


def _render_webmaster(lines: list[str], source: Any) -> None:
    if not isinstance(source, dict):
        return
    lines.append("## Yandex Webmaster")
    for block in _source_property_blocks(source):
        _render_property_heading(lines, block)
        diagnostics = block.get("diagnostics") if isinstance(block.get("diagnostics"), dict) else {}
        present = diagnostics.get("present") if isinstance(diagnostics.get("present"), list) else []
        lines.append(f"- PRESENT diagnostics: {len(present)}")
        if present:
            lines.append("")
            lines.append("### PRESENT diagnostics")
            for item in present:
                if not isinstance(item, dict):
                    continue
                lines.append(f"- {item.get('code')} ({item.get('severity')})")
        lines.append("")
        lines.append("### Расшифровка findings")
        for item in present:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            explanation = item.get("explanation") or YANDEX_FINDING_EXPLANATIONS.get(code)
            if explanation:
                lines.append(f"- {code}: {explanation}")
        if not present:
            lines.append("- PRESENT findings не обнаружены в diagnostics payload.")
        _table(lines, "Popular queries", ["query", "clicks", "impressions", "average_position"], block.get("popular_queries") or [])
        _table(lines, "Sitemaps", ["sitemap_url", "submitted_urls", "indexed_urls", "problem_count"], block.get("sitemaps") or [])
        lines.append("diagnostics сами по себе affected pages не дают; broken-link endpoint может давать конкретные URL.")
        _table(lines, "Broken internal link samples", ["source_url", "target_url", "status"], block.get("broken_internal_links") or [])
    lines.append("")


def _render_metrica(lines: list[str], source: Any) -> None:
    if not isinstance(source, dict):
        return
    lines.append("## Yandex Metrica")
    for block in _source_property_blocks(source):
        _render_property_heading(lines, block)
        _table(lines, "Organic traffic", ["visits", "users", "search_engine", "landing_page_id"], block.get("organic_traffic") or [])
    lines.append("")


def _table(lines: list[str], title: str, columns: list[str], rows: list[Any], *, limit: int = 20) -> None:
    lines.append("")
    lines.append(f"### {title}")
    clean_rows = [row for row in rows if isinstance(row, dict)]
    if not clean_rows:
        lines.append("- Нет данных.")
        return
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in clean_rows[:limit]:
        lines.append("| " + " | ".join(_md_cell(row.get(column)) for column in columns) + " |")


def _rows_for(search: dict[str, Any], section: str) -> list[Any]:
    block = search.get(section) if isinstance(search, dict) else None
    return block.get("rows") if isinstance(block, dict) and isinstance(block.get("rows"), list) else []


def _report_rows(reports: dict[str, Any], section: str) -> list[Any]:
    block = reports.get(section) if isinstance(reports, dict) else None
    return block.get("rows") if isinstance(block, dict) and isinstance(block.get("rows"), list) else []


def _gsc_audit_row(row: dict[str, Any], dimensions: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "clicks": _int_or_none(row.get("clicks")),
        "impressions": _int_or_none(row.get("impressions")),
        "ctr": _number_or_none(row.get("ctr")),
        "average_position": _number_or_none(row.get("average_position")),
    }
    if "query" in dimensions:
        result["query"] = _clean_text(row.get("query_text")) or "__all__"
    if "page" in dimensions:
        result["page_url"] = _clean_text(row.get("page_url")) or "__all__"
    return result


def _webmaster_sitemap_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sitemap_url": _clean_text(row.get("sitemap_url")),
        "sitemap_id": _clean_text(row.get("sitemap_id")),
        "submitted_urls": _int_or_none(row.get("submitted_urls")),
        "indexed_urls": _int_or_none(row.get("indexed_urls")),
        "problem_count": _int_or_none(row.get("problem_count")),
    }


def _append_source_extract(current: Any, new: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(current, dict) or current.get("quality") == "unsupported":
        entry: dict[str, Any] = {"quality": new.get("quality") or "partial", "properties": []}
    elif isinstance(current.get("properties"), list):
        entry = current
    else:
        entry = {"quality": current.get("quality") or "partial", "properties": [current]}
    entry["properties"].append(new)
    entry["quality"] = _aggregate_source_quality(entry["properties"])
    return entry


def _aggregate_source_quality(properties: list[Any]) -> str:
    qualities = [str(item.get("quality") or "partial") for item in properties if isinstance(item, dict)]
    if not qualities:
        return "partial"
    priority = {"partial": 50, "stale": 40, "not_comparable": 35, "unsupported": 30, "local-only": 20, "live": 10}
    return max(qualities, key=lambda item: priority.get(item, 45))


def _source_property_blocks(source: dict[str, Any]) -> list[dict[str, Any]]:
    properties = source.get("properties")
    if isinstance(properties, list):
        return [item for item in properties if isinstance(item, dict)]
    return [source]


def _render_property_heading(lines: list[str], block: dict[str, Any]) -> None:
    property_id = block.get("property_id")
    remote_id = block.get("remote_id")
    if property_id or remote_id:
        lines.append("")
        lines.append(f"### Property {property_id or '__all__'}")
        if remote_id:
            lines.append(f"- Remote: {remote_id}")


def _safe_error(exc: Exception) -> dict[str, str]:
    return {
        "code": exc.__class__.__name__,
        "message": "Provider request failed; details redacted.",
    }


def _optional_source_ready(item: dict[str, Any], env: dict[str, str]) -> bool:
    fields = item["fields"]
    if item["source"] in {"google_search_console", "ga4"}:
        credential_file_env = fields.get("credential_file_env")
        if credential_file_env:
            path = env.get(str(credential_file_env))
            if path and Path(path).is_file():
                return True
        token_file_env = fields.get("token_file_env")
        if token_file_env:
            path = env.get(str(token_file_env))
            if path and Path(path).is_file() and _read_token_file(path):
                return True
        credential_env = fields.get("credential_env")
        return bool(credential_env and env.get(str(credential_env)))
    credential_env = fields.get("credential_env")
    return bool(credential_env and env.get(str(credential_env)))


def _unsupported_message(source_name: str) -> str:
    if source_name == "ga4":
        return "GA4 is unsupported by current seo-observer provider audit."
    return "Source is configured but this provider-audit implementation does not support it yet."


def _validate_period(start: str, end: str) -> None:
    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise ConfigError(
            "PROVIDER_AUDIT_PERIOD_INVALID",
            "`--start` and `--end` must be valid ISO dates.",
            {"start": start, "end": end},
        ) from exc
    if start_date > end_date:
        raise ConfigError(
            "PROVIDER_AUDIT_PERIOD_INVALID",
            "`--start` must be earlier than or equal to `--end`.",
            {"start": start, "end": end},
        )


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "dataset_coverage",
        "freshness",
        "comparability",
        "rows_received",
        "total_rows",
        "pages_received",
        "top_rows",
        "capped",
        "coverage_warnings",
        "data_loss_risk",
        "sampled",
        "sample_share",
    }
    return {key: metadata[key] for key in sorted(allowed) if key in metadata}




def _ga4_access_token(fields: dict[str, Any], env: dict[str, str]) -> str:
    credential_env = fields.get("credential_env")
    if credential_env and env.get(str(credential_env)):
        return str(env[str(credential_env)])
    token_file_env = fields.get("token_file_env")
    if token_file_env and env.get(str(token_file_env)):
        return google_oauth_access_token_from_file(env[str(token_file_env)])
    credential_file_env = fields.get("credential_file_env")
    if credential_file_env and env.get(str(credential_file_env)):
        return _google_analytics_access_token(env[str(credential_file_env)])
    raise ConfigError(
        "GA4_AUTH_NOT_READY",
        "GA4 source requires credential_env, token_file_env, or credential_file_env.",
        {},
    )


def _google_analytics_access_token(credential_file: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise ConfigError(
            "GA4_AUTH_DEPENDENCY_MISSING",
            "GA4 provider audit requires google-auth.",
            {"package": "google-auth"},
        ) from exc
    credentials = service_account.Credentials.from_service_account_file(
        credential_file,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    credentials.refresh(Request())
    return str(credentials.token)


def _assert_no_secret_markers(text: str, *, artifact_name: str) -> None:
    for marker in FORBIDDEN_TEXT_MARKERS:
        if marker in text:
            raise ConfigError(
                "PROVIDER_AUDIT_REDACTION_FAILED",
                "Provider audit artifact contains forbidden secret marker.",
                {"artifact": artifact_name, "marker": marker},
            )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int_or_none(value: Any) -> int | None:
    number = _number_or_none(value)
    return int(number) if number is not None else None


def _md_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), indent=indent)
