# seo-observer aggregate outcomes

The public `seo-observer outcomes` command measures a recorded local action
against stored compare evidence:

```bash
seo-observer outcomes \
  --project demo \
  --action-id action:abc123 \
  --start 2026-07-08 \
  --end 2026-07-14 \
  --baseline-start 2026-07-01 \
  --baseline-end 2026-07-07 \
  --json
```

The after window is `--start`/`--end`. The before window is
`--baseline-start`/`--baseline-end`; when omitted it defaults to the adjacent
previous window with the same inclusive day count, matching `compare`.

The command uses the existing local compare window logic. It evaluates:

- `affected`: rows whose URL or query matches the recorded action targets;
- `control`: rows in the same windows that do not match those targets.

The JSON status is one of:

- `ok`: affected windows are comparable and evidence quality is complete;
- `too_early`: the after window is shorter than the named maturation threshold;
- `not_comparable`: compare reports non-comparable affected windows;
- `partial`: compare is comparable but evidence quality is partial.

`too_early` suppresses effect deltas instead of reporting immature numbers.
Other insufficient-data and non-comparable cases reuse compare status/reasons
instead of inventing a separate outcome-specific reason system.

Outcome output must be phrased as observed change, not causality. The affected
slice and control slice are both emitted. If both move in the same direction or
by the same percentage, consumers must leave that visible rather than hiding
the control movement.

`seo_observer.outcomes` is a local, fixture-testable foundation for
server-side aggregate outcome facts and analytics/server reconciliation. It does
not open database connections, inspect live credentials, accept report-time
query text, export row-level users, or perform autonomous SEO actions.

## Aggregate source contract

Aggregate sources may reference only approved view descriptors. A descriptor is
a stable local contract around an approved source/view ID plus parameter names
and semantic metadata. It is not SQL.

Accepted descriptor shape:

- `view_id` / source-approved aggregate view ID;
- `outcome_id`;
- `population_id` and `population_name`;
- `attribution_model`, `attribution_window`, and `attribution_source`;
- `counting_unit`;
- `dedupe_key` and `dedupe_source`;
- `timestamp_field` and `timezone`;
- `period_start` / `period_end` fields;
- provider/source request ID field;
- `aggregation_grain`.

Forbidden inputs include `sql`, `query`, `table`, `where`, `where_clause`,
`sql_fragment`, and `report_query`. Project config and the descriptor
constructor reject these fields. The current adapter calls only an injected
transport:

```python
transport.fetch_aggregate_view(descriptor, params={"period_start": "...", "period_end": "..."})
```

This keeps tests deterministic and prevents live database calls by default.

## Demo fixtures

`build_demo_outcome_sources()` exposes fixture-approved descriptors for:

- `outcome_auth` / `demo_auth.registration_outcomes_daily_v1` /
  `outcome_id = "registration"`;
- `outcome_pay` / `demo_pay.paid_purchase_outcomes_daily_v1` /
  `outcome_id = "paid_purchase"`.

These are contract fixtures only. They are not production Demo configuration and
do not contain credentials.

## Reconciliation

`reconcile_outcomes()` compares one analytics outcome fact with one server
aggregate fact. It refuses to compute deltas when the facts disagree on:

- outcome or period;
- population ID/name;
- counting unit;
- attribution model/window/source;
- dedupe key/source;
- timestamp field;
- timezone;
- aggregation grain.

Incompatible facts return `status = "not_comparable"` with reasons and lineage
to both source request IDs. Comparable facts return local `delta`, `ratio`,
source counts, and lineage.

## Export policy

Exact counts may remain in local `OutcomeFact` objects and local intermediate
storage. Artifact channels (`git`, `markdown`, `telegram`) use
`export_outcome_value()` and never emit exact counts:

- values below `suppression_threshold` are suppressed as `<threshold`;
- complementary values are suppressed when `total_count - value` would reveal a
  suppressed small cell;
- differencing risk is suppressed when comparing to a prior export would reveal
  a small exact count;
- remaining values are emitted as bands such as `10-24` or `100+`.

The export helper returns explicit `suppressed`, `exact`, and `reason` fields so
report layers can avoid publishing misleading or privacy-sensitive counts.
