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

# The vendor truncates responses at 100 keywords; the page cap guards against an
# infinite loop rather than estimating project size (~1000 keywords is already
# 10 pages).
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
    ) -> dict[str, Any]: ...


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
        raise TopvisorError(
            f"Topvisor has no device key for device {device!r}"
        ) from None


class PostJsonHttp:
    """Bridge from the CLI's shared `post_json` transport to `TopvisorHttp`.

    The CLI builds transports with a `post_json(endpoint, json=..., headers=...)`
    method — timeouts and retries already live there, so no dedicated HTTP client
    is needed here.

    GET is intentionally unsupported rather than being folded into POST:
    Topvisor has GET methods (`get/system_2/common/regions`), and silently
    substituting the verb would produce not a refusal but a response from the
    wrong method — invalid data instead of a clear error.
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
            {
                "searcher_key": searcher_key(search_engine),
                "search": search,
                "limit": 10,
            },
        )
        if not isinstance(result, list) or not result:
            raise TopvisorError(f"Topvisor knows no region matching {search!r}")
        return dict(result[0])

    def estimate_price(
        self, *, project_id: int | str, do_snapshots: bool = True
    ) -> float:
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
        prices = result.get("pricesByUsers") if isinstance(result, dict) else None
        if not isinstance(prices, dict) or not prices:
            raise TopvisorError("Topvisor price response has no pricesByUsers")
        total = 0.0
        for entry in prices.values():
            value = entry.get("price") if isinstance(entry, dict) else None
            if isinstance(value, bool):
                raise TopvisorError("Topvisor returned an invalid price")
            try:
                price = float(value)
            except (TypeError, ValueError):
                raise TopvisorError("Topvisor returned an invalid price") from None
            if not math.isfinite(price) or price < 0:
                raise TopvisorError("Topvisor returned an invalid price")
            total += price
        if not math.isfinite(total):
            raise TopvisorError("Topvisor returned an invalid total price")
        return total

    def run_check(
        self, *, project_id: int | str, do_snapshots: bool = True
    ) -> list[str]:
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
        ids = (
            result.get("projectsIds", result.get("projectIds", []))
            if isinstance(result, dict)
            else []
        )
        if not isinstance(ids, list) or str(project_id) not in [str(i) for i in ids]:
            raise TopvisorError("Topvisor did not acknowledge the requested project")
        return [str(item) for item in ids]

    def project_state(self, *, project_id: int | str) -> dict[str, Any]:
        """Free readiness/date read. No stored snapshots are treated as a run."""
        result = self._call(
            "POST",
            "/v2/json/get/projects_2/projects",
            {
                "filters": [
                    {"name": "id", "operator": "EQUALS", "values": [str(project_id)]}
                ],
                "fields": ["id", "status_positions", "positions_time"],
                "show_searchers_and_regions": 1,
            },
        )
        if (
            not isinstance(result, list)
            or len(result) != 1
            or str(result[0].get("id")) != str(project_id)
        ):
            raise TopvisorError("Topvisor project state is missing")
        return result[0]

    def keyword_inventory(self, *, project_id):
        """Free inventory retaining IDs for explicit operator synchronization."""
        inventory = []
        for offset in range(0, SNAPSHOT_MAX_KEYWORDS, SNAPSHOT_PAGE_SIZE):
            rows = self._call(
                "POST",
                "/v2/json/get/keywords_2/keywords",
                {
                    "project_id": int(project_id),
                    "fields": ["id", "name"],
                    "orders": [{"name": "id", "direction": "ASC"}],
                    "limit": SNAPSHOT_PAGE_SIZE,
                    "offset": offset,
                },
            )
            if not isinstance(rows, list) or any(
                not isinstance(r, dict)
                or not isinstance(r.get("name"), str)
                or not str(r.get("id", "")).isdigit()
                for r in rows
            ):
                raise TopvisorError("Invalid TopVisor keyword inventory")
            inventory.extend(rows)
            if len(rows) < SNAPSHOT_PAGE_SIZE:
                if len({str(r["id"]) for r in inventory}) != len(inventory):
                    raise TopvisorError("Duplicate IDs in TopVisor inventory")
                return inventory
        raise TopvisorError("Keyword inventory exceeds pagination limit")

    def keyword_names(self, *, project_id):
        return [
            " ".join(row["name"].casefold().split())
            for row in self.keyword_inventory(project_id=project_id)
        ]

    def rename_keyword(self, *, project_id, keyword_id, name):
        return self._call(
            "POST",
            "/v2/json/edit/keywords_2/keywords/rename",
            {
                "project_id": int(project_id),
                "id": int(keyword_id),
                "name": name,
            },
        )

    def delete_keyword(self, *, project_id, keyword_id):
        return self._call(
            "POST",
            "/v2/json/del/keywords_2/keywords",
            {
                "project_id": int(project_id),
                "filters": [
                    {"name": "id", "operator": "EQUALS", "values": [int(keyword_id)]}
                ],
            },
        )

    def import_keywords(self, *, project_id, names):
        import csv
        import io

        output = io.StringIO(newline="")
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow(["name"])
        writer.writerows([name] for name in names)
        return self._call(
            "POST",
            "/v2/json/add/keywords_2/keywords/import",
            {
                "project_id": int(project_id),
                "keywords": output.getvalue(),
            },
        )

    def fetch_positions(self, *, project_id, region_index, date):
        """Free position history; '--' means outside the checked depth, not missing."""
        positions = {}
        for offset in range(0, SNAPSHOT_MAX_KEYWORDS, SNAPSHOT_PAGE_SIZE):
            result = self._call(
                "POST",
                "/v2/json/get/positions_2/history",
                {
                    "project_id": int(project_id),
                    "regions_indexes": [int(region_index)],
                    "dates": [date],
                    "type_range": 100,
                    "limit": SNAPSHOT_PAGE_SIZE,
                    "offset": offset,
                    "positions_fields": ["position", "relevant_url"],
                },
            )
            keywords = result.get("keywords") if isinstance(result, dict) else None
            if not isinstance(keywords, list):
                raise TopvisorError("Invalid position history")
            for row in keywords:
                value = (row.get("positionsData") or {}).get(
                    f"{date}:{project_id}:{region_index}", {}
                )
                rank = value.get("position")
                if rank == "--":
                    positions[row["name"]] = "outside_top10"
                elif str(rank).isdigit() and int(rank) > 0:
                    positions[row["name"]] = int(rank)
            if len(keywords) < SNAPSHOT_PAGE_SIZE:
                return positions
        raise TopvisorError("Position history exceeds pagination limit")

    def fetch_snapshots(
        self,
        *,
        project_id: int | str,
        search_engine: str,
        region_key: int | str,
        region_lang: str,
        device: str,
        date: str,
        require_date: bool = False,
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
            result = self._call(
                "POST",
                TOPVISOR_SNAPSHOTS_ENDPOINT,
                {**request, "limit": SNAPSHOT_PAGE_SIZE, "offset": offset},
            )
            if require_date and (
                not isinstance(result, dict) or date not in (result.get("dates") or [])
            ):
                return {}
            page = parse_snapshots(result, date=date)
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
                known = bool(url)
                domain = str(value.get("domain") or _host(url)).lower()
                if not url:
                    if not domain or any(c in domain for c in "/:@?# " + chr(92)):
                        continue
                    url = f"https://{domain}/"
                rows.append(
                    {
                        "position": parsed[1],
                        "url": url,
                        "domain": domain,
                        **({"url_known": False} if not known else {}),
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
    return (parts[0], rank) if rank >= 1 else None


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
    """Topvisor adapter for the shared `SerpProviderAdapter` seam from `competitors.py`.

    The audit parses `rows` the same way for every engine, so the response shape
    matches DataForSEO and Yandex.

    Reads already-taken snapshots and **never triggers a paid check**: Topvisor
    scans the whole project at once, so triggering a check from the per-keyword
    loop would mean one paid run per keyword. Read cost is 0.

    A keyword missing from the snapshot and a keyword with no positions are
    different things: the former is returned as `quality = "unsupported"` with an
    explicit error, the latter as `live` with empty `rows`. Collapsing them would
    turn an uncollected market into "there are no competitors".
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
        except (
            Exception
        ) as exc:  # network/vendor — degrade to a status, not a traceback
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
                    # Topvisor snapshots contain no titles or snippets —
                    # that is a property of the source, not a data loss.
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
