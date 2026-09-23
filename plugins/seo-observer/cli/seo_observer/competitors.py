from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from seo_observer.budget import BudgetGuard
from seo_observer.config import CompetitorsConfig, ProjectConfig, compute_config_hash, observer_home
from seo_observer.content_gap import ContentGapOptions as CompetitorReportOptions
from seo_observer.content_gap import BriefAdapter, create_content_gap_report
from seo_observer.content_extract import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    StdlibPageTransport,
    extract_pages,
    render_content_report_section,
)
from seo_observer.dataforseo import DataForSEOAdapter, DataForSEOSource
from seo_observer.yandex_search import (
    DEFAULT_BASE_URL as DEFAULT_YANDEX_BASE_URL,
)
from seo_observer.yandex_search import YANDEX_SEARCH_ENDPOINT, YandexSerpProviderAdapter
from seo_observer.topvisor import (
    TOPVISOR_BASE_URL,
    TOPVISOR_SNAPSHOTS_ENDPOINT,
    PostJsonHttp,
    TopvisorClient,
    TopvisorCredentials,
    TopvisorSerpProviderAdapter,
    TopvisorSnapshotTransport,
)
from seo_observer.research import ExaHttpTransport, ExaResearchAdapter, FixtureResearchTransport, WebSearchAdapter
from seo_observer.report_rendering import write_polished_report_artifacts
from seo_observer.serp import (
    CompetitorConfig,
    SerpKeywordSet,
    SerpSource,
    classify_domain,
    derive_competitor_metrics,
    generate_protocol_slots,
    keyword_set_hash,
    normalized_host,
    normalized_keywords,
)


def _market_for_keyword_set(config: ProjectConfig, keyword_set: Any) -> Any | None:
    """The market the keyword set belongs to.

    Returns None for unmigrated projects (without a [[markets]] block) — in that
    case the previous DataForSEO behavior is preserved.
    """
    market_id = getattr(keyword_set, "market", None)
    if not market_id:
        return None
    for market in getattr(config, "markets", ()) or ():
        if market.id == market_id:
            return market
    return None


DEFAULT_MEGA_AUTHORITY_DOMAINS = (
    "wikipedia.org",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "vk.com",
    "dzen.ru",
    "t.me",
    "google.com",
    "yandex.ru",
)
DISCOVERY_COMMAND = "competitors.discover"
AUDIT_COMMAND = "competitors.audit"
RESEARCH_COMMAND = "competitors.research"
DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9][a-z0-9-]{1,61}$")


class CompetitorProviderAdapter(Protocol):
    def fetch_competitors_domain(
        self,
        target: str,
        location_code: int | str | None = None,
        language_code: str | None = None,
        limit: int | None = None,
        *,
        location_name: str | None = None,
        exclude_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        ...

    def fetch_domain_intersection(
        self,
        target1: str,
        target2: str,
        location_code: int | str | None = None,
        language_code: str | None = None,
        limit: int | None = None,
        *,
        location_name: str | None = None,
    ) -> dict[str, Any]:
        ...


@dataclasses.dataclass(frozen=True)
class CompetitorDiscoveryOptions:
    provider_mode: str = "artifact"
    output_dir: Path | None = None
    property_id: str | None = None
    target_domain: str | None = None
    limit_competitors: int = 20
    picked_competitors: int = 3
    gap_limit: int = 100
    allow_paid: bool = False


class SerpProviderAdapter(Protocol):
    """SERP collection seam. The method name is intentionally engine-neutral:
    the RU/Yandex and EN/Google markets implement the same contract and differ
    only in the adapter."""

    def fetch_organic_serp(
        self,
        keyword: str,
        location_code: int | str | None = None,
        location_name: str | None = None,
        language_code: str | None = None,
        device: str = "desktop",
        depth: int = 10,
    ) -> dict[str, Any]:
        ...


@dataclasses.dataclass(frozen=True)
class CompetitorAuditOptions:
    keyword_set_id: str
    start: str
    end: str
    provider_mode: str = "artifact"
    output_dir: Path | None = None
    allow_paid: bool = False
    baseline_artifact: Path | None = None
    serp_depth: int | None = None
    devices: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class CompetitorResearchOptions:
    keyword_set_id: str
    provider_mode: str = "artifact"
    output_dir: Path | None = None
    urls: tuple[str, ...] = ()
    allow_paid: bool = False
    confirmed_serp_artifact: Path | None = None
    max_pages: int = 20
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


def discover_competitors(
    config: ProjectConfig,
    options: CompetitorDiscoveryOptions,
    *,
    adapter: CompetitorProviderAdapter | None = None,
    budget_guard: BudgetGuard | None = None,
    env: dict[str, str] | None = None,
    transport_factory: Any | None = None,
) -> dict[str, Any]:
    provider_mode = options.provider_mode or "artifact"
    output_dir = options.output_dir or _default_output_dir(config.project.namespace)
    target_domain = _resolve_target_domain(config, options)
    source = config.sources.get("competitor_discovery")
    provider = config.providers.get("dataforseo")
    source_errors: list[dict[str, Any]] = []
    source_request_ids: list[str] = []
    total_cost = 0.0
    observed_at = _utc_now()
    budget = budget_guard or _budget_guard(config, options, planned_calls=max(1, options.picked_competitors + 1))
    known = _known_competitors(config.competitors, observed_at=observed_at)

    readiness = _readiness(source=source, provider=provider, env=env or {}, require_credentials=provider_mode == "live")
    if provider_mode == "live":
        if not options.allow_paid:
            return _write_result(
                config=config,
                options=options,
                output_dir=output_dir,
                target_domain=target_domain,
                known_competitors=known,
                candidate_competitors=[],
                picked_competitors=[],
                filtered_domains=[],
                keyword_gaps=[],
                source_requests=[],
                source_quality="unsupported",
                budget=budget.manifest(),
                source_request_ids=[],
                cost_usd=0.0,
                errors=[_budget_error("PAID_CALL_NOT_CONFIRMED", budget)],
                ok=False,
            )
        if not readiness["ok"]:
            return _write_result(
                config=config,
                options=options,
                output_dir=output_dir,
                target_domain=target_domain,
                known_competitors=known,
                candidate_competitors=[],
                picked_competitors=[],
                filtered_domains=[],
                keyword_gaps=[],
                source_requests=[],
                source_quality=readiness["quality"],
                budget=budget.manifest(),
                source_request_ids=[],
                cost_usd=0.0,
                errors=[readiness["error"]],
                ok=False,
            )
        preflight_error = budget.preflight()
        if preflight_error is not None:
            return _write_result(
                config=config,
                options=options,
                output_dir=output_dir,
                target_domain=target_domain,
                known_competitors=known,
                candidate_competitors=[],
                picked_competitors=[],
                filtered_domains=[],
                keyword_gaps=[],
                source_requests=[],
                source_quality="unsupported",
                budget=budget.manifest(),
                source_request_ids=[],
                cost_usd=0.0,
                errors=[preflight_error],
                ok=False,
            )
        if adapter is None:
            if transport_factory is None:
                raise ValueError("live competitor discovery requires a transport factory")
            assert provider is not None
            adapter = DataForSEOAdapter(
                DataForSEOSource(
                    credential_env=provider.credential_env,
                    endpoint=provider.endpoint or "https://api.dataforseo.com",
                    provider_mode="live",
                    allow_paid=True,
                    per_run_budget_usd=provider.per_run_budget_usd,
                    monthly_budget_usd=provider.monthly_budget_usd,
                ),
                transport_factory(provider.endpoint or "https://api.dataforseo.com"),
                env=env,
                budget_guard=budget,
            )

    if provider_mode == "artifact":
        cached = _read_cached_extract(output_dir)
        if cached is not None:
            cached["known_competitors"] = cached.get("known_competitors") or known
            return _write_result(
                config=config,
                options=options,
                output_dir=output_dir,
                target_domain=str(cached.get("target_domain") or target_domain),
                known_competitors=list(cached.get("known_competitors") or []),
                candidate_competitors=list(cached.get("candidate_competitors") or []),
                picked_competitors=list(cached.get("picked_competitors") or []),
                filtered_domains=list(cached.get("filtered_domains") or []),
                keyword_gaps=list(cached.get("keyword_gaps") or []),
                source_requests=list(cached.get("source_requests") or []),
                source_quality=str(cached.get("quality_summary", {}).get("overall") or "local-only"),
                budget=dict(cached.get("budget") or budget.manifest()),
                source_request_ids=_source_request_ids(cached.get("source_requests") or []),
                cost_usd=0.0,
                errors=[],
                ok=True,
            )
        if not readiness["ok"]:
            source_errors.append(readiness["error"])
            quality = readiness["quality"]
            return _write_result(
                config=config,
                options=options,
                output_dir=output_dir,
                target_domain=target_domain,
                known_competitors=known,
                candidate_competitors=[],
                picked_competitors=[],
                filtered_domains=[],
                keyword_gaps=[],
                source_requests=[],
                source_quality=quality,
                budget=budget.manifest(),
                source_request_ids=[],
                cost_usd=0.0,
                errors=source_errors,
                ok=not bool(source and source.required),
            )
        return _write_result(
            config=config,
            options=options,
            output_dir=output_dir,
            target_domain=target_domain,
            known_competitors=known,
            candidate_competitors=[],
            picked_competitors=[],
            filtered_domains=[],
            keyword_gaps=[],
            source_requests=[],
            source_quality="local-only",
            budget=budget.manifest(),
            source_request_ids=[],
            cost_usd=0.0,
            errors=[],
            ok=True,
        )

    if provider_mode not in {"fixture", "live"}:
        return _write_result(
            config=config,
            options=options,
            output_dir=output_dir,
            target_domain=target_domain,
            known_competitors=known,
            candidate_competitors=[],
            picked_competitors=[],
            filtered_domains=[],
            keyword_gaps=[],
            source_requests=[],
            source_quality="unsupported",
            budget=budget.manifest(),
            source_request_ids=[],
            cost_usd=0.0,
            errors=[_error("PROVIDER_MODE_INVALID", f"Unsupported provider mode: {provider_mode}", {"provider_mode": provider_mode})],
            ok=False,
        )

    if adapter is None:
        return _write_result(
            config=config,
            options=options,
            output_dir=output_dir,
            target_domain=target_domain,
            known_competitors=known,
            candidate_competitors=[],
            picked_competitors=[],
            filtered_domains=[],
            keyword_gaps=[],
            source_requests=[],
            source_quality="unsupported",
            budget=budget.manifest(),
            source_request_ids=[],
            cost_usd=0.0,
            errors=[_error("COMPETITOR_FIXTURE_MISSING", "Fixture mode requires an injected/local fixture adapter.", {})],
            ok=False,
        )

    candidate_result = adapter.fetch_competitors_domain(
        target_domain,
        config.default_location_code,
        config.default_language_code,
        options.limit_competitors,
        location_name=config.default_location_name,
        exclude_domains=list(config.competitors.owned_domains),
    )
    source_requests = [_source_request("competitors_domain", candidate_result, observed_at)]
    source_request_ids.extend(_provider_request_ids(candidate_result))
    total_cost += _provider_cost(candidate_result)
    source_errors.extend(_result_errors(candidate_result))
    raw_candidates = list(candidate_result.get("rows") or [])
    configured_domains = _configured_domains(config.competitors)
    for configured_domain in configured_domains:
        if not any(_canonical_domain(row.get("domain")) == configured_domain for row in raw_candidates):
            continue
        for row in raw_candidates:
            if _canonical_domain(row.get("domain")) == configured_domain:
                row["configured_class"] = _configured_class(config.competitors, configured_domain)
    candidate_rows, filtered = filter_candidate_competitors(
        raw_candidates,
        target_domain=target_domain,
        owned_domains=config.competitors.owned_domains,
        mega_authority_domains=_mega_authority_domains(config),
    )
    picked = pick_competitors(candidate_rows, config.competitors, limit=options.picked_competitors)
    gap_input: list[dict[str, Any]] = []
    for picked_row in picked:
        domain = str(picked_row["domain"])
        gap_result = adapter.fetch_domain_intersection(
            target_domain,
            domain,
            config.default_location_code,
            config.default_language_code,
            options.gap_limit,
            location_name=config.default_location_name,
        )
        source_requests.append(_source_request(f"domain_intersection:{domain}", gap_result, observed_at))
        source_request_ids.extend(_provider_request_ids(gap_result))
        total_cost += _provider_cost(gap_result)
        source_errors.extend(_result_errors(gap_result))
        for index, row in enumerate(gap_result.get("rows") or []):
            item = dict(row)
            item.setdefault("competitor_domain", domain)
            item.setdefault("location_code", config.default_location_code)
            item.setdefault("location_name", config.default_location_name)
            item.setdefault("language_code", config.default_language_code)
            item.setdefault("source_row_id", f"{domain}:gap:{index}")
            gap_input.append(item)
    gaps = merge_keyword_gaps(gap_input, gap_limit=options.gap_limit)
    source_quality = _aggregate_quality(provider_mode, candidate_result, source_errors)
    return _write_result(
        config=config,
        options=options,
        output_dir=output_dir,
        target_domain=target_domain,
        known_competitors=known,
        candidate_competitors=candidate_rows,
        picked_competitors=picked,
        filtered_domains=filtered,
        keyword_gaps=gaps,
        source_requests=source_requests,
        source_quality=source_quality,
        budget=budget.manifest(),
        source_request_ids=source_request_ids,
        cost_usd=total_cost,
        errors=source_errors,
        ok=False if provider_mode == "live" and source_errors else not bool(source_errors and source and source.required),
    )



def _topvisor_audit_adapter(
    market: Any,
    *,
    config: ProjectConfig,
    env: dict[str, str] | None,
    transport_factory: Any | None,
) -> TopvisorSerpProviderAdapter:
    """Builds the Topvisor adapter from a market: two auth values and a date.

    Topvisor requires an "account id + key" pair rather than a single token, so
    the market declares `user_id_env` alongside `credential_env`. A missing one
    is an error naming the variable: without it, a "failed" message gives no hint
    about what exactly to report.
    """

    if transport_factory is None:
        raise ValueError("live competitor audit requires a transport factory")
    fields = getattr(market, "fields", {}) or {}
    resolved = dict(env or os.environ)
    user_id_env = fields.get("user_id_env") or "TOPVISOR_USER_ID"
    api_key = resolved.get(market.credential_env or "", "")
    user_id = resolved.get(str(user_id_env), "")
    for name, value in ((market.credential_env, api_key), (user_id_env, user_id)):
        if not value:
            raise ValueError(
                f"market {market.id!r} requires {name} to read Topvisor snapshots"
            )
    project_id = fields.get("project_id")
    if not project_id:
        raise ValueError(
            f"market {market.id!r} requires project_id: Topvisor stores keywords "
            "in a persistent project, and creating one per run would lose history"
        )
    snapshot_date = str(
        fields.get("snapshot_date")
        or dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    )
    client = TopvisorClient(
        PostJsonHttp(transport_factory(TOPVISOR_BASE_URL)),
        TopvisorCredentials(user_id=str(user_id), api_key=str(api_key)),
    )
    return TopvisorSerpProviderAdapter(
        TopvisorSnapshotTransport(
            client=client,
            project_id=str(project_id),
            date=snapshot_date,
            region_lang=market.language or "ru",
        ),
        search_engine=market.search_engine or "google",
        language_code=market.language or "ru",
    )


def audit_competitors(
    config: ProjectConfig,
    options: CompetitorAuditOptions,
    *,
    adapter: SerpProviderAdapter | None = None,
    budget_guard: BudgetGuard | None = None,
    env: dict[str, str] | None = None,
    transport_factory: Any | None = None,
    storage: Any | None = None,
) -> dict[str, Any]:
    provider_mode = options.provider_mode or "artifact"
    output_dir = options.output_dir or _default_output_dir(config.project.namespace)
    observed_at = _utc_now()
    keyword_set = _resolve_audit_keyword_set(config, options)
    source = _audit_serp_source(config, keyword_set, options)
    slots = generate_protocol_slots(keyword_set, source)
    budget = budget_guard or _audit_budget_guard(config, options, planned_calls=len(slots))
    errors: list[dict[str, Any]] = []
    source_requests: list[dict[str, Any]] = []
    source_request_ids: list[str] = []
    total_cost = 0.0
    quality = "local-only"
    observations: list[dict[str, Any]] = []

    if provider_mode == "artifact":
        cached = _read_cached_serp_extract(output_dir)
        if cached is not None and _cached_serp_extract_matches(
            cached,
            keyword_set=keyword_set,
            protocol_hashes=[slot.protocol_hash for slot in slots],
        ):
            observations = [dict(row) for row in cached.get("serp_rows") or [] if isinstance(row, dict)]
        else:
            cached = None
        return _write_audit_result(
            config=config,
            options=options,
            output_dir=output_dir,
            keyword_set=keyword_set,
            expected_slots=slots,
            observations=observations,
            source_requests=list(cached.get("source_requests") or []) if cached else [],
            source_quality=str(cached.get("quality_summary", {}).get("overall") or "local-only") if cached else "local-only",
            budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
            source_request_ids=_source_request_ids(cached.get("source_requests") or []) if cached else [],
            cost_usd=_source_requests_cost(cached.get("source_requests") or []) if cached else 0.0,
            errors=[],
            ok=True,
            baseline=_select_audit_baseline(options, storage=storage, protocol_hashes=[slot.protocol_hash for slot in slots]),
        )

    if provider_mode not in {"fixture", "live"}:
        return _write_audit_result(
            config=config,
            options=options,
            output_dir=output_dir,
            keyword_set=keyword_set,
            expected_slots=slots,
            observations=[],
            source_requests=[],
            source_quality="unsupported",
            budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
            source_request_ids=[],
            cost_usd=0.0,
            errors=[_error("PROVIDER_MODE_INVALID", f"Unsupported provider mode: {provider_mode}", {"provider_mode": provider_mode})],
            ok=False,
            baseline={"snapshot": None, "source": None, "reason": "not_found"},
        )

    if provider_mode == "live":
        readiness = _serp_readiness(
            config, env=env or {}, market=_market_for_keyword_set(config, keyword_set)
        )
        if not options.allow_paid:
            return _write_audit_result(
                config=config,
                options=options,
                output_dir=output_dir,
                keyword_set=keyword_set,
                expected_slots=slots,
                observations=[],
                source_requests=[],
                source_quality="unsupported",
                budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
                source_request_ids=[],
                cost_usd=0.0,
                errors=[_audit_budget_error("PAID_CALL_NOT_CONFIRMED", budget)],
                ok=False,
                baseline={"snapshot": None, "source": None, "reason": "not_found"},
            )
        if not readiness["ok"]:
            return _write_audit_result(
                config=config,
                options=options,
                output_dir=output_dir,
                keyword_set=keyword_set,
                expected_slots=slots,
                observations=[],
                source_requests=[],
                source_quality=readiness["quality"],
                budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
                source_request_ids=[],
                cost_usd=0.0,
                errors=[readiness["error"]],
                ok=False,
                baseline={"snapshot": None, "source": None, "reason": "not_found"},
            )
        preflight_error = budget.preflight()
        if preflight_error is not None:
            return _write_audit_result(
                config=config,
                options=options,
                output_dir=output_dir,
                keyword_set=keyword_set,
                expected_slots=slots,
                observations=[],
                source_requests=[],
                source_quality="unsupported",
                budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
                source_request_ids=[],
                cost_usd=0.0,
                errors=[preflight_error],
                ok=False,
                baseline={"snapshot": None, "source": None, "reason": "not_found"},
            )
        if adapter is None:
            market = _market_for_keyword_set(config, keyword_set)
            if market is not None and market.search_engine == "yandex":
                if transport_factory is None:
                    raise ValueError("live competitor audit requires a transport factory")
                token = (env or {}).get(market.credential_env) or os.environ.get(
                    market.credential_env, ""
                )
                if not token:
                    raise ValueError(
                        f"market {market.id!r} requires {market.credential_env} to collect Yandex SERP"
                    )
                adapter = YandexSerpProviderAdapter(
                    transport_factory(DEFAULT_YANDEX_BASE_URL),
                    token=token,
                    language_code=market.language,
                )
            elif market is not None and market.provider.startswith("topvisor_"):
                # Topvisor is read from an already-taken snapshot: it scans the
                # whole project at once, so triggering a check from the
                # per-keyword loop would mean a paid run per keyword.
                adapter = _topvisor_audit_adapter(
                    market, config=config, env=env, transport_factory=transport_factory
                )
            else:
                provider = config.providers.get("dataforseo")
                if transport_factory is None or provider is None:
                    raise ValueError("live competitor audit requires a transport factory")
                adapter = DataForSEOAdapter(
                    DataForSEOSource(
                        credential_env=provider.credential_env,
                        endpoint=provider.endpoint or "https://api.dataforseo.com",
                        provider_mode="live",
                        allow_paid=True,
                        per_run_budget_usd=provider.per_run_budget_usd,
                        monthly_budget_usd=provider.monthly_budget_usd,
                    ),
                    transport_factory(provider.endpoint or "https://api.dataforseo.com"),
                    env=env,
                    budget_guard=budget,
                )

    if adapter is None:
        return _write_audit_result(
            config=config,
            options=options,
            output_dir=output_dir,
            keyword_set=keyword_set,
            expected_slots=slots,
            observations=[],
            source_requests=[],
            source_quality="unsupported",
            budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
            source_request_ids=[],
            cost_usd=0.0,
            errors=[_error("SERP_FIXTURE_MISSING", "Fixture mode requires an injected/local fixture adapter.", {})],
            ok=False,
            baseline={"snapshot": None, "source": None, "reason": "not_found"},
        )

    for slot in slots:
        result = adapter.fetch_organic_serp(
            slot.keyword,
            slot.region_id,
            _dataforseo_location_name(source, slot),
            slot.language,
            slot.device,
            slot.depth,
        )
        source_requests.append(_source_request(f"google_organic_serp:{slot.keyword}:{slot.device}", result, observed_at))
        source_request_ids.extend(_provider_request_ids(result))
        total_cost += _provider_cost(result)
        errors.extend(_result_errors(result))
        quality = _aggregate_quality(provider_mode, result, errors)
        observations.extend(_normalize_serp_result_rows(result.get("rows") or [], slot, config.competitor_config, observed_at, result))

    return _write_audit_result(
        config=config,
        options=options,
        output_dir=output_dir,
        keyword_set=keyword_set,
        expected_slots=slots,
        observations=observations,
        source_requests=source_requests,
        source_quality=quality,
        budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
        source_request_ids=source_request_ids,
        cost_usd=total_cost,
        errors=errors,
        ok=False if provider_mode == "live" and errors else not bool(errors and config.sources.get("serp") and config.sources["serp"].required),
        baseline=_select_audit_baseline(options, storage=storage, protocol_hashes=[slot.protocol_hash for slot in slots]),
    )


def research_competitors(
    config: ProjectConfig,
    options: CompetitorResearchOptions,
    *,
    research_adapter: WebSearchAdapter | None = None,
    page_transport: Any | None = None,
    budget_guard: BudgetGuard | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    provider_mode = options.provider_mode or "artifact"
    output_dir = options.output_dir or _default_output_dir(config.project.namespace)
    keyword_set = _resolve_research_keyword_set(config, options)
    observed_at = _utc_now()
    budget = budget_guard or _research_budget_guard(config, options, planned_calls=0 if options.urls else 1)
    source_request_ids: list[str] = []
    source_requests: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    cost_usd = 0.0
    research_rows: list[dict[str, Any]] = []
    page_extracts: list[dict[str, Any]] = []
    source_quality = "local-only"

    if provider_mode == "artifact":
        cached_research = _read_research_extract(output_dir)
        cached_content = _read_content_extract(output_dir)
        if cached_research is not None:
            research_rows = [dict(row) for row in cached_research.get("research_rows") or [] if isinstance(row, dict)]
            source_requests = [dict(row) for row in cached_research.get("source_requests") or [] if isinstance(row, dict)]
            source_request_ids = _source_request_ids(source_requests)
        if cached_content is not None:
            page_extracts = [dict(row) for row in cached_content.get("page_extracts") or [] if isinstance(row, dict)]
        return _write_research_result(
            config=config,
            options=options,
            output_dir=output_dir,
            keyword_set=keyword_set,
            research_rows=research_rows,
            page_extracts=page_extracts,
            source_requests=source_requests,
            source_quality="local-only",
            budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
            source_request_ids=source_request_ids,
            cost_usd=0.0,
            errors=[],
            ok=True,
            observed_at=observed_at,
        )

    if provider_mode not in {"fixture", "live"}:
        return _write_research_result(
            config=config,
            options=options,
            output_dir=output_dir,
            keyword_set=keyword_set,
            research_rows=[],
            page_extracts=[],
            source_requests=[],
            source_quality="unsupported",
            budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
            source_request_ids=[],
            cost_usd=0.0,
            errors=[
                _research_error(
                    "PROVIDER_MODE_INVALID",
                    f"Unsupported provider mode: {provider_mode}",
                    {"provider_mode": provider_mode},
                    provider=_research_provider_name(config),
                )
            ],
            ok=False,
            observed_at=observed_at,
        )

    if provider_mode == "live":
        readiness = _research_readiness(config, env=env or {})
        if not options.allow_paid:
            return _write_research_result(
                config=config,
                options=options,
                output_dir=output_dir,
                keyword_set=keyword_set,
                research_rows=[],
                page_extracts=[],
                source_requests=[],
                source_quality="unsupported",
                budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
                source_request_ids=[],
                cost_usd=0.0,
                errors=[_research_budget_error("PAID_CALL_NOT_CONFIRMED", budget)],
                ok=False,
                observed_at=observed_at,
            )
        if not readiness["ok"]:
            errors.append(readiness["error"])
            return _write_research_result(
                config=config,
                options=options,
                output_dir=output_dir,
                keyword_set=keyword_set,
                research_rows=[],
                page_extracts=[],
                source_requests=[],
                source_quality=readiness["quality"],
                budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
                source_request_ids=[],
                cost_usd=0.0,
                errors=errors,
                ok=False,
                observed_at=observed_at,
            )
        preflight_error = budget.preflight()
        if preflight_error is not None:
            return _write_research_result(
                config=config,
                options=options,
                output_dir=output_dir,
                keyword_set=keyword_set,
                research_rows=[],
                page_extracts=[],
                source_requests=[],
                source_quality="unsupported",
                budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
                source_request_ids=[],
                cost_usd=0.0,
                errors=[preflight_error],
                ok=False,
                observed_at=observed_at,
            )
        if research_adapter is None:
            provider_name = _research_provider_name(config)
            provider = config.providers.get(provider_name or "")
            if provider_name == "exa" and provider is not None:
                research_adapter = ExaResearchAdapter(
                    ExaHttpTransport(
                        provider.endpoint or "https://api.exa.ai",
                        (env or {}).get(provider.credential_env, ""),
                        timeout_seconds=options.timeout_seconds,
                    ),
                    observed_at=observed_at,
                )
            else:
                raise ValueError("live competitor research requires an injected research adapter")

    if provider_mode == "fixture" and research_adapter is None and not options.urls:
        fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "research" / "exa_search_normal.json"
        if fixture.is_file():
            research_adapter = ExaResearchAdapter(FixtureResearchTransport(fixture), observed_at=observed_at)

    if not options.urls and research_adapter is not None:
        query = _research_query(keyword_set)
        result = research_adapter.search(
            query=query,
            num_results=max(1, min(int(options.max_pages), 100)),
            include_domains=[],
            exclude_domains=list(config.competitors.owned_domains),
            contents={"highlights": True},
            structured_output={},
        )
        research_rows = list(result.get("rows") or [])
        errors.extend(_result_errors(result))
        source_request_ids.extend(_provider_request_ids(result))
        source_requests.append(_research_source_request("research_search", result, observed_at))
        cost_usd += _provider_cost(result)
        source_quality = _aggregate_research_quality(result, errors)
    elif options.urls:
        source_quality = "research-only"
    else:
        errors.append(
            _research_error(
                "RESEARCH_FIXTURE_MISSING",
                "Fixture mode requires injected/local research fixtures or --urls.",
                {},
                provider=_research_provider_name(config),
            )
        )
        source_quality = "unsupported"

    urls = list(options.urls) if options.urls else [str(row.get("url")) for row in research_rows if row.get("url")]
    urls = urls[: max(0, int(options.max_pages))]
    if urls:
        content = extract_pages(
            urls,
            transport=page_transport or StdlibPageTransport(),
            observed_at=observed_at,
            timeout_seconds=options.timeout_seconds,
            max_response_bytes=options.max_response_bytes,
            rate_limit_seconds=1.0 if provider_mode == "live" else 0.0,
        )
        page_extracts = list(content.get("page_extracts") or [])
        errors.extend([dict(item) for item in content.get("errors") or [] if isinstance(item, dict)])
        if content.get("quality_summary", {}).get("overall") == "partial" and source_quality == "research-only":
            source_quality = "partial"

    if errors and source_quality == "research-only":
        source_quality = "partial"
    return _write_research_result(
        config=config,
        options=options,
        output_dir=output_dir,
        keyword_set=keyword_set,
        research_rows=research_rows,
        page_extracts=page_extracts,
        source_requests=source_requests,
        source_quality=source_quality,
        budget={**budget.manifest(), "provider_mode": provider_mode, "allow_paid": options.allow_paid},
        source_request_ids=source_request_ids,
        cost_usd=cost_usd,
        errors=errors,
        ok=_research_result_ok(config, provider_mode=provider_mode, errors=errors, research_rows=research_rows),
        observed_at=observed_at,
    )


def build_competitor_report(
    config: ProjectConfig,
    options: CompetitorReportOptions,
    *,
    brief_adapter: BriefAdapter | None = None,
) -> dict[str, Any]:
    return create_content_gap_report(config, options, brief_adapter=brief_adapter)


def filter_candidate_competitors(
    candidates: list[dict[str, Any]],
    *,
    target_domain: str,
    owned_domains: tuple[str, ...] | list[str],
    mega_authority_domains: tuple[str, ...] | list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    target = _canonical_domain(target_domain)
    owned = tuple(_canonical_pattern(pattern) for pattern in owned_domains)
    mega = {_canonical_domain(domain) for domain in mega_authority_domains}
    kept: list[dict[str, Any]] = []
    filtered: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in candidates:
        raw_domain = str(row.get("domain") or "")
        domain = _canonical_domain(raw_domain)
        if domain is None:
            filtered.append({"domain": raw_domain, "reason": "malformed"})
            continue
        reason = None
        if domain == target:
            reason = "target_domain"
        elif _matches_any_pattern(domain, owned):
            reason = "owned"
        elif domain in mega:
            reason = "mega_authority"
        elif domain in seen:
            reason = "duplicate"
        if reason is not None:
            filtered.append({"domain": domain, "reason": reason})
            continue
        seen.add(domain)
        kept.append({**row, "domain": domain, "quality": row.get("quality") or "live"})
    return kept, filtered


def pick_competitors(
    candidates: list[dict[str, Any]],
    competitors_config: CompetitorsConfig,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    direct_domains = {
        domain
        for item in competitors_config.items
        if item.competitor_class == "direct"
        for domain in _entry_domains(item.domain_patterns)
    }
    rows = []
    for row in candidates:
        domain = _canonical_domain(row.get("domain"))
        if domain is None:
            continue
        is_direct = domain in direct_domains or row.get("configured_class") == "direct"
        rows.append(
            {
                **row,
                "domain": domain,
                "selection_reason": "configured_direct" if is_direct else "market_overlap",
                "_sort": (
                    0 if is_direct else 1,
                    -_int(row.get("intersections")),
                    -_int(row.get("organic_keywords")),
                    -_float(row.get("estimated_traffic")),
                    domain,
                ),
            }
        )
    rows.sort(key=lambda item: item["_sort"])
    picked = []
    for row in rows[: max(limit, 0)]:
        clean = dict(row)
        clean.pop("_sort", None)
        picked.append(clean)
    return picked


def merge_keyword_gaps(rows: list[dict[str, Any]], *, gap_limit: int) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if bool(row.get("our_domain_present")):
            continue
        keyword = str(row.get("keyword") or "").strip()
        if not keyword:
            continue
        normalized = _normalize_keyword(keyword)
        location = str(row.get("location_code") or row.get("location_name") or "")
        language = str(row.get("language_code") or "")
        key = (normalized, location, language)
        domain = _canonical_domain(row.get("competitor_domain"))
        if domain is None:
            continue
        citation_id = str(row.get("source_row_id") or row.get("citation_id") or f"{domain}:{normalized}")
        rank = _optional_int(row.get("competitor_rank"))
        diff = _optional_float(row.get("difficulty", row.get("keyword_difficulty")))
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "keyword": keyword,
                "normalized_keyword": normalized,
                "location_code_or_name": location,
                "language_code": language,
                "competitor_domains": [domain],
                "best_competitor_rank": rank,
                "representative_competitor_url": row.get("competitor_url"),
                "search_volume": _optional_int(row.get("search_volume")) or 0,
                "cpc": _float(row.get("cpc")),
                "difficulty": diff,
                "our_domain_present": False,
                "quality": row.get("quality") or "live",
                "citation_ids": [citation_id],
            }
            continue
        if domain not in current["competitor_domains"]:
            current["competitor_domains"].append(domain)
        if citation_id not in current["citation_ids"]:
            current["citation_ids"].append(citation_id)
        if rank is not None and (
            current["best_competitor_rank"] is None or rank < current["best_competitor_rank"]
        ):
            current["best_competitor_rank"] = rank
            current["representative_competitor_url"] = row.get("competitor_url")
        current["search_volume"] = max(int(current["search_volume"]), _optional_int(row.get("search_volume")) or 0)
        current["cpc"] = max(float(current["cpc"]), _float(row.get("cpc")))
        if diff is not None:
            if current.get("difficulty") is None:
                current["difficulty"] = diff
            else:
                current["difficulty"] = max(float(current["difficulty"]), diff)
        current["competitor_domains"] = sorted(current["competitor_domains"])
        current["citation_ids"] = sorted(current["citation_ids"])
    output = list(merged.values())
    output.sort(key=lambda item: (-len(item["competitor_domains"]), -int(item["search_volume"]), item["normalized_keyword"]))
    return output[: max(gap_limit, 0)]


def _write_result(
    *,
    config: ProjectConfig,
    options: CompetitorDiscoveryOptions,
    output_dir: Path,
    target_domain: str,
    known_competitors: list[dict[str, Any]],
    candidate_competitors: list[dict[str, Any]],
    picked_competitors: list[dict[str, Any]],
    filtered_domains: list[dict[str, str]],
    keyword_gaps: list[dict[str, Any]],
    source_requests: list[dict[str, Any]],
    source_quality: str,
    budget: dict[str, Any],
    source_request_ids: list[str],
    cost_usd: float,
    errors: list[dict[str, Any]],
    ok: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    budget = {**budget, "provider_mode": options.provider_mode, "allow_paid": options.allow_paid}
    extract = {
        "schema": "seo-observer.competitor_extract.v1",
        "target_domain": target_domain,
        "known_competitors": known_competitors,
        "candidate_competitors": candidate_competitors,
        "picked_competitors": picked_competitors,
        "filtered_domains": filtered_domains,
        "keyword_gaps": keyword_gaps,
        "source_requests": source_requests,
        "quality_summary": {
            "overall": source_quality,
            "candidate_count": len(candidate_competitors),
            "picked_count": len(picked_competitors),
            "keyword_gap_count": len(keyword_gaps),
        },
        "budget": budget,
    }
    extract_path = output_dir / "competitor-extract.json"
    report_path = output_dir / "competitor-report.md"
    extract_text = _json_text(extract)
    report_text = _render_report(
        config=config,
        target_domain=target_domain,
        source_quality=source_quality,
        cost_usd=cost_usd,
        known_competitors=known_competitors,
        candidate_competitors=candidate_competitors,
        picked_competitors=picked_competitors,
        filtered_domains=filtered_domains,
        keyword_gaps=keyword_gaps,
        errors=errors,
    )
    extract_path.write_text(extract_text, encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")
    created_at = _utc_now()
    artifacts = [
        _artifact_entry(
            extract_path,
            output_dir=output_dir,
            artifact_type="competitor_extract",
            privacy_class="private_structured_artifact",
            source_request_ids=source_request_ids,
            created_at=created_at,
        ),
        _artifact_entry(
            report_path,
            output_dir=output_dir,
            artifact_type="competitor_report",
            privacy_class="public_report_artifact",
            source_request_ids=source_request_ids,
            created_at=created_at,
        ),
    ]
    manifest = {
        "schema": "seo-observer.competitor_manifest.v1",
        "project": config.project.namespace,
        "command": DISCOVERY_COMMAND,
        "created_at": created_at,
        "config_hash": f"sha256:{compute_config_hash(config)}",
        "protocol_hashes": [],
        "budget": budget,
        "artifacts": artifacts,
        "source_request_ids": source_request_ids,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(_json_text(manifest), encoding="utf-8")
    manifest_entry = _artifact_entry(
        manifest_path,
        output_dir=output_dir,
        artifact_type="competitor_manifest",
        privacy_class="public_report_artifact",
        source_request_ids=source_request_ids,
        created_at=created_at,
    )
    response_artifacts = [manifest_entry, *artifacts]
    return {
        "ok": ok,
        "project": config.project.namespace,
        "command": DISCOVERY_COMMAND,
        "provider_mode": options.provider_mode,
        "sources": {
            "competitor_discovery": {
                "provider": "dataforseo",
                "quality": source_quality,
                "required": bool(config.sources.get("competitor_discovery") and config.sources["competitor_discovery"].required),
                "source_request_ids": source_request_ids,
            }
        },
        "artifacts": response_artifacts,
        "cost": {"provider_cost_usd": round(float(cost_usd), 6), "currency": "USD"},
        "report_path": str(report_path),
        "errors": errors,
    }


def _render_report(
    *,
    config: ProjectConfig,
    target_domain: str,
    source_quality: str,
    cost_usd: float,
    known_competitors: list[dict[str, Any]],
    candidate_competitors: list[dict[str, Any]],
    picked_competitors: list[dict[str, Any]],
    filtered_domains: list[dict[str, str]],
    keyword_gaps: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Competitor Discovery: {config.project.namespace}",
        "",
        "## Source Quality And Cost",
        f"- Target domain: `{target_domain}`",
        f"- Quality: `{source_quality}`",
        f"- Provider cost: `${round(float(cost_usd), 6)}`",
        "",
        "## Candidate Competitors",
    ]
    lines.extend(_domain_rows(candidate_competitors) or ["- None"])
    lines.extend(["", "## Picked Competitors"])
    lines.extend(_domain_rows(picked_competitors) or ["- None"])
    lines.extend(["", "## Known Competitors"])
    lines.extend([f"- `{row['domain']}` ({row['quality']}, {row['class']})" for row in known_competitors] or ["- None"])
    lines.extend(["", "## Filtered Domains"])
    lines.extend([f"- `{row['domain']}`: {row['reason']}" for row in filtered_domains] or ["- None"])
    lines.extend(["", "## Top Keyword Gaps"])
    for row in keyword_gaps[:20]:
        domains = ", ".join(f"`{domain}`" for domain in row["competitor_domains"])
        lines.append(
            f"- `{row['keyword']}`: {len(row['competitor_domains'])} competitor(s), "
            f"volume {row['search_volume']}, best rank {row['best_competitor_rank']}, {domains}"
        )
    if not keyword_gaps:
        lines.append("- None")
    lines.extend(["", "## Caveats"])
    if errors:
        for error in errors:
            lines.append(f"- `{error.get('code')}`: {error.get('safe_message') or error.get('message')}")
    else:
        lines.append("- Discovery artifacts contain normalized rows only, not raw provider payloads.")
    lines.extend(
        [
            "- This command does not calculate share of voice or trend movement.",
            "",
            "## Next Actions",
            "- Review picked competitors before SERP confirmation.",
            "- Run competitor SERP audit when deterministic SERP evidence is available.",
            "",
        ]
    )
    return "\n".join(lines)


def _domain_rows(rows: list[dict[str, Any]]) -> list[str]:
    output = []
    for row in rows:
        details = []
        for key in ("intersections", "organic_keywords", "estimated_traffic"):
            if key in row:
                details.append(f"{key}={row[key]}")
        reason = f", {row['selection_reason']}" if row.get("selection_reason") else ""
        output.append(f"- `{row['domain']}` ({', '.join(details) or 'local'}{reason})")
    return output


def _resolve_audit_keyword_set(config: ProjectConfig, options: CompetitorAuditOptions) -> SerpKeywordSet:
    selected = next((item for item in config.keyword_sets if item.id == options.keyword_set_id), None)
    if selected is None:
        raise ValueError(f"keyword set was not found: {options.keyword_set_id}")
    keywords = tuple(
        line.strip()
        for line in selected.path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    devices = options.devices or selected.devices or config.default_devices or ("desktop",)
    return SerpKeywordSet(
        id=selected.id,
        keywords=keywords,
        locale=selected.locale,
        regions=selected.regions,
        devices=tuple(devices),
        weight=selected.weight if selected.weight is not None else 1.0,
        market=getattr(selected, "market", None),
    )


# api_family goes into protocol_hash, i.e. into the observation comparability
# key. A fallback "everything that is not DataForSEO is Yandex" used to live
# here, and the very first new provider started stamping its rows as
# yandex_search_api: data that looks valid but is labeled with someone else's
# API. Fixing that later costs more than now — the fix changes hashes and voids
# the comparability of all accumulated history.
_SERP_PROVIDER_DEFAULTS = {
    "dataforseo_google_organic": ("dataforseo_serp_api", "v3", "/v3/serp/google/organic/live/advanced"),
    "yandex_search": ("yandex_search_api", "v2", YANDEX_SEARCH_ENDPOINT),
    "fixture_yandex_search": ("yandex_search_api", "v2", YANDEX_SEARCH_ENDPOINT),
    "topvisor_google_organic": ("topvisor_api", "v2", TOPVISOR_SNAPSHOTS_ENDPOINT),
    "topvisor_yandex_organic": ("topvisor_api", "v2", TOPVISOR_SNAPSHOTS_ENDPOINT),
}


def _provider_serp_defaults(provider: str) -> tuple[str, str, str]:
    """Defaults of api_family/api_version/endpoint for the market provider.

    An unknown provider is an error, not a silent attribution to a known one:
    silence here would mean a wrong label in protocol_hash, not missing data.
    """

    try:
        return _SERP_PROVIDER_DEFAULTS[provider]
    except KeyError:
        raise ValueError(
            f"SERP provider {provider!r} has no api_family/endpoint defaults; "
            "add it to _SERP_PROVIDER_DEFAULTS instead of letting it inherit another provider's"
        ) from None


def _audit_serp_source(config: ProjectConfig, keyword_set: SerpKeywordSet, options: CompetitorAuditOptions) -> SerpSource:
    source = config.sources.get("serp")
    fields = source.fields if source is not None else {}
    market = _market_for_keyword_set(config, keyword_set)
    if market is not None:
        provider = market.provider
        search_engine = market.search_engine
        language = market.language
        locale = market.locale
        api_family, api_version, endpoint = _provider_serp_defaults(provider)
    else:
        provider = str(fields.get("provider") or "dataforseo_google_organic")
        is_dataforseo = provider == "dataforseo_google_organic"
        search_engine = str(fields.get("search_engine") or config.default_search_engine or "google")
        language = str(config.default_language_code or "en")
        locale = keyword_set.locale
        api_family = str(fields.get("api_family") or ("dataforseo_serp_api" if is_dataforseo else "yandex_search_api"))
        api_version = str(fields.get("api_version") or ("v3" if is_dataforseo else "v2"))
        endpoint = str(fields.get("endpoint") or "/v3/serp/google/organic/live/advanced")
    depth = options.serp_depth or _int(fields.get("result_depth")) or 10
    observations = _int(fields.get("observations_per_protocol_slot")) or 1
    confirmation = _int(fields.get("confirmation_observations")) or 1
    return SerpSource(
        token="__local__",
        property_id=config.properties[0].id if config.properties else "__default__",
        provider=provider,
        api_family=api_family,
        api_version=api_version,
        endpoint=endpoint,
        method="POST",
        response_format="JSON",
        search_engine=search_engine,
        locale=locale,
        language=language,
        supports_devices=True,
        result_depth=depth,
        observations_per_protocol_slot=observations,
        confirmation_observations=confirmation,
        minimum_weighted_keyword_coverage=_float(fields.get("minimum_weighted_keyword_coverage")) or 0.9,
        classification_config_hash=_classification_config_hash(config.competitor_config),
        date_bucket=f"{options.start}:{options.end}",
    )


def _dataforseo_location_name(source: SerpSource, slot: Any) -> str | None:
    if source.provider == "dataforseo_google_organic" and str(slot.region_id or "").isdigit():
        return None
    return slot.region_name


def _normalize_serp_result_rows(
    rows: list[Any],
    slot: Any,
    competitor_config: CompetitorConfig,
    observed_at: str,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    request_ids = _provider_request_ids(result)
    request_id = request_ids[0] if request_ids else "local:serp"
    observations = []
    for raw_position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        rank = _optional_int(row.get("rank_absolute", row.get("rank", row.get("position"))))
        url = str(row.get("url") or "")
        host = normalized_host(url or str(row.get("domain") or ""))
        classification = classify_domain(url or host, competitor_config)
        observation = {
            "project_id": "__pending__",
            "property_id": slot.property_id,
            "source": "serp",
            "provider": slot.provider,
            "search_engine": slot.search_engine,
            "effective_at": observed_at,
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
            "logical_observation_key": f"serp:{slot.property_id}:{slot.keyword_set_id}:{hashlib.sha256((slot.protocol_hash + url + str(rank)).encode('utf-8')).hexdigest()}",
            "rank": rank,
            "url": url,
            "host": host,
            "domain": host,
            "title": row.get("title"),
            "snippet": row.get("description", row.get("snippet")),
            "serp_features": _serp_features(row),
            "classification": classification["classification"],
            "competitor_id": classification["competitor_id"],
            "competitor_name": classification["competitor_name"],
            "match_reason": classification["match_reason"],
            "matched_pattern": classification["matched_pattern"],
            "provenance_request_id": request_id,
            "raw_result_position": raw_position,
            "slot_weight": slot.weight,
            "normalizer_version": "serp-v1",
            "quality": row.get("quality") or result.get("quality") or "live",
            "result_type": row.get("result_type"),
        }
        observations.append(observation)
    return observations


def _write_audit_result(
    *,
    config: ProjectConfig,
    options: CompetitorAuditOptions,
    output_dir: Path,
    keyword_set: SerpKeywordSet,
    expected_slots: list[Any],
    observations: list[dict[str, Any]],
    source_requests: list[dict[str, Any]],
    source_quality: str,
    budget: dict[str, Any],
    source_request_ids: list[str],
    cost_usd: float,
    errors: list[dict[str, Any]],
    ok: bool,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = _audit_serp_source(config, keyword_set, options)
    expected_hashes = {slot.protocol_hash for slot in expected_slots}
    current_rows = [row for row in observations if str(row.get("protocol_hash") or "") in expected_hashes]
    baseline_snapshot = baseline.get("snapshot") if baseline.get("comparable") else None
    snapshots = []
    if isinstance(baseline_snapshot, dict):
        snapshots.append([dict(row) for row in baseline_snapshot.get("serp_rows") or [] if isinstance(row, dict)])
    snapshots.append(current_rows)
    metrics_raw = derive_competitor_metrics(
        snapshots,
        competitor_config=config.competitor_config,
        expected_slots=expected_slots,
        minimum_weighted_coverage=source.minimum_weighted_keyword_coverage,
        depth=source.result_depth,
        confirmation_observations=source.confirmation_observations,
    )
    protocol_hashes = sorted({slot.protocol_hash for slot in expected_slots})
    if not baseline.get("comparable"):
        metrics_raw["comparability"] = "not_comparable"
        metrics_raw["movements"] = {
            competitor_id: {
                "baseline_median_rank": None,
                "current_median_rank": item.get("median_rank"),
                "movement": None,
                "confirming_observations": 0,
                "required_confirmation_observations": source.confirmation_observations,
                "status": "not_comparable",
            }
            for competitor_id, item in metrics_raw.get("competitors", {}).items()
        }
    classification_summary = _classification_summary(current_rows)
    extract = {
        "schema": "seo-observer.serp_extract.v1",
        "keyword_set_id": keyword_set.id,
        "keyword_set_hash": keyword_set_hash(keyword_set.keywords),
        "protocol_hashes": protocol_hashes,
        "serp_rows": current_rows,
        "classification_summary": classification_summary,
        "coverage": metrics_raw["coverage"],
        "source_requests": source_requests,
        "quality_summary": {"overall": source_quality},
    }
    metrics = {
        "schema": "seo-observer.competitor_metrics.v1",
        "keyword_set_id": keyword_set.id,
        "keyword_set_hash": keyword_set_hash(keyword_set.keywords),
        "protocol_hashes": protocol_hashes,
        "coverage": metrics_raw["coverage"],
        "conclusion_status": metrics_raw["conclusion_status"],
        "comparability": metrics_raw["comparability"],
        "baseline": {"source": baseline.get("source"), "reason": baseline.get("reason")},
        "owned": _metric_items(metrics_raw["competitors"], classification="owned"),
        "competitors": _metric_items(metrics_raw["competitors"], exclude_classification=("owned", "unclassified")),
        "movements": metrics_raw["movements"],
        "quality": metrics_raw["quality"],
    }
    extract_path = output_dir / "serp-extract.json"
    metrics_path = output_dir / "competitor-metrics.json"
    report_path = output_dir / "competitor-audit.md"
    report_text = _render_audit_report(
        config=config,
        keyword_set=keyword_set,
        metrics=metrics,
        rows=current_rows,
        source_quality=source_quality,
        cost_usd=cost_usd,
        errors=errors,
    )
    extract_path.write_text(_json_text(extract), encoding="utf-8")
    metrics_path.write_text(_json_text(metrics), encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")
    polished = write_polished_report_artifacts(
        markdown_text=report_text,
        output_dir=output_dir,
        basename="competitor-audit",
        title=f"Competitor SEO analysis: {config.project.namespace}",
        subtitle=f"Keyword set: {keyword_set.id}. Source quality: {source_quality}.",
    )
    created_at = _utc_now()
    artifacts = [
        _artifact_entry(extract_path, output_dir=output_dir, artifact_type="serp_extract", privacy_class="private_structured_artifact", source_request_ids=source_request_ids, created_at=created_at),
        _artifact_entry(metrics_path, output_dir=output_dir, artifact_type="competitor_metrics", privacy_class="public_report_artifact", source_request_ids=source_request_ids, created_at=created_at),
        _artifact_entry(report_path, output_dir=output_dir, artifact_type="competitor_audit_report", privacy_class="public_report_artifact", source_request_ids=source_request_ids, created_at=created_at),
        _artifact_entry(polished.html_path, output_dir=output_dir, artifact_type="competitor_audit_html_report", privacy_class="public_report_artifact", source_request_ids=source_request_ids, created_at=created_at),
    ]
    if polished.pdf_path is not None:
        artifacts.append(
            _artifact_entry(
                polished.pdf_path,
                output_dir=output_dir,
                artifact_type="competitor_audit_pdf_report",
                privacy_class="public_report_artifact",
                source_request_ids=source_request_ids,
                created_at=created_at,
            )
        )
    manifest = {
        "schema": "seo-observer.competitor_manifest.v1",
        "project": config.project.namespace,
        "command": AUDIT_COMMAND,
        "created_at": created_at,
        "config_hash": f"sha256:{compute_config_hash(config)}",
        "protocol_hashes": protocol_hashes,
        "budget": budget,
        "artifacts": artifacts,
        "source_request_ids": source_request_ids,
    }
    if polished.pdf_error:
        manifest["pdf_error"] = {
            "code": "COMPETITOR_AUDIT_PDF_RENDER_FAILED",
            "message": polished.pdf_error,
        }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(_json_text(manifest), encoding="utf-8")
    response_artifacts = [
        _artifact_entry(manifest_path, output_dir=output_dir, artifact_type="competitor_manifest", privacy_class="public_report_artifact", source_request_ids=source_request_ids, created_at=created_at),
        *artifacts,
    ]
    return {
        "ok": ok,
        "project": config.project.namespace,
        "command": AUDIT_COMMAND,
        "provider_mode": options.provider_mode,
        "sources": {
            "serp": {
                "provider": config.sources.get("serp").fields.get("provider") if config.sources.get("serp") else None,
                "quality": source_quality,
                "required": bool(config.sources.get("serp") and config.sources["serp"].required),
                "source_request_ids": source_request_ids,
            }
        },
        "artifacts": response_artifacts,
        "cost": {"provider_cost_usd": round(float(cost_usd), 6), "currency": "USD"},
        "report_path": str(report_path),
        "html_report_path": str(polished.html_path),
        "pdf_report_path": str(polished.pdf_path) if polished.pdf_path is not None else None,
        "errors": errors,
    }


def _render_audit_report(
    *,
    config: ProjectConfig,
    keyword_set: SerpKeywordSet,
    metrics: dict[str, Any],
    rows: list[dict[str, Any]],
    source_quality: str,
    cost_usd: float,
    errors: list[dict[str, Any]],
) -> str:
    owned = metrics.get("owned") or []
    competitors = metrics.get("competitors") or []
    features = sorted({feature for row in rows for feature in row.get("serp_features") or []})
    outranking = _outranking_rows(rows)
    lines = [
        f"# Competitor SERP Audit: {config.project.namespace}",
        "",
        "## Keyword Set",
        f"- ID: `{keyword_set.id}`",
        f"- Hash: `{keyword_set_hash(keyword_set.keywords)}`",
        f"- Keywords: {len(normalized_keywords(keyword_set.keywords))}",
        "",
        "## Market Device Protocol Coverage",
        f"- Coverage: `{metrics['coverage']['weighted_coverage']}`",
        f"- Comparability: `{metrics['comparability']}`",
        f"- Conclusion: `{metrics['conclusion_status']}`",
        f"- Quality: `{source_quality}`",
        f"- Provider cost: `${round(float(cost_usd), 6)}`",
        "",
        "## Owned Domain Visibility",
    ]
    lines.extend(_metric_report_rows(owned) or ["- None"])
    lines.extend(["", "## Competitor Visibility"])
    lines.extend(_metric_report_rows(competitors) or ["- None"])
    lines.extend(["", "## SERP Features Observed"])
    lines.extend([f"- `{feature}`" for feature in features] or ["- None"])
    lines.extend(["", "## Pages Outranking Owned Domains"])
    lines.extend([f"- `{row['keyword']}` rank {row['rank']}: {row['url']}" for row in outranking[:20]] or ["- None"])
    lines.extend(["", "## Comparability"])
    baseline = metrics.get("baseline") or {}
    if baseline.get("reason") == "protocol_mismatch":
        lines.append("- Baseline exists but protocol hashes differ, so movement is not comparable.")
    elif baseline.get("reason") == "not_found":
        lines.append("- No comparable baseline was found; movement fields are null.")
    elif metrics["comparability"] == "not_comparable":
        lines.append("- Coverage or protocol conditions make this run not comparable.")
    else:
        lines.append("- Current and baseline evidence are comparable for movement fields.")
    lines.extend(["", "## Caveats"])
    if errors:
        lines.extend(f"- `{error.get('code')}`: {error.get('safe_message') or error.get('message')}" for error in errors)
    else:
        lines.append("- SERP observations are point samples, not confirmed trends.")
    lines.extend(["", "## Next Actions"])
    lines.append("- Review owned URLs outranked by configured competitors.")
    lines.append("- Add a comparable baseline before making trend claims.")
    return "\n".join(lines) + "\n"


def _write_research_result(
    *,
    config: ProjectConfig,
    options: CompetitorResearchOptions,
    output_dir: Path,
    keyword_set: SerpKeywordSet,
    research_rows: list[dict[str, Any]],
    page_extracts: list[dict[str, Any]],
    source_requests: list[dict[str, Any]],
    source_quality: str,
    budget: dict[str, Any],
    source_request_ids: list[str],
    cost_usd: float,
    errors: list[dict[str, Any]],
    ok: bool,
    observed_at: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    research_extract = {
        "schema": "seo-observer.research_extract.v1",
        "keyword_set_id": keyword_set.id,
        "keyword_set_hash": keyword_set_hash(keyword_set.keywords),
        "query": _research_query(keyword_set),
        "research_rows": research_rows,
        "source_requests": source_requests,
        "quality_summary": {
            "overall": source_quality,
            "research_row_count": len(research_rows),
            "error_count": len(errors),
        },
        "budget": budget,
    }
    content_extract = {
        "schema": "seo-observer.content_extract.v1",
        "keyword_set_id": keyword_set.id,
        "keyword_set_hash": keyword_set_hash(keyword_set.keywords),
        "page_extracts": page_extracts,
        "quality_summary": {
            "overall": "partial" if any(row.get("error") for row in page_extracts) else "research-only",
            "page_count": len(page_extracts),
            "error_count": sum(1 for row in page_extracts if row.get("error")),
        },
    }
    research_path = output_dir / "research-extract.json"
    content_path = output_dir / "content-extract.json"
    report_path = output_dir / "research-report.md"
    research_path.write_text(_json_text(research_extract), encoding="utf-8")
    content_path.write_text(_json_text(content_extract), encoding="utf-8")
    report_text = _render_research_report(
        config=config,
        keyword_set=keyword_set,
        source_quality=source_quality,
        cost_usd=cost_usd,
        research_rows=research_rows,
        page_extracts=page_extracts,
        errors=errors,
    )
    report_path.write_text(report_text, encoding="utf-8")
    polished = write_polished_report_artifacts(
        markdown_text=report_text,
        output_dir=output_dir,
        basename="research-report",
        title=f"Competitor SEO research: {config.project.namespace}",
        subtitle=f"Keyword set: {keyword_set.id}. Source quality: {source_quality}.",
    )
    artifacts = [
        _artifact_entry(research_path, output_dir=output_dir, artifact_type="research_extract", privacy_class="private_structured_artifact", source_request_ids=source_request_ids, created_at=observed_at),
        _artifact_entry(content_path, output_dir=output_dir, artifact_type="content_extract", privacy_class="private_structured_artifact", source_request_ids=source_request_ids, created_at=observed_at),
        _artifact_entry(report_path, output_dir=output_dir, artifact_type="research_report", privacy_class="public_report_artifact", source_request_ids=source_request_ids, created_at=observed_at),
        _artifact_entry(polished.html_path, output_dir=output_dir, artifact_type="research_html_report", privacy_class="public_report_artifact", source_request_ids=source_request_ids, created_at=observed_at),
    ]
    if polished.pdf_path is not None:
        artifacts.append(
            _artifact_entry(
                polished.pdf_path,
                output_dir=output_dir,
                artifact_type="research_pdf_report",
                privacy_class="public_report_artifact",
                source_request_ids=source_request_ids,
                created_at=observed_at,
            )
        )
    manifest = {
        "schema": "seo-observer.competitor_manifest.v1",
        "project": config.project.namespace,
        "command": RESEARCH_COMMAND,
        "created_at": observed_at,
        "config_hash": f"sha256:{compute_config_hash(config)}",
        "protocol_hashes": [],
        "budget": budget,
        "artifacts": artifacts,
        "source_request_ids": source_request_ids,
    }
    if polished.pdf_error:
        manifest["pdf_error"] = {
            "code": "RESEARCH_PDF_RENDER_FAILED",
            "message": polished.pdf_error,
        }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(_json_text(manifest), encoding="utf-8")
    response_artifacts = [
        _artifact_entry(manifest_path, output_dir=output_dir, artifact_type="competitor_manifest", privacy_class="public_report_artifact", source_request_ids=source_request_ids, created_at=observed_at),
        *artifacts,
    ]
    return {
        "ok": ok,
        "project": config.project.namespace,
        "command": RESEARCH_COMMAND,
        "provider_mode": options.provider_mode,
        "sources": {
            "competitor_research": {
                "provider": _research_provider_name(config),
                "quality": source_quality,
                "required": bool(config.sources.get("competitor_research") and config.sources["competitor_research"].required),
                "source_request_ids": source_request_ids,
            }
        },
        "artifacts": response_artifacts,
        "cost": {"provider_cost_usd": round(float(cost_usd), 6), "currency": "USD"},
        "report_path": str(report_path),
        "html_report_path": str(polished.html_path),
        "pdf_report_path": str(polished.pdf_path) if polished.pdf_path is not None else None,
        "errors": errors,
    }


def _render_research_report(
    *,
    config: ProjectConfig,
    keyword_set: SerpKeywordSet,
    source_quality: str,
    cost_usd: float,
    research_rows: list[dict[str, Any]],
    page_extracts: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Competitor research: {config.project.namespace}",
        "",
        "## Source quality and cost",
        f"- Keyword set: `{keyword_set.id}`",
        f"- Keyword set hash: `{keyword_set_hash(keyword_set.keywords)}`",
        f"- Data quality: `{source_quality}`",
        f"- Provider request cost: `${round(float(cost_usd), 6)}`",
        "",
        "## What can be claimed from this data",
        "- This is a research SERP sample and extracted pages, not a rank measurement. It supports a list of competitor candidates and page topics.",
        "- Do not draw conclusions about share of voice, growth/decline, traffic, or exact ranks without a separate SERP audit.",
        "",
        "## Candidate pages found",
    ]
    if research_rows:
        for row in research_rows[:20]:
            lines.append(
                f"- `{row.get('citation_id')}` `{row.get('quality')}` "
                f"{row.get('title') or row.get('url')} - {row.get('url')}"
            )
            snippet = str(row.get("snippet") or "")[:300]
            if snippet:
                lines.append(f"  - Snippet: {snippet}")
    else:
        lines.append("- No data.")
    lines.extend(["", render_content_report_section(page_extracts), "## Partial limitations and errors"])
    if errors:
        lines.extend(f"- `{error.get('code')}`: {error.get('safe_message') or error.get('message')}" for error in errors)
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Next actions",
            "- Confirm the candidates via a SERP audit if rank, visibility, and share-of-voice conclusions are needed.",
            "- Use page headings and safe text excerpts as input for content-gap analysis.",
            "",
        ]
    )
    return "\n".join(lines)


def _resolve_research_keyword_set(config: ProjectConfig, options: CompetitorResearchOptions) -> SerpKeywordSet:
    selected = next((item for item in config.keyword_sets if item.id == options.keyword_set_id), None)
    if selected is None:
        raise ValueError(f"keyword set was not found: {options.keyword_set_id}")
    keywords = tuple(
        line.strip()
        for line in selected.path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    return SerpKeywordSet(
        id=selected.id,
        keywords=keywords,
        locale=selected.locale,
        regions=selected.regions or config.default_regions,
        devices=selected.devices or config.default_devices or ("desktop",),
        weight=selected.weight if selected.weight is not None else 1.0,
    )


def _research_query(keyword_set: SerpKeywordSet) -> str:
    keywords = normalized_keywords(keyword_set.keywords)
    return " OR ".join(keywords[:10]) if keywords else keyword_set.id


def _read_research_extract(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "research-extract.json"
    value = _read_json_artifact(path) if path.is_file() else {}
    return value if value.get("schema") == "seo-observer.research_extract.v1" else None


def _read_content_extract(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "content-extract.json"
    value = _read_json_artifact(path) if path.is_file() else {}
    return value if value.get("schema") == "seo-observer.content_extract.v1" else None


def _research_source_request(label: str, result: dict[str, Any], observed_at: str) -> dict[str, Any]:
    return {
        "id": _provider_request_ids(result)[0] if _provider_request_ids(result) else f"local:{label}",
        "source": str(result.get("provider") or "exa"),
        "label": label,
        "quality": result.get("quality") or "research-only",
        "observed_at": observed_at,
        "cost": result.get("cost") or {},
    }


def _aggregate_research_quality(result: dict[str, Any], errors: list[dict[str, Any]]) -> str:
    if errors:
        return "partial"
    return str(result.get("quality") or "research-only")


def _research_result_ok(
    config: ProjectConfig,
    *,
    provider_mode: str,
    errors: list[dict[str, Any]],
    research_rows: list[dict[str, Any]],
) -> bool:
    source = config.sources.get("competitor_research")
    if not errors:
        return True
    blocking_errors = [item for item in errors if item.get("code") not in {"PAGE_EXTRACTION_PARTIAL"}]
    if provider_mode == "live":
        return not blocking_errors and bool(research_rows)
    return not bool(errors and source and source.required)


def _research_provider_name(config: ProjectConfig) -> str | None:
    source = config.sources.get("competitor_research")
    if source is None:
        return None
    return str(source.fields.get("provider") or "")


def _research_readiness(config: ProjectConfig, *, env: dict[str, str]) -> dict[str, Any]:
    source = config.sources.get("competitor_research")
    provider_name = _research_provider_name(config)
    provider = config.providers.get(provider_name or "")
    if source is None or not source.enabled:
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _research_error("SOURCE_NOT_CONFIGURED", "Competitor research source is not enabled.", {}),
        }
    if provider is None or not provider.enabled:
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _research_error(
                "SOURCE_PROVIDER_NOT_CONFIGURED",
                "Competitor research provider is not configured.",
                {},
                provider=provider_name,
            ),
        }
    if provider.credential_env and not env.get(provider.credential_env):
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _research_error(
                "PROVIDER_CREDENTIAL_MISSING",
                "Research provider credentials are not ready for live competitor research.",
                {"credential_env": provider.credential_env},
                provider=provider_name,
            ),
        }
    return {"ok": True, "quality": "live", "error": None}


def _select_audit_baseline(
    options: CompetitorAuditOptions,
    *,
    storage: Any | None,
    protocol_hashes: list[str],
) -> dict[str, Any]:
    if options.baseline_artifact is not None:
        snapshot = _read_json_artifact(Path(options.baseline_artifact))
        baseline_hashes = set(str(item) for item in snapshot.get("protocol_hashes") or [])
        current_hashes = set(protocol_hashes)
        if not baseline_hashes:
            reason = "protocol_missing"
        elif baseline_hashes != current_hashes:
            reason = "protocol_mismatch"
        else:
            reason = None
        return {
            "snapshot": snapshot,
            "source": str(options.baseline_artifact),
            "reason": reason,
            "comparable": reason is None,
        }
    if storage is not None and hasattr(storage, "latest_comparable_serp_audit"):
        snapshot = storage.latest_comparable_serp_audit(protocol_hashes)
        if snapshot:
            return {"snapshot": snapshot, "source": "storage", "reason": None, "comparable": True}
    return {"snapshot": None, "source": None, "reason": "not_found", "comparable": False}


def _read_json_artifact(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_cached_serp_extract(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "serp-extract.json"
    if not path.is_file():
        return None
    value = _read_json_artifact(path)
    if value.get("schema") == "seo-observer.serp_extract.v1":
        return value
    return None


def _cached_serp_extract_matches(
    extract: dict[str, Any],
    *,
    keyword_set: SerpKeywordSet,
    protocol_hashes: list[str],
) -> bool:
    return (
        extract.get("keyword_set_id") == keyword_set.id
        and extract.get("keyword_set_hash") == keyword_set_hash(keyword_set.keywords)
        and set(str(item) for item in extract.get("protocol_hashes") or []) == set(protocol_hashes)
    )


def _classification_config_hash(config: CompetitorConfig) -> str:
    payload = {
        "owned_domains": list(config.owned_domains),
        "competitors": [
            {"id": item.id, "name": item.name, "domain_patterns": list(item.domain_patterns), "aliases": list(item.aliases)}
            for item in config.competitors
        ],
    }
    return f"sha256:{hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()}"


def _classification_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("classification") or "unclassified"), str(row.get("protocol_hash") or ""))
        if key in seen:
            continue
        seen.add(key)
        summary[key[0]] = summary.get(key[0], 0) + 1
    return summary


def _metric_items(
    metrics: dict[str, Any],
    *,
    classification: str | None = None,
    exclude_classification: str | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    excluded = {exclude_classification} if isinstance(exclude_classification, str) else set(exclude_classification or ())
    for item in metrics.values():
        if classification is not None and item.get("classification") != classification:
            continue
        if item.get("classification") in excluded:
            continue
        rows.append({key: value for key, value in item.items() if not key.startswith("_")})
    rows.sort(key=lambda item: (-(item.get("visibility") or 0), str(item.get("competitor_id"))))
    return rows


def _serp_features(row: dict[str, Any]) -> list[str]:
    features = row.get("serp_features", row.get("features", []))
    if not isinstance(features, list):
        return []
    values = [str(item) for item in features if str(item)]
    result_type = row.get("result_type")
    if result_type and str(result_type) not in values:
        values.append(str(result_type))
    return values


def _outranking_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    owned_best: dict[str, int] = {}
    for row in rows:
        if row.get("classification") != "owned":
            continue
        rank = _optional_int(row.get("rank"))
        if rank is None:
            continue
        keyword = str(row.get("keyword") or "")
        owned_best[keyword] = min(rank, owned_best.get(keyword, rank))
    output = []
    for row in rows:
        if row.get("classification") == "owned":
            continue
        rank = _optional_int(row.get("rank"))
        keyword = str(row.get("keyword") or "")
        if rank is not None and keyword in owned_best and rank < owned_best[keyword]:
            output.append(row)
    return sorted(output, key=lambda row: (str(row.get("keyword")), _optional_int(row.get("rank")) or 9999))


def _metric_report_rows(rows: list[dict[str, Any]]) -> list[str]:
    return [
        f"- `{row.get('competitor_id')}` visibility `{row.get('visibility')}`, "
        f"SOV `{row.get('share_of_voice')}`, best rank `{row.get('best_rank')}`, median rank `{row.get('median_rank')}`"
        for row in rows
    ]


def _resolve_target_domain(config: ProjectConfig, options: CompetitorDiscoveryOptions) -> str:
    if options.target_domain:
        domain = _canonical_domain(options.target_domain)
        if domain is None:
            raise ValueError("target domain is malformed")
        return domain
    properties = config.properties
    if options.property_id:
        properties = [prop for prop in properties if prop.id == options.property_id]
    if not properties:
        raise ValueError("property was not found")
    parsed = urlparse(properties[0].url)
    domain = _canonical_domain(parsed.hostname or properties[0].url)
    if domain is None:
        raise ValueError("property URL does not contain a valid domain")
    return domain


def _readiness(
    *,
    source: Any,
    provider: Any,
    env: dict[str, str],
    require_credentials: bool,
) -> dict[str, Any]:
    if source is None or not source.enabled:
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _error("SOURCE_NOT_CONFIGURED", "Competitor discovery source is not enabled.", {}),
        }
    if provider is None or not provider.enabled:
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _error("SOURCE_PROVIDER_NOT_CONFIGURED", "DataForSEO provider is not configured for competitor discovery.", {}),
        }
    if require_credentials and provider.credential_env and not env.get(provider.credential_env):
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _error(
                "PROVIDER_CREDENTIAL_MISSING",
                "DataForSEO credentials are not ready for live competitor discovery.",
                {"credential_env": provider.credential_env},
            ),
        }
    return {"ok": True, "quality": "live", "error": None}


def _serp_readiness(
    config: ProjectConfig, *, env: dict[str, str], market: Any | None = None
) -> dict[str, Any]:
    source = config.sources.get("serp")
    if market is not None and market.search_engine == "yandex":
        # The RU market does not depend on DataForSEO: that provider does not
        # serve RU locations at all, so the market credentials must be checked
        # rather than the universal DataForSEO ones.
        if source is None or not source.enabled:
            return {
                "ok": False,
                "quality": "unsupported",
                "error": _error("SOURCE_NOT_CONFIGURED", "SERP source is disabled.", {}),
            }
        if market.credential_env and not env.get(market.credential_env):
            return {
                "ok": False,
                "quality": "unsupported",
                "error": _error(
                    "PROVIDER_CREDENTIAL_MISSING",
                    f"Yandex Search API credentials are not ready for market {market.id!r}.",
                    {"credential_env": market.credential_env},
                ),
            }
        return {"ok": True, "quality": "live", "error": None}
    if market is not None and market.provider.startswith("topvisor_"):
        # Topvisor serves RU-Google, which DataForSEO does not serve at all, so
        # the market credentials are checked rather than the universal DataForSEO
        # ones. There are two of them: account id and key — a missing one is
        # named explicitly.
        if source is None or not source.enabled:
            return {
                "ok": False,
                "quality": "unsupported",
                "error": _error("SOURCE_NOT_CONFIGURED", "SERP source is disabled.", {}),
            }
        fields = getattr(market, "fields", {}) or {}
        required = {
            market.credential_env: env.get(market.credential_env or "", ""),
            str(fields.get("user_id_env") or "TOPVISOR_USER_ID"): env.get(
                str(fields.get("user_id_env") or "TOPVISOR_USER_ID"), ""
            ),
        }
        missing = sorted(name for name, value in required.items() if name and not value)
        if missing:
            return {
                "ok": False,
                "quality": "unsupported",
                "error": _error(
                    "PROVIDER_CREDENTIAL_MISSING",
                    f"Topvisor credentials are not ready for market {market.id!r}.",
                    {"missing_env": missing},
                ),
            }
        if not fields.get("project_id"):
            return {
                "ok": False,
                "quality": "unsupported",
                "error": _error(
                    "SOURCE_NOT_CONFIGURED",
                    f"market {market.id!r} has no project_id; Topvisor keeps keywords "
                    "in a persistent project and creating one per run would lose history.",
                    {"market": market.id},
                ),
            }
        return {"ok": True, "quality": "live", "error": None}
    provider = config.providers.get("dataforseo")
    if source is None or not source.enabled or source.fields.get("provider") != "dataforseo_google_organic":
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _error("SOURCE_NOT_CONFIGURED", "SERP source is not configured for DataForSEO Google organic.", {}),
        }
    if provider is None or not provider.enabled:
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _error("SOURCE_PROVIDER_NOT_CONFIGURED", "DataForSEO provider is not configured for SERP audit.", {}),
        }
    if provider.credential_env and not env.get(provider.credential_env):
        return {
            "ok": False,
            "quality": "unsupported",
            "error": _error(
                "PROVIDER_CREDENTIAL_MISSING",
                "DataForSEO credentials are not ready for live SERP audit.",
                {"credential_env": provider.credential_env},
            ),
        }
    return {"ok": True, "quality": "live", "error": None}


def _known_competitors(config: CompetitorsConfig, *, observed_at: str) -> list[dict[str, Any]]:
    known = []
    for item in config.items:
        for domain in _entry_domains(item.domain_patterns):
            known.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "domain": domain,
                    "class": item.competitor_class,
                    "quality": "local-only",
                    "observed_at": observed_at,
                }
            )
    return known


def _configured_domains(config: CompetitorsConfig) -> set[str]:
    return {domain for item in config.items for domain in _entry_domains(item.domain_patterns)}


def _configured_class(config: CompetitorsConfig, domain: str) -> str | None:
    for item in config.items:
        if domain in _entry_domains(item.domain_patterns):
            return item.competitor_class
    return None


def _entry_domains(patterns: tuple[str, ...]) -> list[str]:
    domains = []
    for pattern in patterns:
        normalized = _canonical_pattern(pattern)
        if normalized.startswith("*."):
            normalized = normalized[2:]
        domain = _canonical_domain(normalized)
        if domain:
            domains.append(domain)
    return sorted(set(domains))


def _mega_authority_domains(config: ProjectConfig) -> tuple[str, ...]:
    configured = ()
    source = config.sources.get("competitor_discovery")
    if source is not None:
        configured = tuple(str(item) for item in source.fields.get("mega_authority_domains") or ())
    return tuple(sorted(set(DEFAULT_MEGA_AUTHORITY_DOMAINS + configured)))


def _canonical_domain(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().rstrip(".")
    if not text or any(ord(char) > 127 for char in text):
        return None
    parsed = urlparse(text)
    if parsed.scheme or parsed.path not in ("", text) or parsed.params or parsed.query or parsed.fragment:
        return None
    if "/" in text or ":" in text or "@" in text or ".." in text:
        return None
    if not DOMAIN_RE.fullmatch(text):
        return None
    return text


def _canonical_pattern(value: str) -> str:
    text = str(value).strip().lower().rstrip(".")
    return text


def _matches_any_pattern(domain: str | None, patterns: tuple[str, ...]) -> bool:
    if domain is None:
        return False
    for pattern in patterns:
        if pattern.startswith("*.") and domain.endswith(pattern[1:]):
            return True
        if domain == pattern:
            return True
    return False


def _normalize_keyword(keyword: str) -> str:
    return " ".join(keyword.casefold().split())


def _source_request(label: str, result: dict[str, Any], observed_at: str) -> dict[str, Any]:
    return {
        "id": _provider_request_ids(result)[0] if _provider_request_ids(result) else f"local:{label}",
        "source": "dataforseo",
        "label": label,
        "quality": result.get("quality") or "partial",
        "observed_at": observed_at,
        "cost": result.get("cost") or {},
    }


def _source_request_ids(rows: list[Any]) -> list[str]:
    ids = []
    for row in rows:
        if isinstance(row, dict) and row.get("id"):
            ids.append(str(row["id"]))
    return ids


def _provider_request_ids(result: dict[str, Any]) -> list[str]:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    return [str(item) for item in metadata.get("provider_request_ids") or [] if item]


def _result_errors(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in result.get("errors") or [] if isinstance(item, dict)]


def _provider_cost(result: dict[str, Any]) -> float:
    cost = result.get("cost") if isinstance(result.get("cost"), dict) else {}
    return _float(cost.get("provider_cost_usd"))


def _source_requests_cost(rows: list[Any]) -> float:
    total = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        cost = row.get("cost") if isinstance(row.get("cost"), dict) else {}
        total += _float(cost.get("provider_cost_usd"))
    return round(total, 6)


def _aggregate_quality(provider_mode: str, result: dict[str, Any], errors: list[dict[str, Any]]) -> str:
    if provider_mode == "fixture":
        return "local-only" if not result.get("rows") else "partial" if errors else "live"
    if errors:
        return "partial"
    return str(result.get("quality") or "live")


def _read_cached_extract(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "competitor-extract.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(value, dict) and value.get("schema") == "seo-observer.competitor_extract.v1":
        return value
    return None


def _artifact_entry(
    path: Path,
    *,
    output_dir: Path,
    artifact_type: str,
    privacy_class: str,
    source_request_ids: list[str],
    created_at: str,
) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(output_dir)),
        "sha256": f"sha256:{_sha256_bytes(path.read_bytes())}",
        "artifact_type": artifact_type,
        "privacy_class": privacy_class,
        "created_at": created_at,
        "source_request_ids": source_request_ids,
    }


def _budget_guard(config: ProjectConfig, options: CompetitorDiscoveryOptions, *, planned_calls: int) -> BudgetGuard:
    provider = config.providers.get("dataforseo")
    return BudgetGuard(
        command=DISCOVERY_COMMAND,
        project=config.project.namespace,
        provider="dataforseo",
        provider_mode=options.provider_mode,
        allow_paid=options.allow_paid,
        planned_calls=planned_calls,
        per_run_budget_usd=provider.per_run_budget_usd if provider else None,
        monthly_budget_usd=provider.monthly_budget_usd if provider else None,
        estimated_cost_usd=None,
    )


def _audit_budget_guard(config: ProjectConfig, options: CompetitorAuditOptions, *, planned_calls: int) -> BudgetGuard:
    provider = config.providers.get("dataforseo")
    return BudgetGuard(
        command=AUDIT_COMMAND,
        project=config.project.namespace,
        provider="dataforseo",
        provider_mode=options.provider_mode,
        allow_paid=options.allow_paid,
        planned_calls=planned_calls,
        per_run_budget_usd=provider.per_run_budget_usd if provider else None,
        monthly_budget_usd=provider.monthly_budget_usd if provider else None,
        estimated_cost_usd=None,
    )


def _research_budget_guard(config: ProjectConfig, options: CompetitorResearchOptions, *, planned_calls: int) -> BudgetGuard:
    provider_name = _research_provider_name(config) or "exa"
    provider = config.providers.get(provider_name)
    return BudgetGuard(
        command=RESEARCH_COMMAND,
        project=config.project.namespace,
        provider=provider_name,
        provider_mode=options.provider_mode,
        allow_paid=options.allow_paid,
        planned_calls=planned_calls,
        per_run_budget_usd=provider.per_run_budget_usd if provider else None,
        monthly_budget_usd=provider.monthly_budget_usd if provider else None,
        estimated_cost_usd=None,
    )


def _budget_error(code: str, budget: BudgetGuard) -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "provider": "dataforseo",
        "endpoint": None,
        "safe_message": "Paid provider calls require allow_paid.",
        "retryable": False,
        "details": {
            "command": DISCOVERY_COMMAND,
            "project": budget.project,
            "planned_calls": budget.planned_calls,
            "allow_paid": False,
        },
    }


def _audit_budget_error(code: str, budget: BudgetGuard) -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "provider": "dataforseo",
        "endpoint": None,
        "safe_message": "Paid provider calls require allow_paid.",
        "retryable": False,
        "details": {
            "command": AUDIT_COMMAND,
            "project": budget.project,
            "planned_calls": budget.planned_calls,
            "allow_paid": False,
        },
    }


def _research_budget_error(code: str, budget: BudgetGuard) -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "provider": budget.provider,
        "endpoint": None,
        "safe_message": "Paid provider calls require allow_paid.",
        "retryable": False,
        "details": {
            "command": RESEARCH_COMMAND,
            "project": budget.project,
            "planned_calls": budget.planned_calls,
            "allow_paid": False,
        },
    }


def _research_error(
    code: str,
    message: str,
    details: dict[str, Any],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    return _error(code, message, details, provider=provider or "exa")


def _error(code: str, message: str, details: dict[str, Any], *, provider: str = "dataforseo") -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "provider": provider,
        "endpoint": None,
        "safe_message": message,
        "retryable": False,
        "details": details,
    }


def _default_output_dir(project: str) -> Path:
    return observer_home() / "projects" / project / "competitors" / "latest"


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _int(value: Any) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else 0


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
