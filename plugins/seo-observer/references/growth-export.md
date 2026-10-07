# Growth export (`seo-observer export`)

Publishes `growth_schema_version = 3` bundles (private panel data — business aggregates only,
no person identifiers) as versioned, immutable build directories with atomic
symlink switching. Read-only over the project SQLite database; no live
provider calls.

```bash
seo-observer export --project demo --kind current --out /srv/growth/demo --json
seo-observer export --project demo --kind current --date 2026-09-20 --out /srv/growth/demo
seo-observer export --project demo --kind weekly --out /srv/growth/demo --panel-url https://panel.example/demo
seo-observer export --project demo --kind weekly --week-start 2026-09-14 --out /srv/growth/demo --no-pdf
```

## Kinds and windows

- `current`: 7-day window ending yesterday (`--date` overrides the end), plus
  a 28-day window with the same end; each window gets a previous window of the
  same length. One `growth.json`: the top level is the 7-day window; the 28-day
  KPI column lives in `derived.kpis_by_window["28d"]`.
- `weekly`: Monday–Sunday week. Default is the latest week whose Sunday is at
  least 3 days in the past (source finalization delay). `--week-start` selects
  a specific week and must be a Monday. Trend history is computed in memory
  for the 12 preceding weeks (never written to disk separately).

## Layout

```
DIR/
  builds/current-<YYYY-MM-DD>-<hash12>/
  builds/weekly-<start>_<end>-<hash12>/
  current        -> builds/current-…
  weekly/<start>_<end> -> ../builds/weekly-…
  latest-weekly  -> builds/weekly-…
  failed/<kind>-<window>-<utc>.receipt.json
```

Builds are written to `builds/.tmp-<uuid>/` and moved into place with
`os.rename` once every file is complete; symlinks swap via
`os.symlink` + `os.replace`. A failure anywhere removes the temp directory and
temp links — previously published builds and links are untouched. Re-running
an export whose data version is already built writes nothing and reports
`unchanged: true` (links are verified). For `weekly`, `unchanged` is `false`
when `latest-weekly` had to be (re)created, even if the build already existed
and no new files were written — links are re-pointed in that case.

`--date` applies only to `--kind current` and `--week-start` only to
`--kind weekly`; combining a flag with the wrong kind is a usage error
(`EXPORT_FLAG_KIND_MISMATCH`, exit code 2).

`weekly` files: `growth.json`, `index.html`, `report.pdf`, `brief.md`,
`receipt.json`. Russian `current` exports contain `growth.json`, `receipt.json`
and six pages at the build root and under `28/`: `index.html`,
`positions.html`, `demand.html`, `traffic.html`, `money.html`, `status.html`.
Navigation stays in the selected window; 7/28 links switch the same page.
English `current` exports keep the original single-page format.

The Russian dashboard reuses existing aggregates and KPI eligibility checks.
Its dedicated renderer embeds packaged CSS, JavaScript and Manrope WOFF2. Its `dashboard` block contains `previous`, 12 `history`
windows and `28d.{current,previous}` raw windows (including derived KPIs).
In schema v3, `dashboard.keyword_clusters` maps search sources to maps of
normalized queries and cluster labels, for example
`{"yandex_webmaster": {"shared query": "Local intent"}, "google_search_console": {"shared query": "Global intent"}}`.
The CLI retains each `KeywordSet.market` and resolves it through the configured
market's `search_engine`. Unscoped sets live under `"*"` as a fallback; a scoped
label takes precedence. An unresolved market never becomes a global fallback.
If two markets on one engine disagree on a query, its value is `null` and the
row displays the unclustered label: source-level rows cannot identify its market.
Cluster labels are cleaned before comparison and storage, so different numeric
keyword-count/volume annotations on the same label do not create a conflict.
This replaces the flat query-to-cluster map in schema v2; schema v2 landing
page fields remain unchanged.
Trend points include `revenue_minor` and `revenue_currency`; different currencies
are never connected in a revenue sparkline. All detail/history inputs participate in the immutable data hash;
`dashboard_version` participates in the render identity. No new database,
collector, API or SPA is introduced. Existing runtime static-file routing
serves these paths without changes.

Positions are impression-weighted averages of observed GSC/Webmaster queries,
not fixed SERP rankings. Bucket counts partition the available queries;
missing averages are separate from >100. Position tables initially show 25 rows sorted by impressions, with eight history
weeks. Buckets include current and previously observed queries; absent current
queries belong to «не показывался». Cluster summaries weight positions by impressions. Changes are
withheld unless both full windows have covered dates and fresh detail rows.
Absent rows are never treated as rank losses or zeros. Weekly history is an
observed slice, not a completeness-certified rank history.

Demand uses only stored impressions/clicks and per-query/page rows. Page→query
links appear only when both dimensions actually exist. Opportunities use an
explicit heuristic (≥20 impressions, average position 4–20, CTR <5%). Traffic
shows separate channel, source and landing slices: no unrecorded join between
source and landing is inferred. Paths are normalized before aggregation, hashing and JSON/HTML export: hex
segments of ≥16 characters, UUIDs, opaque alphanumeric segments of ≥20 characters
and numbers of ≥6 digits become `:id`. JWT-like three-part segments and dotted
tokens containing identifying components are also masked as a whole. Long
word-based slugs, `file.html` and `v1.2` remain intact. Detection includes percent-encoded segments;
safe reserved encoding is preserved. Matching templates become one row in both JSON and HTML. Schema v2 landing
rows contain `page`, total `visits`, and a `channels` map preserving the original
channel subtotals; the former scalar `channel` field is replaced. Page-query
details are indexed in one pass and show at most 100 queries with the most
impressions per displayed page, with an explicit truncation count. Traffic
comparisons retain channels that disappeared; absent rows become zero only
when the corresponding traffic period is fully covered.

Money uses server facts and shows Mixpanel signups separately as a sample.
Relative KPI deltas use percent. When relative change is unavailable, absolute
revenue deltas convert minor units to the stated currency; absolute conversion
deltas convert fractions to percentage points. Counts keep their native units;
missing comparisons remain unavailable.
First/repeat purchase attribution, Yandex diagnostics, index
coverage and links are explicitly unavailable in this export. Multiple
currencies are not summed; revenue changes and historical averages exclude
incompatible currencies. Collection timestamps and factual/reporting dates
are separate; provisional search zeros do not overwrite actual search freshness.
Every source that participates in the growth KPI categories and Mixpanel
appears in status and freshness, including Metrica and custom outcome sources;
competitor provider configuration remains outside this view. Aggregate
non-brand search KPIs remain on Demand and visit-to-signup on Money.

`growth_hash` is the sha256 of the canonical growth JSON with build-time
fields (`generated_at`/`produced_at`) removed; `sources.*.collected_at` is
part of the hash, so a re-collected day inside the same week produces a new
build with a new hash12 and the week link switches to it.

`receipt.json` fields: `schema_version`, `tenant`, `kind`, `window`,
`produced_at`, `growth_hash`, `files`, `pdf` (`{ok, error}`), and `sources`
(per-source `state`/`required`/`collected_at`/`timezone`).

## `growth.json` data-availability fields

Each enabled search source has a `search.<source>` block, emitted even when
the source produced no facts in the window:

- `search.<source>.data_through` — `YYYY-MM-DD` or `null`. The last window
  day with a non-zero `total` fact; trailing zero-row days are provisional
  and excluded. Search KPIs sum only the leading days through this date and
  clip comparison windows to the same count of leading days.
- `search.<source>.totals_available` — boolean. True when at least one
  `total` fact exists in the window (explicit zeros count); false means
  absent data, not a measured zero, and search KPIs report coverage `none`.
- `search.<source>.search_gap` — boolean. True when `data_through` sits more
  than 4 days before the window end — a real collection gap, not the normal
  search reporting lag. A gapped source makes its KPIs `partial`, and a
  previous or history window whose own derivation would set this flag is not
  an eligible comparison (null delta / skipped avg4 week).
- `search.<source>.daily` / `queries` / `pages` — lists, empty when there
  are no facts.
- `search.<source>.details_through` — last date with a real detail row.
- `search.<source>.query_pages` — redacted query/page pairs only when both
  dimensions were observed; empty when the source only emits separate lists.
- `sources.<source>.data_through` — latest stored fact date at/before the
  requested end; dashboard reporting freshness also considers successfully
  covered zero-event days for non-search sources.
- Revenue KPIs include `previous_currency` when the previous window has one
  currency, so a previous USD value cannot be labelled with the current RUB.

## `growth.json` KPI delta fields (`derived.kpis.<name>` and
`derived.kpis_by_window.<window>.<name>`)

- `delta_pct`, `delta_vs_avg4_pct` — number or `null`. Percent change versus
  the previous window / the trailing 4-week average. Null when the
  comparison is not eligible (current KPI not `complete`, comparison window
  not fully covered or itself gap-flagged, missing value, zero or tiny
  base).
- `delta_abs`, `delta_vs_avg4_abs` — number or `null`. Absolute change in
  the KPI's own unit (impressions, clicks, visits, registrations, payments),
  filled **instead of** the percentage only when the comparison is eligible
  and the base is zero or, for count KPIs, below the tiny-base threshold of
  5. A null absolute field therefore does **not** prove the comparison was
  withheld — an eligible comparison on a normal base renders `*_pct` and
  leaves `*_abs` null.
- `avg4`, `avg_weeks` — `avg4` is the mean KPI value over up to the 4 most
  recent eligible history weeks (`avg_weeks` reports how many were used);
  `null`/`0` when none are eligible. Weeks that are empty or themselves
  gap-flagged do not count.

## PDF policy

`weekly` renders `report.pdf` from `index.html` via Playwright Chromium
(`report_rendering.render_pdf_from_html`). If PDF rendering fails the week is
**not** published — neither `weekly/<window>` nor `latest-weekly` switches —
and the receipt is written under `failed/`; exit code is non-zero. Pass
`--no-pdf` on hosts without Chromium: the week is published and the receipt
records `pdf: {ok: false, error: "disabled"}`.

## Retention

- `builds/current-*`: the linked build plus the two newest unlinked builds are
  kept; older unlinked builds are removed.
- `builds/weekly-*`: unlinked builds older than 30 days are removed; builds
  referenced by `weekly/*` or `latest-weekly` are kept indefinitely.
- `failed/*.receipt.json`: removed after 30 days.


## Dashboard interaction and dates

All tables initially show 15 rows (positions: 25) with an explicit total and
«Показать все (N)». Headers sort text/numeric data, with missing values last.
Search, engine, cluster and position filters compose before the row limit.
Without JavaScript the first rows remain visible. Wide matrices scroll
horizontally and keep the query column sticky. Source/medium labels are human
readable; raw dimensions appear only in titles. Missing cells show an em dash
with an explanation; absent observations are never invented zeros.

Dates use Russian month abbreviations; build timestamps use Europe/Moscow.
ISO dates remain in JSON and datetime/data attributes. Source chips point to
«Состояние», where source purpose, status and alert participation are explained.
Only incomplete coverage is called out. Mixpanel remains a separate sample
card with its server registration denominator.

### Monetary units

All monetary presentation (current KPI values and comparisons, sparkline maxima,
daily money tables, weekly/English HTML/PDF and Markdown briefs) uses the same
explicit minor-unit exponents. Raw `value_minor` and `revenue_minor` stay unchanged
in JSON and calculations. Formatting uses Decimal, preserving native precision
without routing integer minor values through binary floating point.

| Currency | Exponent | Minor units per unit |
| --- | ---: | ---: |
| RUB / USD / EUR | 2 | 100 |
| XTR / JPY | 0 | 1 |
| TON | 9 | 1,000,000,000 |
| KWD | 3 | 1,000 |

Unknown or absent currency codes produce an explicit localized unsupported-currency
message instead of assuming cents. A money chart with an unknown series currency
is withheld. Current cards omit decimals for integral amounts; legacy reports
retain fixed native precision. This is unit scaling, not currency conversion.

Russian reports use a decimal comma for percentages, CTR, percentage-point
changes and money, including weekly HTML/PDF and brief. Money display units are
₽ (RUB), $ (USD), € (EUR), ⭐ (XTR), and TON; English reports retain currency
codes and decimal points. Daily server outcomes omit the channel column only
when every row has `unassigned`; totals and known channels remain visible.
Cluster annotations are stripped only when all comma-separated fields are
recognized phrase counts or YWS/volume/frequency metadata. Product descriptions
such as `iPhone (15 Pro, 256)` remain intact.

## Регулярная выдача TopVisor и «Конкуренты»

Для существующего рынка TopVisor добавьте `weekly_budget_rub` **в его секцию
`[[markets]]`**, например сразу после `project_id`. Отсутствие поля и `0`
выключают платный сбор; отрицательные и нечисловые значения отклоняются.

```toml
[[markets]]
id = "ru_google"
competitor_markets = ["ru"] # метки markets у конкурентов
search_engine = "google"
provider = "topvisor_google_organic"
project_id = "<id проекта TopVisor>"
weekly_budget_rub = 0 # после разрешения владельца: 200.0
credential_env = "TOPVISOR_API_KEY"
user_id_env = "TOPVISOR_USER_ID"
regions = ["213"]
devices = ["desktop", "mobile"]
locale = "ru-RU"
language = "ru"
intent = "primary"
```

Установка положительного бюджета разрешает будущие автоматические платные
проверки. Цена зависит от числа ключей, регионов, устройств и глубины; узнайте её
бесплатно через `serp estimate`. Это цена одной проверки, не фиксированный тариф: перед
каждым запуском запрашивается новая оценка; применяется только вариант со
снимками. Бюджет ограничивает весь проект TopVisor (все включённые в нём
регионы и ключи), а панель использует ключи и контуры из конфига тенанта.

```sh
# Бесплатная текущая оценка; не запускает проверку
seo-observer serp estimate --config /config/project.toml --json
# Платный запуск разрешён только положительным бюджетом
seo-observer serp collect --weekly --config /config/project.toml --state-file /data/state/serp.json --json
# Только бесплатное чтение ожидающих проверок
seo-observer serp collect --config /config/project.toml --state-file /data/state/serp.json --json
# Бесплатное чтение существующей даты как исторической точки
seo-observer serp collect --date 2026-08-19 --config /config/project.toml --json
```

Runtime-crontab запускает платный шаг **понедельник 06:00 Europe/Moscow**,
бесплатный сбор — ежедневно в 05:15 МСК; экспорт выполняется штатным расписанием.
Ключ `(tenant, TopVisor project_id, ISO-неделя МСК)` резервируется в SQLite
**до** платного запроса и атомарно защищает от параллельных процессов.
Повторная попытка той же недели возвращает `already_attempted`. После таймаута,
ошибки HTTP 200 или отсутствия подтверждения запись остаётся `uncertain`:
платный запрос не повторяется, бесплатное чтение продолжает проверять готовность.
Бесплатные ретраи ограничены 7 днями с момента попытки: затем статус `expired`,
событие `collection_expired` в `serp_audit`; резерв оплаты остаётся.
Сброс `uncertain` допустим после проверки в TopVisor, что списания не было:

```sh
seo-observer serp reset-uncertain --market ru_google --week 2026-W41 \
  --confirm-not-charged --reason "Проверено в TopVisor: списания не было" \
  --config /config/project.toml --json
```

Команда не запускает проверку. Она атомарно снимает резерв только `uncertain`,
сохраняет предыдущую запись и причину в `serp_audit`; следующий weekly может платить.
Флаг подтверждает именно отсутствие списания, а не разрешение повторной оплаты.
Не удаляйте строки журнала вручную.
Возврат к старой резервной копии базы также возвращает журнал оплат во времени;
перед восстановлением следует выключить бюджет и сверить текущую неделю с TopVisor.
`disabled`, `over_budget`, `pending`, `submitted`, `collected` пишутся JSON-строкой
в cron-журнал. Некорректная цена, включая отсутствующую/NaN, закрывает платный шаг.

Готовность требует завершённого статуса проекта, существующей даты снимков и
позиций для всех ключей/регионов/устройств. Незавершённый набор остаётся `pending`.
Позиции и ТОП-10 сохраняются с источником, датой, устройством и регионом.
Отсутствующий ключ удерживает весь замер в `pending`. Ключ с полученным, но
пустым снимком входит в знаменатель видимости и доли ТОП-10 с нулевым вкладом.
Позиция `--` хранится как `outside_top10`, в панели — «вне ТОП-10», никогда как 11.
Для строки только с доменом URL нормализуется до `https://<domain>/` и помечается
`url_known=false` (в `serp_results` — 0). Панель показывает ключи и «URL неизвестен»;
таблица лучших URL содержит только известные URL.

`/health` читает отдельный `/data/state/serp.json`. SERP — необязательный источник:
после 10 дней без свежего регулярного замера добавляется причина `serp_stale`,
при здоровом core — HTTP 200 и `status="degraded"`. Отсчёт идёт не раньше первого cron-запуска,
увидевшего положительный бюджет. При бюджете 0 тревоги нет. Исторические точки
не освежают регулярный источник. Монтируйте весь `/data` постоянно: там база,
журнал запусков, raw-артефакты и health-state.

### История и сопоставимость

```sh
seo-observer serp import --config /config/project.toml \
  --input "/reports/<дата>/<tenant>/<набор>" --json
# --input принимает файл serp-extract.json или каталог, рекурсивно; можно повторять
seo-observer serp import --config /config/project.toml \
  --input "/reports/<дата>/<tenant>/<аудит>/serp-extract.json" --json
```

Импорт поддерживает `seo-observer.serp_extract.v1`, сохраняет реальный
`provider`/`search_engine` из строк, удаляет повторные наблюдения и пересечения
наборов ключей. При наличии manifest проверяет проект. Метка
`comparability=insufficient_history`, `historical=true`: эти точки показываются
отдельно и не дают процентов изменения к регулярному замеру. Импорт по старым
артефактам знает только ключи с сохранёнными строками, поэтому не восстанавливает
полноту исходного набора. При доступе к TopVisor лучше дополнить историю через
бесплатный `collect --date`.

Импорт берёт `search_engine` из строк артефакта, а не из имени каталога: файл с
Google-строками не станет Яндексом, даже если лежит в каталоге «yandex».

Видимость домена — средний вес его лучшей позиции по ключу: `(11-rank)/10` для
ТОП-10, ноль вне него. SOV — доля этой видимости среди **всех доменов из конфига**,
включая обзорники (это roster SOV, не прежний contestable SOV из audit).
Субдомены и альтернативные домены группируются по `domain_patterns`; собственные
домены объединены в строку «Наш сайт». Один ключ считается один раз.
Метрики сохраняются в `competitor_metrics` по замеру/поисковику/устройству/кластеру.

«Позиции» отделяют реальную выдачу от средних позиций кабинетов. «Конкуренты»
появляются в меню только при наличии хотя бы одного замера: таблица доменов,
изменение SOV в процентных пунктах, тренд своего сайта и топ-5, теплокарта
доли ключей в ТОП-10, раскрытие лучших URL/ключей. Фильтры: поисковик, устройство,
регион. Разные поисковики и устройства никогда не объединяются. Изменения и
линии тренда разрешены только при одинаковых источнике, рынке, составе ключей,
формуле метрик, кластерах и списке доменов; исторические точки не соединяются.

**Состав ключей проекта.** Если ключи загружались в TopVisor вместе с комментариями
(`запрос # частотность …`), проверка мерит не те запросы. Импорт сохраняет исходный
запрос, кластер сопоставляется по части до комментария. До положительного бюджета
согласуйте состав ключей в самом TopVisor. Платный preflight читает их бесплатно и
сравнивает нормализованный состав с конфигом: недостающие, лишние или дублированные
запросы дают `population_mismatch`; `run_check` не вызывается.

### Синхронизация ключей TopVisor

Бюджет держите равным 0 до завершения синхронизации. Конфиг очищается тем же
парсером наборов ключей: комментарий начинается с пробела и `#`; `c#` остаётся
частью запроса. Регистр/пробелы нормализуются,
дубли исключаются. Dry-run показывает счётчики и максимум 10 примеров суммарно:

```sh
seo-observer serp sync-keywords --market ru_google --config /config/project.toml --json
# Только оператор после просмотра dry-run: --apply является явным подтверждением
seo-observer serp sync-keywords --market ru_google --apply --config /config/project.toml --json
```

Бесплатные методы API: `get/keywords_2/keywords`, `del/keywords_2/keywords`,
`edit/keywords_2/keywords/rename`, `add/keywords_2/keywords/import`. Сначала удаляются
дубли/лишние строки, затем сохраняемые ID переименовываются, недостающие добавляются.
Финальное чтение сверяет весь состав. Dry-run, план применения, результат/ошибка
сохраняются в `serp_audit`. При частичной ошибке повторите dry-run: он построит план
по фактическому состоянию. При текущей проверке позиций изменение запрещено.

Перед `--apply` просмотрите примеры dry-run: подозрительные символы в добавляемых
запросах обычно означают опечатку в файле набора ключей.

### Контракт health и отсечка экспорта

Потребители `/health` должны принимать `status: degraded` при HTTP 200.

Все замеры SERP в current/weekly ограничены концом окна экспорта. При перегенерации
старой недели новые измерения не меняют её данные или хеш.
Локальный `weekly_budget_rub` оставлен равным 0. Исправления внешнего проекта и
платные вызовы в рамках этой проверки не выполнялись.


### Рынки конкурентов и сохранённые замеры

`competitor_markets = ["ru"]` в `[[markets]]` явно выбирает конкурентов с меткой
`ru` для рынка `ru_google`. Без поля используются язык (`language`, либо язык из
`locale`), locale и ID рынков с тем же языком. Конкуренты без ограничения `markets`
входят во все рынки; собственный домен входит всегда. Для конфига с 16 RU и 9 EN
конкурентами ростер `ru_google` содержит ровно 17 доменов: RU и собственный.
SOV пересчитывается только внутри этого ростера.

ID рынка входит в идентичность регулярного и исторического замера и в контур
панели вместе с поисковиком, устройством и регионом. На странице есть фильтр
«Рынок». При импорте старых артефактов рынок определяется по набору ключей,
поисковику, региону и провайдеру; неоднозначный импорт отклоняется.

Для уже сохранённых замеров после изменения ростера выполните локальный пересчёт:

```sh
seo-observer serp rebuild --config /config/project.toml --json
```

Команда использует сохранённые снимки, не обращается к API и не меняет дату,
запросы или историческую сопоставимость. Она разрешает рынки до записи,
пересчитывает метрики, заменяет старые замеры и пишет `measurements_rebuilt` в
журнал. Исходные артефакты сохраняются. Повторный запуск не дублирует замеры.
При неоднозначном рынке нужно сначала уточнить конфигурацию наборов ключей.

Если один TopVisor `project_id` назначен нескольким рынкам, `sync-keywords`
отказывает и в dry-run, и с `--apply`: `status: shared_project`, список рынков,
код завершения 1, запись `keyword_sync_refused`. Это предотвращает удаление
ключей соседнего рынка. Используйте отдельные проекты TopVisor для синхронизации.

Повреждённый SERP-state даёт `serp_state_unreadable` и `status: degraded` при
HTTP 200. Ошибки основных источников по-прежнему дают `unhealthy` и HTTP 503.


Значения `competitor_markets` проверяются по ID рынков и меткам `markets`
конкурентов: неизвестная метка отклоняется как ошибка конфигурации.
При переключении рынка панель выбирает его первую доступную комбинацию
поисковика, устройства и региона. Для вручную выбранной отсутствующей комбинации
обе страницы показывают «Нет замеров для этой комбинации».
Дельта SOV берётся относительно последней сопоставимой регулярной точки,
пропуская промежуточные исторические и несопоставимые замеры.

Файл `serp.json` должен содержать массив `markets`, у каждой записи — булевый
`enabled`, у включённого рынка — корректный `latest` или `enabled_at`.
Пустой объект, отсутствие массива или timestamp включённого рынка дают
`serp_state_unreadable`. Отсутствующий необязательный файл и `markets: []`
остаются допустимыми до подключения SERP.
