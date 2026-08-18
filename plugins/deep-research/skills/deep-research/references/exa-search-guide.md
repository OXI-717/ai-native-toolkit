# Exa AI Search Guide

## Overview

Exa is the PRIMARY search tool for all research agents. WebSearch is a fallback only when Exa doesn't return sufficient results.

Exa returns clean, LLM-ready content — no ads, navigation, or boilerplate. This makes it ideal for autonomous subagents that need to process search results without human curation.

## Three Exa Tools

### 1. `mcp__exa__web_search_exa` — Primary Discovery

**When:** Default search for articles, studies, documentation, blog posts, news.

**Key Parameters:**
- `query`: Search query (natural language works best)
- `numResults`: Number of results (default 8, use 10-12 for broad discovery)
- `livecrawl`: Set to `"preferred"` for topics needing fresh 2025-2026 data

**Examples:**
```
# Broad discovery
query: "kubernetes vs nomad orchestration comparison 2026"
numResults: 10

# Specific data
query: "creatine monohydrate dosage meta-analysis RCT"
numResults: 12

# Fresh data
query: "LLM inference cost optimization 2026"
numResults: 8
livecrawl: "preferred"
```

### 2. `mcp__exa__company_research_exa` — Company Intelligence

**When:** Business/product domain — researching companies, competitors, market position.

**Key Parameters:**
- `companyName`: Company name

**Examples:**
```
companyName: "Hetzner"
companyName: "Vercel"
```

### 3. `mcp__exa__get_code_context_exa` — Code & Documentation

**When:** Tech domain — APIs, libraries, implementation patterns, GitHub repos.

**Key Parameters:**
- `query`: What to search for
- `tokensNum`: Amount of context (1000 for quick reference, 5000-10000 for detailed examples, up to 50000 for comprehensive)

**Examples:**
```
query: "fastapi middleware authentication pattern"
tokensNum: 5000

query: "terraform aws ecs module best practices"
tokensNum: 10000
```

## Domain → Tools Mapping

| Domain | Primary Exa Tool | Secondary | WebFetch For |
|--------|-----------------|-----------|--------------|
| **tech** | web_search + code_context | — | Official docs, changelogs |
| **business** | web_search + company_research | — | Annual reports, press releases |
| **health** | web_search | — | PubMed abstracts, clinical trials |
| **security** | web_search + code_context | — | CVE details, security advisories |
| **general** | web_search | — | Specific pages from results |

## Search Strategy for Scouts

Each Scout agent should follow this search pattern:

### Step 1: Broad Discovery (8-12 results)
```
mcp__exa__web_search_exa:
  query: "[topic] comprehensive analysis"
  numResults: 10
```

### Step 2: Deep Extraction (if needed)
For specific pages that need full content:
```
WebFetch:
  url: "[URL from Exa results]"
  prompt: "Extract key findings, data points, and conclusions about [topic]"
```

### Step 3: Supplementary Search (if Exa insufficient)
```
WebSearch:
  query: "[refined query for gaps]"
```

### Step 4: Fresh Data (if topic is current)
```
mcp__exa__web_search_exa:
  query: "[topic] 2025 2026"
  livecrawl: "preferred"
```

## Query Formulation Tips

1. **Vary query structure** — don't repeat similar queries:
   - Broad: "topic overview analysis"
   - Specific: "topic statistics data report 2026"
   - Expert: "topic expert opinion research findings"
   - Counter: "topic criticism problems limitations"

2. **Use date qualifiers** for freshness:
   - "topic 2025 2026" forces recent results
   - `livecrawl: "preferred"` gets latest crawled pages

3. **Search for counterarguments explicitly:**
   - "topic criticism"
   - "topic problems limitations"
   - "topic alternative approach"

4. **Track URLs** — don't search the same content twice

## Source Priority

When evaluating sources from Exa results, prioritize:

1. Government sources (.gov) — highest authority
2. Academic institutions (.edu) — research quality
3. Peer-reviewed journals — verified methodology
4. Established news organizations — editorial standards
5. Industry reports from recognized firms — domain expertise
6. Community resources (Wikipedia, StackOverflow) — overview and context only

## Anti-Patterns

- Don't search the same query twice with minor variations
- Don't over-rely on a single source for multiple claims
- Don't ignore sources that contradict your emerging thesis
- Don't skip WebFetch when a source needs deeper extraction
- Don't use more than 15 Exa calls per scout — diminishing returns
