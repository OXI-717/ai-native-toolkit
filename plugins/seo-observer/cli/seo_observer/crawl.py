from __future__ import annotations

import dataclasses
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Protocol

from seo_observer.config import PropertyConfig


MAX_CRAWL_PAGES = 100
MAX_CRAWL_DEPTH = 3
MAX_CRAWL_SECONDS = 30.0
MAX_CRAWL_REDIRECTS = 10
MAX_CRAWL_RESPONSE_BYTES = 1_000_000
DEFAULT_CRAWL_DELAY_SECONDS = 0.5
DEFAULT_CRAWL_TIMEOUT_SECONDS = 10
DEFAULT_CRAWL_USER_AGENT = "oxi-seo-observer-crawler/1.0 (+local diagnostic)"
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = frozenset({"fbclid", "gclid", "yclid"})


@dataclasses.dataclass(frozen=True)
class CrawlLimits:
    max_pages: int = MAX_CRAWL_PAGES
    max_depth: int = MAX_CRAWL_DEPTH
    max_seconds: float = MAX_CRAWL_SECONDS
    crawl_delay_seconds: float = DEFAULT_CRAWL_DELAY_SECONDS
    timeout_seconds: int = DEFAULT_CRAWL_TIMEOUT_SECONDS
    max_response_bytes: int = MAX_CRAWL_RESPONSE_BYTES
    max_redirects: int = MAX_CRAWL_REDIRECTS


@dataclasses.dataclass(frozen=True)
class CrawlResponse:
    status: int
    body: str
    headers: dict[str, str]
    byte_size: int
    error: str | None = None


class CrawlTransport(Protocol):
    def robots_txt(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> str | None:
        ...

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> CrawlResponse:
        ...


@dataclasses.dataclass
class FixtureCrawlTransport:
    pages: dict[str, dict[str, Any]]
    fetch_calls: list[str] = dataclasses.field(default_factory=list)
    robots_calls: list[str] = dataclasses.field(default_factory=list)

    def robots_txt(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> str | None:
        robots_url = _robots_url(url)
        self.robots_calls.append(robots_url)
        page = self.pages.get(robots_url)
        if page is None:
            return None
        if int(page.get("status") or 200) >= 400:
            return None
        return str(page.get("body") or "")

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> CrawlResponse:
        self.fetch_calls.append(url)
        page = self.pages.get(url)
        if page is None:
            return CrawlResponse(status=404, body="", headers={}, byte_size=0)
        body = str(page.get("body") or "")
        body_bytes = body.encode("utf-8")
        if len(body_bytes) > max_response_bytes:
            return CrawlResponse(
                status=0,
                body="",
                headers=_headers(page),
                byte_size=len(body_bytes),
                error="Response exceeded max_response_bytes.",
            )
        return CrawlResponse(
            status=int(page.get("status") or 200),
            body=body,
            headers=_headers(page),
            byte_size=len(body_bytes),
        )


class StdlibCrawlTransport:
    def __init__(self, user_agent: str = DEFAULT_CRAWL_USER_AGENT) -> None:
        self.user_agent = user_agent
        self.ssl_context = _default_ssl_context()
        self.opener = urllib.request.build_opener(
            _NoRedirectHandler(),
            urllib.request.HTTPSHandler(context=self.ssl_context),
        )

    def robots_txt(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> str | None:
        request = urllib.request.Request(_robots_url(url), headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds, context=self.ssl_context) as response:
                data = response.read(max_response_bytes + 1)
        except Exception:
            return None
        if len(data) > max_response_bytes:
            return None
        return data.decode(_charset(dict(response.headers)), errors="replace")

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> CrawlResponse:
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with self.opener.open(request, timeout=timeout_seconds) as response:
                data = response.read(max_response_bytes + 1)
                headers = dict(response.headers)
                if len(data) > max_response_bytes:
                    return CrawlResponse(
                        status=0,
                        body="",
                        headers=headers,
                        byte_size=len(data),
                        error="Response exceeded max_response_bytes.",
                    )
                return CrawlResponse(
                    status=int(getattr(response, "status", 200)),
                    body=data.decode(_charset(headers), errors="replace"),
                    headers=headers,
                    byte_size=len(data),
                )
        except urllib.error.HTTPError as exc:
            headers = dict(exc.headers)
            body = exc.read(min(max_response_bytes, 4096))
            return CrawlResponse(
                status=int(exc.code),
                body=body.decode(_charset(headers), errors="replace"),
                headers=headers,
                byte_size=len(body),
            )
        except Exception as exc:
            return CrawlResponse(
                status=0,
                body="",
                headers={},
                byte_size=0,
                error=f"{exc.__class__.__name__}: {exc}",
            )


@dataclasses.dataclass
class _RateLimitedCrawlTransport:
    wrapped: CrawlTransport
    crawl_delay_seconds: float
    sleeper: Callable[[float], None]
    fetched_origins: set[str] = dataclasses.field(default_factory=set)

    def robots_txt(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> str | None:
        self._record_origin_request(url)
        return self.wrapped.robots_txt(
            url,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> CrawlResponse:
        self._record_origin_request(url)
        return self.wrapped.fetch(
            url,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )

    def _record_origin_request(self, url: str) -> None:
        origin = _origin(url)
        if origin in self.fetched_origins and self.crawl_delay_seconds > 0:
            self.sleeper(self.crawl_delay_seconds)
        self.fetched_origins.add(origin)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def crawl_properties(
    properties: list[PropertyConfig] | tuple[PropertyConfig, ...],
    *,
    transport: CrawlTransport | None = None,
    limits: CrawlLimits | None = None,
    observed_at: str | None = None,
    user_agent: str = DEFAULT_CRAWL_USER_AGENT,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    limits = limits or CrawlLimits()
    transport = _RateLimitedCrawlTransport(
        transport or StdlibCrawlTransport(user_agent=user_agent),
        crawl_delay_seconds=limits.crawl_delay_seconds,
        sleeper=sleeper,
    )
    observed_at = observed_at or _utc_now()
    started = monotonic()
    scopes = [_property_scope(item) for item in properties]
    queue: deque[tuple[PropertyConfig, str, int]] = deque(
        (item, _normalize_url(item.url), 0) for item in properties
    )
    seen: set[str] = set()
    pages: list[dict[str, Any]] = []
    crawled_count = 0
    robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
    truncated_reason: str | None = None

    while queue:
        if monotonic() - started >= limits.max_seconds:
            truncated_reason = "max_seconds"
            break
        prop, url, depth = queue.popleft()
        if url in seen:
            continue
        if depth > limits.max_depth:
            truncated_reason = "max_depth"
            break
        if crawled_count >= limits.max_pages:
            truncated_reason = "max_pages"
            break
        seen.add(url)
        robots = _robots_for(url, transport=transport, limits=limits, cache=robots_cache)
        if robots is not None and not robots.can_fetch(user_agent, url):
            pages.append(_robots_excluded(prop.id, url, depth))
            continue
        page = _fetch_with_redirects(
            prop.id,
            url,
            depth,
            transport=transport,
            limits=limits,
            scopes=scopes,
            robots_cache=robots_cache,
            user_agent=user_agent,
            seen=seen,
        )
        scoped_links = [link for link in page["internal_links"] if _in_scope(link, scopes)]
        page["internal_links"] = scoped_links
        pages.append(page)
        if page["robots_status"] != "excluded":
            crawled_count += 1
        for link in scoped_links:
            if link not in seen and _in_scope(link, scopes):
                queue.append((prop, link, depth + 1))
        if monotonic() - started >= limits.max_seconds:
            truncated_reason = "max_seconds"
            break
        if crawled_count >= limits.max_pages and queue:
            truncated_reason = "max_pages"
            break

    return {
        "schema": "seo-observer.crawl.v1",
        "status": "partial" if truncated_reason else "complete",
        "truncated_reason": truncated_reason,
        "observed_at": observed_at,
        "property_count": len(properties),
        "crawled_count": crawled_count,
        "page_count": len(pages),
        "limits": {
            "max_pages": limits.max_pages,
            "max_depth": limits.max_depth,
            "max_seconds": limits.max_seconds,
            "crawl_delay_seconds": limits.crawl_delay_seconds,
        },
        "pages": pages,
        "findings": _crawl_findings(pages),
    }


def write_crawl_artifact(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _fetch_with_redirects(
    property_id: str,
    url: str,
    depth: int,
    *,
    transport: CrawlTransport,
    limits: CrawlLimits,
    scopes: list[tuple[str, str]],
    robots_cache: dict[str, urllib.robotparser.RobotFileParser | None],
    user_agent: str,
    seen: set[str],
) -> dict[str, Any]:
    current = url
    chain: list[dict[str, Any]] = []
    visited = {url}
    response = CrawlResponse(status=0, body="", headers={}, byte_size=0, error="No response.")
    for _ in range(limits.max_redirects + 1):
        response = transport.fetch(
            current,
            timeout_seconds=limits.timeout_seconds,
            max_response_bytes=limits.max_response_bytes,
        )
        location = _header(response.headers, "location")
        if response.status not in {301, 302, 303, 307, 308} or not location:
            break
        target = _normalize_url(urllib.parse.urljoin(current, location))
        chain.append({"url": current, "status": response.status, "location": target})
        if not _in_scope(target, scopes):
            return {
                "property_id": property_id,
                "url": url,
                "final_url": target,
                "depth": depth,
                "fetch_status": response.status,
                "redirect_chain": chain,
                "robots_status": "allowed",
                "content_type": _header(response.headers, "content-type") or "text/html",
                "byte_size": response.byte_size,
                "internal_links": [],
                "error": "Redirect target is outside configured crawl scope.",
            }
        if target in visited:
            response = CrawlResponse(
                status=0,
                body="",
                headers=response.headers,
                byte_size=0,
                error="Redirect cycle detected.",
            )
            current = target
            break
        if target in seen:
            return {
                "property_id": property_id,
                "url": url,
                "final_url": target,
                "depth": depth,
                "fetch_status": response.status,
                "redirect_chain": chain,
                "robots_status": "allowed",
                "content_type": _header(response.headers, "content-type") or "text/html",
                "byte_size": response.byte_size,
                "internal_links": [],
                "error": None,
            }
        seen.add(target)
        robots = _robots_for(target, transport=transport, limits=limits, cache=robots_cache)
        if robots is not None and not robots.can_fetch(user_agent, target):
            excluded = _robots_excluded(property_id, url, depth)
            excluded["final_url"] = target
            excluded["redirect_chain"] = chain
            return excluded
        visited.add(target)
        current = target
    else:
        response = CrawlResponse(
            status=0,
            body="",
            headers=response.headers,
            byte_size=0,
            error="Redirect limit exceeded.",
        )
    is_html = _is_html(response.headers, response.body)
    links = _extract_links(response.body, current) if is_html else []
    metadata = _extract_page_metadata(response.body, current, response.headers) if is_html else {}
    return {
        "property_id": property_id,
        "url": url,
        "final_url": current,
        "depth": depth,
        "fetch_status": response.status,
        "redirect_chain": chain,
        "robots_status": "allowed",
        "content_type": _header(response.headers, "content-type") or "text/html",
        "byte_size": response.byte_size,
        **metadata,
        "internal_links": links,
        "error": response.error,
    }


def _robots_excluded(property_id: str, url: str, depth: int) -> dict[str, Any]:
    return {
        "property_id": property_id,
        "url": url,
        "final_url": url,
        "depth": depth,
        "fetch_status": None,
        "redirect_chain": [],
        "robots_status": "excluded",
        "content_type": None,
        "byte_size": 0,
        "internal_links": [],
        "error": "Excluded by robots.txt.",
    }


def _robots_for(
    url: str,
    *,
    transport: CrawlTransport,
    limits: CrawlLimits,
    cache: dict[str, urllib.robotparser.RobotFileParser | None],
) -> urllib.robotparser.RobotFileParser | None:
    robots_url = _robots_url(url)
    if robots_url in cache:
        return cache[robots_url]
    body = transport.robots_txt(
        url,
        timeout_seconds=limits.timeout_seconds,
        max_response_bytes=limits.max_response_bytes,
    )
    if body is None:
        cache[robots_url] = None
        return None
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(body.splitlines())
    cache[robots_url] = parser
    return parser


def _property_scope(prop: PropertyConfig) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(_normalize_url(prop.url))
    path = parsed.path or "/"
    return (_origin(prop.url), path)


def _in_scope(url: str, scopes: list[tuple[str, str]]) -> bool:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
    for scope_origin, scope_path in scopes:
        if origin != scope_origin:
            continue
        if scope_path == "/" or path == scope_path or path.startswith(scope_path.rstrip("/") + "/"):
            return True
    return False


def _normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path or "/"), safe="/%:@")
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = urllib.parse.urlencode(
        sorted(
            (name, value)
            for name, value in query_pairs
            if name.lower() not in TRACKING_QUERY_NAMES
            and not any(name.lower().startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES)
        ),
        doseq=True,
    )
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def _extract_links(body: str, base_url: str) -> list[str]:
    parser = _LinkParser(base_url)
    parser.feed(body)
    origin = _origin(base_url)
    links: list[str] = []
    seen: set[str] = set()
    for link in parser.links:
        normalized = _normalize_url(link)
        if _origin(normalized) != origin or normalized in seen:
            continue
        seen.add(normalized)
        links.append(normalized)
    return links


def _extract_page_metadata(body: str, base_url: str, headers: dict[str, str]) -> dict[str, Any]:
    parser = _PageMetadataParser(base_url)
    parser.feed(body)
    return {
        "title": parser.title.strip(),
        "meta_description": parser.meta_description,
        "h1_text": parser.h1_text.strip(),
        "h1_count": parser.h1_count,
        "canonical_url": parser.canonical_url,
        "header_canonical_url": _header_canonical_url(headers),
        "meta_robots": parser.meta_robots,
        "x_robots_tag": _header(headers, "x-robots-tag"),
        "hreflang": parser.hreflang,
    }


def _crawl_findings(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for page in pages:
        canonical_url = page.get("canonical_url")
        header_canonical_url = page.get("header_canonical_url")
        if canonical_url and header_canonical_url and canonical_url != header_canonical_url:
            findings.append(
                {
                    "type": "crawl_canonical_conflict",
                    "url": str(page.get("url") or ""),
                    "property_id": str(page.get("property_id") or "__all__"),
                    "severity": "high",
                    "reason": "HTML canonical and Link header canonical disagree.",
                    "evidence": {
                        "canonical_url": canonical_url,
                        "header_canonical_url": header_canonical_url,
                    },
                }
            )
        meta_robots = page.get("meta_robots")
        x_robots_tag = page.get("x_robots_tag")
        if (
            meta_robots
            and x_robots_tag
            and _robots_directives(meta_robots) != _robots_directives(x_robots_tag)
        ):
            findings.append(
                {
                    "type": "crawl_meta_robots_conflict",
                    "url": str(page.get("url") or ""),
                    "property_id": str(page.get("property_id") or "__all__"),
                    "severity": "high",
                    "reason": "Meta robots and X-Robots-Tag directives disagree.",
                    "evidence": {
                        "meta_robots": meta_robots,
                        "x_robots_tag": x_robots_tag,
                    },
                }
            )
        h1_count = int(page.get("h1_count") or 0)
        if h1_count > 1:
            h1_text = str(page.get("h1_text") or "")
            findings.append(
                {
                    "type": "crawl_multi_h1",
                    "url": str(page.get("url") or ""),
                    "property_id": str(page.get("property_id") or "__all__"),
                    "severity": "medium",
                    "reason": f"Page contains {h1_count} H1 elements; first H1 is `{h1_text}`.",
                    "evidence": {"h1_text": h1_text, "h1_count": h1_count},
                }
            )
    return findings


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        parsed = urllib.parse.urlparse(href)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return
        self.links.append(urllib.parse.urljoin(self.base_url, href))


class _PageMetadataParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.meta_description = ""
        self.meta_robots: str | None = None
        self.canonical_url: str | None = None
        self.hreflang: list[dict[str, str]] = []
        self.h1_text = ""
        self.h1_count = 0
        self._capture_title = False
        self._h1_depth = 0
        self._current_h1_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attr_map = {name.lower(): value for name, value in attrs if name}
        if lowered == "title":
            self._capture_title = True
            return
        if lowered == "h1":
            self.h1_count += 1
            if self._h1_depth == 0:
                self._current_h1_parts = []
            self._h1_depth += 1
            return
        if lowered == "meta":
            name = (attr_map.get("name") or "").lower()
            content = attr_map.get("content")
            if name == "description" and content is not None and not self.meta_description:
                self.meta_description = content.strip()
            if name == "robots" and content is not None and self.meta_robots is None:
                self.meta_robots = content.strip()
            return
        if lowered != "link":
            return
        rel_tokens = {
            token.strip().lower()
            for token in (attr_map.get("rel") or "").replace(",", " ").split()
            if token.strip()
        }
        href = attr_map.get("href")
        if not href:
            return
        if "canonical" in rel_tokens and self.canonical_url is None:
            self.canonical_url = _normalize_url(urllib.parse.urljoin(self.base_url, href))
        hreflang = attr_map.get("hreflang")
        if "alternate" in rel_tokens and hreflang:
            self.hreflang.append(
                {
                    "hreflang": hreflang.strip(),
                    "url": _normalize_url(urllib.parse.urljoin(self.base_url, href)),
                }
            )

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._capture_title = False
            return
        if lowered == "h1" and self._h1_depth > 0:
            self._h1_depth -= 1
            if self._h1_depth == 0 and not self.h1_text:
                self.h1_text = " ".join("".join(self._current_h1_parts).split())

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self.title += data
        if self._h1_depth > 0:
            self._current_h1_parts.append(data)


def _headers(page: dict[str, Any]) -> dict[str, str]:
    return {str(name): str(value) for name, value in dict(page.get("headers") or {}).items()}


def _header(headers: dict[str, str], name: str) -> str | None:
    needle = name.lower()
    for key, value in headers.items():
        if key.lower() == needle:
            return value
    return None


def _header_canonical_url(headers: dict[str, str]) -> str | None:
    link = _header(headers, "link")
    if not link:
        return None
    for value in _split_link_header(link):
        match = re.match(r"\s*<([^>]+)>\s*(?:;(.*))?$", value)
        if not match:
            continue
        params = match.group(2) or ""
        if any(_link_param_is_canonical_rel(part) for part in params.split(";")):
            return _normalize_url(match.group(1))
    return None


def _link_param_is_canonical_rel(value: str) -> bool:
    name, separator, raw_value = value.strip().partition("=")
    if separator != "=" or name.lower() != "rel":
        return False
    return raw_value.strip().strip("\"'").lower() == "canonical"


def _split_link_header(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_angle = False
    in_quote = False
    for char in value:
        if char == "<" and not in_quote:
            in_angle = True
        elif char == ">" and not in_quote:
            in_angle = False
        elif char == '"':
            in_quote = not in_quote
        if char == "," and not in_angle and not in_quote:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _robots_directives(value: str) -> set[str]:
    normalized = value.lower()
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    return {part.strip() for part in normalized.split(",") if part.strip()}


def _is_html(headers: dict[str, str], body: str) -> bool:
    content_type = _header(headers, "content-type")
    return content_type is None or "html" in content_type.lower() or "<html" in body[:200].lower()


def _robots_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc.lower(), "/robots.txt", "", "", ""))


def _origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _charset(headers: dict[str, str]) -> str:
    content_type = _header(headers, "content-type") or ""
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value
    return "utf-8"


def _default_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
