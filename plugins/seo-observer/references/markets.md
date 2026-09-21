# Поисковые контуры (`[[markets]]`)

Проект может вести несколько независимых поисковых рынков. Контуры **не смешиваются**:
набор ключей принадлежит ровно одному рынку, метрики считаются по каждому отдельно.

```toml
[[markets]]
id = "ru"
search_engine = "yandex"
provider = "yandex_search"
credential_env = "PROJ_YANDEX_SEARCH_API_TOKEN"
regions = ["213", "2"]        # региональные ID Яндекса: 213 Москва, 2 СПб
locale = "ru-RU"
language = "ru"
intent = "primary"
source_roles = ["yandex_webmaster", "yandex_metrica", "wordstat", "google_search_console"]
out_of_scope = ["google_worldwide_serp_competitor_lens"]

[[markets]]
id = "en"
search_engine = "google"
provider = "dataforseo_google_organic"
credential_env = "PROJ_DATAFORSEO_AUTH"
regions = ["2840", "2826"]    # коды локаций DataForSEO
locale = "en-US"
language = "en"
intent = "secondary"
source_roles = ["google_search_console", "dataforseo_google_organic", "competitor_research"]
```

Набор ключей привязывается полем `market`:

```toml
[[keyword_sets]]
id = "ru_core"
market = "ru"
path = "keywords/ru-core.txt"
```

## Почему RU-контур двухдвижковый

**DataForSEO не поддерживает локации России и Беларуси ни в одном сервисе** — это
ограничение провайдера целиком, а не конкретного эндпоинта, и оно не откатится:
локации вырезаны из всех API и баз в 2022 году вместе с Yandex SERP API. Поэтому
RU-позиции через DataForSEO недостижимы независимо от движка.

Отсюда распределение по RU:

- **Яндекс** — `provider = "yandex_search"` (Yandex Search API v2);
- **Google** — `provider = "topvisor_google_organic"`.

Google в России — это ~25–30% запросов (Яндекс отчитывается о своей доле около
70%), и в крупных городах доля Google выше средней. Контур, снятый только
Яндексом, даёт не «неполное покрытие», а смещённую долю видимости: один движок
подаётся как рынок. Поэтому оба движка описываются отдельными рынками с
собственными наборами ключей, а выводы по ним не складываются.

```toml
[[markets]]
id = "ru_google"
search_engine = "google"
provider = "topvisor_google_organic"
credential_env = "TOPVISOR_API_KEY"
regions = ["213", "2"]        # те же ID, что у Яндекса — см. ниже
locale = "ru-RU"
language = "ru"
intent = "primary"
```

**Региональные ID переводить не нужно.** База регионов у Topvisor общая для
движков: поиск по «Москва» возвращает `{"id": 213, "google_id": 1011969}`, и
`region_key` — это тот же яндексовый `lr`. То есть `regions = ["213", "2"]`
работают для обоих движков без изменений.

Частая ошибка при переносе конфига остаётся в силе для DataForSEO: оставить
яндексовые ID (`213`, `2`) при `provider = "dataforseo_google_organic"`. Такая
связка не работает — DataForSEO этих идентификаторов не понимает.

### Глубина стоит линейно

Google убил параметр `num=100`, поэтому одна «страница выдачи» — это ТОП-10, и
Topvisor берёт за глубину пропорционально (замер на живом аккаунте, цена за
ключ со снимками): ТОП-10 — 0,10 ₽, ТОП-30 — 0,30 ₽, ТОП-100 — 1,00 ₽. Бюджет
считается как `depth × keywords × regions × devices`, а не по числу ключей.
Снимки выдачи прибавляют к цене около 11%, и без них доля видимости не
считается вовсе — собирать позиции без снимков смысла нет.

## Конкуренты и пересечение контуров

```toml
[[competitors.items]]
id = "breaking-bet"
name = "BreakingBet"
domain_patterns = ["breaking-bet.com"]
class = "direct"
markets = ["ru"]
```

Поле `markets` **опускается**, когда конкурент работает на всех рынках проекта — это
умолчание, а не «нигде». Конкуренты вполне могут присутствовать одновременно в RU и EN,
и отчёт должен такие пересечения показывать.

Ограничение на сегодня: привязка сохраняется, но при классификации выдачи пока не
применяется (известное ограничение). Практический смысл в том, что пересечения видно
эмпирически: в живом прогоне домен, помеченный `markets = ["en"]`, обнаружился на
позиции 2 RU-выдачи — то есть привязка была уже, чем реальность.

## Intent отчёта

`intent` задаёт не сбор данных, а трактовку отчёта:

- `primary` — основной SEO-контур проекта. Composite report выводит его первым и
  считает его источники главным lens для выводов.
- `secondary` — полезный дополнительный контур. Данные можно цитировать, но они
  не должны становиться default competitor lens.
- `out_of_scope` — контур описан для явного исключения; выводы по нему не делаются
  без отдельного запроса.

`source_roles` перечисляет источники, которые важны для интерпретации рынка.
`out_of_scope` перечисляет выводы, которые отчёт должен проговорить как
намеренно исключённые. Russia-first конфигурация использует
`out_of_scope = ["google_worldwide_serp_competitor_lens"]`: GSC полезен для
собственной видимости, но Google-worldwide SERP competitors не являются default
выводом. Global-first конфигурация использует primary Google worldwide и может
помечать RU/Yandex lens как secondary или out of scope.

## Класс конкурента

`direct` / `indirect` — те, у кого можно отобрать место в выдаче.
`reference` / `marketplace` — обзоры, витрины, первоисточники: они занимают выдачу, но
вытеснить их продуктовой страницей нельзя, туда нужно попадать.

Разделение существенно для трактовки доли видимости — см. issue #1676.

## Авторизация Yandex Search API

Токен — **Api-Key** Yandex Cloud (`AQVN…`), заголовок `Authorization: Api-Key <ключ>`.
Схема `OAuth` даёт HTTP 401. `region` передаётся верхним уровнем тела запроса; внутри
`query` он игнорируется.
