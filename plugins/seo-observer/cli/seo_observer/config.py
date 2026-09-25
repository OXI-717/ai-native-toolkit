from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
import dataclasses
from pathlib import Path

from seo_observer.credential_source import CredentialSource
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seo_observer.channels import ChannelsConfig
from seo_observer.serp import Competitor, CompetitorConfig
from seo_observer.webmaster import validate_host_id


DEFAULT_HOME = Path.home() / ".seo-observer"
CONFIG_RELATIVE_PATH = Path(".seo-observer") / "project.toml"
SUPPORTED_CONFIG_SCHEMA_VERSION = 1
KNOWN_SOURCE_NAMES = frozenset(
    {
        "yandex_metrica",
        "yandex_webmaster",
        "google_search_console",
        "ga4",
        "wordstat",
        "serp",
        "competitor_discovery",
        "competitor_research",
        "outcome_auth",
        "outcome_pay",
    }
)
KNOWN_SERP_PROVIDERS = frozenset(
    {
        "yandex_search",
        "google_search",
        "dataforseo_google_organic",
        # Google RU market: DataForSEO has not served Russian locations in any
        # service since 2022, so Google in RU goes through Topvisor.
        "topvisor_google_organic",
        "topvisor_yandex_organic",
    }
)
KNOWN_SERP_API_FAMILIES = frozenset(
    {"yandex_search_api", "dataforseo_serp_api", "topvisor_api"}
)
KNOWN_RANK_AGGREGATIONS = frozenset({"median", "best", "mean"})
KNOWN_WORDSTAT_API_FAMILIES = frozenset({"search_api_wordstat", "direct_wordstat_report"})
KNOWN_PROVIDER_NAMES = frozenset({"dataforseo", "exa", "topvisor"})
KNOWN_COMPETITOR_CLASSES = frozenset(
    {"direct", "indirect", "marketplace", "aggregator", "reference", "unknown"}
)
KNOWN_MARKET_SEARCH_ENGINES = frozenset({"google", "yandex"})
KNOWN_MARKET_DEVICES = frozenset({"desktop", "mobile"})
KNOWN_MARKET_INTENTS = frozenset({"primary", "secondary", "out_of_scope"})
KNOWN_MARKET_OUT_OF_SCOPE_IDENTIFIERS = frozenset(
    {"google_worldwide_serp_competitor_lens", "default_ru_yandex_competitor_lens"}
)
COMPETITOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
MARKET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
PROVIDER_CREDENTIAL_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
FORBIDDEN_OUTCOME_QUERY_FIELDS = frozenset(
    {
        "sql",
        "query",
        "table",
        "where",
        "where_clause",
        "sql_fragment",
        "report_query",
    }
)
KNOWN_OUTCOME_AGGREGATE_ADAPTERS = frozenset(
    {"fixture_aggregate", "postgres_aggregate", "http_aggregate"}
)


class ConfigError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class ProjectIdentity:
    namespace: str
    timezone: str


@dataclass(frozen=True)
class PropertyConfig:
    id: str
    url: str


@dataclass(frozen=True)
class SourceConfig:
    name: str
    enabled: bool
    required: bool
    fields: dict[str, Any]


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    enabled: bool
    credential_env: str
    endpoint: str | None = None
    monthly_budget_usd: float | None = None
    per_run_budget_usd: float | None = None
    cache_ttl_hours: int | None = None
    finalize_after: str | None = None
    fields: dict[str, Any] | None = None


@dataclass(frozen=True)
class CompetitorEntry:
    id: str
    name: str
    domain_patterns: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    competitor_class: str = "unknown"
    # An empty tuple means "all markets": a competitor may operate on every
    # market the project tracks. Resolved to an explicit list during parsing.
    markets: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompetitorsConfig:
    owned_domains: tuple[str, ...] = ()
    items: tuple[CompetitorEntry, ...] = ()


@dataclass(frozen=True)
class SourceBinding:
    properties: tuple[str, ...]
    source: str
    remote_id: str


@dataclass(frozen=True)
class KeywordSet:
    id: str
    path: Path
    locale: str
    regions: tuple[str, ...]
    devices: tuple[str, ...]
    weight: float | None = None
    # A set belongs to exactly one market; None means the project is not yet split into markets.
    market: str | None = None


@dataclass(frozen=True)
class OutcomeConfig:
    id: str
    order: int
    fields: dict[str, Any]


@dataclass(frozen=True)
class MarketConfig:
    """An independent search market of the project (e.g. RU/Yandex and EN/Google).

    Markets are not mixed: a keyword set belongs to one market, and metrics are
    computed per market. Only competitors may overlap.
    """

    id: str
    search_engine: str
    provider: str
    credential_env: str
    regions: tuple[str, ...]
    locale: str
    language: str
    devices: tuple[str, ...] = ()
    intent: str = "primary"
    source_roles: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    # Raw market fields: provider-specific keys (project_id, user_id_env,
    # snapshot_date for Topvisor) cannot become fields of the shared dataclass,
    # otherwise every new provider would extend the contract of all markets.
    fields: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    project: ProjectIdentity
    schema_version: int
    properties: list[PropertyConfig]
    keyword_files: list[Path]
    default_search_engine: str | None
    default_location_code: int | str | None
    default_location_name: str | None
    default_language_code: str | None
    default_devices: tuple[str, ...]
    providers: dict[str, ProviderConfig]
    competitors: CompetitorsConfig
    competitor_config: CompetitorConfig
    sources: dict[str, SourceConfig]
    source_bindings: list[SourceBinding]
    keyword_sets: list[KeywordSet]
    outcomes: list[OutcomeConfig]
    markets: tuple[MarketConfig, ...] = ()
    credential_source: CredentialSource = CredentialSource()
    channels: ChannelsConfig = ChannelsConfig()


def observer_home() -> Path:
    configured = os.environ.get("SEO_OBSERVER_HOME")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_HOME


def discover_project_config(cwd: Path | None = None) -> Path | None:
    current = (cwd or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_RELATIVE_PATH
        if candidate.exists():
            return candidate
    return None


def load_project_config(path: Path) -> ProjectConfig:
    config_path = path.expanduser()
    if not config_path.exists():
        raise ConfigError(
            "CONFIG_NOT_FOUND",
            "Project config was not found.",
            {"path": str(config_path)},
        )
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        _invalid("TOML_INVALID", config_path, str(exc))
    except OSError as exc:
        raise ConfigError(
            "CONFIG_INVALID",
            "Project config could not be read.",
            {"path": str(config_path), "validation_code": "CONFIG_READ_FAILED", "error": str(exc)},
        ) from exc

    schema_version = _parse_schema_version(raw, config_path)
    project = _parse_project(raw, config_path)
    properties = _parse_properties(raw, config_path)
    market_defaults = _parse_market_defaults(raw, config_path)
    credential_source = _parse_credential_source(raw, config_path)
    providers = _parse_providers(raw, config_path)
    markets = _parse_markets(raw, config_path)
    competitors = _parse_competitors(raw, config_path, markets)
    sources = _parse_sources(raw, providers, config_path)
    bindings = _parse_source_bindings(raw, properties, sources, config_path)
    keyword_sets = _parse_keyword_sets(raw, config_path, markets)
    outcomes = _parse_outcomes(raw, sources, config_path)
    channels = _parse_channels(raw, config_path)
    return ProjectConfig(
        path=config_path,
        project=project,
        schema_version=schema_version,
        properties=properties,
        keyword_files=[item.path for item in keyword_sets],
        default_search_engine=market_defaults["default_search_engine"],
        default_location_code=market_defaults["default_location_code"],
        default_location_name=market_defaults["default_location_name"],
        default_language_code=market_defaults["default_language_code"],
        default_devices=market_defaults["default_devices"],
        providers=providers,
        competitors=competitors,
        competitor_config=_to_serp_competitor_config(competitors),
        sources=sources,
        source_bindings=bindings,
        keyword_sets=keyword_sets,
        outcomes=outcomes,
        markets=markets,
        credential_source=credential_source,
        channels=channels,
    )


def compute_config_hash(config: ProjectConfig) -> str:
    keyword_hashes = []
    for keyword_file in config.keyword_files:
        try:
            content = keyword_file.read_bytes()
        except OSError as exc:
            raise ConfigError(
                "CONFIG_INVALID",
                "Keyword file could not be read.",
                {
                    "path": str(config.path),
                    "keyword_file": str(keyword_file),
                    "validation_code": "KEYWORD_FILE_READ_FAILED",
                    "error": str(exc),
                },
            ) from exc
        keyword_hashes.append(
            {
                "path": _relative_to_config(keyword_file, config.path.parent),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )

    try:
        config_content_hash = hashlib.sha256(config.path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ConfigError(
            "CONFIG_INVALID",
            "Project config could not be read for hashing.",
            {"path": str(config.path), "validation_code": "CONFIG_READ_FAILED", "error": str(exc)},
        ) from exc

    payload = {
        "config_path": _committed_path(config.path),
        "config_sha256": config_content_hash,
        "keyword_files": sorted(keyword_hashes, key=lambda item: item["path"]),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProjectRegistry:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or observer_home()
        self.path = self.home / "projects.toml"

    def list(self) -> dict[str, dict[str, str]]:
        raw = self._read()
        projects = raw.get("projects", {})
        if not isinstance(projects, dict):
            raise ConfigError(
                "CONFIG_INVALID",
                "Project registry has invalid shape.",
                {"path": str(self.path), "validation_code": "REGISTRY_INVALID"},
            )
        entries: dict[str, dict[str, str]] = {}
        for name, entry in projects.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ConfigError(
                    "CONFIG_INVALID",
                    "Project registry has invalid entry.",
                    {
                        "path": str(self.path),
                        "validation_code": "REGISTRY_INVALID",
                        "project": str(name),
                    },
                )
            entries[str(name)] = {
                "path": entry["path"],
                "project": str(entry.get("project", "")),
                "repository": str(entry.get("repository", "")),
            }
        return entries

    def resolve(self, name: str) -> Path:
        entries = self.list()
        entry = entries.get(name)
        if entry is None:
            raise ConfigError(
                "PROJECT_NOT_REGISTERED",
                "Project is not registered.",
                {"project": name, "registry": str(self.path)},
            )
        return Path(entry["path"]).expanduser()

    def register(self, name: str, config_path: Path) -> dict[str, str]:
        config = load_project_config(config_path)
        repository = _repository_identity(config.path)
        with self._locked():
            entries = self.list()
            for registered_name, entry in entries.items():
                if registered_name != name and entry.get("project") == config.project.namespace:
                    raise ConfigError(
                        "PROJECT_NAMESPACE_COLLISION",
                        "Project namespace is already registered.",
                        {
                            "project": name,
                            "namespace": config.project.namespace,
                            "existing": registered_name,
                            "existing_repository": entry.get("repository", ""),
                            "repository": repository,
                        },
                    )
            entries[name] = {
                "path": str(config.path),
                "project": config.project.namespace,
                "repository": repository,
            }
            self._write(entries)
            return entries[name]

    def remove(self, name: str) -> None:
        with self._locked():
            entries = self.list()
            if name not in entries:
                raise ConfigError(
                    "PROJECT_NOT_REGISTERED",
                    "Project is not registered.",
                    {"project": name, "registry": str(self.path)},
                )
            del entries[name]
            self._write(entries)

    @contextmanager
    def _locked(self):
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            with (self.home / ".projects.toml.lock").open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            raise ConfigError(
                "CONFIG_INVALID",
                "Project registry lock could not be acquired.",
                {
                    "path": str(self.home / ".projects.toml.lock"),
                    "validation_code": "REGISTRY_LOCK_FAILED",
                    "error": str(exc),
                },
            ) from exc

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"projects": {}}
        try:
            return tomllib.loads(self.path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                "CONFIG_INVALID",
                "Project registry TOML is invalid.",
                {"path": str(self.path), "validation_code": "REGISTRY_TOML_INVALID", "error": str(exc)},
            ) from exc
        except OSError as exc:
            raise ConfigError(
                "CONFIG_INVALID",
                "Project registry could not be read.",
                {"path": str(self.path), "validation_code": "REGISTRY_READ_FAILED", "error": str(exc)},
            ) from exc

    def _write(self, entries: dict[str, dict[str, str]]) -> None:
        try:
            self.home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                "CONFIG_INVALID",
                "Project registry directory could not be created.",
                {
                    "path": str(self.home),
                    "validation_code": "REGISTRY_HOME_CREATE_FAILED",
                    "error": str(exc),
                },
            ) from exc
        lines = ["[projects]\n"]
        for name in sorted(entries):
            entry = entries[name]
            lines.append(f"\n[projects.{_toml_key(name)}]\n")
            lines.append(f"path = {_toml_string(entry['path'])}\n")
            lines.append(f"project = {_toml_string(entry['project'])}\n")
            lines.append(f"repository = {_toml_string(entry.get('repository', ''))}\n")
        try:
            self.path.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            raise ConfigError(
                "CONFIG_INVALID",
                "Project registry could not be written.",
                {"path": str(self.path), "validation_code": "REGISTRY_WRITE_FAILED", "error": str(exc)},
            ) from exc


def _parse_schema_version(raw: dict[str, Any], path: Path) -> int:
    version = raw.get("config_schema_version")
    if not _is_int(version) or version != SUPPORTED_CONFIG_SCHEMA_VERSION:
        _invalid("CONFIG_SCHEMA_VERSION_INVALID", path, version)
    return SUPPORTED_CONFIG_SCHEMA_VERSION


def _parse_project(raw: dict[str, Any], path: Path) -> ProjectIdentity:
    namespace = raw.get("project")
    if (
        not isinstance(namespace, str)
        or not namespace
        or namespace in {".", ".."}
        or Path(namespace).name != namespace
    ):
        _invalid("PROJECT_MISSING", path)
    timezone = raw.get("timezone")
    if not isinstance(timezone, str) or not timezone:
        _invalid("TIMEZONE_MISSING", path)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        _invalid("TIMEZONE_INVALID", path, timezone)
    return ProjectIdentity(namespace=namespace, timezone=timezone)


def _parse_properties(raw: dict[str, Any], path: Path) -> list[PropertyConfig]:
    properties_raw = raw.get("properties")
    if not isinstance(properties_raw, list) or not properties_raw:
        _invalid("PROPERTIES_MISSING", path)
    properties: list[PropertyConfig] = []
    seen: set[str] = set()
    for item in properties_raw:
        if not isinstance(item, dict):
            _invalid("PROPERTY_INVALID", path)
        property_id = _required_str(item, "id", path, "PROPERTY_ID_MISSING")
        if property_id in seen:
            _invalid("PROPERTY_ID_DUPLICATE", path, property_id)
        url = _required_str(item, "url", path, "PROPERTY_URL_MISSING")
        _validate_url(url, path, "PROPERTY_URL_INVALID")
        seen.add(property_id)
        properties.append(PropertyConfig(id=property_id, url=url))
    return properties


def _parse_markets(raw: dict[str, Any], path: Path) -> tuple[MarketConfig, ...]:
    markets_raw = raw.get("markets")
    if markets_raw is None:
        return ()
    if not isinstance(markets_raw, list):
        _invalid("MARKET_INVALID", path, markets_raw)
    markets: list[MarketConfig] = []
    seen: set[str] = set()
    for item in markets_raw:
        if not isinstance(item, dict):
            _invalid("MARKET_INVALID", path, item)
        market_id = _required_str(item, "id", path, "MARKET_ID_INVALID")
        if not MARKET_ID_RE.fullmatch(market_id):
            _invalid("MARKET_ID_INVALID", path, market_id)
        if market_id in seen:
            _invalid("MARKET_ID_DUPLICATE", path, market_id)
        search_engine = _required_str(item, "search_engine", path, "MARKET_SEARCH_ENGINE_INVALID")
        if search_engine not in KNOWN_MARKET_SEARCH_ENGINES:
            _invalid("MARKET_SEARCH_ENGINE_INVALID", path, search_engine)
        provider = _required_str(item, "provider", path, "MARKET_PROVIDER_INVALID")
        if provider not in KNOWN_SERP_PROVIDERS:
            _invalid("MARKET_PROVIDER_INVALID", path, provider)
        credential_env = item.get("credential_env")
        if not _valid_credential_env_name(credential_env):
            _invalid("MARKET_CREDENTIAL_ENV_INVALID", path, market_id)
        regions = _required_str_list(item, "regions", path, "MARKET_REGIONS_INVALID")
        locale = _required_str(item, "locale", path, "MARKET_LOCALE_INVALID")
        language = _required_str(item, "language", path, "MARKET_LANGUAGE_INVALID")
        devices_raw = item.get("devices", [])
        if not isinstance(devices_raw, list) or not all(
            isinstance(device, str) and device in KNOWN_MARKET_DEVICES for device in devices_raw
        ):
            _invalid("MARKET_DEVICE_INVALID", path, market_id)
        if "intent" in item:
            intent = item["intent"]
        elif len(markets_raw) == 1:
            intent = "primary"
        else:
            _invalid("MARKET_INTENT_INVALID", path, market_id)
        if not isinstance(intent, str) or intent not in KNOWN_MARKET_INTENTS:
            _invalid("MARKET_INTENT_INVALID", path, market_id)
        source_roles = item.get("source_roles", [])
        if not isinstance(source_roles, list) or not all(
            isinstance(role, str) and role for role in source_roles
        ):
            _invalid("MARKET_SOURCE_ROLES_INVALID", path, market_id)
        out_of_scope = item.get("out_of_scope", [])
        if not isinstance(out_of_scope, list) or not all(
            isinstance(scope, str) and scope in KNOWN_MARKET_OUT_OF_SCOPE_IDENTIFIERS for scope in out_of_scope
        ):
            _invalid("MARKET_OUT_OF_SCOPE_INVALID", path, market_id)
        seen.add(market_id)
        markets.append(
            MarketConfig(
                id=market_id,
                search_engine=search_engine,
                provider=provider,
                fields=dict(item),
                credential_env=str(credential_env),
                regions=tuple(regions),
                locale=locale,
                language=language,
                devices=tuple(devices_raw),
                intent=intent,
                source_roles=tuple(source_roles),
                out_of_scope=tuple(out_of_scope),
            )
        )
    return tuple(markets)


def _parse_market_defaults(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    search_engine = raw.get("default_search_engine")
    if search_engine is not None and (
        not isinstance(search_engine, str) or search_engine not in KNOWN_MARKET_SEARCH_ENGINES
    ):
        _invalid("MARKET_SEARCH_ENGINE_INVALID", path, search_engine)
    location_code = raw.get("default_location_code")
    if location_code is not None and not (
        (_is_int(location_code)) or (isinstance(location_code, str) and location_code)
    ):
        _invalid("MARKET_LOCATION_CODE_INVALID", path, location_code)
    location_name = raw.get("default_location_name")
    if location_name is not None and (not isinstance(location_name, str) or not location_name):
        _invalid("MARKET_LOCATION_NAME_INVALID", path, location_name)
    language_code = raw.get("default_language_code")
    if language_code is not None and (not isinstance(language_code, str) or not language_code):
        _invalid("MARKET_LANGUAGE_CODE_INVALID", path, language_code)
    devices_raw = raw.get("default_devices", [])
    if devices_raw is None:
        devices_raw = []
    if not isinstance(devices_raw, list) or not all(
        isinstance(item, str) and item in KNOWN_MARKET_DEVICES for item in devices_raw
    ):
        _invalid("MARKET_DEVICE_INVALID", path, devices_raw)
    return {
        "default_search_engine": search_engine,
        "default_location_code": location_code,
        "default_location_name": location_name,
        "default_language_code": language_code,
        "default_devices": tuple(devices_raw),
    }



def _parse_credential_source(raw: dict[str, Any], path: Path) -> CredentialSource:
    """Parses `[credentials]`: where the project sources env variables from.

    Paths are resolved relative to the config directory, not the working
    directory: a command can run from anywhere, and the declaration must point
    to the same place regardless of cwd.
    """

    section = raw.get("credentials")
    if section in (None, {}):
        return CredentialSource()
    if not isinstance(section, dict):
        _invalid("CREDENTIALS_INVALID", path, section)
    unknown = sorted(set(section) - {"env_file", "loader"})
    if unknown:
        _invalid("CREDENTIALS_UNKNOWN_FIELD", path, unknown[0])
    resolved: dict[str, Path | None] = {"env_file": None, "loader": None}
    for field in ("env_file", "loader"):
        value = section.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            _invalid(f"CREDENTIALS_{field.upper()}_INVALID", path, value)
        resolved[field] = (path.parent / value).resolve()
    return CredentialSource(env_file=resolved["env_file"], loader=resolved["loader"])


def _parse_providers(raw: dict[str, Any], path: Path) -> dict[str, ProviderConfig]:
    providers_raw = raw.get("providers", {})
    if providers_raw in ({}, None):
        return {}
    if not isinstance(providers_raw, dict):
        _invalid("PROVIDER_UNKNOWN", path, providers_raw)
    providers: dict[str, ProviderConfig] = {}
    for name, provider in providers_raw.items():
        provider_name = str(name)
        if provider_name not in KNOWN_PROVIDER_NAMES:
            _invalid("PROVIDER_UNKNOWN", path, provider_name)
        if not isinstance(provider, dict):
            _invalid("PROVIDER_UNKNOWN", path, provider_name)
        enabled = provider.get("enabled")
        if not isinstance(enabled, bool):
            _invalid("PROVIDER_UNKNOWN", path, provider_name)
        credential_env = provider.get("credential_env")
        if not _valid_credential_env_name(credential_env):
            _invalid("PROVIDER_CREDENTIAL_ENV_INVALID", path, provider_name)
        endpoint = provider.get("endpoint")
        if endpoint is not None and (not isinstance(endpoint, str) or not endpoint):
            _invalid("PROVIDER_UNKNOWN", path, provider_name)
        monthly_budget = _optional_budget(provider, "monthly_budget_usd", 0, 100000, path)
        per_run_budget = _optional_budget(provider, "per_run_budget_usd", 0, 1000, path)
        cache_ttl = provider.get("cache_ttl_hours")
        if cache_ttl is not None and (not _is_int(cache_ttl) or cache_ttl < 0):
            _invalid("PROVIDER_UNKNOWN", path, provider_name)
        finalize_after = provider.get("finalize_after")
        if finalize_after is not None and (not isinstance(finalize_after, str) or not finalize_after):
            _invalid("PROVIDER_UNKNOWN", path, provider_name)
        providers[provider_name] = ProviderConfig(
            name=provider_name,
            enabled=enabled,
            credential_env=credential_env,
            endpoint=endpoint,
            monthly_budget_usd=monthly_budget,
            per_run_budget_usd=per_run_budget,
            cache_ttl_hours=cache_ttl,
            finalize_after=finalize_after,
            fields=dict(provider),
        )
    return providers


def _parse_competitors(
    raw: dict[str, Any], path: Path, markets: tuple[MarketConfig, ...] = ()
) -> CompetitorsConfig:
    competitors_raw = raw.get("competitors")
    if competitors_raw is None:
        return CompetitorsConfig()
    if isinstance(competitors_raw, dict):
        if "owned_domains" in competitors_raw and competitors_raw["owned_domains"] is not None:
            owned_domains = tuple(_domain_pattern_list(competitors_raw["owned_domains"], path))
        else:
            owned_domains = ()
        items_raw = competitors_raw.get("items", [])
        if not isinstance(items_raw, list):
            _invalid("COMPETITOR_DOMAIN_INVALID", path, "items")
    elif isinstance(competitors_raw, list):
        owned_domains = ()
        items_raw = competitors_raw
    else:
        _invalid("COMPETITOR_DOMAIN_INVALID", path, competitors_raw)

    assigned_patterns: list[tuple[str, str]] = []
    for pattern in owned_domains:
        for existing_pattern, existing_identity in assigned_patterns:
            if existing_identity != "__owned__" and _domain_patterns_overlap(pattern, existing_pattern):
                _invalid("COMPETITOR_DOMAIN_INVALID", path, pattern)
        assigned_patterns.append((pattern, "__owned__"))

    items: list[CompetitorEntry] = []
    seen: set[str] = set()
    for item in items_raw:
        if not isinstance(item, dict):
            _invalid("COMPETITOR_ID_INVALID", path)
        competitor_id = _required_str(item, "id", path, "COMPETITOR_ID_INVALID")
        if not COMPETITOR_ID_RE.fullmatch(competitor_id) or competitor_id in seen:
            _invalid("COMPETITOR_ID_INVALID", path, competitor_id)
        name = _required_str(item, "name", path, "COMPETITOR_ID_INVALID")
        patterns_raw = item.get("domain_patterns", item.get("domains"))
        domain_patterns = tuple(_domain_pattern_list(patterns_raw, path))
        for pattern in domain_patterns:
            for existing_pattern, existing_identity in assigned_patterns:
                if existing_identity != competitor_id and _domain_patterns_overlap(pattern, existing_pattern):
                    _invalid("COMPETITOR_DOMAIN_INVALID", path, pattern)
            assigned_patterns.append((pattern, competitor_id))
        aliases_raw = item.get("aliases", [])
        if not isinstance(aliases_raw, list) or not all(
            isinstance(alias, str) and alias for alias in aliases_raw
        ):
            _invalid("COMPETITOR_ID_INVALID", path, competitor_id)
        competitor_class = item.get("class", "unknown")
        if not isinstance(competitor_class, str) or competitor_class not in KNOWN_COMPETITOR_CLASSES:
            _invalid("COMPETITOR_CLASS_INVALID", path, competitor_class)
        known_markets = tuple(entry.id for entry in markets)
        markets_raw = item.get("markets")
        if markets_raw is None:
            # Default is participation in all markets: a competitor may operate
            # in Russia and worldwide at the same time.
            competitor_markets = known_markets
        else:
            if not isinstance(markets_raw, list) or not all(
                isinstance(entry, str) and entry for entry in markets_raw
            ):
                _invalid("COMPETITOR_MARKET_UNKNOWN", path, competitor_id)
            for entry in markets_raw:
                if known_markets and entry not in known_markets:
                    _invalid("COMPETITOR_MARKET_UNKNOWN", path, entry)
            competitor_markets = tuple(markets_raw)
        seen.add(competitor_id)
        items.append(
            CompetitorEntry(
                id=competitor_id,
                name=name,
                domain_patterns=domain_patterns,
                aliases=tuple(aliases_raw),
                competitor_class=competitor_class,
                markets=competitor_markets,
            )
        )
    return CompetitorsConfig(owned_domains=owned_domains, items=tuple(items))


def _parse_sources(
    raw: dict[str, Any],
    providers: dict[str, ProviderConfig],
    path: Path,
) -> dict[str, SourceConfig]:
    sources_raw = raw.get("sources")
    if not isinstance(sources_raw, dict) or not sources_raw:
        _invalid("SOURCES_MISSING", path)
    sources: dict[str, SourceConfig] = {}
    for name, source in sources_raw.items():
        source_name = str(name)
        if source_name not in KNOWN_SOURCE_NAMES:
            _invalid("SOURCE_NAME_UNKNOWN", path, source_name)
        if not isinstance(source, dict):
            _invalid("SOURCE_INVALID", path, source_name)
        enabled = source.get("enabled")
        if not isinstance(enabled, bool):
            _invalid("SOURCE_ENABLED_INVALID", path, source_name)
        required = source.get("required", False)
        if not isinstance(required, bool):
            _invalid("SOURCE_REQUIRED_INVALID", path, source_name)
        _validate_source_common(source_name, source, path)
        _validate_source_specific(source_name, source, path)
        _validate_source_provider(source_name, source, providers, path)
        sources[source_name] = SourceConfig(
            name=source_name,
            enabled=enabled,
            required=required,
            fields=dict(source),
        )
    return sources


def _parse_source_bindings(
    raw: dict[str, Any],
    properties: list[PropertyConfig],
    sources: dict[str, SourceConfig],
    path: Path,
) -> list[SourceBinding]:
    bindings_raw = raw.get("source_bindings")
    if not isinstance(bindings_raw, list) or not bindings_raw:
        _invalid("SOURCE_BINDINGS_MISSING", path)
    property_ids = {item.id for item in properties}
    bindings: list[SourceBinding] = []
    seen: set[tuple[tuple[str, ...], str, str]] = set()
    for item in bindings_raw:
        if not isinstance(item, dict):
            _invalid("SOURCE_BINDING_INVALID", path)
        has_property = "property" in item
        has_properties = "properties" in item
        if has_property == has_properties:
            _invalid("SOURCE_BINDING_PROPERTY_SHAPE_INVALID", path)
        if has_property:
            value = item.get("property")
            if not isinstance(value, str) or not value:
                _invalid("SOURCE_BINDING_PROPERTY_INVALID", path, value)
            bound_properties = (value,)
        else:
            values = item.get("properties")
            if not isinstance(values, list) or not values:
                _invalid("SOURCE_BINDING_PROPERTY_INVALID", path, values)
            if not all(isinstance(value, str) and value for value in values):
                _invalid("SOURCE_BINDING_PROPERTY_INVALID", path, values)
            bound_properties = tuple(values)
        for property_id in bound_properties:
            if property_id not in property_ids:
                _invalid("SOURCE_BINDING_PROPERTY_INVALID", path, property_id)
        source = item.get("source")
        if not isinstance(source, str) or source not in sources:
            _invalid("SOURCE_BINDING_SOURCE_INVALID", path, source)
        remote_id = _required_str(item, "remote_id", path, "SOURCE_BINDING_REMOTE_ID_MISSING")
        if source == "yandex_webmaster" and not validate_host_id(remote_id, raise_error=False):
            _invalid("SOURCE_BINDING_REMOTE_ID_INVALID", path, remote_id)
        identity = (tuple(sorted(bound_properties)), source, remote_id)
        if identity in seen:
            _invalid("SOURCE_BINDING_DUPLICATE", path, source)
        seen.add(identity)
        bindings.append(SourceBinding(properties=bound_properties, source=source, remote_id=remote_id))
    return bindings


def _parse_keyword_sets(
    raw: dict[str, Any], path: Path, markets: tuple[MarketConfig, ...] = ()
) -> list[KeywordSet]:
    keyword_sets_raw = raw.get("keyword_sets")
    if not isinstance(keyword_sets_raw, list) or not keyword_sets_raw:
        _invalid("KEYWORD_SETS_MISSING", path)
    keyword_sets: list[KeywordSet] = []
    seen: set[str] = set()
    for item in keyword_sets_raw:
        if not isinstance(item, dict):
            _invalid("KEYWORD_SET_INVALID", path)
        keyword_id = _required_str(item, "id", path, "KEYWORD_SET_ID_MISSING")
        if keyword_id in seen:
            _invalid("KEYWORD_SET_ID_DUPLICATE", path, keyword_id)
        relative_path = _required_str(item, "path", path, "KEYWORD_SET_PATH_MISSING")
        keyword_file = (path.parent / relative_path).resolve()
        if not keyword_file.exists():
            _invalid("KEYWORD_FILE_NOT_FOUND", path, relative_path)
        if not keyword_file.is_file():
            _invalid("KEYWORD_FILE_INVALID", path, relative_path)
        locale = _required_str(item, "locale", path, "KEYWORD_SET_LOCALE_MISSING")
        regions = _required_str_list(item, "regions", path, "KEYWORD_SET_REGIONS_INVALID")
        devices = _required_str_list(item, "devices", path, "KEYWORD_SET_DEVICES_INVALID")
        weight_raw = item.get("weight")
        weight = None
        if weight_raw is not None:
            if not _is_number(weight_raw):
                _invalid("KEYWORD_SET_WEIGHT_INVALID", path, keyword_id)
            weight = float(weight_raw)
        market = item.get("market")
        if market is not None:
            if not isinstance(market, str) or not market:
                _invalid("KEYWORD_SET_MARKET_UNKNOWN", path, keyword_id)
            if markets and market not in {entry.id for entry in markets}:
                _invalid("KEYWORD_SET_MARKET_UNKNOWN", path, market)
        seen.add(keyword_id)
        keyword_sets.append(
            KeywordSet(
                id=keyword_id,
                path=keyword_file,
                locale=locale,
                regions=tuple(regions),
                devices=tuple(devices),
                weight=weight,
                market=market,
            )
        )
    return keyword_sets


def _parse_outcomes(
    raw: dict[str, Any], sources: dict[str, SourceConfig], path: Path
) -> list[OutcomeConfig]:
    outcomes_raw = raw.get("outcomes", [])
    if outcomes_raw == []:
        return []
    if not isinstance(outcomes_raw, list):
        _invalid("OUTCOMES_INVALID", path)
    outcomes: list[OutcomeConfig] = []
    seen: set[str] = set()
    for item in outcomes_raw:
        if not isinstance(item, dict):
            _invalid("OUTCOME_INVALID", path)
        outcome_id = _required_str(item, "id", path, "OUTCOME_ID_MISSING")
        if outcome_id in seen:
            _invalid("OUTCOME_ID_DUPLICATE", path, outcome_id)
        order = item.get("order")
        if not _is_int(order):
            _invalid("OUTCOME_ORDER_INVALID", path, outcome_id)
        truth_source = item.get("truth_source")
        if truth_source is not None and (
            not isinstance(truth_source, str) or truth_source not in sources
        ):
            _invalid("OUTCOME_TRUTH_SOURCE_INVALID", path, truth_source)
        if isinstance(truth_source, str) and truth_source.startswith("outcome_"):
            truth_view = item.get("truth_view")
            if not isinstance(truth_view, str) or not truth_view:
                _invalid("OUTCOME_TRUTH_VIEW_REQUIRED", path, outcome_id)
            approved_views = sources[truth_source].fields.get("approved_views", [])
            if truth_view not in approved_views:
                _invalid("OUTCOME_TRUTH_VIEW_NOT_APPROVED", path, truth_view)
        seen.add(outcome_id)
        outcomes.append(OutcomeConfig(id=outcome_id, order=order, fields=dict(item)))
    return outcomes


def _parse_channels(raw: dict[str, Any], path: Path) -> ChannelsConfig:
    section = raw.get("channels")
    if section is None:
        return ChannelsConfig()
    if not isinstance(section, dict):
        _invalid("CHANNELS_INVALID", path)
    unknown = sorted(set(section) - {"brand_terms", "noise_referrers"})
    if unknown:
        _invalid("CHANNELS_UNKNOWN_FIELD", path, unknown[0])
    values: dict[str, tuple[str, ...]] = {}
    for key, code in (
        ("brand_terms", "CHANNELS_BRAND_TERMS_INVALID"),
        ("noise_referrers", "CHANNELS_NOISE_REFERRERS_INVALID"),
    ):
        items = section.get(key, [])
        if not isinstance(items, list) or not all(
            isinstance(item, str) and item.strip() for item in items
        ):
            _invalid(code, path)
        values[key] = tuple(item.strip() for item in items)
    return ChannelsConfig(**values)


def _required_str(section: dict[str, Any], key: str, path: Path, validation_code: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        _invalid(validation_code, path)
    return value


def _required_str_list(
    section: dict[str, Any], key: str, path: Path, validation_code: str
) -> list[str]:
    value = section.get(key)
    if not isinstance(value, list) or not value:
        _invalid(validation_code, path)
    if not all(isinstance(item, str) and item for item in value):
        _invalid(validation_code, path, value)
    return value


def _validate_url(value: str, path: Path, validation_code: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _invalid(validation_code, path, value)


def _validate_source_common(name: str, source: dict[str, Any], path: Path) -> None:
    credential_env = source.get("credential_env")
    credential_file_env = source.get("credential_file_env")
    token_file_env = source.get("token_file_env")
    if credential_env is not None and (not isinstance(credential_env, str) or not credential_env):
        _invalid("SOURCE_CREDENTIAL_INVALID", path, name)
    if credential_file_env is not None and (
        not isinstance(credential_file_env, str) or not credential_file_env
    ):
        _invalid("SOURCE_CREDENTIAL_INVALID", path, name)
    if token_file_env is not None and (not isinstance(token_file_env, str) or not token_file_env):
        _invalid("SOURCE_CREDENTIAL_INVALID", path, name)
    finalize_after = source.get("finalize_after")
    if finalize_after is not None and (not isinstance(finalize_after, str) or not finalize_after):
        _invalid("SOURCE_FINALIZE_AFTER_INVALID", path, name)
    refresh_recent_periods = source.get("refresh_recent_periods")
    if refresh_recent_periods is not None and (
        not _is_int(refresh_recent_periods) or refresh_recent_periods < 0
    ):
        _invalid("SOURCE_REFRESH_RECENT_PERIODS_INVALID", path, name)


def _validate_source_specific(name: str, source: dict[str, Any], path: Path) -> None:
    if name == "serp":
        provider = source.get("provider")
        if not isinstance(provider, str) or provider not in KNOWN_SERP_PROVIDERS:
            _invalid("SOURCE_SERP_PROVIDER_INVALID", path, provider)
        api_family = source.get("api_family", "yandex_search_api")
        if not isinstance(api_family, str) or api_family not in KNOWN_SERP_API_FAMILIES:
            _invalid("SOURCE_SERP_API_FAMILY_INVALID", path, api_family)
        api_version = source.get("api_version")
        if api_version is not None and (not isinstance(api_version, str) or not api_version):
            _invalid("SOURCE_SERP_FIELD_INVALID", path, "api_version")
        _required_str_list(source, "regions", path, "SOURCE_SERP_REGIONS_INVALID")
        _required_str_list(source, "devices", path, "SOURCE_SERP_DEVICES_INVALID")
        for key in (
            "result_depth",
            "confirmation_observations",
            "observations_per_protocol_slot",
        ):
            value = source.get(key)
            if not _is_int(value) or value <= 0:
                _invalid("SOURCE_SERP_FIELD_INVALID", path, key)
        rank_aggregation = source.get("rank_aggregation")
        if not isinstance(rank_aggregation, str) or rank_aggregation not in KNOWN_RANK_AGGREGATIONS:
            _invalid("SOURCE_SERP_RANK_AGGREGATION_INVALID", path, rank_aggregation)
        minimum_coverage = source.get("minimum_weighted_keyword_coverage")
        if not _is_number(minimum_coverage) or not 0 <= float(minimum_coverage) <= 1:
            _invalid("SOURCE_SERP_MINIMUM_COVERAGE_INVALID", path, minimum_coverage)
    if name == "competitor_discovery":
        provider = source.get("provider")
        if provider != "dataforseo":
            _invalid("PROVIDER_UNKNOWN", path, provider)
        for key in ("limit_competitors", "gap_limit"):
            value = source.get(key)
            if value is not None and (not _is_int(value) or value <= 0):
                _invalid("SOURCE_PROVIDER_NOT_CONFIGURED", path, key)
        domains = source.get("mega_authority_domains")
        if domains is not None:
            _domain_pattern_list(domains, path)
    if name == "competitor_research":
        provider = source.get("provider")
        if provider != "exa":
            _invalid("PROVIDER_UNKNOWN", path, provider)
        num_results = source.get("num_results")
        if num_results is not None and (not _is_int(num_results) or num_results <= 0):
            _invalid("SOURCE_PROVIDER_NOT_CONFIGURED", path, "num_results")
        for key in ("include_domains", "exclude_domains"):
            domains = source.get(key)
            if domains is not None:
                _domain_pattern_list(domains, path)
    if name == "wordstat":
        api_family = source.get("api_family", "search_api_wordstat")
        if not isinstance(api_family, str) or api_family not in KNOWN_WORDSTAT_API_FAMILIES:
            _invalid("SOURCE_WORDSTAT_API_FAMILY_INVALID", path, api_family)
        for key in ("api_version", "locale", "language", "endpoint"):
            value = source.get(key)
            if value is not None and (not isinstance(value, str) or not value):
                _invalid("SOURCE_WORDSTAT_FIELD_INVALID", path, key)
        supports_devices = source.get("supports_devices")
        if supports_devices is not None and not isinstance(supports_devices, bool):
            _invalid("SOURCE_WORDSTAT_SUPPORTS_DEVICES_INVALID", path, supports_devices)
    if name == "ga4":
        timezone = source.get("timezone")
        if timezone is not None and (not isinstance(timezone, str) or not timezone):
            _invalid("SOURCE_GA4_TIMEZONE_INVALID", path, name)
    if name.startswith("outcome_"):
        adapter = source.get("adapter")
        if adapter is not None and (not isinstance(adapter, str) or not adapter):
            _invalid("SOURCE_OUTCOME_ADAPTER_INVALID", path, name)
        if adapter is not None and adapter not in KNOWN_OUTCOME_AGGREGATE_ADAPTERS:
            _invalid("SOURCE_OUTCOME_ADAPTER_INVALID", path, adapter)
        if any(key in source for key in FORBIDDEN_OUTCOME_QUERY_FIELDS):
            _invalid("SOURCE_OUTCOME_QUERY_TEXT_FORBIDDEN", path, name)
        approved_views = source.get("approved_views")
        if (
            not isinstance(approved_views, list)
            or not approved_views
            or not all(isinstance(item, str) and item for item in approved_views)
        ):
            _invalid("SOURCE_OUTCOME_APPROVED_VIEWS_INVALID", path, name)
        parameter_names = source.get("parameter_names")
        if parameter_names is not None and (
            not isinstance(parameter_names, list)
            or set(parameter_names) != {"period_start", "period_end"}
            or len(parameter_names) != 2
        ):
            _invalid("SOURCE_OUTCOME_PARAMETERS_INVALID", path, name)
        if adapter == "http_aggregate":
            for key in (
                "endpoint_env",
                "credential_env",
                "outcome_id",
                "counting_unit",
                "dedupe_key",
                "timestamp_field",
            ):
                value = source.get(key)
                if not isinstance(value, str) or not value:
                    _invalid("SOURCE_OUTCOME_HTTP_FIELD_MISSING", path, key)
            if (
                isinstance(approved_views, list)
                and approved_views
                and all(isinstance(item, str) and item for item in approved_views)
                and len(approved_views) != 1
            ):
                _invalid("SOURCE_OUTCOME_HTTP_VIEWS_INVALID", path, name)


def _validate_source_provider(
    name: str,
    source: dict[str, Any],
    providers: dict[str, ProviderConfig],
    path: Path,
) -> None:
    provider = source.get("provider")
    if name in {"competitor_discovery", "competitor_research"}:
        if not isinstance(provider, str) or provider not in providers:
            _invalid("SOURCE_PROVIDER_NOT_CONFIGURED", path, provider)
    if name == "serp" and provider == "dataforseo_google_organic" and "dataforseo" not in providers:
        _invalid("SOURCE_PROVIDER_NOT_CONFIGURED", path, provider)


def _optional_budget(
    section: dict[str, Any],
    key: str,
    minimum: float,
    maximum: float,
    path: Path,
) -> float | None:
    value = section.get(key)
    if value is None:
        return None
    if not _is_number(value):
        _invalid("BUDGET_INVALID", path, key)
    budget = float(value)
    if not minimum <= budget <= maximum:
        _invalid("BUDGET_INVALID", path, key)
    return budget


def _valid_credential_env_name(value: Any) -> bool:
    return isinstance(value, str) and bool(PROVIDER_CREDENTIAL_ENV_RE.fullmatch(value))


def _domain_pattern_list(value: Any, path: Path) -> list[str]:
    if not isinstance(value, list) or not value:
        _invalid("COMPETITOR_DOMAIN_INVALID", path, value)
    patterns: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _valid_domain_pattern(item):
            _invalid("COMPETITOR_DOMAIN_INVALID", path, item)
        patterns.append(item)
    return patterns


def _valid_domain_pattern(value: str) -> bool:
    if value != value.lower() or not value or not value.isascii():
        return False
    if "://" in value or "/" in value or "?" in value or "#" in value or ":" in value or "@" in value:
        return False
    host = value[2:] if value.startswith("*.") else value
    if not host or host.startswith(".") or host.endswith(".") or ".." in host:
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return False
    label_re = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    return all(bool(label_re.fullmatch(label)) for label in labels)


def _domain_patterns_overlap(p1: str, p2: str) -> bool:
    p1 = p1.casefold().strip()
    p2 = p2.casefold().strip()
    if p1 == p2:
        return True
    is_w1 = p1.startswith("*.")
    is_w2 = p2.startswith("*.")
    if is_w1 and is_w2:
        d1 = p1[2:]
        d2 = p2[2:]
        return d1 == d2 or d2.endswith(f".{d1}") or d1.endswith(f".{d2}")
    if is_w1 and not is_w2:
        d1 = p1[2:]
        return p2.endswith(f".{d1}") and p2 != d1
    if not is_w1 and is_w2:
        d2 = p2[2:]
        return p1.endswith(f".{d2}") and p1 != d2
    return False


def _to_serp_competitor_config(config: CompetitorsConfig) -> CompetitorConfig:
    return CompetitorConfig(
        owned_domains=config.owned_domains,
        competitors=tuple(
            Competitor(
                id=item.id,
                name=item.name,
                domain_patterns=item.domain_patterns,
                aliases=item.aliases,
                competitor_class=item.competitor_class,
                markets=item.markets,
            )
            for item in config.items
        ),
    )


def _invalid(validation_code: str, path: Path, value: Any | None = None) -> None:
    details: dict[str, Any] = {"path": str(path), "validation_code": validation_code}
    if value is not None:
        details["value"] = value
    raise ConfigError("CONFIG_INVALID", "Project config is invalid.", details)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _relative_to_config(path: Path, config_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(), config_dir.resolve())).as_posix()


def _committed_path(path: Path) -> str:
    resolved = path.resolve()
    for directory in (resolved.parent, *resolved.parent.parents):
        if (directory / ".git").exists():
            return resolved.relative_to(directory).as_posix()
    return str(resolved)


def _repository_identity(path: Path) -> str:
    resolved = path.resolve()
    project_root = resolved.parent.parent
    if (project_root / ".git").exists():
        return str(project_root)
    return str(project_root.resolve())


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_key(value: str) -> str:
    if value and all(char.isascii() and (char.isalnum() or char in "_-") for char in value):
        return value
    return _toml_string(value)
