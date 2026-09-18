from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from seo_hub.errors import SeoHubError


class MCPTransportError(SeoHubError, ValueError):
    code = "MCP_TRANSPORT_ERROR"


def is_loopback_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _trusted_private_hostnames() -> set[str]:
    # Compose deploys reach native services by their internal DNS name over the
    # `private` network (deploy/compose.yaml), never routed off-host. Operators
    # opt individual hostnames in explicitly; nothing is trusted by default.
    raw = os.environ.get("SEO_HUB_TRUSTED_PRIVATE_HOSTS", "")
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


def is_trusted_private_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname
    return bool(host) and host.lower() in _trusted_private_hostnames()


def validate_service_url(url: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise MCPTransportError("Invalid service URL") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or port == 0):
        raise MCPTransportError("Invalid service URL")
    if parsed.scheme != "https" and not is_loopback_url(url) and not is_trusted_private_url(url):
        raise MCPTransportError("Non-loopback services require HTTPS")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPMCPClient:
    """OpenSEO's stateless Streamable HTTP, using its legacy JSON protocol lane."""

    def __init__(self, mcp_url: str, *, max_response_bytes: int = 2_000_000) -> None:
        validate_service_url(mcp_url)
        if max_response_bytes < 1:
            raise MCPTransportError("max_response_bytes must be positive")
        self.mcp_url = mcp_url
        self.max_response_bytes = max_response_bytes
        self.server_info: dict[str, Any] = {}

    async def call_tool(self, name: str, arguments: dict[str, Any], *,
                        headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise MCPTransportError("timeout_seconds must be positive")
        # Enforce the boundary even when this transport is used without the adapter.
        from seo_hub.adapters.openseo import OPENSEO_READ_TOOLS
        if name not in OPENSEO_READ_TOOLS:
            raise MCPTransportError("Tool is not allowlisted for read-only import")
        if not is_loopback_url(self.mcp_url) and not (
            headers.get("Authorization") or headers.get("x-api-key") or
            (headers.get("CF-Access-Client-Id") and headers.get("CF-Access-Client-Secret"))
        ):
            raise MCPTransportError("Remote MCP credentials are missing")
        return await asyncio.to_thread(self._call, name, arguments, headers, timeout_seconds)

    def _call(self, name, arguments, headers, timeout_seconds):
        deadline = time.monotonic() + timeout_seconds
        request_headers = {**headers, "Content-Type": "application/json",
                           "Accept": "application/json, text/event-stream"}
        result, session = self._request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "oxyseo", "version": "1"},
        }, 1, request_headers, deadline)
        version = result.get("protocolVersion")
        if version not in {"2024-11-05", "2025-03-26", "2025-06-18"}:
            raise MCPTransportError("Unsupported MCP protocol version")
        self.server_info = result.get("serverInfo", {})
        request_headers["MCP-Protocol-Version"] = version
        if session:
            request_headers["Mcp-Session-Id"] = session
        self._request("notifications/initialized", None, None, request_headers, deadline)
        result, _ = self._request("tools/call", {"name": name, "arguments": arguments},
                                  2, request_headers, deadline)
        return result

    def _request(self, method, params, request_id, headers, deadline):
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if request_id is not None:
            payload["id"] = request_id
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MCPTransportError("MCP request timed out")
        request = urllib.request.Request(self.mcp_url, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
        try:
            # Disable proxy routing for local credentials and refuse every redirect.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=remaining) as response:
                session = response.headers.get("Mcp-Session-Id")
                if request_id is None and response.status in (200, 202, 204):
                    return {}, session
                if response.headers.get_content_type() == "text/event-stream":
                    result = self._read_sse(response, request_id, deadline)
                elif response.headers.get_content_type() == "application/json":
                    result = json.loads(b"".join(self._body_chunks(response, deadline)))
                else:
                    raise MCPTransportError("MCP response is not JSON or SSE")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise MCPTransportError(f"MCP HTTP {code}") from None
        except (OSError, urllib.error.URLError, ValueError, RecursionError) as exc:
            if isinstance(exc, MCPTransportError):
                raise
            raise MCPTransportError("MCP request or response invalid") from None
        if (not isinstance(result, dict) or result.get("jsonrpc") != "2.0"
                or result.get("id") != request_id):
            raise MCPTransportError("MCP response ID or envelope mismatch")
        if "error" in result:
            raise MCPTransportError("MCP JSON-RPC error")
        if not isinstance(result.get("result"), dict):
            raise MCPTransportError("MCP result must be an object")
        return result["result"], session

    def _body_chunks(self, response, deadline):
        # urllib's timeout is an inactivity timeout. Reset the socket timeout
        # to the remaining budget before each single raw read, so a trickle
        # cannot keep a buffered read/readline alive beyond the deadline.
        sock = response.fp.raw._sock
        used = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTransportError("MCP request timed out")
            sock.settimeout(remaining)
            chunk = response.read1(min(65536, self.max_response_bytes - used + 1))
            if time.monotonic() > deadline:
                raise MCPTransportError("MCP request timed out")
            if not chunk:
                return
            used += len(chunk)
            if used > self.max_response_bytes:
                raise MCPTransportError("MCP response exceeds size limit")
            yield chunk
            if response.isclosed():
                return

    def _read_sse(self, response, request_id, deadline):
        data = []
        pending = b""
        for chunk in self._body_chunks(response, deadline):
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                text = line.decode("utf-8").rstrip("\r")
                if text.startswith("data:"):
                    data.append(text[5:].removeprefix(" "))
                elif not text and data:
                    message = json.loads("\n".join(data))
                    data = []
                    if isinstance(message, dict) and message.get("id") == request_id:
                        return message
        raise MCPTransportError("MCP SSE response missing or timed out")
