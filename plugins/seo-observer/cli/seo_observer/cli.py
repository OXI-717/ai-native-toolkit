from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import sqlite3
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seo_observer import __version__
from seo_observer import ai_prompts as ai_prompts_mod
from seo_observer import ai_readiness as ai_readiness_mod
from seo_observer.actions import ActionError, load_action, persist_action, persist_actions
from seo_observer.config import (
    ConfigError,
    ProjectRegistry,
    compute_config_hash,
    discover_project_config,
    load_project_config,
    observer_home,
)
from seo_observer.credential_source import (
    apply_credential_source,
    doctor_credential_source,
)
from seo_observer.comparisons import compare_periods
from seo_observer.competitors import (
    CompetitorAuditOptions,
    CompetitorDiscoveryOptions,
    CompetitorReportOptions,
    CompetitorResearchOptions,
    audit_competitors,
    build_competitor_report,
    discover_competitors,
    research_competitors,
)
from seo_observer.composite_report import (
    CompositeArtifactError,
    CompositeReportOptions,
    build_composite_report,
)
from seo_observer.crawl import (
    StdlibCrawlTransport,
    _normalize_url as normalize_crawl_url,
    crawl_properties,
)
from seo_observer.ga4 import (
    GA4Adapter,
    GA4Period,
    GA4Source,
    doctor_ga4_source,
    google_oauth_access_token_from_file,
)
from seo_observer.gsc import (
    GSCAdapter,
    GSCPeriod,
    GSCSource,
    SearchAnalyticsQuery,
    _gsc_access_token,
    _gsc_access_token_from_fields,
    _gsc_access_token_from_oauth_file,
    doctor_gsc_source,
)
from seo_observer.metrica import MetricaAdapter, MetricaSource, Period as MetricaPeriod, doctor_metrica_source
from seo_observer.outcomes import (
    AggregateOutcomeAdapter,
    AggregateOutcomeSource,
    HttpAggregateTransport,
    Period as OutcomePeriod,
    descriptor_from_source_fields,
    doctor_outcome_source,
    reduce_outcome_facts,
)
from seo_observer.opportunities import OpportunityOptions, build_opportunity_report
from seo_observer.provider_audit import build_provider_audit_payload, collect_provider_audit_sources
from seo_observer.report_rendering import write_polished_report_artifacts
from seo_observer.serp import doctor_serp_source
from seo_observer.webmaster import WebmasterAdapter, WebmasterPeriod, WebmasterSource, doctor_webmaster_source
from seo_observer.wordstat import doctor_wordstat_source
from seo_observer.storage import (
    CollectionRun,
    CrawlPageObservation,
    OutcomeMetricObservation,
    RawArtifact,
    SEOStorage,
    SearchPerformanceObservation,
    SourceRequest,
    StorageError,
    TrafficMetricObservation,
    default_database_path,
    resolve_raw_artifact_path,
)


NOT_IMPLEMENTED_COMMANDS: tuple[str, ...] = ()
MIN_OUTCOME_MATURATION_DAYS = 7
SERP_PROVIDER_ACCOUNTS = {"dataforseo_google_organic": "dataforseo"}


def _json_dump(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _install_check() -> dict[str, Any]:
    """Where the package is executed from.

    An editable install from a git worktree silently freezes the CLI on that
    branch: main moves ahead while the command keeps running the old code. When
    the worktree is deleted, the command stops importing at all — but that case
    never reaches this point; it is caught by the doctor check of the source
    repository.
    """
    package_dir = Path(__file__).resolve().parent
    parts = package_dir.parts
    in_worktree = ".worktrees" in parts
    return {
        "ok": not in_worktree,
        "path": str(package_dir),
        "in_worktree": in_worktree,
        "hint": (
            "package is running from a git worktree; reinstall it non-editable: "
            "uv tool install --force <plugin-root>/cli"
            if in_worktree
            else None
        ),
    }


def doctor_payload(args: argparse.Namespace | None = None) -> dict[str, Any]:
    local_home = observer_home()
    project_config = _selected_config_path(args or argparse.Namespace())
    project_check: dict[str, Any]
    extra_checks: dict[str, Any] = {}
    if project_config is None:
        project_check = {
            "ok": False,
            "path": str(Path.cwd() / ".seo-observer" / "project.toml"),
            "exists": False,
            "required": False,
            "status": "missing_project_config",
        }
    else:
        try:
            config = load_project_config(project_config)
            # The declared env file is loaded before source checks: otherwise
            # doctor reports "no token" where a token is declared and in place,
            # and the hint ends up exactly opposite to the truth.
            apply_credential_source(config.credential_source, os.environ)
            extra_checks["credentials"] = doctor_credential_source(
                config.credential_source,
                required_env=_required_env_names(config),
                env=os.environ,
            )
            project_check = {
                "ok": True,
                "path": str(config.path),
                "exists": True,
                "required": False,
                "project": config.project.namespace,
                "config_hash": compute_config_hash(config),
            }
            metrica = config.sources.get("yandex_metrica")
            if metrica is not None:
                extra_checks["yandex_metrica"] = doctor_metrica_source(
                    metrica.fields,
                    env=os.environ,
                )
            gsc = config.sources.get("google_search_console")
            if gsc is not None:
                extra_checks["google_search_console"] = doctor_gsc_source(
                    gsc.fields,
                    bindings=[
                        {
                            "source": binding.source,
                            "remote_id": binding.remote_id,
                        }
                        for binding in config.source_bindings
                        if binding.source == "google_search_console"
                    ],
                    env=os.environ,
                )
            ga4 = config.sources.get("ga4")
            if ga4 is not None:
                extra_checks["ga4"] = doctor_ga4_source(
                    ga4.fields,
                    bindings=[
                        {
                            "source": binding.source,
                            "remote_id": binding.remote_id,
                        }
                        for binding in config.source_bindings
                        if binding.source == "ga4"
                    ],
                    env=os.environ,
                )
            webmaster = config.sources.get("yandex_webmaster")
            if webmaster is not None:
                extra_checks["yandex_webmaster"] = doctor_webmaster_source(
                    webmaster.fields,
                    bindings=[
                        {
                            "source": binding.source,
                            "remote_id": binding.remote_id,
                        }
                        for binding in config.source_bindings
                        if binding.source == "yandex_webmaster"
                    ],
                    env=os.environ,
                )
            wordstat = config.sources.get("wordstat")
            if wordstat is not None:
                extra_checks["wordstat"] = doctor_wordstat_source(
                    wordstat.fields,
                    env=os.environ,
                )
            serp = config.sources.get("serp")
            if serp is not None:
                extra_checks["serp"] = _doctor_serp_source(
                    source=serp,
                    providers=config.providers,
                    env=os.environ,
                )
            for source_name in ("competitor_discovery", "competitor_research"):
                source = config.sources.get(source_name)
                if source is not None:
                    extra_checks[source_name] = _doctor_provider_source(
                        source_name=source_name,
                        source=source,
                        providers=config.providers,
                        env=os.environ,
                    )
            for source_name, source in config.sources.items():
                if source_name.startswith("outcome_"):
                    extra_checks[source_name] = doctor_outcome_source(source.fields)
            for source_name, check in extra_checks.items():
                if isinstance(check, dict) and source_name in config.sources:
                    s = config.sources[source_name]
                    check.setdefault("source", source_name)
                    check.setdefault("enabled", bool(s.enabled))
                    check.setdefault("required", bool(s.required))
        except ConfigError as exc:
            project_check = {
                "ok": False,
                "path": str(project_config),
                "exists": project_config.exists(),
                "required": False,
                "status": "invalid",
                "error": _error_payload(exc)["error"],
            }
    payload = {
        "ok": True,
        "cli": "seo-observer",
        "version": __version__,
        "checks": {
            "cli": {
                "ok": True,
                "binary": "seo-observer",
                "package": "seo-observer",
            },
            "install": _install_check(),
            "local_home": {
                "ok": True,
                "path": str(local_home),
                "exists": local_home.exists(),
            },
            "project_config": project_check,
            **extra_checks,
        },
    }
    payload["ok"] = _doctor_exit_code(payload) == 0
    return payload



def _required_env_names(config: Any) -> list[str]:
    """Names of env variables referenced by the config of enabled sources."""

    names: list[str] = []
    for source in config.sources.values():
        if not getattr(source, "enabled", False):
            continue
        for field in ("credential_env", "credential_file_env", "token_file_env", "user_id_env"):
            value = source.fields.get(field)
            if isinstance(value, str) and value:
                names.append(value)
    for market in config.markets:
        for field in ("credential_env", "user_id_env"):
            value = getattr(market, field, None) or (
                market.fields.get(field) if hasattr(market, "fields") else None
            )
            if isinstance(value, str) and value:
                names.append(value)
    return sorted(set(names))


def _doctor_provider_source(
    *,
    source_name: str,
    source: Any,
    providers: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    provider_name = str(source.fields.get("provider") or "")
    provider = providers.get(provider_name)
    enabled = bool(source.enabled)
    required = bool(source.required)
    credential_env = provider.credential_env if provider is not None else None
    validation_code = None
    ok = True
    quality = "local-only"
    if enabled and (provider is None or not provider.enabled):
        ok = False
        quality = "unsupported"
        validation_code = "SOURCE_PROVIDER_NOT_CONFIGURED"
    elif enabled and credential_env and not env.get(credential_env):
        ok = False
        quality = "unsupported"
        validation_code = "PROVIDER_CREDENTIAL_MISSING"
    hint = None
    if validation_code == "PROVIDER_CREDENTIAL_MISSING":
        hint = (
            f"export {credential_env} in the environment or declare "
            "[credentials] env_file/loader in project.toml"
        )
    elif validation_code == "SOURCE_PROVIDER_NOT_CONFIGURED":
        hint = f"set enabled = true and endpoint in [providers.{provider_name}] in project.toml"
    return {
        "source": source_name,
        "provider": provider_name,
        "enabled": enabled,
        "required": required,
        "ok": ok,
        "live_checked": False,
        "quality": quality,
        "validation_code": validation_code,
        "credential_env": credential_env,
        "hint": hint,
        "errors": [],
    }


def _doctor_serp_source(
    *,
    source: Any,
    providers: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    provider_name = str(source.fields.get("provider") or "")
    check = doctor_serp_source(source.fields, env=env)
    credential_env = source.fields.get("credential_env")
    quality = "local-only"
    validation_code = None
    provider_account = SERP_PROVIDER_ACCOUNTS.get(provider_name)
    if provider_account:
        provider = providers.get(provider_account)
        credential_env = provider.credential_env if provider is not None else None
        if source.enabled and (provider is None or not provider.enabled):
            check["ok"] = False
            quality = "unsupported"
            validation_code = "SOURCE_PROVIDER_NOT_CONFIGURED"
        elif source.enabled and credential_env and not env.get(credential_env):
            check["ok"] = False
            quality = "unsupported"
            validation_code = "PROVIDER_CREDENTIAL_MISSING"
    hint = None
    if validation_code == "PROVIDER_CREDENTIAL_MISSING":
        hint = (
            f"export {credential_env} in the environment or declare "
            "[credentials] env_file/loader in project.toml"
        )
    elif validation_code == "SOURCE_PROVIDER_NOT_CONFIGURED":
        hint = f"set enabled = true and endpoint in [providers.{provider_account}] in project.toml"
    check.update(
        {
            "source": "serp",
            "enabled": bool(source.enabled),
            "required": bool(source.required),
            "provider": provider_name,
            "provider_account": provider_account,
            "quality": quality,
            "validation_code": validation_code,
            "credential_env": credential_env if isinstance(credential_env, str) else None,
            "hint": hint,
        }
    )
    return check


def _not_implemented_payload(command: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "NOT_IMPLEMENTED",
            "message": f"`{command}` is not implemented yet.",
            "details": {"command": command},
        },
    }


def _error_payload(exc: ConfigError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        },
    }


def _structured_error_payload(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    }


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit machine-readable JSON",
    )


def _add_selector_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="path to .seo-observer/project.toml",
    )
    parser.add_argument(
        "--project",
        default=argparse.SUPPRESS,
        help="registered project name from SEO_OBSERVER_HOME/projects.toml",
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_flags(common)
    selectors = argparse.ArgumentParser(add_help=False)
    _add_selector_flags(selectors)

    parser = argparse.ArgumentParser(
        prog="seo-observer",
        description="Evidence-first SEO observer CLI.",
        parents=[common, selectors],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in NOT_IMPLEMENTED_COMMANDS:
        subparser = subparsers.add_parser(command, parents=[common, selectors])
        subparser.set_defaults(handler=_handle_not_implemented)

    actions = subparsers.add_parser("actions", parents=[common, selectors])
    actions_subparsers = actions.add_subparsers(dest="actions_command", required=True)
    actions_add = actions_subparsers.add_parser("add", parents=[common, selectors])
    actions_add.add_argument("--id", dest="action_id", default=None)
    actions_add.add_argument(
        "--changed-at",
        default=None,
        help="date when action occurred, formatted as YYYY-MM-DD",
    )
    actions_add.add_argument("--type", dest="action_type", default=None)
    actions_add.add_argument("--url", action="append", default=[])
    actions_add.add_argument("--query", action="append", default=[])
    actions_add.add_argument("--description", default=None)
    actions_add.add_argument("--artifact-ref", default="")
    actions_add.add_argument("--file", default=None, help="path to a TOML action file to ingest")
    actions_add.set_defaults(handler=_handle_actions_add)
    actions_ingest = actions_subparsers.add_parser("ingest", parents=[common, selectors])
    actions_ingest.add_argument("--file", default=None, help="path to a specific action TOML file to ingest")
    actions_ingest.set_defaults(handler=_handle_actions_ingest)
    actions_list = actions_subparsers.add_parser("list", parents=[common, selectors])
    actions_list.set_defaults(handler=_handle_actions_list)
    actions_show = actions_subparsers.add_parser("show", parents=[common, selectors])
    actions_show.add_argument("action_id")
    actions_show.set_defaults(handler=_handle_actions_show)

    outcomes = subparsers.add_parser("outcomes", parents=[common, selectors])
    outcomes.add_argument("--action-id", required=True)
    outcomes.add_argument("--start", required=True)
    outcomes.add_argument("--end", required=True)
    outcomes.add_argument("--baseline-start", default=None)
    outcomes.add_argument("--baseline-end", default=None)
    outcomes.set_defaults(handler=_handle_outcomes)

    compare = subparsers.add_parser("compare", parents=[common, selectors])
    compare.add_argument("--start", required=True)
    compare.add_argument("--end", required=True)
    compare.add_argument("--baseline-start", default=None)
    compare.add_argument("--baseline-end", default=None)
    compare.set_defaults(handler=_handle_compare)

    collect = subparsers.add_parser("collect", parents=[common, selectors])
    collect.add_argument("--period-id", default="30d")
    collect.add_argument("--start", default=None)
    collect.add_argument("--end", default=None)
    collect.add_argument("--daily", action="store_true",
                         help="Collect one period per calendar day; re-collecting a day supersedes it.")
    collect.add_argument("--days", type=int, default=3,
                         help="With --daily and no --start/--end: re-collect the last N finished days.")
    collect.add_argument("--pause-seconds", type=float, default=0.0,
                         help="With --daily: pause between days (provider quotas during backfill).")
    collect.set_defaults(handler=_handle_collect)

    crawl = subparsers.add_parser("crawl", parents=[common, selectors])
    crawl.set_defaults(handler=_handle_crawl)

    opportunities = subparsers.add_parser("opportunities", parents=[common, selectors])
    opportunities.add_argument("--start", required=True)
    opportunities.add_argument("--end", required=True)
    opportunities.add_argument("--type-breakdown", action="store_true", default=False)
    opportunities.set_defaults(handler=_handle_opportunities)

    provider_audit = subparsers.add_parser("provider-audit", parents=[common, selectors])
    provider_audit.add_argument("--start", required=True)
    provider_audit.add_argument("--end", required=True)
    provider_audit.add_argument("--output-dir", default=None)
    provider_audit.set_defaults(handler=_handle_provider_audit)

    render_report = subparsers.add_parser("render-report", parents=[common])
    render_report.add_argument("--input", required=True, help="path to a Markdown report")
    render_report.add_argument("--output-dir", required=True)
    render_report.add_argument("--basename", default="seo-report")
    render_report.add_argument("--title", required=True)
    render_report.add_argument("--subtitle", default=None)
    render_report.add_argument("--no-pdf", action="store_true", default=False)
    render_report.set_defaults(handler=_handle_render_report)

    composite_report = subparsers.add_parser("composite-report", parents=[common])
    composite_report.add_argument("--provider-artifact", required=True)
    composite_report.add_argument("--research-artifact", required=True)
    composite_report.add_argument("--content-artifact", required=True)
    composite_report.add_argument("--serp-artifact", default=None)
    composite_report.add_argument("--metrics-artifact", default=None)
    composite_report.add_argument("--output-dir", required=True)
    composite_report.set_defaults(handler=_handle_composite_report)

    competitors = subparsers.add_parser("competitors", parents=[common, selectors])
    competitor_subparsers = competitors.add_subparsers(dest="competitor_command", required=True)
    competitors_discover = competitor_subparsers.add_parser("discover", parents=[common, selectors])
    competitors_discover.add_argument("--property", dest="property_id", default=None)
    competitors_discover.add_argument("--target-domain", default=None)
    competitors_discover.add_argument("--limit-competitors", type=int, default=20)
    competitors_discover.add_argument("--picked-competitors", type=int, default=3)
    competitors_discover.add_argument("--gap-limit", type=int, default=100)
    competitors_discover.add_argument(
        "--provider-mode",
        choices=("fixture", "artifact", "live"),
        default="artifact",
    )
    competitors_discover.add_argument("--allow-paid", action="store_true", default=False)
    competitors_discover.add_argument("--output-dir", default=None)
    competitors_discover.set_defaults(handler=_handle_competitors_discover)
    competitors_audit = competitor_subparsers.add_parser("audit", parents=[common, selectors])
    competitors_audit.add_argument("--keyword-set", required=True)
    competitors_audit.add_argument("--start", required=True)
    competitors_audit.add_argument("--end", required=True)
    competitors_audit.add_argument(
        "--provider-mode",
        choices=("fixture", "artifact", "live"),
        default="artifact",
    )
    competitors_audit.add_argument("--allow-paid", action="store_true", default=False)
    competitors_audit.add_argument("--baseline-artifact", default=None)
    competitors_audit.add_argument("--serp-depth", type=int, default=None)
    competitors_audit.add_argument("--device", action="append", choices=("desktop", "mobile"), default=[])
    competitors_audit.add_argument("--output-dir", required=True)
    competitors_audit.set_defaults(handler=_handle_competitors_audit)
    competitors_research = competitor_subparsers.add_parser("research", parents=[common, selectors])
    competitors_research.add_argument("--keyword-set", required=True)
    competitors_research.add_argument(
        "--provider-mode",
        choices=("fixture", "artifact", "live"),
        default="artifact",
    )
    competitors_research.add_argument("--allow-paid", action="store_true", default=False)
    competitors_research.add_argument("--confirmed-serp-artifact", default=None)
    competitors_research.add_argument("--urls", default="")
    competitors_research.add_argument("--max-pages", type=int, default=20)
    competitors_research.add_argument("--max-response-bytes", type=int, default=1_000_000)
    competitors_research.add_argument("--timeout-seconds", type=int, default=10)
    competitors_research.add_argument("--output-dir", required=True)
    competitors_research.set_defaults(handler=_handle_competitors_research)
    competitors_report = competitor_subparsers.add_parser("report", parents=[common, selectors])
    competitors_report.add_argument("--discovery-artifact", default=None)
    competitors_report.add_argument("--serp-artifact", default=None)
    competitors_report.add_argument("--content-artifact", default=None)
    competitors_report.add_argument(
        "--provider-mode",
        choices=("fixture", "artifact", "live"),
        default="artifact",
    )
    competitors_report.add_argument("--allow-paid", action="store_true", default=False)
    competitors_report.add_argument("--with-brief", action="store_true", default=False)
    competitors_report.add_argument("--llm-fixture", default=None)
    competitors_report.add_argument("--output-dir", required=True)
    competitors_report.set_defaults(handler=_handle_competitors_report)

    weekly = subparsers.add_parser("weekly", parents=[common, selectors])
    weekly.add_argument("--period-id", default="30d")
    weekly.add_argument("--baseline-date", default=None)
    weekly.add_argument("--output-dir", default=None)
    weekly.set_defaults(handler=_handle_weekly)

    snapshot = subparsers.add_parser("snapshot", parents=[common, selectors])
    snapshot.add_argument("--period-id", default="30d")
    snapshot.add_argument("--baseline-date", default=None)
    snapshot.add_argument("--output-dir", default=None)
    snapshot.set_defaults(handler=_handle_snapshot)

    report = subparsers.add_parser("report", parents=[common, selectors])
    report.add_argument("--period-id", default="30d")
    report.add_argument("--baseline-date", default=None)
    report.add_argument("--output-dir", default=None)
    report.set_defaults(handler=_handle_report)

    ai_readiness = subparsers.add_parser("ai-readiness", parents=[common, selectors])
    ai_readiness.add_argument("--output-dir", default=None)
    ai_readiness.set_defaults(handler=_handle_ai_readiness)

    prompts = subparsers.add_parser("prompts", parents=[common, selectors])
    prompts.add_argument("--keyword-set", default=None, help="keyword set ID to generate prompts from")
    prompts.add_argument("--keywords", default=None, help="comma-separated list of keywords")
    prompts.add_argument("--keyword-file", default=None, help="path to custom keyword text file")
    prompts.add_argument("--brand", dest="brand", action="append", default=[], help="explicit brand name")
    prompts.add_argument("--locale", default=None, help="locale for prompt templates (e.g. ru-RU, en-US)")
    prompts.add_argument("--region", default=None, help="region name or code")
    prompts.add_argument("--from-gsc", action="store_true", default=False, help="include GSC search queries")
    prompts.add_argument("--from-wordstat", action="store_true", default=False, help="include Wordstat queries")
    prompts.add_argument("--llm-paraphrase", action="store_true", default=False, help="enable LLM paraphrase of prompts")
    prompts.add_argument("--llm-model", default="gpt-4o", help="model name for LLM paraphrase")
    prompts.add_argument("--llm-fixture", default=None, help="path to JSON fixture for LLM paraphrase")
    prompts.add_argument("--limit", type=int, default=None, help="limit total prompts generated")
    prompts.add_argument("--provider-brand-id", default=None, help="configured provider brand ID (e.g. for Elmo)")
    prompts.add_argument("--output-dir", default=None, help="output directory for prompt artifacts")
    prompts.set_defaults(handler=_handle_ai_prompts)

    ai_prompts = subparsers.add_parser("ai-prompts", parents=[common, selectors])
    ai_prompts.add_argument("--keyword-set", default=None, help="keyword set ID to generate prompts from")
    ai_prompts.add_argument("--keywords", default=None, help="comma-separated list of keywords")
    ai_prompts.add_argument("--keyword-file", default=None, help="path to custom keyword text file")
    ai_prompts.add_argument("--brand", dest="brand", action="append", default=[], help="explicit brand name")
    ai_prompts.add_argument("--locale", default=None, help="locale for prompt templates (e.g. ru-RU, en-US)")
    ai_prompts.add_argument("--region", default=None, help="region name or code")
    ai_prompts.add_argument("--from-gsc", action="store_true", default=False, help="include GSC search queries")
    ai_prompts.add_argument("--from-wordstat", action="store_true", default=False, help="include Wordstat queries")
    ai_prompts.add_argument("--llm-paraphrase", action="store_true", default=False, help="enable LLM paraphrase of prompts")
    ai_prompts.add_argument("--llm-model", default="gpt-4o", help="model name for LLM paraphrase")
    ai_prompts.add_argument("--llm-fixture", default=None, help="path to JSON fixture for LLM paraphrase")
    ai_prompts.add_argument("--limit", type=int, default=None, help="limit total prompts generated")
    ai_prompts.add_argument("--provider-brand-id", default=None, help="configured provider brand ID (e.g. for Elmo)")
    ai_prompts.add_argument("--output-dir", default=None, help="output directory for prompt artifacts")
    ai_prompts.set_defaults(handler=_handle_ai_prompts)

    doctor = subparsers.add_parser("doctor", parents=[common, selectors])
    doctor.set_defaults(handler=_handle_doctor)

    projects = subparsers.add_parser("projects", parents=[common])
    project_subparsers = projects.add_subparsers(dest="project_command", required=True)
    projects_list = project_subparsers.add_parser("list", parents=[common])
    projects_list.set_defaults(handler=_handle_projects_list)
    projects_register = project_subparsers.add_parser("register", parents=[common, selectors])
    projects_register.add_argument("name")
    projects_register.set_defaults(handler=_handle_projects_register)
    projects_remove = project_subparsers.add_parser("remove", parents=[common])
    projects_remove.add_argument("name")
    projects_remove.set_defaults(handler=_handle_projects_remove)
    return parser


def _json_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _handle_doctor(args: argparse.Namespace) -> int:
    try:
        payload = doctor_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    if _json_requested(args):
        _json_dump(payload)
    else:
        print("seo-observer doctor")
        for name, check in payload["checks"].items():
            status = "ok" if check["ok"] else "fail"
            print(f"- {name}: {status}")
    return _doctor_exit_code(payload)


def _doctor_exit_code(payload: dict[str, Any]) -> int:
    project_config = payload.get("checks", {}).get("project_config", {})
    if isinstance(project_config, dict) and project_config.get("status") == "invalid":
        return 2
    for check in payload.get("checks", {}).values():
        if (
            isinstance(check, dict)
            and check.get("source")
            and check.get("enabled") is True
            and check.get("required") is True
            and check.get("ok") is False
        ):
            return 3
    return 0


def _handle_not_implemented(args: argparse.Namespace) -> int:
    command = str(args.command)
    try:
        _preflight_selector(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    payload = _not_implemented_payload(command)
    if _json_requested(args):
        _json_dump(payload)
    else:
        print(payload["error"]["message"], file=sys.stderr)
    return 2


def _handle_collect(args: argparse.Namespace) -> int:
    try:
        payload = _collect_daily_payload(args) if getattr(args, "daily", False) else _collect_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    return _emit_payload(args, payload)


def _handle_crawl(args: argparse.Namespace) -> int:
    try:
        payload = _crawl_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (StorageError, sqlite3.Error, OSError, ValueError, TypeError) as exc:
        payload = _structured_error_payload(
            "CRAWL_FAILED",
            "Local crawl failed before page facts could be stored.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_compare(args: argparse.Namespace) -> int:
    try:
        payload = _compare_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (StorageError, sqlite3.Error, ValueError) as exc:
        payload = _structured_error_payload(
            "COMPARE_FAILED",
            "Period comparison failed before a comparable result could be built.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_actions_add(args: argparse.Namespace) -> int:
    try:
        payload = _actions_add_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (StorageError, sqlite3.Error, ValueError, ActionError) as exc:
        payload = _structured_error_payload(
            "ACTIONS_FAILED",
            "Action journal update failed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_actions_ingest(args: argparse.Namespace) -> int:
    try:
        payload = _actions_ingest_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (StorageError, sqlite3.Error, ValueError, ActionError) as exc:
        payload = _structured_error_payload(
            "ACTIONS_FAILED",
            "Action journal ingest failed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_actions_list(args: argparse.Namespace) -> int:
    try:
        payload = _actions_list_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (StorageError, sqlite3.Error, ValueError) as exc:
        payload = _structured_error_payload(
            "ACTIONS_FAILED",
            "Action journal read failed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_actions_show(args: argparse.Namespace) -> int:
    try:
        payload = _actions_show_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (StorageError, sqlite3.Error, ValueError) as exc:
        payload = _structured_error_payload(
            "ACTIONS_FAILED",
            "Action journal read failed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_outcomes(args: argparse.Namespace) -> int:
    try:
        payload = _outcomes_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (StorageError, sqlite3.Error, ValueError) as exc:
        payload = _structured_error_payload(
            "OUTCOMES_FAILED",
            "Action outcome measurement failed before a comparable result could be built.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_provider_audit(args: argparse.Namespace) -> int:
    try:
        payload = _provider_audit_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    return _emit_payload(args, payload)


def _handle_render_report(args: argparse.Namespace) -> int:
    try:
        payload = _render_report_payload(args)
    except OSError as exc:
        payload = _structured_error_payload(
            "REPORT_RENDER_IO_ERROR",
            "Report rendering failed because an input or output file could not be accessed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_composite_report(args: argparse.Namespace) -> int:
    try:
        payload = _composite_report_payload(args)
    except CompositeArtifactError as exc:
        payload = _structured_error_payload(
            "COMPOSITE_REPORT_INVALID_ARTIFACT",
            "Composite report input artifact is missing, unreadable, or has an unsupported schema.",
            exc.payload(),
        )
        if _json_requested(args):
            _json_dump(payload)
        else:
            print(payload["error"]["message"], file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError) as exc:
        payload = _structured_error_payload(
            "COMPOSITE_REPORT_FAILED",
            "Composite report failed before artifacts could be completed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_competitors_discover(args: argparse.Namespace) -> int:
    try:
        payload = _competitors_discover_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (OSError, ValueError, TypeError) as exc:
        payload = _structured_error_payload(
            "COMPETITOR_DISCOVERY_FAILED",
            "Competitor discovery failed before artifacts could be completed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_competitors_audit(args: argparse.Namespace) -> int:
    try:
        payload = _competitors_audit_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (OSError, ValueError, TypeError) as exc:
        payload = _structured_error_payload(
            "COMPETITOR_AUDIT_FAILED",
            "Competitor SERP audit failed before artifacts could be completed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_competitors_research(args: argparse.Namespace) -> int:
    try:
        payload = _competitors_research_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (OSError, ValueError, TypeError) as exc:
        payload = _structured_error_payload(
            "COMPETITOR_RESEARCH_FAILED",
            "Competitor research failed before artifacts could be completed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_competitors_report(args: argparse.Namespace) -> int:
    try:
        payload = _competitors_report_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (OSError, ValueError, TypeError) as exc:
        payload = _structured_error_payload(
            "COMPETITOR_REPORT_FAILED",
            "Competitor content-gap report failed before artifacts could be completed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def _handle_snapshot(args: argparse.Namespace) -> int:
    try:
        payload = _snapshot_or_report_payload(args, render_report=False)
    except ConfigError as exc:
        return _emit_error(args, exc)
    return _emit_payload(args, payload)


def _handle_report(args: argparse.Namespace) -> int:
    try:
        payload = _snapshot_or_report_payload(args, render_report=True)
    except ConfigError as exc:
        return _emit_error(args, exc)
    return _emit_payload(args, payload)


def _handle_weekly(args: argparse.Namespace) -> int:
    try:
        payload = _weekly_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    return _emit_payload(args, payload)


def _handle_ai_readiness(args: argparse.Namespace) -> int:
    try:
        payload = build_ai_readiness_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (OSError, ValueError, TypeError) as exc:
        payload = _structured_error_payload(
            "AI_READINESS_FAILED",
            "AI readiness audit failed before artifacts could be completed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def build_ai_readiness_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    output_dir_arg = getattr(args, "output_dir", None)
    output_dir = (
        Path(str(output_dir_arg)).expanduser()
        if output_dir_arg
        else observer_home() / "projects" / config.project.namespace / "ai-readiness"
    )
    return ai_readiness_mod.build_ai_readiness_payload(config=config, output_dir=output_dir)


def _handle_ai_prompts(args: argparse.Namespace) -> int:
    try:
        payload = build_ai_prompts_cli_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (OSError, ValueError, TypeError) as exc:
        payload = _structured_error_payload(
            "AI_PROMPTS_FAILED",
            "AI prompt generation failed.",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)


def build_ai_prompts_cli_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)

    keywords_raw = getattr(args, "keywords", None)
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()] if keywords_raw else None

    keyword_file_arg = getattr(args, "keyword_file", None)
    keyword_file = Path(str(keyword_file_arg)).expanduser() if keyword_file_arg else None

    llm_fixture_arg = getattr(args, "llm_fixture", None)
    llm_fixture = Path(str(llm_fixture_arg)).expanduser() if llm_fixture_arg else None

    output_dir_arg = getattr(args, "output_dir", None)
    output_dir = (
        Path(str(output_dir_arg)).expanduser()
        if output_dir_arg
        else observer_home() / "projects" / config.project.namespace / "ai-prompts"
    )

    return ai_prompts_mod.build_ai_prompts_payload(
        config=config,
        keyword_set_id=getattr(args, "keyword_set", None),
        keywords=keywords,
        keyword_file=keyword_file,
        brand_names=getattr(args, "brand", []),
        provider_brand_id=getattr(args, "provider_brand_id", None),
        locale=getattr(args, "locale", None),
        region=getattr(args, "region", None),
        from_gsc=bool(getattr(args, "from_gsc", False)),
        from_wordstat=bool(getattr(args, "from_wordstat", False)),
        llm_paraphrase=bool(getattr(args, "llm_paraphrase", False)),
        llm_model=getattr(args, "llm_model", "gpt-4o"),
        llm_fixture=llm_fixture,
        limit=getattr(args, "limit", None),
        output_dir=output_dir,
    )


def _handle_opportunities(args: argparse.Namespace) -> int:
    try:
        payload = _opportunities_payload(args)
    except ConfigError as exc:
        return _emit_error(args, exc)
    return _emit_payload(args, payload)


def _handle_projects_list(args: argparse.Namespace) -> int:
    try:
        projects = ProjectRegistry().list()
    except ConfigError as exc:
        return _emit_error(args, exc)
    payload = {"ok": True, "projects": projects}
    if _json_requested(args):
        _json_dump(payload)
    else:
        for name in sorted(projects):
            print(f"{name}\t{projects[name]['project']}\t{projects[name]['path']}")
    return 0


def _handle_projects_register(args: argparse.Namespace) -> int:
    try:
        config_path = _selected_config_path(args, allow_discovery=False)
        if config_path is None:
            raise ConfigError(
                "CONFIG_NOT_FOUND",
                "Project config was not found.",
                {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
            )
        entry = ProjectRegistry().register(str(args.name), config_path)
    except ConfigError as exc:
        return _emit_error(args, exc)
    payload = {"ok": True, "registered": str(args.name), "entry": entry}
    if _json_requested(args):
        _json_dump(payload)
    else:
        print(f"registered {args.name}")
    return 0


def _handle_projects_remove(args: argparse.Namespace) -> int:
    try:
        ProjectRegistry().remove(str(args.name))
    except ConfigError as exc:
        return _emit_error(args, exc)
    payload = {"ok": True, "removed": str(args.name)}
    if _json_requested(args):
        _json_dump(payload)
    else:
        print(f"removed {args.name}")
    return 0


def _emit_payload(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    if _json_requested(args):
        _json_dump(payload)
    elif payload["ok"]:
        artifacts = payload.get("artifacts", {})
        if isinstance(artifacts, dict):
            print(artifacts.get("report_path") or artifacts.get("snapshot_path") or "ok")
        else:
            print(payload.get("report_path") or "ok")
    else:
        error = payload.get("error")
        if isinstance(error, dict):
            print(error.get("message") or error.get("code") or "failed", file=sys.stderr)
        else:
            errors = payload.get("errors") or []
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            print(first.get("safe_message") or first.get("code") or "failed", file=sys.stderr)
    return 0 if payload["ok"] else 2


def _selected_config_path(
    args: argparse.Namespace,
    *,
    allow_discovery: bool = True,
) -> Path | None:
    config = getattr(args, "config", None)
    project = getattr(args, "project", None)
    if config and project:
        raise ConfigError(
            "SELECTOR_CONFLICT",
            "`--config` and `--project` cannot be used together.",
            {"config": str(config), "project": str(project)},
        )
    if config:
        path = Path(str(config)).expanduser()
        if not path.exists():
            raise ConfigError(
                "CONFIG_NOT_FOUND",
                "Project config was not found.",
                {"path": str(path)},
            )
        return path
    if project:
        return ProjectRegistry().resolve(str(project))
    if allow_discovery:
        return discover_project_config()
    return None


def _preflight_selector(args: argparse.Namespace) -> None:
    path = _selected_config_path(args)
    if path is not None:
        load_project_config(path)


def _emit_error(args: argparse.Namespace, exc: ConfigError) -> int:
    payload = _error_payload(exc)
    if _json_requested(args):
        _json_dump(payload)
    else:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
    return 2


def _snapshot_or_report_payload(
    args: argparse.Namespace,
    *,
    render_report: bool,
) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    period_id = str(getattr(args, "period_id", "30d") or "30d")
    baseline = _select_baseline(config.path.parent, getattr(args, "baseline_date", None))
    if baseline is None:
        return _structured_error_payload(
            "BASELINE_NOT_FOUND",
            "Export-approved baseline was not found.",
            {
                "baselines_dir": str(config.path.parent / "baselines"),
                "baseline_date": getattr(args, "baseline_date", None),
            },
        )
    observations_path = baseline / "import-ready-observations.jsonl"
    manifest_path = baseline / "manifest.json"
    if not observations_path.is_file() or not manifest_path.is_file():
        return _structured_error_payload(
            "BASELINE_NOT_FOUND",
            "Baseline is missing manifest.json or import-ready-observations.jsonl.",
            {
                "baseline_path": str(baseline),
                "manifest_path": str(manifest_path),
                "observations_path": str(observations_path),
            },
        )
    manifest = _read_baseline_manifest(manifest_path)
    if manifest.get("privacy", {}).get("export_approved") is not True:
        return _structured_error_payload(
            "BASELINE_NOT_EXPORT_APPROVED",
            "Baseline manifest is not export-approved.",
            {"manifest_path": str(manifest_path)},
        )
    rows = _read_jsonl(observations_path)
    snapshot, source_status = _build_baseline_snapshot(
        config=config,
        baseline=baseline,
        baseline_manifest=manifest,
        observations_path=observations_path,
        rows=rows,
        period_id=period_id,
    )
    if snapshot is None:
        return _structured_error_payload(
            "NO_EXPORTABLE_OBSERVATIONS",
            "Baseline has no exportable search_performance observations for the requested period.",
            {"baseline_path": str(baseline), "period_id": period_id},
        )
    output_root = _output_root(args, config.project.namespace, baseline.name)
    database_path = _write_baseline_storage(
        config=config,
        snapshot=snapshot,
        observations_path=observations_path,
        database_path=output_root / "observer.db",
    )
    snapshot_path = output_root / "snapshots" / "v1" / config.project.namespace / period_id / (
        f"snapshot-{snapshot['manifest']['snapshot_hash'][:16]}.json"
    )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_text = _canonical_json(snapshot, indent=2) + "\n"
    snapshot_path.write_text(snapshot_text, encoding="utf-8")
    artifacts: dict[str, Any] = {
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256": _sha256_text(snapshot_text),
        "database_path": str(database_path),
    }
    if render_report:
        report_text = _render_baseline_report(snapshot)
        report_path = output_root / "report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
        artifacts.update(
            {
                "report_path": str(report_path),
                "report_sha256": _sha256_text(report_text),
            }
        )
    return {
        "ok": True,
        "command": "report" if render_report else "snapshot",
        "project": config.project.namespace,
        "period_id": period_id,
        "baseline": {
            "path": str(baseline),
            "manifest_path": str(manifest_path),
            "observations_path": str(observations_path),
            "source_state": "local-only",
        },
        "artifacts": artifacts,
        "source_status": source_status,
    }


def _project_mismatch_error(path: Path, action_project: str, selected: str) -> dict[str, Any] | None:
    """An action declaring a foreign project_id would be written to the wrong journal.

    The file's declaration and `--project` diverge on a stale template, copy-paste, or a typo.
    A foreign key catches this only if the foreign project is not registered in this
    database, and reports `FOREIGN KEY constraint failed` — which gives no clue about the
    cause. When the foreign project is known to the database, the action goes into it
    silently: `ingested_count: 1`, yet it is absent from
    `actions list --project <selected>`.
    """
    if action_project == selected:
        return None
    return _structured_error_payload(
        "ACTION_PROJECT_MISMATCH",
        f"Action file declares project '{action_project}', but --project is '{selected}'.",
        {"path": str(path), "action_project": action_project, "selected_project": selected},
    )


def _actions_add_payload(args: argparse.Namespace) -> dict[str, Any]:
    config, storage = _selected_config_and_storage(args)
    storage.bootstrap()
    storage.upsert_project_config(config, config_hash=compute_config_hash(config))

    file_arg = getattr(args, "file", None)
    if file_arg:
        path = Path(file_arg)
        if not path.exists():
            return _structured_error_payload(
                "ACTION_FILE_NOT_FOUND",
                f"Action file not found: {path}",
                {"path": str(path)},
            )
        try:
            action_obj = load_action(path)
        except ActionError as exc:
            return _structured_error_payload(
                "ACTION_PARSE_ERROR",
                f"Failed to parse action file {path}: {exc}",
                {"path": str(path), "error": str(exc)},
            )
        mismatch = _project_mismatch_error(path, action_obj.project_id, config.project.namespace)
        if mismatch is not None:
            return mismatch
        persist_action(storage, action_obj)
        return {
            "ok": True,
            "command": "actions",
            "action": {
                "id": action_obj.action_id,
                "project": action_obj.project_id,
                "changed_at": action_obj.changed_at,
                "type": action_obj.action_type,
                "description": action_obj.description,
                "artifact_ref": action_obj.evidence_ref,
                "revision_hash": action_obj.revision_hash,
                "targets": {
                    "urls": [t.target_value for t in action_obj.targets if t.target_type == "url"],
                    "queries": [t.target_value for t in action_obj.targets if t.target_type == "query"],
                },
            },
            "database_path": str(storage.db_path),
        }

    raw_changed_at = getattr(args, "changed_at", None)
    raw_type = getattr(args, "action_type", None)
    raw_description = getattr(args, "description", None)
    if not raw_changed_at or not raw_type or not raw_description:
        return _structured_error_payload(
            "ACTION_FIELDS_REQUIRED",
            "--changed-at, --type, and --description are required when not using --file.",
            {
                "changed_at": bool(raw_changed_at),
                "type": bool(raw_type),
                "description": bool(raw_description),
            },
        )

    str_changed_at = str(raw_changed_at).strip()
    try:
        s = str_changed_at
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        changed_at = (dt.date.fromisoformat(s) if len(s) == 10 else dt.datetime.fromisoformat(s).date()).isoformat()
    except ValueError as exc:
        return _structured_error_payload(
            "INVALID_CHANGED_AT_FORMAT",
            f"Invalid --changed-at date format '{str_changed_at}'. Expected YYYY-MM-DD (e.g. 2026-08-17).",
            {"changed_at": str_changed_at, "error": str(exc)},
        )

    urls = _normalized_repeated_values(getattr(args, "url", []))
    queries = _normalized_repeated_values(getattr(args, "query", []))
    if not urls and not queries:
        return _structured_error_payload(
            "ACTION_TARGETS_REQUIRED",
            "Action must target at least one URL or query.",
            {"url_count": 0, "query_count": 0},
        )
    action_id = str(getattr(args, "action_id", "") or f"action:{uuid.uuid4().hex[:12]}")
    action = {
        "id": action_id,
        "project": config.project.namespace,
        "changed_at": changed_at,
        "type": str(raw_type),
        "description": str(raw_description),
        "artifact_ref": str(getattr(args, "artifact_ref", "") or ""),
        "targets": {"urls": urls, "queries": queries},
    }
    revision_hash = _action_revision_hash(action)
    with storage.connect() as con:
        con.execute(
            """
            INSERT INTO seo_actions(
              action_id, project_id, changed_at, action_type, description, hypothesis_id,
              evidence_ref, lifecycle_state, action_revision_hash, supersedes_action_revision_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'done', ?, NULL)
            ON CONFLICT(action_id, action_revision_hash) DO UPDATE SET
              changed_at = excluded.changed_at,
              action_type = excluded.action_type,
              description = excluded.description,
              evidence_ref = excluded.evidence_ref,
              lifecycle_state = excluded.lifecycle_state
            """,
            (
                action_id,
                config.project.namespace,
                changed_at,
                str(raw_type),
                str(raw_description),
                "local-change-log",
                action["artifact_ref"],
                revision_hash,
            ),
        )
        con.execute(
            "DELETE FROM action_targets WHERE action_id = ? AND action_revision_hash = ?",
            (action_id, revision_hash),
        )
        for url in urls:
            con.execute(
                """
                INSERT INTO action_targets(action_id, action_revision_hash, target_type, target_value, target_role)
                VALUES (?, ?, 'url', ?, 'primary')
                """,
                (action_id, revision_hash, url),
            )
        for query in queries:
            con.execute(
                """
                INSERT INTO action_targets(action_id, action_revision_hash, target_type, target_value, target_role)
                VALUES (?, ?, 'query', ?, 'primary')
                """,
                (action_id, revision_hash, query),
            )
    action["revision_hash"] = revision_hash
    return {"ok": True, "command": "actions", "action": action, "database_path": str(storage.db_path)}


def _actions_ingest_payload(args: argparse.Namespace) -> dict[str, Any]:
    config, storage = _selected_config_and_storage(args)
    storage.bootstrap()
    storage.upsert_project_config(config, config_hash=compute_config_hash(config))

    file_arg = getattr(args, "file", None)
    files_to_ingest: list[Path] = []
    search_dirs: list[Path] = []
    if file_arg:
        path = Path(file_arg)
        if not path.exists():
            return _structured_error_payload(
                "ACTION_FILE_NOT_FOUND",
                f"Action file not found: {path}",
                {"path": str(path)},
            )
        files_to_ingest.append(path)
    else:
        search_dirs = [Path.cwd() / ".seo-observer" / "actions"]
        if config.path and config.path.parent:
            search_dirs.append(config.path.parent / "actions")
        seen: set[Path] = set()
        for sdir in search_dirs:
            if sdir.exists():
                for p in sorted(sdir.glob("*.toml")):
                    if p.resolve() not in seen:
                        seen.add(p.resolve())
                        files_to_ingest.append(p)

    if not files_to_ingest:
        return _structured_error_payload(
            "NO_ACTION_FILES_FOUND",
            "No action TOML files found to ingest.",
            {"searched": [str(p) for p in search_dirs]} if not file_arg else {"file": file_arg},
        )

    action_objs: list[tuple[Path, Any]] = []
    for path in files_to_ingest:
        action_obj = load_action(path)
        mismatch = _project_mismatch_error(path, action_obj.project_id, config.project.namespace)
        if mismatch is not None:
            return mismatch
        action_objs.append((path, action_obj))

    persist_actions(storage, [action_obj for _, action_obj in action_objs])

    ingested: list[dict[str, Any]] = []
    for path, action_obj in action_objs:
        ingested.append(
            {
                "action_id": action_obj.action_id,
                "project": action_obj.project_id,
                "revision_hash": action_obj.revision_hash,
                "path": str(path),
            }
        )
    return {
        "ok": True,
        "command": "actions",
        "subcommand": "ingest",
        "ingested_count": len(ingested),
        "actions": ingested,
        "database_path": str(storage.db_path),
    }


def _actions_list_payload(args: argparse.Namespace) -> dict[str, Any]:
    config, storage = _selected_config_and_storage(args)
    storage.bootstrap()
    return {
        "ok": True,
        "command": "actions",
        "project": config.project.namespace,
        "actions": _load_actions(storage, project_id=config.project.namespace),
        "database_path": str(storage.db_path),
    }


def _actions_show_payload(args: argparse.Namespace) -> dict[str, Any]:
    config, storage = _selected_config_and_storage(args)
    storage.bootstrap()
    action = _load_action(storage, project_id=config.project.namespace, action_id=str(args.action_id))
    if action is None:
        return _structured_error_payload(
            "ACTION_NOT_FOUND",
            "Action was not found in the local journal.",
            {"project": config.project.namespace, "action_id": str(args.action_id)},
        )
    return {
        "ok": True,
        "command": "actions",
        "project": config.project.namespace,
        "action": action,
        "database_path": str(storage.db_path),
    }


def _outcomes_payload(args: argparse.Namespace) -> dict[str, Any]:
    config, storage = _selected_config_and_storage(args)
    storage.bootstrap()
    action = _load_action(storage, project_id=config.project.namespace, action_id=str(args.action_id))
    if action is None:
        return _structured_error_payload(
            "ACTION_NOT_FOUND",
            "Action was not found in the local journal.",
            {"project": config.project.namespace, "action_id": str(args.action_id)},
        )
    after_start, after_end = _parse_date_window(str(args.start), str(args.end))
    baseline_start_arg = getattr(args, "baseline_start", None)
    baseline_end_arg = getattr(args, "baseline_end", None)
    if bool(baseline_start_arg) != bool(baseline_end_arg):
        return _structured_error_payload(
            "OUTCOMES_BASELINE_WINDOW_INCOMPLETE",
            "`--baseline-start` and `--baseline-end` must be supplied together.",
            {"baseline_start": baseline_start_arg, "baseline_end": baseline_end_arg},
        )
    if baseline_start_arg and baseline_end_arg:
        before_start, before_end = _parse_date_window(str(baseline_start_arg), str(baseline_end_arg))
    else:
        days = _window_days(after_start, after_end)
        before_end = after_start - dt.timedelta(days=1)
        before_start = before_end - dt.timedelta(days=days - 1)

    targets = action["targets"]
    affected = _comparison_for_targets(
        storage,
        project_id=config.project.namespace,
        timezone=config.project.timezone,
        current_start=after_start,
        current_end=after_end,
        baseline_start=before_start,
        baseline_end=before_end,
        include_targets=targets,
    )
    control = _comparison_for_targets(
        storage,
        project_id=config.project.namespace,
        timezone=config.project.timezone,
        current_start=after_start,
        current_end=after_end,
        baseline_start=before_start,
        baseline_end=before_end,
        exclude_targets=targets,
    )
    status = _outcome_status(affected)
    if _window_days(after_start, after_end) < MIN_OUTCOME_MATURATION_DAYS:
        status = "too_early"
        _suppress_effect_numbers(affected)
    return {
        "ok": True,
        "command": "outcomes",
        "project": config.project.namespace,
        "action": action,
        "status": status,
        "windows": {
            "after": _window_payload(after_start, after_end),
            "before": _window_payload(before_start, before_end),
        },
        "maturation": {
            "minimum_after_days": MIN_OUTCOME_MATURATION_DAYS,
            "after_days": _window_days(after_start, after_end),
        },
        "affected": affected,
        "control": control,
        "causality_note": "observed_change_not_causality",
    }


def _compare_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    current_start, current_end = _parse_date_window(str(args.start), str(args.end))
    baseline_start_arg = getattr(args, "baseline_start", None)
    baseline_end_arg = getattr(args, "baseline_end", None)
    if bool(baseline_start_arg) != bool(baseline_end_arg):
        return _structured_error_payload(
            "COMPARE_BASELINE_WINDOW_INCOMPLETE",
            "`--baseline-start` and `--baseline-end` must be supplied together.",
            {"baseline_start": baseline_start_arg, "baseline_end": baseline_end_arg},
        )
    if baseline_start_arg and baseline_end_arg:
        baseline_start, baseline_end = _parse_date_window(str(baseline_start_arg), str(baseline_end_arg))
    else:
        days = _window_days(current_start, current_end)
        baseline_end = current_start - dt.timedelta(days=1)
        baseline_start = baseline_end - dt.timedelta(days=days - 1)

    storage = SEOStorage(default_database_path(config.project.namespace), observer_home=observer_home())
    current_window = _load_compare_window(
        storage,
        project_id=config.project.namespace,
        timezone=config.project.timezone,
        start=current_start,
        end=current_end,
    )
    baseline_window = _load_compare_window(
        storage,
        project_id=config.project.namespace,
        timezone=config.project.timezone,
        start=baseline_start,
        end=baseline_end,
    )
    comparability = _compare_comparability(current_window, baseline_window)
    slices = {
        "total": _compare_slice(
            current_window,
            baseline_window,
            slice_name="total",
            metric_path_prefix="formula_inputs.search_performance_totals",
            comparable=comparability["state"] == "comparable",
        ),
        "queries": _compare_grouped_slices(
            current_window,
            baseline_window,
            group_name="queries",
            comparable=comparability["state"] == "comparable",
        ),
        "pages": _compare_grouped_slices(
            current_window,
            baseline_window,
            group_name="pages",
            comparable=comparability["state"] == "comparable",
        ),
    }
    if current_window["formula_inputs"]["traffic_totals"] or baseline_window["formula_inputs"]["traffic_totals"]:
        slices["traffic"] = _compare_slice(
            current_window,
            baseline_window,
            slice_name="traffic",
            metric_path_prefix="formula_inputs.traffic_totals",
            comparable=comparability["state"] == "comparable",
            metric_names=("visits", "users", "pageviews"),
        )
    return {
        "ok": True,
        "command": "compare",
        "project": config.project.namespace,
        "windows": {
            "current": _window_payload(current_start, current_end),
            "baseline": _window_payload(baseline_start, baseline_end),
        },
        "comparability": comparability,
        "evidence_quality": _compare_evidence_quality(current_window, baseline_window, comparability),
        "slices": slices,
    }


def _selected_config_and_storage(args: argparse.Namespace) -> tuple[Any, SEOStorage]:
    config_path = _selected_config_path(args)
    if config_path is None:
        raise ConfigError(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    storage = SEOStorage(default_database_path(config.project.namespace), observer_home=observer_home())
    return config, storage


def _normalized_repeated_values(values: list[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _action_revision_hash(action: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(action).encode("utf-8")).hexdigest()


def _load_actions(storage: SEOStorage, *, project_id: str) -> list[dict[str, Any]]:
    rows = storage.fetchall(
        """
        SELECT action_id
        FROM seo_actions
        WHERE project_id = ?
        GROUP BY action_id
        ORDER BY MAX(changed_at) DESC, action_id ASC
        """,
        (project_id,),
    )
    actions = []
    for row in rows:
        action = _load_action(storage, project_id=project_id, action_id=str(row["action_id"]))
        if action is not None:
            actions.append(action)
    return actions


def _load_action(storage: SEOStorage, *, project_id: str, action_id: str) -> dict[str, Any] | None:
    row = storage.fetchone(
        """
        SELECT action_id, project_id, changed_at, action_type, description,
               evidence_ref, lifecycle_state, action_revision_hash
        FROM seo_actions
        WHERE project_id = ? AND action_id = ?
        ORDER BY changed_at DESC, rowid DESC
        LIMIT 1
        """,
        (project_id, action_id),
    )
    if row is None:
        return None
    targets = storage.fetchall(
        """
        SELECT target_type, target_value, target_role
        FROM action_targets
        WHERE action_id = ? AND action_revision_hash = ?
        ORDER BY target_type, target_value
        """,
        (action_id, row["action_revision_hash"]),
    )
    urls = [
        str(target["target_value"])
        for target in targets
        if target["target_type"] == "url" and target["target_role"] == "primary"
    ]
    queries = [
        str(target["target_value"])
        for target in targets
        if target["target_type"] == "query" and target["target_role"] == "primary"
    ]
    return {
        "id": str(row["action_id"]),
        "project": str(row["project_id"]),
        "changed_at": str(row["changed_at"]),
        "type": str(row["action_type"]),
        "description": str(row["description"]),
        "artifact_ref": str(row["evidence_ref"]),
        "lifecycle_state": str(row["lifecycle_state"]),
        "revision_hash": str(row["action_revision_hash"]),
        "targets": {"urls": urls, "queries": queries},
    }


def _comparison_for_targets(
    storage: SEOStorage,
    *,
    project_id: str,
    timezone: str,
    current_start: dt.date,
    current_end: dt.date,
    baseline_start: dt.date,
    baseline_end: dt.date,
    include_targets: dict[str, list[str]] | None = None,
    exclude_targets: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    current_window = _load_compare_window(
        storage,
        project_id=project_id,
        timezone=timezone,
        start=current_start,
        end=current_end,
        include_targets=include_targets,
        exclude_targets=exclude_targets,
    )
    baseline_window = _load_compare_window(
        storage,
        project_id=project_id,
        timezone=timezone,
        start=baseline_start,
        end=baseline_end,
        include_targets=include_targets,
        exclude_targets=exclude_targets,
    )
    comparability = _compare_comparability(current_window, baseline_window)
    slices = {
        "total": _compare_slice(
            current_window,
            baseline_window,
            slice_name="total",
            metric_path_prefix="formula_inputs.search_performance_totals",
            comparable=comparability["state"] == "comparable",
        ),
        "queries": _compare_grouped_slices(
            current_window,
            baseline_window,
            group_name="queries",
            comparable=comparability["state"] == "comparable",
        ),
        "pages": _compare_grouped_slices(
            current_window,
            baseline_window,
            group_name="pages",
            comparable=comparability["state"] == "comparable",
        ),
    }
    return {
        "comparability": comparability,
        "evidence_quality": _compare_evidence_quality(current_window, baseline_window, comparability),
        "slices": slices,
    }


def _outcome_status(comparison: dict[str, Any]) -> str:
    if comparison["comparability"]["state"] != "comparable":
        return "not_comparable"
    if comparison["evidence_quality"]["state"] == "partial":
        return "partial"
    return "ok"


def _suppress_effect_numbers(comparison: dict[str, Any]) -> None:
    def suppress_metric(metric: dict[str, Any]) -> None:
        delta = metric.get("delta")
        if isinstance(delta, dict):
            delta["absolute"] = None
            delta["percent"] = None
            delta["percent_reason"] = "too_early"
            delta["comparable"] = False

    slices = comparison.get("slices", {})
    total = slices.get("total", {})
    for metric in total.get("metrics", {}).values():
        if isinstance(metric, dict):
            suppress_metric(metric)
    for group_name in ("queries", "pages"):
        for row in slices.get(group_name, []):
            if isinstance(row, dict):
                for metric in row.get("metrics", {}).values():
                    if isinstance(metric, dict):
                        suppress_metric(metric)


def _parse_date_window(start: str, end: str) -> tuple[dt.date, dt.date]:
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end)
    if end_date < start_date:
        raise ValueError("window end must be on or after start")
    return start_date, end_date


def _window_days(start: dt.date, end: dt.date) -> int:
    return (end - start).days + 1


def _window_payload(start: dt.date, end: dt.date) -> dict[str, Any]:
    return {"start": start.isoformat(), "end": end.isoformat(), "days": _window_days(start, end)}


def _load_compare_window(
    storage: SEOStorage,
    *,
    project_id: str,
    timezone: str,
    start: dt.date,
    end: dt.date,
    include_targets: dict[str, list[str]] | None = None,
    exclude_targets: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    search_rows = storage.fetchall(
        """
        SELECT source, effective_start, effective_end, query_text, page_url,
               clicks, impressions, ctr, average_position, dataset_coverage,
               freshness, comparability, sampled
        FROM search_performance
        WHERE project_id = ?
          AND is_current = 1
          AND segment_id NOT IN ('total', 'brand')
          AND effective_start <= ?
          AND effective_end >= ?
        """,
        (project_id, end.isoformat(), start.isoformat()),
    )
    if include_targets is not None or exclude_targets is not None:
        search_rows = [
            row
            for row in search_rows
            if _search_row_target_allowed(row, include_targets=include_targets, exclude_targets=exclude_targets)
        ]
        traffic_rows = []
    else:
        traffic_rows = storage.fetchall(
            """
            SELECT source, effective_start, effective_end, visits, users, pageviews,
                   dataset_coverage, freshness, comparability, 0 AS sampled
            FROM traffic_metrics
            WHERE project_id = ?
              AND is_current = 1
              AND attribution_model != 'ga4_session_all_channels'
              AND effective_start <= ?
              AND effective_end >= ?
            """,
            (project_id, end.isoformat(), start.isoformat()),
        )
    search_totals = _aggregate_search(search_rows)
    query_groups = _aggregate_search_groups(search_rows, "query_text")
    page_groups = _aggregate_search_groups(search_rows, "page_url")
    snapshot = {
        "project": {"project_id": project_id},
        "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": timezone},
        "coverage": {
            "search_performance": {"row_count": len(search_rows)},
            "traffic_metrics": {"row_count": len(traffic_rows)},
        },
        "formula_inputs": {
            "search_performance_totals": search_totals,
            "queries": query_groups,
            "pages": page_groups,
            "traffic_totals": _aggregate_traffic(traffic_rows),
        },
        "evidence": {
            "search_performance": [_compare_evidence_row(row) for row in search_rows],
            "traffic_metrics": [_compare_evidence_row(row) for row in traffic_rows],
        },
        "source_set": sorted({str(row["source"]) for row in search_rows + traffic_rows}),
        "coverage_gaps": _window_coverage_gaps(start, end, search_rows + traffic_rows),
    }
    return snapshot


def _search_row_target_allowed(
    row: dict[str, Any],
    *,
    include_targets: dict[str, list[str]] | None,
    exclude_targets: dict[str, list[str]] | None,
) -> bool:
    matches = _search_row_matches_targets(row, include_targets or exclude_targets or {})
    if include_targets is not None and not matches:
        return False
    if exclude_targets is not None and matches:
        return False
    return True


def _search_row_matches_targets(row: dict[str, Any], targets: dict[str, list[str]]) -> bool:
    urls = set(targets.get("urls") or [])
    queries = set(targets.get("queries") or [])
    if not urls and not queries:
        return True
    return str(row.get("page_url") or "") in urls or str(row.get("query_text") or "") in queries


def _aggregate_search(rows: list[dict[str, Any]]) -> dict[str, float]:
    clicks = sum(int(row["clicks"] or 0) for row in rows)
    impressions = sum(int(row["impressions"] or 0) for row in rows)
    position_weight = sum(
        float(row["average_position"]) * int(row["impressions"] or 0)
        for row in rows
        if row.get("average_position") is not None
    )
    return {
        "clicks": float(clicks),
        "impressions": float(impressions),
        "ctr": (clicks / impressions) if impressions else 0.0,
        "average_position": (position_weight / impressions) if impressions else None,
    }


def _aggregate_search_groups(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get(field) or "")
        grouped.setdefault(key, []).append(row)
    return {key: _aggregate_search(value) for key, value in sorted(grouped.items())}


def _aggregate_traffic(rows: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for name in ("visits", "users", "pageviews"):
        values = [row.get(name) for row in rows if row.get(name) is not None]
        if values:
            totals[name] = float(sum(int(value) for value in values))
    return totals


def _compare_evidence_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": row.get("source"),
        "effective_start": row.get("effective_start"),
        "effective_end": row.get("effective_end"),
        "dataset_coverage": row.get("dataset_coverage"),
        "freshness": row.get("freshness"),
        "comparability": row.get("comparability"),
        "sampled": bool(row.get("sampled")),
    }


def _window_coverage_gaps(start: dt.date, end: dt.date, rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row["source"]), []).append(row)
    gaps: list[str] = []
    expected_dates = [start + dt.timedelta(days=offset) for offset in range(_window_days(start, end))]
    for source, source_rows in sorted(by_source.items()):
        covered: set[dt.date] = set()
        for row in source_rows:
            row_start = max(start, dt.date.fromisoformat(str(row["effective_start"])))
            row_end = min(end, dt.date.fromisoformat(str(row["effective_end"])))
            for offset in range(_window_days(row_start, row_end)):
                covered.add(row_start + dt.timedelta(days=offset))
        gaps.extend(f"{source}:{value.isoformat()}" for value in expected_dates if value not in covered)
    return gaps


def _compare_comparability(
    current_window: dict[str, Any],
    baseline_window: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    current_period = current_window["period"]
    baseline_period = baseline_window["period"]
    current_days = _window_days(
        dt.date.fromisoformat(current_period["start"]),
        dt.date.fromisoformat(current_period["end"]),
    )
    baseline_days = _window_days(
        dt.date.fromisoformat(baseline_period["start"]),
        dt.date.fromisoformat(baseline_period["end"]),
    )
    if current_days != baseline_days:
        reasons.append("window_length_mismatch")
    if current_window["source_set"] != baseline_window["source_set"]:
        reasons.append("source_set_mismatch")
    if current_window["coverage_gaps"]:
        reasons.append("current_collection_gap")
    if baseline_window["coverage_gaps"]:
        reasons.append("baseline_collection_gap")
    for metric_path in (
        "formula_inputs.search_performance_totals.clicks",
        "formula_inputs.search_performance_totals.impressions",
    ):
        state = compare_periods(
            current_window,
            baseline_window,
            metric_path=metric_path,
            comparison_kind="matched_period",
            minimum_denominator=0,
        ).state
        if state != "ready" and state != "not_ready":
            reason = f"search_performance_{state}"
            if reason not in reasons:
                reasons.append(reason)
    return {"state": "not_comparable" if reasons else "comparable", "reasons": reasons}


def _compare_evidence_quality(
    current_window: dict[str, Any],
    baseline_window: dict[str, Any],
    comparability: dict[str, Any],
) -> dict[str, Any]:
    rows = current_window["evidence"]["search_performance"] + baseline_window["evidence"]["search_performance"]
    rows += current_window["evidence"]["traffic_metrics"] + baseline_window["evidence"]["traffic_metrics"]
    if comparability["state"] != "comparable":
        state = "not_comparable"
    elif not rows:
        state = "missing"
    elif any(row.get("freshness") == "stale" for row in rows):
        state = "stale"
    elif any(row.get("dataset_coverage") not in (None, "complete") for row in rows):
        state = "partial"
    elif any(row.get("comparability") not in (None, "comparable") for row in rows):
        state = "not_comparable"
    else:
        state = "complete"
    return {
        "state": state,
        "current_rows": current_window["coverage"]["search_performance"]["row_count"]
        + current_window["coverage"]["traffic_metrics"]["row_count"],
        "baseline_rows": baseline_window["coverage"]["search_performance"]["row_count"]
        + baseline_window["coverage"]["traffic_metrics"]["row_count"],
    }


def _compare_slice(
    current_window: dict[str, Any],
    baseline_window: dict[str, Any],
    *,
    slice_name: str,
    metric_path_prefix: str,
    comparable: bool,
    metric_names: tuple[str, ...] = ("clicks", "impressions", "ctr", "average_position"),
) -> dict[str, Any]:
    metrics = {
        name: _metric_delta(
            current_window,
            baseline_window,
            metric_path=f"{metric_path_prefix}.{name}",
            comparable=comparable,
        )
        for name in metric_names
    }
    return {"slice": slice_name, "metrics": metrics}


def _compare_grouped_slices(
    current_window: dict[str, Any],
    baseline_window: dict[str, Any],
    *,
    group_name: str,
    comparable: bool,
) -> list[dict[str, Any]]:
    current_groups = current_window["formula_inputs"][group_name]
    baseline_groups = baseline_window["formula_inputs"][group_name]
    keys = sorted(set(current_groups) | set(baseline_groups))
    rows = []
    for key in keys:
        current_item = _snapshot_with_group_metric(current_window, group_name, key)
        baseline_item = _snapshot_with_group_metric(baseline_window, group_name, key)
        rows.append(
            {
                "key": key,
                "metrics": {
                    name: _metric_delta(
                        current_item,
                        baseline_item,
                        metric_path=f"formula_inputs.search_performance_totals.{name}",
                        comparable=comparable,
                    )
                    for name in ("clicks", "impressions", "ctr", "average_position")
                },
            }
        )
    return sorted(rows, key=lambda item: item["metrics"]["clicks"]["current"] or 0, reverse=True)


def _snapshot_with_group_metric(snapshot: dict[str, Any], group_name: str, key: str) -> dict[str, Any]:
    grouped = snapshot["formula_inputs"][group_name]
    totals = grouped.get(key, {"clicks": 0.0, "impressions": 0.0, "ctr": 0.0, "average_position": None})
    return {
        **snapshot,
        "formula_inputs": {
            **snapshot["formula_inputs"],
            "search_performance_totals": totals,
        },
    }


def _metric_delta(
    current_window: dict[str, Any],
    baseline_window: dict[str, Any],
    *,
    metric_path: str,
    comparable: bool,
) -> dict[str, Any]:
    result = compare_periods(
        current_window,
        baseline_window,
        metric_path=metric_path,
        comparison_kind="matched_period",
        minimum_denominator=0,
    )
    current = result.current_value
    baseline = result.baseline_value
    if current is None:
        current = 0.0
    if baseline is None:
        baseline = 0.0
    absolute = current - baseline
    percent = None
    percent_reason = None
    if not comparable:
        absolute = None
        percent_reason = "not_comparable"
    elif baseline == 0:
        percent_reason = "baseline_is_zero"
    else:
        percent = (absolute / baseline) * 100
    return {
        "current": current,
        "baseline": baseline,
        "delta": {
            "absolute": absolute,
            "percent": percent,
            "percent_reason": percent_reason,
            "comparable": comparable,
        },
        "comparison_state": "ready" if comparable and result.state in {"ready", "not_ready"} else result.state,
    }


def _today() -> dt.date:
    return dt.date.today()


def _daily_dates(args: argparse.Namespace) -> list[dt.date]:
    if getattr(args, "start", None) or getattr(args, "end", None):
        start_s, end_s = _collect_period(args, "1d")
        start, end = dt.date.fromisoformat(start_s), dt.date.fromisoformat(end_s)
    else:
        days_raw = getattr(args, "days", None)
        days = int(days_raw if days_raw is not None else 3)
        if days < 1:
            raise ConfigError("COLLECT_PERIOD_INVALID", "`--days` must be >= 1.", {"days": days})
        end = _today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=days - 1)
    return [start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)]


def _collect_daily_payload(args: argparse.Namespace) -> dict[str, Any]:
    pause = getattr(args, "pause_seconds", 0.0) or 0.0
    if not isinstance(pause, (int, float)) or not math.isfinite(pause) or pause < 0:
        raise ConfigError(
            "COLLECT_PERIOD_INVALID",
            "`--pause-seconds` must be a finite number >= 0.",
            {"pause_seconds": pause},
        )
    day_payloads: list[dict[str, Any]] = []
    failed: list[str] = []
    dates = _daily_dates(args)
    for index, day in enumerate(dates):
        day_args = argparse.Namespace(**{**vars(args), "start": day.isoformat(), "end": day.isoformat(),
                                         "period_id": "1d", "daily": False})
        payload = _collect_payload(day_args)
        day_payloads.append(payload)
        if not payload.get("ok", False):
            failed.append(day.isoformat())
        if args.pause_seconds and index < len(dates) - 1:
            time.sleep(args.pause_seconds)
    return {"ok": not failed, "command": "collect", "mode": "daily", "days": day_payloads, "failed_days": failed}


def _collect_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    plan = _collect_source_plan(config)
    unsupported = _collect_unsupported_sources(config)
    missing = _collect_missing_required_inputs(plan, os.environ)
    if missing:
        return _structured_error_payload(
            "COLLECT_NOT_READY",
            "Required collect inputs are missing.",
            {"missing_required_inputs": missing},
        )
    period_id = str(getattr(args, "period_id", "30d") or "30d")
    period_start, period_end = _collect_period(args, period_id)
    results = _collect_provider_results(
        config=config,
        plan=plan,
        period_id=period_id,
        period_start=period_start,
        period_end=period_end,
    )
    database_path = observer_home() / "projects" / config.project.namespace / "observer.db"
    storage = SEOStorage(database_path, observer_home=observer_home())
    run_started = _utc_now()
    run_id = _collect_run_id(config.project.namespace, period_id, period_start, period_end, uuid.uuid4().hex)
    try:
        source_summary = _write_collect_results(
            storage=storage,
            config=config,
            period_id=period_id,
            period_start=period_start,
            period_end=period_end,
            run_id=run_id,
            run_started=run_started,
            results=results,
            unsupported=unsupported,
        )
    except (StorageError, ValueError, TypeError, OSError, sqlite3.Error) as exc:
        return _structured_error_payload(
            "COLLECT_STORAGE_ERROR",
            "Collect results could not be stored.",
            {"error_type": exc.__class__.__name__, "error": str(exc), "database_path": str(database_path)},
        )
    payload = {
        "ok": True,
        "command": "collect",
        "project": config.project.namespace,
        "period_id": period_id,
        "period": {
            "start": period_start,
            "end": period_end,
            "timezone": config.project.timezone,
        },
        "run_id": run_id,
        "database_path": str(database_path),
        "sources": source_summary["sources"],
        "source_status": source_summary["source_status"],
    }
    if source_summary["source_status"]["failed_required_sources"]:
        return {
            "ok": False,
            "error": {
                "code": "COLLECT_PARTIAL",
                "message": "One or more required sources failed during collect.",
                "details": payload,
            },
        }
    return payload


def _crawl_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    storage = SEOStorage(default_database_path(config.project.namespace), observer_home=observer_home())
    storage.bootstrap()
    storage.upsert_project_config(config, config_hash=compute_config_hash(config))
    started_at = _utc_now()
    crawl_result = crawl_properties(config.properties, transport=StdlibCrawlTransport(), observed_at=started_at)
    finished_at = _utc_now()
    run_uuid = uuid.uuid4().hex
    run_id = f"crawl:{config.project.namespace}:{run_uuid[:16]}"
    request_hash = _sha256_text(
        _canonical_json(
            {
                "run_id": run_id,
                "project": config.project.namespace,
                "observed_at": crawl_result["observed_at"],
                "page_count": crawl_result["page_count"],
            }
        )
    )
    request_id = f"request:{request_hash[:16]}"
    collection_attempt_key = f"crawl-attempt:{request_hash[:16]}"
    artifact_text = _canonical_json(crawl_result, indent=2) + "\n"
    artifact_hash = _sha256_text(artifact_text)
    artifact = RawArtifact(
        artifact_id=f"artifact:{_sha256_text(request_id + ':' + artifact_hash)[:16]}",
        request_id=request_id,
        relative_path=f"{run_id}/local-crawl-{artifact_hash[:16]}.json",
        sha256=artifact_hash,
        content_type="application/json",
        compression="none",
        redaction_state="redacted",
        byte_size=len(artifact_text.encode("utf-8")),
    )
    request = SourceRequest(
        request_id=request_id,
        run_id=run_id,
        source="local_crawl",
        property_id="__all__",
        logical_observation_key=f"crawl:{config.project.namespace}:{run_id}",
        collection_attempt_key=collection_attempt_key,
        request_descriptor={
            "source": "local_crawl",
            "project": config.project.namespace,
            "properties": [{"id": prop.id, "url": prop.url} for prop in config.properties],
            "limits": crawl_result.get("limits") or {},
        },
        attempt=1,
        queried_at=started_at,
        completed_at=finished_at,
        transport_status="partial" if crawl_result.get("status") == "partial" else "success",
        freshness="provisional",
        pages_expected=len(config.properties),
        pages_received=int(crawl_result.get("page_count") or 0),
        error_code=str(crawl_result.get("truncated_reason")) if crawl_result.get("truncated_reason") else None,
    )
    run = CollectionRun(
        run_id=run_id,
        project_id=config.project.namespace,
        period_start=str(crawl_result["observed_at"])[:10],
        period_end=str(crawl_result["observed_at"])[:10],
        timezone=config.project.timezone,
        started_at=started_at,
        finished_at=finished_at,
        status="partial" if crawl_result.get("status") == "partial" else "complete",
        config_hash=compute_config_hash(config),
        cli_version=__version__,
        config_schema_version=config.schema_version,
    )
    artifact_path = _persist_collect_artifact(storage=storage, config=config, artifact=artifact, artifact_text=artifact_text)
    observations = [
        _crawl_page_observation(
            config=config,
            page=page,
            request=request,
            artifact=artifact,
            effective_at=str(crawl_result["observed_at"]),
            dataset_coverage="truncated" if crawl_result.get("status") == "partial" else "complete",
        )
        for page in crawl_result.get("pages", [])
        if isinstance(page, dict)
    ]
    storage.ingest_crawl_pages(run, request, [artifact], observations)
    crawled_urls = sorted({obs.crawled_url for obs in observations})
    stale_urls = _crawl_stale_current_pages(
        storage,
        project_id=config.project.namespace,
        confirmed_urls=crawled_urls,
    )
    known_pages = _crawl_known_current_pages(storage, project_id=config.project.namespace)
    reachable_urls = _crawl_reachable_urls(observations)
    orphan_pages = (
        []
        if crawl_result.get("truncated_reason")
        else _crawl_orphan_findings(known_pages, reachable_urls=reachable_urls)
    )
    return {
        "ok": True,
        "command": "crawl",
        "project": config.project.namespace,
        "run_id": run_id,
        "database_path": str(storage.db_path),
        "artifact_path": str(artifact_path),
        "request": {
            "request_id": request.request_id,
            "collection_attempt_key": request.collection_attempt_key,
        },
        "crawl": {
            "schema": crawl_result.get("schema"),
            "status": crawl_result.get("status"),
            "truncated_reason": crawl_result.get("truncated_reason"),
            "observed_at": crawl_result.get("observed_at"),
            "crawled_count": crawl_result.get("crawled_count"),
            "page_count": crawl_result.get("page_count"),
        },
        "storage": {
            "inserted_pages": len(observations),
            "confirmed_current_pages": crawled_urls,
            "stale_current_pages": stale_urls,
            "known_current_pages": sorted({row["url"] for row in known_pages}),
            "orphan_pages": orphan_pages,
        },
    }


def _opportunities_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    storage = SEOStorage(default_database_path(config.project.namespace), observer_home=observer_home())
    return build_opportunity_report(
        storage,
        OpportunityOptions(
            project_id=config.project.namespace,
            start=str(args.start),
            end=str(args.end),
            type_breakdown=bool(getattr(args, "type_breakdown", False)),
        ),
    )


def _provider_audit_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    output_dir = Path(str(args.output_dir)).expanduser() if getattr(args, "output_dir", None) else None
    return build_provider_audit_payload(
        config=config,
        start=str(args.start),
        end=str(args.end),
        output_dir=output_dir,
        env=os.environ,
        transport_factory=_JsonHttpTransport,
        collector=_provider_audit_collect_sources,
    )


def _render_report_payload(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(str(args.input)).expanduser()
    output_dir = Path(str(args.output_dir)).expanduser()
    markdown_text = input_path.read_text(encoding="utf-8")
    artifacts = write_polished_report_artifacts(
        markdown_text=markdown_text,
        output_dir=output_dir,
        basename=str(args.basename),
        title=str(args.title),
        subtitle=str(args.subtitle) if args.subtitle else None,
        render_pdf=not bool(args.no_pdf),
    )
    files: dict[str, Any] = {
        "html_report": {
            "path": str(artifacts.html_path),
            "sha256": _sha256_file(artifacts.html_path),
        }
    }
    if artifacts.pdf_path is not None:
        files["pdf_report"] = {
            "path": str(artifacts.pdf_path),
            "sha256": _sha256_file(artifacts.pdf_path),
        }
    payload: dict[str, Any] = {
        "ok": True,
        "command": "render-report",
        "input": str(input_path),
        "output_dir": str(output_dir),
        "artifacts": files,
    }
    if artifacts.pdf_error:
        payload["pdf_error"] = {
            "code": "REPORT_PDF_RENDER_FAILED",
            "message": artifacts.pdf_error,
        }
    return payload


def _provider_audit_collect_sources(**kwargs: Any) -> dict[str, Any]:
    return collect_provider_audit_sources(**kwargs)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _competitors_discover_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    output_dir = Path(str(args.output_dir)).expanduser() if getattr(args, "output_dir", None) else None
    provider = config.providers.get("dataforseo")
    endpoint = provider.endpoint if provider and provider.endpoint else "https://api.dataforseo.com"
    return discover_competitors(
        config,
        CompetitorDiscoveryOptions(
            provider_mode=str(getattr(args, "provider_mode", "artifact") or "artifact"),
            output_dir=output_dir,
            property_id=getattr(args, "property_id", None),
            target_domain=getattr(args, "target_domain", None),
            limit_competitors=int(getattr(args, "limit_competitors", 20) or 20),
            picked_competitors=int(getattr(args, "picked_competitors", 3) or 3),
            gap_limit=int(getattr(args, "gap_limit", 100) or 100),
            allow_paid=bool(getattr(args, "allow_paid", False)),
        ),
        env=os.environ,
        transport_factory=lambda base_url: _JsonHttpTransport(base_url or endpoint),
    )


def _competitors_audit_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    provider = config.providers.get("dataforseo")
    endpoint = provider.endpoint if provider and provider.endpoint else "https://api.dataforseo.com"
    baseline = getattr(args, "baseline_artifact", None)
    return audit_competitors(
        config,
        CompetitorAuditOptions(
            keyword_set_id=str(args.keyword_set),
            start=str(args.start),
            end=str(args.end),
            provider_mode=str(getattr(args, "provider_mode", "artifact") or "artifact"),
            output_dir=Path(str(args.output_dir)).expanduser(),
            allow_paid=bool(getattr(args, "allow_paid", False)),
            baseline_artifact=Path(str(baseline)).expanduser() if baseline else None,
            serp_depth=getattr(args, "serp_depth", None),
            devices=tuple(getattr(args, "device", ()) or ()),
        ),
        env=os.environ,
        transport_factory=lambda base_url: _JsonHttpTransport(base_url or endpoint),
    )


def _competitors_research_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    confirmed = getattr(args, "confirmed_serp_artifact", None)
    urls = tuple(
        item.strip()
        for item in str(getattr(args, "urls", "") or "").split(",")
        if item.strip()
    )
    return research_competitors(
        config,
        CompetitorResearchOptions(
            keyword_set_id=str(args.keyword_set),
            provider_mode=str(getattr(args, "provider_mode", "artifact") or "artifact"),
            output_dir=Path(str(args.output_dir)).expanduser(),
            urls=urls,
            allow_paid=bool(getattr(args, "allow_paid", False)),
            confirmed_serp_artifact=Path(str(confirmed)).expanduser() if confirmed else None,
            max_pages=int(getattr(args, "max_pages", 20) or 20),
            max_response_bytes=int(getattr(args, "max_response_bytes", 1_000_000) or 1_000_000),
            timeout_seconds=int(getattr(args, "timeout_seconds", 10) or 10),
        ),
        env=os.environ,
    )


def _competitors_report_payload(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _selected_config_path(args)
    if config_path is None:
        return _structured_error_payload(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(Path.cwd() / ".seo-observer" / "project.toml")},
        )
    config = load_project_config(config_path)
    discovery = getattr(args, "discovery_artifact", None)
    serp = getattr(args, "serp_artifact", None)
    content = getattr(args, "content_artifact", None)
    llm_fixture = getattr(args, "llm_fixture", None)
    return build_competitor_report(
        config,
        CompetitorReportOptions(
            provider_mode=str(getattr(args, "provider_mode", "artifact") or "artifact"),
            output_dir=Path(str(args.output_dir)).expanduser(),
            discovery_artifact=Path(str(discovery)).expanduser() if discovery else None,
            serp_artifact=Path(str(serp)).expanduser() if serp else None,
            content_artifact=Path(str(content)).expanduser() if content else None,
            allow_paid=bool(getattr(args, "allow_paid", False)),
            with_brief=bool(getattr(args, "with_brief", False)),
            llm_fixture=Path(str(llm_fixture)).expanduser() if llm_fixture else None,
        ),
    )


def _composite_report_payload(args: argparse.Namespace) -> dict[str, Any]:
    serp = getattr(args, "serp_artifact", None)
    metrics = getattr(args, "metrics_artifact", None)
    return build_composite_report(
        CompositeReportOptions(
            provider_artifact=Path(str(args.provider_artifact)).expanduser(),
            research_artifact=Path(str(args.research_artifact)).expanduser(),
            content_artifact=Path(str(args.content_artifact)).expanduser(),
            serp_artifact=Path(str(serp)).expanduser() if serp else None,
            metrics_artifact=Path(str(metrics)).expanduser() if metrics else None,
            output_dir=Path(str(args.output_dir)).expanduser(),
        )
    )


def _collect_missing_required_inputs(plan: list[dict[str, Any]], env: dict[str, str]) -> list[dict[str, Any]]:
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

        has_usable_credential = False
        for kind, env_var in [
            ("credential_env", credential_env),
            ("token_file_env", token_file_env),
            ("credential_file_env", credential_file_env),
        ]:
            if not env_var:
                continue
            val = env.get(str(env_var), "")
            if not val:
                continue
            if kind in ("credential_file_env", "token_file_env"):
                if not Path(val).is_file():
                    continue
            has_usable_credential = True
            break

        if not has_usable_credential:
            primary_env = None
            for kind, env_var in [
                ("credential_env", credential_env),
                ("token_file_env", token_file_env),
                ("credential_file_env", credential_file_env),
            ]:
                if env_var:
                    primary_env = env_var
                    break

            if not primary_env:
                key = (source_name, "")
                if key not in checked:
                    checked.add(key)
                    missing.append({"source": source_name, "reason": "required source has no credential env configured"})
                continue

            key = (source_name, str(primary_env))
            if key not in checked:
                checked.add(key)
                val = env.get(str(primary_env), "")
                if not val:
                    missing.append({"source": source_name, "env": str(primary_env), "reason": "missing environment variable"})
                else:
                    missing.append({"source": source_name, "env": str(primary_env), "reason": "credential file does not exist"})

        endpoint_env = fields.get("endpoint_env")
        if source_name.startswith("outcome_") and endpoint_env and not env.get(str(endpoint_env), ""):
            key = (source_name, str(endpoint_env))
            if key not in checked:
                checked.add(key)
                missing.append({"source": source_name, "env": str(endpoint_env), "reason": "missing environment variable"})
    return missing


_COLLECT_SUPPORTED = {"google_search_console", "yandex_metrica", "yandex_webmaster", "ga4"}


def _collect_supports(source_name: str, source: Any) -> bool:
    if source_name in _COLLECT_SUPPORTED:
        return True
    return source_name.startswith("outcome_") and source.fields.get("adapter") == "http_aggregate"


def _collect_source_plan(config: Any) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for binding in config.source_bindings:
        source = config.sources.get(binding.source)
        if source is None or not source.enabled or not _collect_supports(binding.source, source):
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


def _collect_unsupported_sources(config: Any) -> list[dict[str, Any]]:
    unsupported: list[dict[str, Any]] = []
    for source_name, source in sorted(config.sources.items()):
        if not source.enabled or _collect_supports(source_name, source):
            continue
        unsupported.append(
            {
                "source": source_name,
                "required": source.required,
                "status": "unsupported",
                "observations": 0,
                "error": {
                    "code": "COLLECT_SOURCE_UNSUPPORTED",
                    "message": "Source is configured but this collect implementation does not support it yet.",
                },
            }
        )
    return unsupported


def _collect_provider_results(
    *,
    config: Any,
    plan: list[dict[str, Any]],
    period_id: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in plan:
        try:
            results.append(
                _collect_one_provider(
                    item=item,
                    config=config,
                    period_id=period_id,
                    period_start=period_start,
                    period_end=period_end,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "source": item["source"],
                    "property_id": item["property_id"],
                    "remote_id": item["remote_id"],
                    "required": item["required"],
                    "status": "failed",
                    "collection": _collect_collection_for_source(str(item["source"])),
                    "metadata": {},
                    "observations": [],
                    "error": {
                        "code": exc.__class__.__name__,
                        "message": str(exc),
                    },
                }
            )
    return results


def _collect_collection_for_source(source_name: str) -> str:
    if source_name.startswith("outcome_"):
        return "outcome_metrics"
    if source_name in {"yandex_metrica", "ga4"}:
        return "traffic_metrics"
    return "search_performance"


def _collect_one_provider(
    *,
    item: dict[str, Any],
    config: Any,
    period_id: str,
    period_start: str,
    period_end: str,
) -> dict[str, Any]:
    source_name = str(item["source"])
    fields = item["fields"]
    property_id = str(item["property_id"])
    remote_id = str(item["remote_id"])
    if source_name == "google_search_console":
        adapter = GSCAdapter(
            GSCSource(
                site_url=remote_id,
                credential_file_env=str(fields.get("credential_file_env") or ""),
                property_id=property_id,
                timezone=config.project.timezone,
                access_token=_gsc_access_token_from_fields(fields, dict(os.environ)),
                row_limit=int(fields.get("row_limit") or 500),
                data_state=str(fields.get("data_state") or "final"),
                finalize_after=fields.get("finalize_after"),
            ),
            _JsonHttpTransport("https://www.googleapis.com"),
        )
        result = adapter.fetch_search_performance(
            GSCPeriod(period_start, period_end),
            query=SearchAnalyticsQuery(dimensions=("query", "page", "device", "country")),
        )
        split = adapter.fetch_brand_split(GSCPeriod(period_start, period_end), config.channels.brand_terms)
        result = {
            **result,
            "metadata": _merge_source_metadata(
                result.get("metadata") or {}, split.get("metadata") or {}
            ),
            "observations": list(result.get("observations") or []) + list(split["observations"]),
        }
    elif source_name == "yandex_metrica":
        result = MetricaAdapter(
            MetricaSource(
                counter_id=str(fields["counter_id"]),
                token=os.environ[str(fields["credential_env"])],
                property_id=property_id,
                timezone=config.project.timezone,
                accuracy=str(fields.get("accuracy") or "full"),
                finalize_after=fields.get("finalize_after"),
            ),
            _JsonHttpTransport("https://api-metrika.yandex.net"),
        ).fetch_organic_traffic(MetricaPeriod(period_start, period_end))
    elif source_name == "ga4":
        adapter = GA4Adapter(
            GA4Source(
                property_resource=remote_id,
                token=_ga4_access_token(fields, os.environ),
                property_id=property_id,
                timezone=config.project.timezone,
                limit=int(fields.get("limit") or 10000),
                finalize_after=fields.get("finalize_after"),
                channel_timezone=str(fields.get("timezone") or "property_timezone"),
            ),
            _JsonHttpTransport("https://analyticsdata.googleapis.com"),
        )
        organic = adapter.fetch_organic_traffic_bundle(GA4Period(period_start, period_end))
        channels = adapter.fetch_channel_traffic_bundle(GA4Period(period_start, period_end))
        result = {
            **organic,
            "metadata": _merge_source_metadata(
                organic.get("metadata") or {}, channels.get("metadata") or {}
            ),
            "observations": organic["observations"] + channels["observations"],
        }
    elif source_name.startswith("outcome_"):
        descriptor = descriptor_from_source_fields(source_name, fields, timezone=config.project.timezone)
        adapter = AggregateOutcomeAdapter(
            AggregateOutcomeSource(source_id=source_name, adapter="http_aggregate", approved_views=(descriptor,)),
            HttpAggregateTransport(os.environ[str(fields["endpoint_env"])], os.environ[str(fields["credential_env"])]),
            project_id=config.project.namespace,
            property_id=property_id,
        )
        facts = adapter.fetch_outcome_facts(OutcomePeriod(period_start, period_end), view_id=descriptor.view_id)
        result = {
            "collection": "outcome_metrics",
            "metadata": facts["metadata"],
            "observations": [
                dataclasses.asdict(fact) for fact in reduce_outcome_facts(facts["observations"])
            ],
        }
    elif source_name == "yandex_webmaster":
        result = WebmasterAdapter(
            WebmasterSource(
                user_id=str(fields["user_id"]),
                host_id=remote_id,
                token=os.environ[str(fields["credential_env"])],
                property_id=property_id,
                timezone=config.project.timezone,
                limit=int(fields.get("limit") or 500),
                finalize_after=fields.get("finalize_after"),
            ),
            _JsonHttpTransport("https://api.webmaster.yandex.net"),
        ).fetch_popular_queries(WebmasterPeriod(period_start, period_end))
    else:
        raise ConfigError(
            "COLLECT_SOURCE_UNSUPPORTED",
            "Collect source is not supported.",
            {"source": source_name},
        )
    return {
        "source": source_name,
        "property_id": property_id,
        "remote_id": remote_id,
        "required": item["required"],
        "status": "ok",
        "collection": result["collection"],
        "metadata": result.get("metadata") or {},
        "observations": result.get("observations") or [],
    }


class _JsonHttpTransport:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.ssl_context = _default_ssl_context()

    def get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{self.base_url}{endpoint}"
        if query:
            url = f"{url}?{query}"
        return self._request_json(url, headers=headers)

    def post_json(
        self,
        endpoint: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        body = _canonical_json(json).encode("utf-8")
        return self._request_json(
            f"{self.base_url}{endpoint}",
            headers={**headers, "Content-Type": "application/json"},
            data=body,
        )

    def _request_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None = None,
    ) -> dict[str, Any]:
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        with urllib.request.urlopen(request, timeout=30, context=self.ssl_context) as response:
            payload = response.read().decode("utf-8")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ConfigError("PROVIDER_RESPONSE_INVALID", "Provider response must be a JSON object.", {})
        return value




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
            "GA4 collect requires google-auth.",
            {"package": "google-auth"},
        ) from exc
    credentials = service_account.Credentials.from_service_account_file(
        credential_file,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    credentials.refresh(Request())
    return str(credentials.token)


def _default_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _write_collect_results(
    *,
    storage: SEOStorage,
    config: Any,
    period_id: str,
    period_start: str,
    period_end: str,
    run_id: str,
    run_started: str,
    results: list[dict[str, Any]],
    unsupported: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config_hash = compute_config_hash(config)
    run = CollectionRun(
        run_id=run_id,
        project_id=config.project.namespace,
        period_start=period_start,
        period_end=period_end,
        timezone=config.project.timezone,
        started_at=run_started,
        finished_at=_utc_now(),
        status="partial",
        config_hash=config_hash,
        cli_version=__version__,
        config_schema_version=config.schema_version,
    )
    storage.bootstrap()
    storage.upsert_project_config(config, config_hash=config_hash)
    sources: dict[str, dict[str, Any]] = {}
    observations_written = 0
    observations_received = 0
    ok_sources = 0
    partial_sources = 0
    failed_sources = 0
    failed_required_sources = 0
    unsupported_sources = 0
    for item in unsupported or []:
        source = str(item.get("source") or "unknown")
        unsupported_sources += 1
        block = sources.setdefault(source, {"status": "unsupported", "observations": 0, "observations_written": 0})
        block["status"] = _collect_aggregate_status(str(block["status"]), "unsupported")
        block["error"] = item.get("error")
    for result in results:
        source = str(result.get("source") or "unknown")
        status = str(result.get("status") or "ok")
        observations = list(result.get("observations") or [])
        collection = str(result.get("collection") or "search_performance")
        valid_observations = observations
        invalid_rows: list[dict[str, Any]] = []
        if collection == "search_performance":
            valid_observations, invalid_rows = _collect_split_valid_search_rows(observations)
            if invalid_rows:
                status = "partial"
                result = {
                    **result,
                    "status": status,
                    "error": {
                        "code": "NORMALIZATION_INVALID_SEARCH_ROW",
                        "message": f"{len(invalid_rows)} search_performance row(s) failed normalization validation.",
                        "invalid_observations": len(invalid_rows),
                    },
                }
        observations_received += len(observations)
        if status == "ok":
            ok_sources += 1
        elif status == "partial":
            partial_sources += 1
        else:
            failed_sources += 1
            if bool(result.get("required", False)):
                failed_required_sources += 1
        block = sources.setdefault(source, {"status": status, "observations": 0, "observations_written": 0})
        block["status"] = _collect_aggregate_status(str(block["status"]), status)
        block["observations"] += len(observations)
        if invalid_rows:
            block["invalid_observations"] = int(block.get("invalid_observations", 0)) + len(invalid_rows)
        if result.get("error"):
            block["error"] = result["error"]
        if collection not in {"search_performance", "traffic_metrics", "outcome_metrics"}:
            continue
        request, artifact, artifact_text = _collect_request_artifact(
            result=result,
            run=run,
            collection=collection,
            period_id=period_id,
            period_start=period_start,
            period_end=period_end,
        )
        artifact_path = _persist_collect_artifact(storage=storage, config=config, artifact=artifact, artifact_text=artifact_text)
        if collection == "search_performance":
            search_rows = [
                _collect_search_observation(
                    config=config,
                    result=result,
                    row=row,
                    period_id=period_id,
                    period_start=period_start,
                    period_end=period_end,
                    run=run,
                    request=request,
                    artifact=artifact,
                )
                for row in valid_observations
            ]
            try:
                storage.ingest_search_performance(run, request, [artifact], search_rows)
            except Exception:
                artifact_path.unlink(missing_ok=True)
                raise
            observations_written += len(search_rows)
            block["observations_written"] += len(search_rows)
        elif collection == "traffic_metrics":
            traffic_rows = [
                _collect_traffic_observation(
                    config=config,
                    row=row,
                    period_start=period_start,
                    period_end=period_end,
                    request=request,
                    artifact=artifact,
                )
                for row in observations
            ]
            try:
                storage.ingest_traffic_metrics(
                    run,
                    request,
                    [artifact],
                    traffic_rows,
                    authoritative=_collect_authoritative_refresh(
                        result=result,
                        period_start=period_start,
                        period_end=period_end,
                    ),
                )
            except Exception:
                artifact_path.unlink(missing_ok=True)
                raise
            observations_written += len(traffic_rows)
            block["observations_written"] += len(traffic_rows)
        elif collection == "outcome_metrics":
            outcome_rows = [
                _collect_outcome_observation(
                    config=config,
                    row=row,
                    period_start=period_start,
                    period_end=period_end,
                    request=request,
                    artifact=artifact,
                )
                for row in observations
            ]
            try:
                storage.ingest_outcome_metrics(
                    run,
                    request,
                    [artifact],
                    outcome_rows,
                    authoritative=_collect_authoritative_refresh(
                        result=result,
                        period_start=period_start,
                        period_end=period_end,
                    ),
                )
            except Exception:
                artifact_path.unlink(missing_ok=True)
                raise
            observations_written += len(outcome_rows)
            block["observations_written"] += len(outcome_rows)
    final_status = "partial" if partial_sources or failed_sources else "complete"
    storage.update_collection_run_status(run.run_id, final_status, finished_at=run.finished_at)
    return {
        "sources": sources,
        "source_status": {
            "ok_sources": ok_sources,
            "partial_sources": partial_sources,
            "failed_sources": failed_sources,
            "failed_required_sources": failed_required_sources,
            "unsupported_sources": unsupported_sources,
            "observations_received": observations_received,
            "observations_written": observations_written,
            "observations": observations_written,
        },
    }


def _persist_collect_artifact(
    *,
    storage: SEOStorage,
    config: Any,
    artifact: RawArtifact,
    artifact_text: str,
) -> Path:
    path = resolve_raw_artifact_path(
        observer_home=storage.observer_home,
        project_id=config.project.namespace,
        relative_path=artifact.relative_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact_text, encoding="utf-8")
    actual_sha = _sha256_text(artifact_text)
    if actual_sha != artifact.sha256:
        raise StorageError("Raw artifact content hash does not match artifact metadata.")
    return path


def _collect_split_valid_search_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        reason = _collect_search_row_invalid_reason(row)
        if reason:
            invalid.append({"index": index, "reason": reason})
        else:
            valid.append(row)
    return valid, invalid


def _collect_search_row_invalid_reason(row: dict[str, Any]) -> str | None:
    impressions = _optional_int(row.get("impressions")) or 0
    clicks = _optional_int(row.get("clicks")) or 0
    if impressions < 0 or clicks < 0:
        return "negative_search_count"
    ctr = _optional_float(row.get("ctr")) or 0
    if ctr < 0:
        return "negative_ctr"
    return None


def _collect_request_artifact(
    *,
    result: dict[str, Any],
    run: CollectionRun,
    collection: str,
    period_id: str,
    period_start: str,
    period_end: str,
) -> tuple[SourceRequest, RawArtifact, str]:
    source = str(result.get("source") or "unknown")
    property_id = str(result.get("property_id") or "__all__")
    remote_id = str(result.get("remote_id") or property_id)
    request_hash = _sha256_text(
        _canonical_json(
            {
                "run_id": run.run_id,
                "source": source,
                "property": property_id,
                "remote": remote_id,
                "collection": collection,
            }
        )
    )
    request_id = f"request:{request_hash[:16]}"
    artifact_text = _canonical_json(
        {
            "metadata": result.get("metadata") or {},
            "error": result.get("error"),
            "observations": result.get("observations") or [],
        }
    )
    artifact_hash = _sha256_text(artifact_text)
    artifact_id_hash = _sha256_text(f"{request_id}:{artifact_hash}")
    artifact = RawArtifact(
        artifact_id=f"artifact:{artifact_id_hash[:16]}",
        request_id=request_id,
        relative_path=f"{run.run_id}/{source}-{property_id}-{artifact_hash[:16]}.json",
        sha256=artifact_hash,
        content_type="application/json",
        compression="none",
        redaction_state="redacted",
        byte_size=len(artifact_text.encode("utf-8")),
    )
    metadata = result.get("metadata") or {}
    if collection == "outcome_metrics":
        logical_key = f"{source}:{property_id}:day:{collection}"
    else:
        logical_key = f"{source}:{property_id}:{period_id}:{collection}"
    error = result.get("error") or {}
    request = SourceRequest(
        request_id=request_id,
        run_id=run.run_id,
        source=source,
        property_id=property_id,
        logical_observation_key=logical_key,
        collection_attempt_key=f"attempt:{request_hash[:16]}",
        request_descriptor={
            "source": source,
            "property_id": property_id,
            "remote_id": remote_id,
            "period_start": period_start,
            "period_end": period_end,
            "period_id": period_id,
            "collection": collection,
        },
        attempt=1,
        queried_at=run.started_at,
        completed_at=run.finished_at,
        transport_status=_collect_transport_status(str(result.get("status") or "ok")),
        freshness=str(metadata.get("freshness") or "provisional"),
        sampled=bool(metadata.get("sampled", False)),
        sample_share=metadata.get("sample_share"),
        rows_received=metadata.get("rows_received"),
        pages_received=metadata.get("pages_received"),
        error_code=str(error.get("code")) if error.get("code") else None,
        error_summary=str(error.get("message"))[:500] if error.get("message") else None,
    )
    return request, artifact, artifact_text


def _collect_authoritative_refresh(
    *,
    result: dict[str, Any],
    period_start: str,
    period_end: str,
) -> bool:
    """True only when a successful complete single-day result may retire absent facts."""
    metadata = result.get("metadata") or {}
    return (
        str(result.get("status") or "ok") == "ok"
        and str(metadata.get("dataset_coverage") or "") == "complete"
        and period_start == period_end
    )


def _collect_aggregate_status(current: str, new: str) -> str:
    priority = {
        "failed": 50,
        "partial": 40,
        "unsupported": 30,
        "ok": 10,
    }
    return new if priority.get(new, 20) > priority.get(current, 20) else current


def _collect_transport_status(status: str) -> str:
    if status == "ok":
        return "success"
    if status in {"partial", "failed", "cancelled", "abandoned"}:
        return status
    return "failed"


def _collect_search_observation(
    *,
    config: Any,
    result: dict[str, Any],
    row: dict[str, Any],
    period_id: str,
    period_start: str,
    period_end: str,
    run: CollectionRun,
    request: SourceRequest,
    artifact: RawArtifact,
) -> SearchPerformanceObservation:
    source = str(row.get("source") or result.get("source") or "unknown")
    property_id = str(row.get("property_id") or result.get("property_id") or "__all__")
    return SearchPerformanceObservation(
        project_id=config.project.namespace,
        property_id=property_id,
        source=source,
        effective_start=str(row.get("effective_start") or period_start),
        effective_end=str(row.get("effective_end") or period_end),
        source_timezone=str(row.get("source_timezone") or config.project.timezone),
        effective_instant_start=f"{row.get('effective_start') or period_start}T00:00:00Z",
        effective_instant_end=f"{row.get('effective_end') or period_end}T23:59:59Z",
        observed_at=run.finished_at or run.started_at,
        reporting_period_id=period_id,
        request_id=request.request_id,
        artifact_id=artifact.artifact_id,
        logical_observation_key=request.logical_observation_key,
        collection_attempt_key=request.collection_attempt_key,
        query_id=str(row.get("query_id") or "__aggregate__"),
        query_text=str(row.get("query_text") or "__aggregate__"),
        page_id=str(row.get("page_id") or property_id),
        page_url=str(row.get("page_url") or _property_url(config, property_id)),
        search_engine=str(row.get("search_engine") or _search_engine(source)),
        impressions=int(float(row.get("impressions") or 0)),
        clicks=int(float(row.get("clicks") or 0)),
        ctr=float(row.get("ctr") or 0),
        average_position=row.get("average_position"),
        dataset_coverage=_storage_dataset_coverage(str(row.get("dataset_coverage") or "unknown")),
        freshness=str(row.get("freshness") or "provisional"),
        comparability=_storage_comparability(str(row.get("comparability") or "comparable")),
        device=row.get("device"),
        country=row.get("country"),
        region=row.get("region"),
        segment_id=row.get("segment_id"),
        sampled=bool(row.get("sampled", False)),
        sample_share=row.get("sample_share"),
        normalizer_version=str(row.get("normalizer_version") or "collect-v1"),
    )


def _collect_traffic_observation(
    *,
    config: Any,
    row: dict[str, Any],
    period_start: str,
    period_end: str,
    request: SourceRequest,
    artifact: RawArtifact,
) -> TrafficMetricObservation:
    attribution_model = str(row.get("attribution_model") or "__all__")
    logical_key = request.logical_observation_key
    if attribution_model == "ga4_session_all_channels":
        descriptor = request.request_descriptor if isinstance(request.request_descriptor, dict) else {}
        key_source = str(descriptor.get("source") or request.source)
        key_property = str(descriptor.get("property_id") or request.property_id or "__all__")
        logical_key = f"{key_source}:{key_property}:day:traffic_metrics"
    return TrafficMetricObservation(
        project_id=config.project.namespace,
        property_id=str(row.get("property_id") or "__all__"),
        source=str(row.get("source") or "yandex_metrica"),
        effective_start=str(row.get("effective_start") or period_start),
        effective_end=str(row.get("effective_end") or period_end),
        source_timezone=str(row.get("source_timezone") or config.project.timezone),
        request_id=request.request_id,
        artifact_id=artifact.artifact_id,
        logical_observation_key=logical_key,
        collection_attempt_key=request.collection_attempt_key,
        channel=str(row.get("channel") or "__all__"),
        search_engine=str(row.get("search_engine") or "__all__"),
        landing_page_id=str(row.get("landing_page_id") or "__all__"),
        device=str(row.get("device") or "__all__"),
        region=str(row.get("region") or "__all__"),
        attribution_model=attribution_model,
        visits=_optional_int(row.get("visits")),
        users=_optional_int(row.get("users")),
        pageviews=_optional_int(row.get("pageviews")),
        bounce_rate=_optional_float(row.get("bounce_rate")),
        avg_visit_duration_seconds=_optional_float(row.get("avg_visit_duration_seconds")),
        dataset_coverage=_storage_dataset_coverage(str(row.get("dataset_coverage") or "unknown")),
        freshness=str(row.get("freshness") or "provisional"),
        comparability=_storage_comparability(str(row.get("comparability") or "comparable")),
        normalizer_version=str(row.get("normalizer_version") or "metrica-v1"),
    )


def _collect_outcome_observation(
    *,
    config: Any,
    row: dict[str, Any],
    period_start: str,
    period_end: str,
    request: SourceRequest,
    artifact: RawArtifact,
) -> OutcomeMetricObservation:
    return OutcomeMetricObservation(
        project_id=config.project.namespace,
        property_id=str(row.get("property_id") or "__all__"),
        source=str(row.get("source") or "outcome"),
        effective_start=str(row.get("period_start") or period_start),
        effective_end=str(row.get("period_end") or period_end),
        source_timezone=str(row.get("timezone") or config.project.timezone),
        request_id=request.request_id,
        artifact_id=artifact.artifact_id,
        logical_observation_key=request.logical_observation_key,
        collection_attempt_key=request.collection_attempt_key,
        outcome_id=str(row["outcome_id"]),
        evidence_kind=str(row.get("evidence_kind") or "server_fact"),
        counting_unit=str(row["counting_unit"]),
        deduplication_rule=str(row.get("dedupe_key") or "__unknown__"),
        population_scope=str(row.get("population_id") or "__all__"),
        attribution_model=str(row.get("attribution_model") or "__all__"),
        attribution_scope=str(row.get("attribution_scope") or "__all__"),
        attribution_level="channel_aggregate" if row.get("traffic_channel") not in (None, "__all__") else "server_aggregate",
        traffic_channel=str(row.get("traffic_channel") or "__all__"),
        count=_optional_int(row.get("count")),
        value_minor=_optional_int(row.get("value_minor")),
        currency=row.get("currency"),
        dataset_coverage=_storage_dataset_coverage(str(row.get("dataset_coverage") or "unknown")),
        freshness=str(row.get("freshness") or "provisional"),
        comparability="comparable",
        normalizer_version="http-aggregate-v1",
    )


def _crawl_page_observation(
    *,
    config: Any,
    page: dict[str, Any],
    request: SourceRequest,
    artifact: RawArtifact,
    effective_at: str,
    dataset_coverage: str,
) -> CrawlPageObservation:
    crawled_url = str(page.get("url") or "")
    if not crawled_url:
        raise ValueError("Crawl page is missing url.")
    return CrawlPageObservation(
        project_id=config.project.namespace,
        property_id=str(page.get("property_id") or "__all__"),
        source="local_crawl",
        effective_at=effective_at,
        source_timezone=config.project.timezone,
        request_id=request.request_id,
        artifact_id=artifact.artifact_id,
        logical_observation_key=crawled_url,
        collection_attempt_key=request.collection_attempt_key,
        crawled_url=crawled_url,
        final_url=str(page.get("final_url") or crawled_url),
        depth=int(page.get("depth") or 0),
        fetch_status=_optional_int(page.get("fetch_status")),
        robots_status=str(page.get("robots_status") or "allowed"),
        content_type=str(page.get("content_type")) if page.get("content_type") is not None else None,
        byte_size=int(page.get("byte_size") or 0),
        title=str(page.get("title") or ""),
        meta_description=str(page.get("meta_description") or ""),
        h1_text=str(page.get("h1_text") or ""),
        h1_count=int(page.get("h1_count") or 0),
        canonical_url=str(page.get("canonical_url")) if page.get("canonical_url") is not None else None,
        header_canonical_url=(
            str(page.get("header_canonical_url")) if page.get("header_canonical_url") is not None else None
        ),
        meta_robots=str(page.get("meta_robots")) if page.get("meta_robots") is not None else None,
        x_robots_tag=str(page.get("x_robots_tag")) if page.get("x_robots_tag") is not None else None,
        hreflang_json=_canonical_json(page.get("hreflang") or []),
        redirect_chain_json=_canonical_json(page.get("redirect_chain") or []),
        internal_links_json=_canonical_json(page.get("internal_links") or []),
        error=str(page.get("error")) if page.get("error") is not None else None,
        dataset_coverage=dataset_coverage,
        freshness="provisional",
        comparability="comparable",
    )


def _crawl_stale_current_pages(
    storage: SEOStorage,
    *,
    project_id: str,
    confirmed_urls: list[str],
) -> list[str]:
    params: list[Any] = [project_id]
    exclusion = ""
    if confirmed_urls:
        placeholders = ",".join("?" for _ in confirmed_urls)
        exclusion = f"AND crawled_url NOT IN ({placeholders})"
        params.extend(confirmed_urls)
    rows = storage.fetchall(
        f"""
        SELECT crawled_url
        FROM crawl_pages
        WHERE project_id = ?
          AND is_current = 1
          {exclusion}
        ORDER BY crawled_url
        """,
        tuple(params),
    )
    return [str(row["crawled_url"]) for row in rows]


def _crawl_known_current_pages(storage: SEOStorage, *, project_id: str) -> list[dict[str, str]]:
    rows = storage.fetchall(
        """
        SELECT DISTINCT property_id, page_url
        FROM search_performance
        WHERE project_id = ?
          AND is_current = 1
          AND segment_id NOT IN ('total', 'brand')
        ORDER BY page_url
        """,
        (project_id,),
    )
    known: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        url = str(row["page_url"] or "")
        if not url:
            continue
        normalized = normalize_crawl_url(url)
        key = (str(row["property_id"] or "__all__"), normalized)
        if key in seen:
            continue
        seen.add(key)
        known.append({"property_id": key[0], "url": normalized, "source": "search_performance"})
    return known


def _crawl_reachable_urls(observations: list[CrawlPageObservation]) -> set[tuple[str, str]]:
    reachable: set[tuple[str, str]] = set()
    for obs in observations:
        property_id = obs.property_id or "__all__"
        for url in (obs.crawled_url, obs.final_url):
            if url:
                reachable.add((property_id, normalize_crawl_url(url)))
        for link in _json_list(obs.internal_links_json):
            if isinstance(link, str) and link:
                reachable.add((property_id, normalize_crawl_url(link)))
    return reachable


def _crawl_orphan_findings(
    known_pages: list[dict[str, str]],
    *,
    reachable_urls: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in known_pages:
        url = row["url"]
        if (row["property_id"], url) in reachable_urls:
            continue
        findings.append(
            {
                "type": "crawl_orphan_page",
                "url": url,
                "property_id": row["property_id"],
                "severity": "high",
                "reason": "Known URL from search_performance was not reached by the latest crawl graph.",
                "evidence": {"sources": [row["source"]]},
            }
        )
    return findings


def _json_list(value: str) -> list[Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return decoded if isinstance(decoded, list) else []


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(float(value))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


_COVERAGE_WORST_FIRST = (
    "unavailable", "unknown", "truncated", "privacy_thresholded",
    "empty", "top_rows", "partial", "available", "complete",
)
_FRESHNESS_WORST_FIRST = ("stale", "incomplete", "local", "provisional", "final")


def _worst_ranked(values: list[str], order: tuple[str, ...]) -> str | None:
    if not values:
        return None
    ranks = {value: index for index, value in enumerate(order)}

    def rank(value: str) -> int:
        return ranks.get(value, 0)

    return min(values, key=rank)


def _merge_source_metadata(*items: dict[str, Any]) -> dict[str, Any]:
    """Combine per-part source metadata conservatively.

    Coverage and freshness keep the worst value seen in any part so a
    truncated or provisional sub-report is never masked by a complete one.
    Row counts are summed; all other keys keep the first part's values.
    """
    merged: dict[str, Any] = {}
    for item in items:
        for key, value in item.items():
            if key not in merged or merged.get(key) is None:
                merged[key] = value
    coverages = [str(item["dataset_coverage"]) for item in items if item.get("dataset_coverage")]
    freshness = [str(item["freshness"]) for item in items if item.get("freshness")]
    coverage = _worst_ranked(coverages, _COVERAGE_WORST_FIRST)
    fresh = _worst_ranked(freshness, _FRESHNESS_WORST_FIRST)
    if coverage is not None:
        merged["dataset_coverage"] = coverage
    if fresh is not None:
        merged["freshness"] = fresh
    merged["rows_received"] = sum(int(item.get("rows_received") or 0) for item in items)
    return merged


def _collect_period(args: argparse.Namespace, period_id: str) -> tuple[str, str]:
    start = getattr(args, "start", None)
    end = getattr(args, "end", None)
    if start or end:
        if not start or not end:
            raise ConfigError(
                "COLLECT_PERIOD_INVALID",
                "`--start` and `--end` must be supplied together.",
                {"start": start, "end": end},
            )
        try:
            start_date = dt.date.fromisoformat(str(start))
            end_date = dt.date.fromisoformat(str(end))
        except ValueError as exc:
            raise ConfigError(
                "COLLECT_PERIOD_INVALID",
                "`--start` and `--end` must be valid ISO dates.",
                {"start": start, "end": end},
            ) from exc
        if start_date > end_date:
            raise ConfigError(
                "COLLECT_PERIOD_INVALID",
                "`--start` must be earlier than or equal to `--end`.",
                {"start": start, "end": end},
            )
        return start_date.isoformat(), end_date.isoformat()
    today = dt.date.today()
    if period_id.endswith("d") and period_id[:-1].isdigit():
        days = int(period_id[:-1])
        return (today - dt.timedelta(days=days)).isoformat(), (today - dt.timedelta(days=1)).isoformat()
    raise ConfigError(
        "COLLECT_PERIOD_INVALID",
        "Collect period_id must use Nd format or explicit --start/--end.",
        {"period_id": period_id},
    )


def _collect_run_id(project: str, period_id: str, period_start: str, period_end: str, nonce: str) -> str:
    material = f"{project}:{period_id}:{period_start}:{period_end}:{nonce}"
    return f"collect:{project}:{period_id}:{_sha256_text(material)[:16]}"


def _weekly_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload = _snapshot_or_report_payload(args, render_report=True)
    if not payload["ok"]:
        return payload
    payload["command"] = "weekly"
    payload["weekly"] = {
        "schema_version": 1,
        "mode": "baseline_export",
        "status": "ready",
        "selected_baseline": Path(payload["baseline"]["path"]).name,
        "live_collection": False,
    }
    summary_path = Path(payload["artifacts"]["report_path"]).parent / "weekly-summary.json"
    summary = {
        "schema_version": 1,
        "command": "weekly",
        "project": payload["project"],
        "period_id": payload["period_id"],
        "baseline": payload["baseline"],
        "weekly": payload["weekly"],
        "source_status": payload["source_status"],
        "artifacts": {
            key: payload["artifacts"][key]
            for key in (
                "snapshot_path",
                "snapshot_sha256",
                "report_path",
                "report_sha256",
                "database_path",
            )
        },
    }
    summary["artifacts"]["database_disposition"] = "disposable_non_canonical"
    summary_text = _canonical_json(summary, indent=2) + "\n"
    summary_path.write_text(summary_text, encoding="utf-8")
    payload["artifacts"]["weekly_summary_path"] = str(summary_path)
    payload["artifacts"]["weekly_summary_sha256"] = _sha256_text(summary_text)
    return payload


def _select_baseline(config_dir: Path, baseline_date: str | None) -> Path | None:
    baselines_dir = config_dir / "baselines"
    if baseline_date:
        candidate = baselines_dir / baseline_date
        return candidate if candidate.is_dir() else None
    if not baselines_dir.is_dir():
        return None
    candidates = sorted(path for path in baselines_dir.iterdir() if path.is_dir())
    fallback = candidates[-1] if candidates else None
    for candidate in reversed(candidates):
        if _baseline_is_export_approved(candidate):
            return candidate
    return fallback


def _baseline_is_export_approved(path: Path) -> bool:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("privacy", {}).get("export_approved") is True


def _output_root(args: argparse.Namespace, project: str, baseline_name: str) -> Path:
    output_dir = getattr(args, "output_dir", None)
    if output_dir:
        return Path(str(output_dir)).expanduser()
    return observer_home() / "projects" / project / "exports" / "baselines" / baseline_name


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                "BASELINE_INVALID",
                "Baseline JSONL is invalid.",
                {"path": str(path), "line": line_number, "error": str(exc)},
            ) from exc
        if not isinstance(value, dict):
            raise ConfigError(
                "BASELINE_INVALID",
                "Baseline JSONL row must be an object.",
                {"path": str(path), "line": line_number},
            )
        rows.append(value)
    return rows


def _read_baseline_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(
            "BASELINE_INVALID",
            "Baseline manifest is invalid.",
            {"path": str(path), "error": str(exc)},
        ) from exc
    if not isinstance(manifest, dict):
        raise ConfigError(
            "BASELINE_INVALID",
            "Baseline manifest must be a JSON object.",
            {"path": str(path)},
        )
    return manifest


def _build_baseline_snapshot(
    *,
    config: Any,
    baseline: Path,
    baseline_manifest: dict[str, Any],
    observations_path: Path,
    rows: list[dict[str, Any]],
    period_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    skipped: dict[str, int] = {}
    search_rows: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("period_id")) != period_id:
            skipped["period_mismatch"] = skipped.get("period_mismatch", 0) + 1
            continue
        target = str(row.get("target_table") or "unknown")
        if target != "search_performance":
            skipped[target] = skipped.get(target, 0) + 1
            continue
        search_rows.append(row)
    if not search_rows:
        return None, {"input_rows": len(rows), "used_rows": 0, "skipped_rows": skipped}
    period_start = min(str(row["period_start"]) for row in search_rows)
    period_end = max(str(row["period_end"]) for row in search_rows)
    generated_at = str(
        baseline_manifest.get("collected_at_utc")
        or baseline_manifest.get("generated_at")
        or "1970-01-01T00:00:00Z"
    )
    evidence_rows = [
        _baseline_search_evidence_row(config, row, period_id, generated_at)
        for row in search_rows
    ]
    source_requests = [
        _baseline_source_request(row, period_id, observations_path)
        for row in search_rows
    ]
    source_coverage = _baseline_source_coverage(evidence_rows)
    totals = _baseline_search_totals(evidence_rows)
    snapshot: dict[str, Any] = {
        "snapshot_schema_version": 1,
        "project": {
            "project_id": config.project.namespace,
            "timezone": config.project.timezone,
            "config_hash": compute_config_hash(config),
            "config_schema_version": config.schema_version,
        },
        "period": {
            "reporting_period_id": period_id,
            "start": period_start,
            "end": period_end,
            "timezone": config.project.timezone,
        },
        "versions": {
            "cli": __version__,
            "storage_schema": 1,
            "snapshot_schema": 1,
            "renderer": "baseline-jsonl-renderer-v1",
        },
        "privacy": {
            "policy": {"allow_exact_local_business_values": False},
            "redactions": [],
        },
        "manifest": {
            "manifest_id": "",
            "snapshot_schema_version": 1,
            "project_id": config.project.namespace,
            "reporting_period_id": period_id,
            "period_start": period_start,
            "period_end": period_end,
            "timezone": config.project.timezone,
            "generated_at": generated_at,
            "source_coverage": source_coverage,
            "logical_evidence_descriptors": _baseline_descriptors(source_requests),
            "logical_evidence_ids": [row["evidence_id"] for row in evidence_rows],
            "local_attempt_receipt": source_requests,
            "snapshot_hash": "",
            "renderer_version": "baseline-jsonl-renderer-v1",
        },
        "coverage": {
            "search_performance": _baseline_coverage(evidence_rows),
            "outcome_metrics": {
                "row_count": 0,
                "dataset_coverage": {},
                "freshness": {},
                "comparability": {},
                "sampled_rows": 0,
            },
        },
        "formula_inputs": {
            "search_performance_totals": totals,
            "outcome_metric_inputs": [],
        },
        "lineage": {
            "collection_runs": [
                {
                    "run_id": f"baseline-{baseline.name}-{period_id}",
                    "project_id": config.project.namespace,
                    "period_start": period_start,
                    "period_end": period_end,
                    "timezone": config.project.timezone,
                    "started_at": generated_at,
                    "finished_at": generated_at,
                    "status": "partial",
                    "config_hash": compute_config_hash(config),
                    "cli_version": __version__,
                    "db_schema_version": 1,
                    "config_schema_version": config.schema_version,
                }
            ],
            "source_requests": source_requests,
            "raw_artifacts": [
                _baseline_raw_artifact(row, observations_path)
                for row in evidence_rows
            ],
        },
        "evidence": {
            "search_performance": evidence_rows,
            "outcome_metrics": [],
        },
    }
    snapshot_hash = _snapshot_hash(snapshot)
    snapshot["manifest"]["snapshot_hash"] = snapshot_hash
    snapshot["manifest"]["manifest_id"] = f"manifest:{config.project.namespace}:{period_id}:{snapshot_hash[:16]}"
    return snapshot, {
        "input_rows": len(rows),
        "used_rows": len(evidence_rows),
        "skipped_rows": skipped,
    }


def _baseline_search_evidence_row(
    config: Any,
    row: dict[str, Any],
    period_id: str,
    generated_at: str,
) -> dict[str, Any]:
    source = str(row.get("source") or "unknown")
    property_id = str(row.get("property_id") or "__all__")
    logical_key = str(row.get("logical_observation_key") or f"{source}:{property_id}:{period_id}")
    content_hash = _sha256_text(_canonical_json(row))
    page_url = str(row.get("page_url") or _property_url(config, property_id))
    source_timezone = str(row.get("timezone") or config.project.timezone)
    effective_instant_start, effective_instant_end = _effective_instants(
        str(row["period_start"]), str(row["period_end"]), source_timezone
    )
    return {
        "evidence_id": f"ev:search_performance:{content_hash[:16]}",
        "project_id": config.project.namespace,
        "property_id": property_id,
        "source": source,
        "effective_start": str(row["period_start"]),
        "effective_end": str(row["period_end"]),
        "source_timezone": source_timezone,
        "effective_instant_start": effective_instant_start,
        "effective_instant_end": effective_instant_end,
        "observed_at": generated_at,
        "reporting_period_id": period_id,
        "request_id": f"request:{content_hash[:16]}",
        "artifact_id": f"artifact:{content_hash[:16]}",
        "logical_observation_key": logical_key,
        "collection_attempt_key": f"attempt:{content_hash[:16]}",
        "query_id": str(row.get("query_id") or "__aggregate__"),
        "query_text": str(row.get("query_text") or "__aggregate__"),
        "page_id": str(row.get("page_id") or property_id),
        "page_url": page_url,
        "search_engine": _search_engine(source),
        "device": str(row.get("device") or "__all__"),
        "country": str(row.get("country") or "__all__"),
        "region": str(row.get("region") or "__all__"),
        "segment_id": str(row.get("segment_id") or "__all__"),
        "impressions": _baseline_required_count(row, "impressions"),
        "clicks": _baseline_required_count(row, "clicks"),
        "ctr": row.get("ctr"),
        "average_position": row.get("position", row.get("avg_position_unweighted")),
        "dataset_coverage": str(row.get("quality") or "available"),
        "sampled": bool(row.get("sampled", False)),
        "sample_share": row.get("sample_share"),
        "freshness": "local-only",
        "comparability": "not_comparable",
        "fact_schema_version": 1,
        "normalizer_version": "baseline-jsonl-v1",
        "content_hash": content_hash,
    }


def _write_baseline_storage(
    *,
    config: Any,
    snapshot: dict[str, Any],
    observations_path: Path,
    database_path: Path,
) -> Path:
    home = observer_home()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{database_path.name}.", suffix=".tmp", dir=database_path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    storage = SEOStorage(temporary_path, observer_home=home)
    try:
        _populate_baseline_storage(storage, config, snapshot, observations_path)
        os.replace(temporary_path, database_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return database_path


def _populate_baseline_storage(
    storage: SEOStorage,
    config: Any,
    snapshot: dict[str, Any],
    observations_path: Path,
) -> None:
    storage.upsert_project_config(config, config_hash=snapshot["project"]["config_hash"])
    run_row = snapshot["lineage"]["collection_runs"][0]
    run = CollectionRun(
        run_id=run_row["run_id"],
        project_id=run_row["project_id"],
        period_start=run_row["period_start"],
        period_end=run_row["period_end"],
        timezone=run_row["timezone"],
        started_at=run_row["started_at"],
        finished_at=run_row["finished_at"],
        status=run_row["status"],
        config_hash=run_row["config_hash"],
        cli_version=run_row["cli_version"],
        config_schema_version=run_row["config_schema_version"],
    )
    request_by_id = {request["request_id"]: request for request in snapshot["lineage"]["source_requests"]}
    artifact_text = observations_path.read_text(encoding="utf-8")
    for row in snapshot["evidence"]["search_performance"]:
        request_row = request_by_id[row["request_id"]]
        request = SourceRequest(
            request_id=request_row["request_id"],
            run_id=run.run_id,
            source=request_row["source"],
            property_id=request_row["property_id"],
            logical_observation_key=request_row["logical_observation_key"],
            collection_attempt_key=request_row["collection_attempt_key"],
            request_descriptor=request_row["request_descriptor"],
            attempt=request_row["attempt"],
            queried_at=run.started_at,
            completed_at=run.finished_at,
            transport_status=request_row["transport_status"],
            freshness="provisional",
            sampled=request_row["sampled"],
            sample_share=request_row["sample_share"],
            data_lag_seconds=request_row["data_lag_seconds"],
            row_limit=request_row["row_limit"],
            rows_received=request_row["rows_received"],
            pages_expected=request_row["pages_expected"],
            pages_received=request_row["pages_received"],
            error_code=request_row["error_code"],
            error_summary=request_row["error_summary"],
        )
        artifact = RawArtifact(
            artifact_id=row["artifact_id"],
            request_id=row["request_id"],
            relative_path=(
                f"baseline/{snapshot['manifest']['generated_at'][:10]}/"
                f"{row['artifact_id'].replace(':', '-')}.jsonl"
            ),
            sha256=_sha256_text(artifact_text),
            content_type="application/x-jsonlines",
            compression="none",
            redaction_state="redacted",
            byte_size=observations_path.stat().st_size,
        )
        observation = SearchPerformanceObservation(
            project_id=row["project_id"],
            property_id=row["property_id"],
            source=row["source"],
            effective_start=row["effective_start"],
            effective_end=row["effective_end"],
            source_timezone=row["source_timezone"],
            effective_instant_start=row["effective_instant_start"],
            effective_instant_end=row["effective_instant_end"],
            observed_at=row["observed_at"],
            reporting_period_id=row["reporting_period_id"],
            request_id=row["request_id"],
            artifact_id=row["artifact_id"],
            logical_observation_key=row["logical_observation_key"],
            collection_attempt_key=row["collection_attempt_key"],
            query_id=row["query_id"],
            query_text=row["query_text"],
            page_id=row["page_id"],
            page_url=row["page_url"],
            search_engine=row["search_engine"],
            impressions=row["impressions"],
            clicks=row["clicks"],
            ctr=float(row["ctr"] or 0),
            average_position=row["average_position"],
            dataset_coverage=_storage_dataset_coverage(row["dataset_coverage"]),
            freshness="provisional",
            comparability=_storage_comparability(row["comparability"]),
            device=row["device"],
            country=row["country"],
            region=row["region"],
            segment_id=row["segment_id"],
            sampled=row["sampled"],
            sample_share=row["sample_share"],
            fact_schema_version=row["fact_schema_version"],
            normalizer_version=row["normalizer_version"],
        )
        storage.ingest_search_performance(run, request, [artifact], [observation])


def _baseline_raw_artifact(row: dict[str, Any], observations_path: Path) -> dict[str, Any]:
    content_hash = row["content_hash"]
    artifact_text = _canonical_json(row)
    return {
        "artifact_id": row["artifact_id"],
        "request_id": row["request_id"],
        "relative_path": f"{observations_path.name}/{content_hash}.json",
        "sha256": _sha256_text(artifact_text),
        "content_type": "application/json",
        "compression": None,
        "redaction_state": "export-approved",
        "byte_size": len(artifact_text.encode("utf-8")),
    }


def _baseline_required_count(row: dict[str, Any], field: str) -> int:
    value = row.get(field)
    if value is None:
        raise ConfigError(
            "BASELINE_INVALID",
            "Baseline search_performance row is missing a required metric.",
            {"field": field, "logical_observation_key": row.get("logical_observation_key")},
        )
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "BASELINE_INVALID",
            "Baseline search_performance metric must be numeric.",
            {"field": field, "logical_observation_key": row.get("logical_observation_key")},
        ) from exc


def _effective_instants(period_start: str, period_end: str, source_timezone: str) -> tuple[str, str]:
    zone_name = source_timezone.removesuffix(" provider dates")
    try:
        zone = ZoneInfo(zone_name)
        start = dt.datetime.combine(dt.date.fromisoformat(period_start), dt.time.min, tzinfo=zone)
        end = dt.datetime.combine(dt.date.fromisoformat(period_end), dt.time(23, 59, 59), tzinfo=zone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ConfigError(
            "BASELINE_INVALID",
            "Baseline source timezone or period date is invalid.",
            {"source_timezone": source_timezone, "period_start": period_start, "period_end": period_end},
        ) from exc
    return (
        start.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        end.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _baseline_source_request(row: dict[str, Any], period_id: str, observations_path: Path) -> dict[str, Any]:
    content_hash = _sha256_text(_canonical_json(row))
    source = str(row.get("source") or "unknown")
    property_id = str(row.get("property_id") or "__all__")
    logical_key = str(row.get("logical_observation_key") or f"{source}:{property_id}:{period_id}")
    return {
        "request_id": f"request:{content_hash[:16]}",
        "run_id": f"baseline-jsonl:{period_id}",
        "source": source,
        "property_id": property_id,
        "logical_observation_key": logical_key,
        "collection_attempt_key": f"attempt:{content_hash[:16]}",
        "request_descriptor": {
            "baseline_jsonl": observations_path.name,
            "period_id": period_id,
            "source": source,
            "property_id": property_id,
        },
        "attempt": 1,
        "queried_at": None,
        "completed_at": None,
        "transport_status": "partial",
        "freshness": "local-only",
        "sampled": bool(row.get("sampled", False)),
        "sample_share": row.get("sample_share"),
        "data_lag_seconds": None,
        "row_limit": None,
        "rows_received": int(row.get("returned_rows") or row.get("query_count") or 1),
        "pages_expected": 1,
        "pages_received": 1,
        "error_code": None,
        "error_summary": None,
    }


def _baseline_source_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    for row in rows:
        source = row["source"]
        block = coverage.setdefault(
            source,
            {
                "current_evidence_rows": 0,
                "logical_observation_keys": [],
                "dataset_coverage": {},
                "freshness": {},
            },
        )
        block["current_evidence_rows"] += 1
        if row["logical_observation_key"] not in block["logical_observation_keys"]:
            block["logical_observation_keys"].append(row["logical_observation_key"])
        block["dataset_coverage"][row["dataset_coverage"]] = block["dataset_coverage"].get(row["dataset_coverage"], 0) + 1
        block["freshness"][row["freshness"]] = block["freshness"].get(row["freshness"], 0) + 1
    return coverage


def _baseline_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    block = {
        "row_count": len(rows),
        "dataset_coverage": {},
        "freshness": {},
        "comparability": {},
        "sampled_rows": 0,
    }
    for row in rows:
        for key in ("dataset_coverage", "freshness", "comparability"):
            value = row[key]
            block[key][value] = block[key].get(value, 0) + 1
        if row["sampled"]:
            block["sampled_rows"] += 1
    return block


def _baseline_descriptors(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    descriptors = []
    for request in requests:
        descriptor_hash = _sha256_text(_canonical_json(request["request_descriptor"]))
        descriptors.append(
            {
                "descriptor_id": (
                    f"desc:{request['source']}:{request['property_id']}:"
                    f"{request['logical_observation_key']}"
                ),
                "source": request["source"],
                "property_id": request["property_id"],
                "logical_observation_key": request["logical_observation_key"],
                "request_descriptor_hash": descriptor_hash,
            }
        )
    return descriptors


def _baseline_search_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clicks = sum(int(row["clicks"]) for row in rows)
    impressions = sum(int(row["impressions"]) for row in rows)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": (clicks / impressions) if impressions else None,
    }


def _render_baseline_report(snapshot: dict[str, Any]) -> str:
    totals = snapshot["formula_inputs"]["search_performance_totals"]
    lines = [
        f"# SEO snapshot: {snapshot['project']['project_id']} / {snapshot['period']['reporting_period_id']}",
        "",
        f"- Snapshot hash: `{snapshot['manifest']['snapshot_hash']}`",
        f"- Period: {snapshot['period']['start']}..{snapshot['period']['end']} ({snapshot['period']['timezone']})",
        f"- Search performance rows: {snapshot['coverage']['search_performance']['row_count']}",
        f"- Clicks: {totals['clicks']}",
        f"- Impressions: {totals['impressions']}",
        f"- CTR: {totals['ctr']}",
        "",
        "## Source Quality",
    ]
    for source, coverage in sorted(snapshot["manifest"]["source_coverage"].items()):
        lines.append(
            f"- {source}: {coverage['current_evidence_rows']} rows; "
            f"freshness={coverage['freshness']}; coverage={coverage['dataset_coverage']}"
        )
    lines.extend(
        [
            "",
            "## Limits",
            "- Source state: local-only baseline import.",
            "- Comparability: not_comparable until live provider windows are collected through the same protocol.",
            "- No live provider calls were made.",
        ]
    )
    return "\n".join(lines) + "\n"


def _property_url(config: Any, property_id: str) -> str:
    for prop in config.properties:
        if prop.id == property_id:
            return prop.url
    if config.properties:
        return config.properties[0].url
    return "__all__"


def _search_engine(source: str) -> str:
    if source == "google_search_console":
        return "google"
    if source == "yandex_webmaster":
        return "yandex"
    return "__all__"


def _storage_dataset_coverage(value: str) -> str:
    if value in {"complete", "truncated", "privacy_thresholded", "unknown", "unavailable"}:
        return value
    if value == "available":
        return "complete"
    if value in {"top_rows", "sampled", "partial"}:
        return "truncated"
    if value == "empty":
        return "complete"
    if value in {"missing", "disabled", "failed"}:
        return "unavailable"
    return "unknown"


def _storage_comparability(value: str) -> str:
    if value in {
        "comparable",
        "config_break",
        "protocol_break",
        "population_mismatch",
        "insufficient_history",
        "window_mismatch",
    }:
        return value
    if value in {"not_comparable", "local-only"}:
        return "insufficient_history"
    if value in {"incomplete_top_rows", "partial"}:
        return "protocol_break"
    return "protocol_break"


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    copy = json.loads(_canonical_json(snapshot))
    copy["manifest"]["generated_at"] = "<generated_at>"
    copy["manifest"]["snapshot_hash"] = ""
    copy["manifest"]["manifest_id"] = ""
    return _sha256_text(_canonical_json(copy))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), indent=indent)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler")
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
