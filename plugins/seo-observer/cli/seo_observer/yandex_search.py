"""Живой транспорт для Yandex Cloud Search API v2.

`serp.py` намеренно провайдер-нейтрален: `SerpAdapter` формирует канонический
дескриптор запроса и ждёт от транспорта канонический же ответ
(`{"request_id", "results": [{"position", "url", "title", "snippet"}]}`).
Этот модуль — переходник между каноническим форматом и реальным API Яндекса.

Проверено живым API 2026-07-31:

* авторизация — `Api-Key <key>`; тот же ключ с `OAuth` даёт HTTP 401
  "IAM token or API key has to be passed in request";
* `region` действует только на верхнем уровне тела (внутри `query` игнорируется):
  213/Москва и 2/Санкт-Петербург дают разный топ;
* ответ приходит как base64 в поле `rawData`, внутри — XML `<yandexsearch>`.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Protocol
from xml.etree.ElementTree import Element, ParseError

# Ответ приходит от внешнего API, поэтому разбор идёт через defusedxml:
# stdlib-парсер по умолчанию уязвим к раскрытию сущностей (billion laughs)
# и внешним ссылкам в DTD.
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as _xml_fromstring

DEFAULT_BASE_URL = "https://searchapi.api.cloud.yandex.net"
YANDEX_SEARCH_ENDPOINT = "/v2/web/search"
DEFAULT_TIMEOUT_SECONDS = 40

# Яндекс различает поисковые типы по рынку, а не по языку интерфейса.
_SEARCH_TYPE_BY_LANGUAGE = {
    "ru": "SEARCH_TYPE_RU",
    "be": "SEARCH_TYPE_BE",
    "kk": "SEARCH_TYPE_KK",
    "uz": "SEARCH_TYPE_UZ",
    "tr": "SEARCH_TYPE_TR",
}
_DEFAULT_SEARCH_TYPE = "SEARCH_TYPE_COM"

# `generate_protocol_slots` подставляет этот маркер, когда срез не разбит по
# регионам/устройствам. Отправлять его в API буквально нельзя.
_ALL_SENTINEL = "__all__"

_REQUIRED_AUTH_SCHEME = "Api-Key"


class JsonTransport(Protocol):
    """Контракт транспорта, принятый в плагине (`_JsonHttpTransport` в cli.py):
    относительный endpoint, базовый URL держит сам транспорт."""

    def post_json(
        self, endpoint: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        ...


def build_yandex_request(payload: dict[str, Any], *, folder_id: str | None = None) -> dict[str, Any]:
    """Канонический дескриптор -> тело запроса Yandex Search API v2."""
    language = str(payload.get("language") or "").lower()
    depth = int(payload.get("depth") or 10)
    request: dict[str, Any] = {
        "query": {
            "searchType": _SEARCH_TYPE_BY_LANGUAGE.get(language, _DEFAULT_SEARCH_TYPE),
            "queryText": str(payload.get("query_text") or ""),
        },
        "responseFormat": "FORMAT_XML",
        "groupSpec": {"groupsOnPage": str(depth), "docsInGroup": "1"},
    }
    region = (payload.get("region") or {}).get("id")
    if region and str(region) != _ALL_SENTINEL:
        request["region"] = str(region)
    if folder_id:
        request["folderId"] = folder_id
    return request


def parse_yandex_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Ответ Yandex Search API v2 -> канонический формат для `SerpAdapter`."""
    if not isinstance(raw, dict) or "rawData" not in raw:
        raise ValueError(
            "Yandex Search API ответил без поля rawData; получены ключи: "
            f"{sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
        )
    try:
        xml_bytes = base64.b64decode(str(raw["rawData"]), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Yandex Search API вернул нечитаемый base64 в rawData: {exc}") from exc
    try:
        root = _xml_fromstring(xml_bytes.decode("utf-8", "replace"))
    except DefusedXmlException as exc:
        raise ValueError(
            f"Yandex Search API вернул XML с запрещённой конструкцией (DTD/сущности): {exc}"
        ) from exc
    except ParseError as exc:
        raise ValueError(f"Yandex Search API вернул невалидный XML: {exc}") from exc

    results: list[dict[str, Any]] = []
    for position, doc in enumerate(root.iterfind(".//doc"), start=1):
        results.append(
            {
                "position": position,
                "url": (doc.findtext("url") or "").strip(),
                "domain": (doc.findtext("domain") or "").strip(),
                "title": _flatten(doc.find("title")),
                "snippet": _flatten(doc.find("passages")),
            }
        )
    return {
        "request_id": (root.findtext(".//reqid") or "").strip(),
        "total_found": _found_count(root),
        "results": results,
    }


class YandexSearchTransport:
    """Транспорт `SerpTransport` поверх Yandex Cloud Search API v2."""

    def __init__(
        self,
        transport: JsonTransport,
        *,
        folder_id: str | None = None,
    ) -> None:
        self._transport = transport
        self._folder_id = folder_id

    def post_json(
        self, endpoint: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        self._require_api_key_scheme(headers)
        body = build_yandex_request(json, folder_id=self._folder_id)
        raw = self._transport.post_json(
            endpoint or YANDEX_SEARCH_ENDPOINT, json=body, headers=headers
        )
        return parse_yandex_response(raw)

    @staticmethod
    def _require_api_key_scheme(headers: dict[str, str]) -> None:
        authorization = str(headers.get("Authorization") or "")
        if not authorization.startswith(f"{_REQUIRED_AUTH_SCHEME} "):
            scheme = authorization.split(" ", 1)[0] or "<пусто>"
            raise ValueError(
                "Yandex Search API v2 принимает только Api-Key (или IAM-токен), "
                f"получена схема {scheme!r}. Схема OAuth даёт HTTP 401."
            )


def _flatten(element: Element | None) -> str:
    """Склеивает текст с учётом инлайновой разметки `<hlword>` внутри title/passages."""
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _found_count(root: Element) -> int | None:
    for element in root.iterfind(".//found"):
        if element.get("priority") == "phrase":
            try:
                return int((element.text or "").strip())
            except ValueError:
                return None
    return None


class YandexSerpProviderAdapter:
    """Адаптер Яндекса под общий шов `SerpProviderAdapter` из `competitors.py`.

    Аудит-цикл разбирает `rows` одинаково для всех движков, поэтому форма ответа
    здесь совпадает с DataForSEO: `rank_absolute`, `result_type`, `url`, `title`,
    `description`, `serp_features`, `quality`.

    Стоимость всегда 0: Yandex Search API оплачивается по подписке Yandex Cloud,
    а не поштучно через budget guard плагина.
    """

    def __init__(
        self,
        transport: JsonTransport,
        *,
        token: str,
        folder_id: str | None = None,
        language_code: str = "ru",
    ) -> None:
        self._transport = YandexSearchTransport(transport, folder_id=folder_id)
        self._token = token
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
            "depth": depth,
        }
        try:
            page = self._transport.post_json(
                YANDEX_SEARCH_ENDPOINT,
                json=payload,
                headers={"Authorization": f"{_REQUIRED_AUTH_SCHEME} {self._token}"},
            )
        except Exception as exc:  # транспорт/сеть/разбор — деградируем в статус, не в traceback
            return {
                "rows": [],
                "quality": "unsupported",
                "cost_usd": 0.0,
                "request_ids": [],
                "errors": [
                    {
                        "code": "YANDEX_SERP_REQUEST_FAILED",
                        "message": f"{exc.__class__.__name__}: {exc}",
                        "details": {"keyword": keyword, "region": location_code},
                    }
                ],
            }
        request_id = page.get("request_id") or ""
        return {
            "rows": [
                {
                    "rank_absolute": item["position"],
                    "result_type": "organic",
                    "url": item["url"],
                    "title": item["title"],
                    "description": item["snippet"],
                    "serp_features": ["organic"],
                    "quality": "live",
                }
                for item in page.get("results") or []
            ],
            "quality": "live",
            "cost_usd": 0.0,
            "request_ids": [request_id] if request_id else [],
            "errors": [],
            "total_found": page.get("total_found"),
        }
