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

## Where credentials come from

The plugin resolves secrets via `os.environ[<name from credential_env>]` and is
agnostic about variable names: any non-empty name is valid. So the config must
declare where those variables come from — otherwise "where are this project's
keys" becomes an archaeology exercise.

```toml
[credentials]
env_file = "../.env.seo"                 # loaded by the CLI
loader = "scripts/seo-observer-env.sh"   # named only, never executed
```

Paths resolve relative to the config directory, not the working directory: a
command can run from anywhere while the declaration still points at one place.

**`env_file` is data.** A flat `KEY=VALUE` list (`export`, quoting, and
comments are supported). A value from the file does **not** override an
already-set variable: an explicit export outranks the file, so the file is a
default, not an authority, and one-off overrides keep working.

**`loader` is code, and therefore is not executed.** A config is not a trust
boundary: it travels in PRs, agents edit it, and it is read from directories
the CLI merely visits. A declared loader is only **reported**: `doctor` prints
`source <path>` so a human runs the command. If execution were added,
`project.toml` would become a way to run arbitrary shell from anything that
loads the config. A file that needs logic (extract a token from an OAuth JSON,
hit a keychain) is declared as a `loader`.

## Variable naming

A project prefix (`PROJ_`, `DEMO_`, `ACME_`) protects against exactly one
thing: **different values under one name**. Hence the rule:

- values **differ** across projects (GSC, Metrica counter, Webmaster) →
  prefix required;
- the value is **shared** across all projects (one account:
  `TOPVISOR_USER_ID`, `TOPVISOR_API_KEY`) → a prefix protects nothing and only
  multiplies one value under three names → no prefix.

## Schema

The local schema is documented below.

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

[sources.ga4]
enabled = true
required = false
token_file_env = "GOOGLE_OAUTH_TOKEN_FILE"
timezone = "Europe/Moscow"
limit = 10000
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

[channels]
brand_terms = ["demobrand", "demo brand"]
noise_referrers = ["pay.demo.example"]

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
- `[sources.ga4]` can carry local adapter fields such as `credential_env`,
  `credential_file_env`, `token_file_env`, `limit`, `finalize_after`, and the
  optional `timezone`. When present, `timezone` must be a non-empty string and
  is used as `source_timezone` on GA4 all-channel traffic rows; when omitted
  those rows carry the literal `"property_timezone"` (the GA4 property's own
  reporting timezone). Source bindings must use the `properties/<id>` resource
  name as `remote_id`. Collect fetches both the legacy organic bundle and the
  all-channel bundle (`sessionDefaultChannelGroup` × `sessionSource` ×
  `sessionMedium` × `landingPagePlusQueryString`, attribution model
  `ga4_session_all_channels`); noise-referrer exclusion and engagedSessions are
  Plan 2 export behavior, not collection filters.
- `[channels]` is optional and tunes channel classification shared by sources.
  Both lists default to empty. `brand_terms` lists brand name variants; GSC
  uses them to fetch a brand-only aggregate segment so `non-brand = total -
  brand` matches the Search Console UI (anonymized queries never arrive as
  rows). `noise_referrers` lists referrer substrings that are not real traffic
  (payment processors, auth flows). Matching is a case-insensitive substring
  match implemented as a regex alternative: `pay.demo.example` also catches
  `secure.pay.demo.example`. Noise-referrer exclusion is applied at export
  time in Plan 2, not during collection.
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
- `adapter = "http_aggregate"` reads daily aggregate counters from a tenant
  HTTP endpoint. Required fields are `endpoint_env` (env var name holding the
  endpoint URL), `credential_env` (env var name holding the bearer token),
  `outcome_id`, `counting_unit`, `dedupe_key`, `timestamp_field`, and
  `attribution_model` (payment sources use `"registration_source"` so payments
  are attributed by the source of the registration). The endpoint is called as
  `GET {endpoint}?view_id=...&period_start=...&period_end=...` with an
  `Authorization: Bearer <token>` header; production endpoints must use `https`
  (`http://127.0.0.1` is accepted only for local test stands). The response is
  a JSON object of the form `{"source": "tenant_api", "dataset_coverage":
  "complete", "freshness": "final", "rows": [{"grain_start": "2026-09-24",
  "period_start": "2026-09-24", "period_end": "2026-09-24", "source":
  "google", "medium": "organic", "count": 3, "value_minor": 490000,
  "currency": "RUB"}]}` where `count` is the daily registration or payment
  counter depending on the approved view, and `value_minor`/`currency` are
  present only on payment rows. Rows must never carry person identifiers
  (`user_id`, `email`, `phone`, `login`, `username`, `ip`, `payment_id`,
  `account_id`, `distinct_id`) — rows containing identifier fields are
  rejected outright.

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
