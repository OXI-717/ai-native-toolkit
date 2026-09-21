"""Разбор наборов ключей с кластерами и частотностью.

Кластер — это то измерение, в котором маркетинг реально принимает решения:
«просели три запроса» и «просел весь кластер на 6 930 показов» требуют разных
действий (правка мета-тега против отдельной посадочной страницы).

Канонический формат:

    # cluster: калькулятор вилок
    калькулятор вилок    # yws=5772 yws_exact=3327

Поддерживается и формат выгрузок, уже лежащих в проектах:

    # --- кластер: калькулятор вилок (24 фразы, YWS 6930) ---
    калькулятор вилок    # YWS 5772 / 3327

Файл без разметки читается как раньше: один кластер `None`, частотность `None`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_CLUSTER_CANON = re.compile(r"^#\s*cluster:\s*(?P<name>.+?)\s*$", re.IGNORECASE)
_CLUSTER_LEGACY = re.compile(r"^#\s*-*\s*кластер:\s*(?P<name>.+?)\s*(?:\(.*\))?\s*-*\s*$", re.IGNORECASE)
_YWS_CANON = re.compile(r"yws\s*=\s*(?P<base>\d+)(?:\s+yws_exact\s*=\s*(?P<exact>\d+))?", re.IGNORECASE)
# Для не-яндексовых контуров частотность приходит из другого источника
# (Google search volume), и называть её yws было бы неверно.
_SV_CANON = re.compile(r"\bsv\s*=\s*(?P<base>\d+)(?:\s+sv_exact\s*=\s*(?P<exact>\d+))?", re.IGNORECASE)
_YWS_LEGACY = re.compile(r"YWS\s+(?P<base>\d+)\s*(?:/\s*(?P<exact>\d+))?", re.IGNORECASE)


@dataclass(frozen=True)
class ClusteredKeyword:
    keyword: str
    cluster: str | None = None
    # Частотность запроса в его рынке: Wordstat для Яндекса, search volume
    # для Google. Единица подписана в шапке файла ключей.
    yws: int | None = None
    yws_exact: int | None = None


def parse_keyword_file(path: Path) -> list[ClusteredKeyword]:
    """Читает файл ключей, сохраняя кластер и частотность, если они размечены."""
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
    """Суммарная частотность по кластеру — вес темы, а не число фраз в ней."""
    demand: dict[str, int] = {}
    for item in keywords:
        name = item.cluster or "__без кластера__"
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
