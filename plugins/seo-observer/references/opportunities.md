# Opportunity Reports

`seo-observer opportunities` builds prioritized opportunity findings from the
already collected `search_performance` and `crawl_pages` tables. It does not
call GSC, Webmaster, providers, or live URLs.

## Command

```bash
seo-observer opportunities --project demo --start 2026-07-01 --end 2026-07-07 --json
```

Default JSON output is one ranked list in `opportunities`. `--type-breakdown`
adds the source report groups for debugging and audit; the default output is
not independent reports per source.

Empty storage returns status `empty_storage`. Fewer rows than the sample
threshold returns `insufficient_storage`. A populated window with no ranked
findings returns `no_findings`.

## Constants

Constants live in `seo_observer.opportunities`:

- `EXPECTED_CTR_BY_POSITION`: CTR heuristic for Google positions 1..10.
- `SECOND_PAGE_MIN_POSITION = 11.0`.
- `SECOND_PAGE_MAX_POSITION = 20.0`.
- `SECOND_PAGE_EXPECTED_CTR = EXPECTED_CTR_BY_POSITION[10]`.
- `MIN_IMPRESSIONS = 1`.
- `MIN_SAMPLE_SIZE = 3`.
- `DECAY_DROP_THRESHOLD = 0.30`.
- `QUICK_WIN_MEDIAN_MODE = "strictly_above"`.
- `REFRESH_AGE_SCORE_UNIT_DAYS = 30`.
- `CRAWL_OPPORTUNITY_TYPES`: local crawl opportunity type values.

The CTR table is a heuristic, not a provider fact:

| Position | Expected CTR |
|---:|---:|
| 1 | 0.280 |
| 2 | 0.150 |
| 3 | 0.110 |
| 4 | 0.080 |
| 5 | 0.060 |
| 6 | 0.050 |
| 7 | 0.040 |
| 8 | 0.035 |
| 9 | 0.030 |
| 10 | 0.025 |

Average positions are rounded to the nearest integer and clamped to 1..10
before looking up this table.

## Formulas

Quick wins:

```text
median = median(nonzero impressions in the selected window)
candidate = position in 1..10
            and impressions > max(MIN_IMPRESSIONS, median)
            and observed_ctr < expected_ctr(position)
score = max(impressions * expected_ctr - clicks, 0)
```

Median handling is strict: a row exactly at the median is not a quick win.

Second-page queries:

```text
candidate = 11.0 <= average_position <= 20.0
            and impressions > 0
score = impressions * SECOND_PAGE_EXPECTED_CTR
```

The boundaries are inclusive, so exact positions 11 and 20 count. Zero
impressions do not count.

Decay:

```text
latest = selected start/end window
previous = same number of days ending the day before start
drop = (previous_clicks - latest_clicks) / previous_clicks
candidate = drop >= DECAY_DROP_THRESHOLD
score = (previous_clicks - latest_clicks) * drop
```

If the previous comparable window is missing, the finding is emitted with
`evidence_quality = "partial"` and `rank_excluded = true`. If previous clicks are
zero, the finding is `not_comparable` and excluded from rank.

Cannibalisation:

```text
candidate = one query has at least two page URLs on the same domain
score = total_impressions_for_query_domain * (url_count - 1)
```

A single URL for a query is not cannibalisation.

Refresh:

```text
potential = sum(max(impressions * expected_ctr_or_second_page_ctr - clicks, 0))
age_days = selected_end - last_change_date
score = potential * (age_days / REFRESH_AGE_SCORE_UNIT_DAYS)
```

Last-change dates are read only from existing raw artifacts linked to the
window rows. Recognized URL fields are `url`, `page_url`, and `canonical_url`;
recognized date fields are `last_change`, `last_changed`, `last_modified`,
`modified_at`, `updated_at`, and `published_at`. If no date is available, the
refresh finding is emitted with `evidence_quality = "missing"` and excluded from
the ranked list.

Local crawl findings:

```text
crawl_canonical_conflict = crawled page has disagreeing HTML and Link-header canonicals
crawl_broken_internal_link = crawled URL returns HTTP 4xx/5xx and was reached from an internal link
crawl_orphan_page = search_performance URL is not reached by the latest crawl graph
crawl_meta_robots_conflict = meta robots and X-Robots-Tag directives disagree
```

If no current local crawl rows exist for the project, each crawl type is emitted
in `--type-breakdown` with `evidence_quality = "missing"` and
`rank_excluded = true`; missing crawl data does not fail the command.

## Honesty Layer

Every finding includes:

- `evidence_quality`: `complete`, `partial`, `stale`, `missing`, or
  `not_comparable`.
- `sample_size`: number of storage rows supporting the finding.

Search-performance findings backed by fewer than `MIN_SAMPLE_SIZE` rows are
`partial` unless they provide an explicit evidence quality. Rows with stale
freshness are `stale`; rows with non-comparable storage comparability are
`not_comparable`; sampled or non-complete coverage rows are `partial`.
