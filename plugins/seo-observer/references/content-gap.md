# seo-observer content gap and grounded briefs

Last verified: 2026-07-30

Provider endpoint family/version notes:

- This report command reads Task 03 `competitor-extract.json`, Task 04
  `serp-extract.json` / `competitor-metrics.json` evidence when present, and
  Task 05 `content-extract.json` page extracts.
- It does not call DataForSEO, Exa, browser automation, or a live LLM in
  artifact mode.
- Fixture capture date for provider fixtures: 2026-07-30.

## Command

```bash
seo-observer competitors report \
  --project demo \
  --discovery-artifact /tmp/demo/competitor-extract.json \
  --serp-artifact /tmp/demo/serp-extract.json \
  --content-artifact /tmp/demo/content-extract.json \
  --output-dir /tmp/demo-content-gap \
  --json
```

Optional brief synthesis:

```bash
seo-observer competitors report \
  --project demo \
  --discovery-artifact /tmp/demo/competitor-extract.json \
  --serp-artifact /tmp/demo/serp-extract.json \
  --content-artifact /tmp/demo/content-extract.json \
  --output-dir /tmp/demo-content-gap \
  --with-brief \
  --llm-fixture /tmp/demo/brief-fixture.json \
  --json
```

## Artifacts

The command writes:

- `manifest.json`
- `content-gap.json`
- `competitor-report.md`
- `brief.json` only when `--with-brief` is supplied

`content-gap.json` contains deterministic content gaps from keyword gaps,
confirmed SERP competitor pages, page headings, page summaries, and SERP
features such as people-also-ask rows. Opportunity score is transparent:

```text
search_volume * (1 - difficulty / 100)
```

When either component is missing, `opportunity_score` is `null` and
`opportunity_score_reason` explains the missing input.

## Grounding Rules

LLM briefs are optional synthesis, not evidence. Every substantive claim must
cite known artifact IDs. Unknown citation IDs, fabricated URLs, fabricated rank
/ volume / traffic / SOV numbers, and research-only citations for rank,
traffic, SOV, or trend claims block the brief section.

When evidence is below threshold, deterministic artifacts are still written and
`brief.json` is marked `blocked_low_evidence`.

Markdown includes citation IDs and artifact references, but not raw provider
payloads, request bodies, auth headers, cookies, tokens, or credential values.
