# AI Search Evidence Stack

SEO Hub and SEO Observer keep AI-search evidence dimensions separate:

- `mention`: the brand or domain appears in an AI answer body.
- `citation`: the brand or domain appears as a cited or linked source in the AI
  answer.
- `backlink`: a crawlable third-party page links to the target domain.
- `branded`: the query itself is branded; this is a separate dimension, not a
  stronger mention/citation/backlink.

Quality negatives are explicit:

- `stale`: evidence is older than the accepted collection window.
- `missing conversion`: outcome attribution is missing a required conversion
  metric.
- `changed basket`: the keyword/query basket changed between compared runs and
  the result is `not comparable`.

Synthetic smoke fixtures may exercise these states without calling paid
providers or live native collectors.
