"""Read-only HTTP server for the tenant runtime.

Serves the ``exports/`` tree produced by ``seo-observer export`` plus a
``/health`` endpoint backed by :func:`runtime_health.evaluate_health`. There
is no authentication here by design: network access is granted exclusively
by Cloudflare Access in front of this server, so the handler is hardened
against path escapes instead — every request path is normalized, dot
segments and ``failed/`` are refused, and the resolved real path must stay
inside the real exports root.
"""

from __future__ import annotations

import json
import os
import posixpath
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from seo_observer.runtime_health import evaluate_health

_CHUNK = 64 * 1024

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def _resolve_request_path(exports_root: Path, raw_path: str) -> tuple[Path | None, bool]:
    """Map a decoded URL path to a file inside ``exports_root``.

    Returns ``(candidate, rejected)``: ``rejected`` marks a path that must be
    answered with 404 without touching the filesystem any further.
    """
    # raw_path starts with exactly one "/" (verified by the caller); strip it
    # before normpath so a ".." escape stays ".." instead of collapsing into
    # the root and being silently served.
    rel = posixpath.normpath(raw_path[1:])
    if rel == ".":
        rel = ""
    parts = [part for part in rel.split("/") if part]
    if any(part == ".." or part.startswith(".") or "\x00" in part for part in parts):
        return None, True
    if parts and parts[0] == "failed":
        return None, True
    candidate = Path(exports_root) / rel if rel else Path(exports_root)
    real = _contained_real_path(candidate, exports_root)
    if real is None:
        return None, True
    return real, False


def _contained_real_path(path: Path, exports_root: Path) -> Path | None:
    """Resolve ``path`` and return it only if it stays inside ``exports_root``."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return None
    root = os.path.realpath(exports_root)
    if real != root and not real.startswith(root + os.sep):
        return None
    return Path(real)


def make_server(*, host: str, port: int, exports_dir: Path, state_file: Path,
                clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> ThreadingHTTPServer:
    exports_dir = Path(exports_dir)
    state_file = Path(state_file)

    class Handler(BaseHTTPRequestHandler):
        server_version = "seo-observer-runtime"
        sys_version = ""
        _response_started = False

        def send_response(self, code: int, message: str | None = None) -> None:
            # Reset per response: a keep-alive connection reuses this handler.
            self._response_started = False
            super().send_response(code, message)

        def end_headers(self) -> None:
            self._response_started = True
            super().end_headers()

        def log_request(self, code: str | int = "-", size: str | int = "-") -> None:
            # One line per request with the status code, path without query
            # string: client access tokens may arrive in the query and must
            # not land in stderr.
            path = urllib.parse.urlsplit(self.path).path
            sys.stderr.write(f"{self.address_string()} - {self.command} {path} {code}\n")

        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

        def _send(self, status: int, body: bytes = b"", *,
                  content_type: str = "text/plain; charset=utf-8",
                  extra_headers: dict[str, str] | None = None,
                  head_only: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if body and not head_only:
                self.wfile.write(body)

        def _health(self, head_only: bool) -> None:
            status, body = evaluate_health(state_file=state_file, exports_dir=exports_dir, now=clock())
            self._send(status, json.dumps(body).encode("utf-8"),
                       content_type="application/json",
                       extra_headers={"Cache-Control": "no-store"},
                       head_only=head_only)

        def _static(self, head_only: bool) -> None:
            raw = urllib.parse.urlsplit(self.path).path
            decoded = urllib.parse.unquote(raw)
            if not decoded.startswith("/") or decoded.startswith("//"):
                self._send(404, b"not found\n", extra_headers={"Cache-Control": "no-cache"},
                           head_only=head_only)
                return
            candidate, rejected = _resolve_request_path(exports_dir, decoded)
            if rejected or candidate is None:
                self._send(404, b"not found\n", extra_headers={"Cache-Control": "no-cache"},
                           head_only=head_only)
                return
            if candidate.is_dir():
                if not raw.endswith("/"):
                    location = raw + "/"
                    self.send_response(301)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                index = _contained_real_path(candidate / "index.html", exports_dir)
                if index is not None and index.is_file():
                    candidate = index
                else:
                    self._send(404, b"not found\n", extra_headers={"Cache-Control": "no-cache"},
                               head_only=head_only)
                    return
            if not candidate.is_file():
                self._send(404, b"not found\n", extra_headers={"Cache-Control": "no-cache"},
                           head_only=head_only)
                return
            try:
                size = candidate.stat().st_size
                stream = candidate.open("rb")
            except OSError:
                self._send(404, b"not found\n", extra_headers={"Cache-Control": "no-cache"},
                           head_only=head_only)
                return
            content_type = _CONTENT_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not head_only:
                with stream:
                    while True:
                        chunk = stream.read(_CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            else:
                stream.close()

        def _dispatch(self, head_only: bool) -> None:
            try:
                if urllib.parse.urlsplit(self.path).path == "/health":
                    self._health(head_only)
                else:
                    self._static(head_only)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.log_error("handler error")
                if self._response_started:
                    # A status line was already sent (e.g. mid-body failure);
                    # a second one would corrupt the stream, so just hang up.
                    self.close_connection = True
                    return
                try:
                    self._send(500, b"internal error\n", head_only=head_only)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_GET(self) -> None:
            self._dispatch(head_only=False)

        def do_HEAD(self) -> None:
            self._dispatch(head_only=True)

        def _method_not_allowed(self) -> None:
            self._send(405, b"method not allowed\n", extra_headers={"Allow": "GET, HEAD"})

        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_OPTIONS = _method_not_allowed

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    return Server((host, port), Handler)
