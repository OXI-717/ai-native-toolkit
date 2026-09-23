# Search-market contours (`[[markets]]`)

A project can track several independent search markets. Contours **do not
mix**: a keyword set belongs to exactly one market, and metrics are computed
per market.

```toml
[[markets]]
id = "ru"
search_engine = "yandex"
provider = "yandex_search"
credential_env = "PROJ_YANDEX_SEARCH_API_TOKEN"
regions = ["213", "2"]        # Yandex regional IDs: 213 Moscow, 2 Saint Petersburg
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
regions = ["2840", "2826"]    # DataForSEO location codes
locale = "en-US"
language = "en"
intent = "secondary"
source_roles = ["google_search_console", "dataforseo_google_organic", "competitor_research"]
```

A keyword set binds to a market via the `market` field:

```toml
[[keyword_sets]]
id = "ru_core"
market = "ru"
path = "keywords/ru-core.txt"
```

## Why the RU contour is two-engine

**DataForSEO does not support Russian or Belarusian locations in any service** —
this is a provider-wide limitation, not an endpoint quirk, and it will not be
reverted: the locations were removed from all APIs and databases in 2022
together with the Yandex SERP API. So RU positions are unreachable through
DataForSEO regardless of engine.

Hence the RU split:

- **Yandex** — `provider = "yandex_search"` (Yandex Search API v2);
- **Google** — `provider = "topvisor_google_organic"`.

Google holds ~25–30% of queries in Russia (Yandex reports its own share near
70%), and in large cities Google's share is above average. A contour measured
only via Yandex produces not "incomplete coverage" but a biased share of
visibility: one engine is presented as the market. That is why both engines
are described as separate markets with their own keyword sets, and
conclusions from them are not merged.

```toml
[[markets]]
id = "ru_google"
search_engine = "google"
provider = "topvisor_google_organic"
credential_env = "TOPVISOR_API_KEY"
regions = ["213", "2"]        # same IDs as Yandex — see below
locale = "ru-RU"
language = "ru"
intent = "primary"
```

**Regional IDs do not need translation.** Topvisor's region database is shared
across engines: searching for "Moscow" returns `{"id": 213, "google_id":
1011969}`, and `region_key` is the same Yandex `lr`. In other words,
`regions = ["213", "2"]` works for both engines unchanged.

A common config-migration mistake still applies to DataForSEO: keeping Yandex
IDs (`213`, `2`) with `provider = "dataforseo_google_organic"`. That pairing
does not work — DataForSEO does not understand these identifiers.

### Depth costs linearly

Google removed the `num=100` parameter, so one "SERP page" is TOP-10, and
Topvisor charges proportionally to depth (measured on a live account, price
per keyword with snapshots): TOP-10 — 0.10 ₽, TOP-30 — 0.30 ₽, TOP-100 —
1.00 ₽. Budget is computed as `depth × keywords × regions × devices`, not by
keyword count. SERP snapshots add ~11% to the price, and without them share
of visibility is not computed at all — collecting positions without snapshots
is pointless.

## Competitors and contour overlap

```toml
[[competitors.items]]
id = "demo-rival"
name = "DemoRival"
domain_patterns = ["demo-rival.com"]
class = "direct"
markets = ["ru"]
```

The `markets` field is **omitted** when the competitor operates on all of the
project's markets — that is the default, not "nowhere". Competitors can well
be present in both RU and EN at once, and reports should show such overlaps.

Current limitation: the binding is stored but not yet applied during SERP
classification (a known limitation). The practical value is that overlaps are
visible empirically: in a live run, a domain tagged `markets = ["en"]` was
found at position 2 of the RU SERP — the binding was already stricter than
reality.

## Report intent

`intent` controls not data collection but report interpretation:

- `primary` — the project's main SEO contour. The composite report lists it
  first and treats its sources as the primary lens for conclusions.
- `secondary` — a useful supplementary contour. Its data may be cited but must
  not become the default competitor lens.
- `out_of_scope` — the contour is described for explicit exclusion; no
  conclusions are drawn from it without a separate request.

`source_roles` lists the sources that matter for interpreting the market.
`out_of_scope` lists the conclusions the report must state as deliberately
excluded. A Russia-first configuration uses `out_of_scope =
["google_worldwide_serp_competitor_lens"]`: GSC is useful for own visibility,
but Google-worldwide SERP competitors are not the default conclusion. A
global-first configuration uses primary Google worldwide and may mark the
RU/Yandex lens as secondary or out of scope.

## Competitor class

`direct` / `indirect` — players whose SERP positions can be taken.
`reference` / `marketplace` — reviews, showcases, primary sources: they occupy
the SERP but cannot be displaced by a product page; the goal is to be listed
there.

The distinction matters for interpreting share of visibility.

## Yandex Search API authorization

The token is a Yandex Cloud **Api-Key** (`AQVN…`), sent as `Authorization:
Api-Key <key>`. The `OAuth` scheme returns HTTP 401. `region` is passed at the
top level of the request body; inside `query` it is ignored.
