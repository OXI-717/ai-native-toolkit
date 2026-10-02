"""Traffic channel taxonomy shared by GA4 visits and server-side outcomes.

Channel groups follow GA4 default channel group names so that visits
(`sessionDefaultChannelGroup`) and outcome rows (source/medium) land in the
same buckets. Only the brand split and noise referrers are tenant-specific.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SOCIAL_SOURCES = frozenset(
    {
        "t.me",
        "telegram",
        "telegram.org",
        "vk.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "x.com",
        "twitter.com",
        "linkedin.com",
        "reddit.com",
        "dzen.ru",
    }
)
SOCIAL_MEDIUMS = frozenset({"social", "sm", "social-network", "social-media"})
PAID_SEARCH_MEDIUMS = frozenset({"cpc", "ppc", "paidsearch", "paid-search"})
DIRECT_VALUES = frozenset({"(direct)", "direct", "(none)", "none"})

# Characters that must be escaped for the pattern to be interpreted literally
# by RE2/Google Search Console. Spaces and other non-metacharacters are kept
# literal so multi-word brand terms match without unexpected RE2 failures.
_REGEX_METACHARACTERS = frozenset("\\.+*?()|[]{}^$")


SEARCH_HOST_SUFFIXES = (
    "google.com", "google.ru", "yandex.ru", "yandex.com", "ya.ru", "bing.com",
    "duckduckgo.com", "search.yahoo.com", "ecosia.org", "search.brave.com",
)
AI_ASSISTANT_HOSTS = (
    "chatgpt.com", "chat.openai.com", "perplexity.ai", "gemini.google.com",
    "copilot.microsoft.com", "claude.ai", "chat.deepseek.com",
)


@dataclass(frozen=True)
class ChannelsConfig:
    brand_terms: tuple[str, ...] = ()
    noise_referrers: tuple[str, ...] = ()
    self_domains: tuple[str, ...] = ()
    app_paths: tuple[str, ...] = ()


def channel_group(source: str | None, medium: str | None) -> str:
    src = (source or "").strip().lower()
    med = (medium or "").strip().lower()
    if not src and not med:
        return "Unassigned"
    if src in DIRECT_VALUES and med in DIRECT_VALUES:
        return "Direct"
    if med == "organic":
        return "Organic Search"
    if src in SOCIAL_SOURCES and med in PAID_SEARCH_MEDIUMS:
        return "Paid Social"
    if med in PAID_SEARCH_MEDIUMS:
        return "Paid Search"
    if med in SOCIAL_MEDIUMS or src in SOCIAL_SOURCES:
        return "Organic Social"
    if med == "email":
        return "Email"
    if med == "referral":
        return "Referral"
    return "Unassigned"


def channel_slug(group: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", group.strip().lower()).strip("_") or "unassigned"


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        pattern = pattern.lower().lstrip(".")
        if pattern.startswith("*."):
            base = pattern[2:]
            if host == base or host.endswith("." + base):
                return True
        elif host == pattern or host.endswith("." + pattern):
            return True
    return False


def referrer_channel(host: str | None, *, self_domains: tuple[str, ...] = ()) -> str:
    value = (host or "").strip().lower()
    if not value:
        return "direct"
    if self_domains and _host_matches(value, self_domains):
        return "internal"
    if _host_matches(value, AI_ASSISTANT_HOSTS):
        return "ai_assistant"
    if _host_matches(value, SEARCH_HOST_SUFFIXES):
        return "organic_search"
    if value in SOCIAL_SOURCES or _host_matches(value, tuple(SOCIAL_SOURCES)):
        return "organic_social"
    return "referral"


def event_channel(
    utm_source: str | None,
    utm_medium: str | None,
    referrer_host: str | None,
    *,
    self_domains: tuple[str, ...] = (),
) -> str:
    if (utm_source or "").strip() or (utm_medium or "").strip():
        return channel_slug(channel_group(utm_source, utm_medium))
    return referrer_channel(referrer_host, self_domains=self_domains)


def app_path_match(path: str, patterns: tuple[str, ...]) -> bool:
    """True when ``path`` matches a pattern as a path prefix.

    ``*`` matches exactly one path segment, so ``/*/signin`` matches
    ``/en/signin`` and ``/en/signin/confirm`` but not ``/signin`` or
    ``/en/other/signin``.
    """
    segments = [segment for segment in path.split("/") if segment]
    for pattern in patterns:
        wanted = [segment for segment in pattern.split("/") if segment]
        if not wanted or len(wanted) > len(segments):
            continue
        if all(
            want == "*" or want == segment
            for want, segment in zip(wanted, segments)
        ):
            return True
    return False


def _escape_regex_literal(term: str) -> str:
    return "".join(
        f"\\{char}" if char in _REGEX_METACHARACTERS else char for char in term
    )


def brand_regex(terms: tuple[str, ...]) -> str | None:
    cleaned = [term.strip() for term in terms if term and term.strip()]
    if not cleaned:
        return None
    return "(?i)(" + "|".join(_escape_regex_literal(term) for term in cleaned) + ")"
