"""Local, read-only HTTP transport for the SEO Hub API and UI."""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sys
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from seo_hub.auth import AuthError, local_noauth_principal, principal_from_cloudflare_access_jwt
from seo_hub.client import APIError, HubAPI
from seo_hub.errors import SeoHubError
from seo_hub.registry import Registry, load_registry
from seo_hub.store import RunStore
from seo_hub.web import render_index, render_project, render_run


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class RouteError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def _identifier(value: str) -> str:
    if not _ID.fullmatch(value):
        raise RouteError(400, "Invalid project or run ID")
    return value


class HTTPRunStore(RunStore):
    """Reject stored links and mismatched manifest identities before HTTP reads."""

    def list_manifests(self, project_id: str):
        project_dir = self.project_dir(project_id)
        runs_dir = project_dir / "runs"
        self._check_paths([self.data_dir, project_dir, runs_dir])
        for run_dir in runs_dir.glob("*"):
            self._check_paths([run_dir, run_dir / "manifest.json", run_dir / "observer"])
            self._check_paths(run_dir.glob("observer/*"))
        manifests = super().list_manifests(project_id)
        for manifest in manifests:
            _identifier(manifest.run_id)
            if manifest.project_id != project_id:
                raise RouteError(400, "Stored manifest project does not match its directory")
        return manifests

    @staticmethod
    def _check_paths(paths) -> None:
        for path in paths:
            if path.is_symlink():
                raise RouteError(400, "Unsafe stored path: symbolic links are not served")


def create_server(registry: Registry, *, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    """Create a local listener or a JWT-protected private/container listener."""
    auth_mode = registry.server.auth_mode
    if auth_mode not in {"local_noauth", "cloudflare_access"}:
        raise AuthError("Unsupported server auth_mode")
    if auth_mode == "local_noauth" and host not in _LOOPBACK:
        raise AuthError("local_noauth HTTP server may only bind to loopback.")
    if host not in _LOOPBACK:
        try:
            private = ipaddress.ip_address(host).is_private
        except ValueError:
            private = False
        if not private:
            raise AuthError("HTTP server must bind to a loopback or private IP address.")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    # Resolve localhost ourselves so a hosts/DNS override cannot widen the bind.
    host = "127.0.0.1" if host == "localhost" else host
    bind = f"[{host}]:{port}" if host == "::1" else f"{host}:{port}"
    local_registry = replace(registry, server=replace(registry.server, bind=bind))
    local_principal = local_noauth_principal(local_registry) if auth_mode == "local_noauth" else None
    api = HubAPI(registry=local_registry, store=HTTPRunStore(registry.data_dir))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._handle()

        def do_HEAD(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._error(405, "METHOD_NOT_ALLOWED", "Only GET and HEAD are supported")

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST
        do_OPTIONS = do_POST

        def _handle(self) -> None:
            try:
                if auth_mode == "local_noauth":
                    self._check_authority()
                payload, content_type = self._route()
                self._send(200, payload, content_type)
            except RouteError as exc:
                self._error(exc.status, "HTTP_ERROR", str(exc))
            except AuthError as exc:
                self._error(403, exc.code, str(exc))
            except APIError as exc:
                self._error(404, exc.code, str(exc))
            except SeoHubError as exc:
                self._error(500, exc.code, str(exc))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.log_error("Failed to read Hub data: %s", exc)
                self._error(500, "DATA_READ_FAILED", "Failed to read Hub data; check the local server log")

        def _check_authority(self) -> None:
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or not self._local_authority(hosts[0]):
                raise RouteError(403, "Host must address this loopback server")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise RouteError(403, "Cross-site requests are not allowed")
            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                except ValueError as exc:
                    raise RouteError(403, "Invalid Origin") from exc
                if parsed.scheme != "http" or parsed.path or parsed.query or parsed.fragment or not self._local_authority(parsed.netloc):
                    raise RouteError(403, "Origin must address this loopback server")

        def _local_authority(self, authority: str) -> bool:
            try:
                parsed = urlsplit("http://" + authority)
                return (
                    parsed.hostname in _LOOPBACK
                    and (parsed.port or 80) == self.server.server_port
                    and parsed.username is None and parsed.password is None
                    and not parsed.path and not parsed.query and not parsed.fragment
                )
            except ValueError:
                return False

        def _route(self):
            if not self.path.startswith("/") or self.path.startswith("//"):
                raise RouteError(400, "Invalid request path")
            try:
                parsed = urlsplit(self.path)
            except ValueError as exc:
                raise RouteError(400, "Invalid request path") from exc
            if parsed.fragment or re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path):
                raise RouteError(400, "Invalid request path")
            try:
                parts = [unquote(part, errors="strict") for part in parsed.path.split("/")[1:]]
                query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=10)
            except ValueError as exc:
                raise RouteError(400, "Invalid request path or query") from exc
            if set(query) - {"run_id"} or any(len(values) != 1 for values in query.values()):
                raise RouteError(400, "Unsupported or duplicate query parameter")
            run_id = _identifier(query["run_id"][0]) if "run_id" in query else None
            if parts == ["health"] or parts == ["healthz"]:
                return {"ok": True}, "application/json"
            principal = self._principal(is_api=parts[0] == "api")
            if parts == ["static", "app.css"]:
                return (Path(__file__).parent / "static" / "app.css").read_text(encoding="utf-8"), "text/css"
            if parts == [""]:
                return render_index(api, principal), "text/html"
            is_api = parts[0] == "api"
            route = parts[1:] if is_api else parts
            if is_api and route == ["projects"]:
                return api.projects(principal), "application/json"
            if len(route) < 2 or route[0] != "projects":
                raise RouteError(404, "Route not found")
            project_id = _identifier(route[1])
            if not any(project.id == project_id for project in registry.projects):
                raise RouteError(404, "Project not found")
            if len(route) == 4 and route[2] == "runs":
                selected_run = _identifier(route[3])
                if is_api:
                    return api.report(project_id, principal, run_id=selected_run), "application/json"
                return render_run(api, principal, project_id, selected_run), "text/html"
            if len(route) == 2:
                if is_api:
                    return api.project_status(project_id, principal), "application/json"
                return render_project(api, principal, project_id), "text/html"
            if is_api and len(route) == 3:
                action = route[2]
                if action in {"status", "runs"}:
                    return api.project_status(project_id, principal), "application/json"
                if action == "report":
                    return api.report(project_id, principal, run_id=run_id), "application/json"
                if action == "opportunities":
                    return api.opportunities(project_id, principal, run_id=run_id), "application/json"
                if action == "outcomes":
                    return api.outcomes(project_id, principal), "application/json"
            raise RouteError(404, "Route not found")

        def _principal(self, *, is_api: bool):
            if local_principal is not None:
                return local_principal
            assertions = self.headers.get_all("Cf-Access-Jwt-Assertion", [])
            if len(assertions) != 1 or not assertions[0].strip():
                raise AuthError("A verified Cloudflare Access JWT assertion is required.")
            audiences = [registry.server.human_audience]
            if is_api:
                audiences.insert(0, registry.server.service_audience)
            for audience in audiences:
                try:
                    return principal_from_cloudflare_access_jwt(
                        assertions[0], registry=registry, audience=audience,
                    )
                except AuthError:
                    continue
            raise AuthError("Cloudflare Access JWT verification failed.")

        def _error(self, status: int, code: str, message: str) -> None:
            self._send(status, {"ok": False, "error": {"code": code, "message": message}}, "application/json")

        def _send(self, status: int, payload, content_type: str) -> None:
            text = json.dumps(payload, ensure_ascii=False) if content_type == "application/json" else payload
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            if status == 405:
                self.send_header("Allow", "GET, HEAD")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        daemon_threads = True

    return Server((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only SEO Hub HTTP server")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.config)
        with create_server(registry, host=args.host, port=args.port) as server:
            bound_host, bound_port = server.server_address[:2]
            url_host = f"[{bound_host}]" if ":" in bound_host else bound_host
            print(f"SEO Hub: http://{url_host}:{bound_port} ({registry.server.auth_mode}, read-only)", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (SeoHubError, OSError, ValueError) as exc:
        print(f"oxi-seo server: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
