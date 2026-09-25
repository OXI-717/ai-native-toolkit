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


@dataclass(frozen=True)
class ChannelsConfig:
    brand_terms: tuple[str, ...] = ()
    noise_referrers: tuple[str, ...] = ()


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


def _escape_regex_literal(term: str) -> str:
    return "".join(
        f"\\{char}" if char in _REGEX_METACHARACTERS else char for char in term
    )


def brand_regex(terms: tuple[str, ...]) -> str | None:
    cleaned = [term.strip() for term in terms if term and term.strip()]
    if not cleaned:
        return None
    return "(?i)(" + "|".join(_escape_regex_literal(term) for term in cleaned) + ")"
