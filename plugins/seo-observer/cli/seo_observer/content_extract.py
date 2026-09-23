from __future__ import annotations

import dataclasses
import hashlib
import html
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from typing import Any, Protocol


USER_AGENT = "seo-observer/competitor-research"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
TEXT_EXCERPT_CHARS = 2000
MARKDOWN_EXCERPT_CHARS = 500


class PageTransport(Protocol):
    def robots_txt(self, url: str, *, timeout_seconds: int) -> str | None:
        ...

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> dict[str, Any]:
        ...


@dataclasses.dataclass
class FixturePageTransport:
    pages: dict[str, dict[str, Any]]

    def robots_txt(self, url: str, *, timeout_seconds: int) -> str | None:
        if url not in self.pages:
            return None
        return self.pages.get(url, {}).get("robots", "")

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> dict[str, Any]:
        page = self.pages.get(url)
        if page is None:
            return {"status": 404, "body": "", "headers": {}}
        body = str(page.get("body") or "")
        if len(body.encode("utf-8")) > max_response_bytes:
            return {"status": 0, "body": "", "headers": {}, "too_large": True}
        return {
            "status": int(page.get("status") or 200),
            "body": body,
            "headers": dict(page.get("headers") or {}),
        }


class StdlibPageTransport:
    def __init__(self) -> None:
        self.ssl_context = _default_ssl_context()

    def robots_txt(self, url: str, *, timeout_seconds: int) -> str | None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
        request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(
                request,
                timeout=min(timeout_seconds, DEFAULT_TIMEOUT_SECONDS),
                context=self.ssl_context,
            ) as response:
                data = response.read(DEFAULT_MAX_RESPONSE_BYTES)
        except Exception:
            return None
        return data.decode("utf-8", errors="replace")

    def fetch(self, url: str, *, timeout_seconds: int, max_response_bytes: int) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(
                request,
                timeout=min(timeout_seconds, DEFAULT_TIMEOUT_SECONDS),
                context=self.ssl_context,
            ) as response:
                chunks = response.read(max_response_bytes + 1)
                if len(chunks) > max_response_bytes:
                    return {"status": 0, "body": "", "headers": dict(response.headers), "too_large": True}
                return {
                    "status": int(getattr(response, "status", 200)),
                    "body": chunks.decode(_charset(response.headers.get("Content-Type")), errors="replace"),
                    "headers": dict(response.headers),
                }
        except urllib.error.HTTPError as exc:
            body = exc.read(min(max_response_bytes, 4096)).decode("utf-8", errors="replace")
            return {"status": int(exc.code), "body": body, "headers": dict(exc.headers)}
        except Exception as exc:
            return {"status": 0, "body": "", "headers": {}, "error": f"{exc.__class__.__name__}: {exc}"}


def _default_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def extract_pages(
    urls: list[str],
    *,
    transport: PageTransport | None = None,
    observed_at: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    rate_limit_seconds: float = 0.0,
) -> dict[str, Any]:
    transport = transport or StdlibPageTransport()
    observed_at = observed_at or _utc_now()
    extracts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, url in enumerate(urls):
        if index and rate_limit_seconds > 0:
            time.sleep(rate_limit_seconds)
        page = _extract_one(
            url,
            transport=transport,
            observed_at=observed_at,
            timeout_seconds=min(int(timeout_seconds), DEFAULT_TIMEOUT_SECONDS),
            max_response_bytes=int(max_response_bytes),
        )
        extracts.append(page)
        if page.get("error"):
            errors.append({"code": "PAGE_EXTRACTION_PARTIAL", "safe_message": str(page["error"]), "details": {"url": url}})
    return {
        "schema": "seo-observer.content_extract.v1",
        "page_extracts": extracts,
        "quality_summary": {
            "overall": "partial" if errors else "research-only",
            "page_count": len(extracts),
            "error_count": len(errors),
        },
        "errors": errors,
    }


def _extract_one(
    url: str,
    *,
    transport: PageTransport,
    observed_at: str,
    timeout_seconds: int,
    max_response_bytes: int,
) -> dict[str, Any]:
    robots_txt = transport.robots_txt(url, timeout_seconds=timeout_seconds)
    robots_checked = robots_txt is not None
    if robots_txt is not None and not _robots_allows(url, robots_txt):
        return _failed_page(url, quality="blocked_by_robots", observed_at=observed_at, robots_checked=True, fetch_status=None, error="Blocked by robots.txt.")
    fetched = transport.fetch(url, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes)
    status = int(fetched.get("status") or 0)
    if fetched.get("too_large"):
        return _failed_page(url, quality="too_large", observed_at=observed_at, robots_checked=robots_checked, fetch_status=status, error="Response exceeded max_response_bytes.")
    if status in {401, 403}:
        return _failed_page(url, quality="forbidden", observed_at=observed_at, robots_checked=robots_checked, fetch_status=status, error=f"HTTP {status} is not fetchable without credentials.")
    body = str(fetched.get("body") or "")
    if _looks_paywalled(body):
        return _failed_page(url, quality="paywalled", observed_at=observed_at, robots_checked=robots_checked, fetch_status=status, error="Page appears to require login or subscription.")
    if status >= 400 or status == 0:
        return _failed_page(url, quality="forbidden", observed_at=observed_at, robots_checked=robots_checked, fetch_status=status, error=str(fetched.get("error") or f"HTTP {status} fetch failed."))
    parser = _ContentParser(base_url=url)
    parser.feed(body)
    text = _normalize_space(" ".join(parser.text_parts))
    headings = build_heading_tree(parser.heading_pairs)
    canonical = parser.canonical_url or url
    return {
        "url": url,
        "canonical_url": canonical,
        "title": parser.title.strip() or None,
        "meta_description": parser.meta_description.strip() or None,
        "word_count": len(re.findall(r"\b\w+\b", text)),
        "headings": headings,
        "text_excerpt": text[:TEXT_EXCERPT_CHARS],
        "internal_link_count": parser.internal_link_count,
        "external_link_count": parser.external_link_count,
        "json_ld_types": sorted(parser.json_ld_types),
        "robots_checked": robots_checked,
        "fetch_status": status,
        "extraction_method": "html_parser",
        "quality": "research-only",
        "observed_at": observed_at,
        "citation_id": f"page:{_hash(url)}",
        "error": None,
    }


def build_heading_tree(headings: list[tuple[int, str]]) -> list[dict[str, Any]]:
    stack: list[tuple[int, str]] = []
    output: list[dict[str, Any]] = []
    for level, text in headings:
        clean = _normalize_space(text)
        if not clean or level < 1 or level > 4:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, clean))
        output.append({"level": level, "text": clean, "path": [item[1] for item in stack]})
    return output


def render_content_report_section(pages: list[dict[str, Any]], *, markdown_excerpt_chars: int = MARKDOWN_EXCERPT_CHARS) -> str:
    lines = ["## Extracted pages", ""]
    if not pages:
        lines.extend(["No pages extracted.", ""])
        return "\n".join(lines)
    for page in pages:
        lines.append(f"### {_safe_md(str(page.get('title') or page.get('url') or 'Untitled page'))}")
        lines.append(f"- URL: `{page.get('url')}`")
        lines.append(f"- Evidence ID: `{page.get('citation_id')}`")
        lines.append(f"- Quality: `{page.get('quality')}`")
        lines.append(f"- HTTP status: `{page.get('fetch_status')}`")
        if page.get("error"):
            lines.append(f"- Error: {_safe_md(str(page.get('error')))}")
        headings = page.get("headings") or []
        if headings:
            rendered = "; ".join(str(item.get("text") or "") for item in headings[:6] if isinstance(item, dict))
            lines.append(f"- Page headings: {_safe_md(rendered)}")
        excerpt = _safe_md(str(page.get("text_excerpt") or "")[:markdown_excerpt_chars])
        if excerpt:
            lines.append("")
            lines.append(excerpt)
        lines.append("")
    return "\n".join(lines)


class _ContentParser(HTMLParser):
    def __init__(self, *, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.meta_description = ""
        self.canonical_url = ""
        self.text_parts: list[str] = []
        self.heading_pairs: list[tuple[int, str]] = []
        self._tag_stack: list[str] = []
        self._current_heading: tuple[int, list[str]] | None = None
        self._in_title = False
        self._in_script_json_ld = False
        self._script_parts: list[str] = []
        self.json_ld_types: set[str] = set()
        self.internal_link_count = 0
        self.external_link_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {name.lower(): value or "" for name, value in attrs}
        self._tag_stack.append(tag)
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and attr.get("name", "").lower() == "description":
            self.meta_description = attr.get("content", "")
        elif tag == "link" and attr.get("rel", "").lower() == "canonical":
            self.canonical_url = urllib.parse.urljoin(self.base_url, attr.get("href", ""))
        elif tag == "a" and attr.get("href"):
            self._count_link(attr["href"])
        elif tag in {"h1", "h2", "h3", "h4"}:
            self._current_heading = (int(tag[1]), [])
        elif tag == "script" and "ld+json" in attr.get("type", "").lower():
            self._in_script_json_ld = True
            self._script_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2", "h3", "h4"} and self._current_heading is not None:
            level, parts = self._current_heading
            self.heading_pairs.append((level, " ".join(parts)))
            self._current_heading = None
        elif tag == "script" and self._in_script_json_ld:
            self._collect_json_ld("".join(self._script_parts))
            self._in_script_json_ld = False
            self._script_parts = []
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._in_script_json_ld:
            self._script_parts.append(data)
            return
        if self._in_title:
            self.title += data
            return
        if self._current_heading is not None:
            self._current_heading[1].append(data)
        if self._tag_stack and self._tag_stack[-1] in {"script", "style", "noscript"}:
            return
        if data.strip():
            self.text_parts.append(data)

    def _count_link(self, href: str) -> None:
        absolute = urllib.parse.urljoin(self.base_url, href)
        base_host = urllib.parse.urlparse(self.base_url).netloc.lower()
        link_host = urllib.parse.urlparse(absolute).netloc.lower()
        if not link_host or link_host == base_host:
            self.internal_link_count += 1
        else:
            self.external_link_count += 1

    def _collect_json_ld(self, raw: str) -> None:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return
        for item in _iter_json_ld(value):
            typ = item.get("@type") if isinstance(item, dict) else None
            if isinstance(typ, str):
                self.json_ld_types.add(typ)
            elif isinstance(typ, list):
                self.json_ld_types.update(str(part) for part in typ if str(part).strip())


def _iter_json_ld(value: Any):
    if isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield item
        yield value
    elif isinstance(value, list):
        for item in value:
            yield item


def _failed_page(
    url: str,
    *,
    quality: str,
    observed_at: str,
    robots_checked: bool,
    fetch_status: int | None,
    error: str,
) -> dict[str, Any]:
    return {
        "url": url,
        "canonical_url": url,
        "title": None,
        "meta_description": None,
        "word_count": 0,
        "headings": [],
        "text_excerpt": "",
        "internal_link_count": 0,
        "external_link_count": 0,
        "json_ld_types": [],
        "robots_checked": robots_checked,
        "fetch_status": fetch_status,
        "extraction_method": "html_parser",
        "quality": quality,
        "observed_at": observed_at,
        "citation_id": f"page:{_hash(url)}",
        "error": error,
    }


def _robots_allows(url: str, robots_txt: str) -> bool:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(robots_txt.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _looks_paywalled(body: str) -> bool:
    lowered = body.lower()
    return any(phrase in lowered for phrase in ("subscribe or log in", "login to continue", "sign in to continue", "paywall"))


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _safe_md(value: str) -> str:
    value = re.sub(r"(?i)(private_key|authorization|oauth |bearer |basic |cookie|set-cookie|x-api-key|client_secret|password|refresh_token|access_token|api_key|token=)[^\s`]*", "[REDACTED]", value)
    return value.replace("\r", " ").strip()


def _charset(content_type: str | None) -> str:
    if content_type:
        match = re.search(r"charset=([^;\s]+)", content_type, flags=re.I)
        if match:
            return match.group(1)
    return "utf-8"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
