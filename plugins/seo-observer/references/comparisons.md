# seo-observer comparisons v1

Deterministic comparisons are implemented in:

```text
plugins/seo-observer/cli/seo_observer/comparisons.py
```

They operate on already-built snapshot JSON dictionaries and do not call SEO
providers, read project configs, schedule jobs, or mutate storage.

The public local API is `compare_periods(current_snapshot, baseline_snapshot,
metric_path=..., comparison_kind=..., minimum_denominator=...)`.

Supported comparison kinds:

- `wow`
- `mom`
- `yoy`
- `matched_period`

Bad or non-comparable evidence returns explicit states instead of numeric
conclusions:

- `missing`
- `partial`
- `stale`
- `sampled`
- `incomparable`
- `denominator_too_small`
- `not_ready`

Ready comparisons return `state = "ready"`, `absolute_delta`,
`relative_delta`, and a `period_ratio` block. Period ratios are labeled as
period ratios, not percentages of goal completion. `period_ratio_percent` may
exceed 100 when the current period is larger than the baseline period.
