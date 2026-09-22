from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from seo_hub.errors import SeoHubError


class RegistryError(SeoHubError, ValueError):
    code = "REGISTRY_INVALID"


@dataclass(frozen=True)
class Server:
    bind: str
    auth_mode: str
    owner_email: str
    access_team_domain: str | None = None
    human_audience: str | None = None
    service_audience: str | None = None
    service_common_name: str | None = None


@dataclass(frozen=True)
class ProjectExport:
    name: str
    format: str
    relative_dir: Path


@dataclass(frozen=True)
class Project:
    id: str
    label: str
    observer_config: Path | None
    credentials_env_file: Path | None
    elmo_base_url: str | None
    elmo_brand_id: str | None
    openseo_mcp_url: str | None
    openseo_project_id: str | None
    openseo_rank_tracker_id: str | None
    exports: list[ProjectExport]
    provider_credentials_env_file: Path | None = None
    elmo_organization_slug: str | None = None


@dataclass(frozen=True)
class Registry:
    schema_version: int
    path: Path
    data_dir: Path
    export_dir: Path
    server: Server | None
    projects: list[Project]


TOP_LEVEL_KEYS = {"schema_version", "data_dir", "export_dir", "server", "projects"}
TOP_LEVEL_REQUIRED_KEYS = TOP_LEVEL_KEYS - {"server"}
SERVER_BASE_KEYS = {"bind", "auth_mode", "owner_email"}
SERVER_CLOUDFLARE_KEYS = {
    "access_team_domain",
    "human_audience",
    "service_audience",
    "service_common_name",
}
SERVER_KEYS = SERVER_BASE_KEYS | SERVER_CLOUDFLARE_KEYS
AUTH_MODES = {"local_noauth", "cloudflare_access"}
PROJECT_KEYS = {
    "id",
    "label",
    "observer_config",
    "credentials_env_file",
    "elmo_base_url",
    "elmo_brand_id",
    "openseo_mcp_url",
    "openseo_project_id",
    "openseo_rank_tracker_id",
    "exports",
    "provider_credentials_env_file",
    "elmo_organization_slug",
}
PROJECT_REQUIRED_KEYS = {"id", "label"}
ELMO_KEY_PAIR = {"elmo_base_url", "elmo_brand_id"}
OPENSEO_KEY_PAIR = {"openseo_mcp_url", "openseo_project_id"}
EXPORT_KEYS = {"name", "format", "relative_dir"}
EXPORT_FORMATS = {"json", "markdown", "observer-actions"}


def _unknown_keys(scope: str, data: dict[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise RegistryError(f"unknown key in {scope}: {', '.join(extra)}")


def _required_keys(scope: str, data: dict[str, Any], required: set[str]) -> None:
    missing = sorted(required - set(data))
    if missing:
        raise RegistryError(f"missing required key in {scope}: {', '.join(missing)}")


def _string(scope: str, data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{scope}.{key} must be a non-empty string")
    return value


def _resolve_path(registry_dir: Path, registry_root: Path, value: str, key: str) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        raise RegistryError(f"{key} must be a relative path")
    registry_root = registry_root.resolve()
    if registry_root.parent == registry_root:
        raise RegistryError("registry root must not be filesystem root")
    resolved = registry_dir
    for part in raw.parts:
        if part in ("", "."):
            continue
        if part == "..":
            resolved = resolved.parent
        else:
            resolved = resolved / part
    try:
        resolved.relative_to(registry_root)
    except ValueError as exc:
        raise RegistryError(f"{key} escapes registry root: {value}") from exc
    relative = resolved.relative_to(registry_root)
    current = registry_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RegistryError(f"{key} must not contain symlink: {value}")
    return resolved


def _relative_dir(value: str, key: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RegistryError(f"{key} must be a non-escaping relative path")
    if str(path) in ("", "."):
        raise RegistryError(f"{key} must not be empty")
    return path


def _parse_server(data: Any) -> Server:
    if not isinstance(data, dict):
        raise RegistryError("server must be a table")
    _unknown_keys("server", data, SERVER_KEYS)
    _required_keys("server", data, SERVER_BASE_KEYS)
    auth_mode = _string("server", data, "auth_mode")
    if auth_mode not in AUTH_MODES:
        raise RegistryError(f"server.auth_mode must be one of {', '.join(sorted(AUTH_MODES))}")
    if auth_mode == "cloudflare_access":
        _required_keys("server", data, SERVER_CLOUDFLARE_KEYS)
    return Server(
        **{
            key: _string("server", data, key)
            for key in sorted(SERVER_KEYS & set(data))
        }
    )


def _parse_exports(project_index: int, raw_exports: Any) -> list[ProjectExport]:
    if raw_exports is None:
        return []
    if not isinstance(raw_exports, list):
        raise RegistryError(f"projects[{project_index}].exports must be a list")
    exports = []
    names: set[str] = set()
    for export_index, item in enumerate(raw_exports):
        scope = f"projects[{project_index}].exports[{export_index}]"
        if not isinstance(item, dict):
            raise RegistryError(f"{scope} must be a table")
        _unknown_keys(scope, item, EXPORT_KEYS)
        _required_keys(scope, item, EXPORT_KEYS)
        name = _string(scope, item, "name")
        if name in names:
            raise RegistryError(f"duplicate export name in project: {name}")
        names.add(name)
        fmt = _string(scope, item, "format")
        if fmt not in EXPORT_FORMATS:
            raise RegistryError(f"{scope}.format must be one of {', '.join(sorted(EXPORT_FORMATS))}")
        exports.append(
            ProjectExport(
                name=name,
                format=fmt,
                relative_dir=_relative_dir(_string(scope, item, "relative_dir"), f"{scope}.relative_dir"),
            )
        )
    return exports


def _parse_projects(raw_projects: Any, registry_dir: Path, registry_root: Path) -> list[Project]:
    if not isinstance(raw_projects, list) or not raw_projects:
        raise RegistryError("projects must be a non-empty list")
    projects = []
    ids: set[str] = set()
    for index, item in enumerate(raw_projects):
        scope = f"projects[{index}]"
        if not isinstance(item, dict):
            raise RegistryError(f"{scope} must be a table")
        _unknown_keys(scope, item, PROJECT_KEYS)
        _required_keys(scope, item, PROJECT_REQUIRED_KEYS)
        project_id = _string(scope, item, "id")
        if project_id in ids:
            raise RegistryError(f"duplicate project id: {project_id}")
        ids.add(project_id)
        for pair in (ELMO_KEY_PAIR, OPENSEO_KEY_PAIR):
            present = pair & set(item)
            if present and present != pair:
                missing = sorted(pair - present)
                raise RegistryError(
                    f"{scope}: {' and '.join(sorted(pair))} must be set together; missing: {', '.join(missing)}"
                )
        projects.append(
            Project(
                id=project_id,
                label=_string(scope, item, "label"),
                observer_config=(
                    _resolve_path(
                        registry_dir,
                        registry_root,
                        _string(scope, item, "observer_config"),
                        f"{scope}.observer_config",
                    )
                    if "observer_config" in item
                    else None
                ),
                credentials_env_file=(
                    _resolve_path(
                        registry_dir,
                        registry_root,
                        _string(scope, item, "credentials_env_file"),
                        f"{scope}.credentials_env_file",
                    )
                    if "credentials_env_file" in item
                    else None
                ),
                elmo_base_url=(
                    _string(scope, item, "elmo_base_url") if "elmo_base_url" in item else None
                ),
                elmo_brand_id=(
                    _string(scope, item, "elmo_brand_id") if "elmo_brand_id" in item else None
                ),
                openseo_mcp_url=(
                    _string(scope, item, "openseo_mcp_url") if "openseo_mcp_url" in item else None
                ),
                openseo_project_id=(
                    _string(scope, item, "openseo_project_id") if "openseo_project_id" in item else None
                ),
                openseo_rank_tracker_id=(
                    _string(scope, item, "openseo_rank_tracker_id")
                    if "openseo_rank_tracker_id" in item
                    else None
                ),
                exports=_parse_exports(index, item.get("exports")),
                elmo_organization_slug=(
                    _string(scope, item, "elmo_organization_slug")
                    if "elmo_organization_slug" in item else None
                ),
                provider_credentials_env_file=(
                    _resolve_path(registry_dir, registry_root,
                                  _string(scope, item, "provider_credentials_env_file"),
                                  f"{scope}.provider_credentials_env_file")
                    if "provider_credentials_env_file" in item else None
                ),
            )
        )
    return projects


def load_registry(path: str | Path) -> Registry:
    registry_path = Path(path).expanduser()
    try:
        data = tomllib.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"registry config not found: {registry_path}", path=str(registry_path)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"invalid TOML in registry {registry_path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise RegistryError(f"invalid encoding in registry {registry_path}: {exc}") from exc
    except OSError as exc:
        raise RegistryError(f"failed to read registry {registry_path}: {exc}") from exc

    _unknown_keys("registry", data, TOP_LEVEL_KEYS)
    _required_keys("registry", data, TOP_LEVEL_REQUIRED_KEYS)
    schema_version = data["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != 1:
        raise RegistryError("schema_version must be 1")
    registry_dir = registry_path.resolve().parent
    registry_root = registry_dir.parent
    return Registry(
        schema_version=1,
        path=registry_path.resolve(),
        data_dir=_resolve_path(registry_dir, registry_root, _string("registry", data, "data_dir"), "data_dir"),
        export_dir=_resolve_path(registry_dir, registry_root, _string("registry", data, "export_dir"), "export_dir"),
        server=_parse_server(data["server"]) if "server" in data else None,
        projects=_parse_projects(data["projects"], registry_dir, registry_root),
    )
