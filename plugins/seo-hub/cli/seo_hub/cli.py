from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from seo_hub import __version__
from seo_hub.auth import local_noauth_principal
from seo_hub.client import APIError, HubAPI, LocalHubClient
from seo_hub.errors import SeoHubError
from seo_hub.exports import ExportError
from seo_hub.modes import allowed_modes
from seo_hub.registry import Registry, RegistryError, load_registry
from seo_hub.store import StoreError


DEFAULT_CONFIG_NAMES = (Path("seo-hub.toml"), Path(".seo-hub") / "seo-hub.toml")


class _JsonAwareArgumentParser(argparse.ArgumentParser):
    argv: list[str] = []

    def error(self, message: str) -> None:
        as_json = "--json" in self.argv
        exit_code = _print_error("ARGUMENT_ERROR", message, as_json=as_json)
        raise SystemExit(exit_code)


def _common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--config", default=argparse.SUPPRESS, help="path to seo-hub.toml")
    return common


def _discover_config(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "config", None)
    if explicit:
        return Path(explicit).expanduser()
    env_path = os.environ.get("SEO_HUB_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    for name in DEFAULT_CONFIG_NAMES:
        candidate = Path.cwd() / name
        if candidate.exists():
            return candidate
    user_config = Path.home() / ".seo-hub" / "seo-hub.toml"
    if user_config.exists():
        return user_config
    return Path.cwd() / DEFAULT_CONFIG_NAMES[0]


def _dump(payload: dict[str, Any], *, stream) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream)


def _error_payload(code: str, message: str, **details: object) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "details": details}}


def _print_error(code: str, message: str, *, as_json: bool, exit_code: int = 2, **details: object) -> int:
    if as_json:
        _dump(_error_payload(code, message, **details), stream=sys.stderr)
    else:
        print(f"{code}: {message}", file=sys.stderr)
    return exit_code


def _registry_or_error(args: argparse.Namespace, *, as_json: bool) -> Registry | int:
    config_path = _discover_config(args)
    if not config_path.exists():
        return _print_error(
            "CONFIG_NOT_FOUND",
            f"SEO Hub config not found: {config_path}",
            as_json=as_json,
            path=str(config_path),
        )
    try:
        return load_registry(config_path)
    except SeoHubError as exc:
        return _print_error(exc.code, exc.message, as_json=as_json, **exc.details)


def _check_project_file(path: Path, *, require_mode_0600: bool) -> dict[str, Any]:
    if path.is_symlink():
        return {"ok": False, "path": str(path), "error": "must not be a symlink"}
    try:
        st = path.stat()
    except OSError as exc:
        return {"ok": False, "path": str(path), "error": f"not readable: {exc}"}
    if not stat.S_ISREG(st.st_mode):
        return {"ok": False, "path": str(path), "error": "must be a regular file"}
    if require_mode_0600 and stat.S_IMODE(st.st_mode) != 0o600:
        return {"ok": False, "path": str(path), "error": "must have mode 0600"}
    return {"ok": True, "path": str(path)}


def _check_registry_project_files(registry: Registry) -> dict[str, Any]:
    projects: dict[str, Any] = {}
    ok = True
    for project in registry.projects:
        observer_config = (
            _check_project_file(project.observer_config, require_mode_0600=False)
            if project.observer_config else {"ok": True, "configured": False}
        )
        credentials_env_file = (
            _check_project_file(project.credentials_env_file, require_mode_0600=True)
            if project.credentials_env_file else {"ok": True, "configured": False}
        )
        project_ok = observer_config["ok"] and credentials_env_file["ok"]
        provider_credentials = (
            _check_project_file(project.provider_credentials_env_file, require_mode_0600=True)
            if project.provider_credentials_env_file else {"ok": True, "configured": False}
        )
        project_ok = project_ok and provider_credentials["ok"]
        ok = ok and project_ok
        projects[project.id] = {
            "ok": project_ok,
            "observer_config": observer_config,
            "credentials_env_file": credentials_env_file,
            "provider_credentials_env_file": provider_credentials,
        }
    return {"ok": ok, "projects": projects}


def _doctor(args: argparse.Namespace) -> int:
    config_path = _discover_config(args)
    config_check: dict[str, Any]
    if not config_path.exists():
        config_check = {
            "ok": False,
            "status": "not_ready",
            "path": str(config_path),
            "exists": False,
            "required": False,
        }
    else:
        try:
            registry = load_registry(config_path)
            project_files = _check_registry_project_files(registry)
            config_check = {
                "ok": project_files["ok"],
                "status": "ready" if project_files["ok"] else "invalid",
                "path": str(registry.path),
                "exists": True,
                "projects": len(registry.projects),
                "project_files": project_files["projects"],
            }
        except SeoHubError as exc:
            config_check = {
                "ok": False,
                "status": "invalid",
                "path": str(config_path),
                "exists": True,
                "error": {"code": exc.code, "message": exc.message},
            }
    payload = {
        "ok": config_check["ok"] or not config_check["exists"],
        "cli": "seo-hub",
        "version": __version__,
        "checks": {
            "cli": {"ok": True, "binary": "seo-hub", "package": "seo-hub"},
            "config": config_check,
        },
    }
    if getattr(args, "json", False):
        _dump(payload, stream=sys.stdout)
    else:
        status = payload["checks"]["config"]["status"]
        print(f"seo-hub {__version__}: config {status}")
    return 0 if payload["ok"] else 2


def _projects(args: argparse.Namespace) -> int:
    return _api_command(args, "projects")


def _api_client(args: argparse.Namespace) -> LocalHubClient | int:
    registry = _registry_or_error(args, as_json=getattr(args, "json", False))
    if isinstance(registry, int):
        return registry
    try:
        principal = local_noauth_principal(registry)
    except SeoHubError as exc:
        return _print_error(exc.code, exc.message, as_json=getattr(args, "json", False), **exc.details)
    return LocalHubClient(api=HubAPI(registry=registry), principal=principal)


def _api_command(args: argparse.Namespace, command: str) -> int:
    client = _api_client(args)
    if isinstance(client, int):
        return client
    try:
        payload = client.request(command, **_api_kwargs(args))
    except SeoHubError as exc:
        return _print_error(exc.code, exc.message, as_json=getattr(args, "json", False), **exc.details)
    if getattr(args, "json", False):
        _dump(payload, stream=sys.stdout)
    else:
        _print_human(command, payload)
    return 0


def _api_kwargs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "project_id": getattr(args, "project", None),
        "run_id": getattr(args, "run", None),
        "mode": getattr(args, "mode", None),
        "format_name": getattr(args, "format", None),
        "profile_name": getattr(args, "profile", None),
        "idempotency_key": getattr(args, "idempotency_key", None),
    }


def _print_human(command: str, payload: dict[str, Any]) -> None:
    if command == "projects":
        for project in payload["projects"]:
            print(f"{project['id']}\t{project['label']}")
    elif command == "status":
        if "projects" in payload:
            for project in payload["projects"]:
                print(f"{project['id']}\t{project['label']}")
        else:
            project = payload.get("project", {})
            latest = payload.get("latest_run") or {}
            print(f"{project.get('id')}\t{latest.get('state', 'no-runs')}")
    elif command == "run":
        run = payload["run"]
        print(f"{run['project_id']}\t{run['run_id']}\t{run['state']}")
    elif command in {"report", "opportunities", "outcomes"}:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif command == "export":
        print(payload["export"]["path"])


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = _JsonAwareArgumentParser(description="Read-only SEO intelligence hub CLI.", parents=[common])
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=_JsonAwareArgumentParser)

    doctor = subparsers.add_parser("doctor", parents=[common], help="check local CLI/config readiness")
    doctor.set_defaults(handler=_doctor)

    projects = subparsers.add_parser("projects", parents=[common], help="list configured projects")
    projects.set_defaults(handler=_projects)

    status = subparsers.add_parser("status", parents=[common], help="show project status")
    status.add_argument("--project")
    status.set_defaults(handler=lambda args: _api_command(args, "status"))

    run = subparsers.add_parser("run", parents=[common], help="read/import existing evidence")
    run.add_argument("--project", required=True)
    run.add_argument("--mode", required=True, choices=allowed_modes())
    run.add_argument("--idempotency-key")
    run.set_defaults(handler=lambda args: _api_command(args, "run"))

    for name in ("report", "opportunities"):
        command = subparsers.add_parser(name, parents=[common])
        command.add_argument("--project", required=True)
        command.add_argument("--run")
        command.set_defaults(handler=lambda args, command_name=name: _api_command(args, command_name))

    outcomes = subparsers.add_parser("outcomes", parents=[common])
    outcomes.add_argument("--project", required=True)
    outcomes.set_defaults(handler=lambda args: _api_command(args, "outcomes"))

    export = subparsers.add_parser("export", parents=[common])
    export.add_argument("--project", required=True)
    export.add_argument("--run", required=True)
    export.add_argument("--format", required=True, choices=("json", "markdown", "observer-actions"))
    export.add_argument("--profile")
    export.set_defaults(handler=lambda args: _api_command(args, "export"))
    return parser


def main(argv: list[str] | None = None) -> int:
    resolved_argv = sys.argv[1:] if argv is None else argv
    _JsonAwareArgumentParser.argv = resolved_argv
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.handler(args))
    except RegistryError as exc:
        return _print_error(exc.code, exc.message, as_json="--json" in resolved_argv, **exc.details)
    except (APIError, ExportError, StoreError) as exc:
        return _print_error(exc.code, exc.message, as_json="--json" in resolved_argv, **exc.details)


if __name__ == "__main__":
    raise SystemExit(main())
