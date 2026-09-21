"""Topvisor provider boundary for RU Google/Yandex rank observations.

DataForSEO removed every Russian and Belarusian location from all of its APIs in
2022, so the RU contour cannot see Google through it at all. Topvisor is the
replacement for that contour: it serves both engines, all Yandex/Google regions,
and prices a check at 0.09 RUB.

Two properties of the vendor shape this module.

* The API is **stateful**. A check runs against a stored project (keywords,
  engines, regions), not against a single query. So collection is a batch
  operation, and one paid run answers every keyword at once. `fetch_slot` in
  `serp.py` is per-keyword, which is why reads here are served from a snapshot
  fetched once and cached, and why triggering the paid run is a separate,
  explicit call rather than a side effect of reading.
* Errors arrive with **HTTP 200** and a body of
  ``{"result": null, "errors": [...]}``. Treating a status code as success turns
  a failed collection into an empty SERP, and an empty SERP does not read as
  "no data" downstream - it reads as "no competitors". Every response goes
  through `_call`, which raises instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse


TOPVISOR_BASE_URL = "https://api.topvisor.com"
TOPVISOR_API_FAMILY = "topvisor_api"
TOPVISOR_API_VERSION = "v2"
TOPVISOR_SNAPSHOTS_ENDPOINT = "/v2/json/get/snapshots_2/history"
TOPVISOR_PRICE_ENDPOINT = "/v2/json/get/positions_2/checker/price"
TOPVISOR_CHECKER_ENDPOINT = "/v2/json/edit/positions_2/checker/go"
TOPVISOR_REGIONS_ENDPOINT = "/v2/json/get/system_2/common/regions"

# Вендор режет ответ на 100 ключей; потолок страниц — защита от бесконечного
# цикла, а не оценка размера проектов (~1000 ключей — уже 10 страниц).
SNAPSHOT_PAGE_SIZE = 100
SNAPSHOT_MAX_KEYWORDS = 20000

# Vendor keys, from add/positions_2/searchers.
SEARCHER_KEYS = {"yandex": 0, "google": 1, "youtube": 4, "bing": 5}
# 0 desktop, 1 tablet, 2 phone. "__all__" means the protocol slot does not
# distinguish devices; Topvisor always needs a concrete value, and desktop is
# the vendor default.
DEVICE_KEYS = {"desktop": 0, "tablet": 1, "mobile": 2, "phone": 2, "__all__": 0}

# One Google "SERP page" is TOP-10, and depth is charged linearly: TOP-100 costs
# ten times TOP-10 because Google dropped num=100. Yandex pages are TOP-100.
RESULTS_PER_DEPTH_UNIT = {"google": 10, "yandex": 100}


class TopvisorError(RuntimeError):
    """Base class for Topvisor boundary failures."""


class TopvisorApiError(TopvisorError):
    """The API answered 200 with an `errors` array."""

    def __init__(self, code: int | None, message: str, *, path: str) -> None:
        self.code = code
        self.path = path
        super().__init__(f"Topvisor {path} failed [{code}]: {message}")


class TopvisorHttp(Protocol):
    """Injected HTTP boundary. Nothing in this module opens a socket itself."""

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class TopvisorCredentials:
    """Topvisor needs two values, not one: an account id and an API key."""

    user_id: str
    api_key: str

    def headers(self) -> dict[str, str]:
        return {
            "User-Id": self.user_id,
            "Authorization": f"bearer {self.api_key}",
            "Content-Type": "application/json",
        }


def depth_to_region_depth(depth: int, search_engine: str) -> int:
    """Translate a protocol depth into the vendor's `region_depth` units."""

    per_unit = RESULTS_PER_DEPTH_UNIT.get(search_engine, 10)
    if not isinstance(depth, int) or isinstance(depth, bool) or depth <= 0:
        raise TopvisorError("depth must be a positive integer")
    return max(1, math.ceil(depth / per_unit))


def searcher_key(search_engine: str) -> int:
    try:
        return SEARCHER_KEYS[search_engine]
    except KeyError:
        raise TopvisorError(
            f"Topvisor has no searcher key for search engine {search_engine!r}"
        ) from None


def device_key(device: str) -> int:
    try:
        return DEVICE_KEYS[device]
    except KeyError:
        raise TopvisorError(f"Topvisor has no device key for device {device!r}") from None


class PostJsonHttp:
    """Переходник от общего `post_json`-транспорта CLI к `TopvisorHttp`.

    CLI строит транспорты с методом `post_json(endpoint, json=..., headers=...)`
    — там уже живут таймауты и ретраи, поэтому свой HTTP-клиент здесь не нужен.

    GET намеренно не поддерживается, а не сводится к POST: у Topvisor есть
    GET-методы (`get/system_2/common/regions`), и молчаливая подмена глагола
    дала бы не отказ, а ответ не того метода — то есть неверные данные вместо
    внятной ошибки.
    """

    def __init__(self, transport: Any) -> None:
        self._transport = transport

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        if method.upper() != "POST":
            raise TopvisorError(
                f"PostJsonHttp supports POST only; {method} to {path} needs a transport "
                "that can issue it"
            )
        return self._transport.post_json(path, json=body, headers=headers)


class TopvisorClient:
    """Stateful vendor operations: pricing, paid runs, snapshot reads."""

    def __init__(self, http: TopvisorHttp, credentials: TopvisorCredentials) -> None:
        self.http = http
        self.credentials = credentials

    def _call(self, method: str, path: str, body: dict[str, Any]) -> Any:
        response = self.http.request(
            method, path, body=body, headers=self.credentials.headers()
        )
        if not isinstance(response, dict):
            raise TopvisorError(f"Topvisor {path} returned a non-object response.")
        errors = response.get("errors")
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            code = first.get("code") if isinstance(first, dict) else None
            message = str(first.get("string") if isinstance(first, dict) else first)
            raise TopvisorApiError(code, message, path=path)
        if "result" not in response:
            raise TopvisorError(f"Topvisor {path} response has no result field.")
        return response["result"]

    def resolve_region(self, *, search_engine: str, search: str) -> dict[str, Any]:
        """Look up a region. The base is shared across engines: Moscow is id 213
        for both Yandex and Google, so an existing Yandex `lr` needs no
        translation."""

        result = self._call(
            "GET",
            TOPVISOR_REGIONS_ENDPOINT,
            {"searcher_key": searcher_key(search_engine), "search": search, "limit": 10},
        )
        if not isinstance(result, list) or not result:
            raise TopvisorError(f"Topvisor knows no region matching {search!r}")
        return dict(result[0])

    def estimate_price(self, *, project_id: int | str, do_snapshots: bool = True) -> float:
        """Free pre-flight. The budget guard must ask the vendor rather than
        model the price itself: depth multiplies the cost linearly."""

        result = self._call(
            "POST",
            TOPVISOR_PRICE_ENDPOINT,
            {
                "filters": [
                    {"name": "id", "operator": "EQUALS", "values": [str(project_id)]}
                ],
                "do_snapshots": int(bool(do_snapshots)),
            },
        )
        prices = (result or {}).get("pricesByUsers", {}) if isinstance(result, dict) else {}
        total = 0.0
        for entry in prices.values():
            if isinstance(entry, dict) and entry.get("price") is not None:
                total += float(entry["price"])
        return total

    def run_check(self, *, project_id: int | str, do_snapshots: bool = True) -> list[str]:
        """Paid. Callers own the budget guard and the live-mode confirmation."""

        result = self._call(
            "POST",
            TOPVISOR_CHECKER_ENDPOINT,
            {
                "filters": [
                    {"name": "id", "operator": "EQUALS", "values": [str(project_id)]}
                ],
                "do_snapshots": int(bool(do_snapshots)),
            },
        )
        ids = (result or {}).get("projectIds", []) if isinstance(result, dict) else []
        return [str(item) for item in ids]

    def fetch_snapshots(
        self,
        *,
        project_id: int | str,
        search_engine: str,
        region_key: int | str,
        region_lang: str,
        device: str,
        date: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Read the stored SERP for one engine/region/device/date.

        The region is addressed here by the quadruple searcher/key/lang/device,
        while `edit/positions_2/searchers_regions` addresses the same region by
        its ordinal `region_index`. Two keys for one entity; sending the wrong
        one yields a 2001/2003 error rather than a wrong answer, which is why
        both travel together through this module.

        The endpoint caps a response at 100 keywords, so the snapshot is paged.
        Without paging a project larger than that answers `2003 Maximum keywords
        per query` for the whole read - loud, but only because the vendor
        happens to reject it; a cap that silently truncated instead would have
        produced a SERP missing most of its competitors and looking complete.
        Paging stops at an explicit ceiling for the same reason: an unbounded
        loop against a paging bug is worse than a named failure.
        """

        request = {
            "project_id": int(project_id),
            "searcher_key": searcher_key(search_engine),
            "region_key": int(region_key),
            "region_lang": region_lang,
            "region_device": device_key(device),
            "date1": date,
            "date2": date,
        }
        snapshot: dict[str, list[dict[str, Any]]] = {}
        offset = 0
        while True:
            page = parse_snapshots(
                self._call(
                    "POST",
                    TOPVISOR_SNAPSHOTS_ENDPOINT,
                    {**request, "limit": SNAPSHOT_PAGE_SIZE, "offset": offset},
                ),
                date=date,
            )
            if not page:
                break
            snapshot.update(page)
            if len(page) < SNAPSHOT_PAGE_SIZE:
                break
            offset += SNAPSHOT_PAGE_SIZE
            if offset >= SNAPSHOT_MAX_KEYWORDS:
                raise TopvisorError(
                    f"snapshot for project {project_id} exceeds {SNAPSHOT_MAX_KEYWORDS} "
                    "keywords; refusing to page further rather than returning a "
                    "silently truncated SERP"
                )
        return snapshot


def parse_snapshots(result: Any, *, date: str) -> dict[str, list[dict[str, Any]]]:
    """Normalize `get/snapshots_2/history` into rows the SERP adapter understands.

    Vendor shape - the whole SERP, present whether or not the tracked domain is
    in it, which is what makes share-of-voice computable::

        {"keywords": [{"name": "...", "snapshotsData": {
            "2026-08-19:1:2": {"url": "...", "domain": "..."}, ...}}]}

    The key is ``date:rank:region_index``. Titles and snippets are absent from
    snapshots; the adapter treats both as optional and will report
    `serp_features_supported = false` for this provider, which is honest rather
    than degraded.
    """

    if not isinstance(result, dict):
        raise TopvisorError("Topvisor snapshots result must be an object.")
    keywords = result.get("keywords")
    if keywords is None:
        return {}
    if not isinstance(keywords, list):
        raise TopvisorError("Topvisor snapshots keywords must be a list.")

    by_keyword: dict[str, list[dict[str, Any]]] = {}
    for entry in keywords:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if not name:
            continue
        rows: list[dict[str, Any]] = []
        snapshots = entry.get("snapshotsData")
        if isinstance(snapshots, dict):
            for key, value in snapshots.items():
                parsed = _parse_snapshot_key(str(key))
                if parsed is None or parsed[0] != date:
                    continue
                if not isinstance(value, dict):
                    continue
                url = str(value.get("url") or "")
                if not url:
                    continue
                rows.append(
                    {
                        "position": parsed[1],
                        "url": url,
                        "domain": str(value.get("domain") or _host(url)),
                    }
                )
        rows.sort(key=lambda row: row["position"])
        by_keyword[name] = rows
    return by_keyword


def _parse_snapshot_key(key: str) -> tuple[str, int] | None:
    parts = key.split(":")
    if len(parts) < 2:
        return None
    try:
        rank = int(parts[1])
    except ValueError:
        return None
    return parts[0], rank


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


@dataclass
class TopvisorSnapshotTransport:
    """`SerpTransport` over stored Topvisor snapshots.

    Read-only by construction: it never triggers a paid check. A slot whose
    keyword is absent from the snapshot returns an empty result set together
    with `keyword_missing`, so the caller can tell "collected, ranked nowhere"
    apart from "never collected" - conflating the two is what silently turns a
    failed run into a clean-looking zero.
    """

    client: TopvisorClient
    project_id: int | str
    date: str
    region_lang: str = "ru"
    _cache: dict[tuple[str, str, str], dict[str, list[dict[str, Any]]]] = field(
        default_factory=dict, init=False, repr=False
    )

    def post_json(
        self,
        endpoint: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        del endpoint, headers  # credentials and routing belong to the client
        region = json.get("region") or {}
        region_key = str(region.get("id") or "")
        if not region_key:
            raise TopvisorError("SERP slot has no region id; Topvisor requires one.")
        search_engine = str(json.get("search_engine") or "")
        device = str((json.get("device") or {}).get("value") or "__all__")
        cache_key = (search_engine, region_key, device)
        if cache_key not in self._cache:
            self._cache[cache_key] = self.client.fetch_snapshots(
                project_id=self.project_id,
                search_engine=search_engine,
                region_key=region_key,
                region_lang=self.region_lang,
                device=device,
                date=self.date,
            )
        snapshot = self._cache[cache_key]
        keyword = str(json.get("query_text") or "")
        depth = int(json.get("depth") or 0)
        rows = snapshot.get(keyword)
        return {
            "request_id": f"topvisor:{self.project_id}:{self.date}",
            "results": [] if rows is None else rows[:depth] if depth > 0 else rows,
            "keyword_missing": rows is None,
        }


class TopvisorSerpProviderAdapter:
    """Адаптер Topvisor под общий шов `SerpProviderAdapter` из `competitors.py`.

    Аудит разбирает `rows` одинаково для всех движков, поэтому форма ответа
    совпадает с DataForSEO и Яндексом.

    Читает уже снятые снимки и **никогда не запускает платную проверку**: у
    Topvisor съём идёт по всему проекту сразу, поэтому запуск из per-keyword
    цикла означал бы один платный прогон на каждый ключ. Стоимость чтения — 0.

    Отсутствие ключа в снимке и отсутствие позиций у ключа — разные вещи:
    первое отдаётся как `quality = "unsupported"` с явной ошибкой, второе —
    как `live` с пустым `rows`. Схлопывание их превратило бы несобранный
    контур в «конкурентов нет».
    """

    def __init__(
        self,
        transport: TopvisorSnapshotTransport,
        *,
        search_engine: str = "google",
        language_code: str = "ru",
    ) -> None:
        self._transport = transport
        self._search_engine = search_engine
        self._language_code = language_code

    def fetch_organic_serp(
        self,
        keyword: str,
        location_code: int | str | None = None,
        location_name: str | None = None,
        language_code: str | None = None,
        device: str = "desktop",
        depth: int = 10,
    ) -> dict[str, Any]:
        payload = {
            "query_text": keyword,
            "region": {"id": location_code, "name": location_name or ""},
            "language": language_code or self._language_code,
            "search_engine": self._search_engine,
            "device": {"value": device, "supported": True},
            "depth": depth,
        }
        try:
            page = self._transport.post_json("", json=payload, headers={})
        except Exception as exc:  # сеть/вендор — деградируем в статус, не в traceback
            return {
                "rows": [],
                "quality": "unsupported",
                "cost_usd": 0.0,
                "request_ids": [],
                "errors": [
                    {
                        "code": "TOPVISOR_SNAPSHOT_REQUEST_FAILED",
                        "message": f"{exc.__class__.__name__}: {exc}",
                        "details": {"keyword": keyword, "region": location_code},
                    }
                ],
            }
        if page.get("keyword_missing"):
            return {
                "rows": [],
                "quality": "unsupported",
                "cost_usd": 0.0,
                "request_ids": [str(page.get("request_id") or "")],
                "errors": [
                    {
                        "code": "TOPVISOR_KEYWORD_NOT_IN_SNAPSHOT",
                        "message": "keyword is absent from the stored snapshot; run a check first",
                        "details": {"keyword": keyword, "region": location_code},
                    }
                ],
            }
        return {
            "rows": [
                {
                    "rank_absolute": row["position"],
                    "result_type": "organic",
                    "url": row["url"],
                    # Снимки Topvisor не содержат заголовков и сниппетов —
                    # это свойство источника, а не потеря данных.
                    "title": None,
                    "description": None,
                    "serp_features": ["organic"],
                    "quality": "live",
                }
                for row in page.get("results") or []
            ],
            "quality": "live",
            "cost_usd": 0.0,
            "request_ids": [str(page.get("request_id") or "")],
            "errors": [],
        }
