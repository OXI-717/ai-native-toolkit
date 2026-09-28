"""Deterministic platform-type directory for AI citation domains.

Domains that AI answers cite are classified into a fixed set of external
platform types. The directory is a static, reviewable mapping; anything not
listed resolves to ``unknown`` — never a guessed type.
"""

from __future__ import annotations

import urllib.parse

PLATFORM_TYPES = frozenset(
    {"reviews", "forum_qa", "aggregator", "media", "marketplace", "unknown"}
)

PLATFORM_DIRECTORY: dict[str, str] = {
    # Review platforms and local-review maps (Яндекс Карты, 2ГИС, Отзовик, ...).
    "2gis.com": "reviews",
    "2gis.ru": "reviews",
    "flamp.ru": "reviews",
    "irecommend.com": "reviews",
    "irecommend.ru": "reviews",
    "maps.google.com": "reviews",
    "maps.yandex.com": "reviews",
    "maps.yandex.ru": "reviews",
    "otzovik.com": "reviews",
    "otzyv.ru": "reviews",
    "tripadvisor.com": "reviews",
    "tripadvisor.ru": "reviews",
    "trustpilot.com": "reviews",
    "yell.ru": "reviews",
    "zoon.ru": "reviews",
    # Forums and Q&A (Reddit, Quora, Хабр Q&A, Пикабу, ...).
    "askubuntu.com": "forum_qa",
    "otvet.mail.ru": "forum_qa",
    "pikabu.ru": "forum_qa",
    "qna.habr.com": "forum_qa",
    "quora.com": "forum_qa",
    "reddit.com": "forum_qa",
    "serverfault.com": "forum_qa",
    "stackexchange.com": "forum_qa",
    "stackoverflow.com": "forum_qa",
    "superuser.com": "forum_qa",
    # Aggregators and curated review/comparison directories.
    "alternativeto.net": "aggregator",
    "capterra.com": "aggregator",
    "crozdesk.com": "aggregator",
    "g2.com": "aggregator",
    "getapp.com": "aggregator",
    "producthunt.com": "aggregator",
    "slant.co": "aggregator",
    "softwareadvice.com": "aggregator",
    "trustradius.com": "aggregator",
    # Media and industry publications.
    "bloomberg.com": "media",
    "cnews.ru": "media",
    "cossa.ru": "media",
    "dtf.ru": "media",
    "forbes.com": "media",
    "forbes.ru": "media",
    "habr.com": "media",
    "interfax.ru": "media",
    "ixbt.com": "media",
    "kommersant.ru": "media",
    "lenta.ru": "media",
    "medium.com": "media",
    "nytimes.com": "media",
    "rbc.ru": "media",
    "sostav.ru": "media",
    "tass.ru": "media",
    "techcrunch.com": "media",
    "theguardian.com": "media",
    "theverge.com": "media",
    "vc.ru": "media",
    "wired.com": "media",
    # Marketplaces.
    "aliexpress.com": "marketplace",
    "aliexpress.ru": "marketplace",
    "allegro.pl": "marketplace",
    "amazon.com": "marketplace",
    "avito.ru": "marketplace",
    "ebay.com": "marketplace",
    "etsy.com": "marketplace",
    "flipkart.com": "marketplace",
    "market.yandex.ru": "marketplace",
    "mercadolibre.com": "marketplace",
    "ozon.ru": "marketplace",
    "rakuten.com": "marketplace",
    "taobao.com": "marketplace",
    "walmart.com": "marketplace",
    "wildberries.ru": "marketplace",
}


def normalize_citation_domain(domain: object) -> str:
    """Return the normalized host form of a citation domain.

    Accepts bare domains and URL-ish values; strips scheme, port, path, a
    single ``www.`` prefix, and a trailing dot. Returns ``""`` for unusable
    input.
    """
    value = str(domain or "").strip().lower()
    if "://" in value or value.startswith("//"):
        try:
            value = urllib.parse.urlsplit(value).hostname or ""
        except ValueError:
            return ""
    else:
        value = value.split("/", 1)[0].split(":", 1)[0]
    value = value.rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


def classify_platform_domain(domain: object) -> str:
    """Classify a citation domain into a platform type.

    An exact directory match wins; otherwise the leftmost label is stripped
    progressively (``community.reddit.com`` → ``reddit.com``). Anything
    unmatched resolves to ``unknown`` — the classifier never guesses.
    """
    candidate = normalize_citation_domain(domain)
    while candidate:
        platform_type = PLATFORM_DIRECTORY.get(candidate)
        if platform_type is not None:
            return platform_type
        _, _, candidate = candidate.partition(".")
    return "unknown"
