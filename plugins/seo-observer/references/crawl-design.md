# Local crawl layer — design (issue #2148)

Status: design only, no code in this document's scope. Follow-up issues are
listed at the end with their own acceptance criteria.

## Why this exists

Today the only technical-SEO signal we have is provider-reported: Yandex
Webmaster diagnostics/sitemaps/broken-link samples via `provider-audit`, and
GSC's sitemap-backed `index_coverage`. Two gaps follow directly from that:

- **Blind for properties without a verified provider.** Non-RU markets or any
  domain not verified in Yandex Webmaster get zero technical-SEO signal.
- **Provider-shaped, not page-shaped.** `index_coverage` stores provider
  diagnostics (`problem_code`, `searchable_pages`, sitemap-derived counts),
  not per-page facts (canonical, meta robots, redirect chain, internal link
  graph). A page can look fine in Webmaster and still have a wrong canonical
  or an orphaned URL that no provider surfaces until it disappears from a
  sitemap much later.

A local crawl is provider-independent and observes the page directly.

## 1. Crawl boundaries

- **Scope**: only the domains/paths declared in the project's `[[properties]]`
  (`references/project-config.md`). Crawling third-party sites (competitors)
  is explicitly out of scope for this layer — that already exists as
  `competitors discover`/`competitors audit` with its own paid-provider gate.
- **Limits, named constants, not magic numbers**: `MAX_CRAWL_PAGES`,
  `MAX_CRAWL_DEPTH`, `MAX_CRAWL_SECONDS`, mirroring the
  `opportunities.py`/`ai_readiness.py` convention of documented constants in
  `references/*.md`.
- **On limit hit**: the crawl stops cleanly and the payload reports
  `status: "partial"` with `truncated_reason` (`max_pages` / `max_depth` /
  `max_seconds`) and the count actually crawled — never a silent partial
  result that looks complete (same honesty-layer requirement as every other
  command in this plugin).

## 2. robots.txt and rate limiting

- Fetch and parse `robots.txt` once per crawl run; respect `Disallow` for the
  configured user-agent and the wildcard group. A page excluded by robots.txt
  is recorded as `robots_excluded`, not silently skipped (it is itself a
  finding — "this page can't be crawled by us or by search engines").
- Default User-Agent identifies the tool explicitly (e.g.
  `oxi-seo-observer-crawler/1.0 (+local diagnostic)`), never spoofs a real
  search engine UA — this is a diagnostic tool, not a bypass tool.
  A configured per-project `Crawl-Delay` override is honored if present.
- Conservative default rate limit (named constant,
  e.g. `DEFAULT_CRAWL_DELAY_SECONDS`), and a hard concurrency cap
  (`MAX_CRAWL_CONCURRENCY`) — this is the same "subprocess/network call always
  has a timeout" defensive rule applied to a
  fetch loop instead of a subprocess.

## 3. What is collected per page

One fact per crawled URL per crawl run:

- HTTP status and the full redirect chain (each hop's status + target).
- `canonical` (from `<link rel="canonical">` and, if present, the
  `Link:` header — flag disagreement between the two as a finding).
- `meta robots` content and `X-Robots-Tag` header (flag disagreement the same
  way).
- `title`, `meta description`, `h1` (first occurrence + count, to catch
  multiple-H1 pages).
- `hreflang` alternates declared on the page.
- Internal links discovered on the page (used to build the crawl graph — see
  orphan detection below), response time, and byte size.

**Orphan pages**: not a per-page field but a derived comparison — a URL known
from GSC `search_performance`/sitemap (already in storage) that the crawl
graph never reaches from any crawled page is an orphan finding. This reuses
data already in storage instead of a second discovery mechanism.

## 4. Storage and incrementality

Follow the existing evidence contract in `references/storage.md` — do not
invent a parallel one. A new fact table, e.g. `crawl_pages`, with the same
lineage shape every other fact table already uses:

- `project_id`, `property_id`, `source = "local_crawl"`, `effective_at`,
  `request_id`, `artifact_id`, `logical_observation_key`,
  `collection_attempt_key`, `dataset_coverage`, `freshness`, `comparability`,
  `fact_schema_version`, `normalizer_version`, `is_current`.
- `logical_observation_key` = crawled URL (stable identity across runs).
  `collection_attempt_key` = this crawl run's attempt for that URL. Re-crawling
  the same URL inserts a new fact revision and marks the previous one
  `is_current = 0` via `supersedes_fact_id`/`superseded_by_fact_id` — identical
  to how `search_performance` already handles re-ingestion
  (`references/storage.md` § Evidence identity). This is what makes "page
  changed" distinguishable from "page not re-crawled this run": a page with no
  new fact revision since crawl run N has stale evidence, not a confirmed
  no-change.
- `index_coverage` is **not** reused for per-page crawl facts — it is
  provider-aggregate shaped (counts, problem codes), not per-URL shaped.
  `crawl_pages` is a new observed-fact table alongside it, not a replacement.

Do not reuse `index_coverage` is deliberate: mixing provider-reported
aggregate diagnostics with locally-observed per-page facts in one table would
make `source` ambiguous for every downstream consumer.

## 5. Feeding the ranked opportunity list

`seo-observer opportunities` (`references/opportunities.md`) already defines
the finding shape every source must emit: `type`, `score`, `reason`,
`evidence_quality`, `sample_size`, optional `rank_excluded`. Crawl findings
join the same ranked list under new `type` values (e.g. `crawl_canonical_conflict`,
`crawl_broken_internal_link`, `crawl_orphan_page`, `crawl_meta_robots_conflict`)
instead of a sixth independent report — this is the same anti-catalog
requirement that shaped the other four report types: breadth without a
priority layer becomes a command catalog, not a decision system.
`--type-breakdown` continues to expose all six groups for debugging.

## Follow-up issues (executable, split from this design)

1. **Crawl fetch/robots/rate-limit engine** — HTTP client with redirect chain
   capture, robots.txt parsing, concurrency + rate limit, named limit
   constants, `--dry-run`-style page-count estimate. No storage integration
   yet; writes an in-memory/artifact result the next issue consumes.
   Acceptance: unit tests for redirect chains, robots exclusion, rate limit
   honored, and the three named limits each triggering `partial` with correct
   `truncated_reason`.
2. **`crawl_pages` storage + `seo-observer crawl` command** — migration adding
   the table per §4, ingestion following the existing revision/supersession
   contract, public `seo-observer crawl --project <p> --json` command.
   Acceptance: re-crawl same URL produces a new revision and supersedes the
   old one; stale (not re-crawled) pages are distinguishable from confirmed
   no-change in the payload; schema snapshot test updated alongside
   `storage_v1.json`.
3. **Per-page findings + orphan detection** — canonical/meta-robots conflict
   detection, multi-H1 detection, orphan-page comparison against
   `search_performance`/sitemap URLs already in storage.
   Acceptance: each finding type has a test with a real conflicting-signal
   fixture and a clean fixture that must NOT fire.
4. **Wire into `opportunities`** — add the new `type` values from §5 into the
   existing ranked list and `--type-breakdown` groups.
   Acceptance: a crawl finding appears in the default ranked
   `seo-observer opportunities` output with the same shape as the other five
   types, ranked by the same `score` comparator, and does not require a crawl
   to have run (missing crawl data is `missing` evidence quality, not an
   error).
