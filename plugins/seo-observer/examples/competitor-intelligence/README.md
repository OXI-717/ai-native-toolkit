# seo-observer competitor intelligence example

Last verified: 2026-07-30

This folder is a safe local example for the full competitor workflow. The
domains use `.example`, provider credential fields name local environment
variables only, and fixture artifacts contain normalized rows only.

## Commands

Run config readiness without external calls:

```bash
seo-observer doctor --config project.toml --json
```

Create deterministic competitor discovery artifacts:

```bash
seo-observer competitors discover \
  --config project.toml \
  --provider-mode artifact \
  --output-dir /tmp/demo-example-competitors \
  --json
```

Confirm competitors against deterministic SERP evidence:

```bash
seo-observer competitors audit \
  --config project.toml \
  --keyword-set core \
  --start 2026-07-01 \
  --end 2026-07-30 \
  --provider-mode artifact \
  --output-dir /tmp/demo-example-competitors \
  --json
```

Collect research-only page evidence:

```bash
seo-observer competitors research \
  --config project.toml \
  --keyword-set core \
  --provider-mode artifact \
  --output-dir /tmp/demo-example-content \
  --json
```

Derive content gaps and an optional grounded brief:

```bash
seo-observer competitors report \
  --config project.toml \
  --discovery-artifact fixtures/competitor-extract.json \
  --serp-artifact fixtures/serp-extract.json \
  --content-artifact fixtures/content-extract.json \
  --provider-mode artifact \
  --output-dir /tmp/demo-example-report \
  --json
```

Live paid calls require both `--provider-mode live` and `--allow-paid`; artifact
mode reads local files only. `--output-dir` controls local artifact placement,
and `--json` returns the machine-readable contract.

## Evidence Classes

- Discovery and audit rows are deterministic when source quality is `live`.
- Research page extracts are `research-only` until confirmed by SERP evidence.
- Content-gap reports can use deterministic and research-only citations, but
  rank, traffic, share-of-voice, and trend claims require deterministic
  citations.
- Brief output is optional and citation-validated; failed validation writes a
  blocked brief artifact instead of an unsupported conclusion.

## Fixtures

The `fixtures/` directory mirrors the artifact files used by
`competitors report`:

- `manifest.json`
- `competitor-extract.json`
- `serp-extract.json`
- `competitor-metrics.json`
- `content-extract.json`
