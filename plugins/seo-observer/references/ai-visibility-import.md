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
targets its own platform through `platform_metrics`: `mention_prompts`
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

### Native storage and snapshots

The artifact-only `ai-visibility` CLI persists normalized import receipts in the
project SQLite database. Raw provider payloads and prompt text are not embedded
in portable snapshots; receipts retain the input hash, prompt-set hash, brand,
property, model, surface, locale, window, quality and per-platform metrics.

```sh
seo-observer --config project.toml ai-visibility import --json \
  --input baseline-envelope.json --property-id main --period-id ai-baseline \
  --observed-at 2026-09-01T00:00:00Z
seo-observer --config project.toml ai-visibility snapshot --json \
  --period-id ai-baseline --generated-at 2026-09-01T00:00:00Z --output-dir ./reports
```

Import the observation envelope under a separate period ID and write its
snapshot in the same way. Then evaluate one saved JSON draft (or the existing
TOML action format) using its explicit measurement window and one primary platform
signal. Drafts with multiple signals are rejected:

```sh
seo-observer --config project.toml ai-visibility evaluate --json \
  --action platform-action.json --window-id '<window_id from the action>' \
  --baseline baseline-snapshot.json --observation observation-snapshot.json \
  --as-of 2026-10-03
```

Import writes evidence only. Snapshot writes the native snapshot and manifest.
Evaluate returns a verdict without activating the draft or writing the action
journal. None of these commands calls a provider, posts content or runs a model.
A positive verdict means the configured signal improved under matched evidence;
it does not establish that the action caused the change.

The evaluator requires complete, fresh, comparable receipts with matching
project, property, brand, model, surface, locale, prompt set and executed prompt
population. Both source windows must exactly match the action window and timezone.
Source envelopes must use schema version 1 and include the provider analytics
`updated_at`; missing source freshness is an import error. Future source
updates/open measurement windows cannot be treated as comparable. The evaluator
verifies the full native snapshot hash before using receipt metrics.
Unobserved platforms and absent brand exposure remain `not_ready` for sentiment
repair. Ordinary snapshots without AI evidence still return `not_ready`.

Imports are immutable and idempotent. A later partial observation supersedes an
older complete one for the period. Conflicting observations at the same timestamp
or multiple properties in one period are rejected as ambiguous; choose distinct
period IDs for separate properties. Snapshot rebuild restores the receipt and
its evidence hash. Existing snapshots without the additive AI section remain valid.
