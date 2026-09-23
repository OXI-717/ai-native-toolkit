# Marketing report

```bash
python3 scripts/marketing-report.py \
    --config <project.toml> --keyword-set <id> --market ru \
    --output <report.md> [--limit 60]
```

It answers a different question than `competitors audit`. The audit shows
**what the share of visibility is right now**; the marketing report shows
**where to invest first**. Hence two dimensions the audit does not have.

## Cluster

A whole topic, weighted by demand rather than phrase count. The difference
between "three queries dropped" and "a 6,930-impression cluster dropped" is
the difference between editing a meta tag and building a dedicated landing
page.

## Gap

A query where all of the following hold: demand exists, we are absent from
the top 20, and a direct competitor sits in the top 3. The third condition
matters — if no direct competitor takes the query, it is not a gap but an
unoccupied niche: a different kind of task with a different entry cost.

Gaps are sorted by demand, so the table is itself a work queue.

## Keyword-set markup

Canonical format:

```
# cluster: mortgage calculator
mortgage calculator    # yws=5772 yws_exact=3327
```

Contractor-export format is also supported:

```
# --- cluster: mortgage calculator (24 phrases, YWS 6930) ---
mortgage calculator    # YWS 5772 / 3327
```

A file without markup is read as before. In that case the report **honestly
states** that topics cannot be prioritized by volume, instead of showing
invented zero percentages.

## Source interpretation

The report explicitly warns about a discrepancy that is otherwise mistaken
for a bug: **Yandex Webmaster averages position over actually-served
impressions** — personalized, regional, and long-tail reformulations — and
therefore systematically looks more optimistic than a clean SERP.

A real case: for the query "price comparison tool" Webmaster showed an
average position of 8.5 with live clicks, while the site was absent from the
clean SERP top 20. There is no contradiction, but planning by Webmaster's
average position is impossible — decisions need a deterministic capture.
