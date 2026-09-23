# AI Long-Tail Prompt Generation

`seo_observer.ai_prompts` builds deterministic long-tail conversational prompts from keyword sets (including Wordstat demand and GSC queries) for AI search engine visibility monitoring (Elmo, GEORank, ChatGPT Search, Perplexity, Gemini).

The module does not query search engines or neural networks directly (`НЕ делать: отправку промптов в нейронки`). Instead, it generates a structured prompt set artifact and manual provider export.

## CLI Usage

```bash
# Generate prompts from a configured keyword set
seo-observer prompts --project demo --keyword-set core --json

# Generate prompts with custom region and export directory
seo-observer prompts --project demo --keyword-set core --region "Москва" --output-dir /tmp/demo-prompts --json

# Generate prompts from ad-hoc keywords with explicit brand
seo-observer prompts --keywords "exam preparation, demo exam tickets" --brand demo --locale en-US --json

# Generate prompts including GSC search queries
seo-observer prompts --project demo --from-gsc --output-dir /tmp/demo-gsc-prompts --json

# Paraphrase using LLM fixture (deterministic acceptance runs)
seo-observer prompts --project demo --keyword-set core --llm-paraphrase --llm-model gpt-4o --llm-fixture fixtures/paraphrases.json --json
```

The alias `seo-observer ai-prompts` is also supported.

## Output Artifacts

When `--output-dir` is provided, the command writes:

- `prompts-extract.json`: complete JSON artifact with `prompt_set_hash`, generator metadata, summary counts, and prompt rows.
- `prompts-export.csv`: CSV table ready for manual upload to providers (Elmo/GEORank).
- `query-fan-out.json`: Elmo-compatible `query-fan-out` endpoint payload.
- `prompts-report.md`: human-readable Markdown summary with prompt set hash and sample discovery/branded prompts.
- `manifest.json`: artifact manifest detailing hashes and paths.

## Evidence Semantics & Invariants

1. **Determinism**:
   Identical input keywords, brands, locale, and region always produce the same `prompt_set_hash` and identical prompt rows. The prompt set hash and prompt export table (`prompts-export.csv`) are strictly reproducible across runs; volatile execution timestamps (`created_at`) are captured in extracts and manifests for audit and lineage.
2. **Cohort & Control Separation**:
   - Non-branded queries generate `cohort="discovery"`, `control="unbranded"`, `discovery_eligible=True`.
   - Branded queries (or brand-injected control pairs) generate `cohort="branded_control"`, `control="branded"`, `discovery_eligible=False`.
   - Branded control prompts are explicitly excluded from discovery metrics in Elmo imports (`discovery_metrics`).
3. **LLM Paraphrase**:
   - Only executed under the explicit flag `--llm-paraphrase`.
   - The model name is recorded in the artifact metadata under `generator.model`.
