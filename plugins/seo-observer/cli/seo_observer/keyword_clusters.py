"""Parsing of keyword sets with clusters and search volume.

A cluster is the dimension in which marketing actually makes decisions:
"three queries dropped" and "the whole cluster dropped by 6,930 impressions"
require different actions (a meta-tag fix versus a dedicated landing page).

Canonical format:

    # cluster: sofa calculator
    sofa calculator    # yws=5772 yws_exact=3327

The export format already found in projects is also supported:

    # --- кластер: sofa calculator (24 phrases, YWS 6930) ---
    sofa calculator    # YWS 5772 / 3327

A file without markup is read as before: a single `None` cluster, `None` volume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_CLUSTER_CANON = re.compile(r"^#\s*cluster:\s*(?P<name>.+?)\s*$", re.IGNORECASE)
_CLUSTER_LEGACY = re.compile(r"^#\s*-*\s*кластер:\s*(?P<name>.+?)\s*(?:\(.*\))?\s*-*\s*$", re.IGNORECASE)
_YWS_CANON = re.compile(r"yws\s*=\s*(?P<base>\d+)(?:\s+yws_exact\s*=\s*(?P<exact>\d+))?", re.IGNORECASE)
# For non-Yandex markets the volume comes from a different source
# (Google search volume), so calling it yws would be wrong.
_SV_CANON = re.compile(r"\bsv\s*=\s*(?P<base>\d+)(?:\s+sv_exact\s*=\s*(?P<exact>\d+))?", re.IGNORECASE)
_YWS_LEGACY = re.compile(r"YWS\s+(?P<base>\d+)\s*(?:/\s*(?P<exact>\d+))?", re.IGNORECASE)


@dataclass(frozen=True)
class ClusteredKeyword:
    keyword: str
    cluster: str | None = None
    # Query volume in its market: Wordstat for Yandex, search volume
    # for Google. The unit is labeled in the keyword file header.
    yws: int | None = None
    yws_exact: int | None = None


def parse_keyword_file(path: Path) -> list[ClusteredKeyword]:
    """Reads a keyword file, preserving cluster and volume markup if present."""
    keywords: list[ClusteredKeyword] = []
    current_cluster: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            cluster = _match_cluster(line)
            if cluster is not None:
                current_cluster = cluster
            continue
        keyword, _, comment = line.partition("#")
        keyword = keyword.strip()
        if not keyword:
            continue
        base, exact = _match_frequency(comment)
        keywords.append(
            ClusteredKeyword(
                keyword=keyword, cluster=current_cluster, yws=base, yws_exact=exact
            )
        )
    return keywords


def cluster_demand(keywords: list[ClusteredKeyword]) -> dict[str, int]:
    """Total volume per cluster — the weight of a topic, not the phrase count."""
    demand: dict[str, int] = {}
    for item in keywords:
        name = item.cluster or "__no cluster__"
        demand[name] = demand.get(name, 0) + (item.yws or 0)
    return demand


def _match_cluster(line: str) -> str | None:
    for pattern in (_CLUSTER_CANON, _CLUSTER_LEGACY):
        found = pattern.match(line)
        if found:
            name = found.group("name").strip(" -—")
            return name or None
    return None


def _match_frequency(comment: str) -> tuple[int | None, int | None]:
    for pattern in (_YWS_CANON, _SV_CANON, _YWS_LEGACY):
        found = pattern.search(comment)
        if found:
            exact = found.group("exact")
            return int(found.group("base")), int(exact) if exact else None
    return None, None
