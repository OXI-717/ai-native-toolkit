# AI Visibility Import

`seo_observer.ai_visibility_import` normalizes the read-only Elmo envelope emitted by
`oxi-seo`. The module does not call Elmo, does not trigger prompt runs, and does not
duplicate Elmo formulas.

## Evidence Semantics

The import result carries:

- `prompt_set_hash`
- expected and executed prompt counts
- prompt `cohort`
- `surface` and `model`
- `locale`
- observation `window`
- quality states: `complete`, `partial`, `stale`, `not_comparable`, `missing`

Prompt rows are preserved with branded-control metadata. Discovery metrics include only
non-branded prompts. Mention observations and citation observations are separate rows.

`citation-domains` rows are emitted with role `opportunity_candidate`; they are not treated
as backlink/referring-domain evidence.

## Quality Rules

- `missing`: required endpoints or executed evidence are absent.
- `partial`: expected prompt count is greater than executed prompt count.
- `stale`: Elmo `updated_at` is older than the configured freshness window.
- `not_comparable`: the imported Elmo window differs from the caller's expected window.
- `complete`: required endpoints exist, executed prompts cover expected prompts, data is
  fresh, and the requested window is comparable.
