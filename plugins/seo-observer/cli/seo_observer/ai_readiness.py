from __future__ import annotations

import ipaddress
import re
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from seo_observer.config import ProjectConfig, observer_home
from seo_observer.storage import default_database_path


AI_REFERRAL_SOURCE_LAST_CHECKED = "2026-08-18"
AI_REFERRAL_SOURCES: dict[str, tuple[str, ...]] = {
    "chatgpt": ("chatgpt.com", "openai.com", "chat.openai.com"),
    "perplexity": ("perplexity.ai",),
    "claude": ("claude.ai",),
    "gemini": ("gemini.google.com", "bard.google.com"),
    "copilot": ("copilot.microsoft.com", "bing.com/chat"),
    "you": ("you.com",),
    "poe": ("poe.com",),
    "phind": ("phind.com",),
}

Fetch = Callable[[str], "FetchResponse"]
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
MAX_DECLARED_LINK_CHECKS = 50
ALLOWED_FETCH_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int | None
    body: str
    final_url: str
    content_type: str
    error: str | None = None


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, allowed_host: str | None = None) -> None:
        super().__init__()
        self.allowed_host = allowed_host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        unsafe_reason = _unsafe_fetch_reason(newurl, allowed_host=self.allowed_host, resolve_host=True)
        if unsafe_reason:
            raise urllib.error.URLError(unsafe_reason)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_fetch(
    url: str, *, timeout: int = 10, max_bytes: int = 1_000_000, allowed_host: str | None = None
) -> FetchResponse:
    unsafe_reason = _unsafe_fetch_reason(url, allowed_host=allowed_host, resolve_host=True)
    if unsafe_reason:
        return FetchResponse(
            url=url,
            status=None,
            body="",
            final_url=url,
            content_type="",
            error=unsafe_reason,
        )
    request = urllib.request.Request(url, headers={"User-Agent": "seo-observer/ai-readiness"})
    try:
        opener = urllib.request.build_opener(_SafeRedirectHandler(allowed_host=allowed_host))
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                body = body[:max_bytes]
            content_type = response.headers.get("content-type", "")
            charset = response.headers.get_content_charset() or "utf-8"
            return FetchResponse(
                url=url,
                status=int(response.status),
                body=body.decode(charset, errors="replace"),
                final_url=str(response.geturl()),
                content_type=content_type,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(max_bytes)
        charset = exc.headers.get_content_charset() if exc.headers else None
        return FetchResponse(
            url=url,
            status=int(exc.code),
            body=body.decode(charset or "utf-8", errors="replace"),
            final_url=str(exc.geturl()),
            content_type=exc.headers.get("content-type", "") if exc.headers else "",
            error=str(exc),
        )
    except (urllib.error.URLError, TimeoutError, OSError, TypeError, ValueError) as exc:
        return FetchResponse(
            url=url,
            status=None,
            body="",
            final_url=url,
            content_type="",
            error=f"{exc.__class__.__name__}: {exc}",
        )


def build_ai_readiness_payload(
    *,
    config: ProjectConfig,
    output_dir: Path,
    fetcher: Fetch = http_fetch,
) -> dict[str, Any]:
    return {
        "ok": True,
        "command": "ai-readiness",
        "project": config.project.namespace,
        "llms_txt": audit_llms_txt(config=config, output_dir=output_dir, fetcher=fetcher),
        "ai_referrals": collect_ai_referrals(config),
    }


def audit_llms_txt(*, config: ProjectConfig, output_dir: Path, fetcher: Fetch = http_fetch) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    property_results = [_audit_property_llms(property_url=prop.url, fetcher=fetcher) for prop in config.properties]
    all_known_urls = sorted({url for result in property_results for url in result["known_urls"]})
    draft_path = output_dir / "llms-draft.txt"
    draft_text = _draft_llms_txt(config.project.namespace, all_known_urls)
    draft_path.write_text(draft_text, encoding="utf-8")

    findings = [finding for result in property_results for finding in result["findings"]]
    status = _llms_status(findings)
    return {
        "status": status,
        "properties": [
            {key: value for key, value in result.items() if key not in {"findings", "known_urls"}}
            for result in property_results
        ],
        "findings": findings,
        "draft_artifact": {
            "path": str(draft_path),
            "source": "local-known-urls",
            "published": False,
            "url_count": len(all_known_urls),
        },
    }


def collect_ai_referrals(config: ProjectConfig) -> dict[str, Any]:
    db_path = default_database_path(config.project.namespace, observer_home())
    empty = {
        "sources": [],
        "unknown": [],
        "window": {"start": None, "end": None, "timezone": config.project.timezone},
        "evidence_quality": "empty",
        "source_registry": {
            "last_checked": AI_REFERRAL_SOURCE_LAST_CHECKED,
            "heuristic": True,
            "source_ids": sorted(AI_REFERRAL_SOURCES),
        },
    }
    if not db_path.exists():
        return empty
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT
                  search_engine AS source_referrer,
                  SUM(COALESCE(visits, 0)) AS visits,
                  SUM(COALESCE(users, 0)) AS users,
                  MIN(effective_start) AS window_start,
                  MAX(effective_end) AS window_end
                FROM traffic_metrics
                WHERE project_id = ?
                  AND source IN ('ga4', 'yandex_metrica')
                  AND is_current = 1
                  AND attribution_model != 'ga4_session_all_channels'
                GROUP BY search_engine
                ORDER BY visits DESC, users DESC, search_engine ASC
                """,
                (config.project.namespace,),
            ).fetchall()
    except sqlite3.Error:
        return empty
    if not rows:
        return empty

    sources: dict[str, dict[str, Any]] = {}
    unknown: list[dict[str, Any]] = []
    window_start: str | None = None
    window_end: str | None = None
    for row in rows:
        raw_source = str(row["source_referrer"] or "").strip()
        if not raw_source or raw_source == "__all__":
            continue
        window_start = _min_date(window_start, row["window_start"])
        window_end = _max_date(window_end, row["window_end"])
        visits = int(row["visits"] or 0)
        users = int(row["users"] or 0)
        source_id = recognize_ai_referral_source(raw_source)
        if source_id is None:
            unknown.append(
                {
                    "source": raw_source,
                    "visits": visits,
                    "users": users,
                    "evidence": "traffic_metrics.search_engine",
                }
            )
            continue
        bucket = sources.setdefault(
            source_id,
            {
                "source_id": source_id,
                "visits": 0,
                "users": 0,
                "matched_referrers": [],
                "heuristic": True,
                "last_checked": AI_REFERRAL_SOURCE_LAST_CHECKED,
                "evidence": "traffic_metrics.search_engine",
            },
        )
        bucket["visits"] += visits
        bucket["users"] += users
        bucket["matched_referrers"].append(raw_source)

    source_rows = sorted(sources.values(), key=lambda item: (-int(item["visits"]), item["source_id"]))
    quality = "heuristic" if source_rows or unknown else "empty"
    return {
        "sources": source_rows,
        "unknown": unknown,
        "window": {"start": window_start, "end": window_end, "timezone": config.project.timezone},
        "evidence_quality": quality,
        "source_registry": empty["source_registry"],
    }


def recognize_ai_referral_source(value: str) -> str | None:
    normalized = _source_key(value)
    for source_id, patterns in AI_REFERRAL_SOURCES.items():
        if any(_matches_referral_pattern(normalized, pattern) for pattern in patterns):
            return source_id
    return None


def _audit_property_llms(*, property_url: str, fetcher: Fetch) -> dict[str, Any]:
    base = _base_url(property_url)
    property_host = urllib.parse.urlparse(base).hostname
    llms_url = urllib.parse.urljoin(base, "/llms.txt")
    llms_full_url = urllib.parse.urljoin(base, "/llms-full.txt")
    sitemap_url = urllib.parse.urljoin(base, "/sitemap.xml")
    llms = _safe_fetch(llms_url, fetcher=fetcher, allowed_host=property_host)
    llms_full = _safe_fetch(llms_full_url, fetcher=fetcher, allowed_host=property_host)
    sitemap = _safe_fetch(sitemap_url, fetcher=fetcher, allowed_host=property_host)
    findings: list[dict[str, Any]] = []
    known_urls = {_canonical_url(base)}
    sitemap_urls = _parse_sitemap_urls(sitemap.body) if sitemap.status == 200 else set()
    known_urls.update(sitemap_urls)
    llms_links: set[str] = set()
    llms_full_links: set[str] = set()

    if llms.status == 404:
        findings.append(_finding("LLMS_TXT_MISSING", "error", llms_url, "llms.txt is not available."))
    elif llms.status != 200:
        findings.append(
            _finding(
                "LLMS_TXT_HTTP_STATUS",
                "error",
                llms_url,
                "llms.txt returned a non-200 HTTP status.",
                {"status": llms.status, "error": llms.error},
            )
        )
    else:
        structure = _parse_llms_structure(llms.body)
        llms_links, unsafe_links = _safe_declared_links(structure["links"], base=base, allowed_host=property_host)
        known_urls.update(llms_links)
        if not structure["valid"]:
            findings.append(
                _finding(
                    "LLMS_TXT_INVALID_FORMAT",
                    "error",
                    llms_url,
                    "llms.txt does not start with a Markdown H1 heading.",
                    {"missing": structure["missing"]},
                )
            )
        for link, reason in unsafe_links:
            findings.append(
                _finding(
                    "LLMS_TXT_UNSAFE_LINK",
                    "error",
                    link,
                    "A link declared in llms.txt is outside the allowed audit fetch scope.",
                    {"error": reason},
                )
            )
        for link in sorted(llms_links)[:MAX_DECLARED_LINK_CHECKS]:
            response = fetcher(link)
            if response.status is None or response.status >= 400:
                findings.append(
                    _finding(
                        "LLMS_TXT_BROKEN_LINK",
                        "error",
                        link,
                        "A link declared in llms.txt did not resolve successfully.",
                        {"status": response.status, "error": response.error},
                    )
                )
        missing_from_llms = sorted(sitemap_urls - llms_links)
        if sitemap.status == 200 and missing_from_llms:
            findings.append(
                _finding(
                    "LLMS_TXT_SITEMAP_MISMATCH",
                    "warning",
                    llms_url,
                    "Sitemap contains URLs that are not declared in llms.txt.",
                    {"missing_from_llms_txt": missing_from_llms[:50], "missing_count": len(missing_from_llms)},
                )
            )

    if llms_full.status == 404:
        findings.append(
            _finding("LLMS_FULL_TXT_MISSING", "info", llms_full_url, "llms-full.txt is not available.")
        )
    elif llms_full.status != 200:
        findings.append(
            _finding(
                "LLMS_FULL_TXT_HTTP_STATUS",
                "warning",
                llms_full_url,
                "llms-full.txt returned a non-200 HTTP status.",
                {"status": llms_full.status, "error": llms_full.error},
            )
        )
    else:
        structure = _parse_llms_structure(llms_full.body)
        llms_full_links, unsafe_links = _safe_declared_links(
            structure["links"], base=base, allowed_host=property_host
        )
        known_urls.update(llms_full_links)
        if not structure["valid"]:
            findings.append(
                _finding(
                    "LLMS_FULL_TXT_INVALID_FORMAT",
                    "warning",
                    llms_full_url,
                    "llms-full.txt does not start with a Markdown H1 heading.",
                    {"missing": structure["missing"]},
                )
            )
        for link, reason in unsafe_links:
            findings.append(
                _finding(
                    "LLMS_FULL_TXT_UNSAFE_LINK",
                    "warning",
                    link,
                    "A link declared in llms-full.txt is outside the allowed audit fetch scope.",
                    {"error": reason},
                )
            )
        for link in sorted(llms_full_links)[:MAX_DECLARED_LINK_CHECKS]:
            response = fetcher(link)
            if response.status is None or response.status >= 400:
                findings.append(
                    _finding(
                        "LLMS_FULL_TXT_BROKEN_LINK",
                        "warning",
                        link,
                        "A link declared in llms-full.txt did not resolve successfully.",
                        {"status": response.status, "error": response.error},
                    )
                )

    return {
        "property_url": property_url,
        "llms_txt_url": llms_url,
        "llms_txt_http_status": llms.status,
        "llms_full_txt_url": llms_full_url,
        "llms_full_txt_http_status": llms_full.status,
        "sitemap_url": sitemap_url,
        "sitemap_http_status": sitemap.status,
        "llms_txt_links": sorted(llms_links),
        "llms_full_txt_links": sorted(llms_full_links),
        "sitemap_urls": sorted(sitemap_urls),
        "findings": findings,
        "known_urls": known_urls,
    }


def _parse_llms_structure(body: str) -> dict[str, Any]:
    first_nonblank = next((line.strip() for line in body.splitlines() if line.strip()), "")
    missing: list[str] = []
    if not first_nonblank.startswith("# "):
        missing.append("h1")
    return {
        "valid": not missing,
        "missing": missing,
        "links": [match.group(1).strip() for match in MARKDOWN_LINK_RE.finditer(body)],
    }


def _parse_sitemap_urls(body: str) -> set[str]:
    try:
        root = ET.fromstring(body.encode("utf-8"))
    except ET.ParseError:
        return set()
    urls: set[str] = set()
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] == "loc" and item.text:
            urls.add(_canonical_url(item.text.strip()))
    return urls


def _safe_fetch(url: str, *, fetcher: Fetch, allowed_host: str | None) -> FetchResponse:
    unsafe_reason = _unsafe_fetch_reason(url, allowed_host=allowed_host, resolve_host=False)
    if unsafe_reason:
        return FetchResponse(
            url=url,
            status=None,
            body="",
            final_url=url,
            content_type="",
            error=unsafe_reason,
        )
    if fetcher is http_fetch:
        return http_fetch(url, allowed_host=allowed_host)
    return fetcher(url)


def _safe_declared_links(
    links: list[str], *, base: str, allowed_host: str | None
) -> tuple[set[str], list[tuple[str, str]]]:
    safe_links: set[str] = set()
    unsafe_links: list[tuple[str, str]] = []
    for raw_link in links:
        link = _canonical_url(urllib.parse.urljoin(base, raw_link))
        unsafe_reason = _unsafe_fetch_reason(link, allowed_host=allowed_host, resolve_host=False)
        if unsafe_reason:
            unsafe_links.append((link, unsafe_reason))
            continue
        safe_links.add(link)
    return safe_links, unsafe_links


def _unsafe_fetch_reason(
    url: str, *, allowed_host: str | None = None, resolve_host: bool = False
) -> str | None:
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_FETCH_SCHEMES:
        return f"unsupported URL scheme: {scheme or '<empty>'}"
    if not parsed.hostname:
        return "missing URL host"
    hostname = parsed.hostname.lower()
    if allowed_host and hostname != allowed_host.lower():
        return f"URL host {hostname} does not match audited property host {allowed_host.lower()}"
    unsafe_host_reason = _unsafe_host_reason(hostname, resolve_host=resolve_host)
    if unsafe_host_reason:
        return unsafe_host_reason
    return None


def _unsafe_host_reason(hostname: str, *, resolve_host: bool) -> str | None:
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        if not resolve_host:
            return None
        try:
            addrinfos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            return f"host resolution failed: {exc}"
        addresses = []
        for addrinfo in addrinfos:
            address = addrinfo[4][0]
            try:
                addresses.append(ipaddress.ip_address(address))
            except ValueError:
                return f"host resolved to invalid address: {address}"
    for address in addresses:
        if _is_restricted_address(address):
            return f"host resolves to restricted address: {address}"
    return None


def _is_restricted_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _draft_llms_txt(project_id: str, urls: list[str]) -> str:
    title = project_id.replace("_", " ").replace("-", " ").title()
    lines = [f"# {title} AI Readiness Draft", "", "## Known URLs", ""]
    for url in urls:
        lines.append(f"- [{url}]({url})")
    return "\n".join(lines).rstrip() + "\n"


def _llms_status(findings: list[dict[str, Any]]) -> str:
    codes = {finding["code"] for finding in findings if finding.get("severity") == "error"}
    if "LLMS_TXT_MISSING" in codes:
        return "missing"
    if codes:
        return "invalid"
    if any(finding.get("severity") == "warning" for finding in findings):
        return "warning"
    return "ok"


def _finding(code: str, severity: str, url: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "severity": severity, "url": url, "message": message}
    if details:
        payload["details"] = details
    return payload


def _base_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def _canonical_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    return urllib.parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def _source_key(value: str) -> str:
    return value.strip().lower()


def _matches_referral_pattern(normalized: str, pattern: str) -> bool:
    escaped = re.escape(pattern)
    return re.search(rf"(?<![a-z0-9-]){escaped}(?![a-z0-9-])", normalized) is not None


def _min_date(current: str | None, candidate: Any) -> str | None:
    if candidate is None:
        return current
    value = str(candidate)
    return value if current is None or value < current else current


def _max_date(current: str | None, candidate: Any) -> str | None:
    if candidate is None:
        return current
    value = str(candidate)
    return value if current is None or value > current else current
