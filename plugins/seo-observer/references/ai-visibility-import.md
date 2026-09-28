# AI Visibility Import

`seo_observer.ai_visibility_import` normalizes the read-only Elmo envelope emitted by
`seo-hub`. The module does not call Elmo, does not trigger prompt runs, and does not
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

## Citation platform types

Each `cited_domains` row carries `platform_type` from the deterministic
directory in `seo_observer.citation_platforms`: `reviews`, `forum_qa`,
`aggregator`, `media`, `marketplace`, or `unknown` for any domain not listed.
Matching is exact or by stripping leftmost labels (`old.reddit.com` →
`reddit.com`); unknown domains are never assigned a guessed type. Citation
observation rows carry a per-domain `platform_types` map.

## Platform presence

Each `cited_domains` row also carries `presence`, computed only from the
imported envelope (no live crawl of the platform):

- `present` — at least one executed prompt citing the domain reported a brand
  mention;
- `present_negative` — such a mention carries `sentiment: negative`;
- `absent` — executed prompts cite the domain without brand mentions;
- `unknown` — the domain appears only in the aggregate `citation-domains`
  endpoint or is cited only by excluded prompts, so mention context cannot be
  determined.

Only discovery-eligible prompts count, matching `discovery_metrics`: a
branded-control prompt's expected brand mention is not presence evidence for
the cited platform.

`platform_summary` groups cited domains by `platform_type` with domain and
citation counts and a per-state presence breakdown. `platform_metrics` exposes
the same per-domain counts (`prompts`, `mention_prompts`,
`negative_mention_prompts`) keyed by a collision-resistant domain key, so a
snapshot can resolve `ai_visibility.platform_metrics.<key>.<field>` per
platform.

Domain keys encode the normalized hostname bytes reversibly as hex (prefixed
with `host-`), avoiding collisions and dots in metric paths. Unusable hosts are
excluded from both metrics and draft actions. `negative_mention_prompts` is
`null` when no discovery prompt mentions the brand on that domain: disappearing
citations or mentions are missing evidence, not a successful sentiment repair.
Zero negative mentions with continued brand exposure remains a measurable
improvement. Draft IDs from the earlier slug/digest format must be regenerated;
these drafts are not automatically persisted.

## Draft observer-actions

`draft_actions` exports warmup actions for cited platforms whose presence is
not `present`. Drafts follow the existing observer-actions contract
(`seo_observer.actions.action_from_dict`): `lifecycle_state` `planned`, a
platform URL target, and a matched-period measurement window derived from the
import window. They are export payloads only — the importer does not persist
them, crawl platforms, publish, or perform outreach/autoposting.

Action, hypothesis, and window identifiers embed the domain key (a reversible
hex encoding of the normalized hostname), so distinct normalized domains
produce distinct actions. Each draft's expected signal
measures its own platform through `platform_metrics`: `mention_prompts`
(increase, `minimum_absolute_delta` 1) for `absent`/`unknown` presence, and
`negative_mention_prompts` (decrease, same threshold) for `present_negative`.
The observation window never starts earlier than the action's `changed_at`
date, even when the import arrives after the source window has closed.

## Quality Rules

- `missing`: required endpoints or executed evidence are absent.
- `partial`: expected prompt count is greater than executed prompt count.
- `stale`: Elmo `updated_at` is older than the configured freshness window.
- `not_comparable`: the imported Elmo window differs from the caller's expected window.
- `complete`: required endpoints exist, executed prompts cover expected prompts, data is
  fresh, and the requested window is comparable.
