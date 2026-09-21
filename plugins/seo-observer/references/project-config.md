# seo-observer project config

Last verified: 2026-07-30

`seo-observer` reads deterministic local project configuration from:

```text
.seo-observer/project.toml
```

Discovery walks upward from the current working directory. Explicit selectors take precedence:

1. `--config /path/to/project.toml`
2. `--project NAME` from `SEO_OBSERVER_HOME/projects.toml`
3. cwd-upward `.seo-observer/project.toml`

`--config` and `--project` are mutually exclusive and return `SELECTOR_CONFLICT`.

## Откуда берутся креды

Плагин резолвит секреты через `os.environ[<имя из credential_env>]` и к именам
переменных никак не относится: валидно любое непустое имя. Поэтому конфиг обязан
сам объявлять, откуда эти переменные берутся, — иначе «где у проекта ключи»
выясняется раскопками (у трёх живых проектов было три разных механизма и ни одного
указания в конфиге).

```toml
[credentials]
env_file = "../.env.seo"                 # подгружается CLI
loader = "scripts/seo-observer-env.sh"   # только называется, никогда не исполняется
```

Пути резолвятся относительно каталога конфига, а не рабочего каталога: команда
запускается откуда угодно, а объявление обязано указывать на одно и то же место.

**`env_file` — данные.** Плоский список `KEY=VALUE` (поддерживаются `export`,
кавычки, комментарии). Значение из файла **не перекрывает** уже заданную
переменную: явный экспорт старше файла, поэтому файл — это умолчание, а не
авторитет, и разовый оверрайд продолжает работать.

**`loader` — код, и потому не исполняется.** Конфиг не является границей доверия:
он ездит в PR, его правят агенты, и его читают из каталогов, куда CLI просто
зашёл. Объявленный loader только **сообщается**: `doctor` печатает
`source <путь>`, чтобы команду выполнил человек. Если бы исполнение добавили,
`project.toml` стал бы способом запустить произвольный shell из всего, что
загружает конфиг. Файл, которому нужна логика (вытащить токен из OAuth-JSON,
сходить в keychain), объявляется именно как `loader`.

## Именование переменных

Префикс проекта (`PROJ_`, `DEMO_`, `ACME_`) защищает ровно от одного:
**разные значения под одним именем**. Отсюда критерий:

- значение у проектов **различается** (GSC, счётчик Метрики, Webmaster) →
  префикс обязателен;
- значение **общее** для всех проектов (единый аккаунт: `TOPVISOR_USER_ID`,
  `TOPVISOR_API_KEY`) → префикс не защищает ни от чего и лишь размножает одно
  значение под тремя именами → без префикса.

## Schema

The local schema follows
`docs/superpowers/specs/2026-07-27-seo-observer-design.md`.

```toml
config_schema_version = 1
project = "demo"
timezone = "Europe/Moscow"
default_search_engine = "google"
default_location_code = 2840
default_location_name = "United States"
default_language_code = "en"
default_devices = ["desktop", "mobile"]

[[properties]]
id = "main"
url = "https://demo.example/"

[providers.dataforseo]
enabled = true
credential_env = "DATAFORSEO_AUTH"
endpoint = "https://api.dataforseo.com"
per_run_budget_usd = 3.00
monthly_budget_usd = 50.00
cache_ttl_hours = 24

[providers.exa]
enabled = false
credential_env = "EXA_API_KEY"
endpoint = "https://api.exa.ai"
per_run_budget_usd = 1.00
cache_ttl_hours = 24

[[markets]]
id = "ru"
search_engine = "yandex"
provider = "yandex_search"
credential_env = "YANDEX_SEARCH_API_TOKEN"
regions = ["213"]
locale = "ru-RU"
language = "ru"
intent = "primary"
source_roles = ["yandex_webmaster", "yandex_metrica", "wordstat", "google_search_console"]
out_of_scope = ["google_worldwide_serp_competitor_lens"]

[sources.google_search_console]
enabled = true
required = true
credential_file_env = "GSC_SA_JSON_PATH"
finalize_after = "P3D"
refresh_recent_periods = 3

[sources.yandex_metrica]
enabled = true
required = false
counter_id = "123456"
credential_env = "YANDEX_METRICA_TOKEN"
accuracy = "full"
finalize_after = "P3D"
refresh_recent_periods = 3

[sources.yandex_webmaster]
enabled = true
required = false
user_id = "42"
credential_env = "YANDEX_WEBMASTER_TOKEN"
finalize_after = "P3D"

[sources.wordstat]
enabled = true
required = false
credential_env = "YANDEX_WORDSTAT_TOKEN"
api_family = "search_api_wordstat"
api_version = "v1"
supports_devices = true
finalize_after = "P7D"

[sources.serp]
enabled = true
required = true
provider = "dataforseo_google_organic"
regions = ["2840"]
devices = ["desktop", "mobile"]
result_depth = 10
confirmation_observations = 2
observations_per_protocol_slot = 2
rank_aggregation = "median"
minimum_weighted_keyword_coverage = 0.90

[sources.competitor_discovery]
enabled = true
required = false
provider = "dataforseo"
limit_competitors = 20
gap_limit = 100
mega_authority_domains = ["wikipedia.org", "youtube.com", "example-directory.example"]

[sources.competitor_research]
enabled = false
required = false
provider = "exa"
num_results = 10
include_domains = ["learning.example"]
exclude_domains = ["jobs.example"]

[sources.outcome_auth]
enabled = true
required = false
adapter = "fixture_aggregate"
approved_views = ["demo_auth.registration_outcomes_daily_v1"]
parameter_names = ["period_start", "period_end"]

[sources.outcome_pay]
enabled = true
required = false
adapter = "fixture_aggregate"
approved_views = ["demo_pay.paid_purchase_outcomes_daily_v1"]
parameter_names = ["period_start", "period_end"]

[[source_bindings]]
property = "main"
source = "google_search_console"
remote_id = "https://demo.example/"

[[source_bindings]]
property = "main"
source = "yandex_metrica"
remote_id = "123456"

[[source_bindings]]
property = "main"
source = "yandex_webmaster"
remote_id = "https:demo.example:443"

[[source_bindings]]
property = "main"
source = "wordstat"
remote_id = "wordstat-market-demand"

[[source_bindings]]
property = "main"
source = "serp"
remote_id = "demo-serp"

[[keyword_sets]]
id = "core"
path = "keywords/core.txt"
locale = "ru-RU"
regions = ["2840"]
devices = ["desktop", "mobile"]

[competitors]
owned_domains = ["demo.example", "*.demo.example"]

[[competitors.items]]
id = "learning-platform"
name = "Learning Platform"
domain_patterns = ["learning.example", "*.learning.example"]
aliases = ["Learning"]
class = "direct"
```

Required local validation covers:

- `config_schema_version = 1`;
- top-level `project` and IANA `timezone`;
- `[[properties]]` entries with unique `id` and valid HTTP(S) `url`;
- `[sources.*]` entries with known source names and typed local fields;
- `[[source_bindings]]` entries that reference existing properties and sources, using either
  `property = "id"` or `properties = ["id", ...]`;
- `[[keyword_sets]]` entries with unique `id`, local `path`, `locale`, `regions`, and `devices`;
- optional declared `[[outcomes]]` entries with unique `id` and valid source references.
- `[sources.google_search_console]` can carry local adapter fields such as
  `credential_file_env`, `finalize_after`, `refresh_recent_periods`,
  `row_limit`, and `data_state`; source bindings must use the GSC URL-prefix
  property URL with trailing slash as `remote_id`. Doctor checks validate shape,
  credential-file env presence, and URL-prefix binding shape without live calls.
- `[sources.yandex_metrica]` can carry local adapter fields such as `counter_id`,
  `credential_env`, `accuracy`, `finalize_after`, and fixture/config-driven goal
  mappings consumed by `seo_observer.metrica`; doctor checks validate shape and
  token presence without live calls.
- `[sources.yandex_webmaster]` can carry local adapter fields such as `user_id`,
  `credential_env`, `finalize_after`, and `limit`; source bindings must use the
  Yandex remote `host-id` string (for example `https:demo.example:443`) as
  `remote_id`, not the property URL. Doctor checks validate shape, token presence,
  and binding shape without live calls.
- `[sources.wordstat]` can carry local adapter fields such as `credential_env`,
  `api_family`, `api_version`, `locale`, `language`, `endpoint`,
  `supports_devices`, and `finalize_after`. Doctor checks validate source shape
  and token presence without live calls. `api_family` is explicit metadata:
  current Search API Wordstat fixtures use `search_api_wordstat`, while legacy
  Yandex Direct report fixtures may use `direct_wordstat_report`.
- `[sources.serp]` can carry local adapter fields such as `provider`,
  `credential_env`, `api_family = "yandex_search_api"`, `api_version`,
  `regions`, `devices`, `result_depth`, `confirmation_observations`,
  `observations_per_protocol_slot`, `rank_aggregation`, and
  `minimum_weighted_keyword_coverage`. Accepted provider values include
  `yandex_search`, `google_search`, and `dataforseo_google_organic`; the
  DataForSEO SERP provider also requires `[providers.dataforseo]`. Doctor checks validate source shape and
  optional token presence without live calls. The implemented adapter builds
  fixture-testable point SERP protocol slots and rank observations; it does not
  automate a browser, scrape public search pages, bypass captcha, make paid API
  calls, schedule collection, or create production Demo config.
- `[providers.dataforseo]` and `[providers.exa]` are provider account sections.
  They contain only local readiness metadata such as `enabled`, `credential_env`,
  optional `endpoint`, budgets, `cache_ttl_hours`, and `finalize_after`. The
  config file names environment variables; it must not contain credential values.
- `[[markets]]` describes independent search-market contours and report
  interpretation intent. `intent = "primary"` means the report should lead with
  that contour. `intent = "secondary"` means evidence is useful but not the
  default lens. `source_roles` lists sources that matter for interpretation, and
  `out_of_scope` records conclusions the report should explicitly avoid unless
  requested, such as `google_worldwide_serp_competitor_lens` for Russia-first
  Russia-first reports or `default_ru_yandex_competitor_lens` for global-first
  reports.
- `[sources.competitor_discovery]` is the semantic source for deterministic
  competitor discovery. It references `provider = "dataforseo"` and can carry
  `limit_competitors`, `gap_limit`, and `mega_authority_domains`.
- `[sources.competitor_research]` is the semantic source for research-only
  competitor/content discovery. It references `provider = "exa"` and can carry
  `num_results`, `include_domains`, and `exclude_domains`.
- Competitor identity config uses `[competitors]` with `owned_domains` and
  `[[competitors.items]]` entries. Runtime normalization preserves the existing
  SERP shape: owned domains remain `CompetitorConfig.owned_domains`, and each
  competitor entry becomes a SERP `Competitor` with `domain_patterns` and
  `aliases`. The parser still accepts legacy `[[competitors]] domains = [...]`
  as an input alias for one-release compatibility; new configs should use
  `[competitors]` plus `[[competitors.items]]`.
- `[sources.outcome_*]` can carry local aggregate outcome adapter fields such as
  `adapter = "fixture_aggregate"` or `adapter = "postgres_aggregate"`,
  `approved_views = ["stable.view_id"]`, and
  `parameter_names = ["period_start", "period_end"]`. Config validation rejects
  free-form query fields including `sql`, `query`, `table`, `where`,
  `where_clause`, `sql_fragment`, and `report_query`. Doctor checks validate
  shape only and do not perform live DB calls or credential logging.

Invalid config returns `CONFIG_INVALID` with a stable `validation_code`.

## Registry

Default home is `~/.seo-observer`, or `SEO_OBSERVER_HOME` when set. The registry file is:

```text
$SEO_OBSERVER_HOME/projects.toml
```

Local registry commands:

```bash
seo-observer projects register demo --config .seo-observer/project.toml --json
seo-observer projects list --json
seo-observer projects remove demo --json
```

The top-level `project` string is the storage namespace. Registry entries store the config path,
project namespace, and canonical local repository identity. Registering two names for the same
namespace returns `PROJECT_NAMESPACE_COLLISION`.

## Hashing

`config_hash` is a SHA-256 digest over:

- the committed config path when the file is inside a git checkout, otherwise the absolute path;
- the config file content hash;
- every keyword file path relative to the config directory plus its content hash.

Provider API calls, snapshots, reports, scheduling, and production project configs are intentionally
out of scope for the project-config contract. The Yandex Metrica adapter foundation is documented in
`plugins/seo-observer/references/metrica.md`; the Yandex Webmaster adapter foundation is
documented in `plugins/seo-observer/references/webmaster.md`; the Google Search Console
adapter foundation is documented in `plugins/seo-observer/references/gsc.md`; the Wordstat
demand adapter foundation is documented in `plugins/seo-observer/references/wordstat.md`;
the SERP adapter foundation is documented in `plugins/seo-observer/references/serp.md`;
the aggregate outcome and reconciliation foundation is documented in
`plugins/seo-observer/references/outcomes.md`;
local SQLite storage is documented separately in
`plugins/seo-observer/references/storage.md`.
