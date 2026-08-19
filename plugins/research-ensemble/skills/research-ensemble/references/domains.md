# Domain Configuration

## How Domains Work

Each research domain customizes:
1. Scout stream assignments (what each scout researches)
2. Which Exa tools to prioritize
3. Which conditional agents are MANDATORY/optional/skip
4. What the synthesis should focus on
5. Additional rules injected into agent prompts

## Domain: tech

### Scout Streams
- **Stream A (Analytical):** Technology landscape — current state, key players, maturity levels
- **Stream B (Contrarian):** Criticisms, failure cases, limitations, alternatives
- **Stream C (Pragmatic):** Benchmarks, performance comparisons, migration paths, real-world usage

Deep level adds:
- **Stream D (Systems-thinking):** Architecture patterns, integration complexity, ecosystem effects
- **Stream E (Mechanistic):** Implementation details, internals, how it actually works

### Exa Tools
- Primary: `mcp__exa__web_search_exa` + `mcp__exa__get_code_context_exa`
- WebFetch for: official documentation, changelogs, benchmark results

### Conditional Agents
- Statistician: if ≥10 quantitative claims (benchmarks, performance numbers)
- Domain Reviewer: OPTIONAL — check for deprecated APIs, licensing issues, scalability concerns
- Interaction Mapper: SKIP

### Synthesis Focus
Decision matrix, migration path, implementation recommendations, risk assessment.

### Additional Agent Rules
- Prefer primary sources (official docs, GitHub repos) over blog posts
- Note version numbers — tech changes fast
- Distinguish between marketing claims and benchmark data

---

## Domain: business

### Scout Streams
- **Stream A (Analytical):** Market size, growth projections, industry structure
- **Stream B (Contrarian):** Market risks, failure cases, bear thesis
- **Stream C (Pragmatic):** Business models, unit economics, competitive moats

Deep level adds:
- **Stream D (Systems-thinking):** Market dynamics, network effects, regulatory landscape
- **Stream E (Mechanistic):** Revenue mechanics, cost structures, scaling economics

### Exa Tools
- Primary: `mcp__exa__web_search_exa` + `mcp__exa__company_research_exa`
- WebFetch for: annual reports, press releases, SEC filings

### Conditional Agents
- Statistician: if ≥10 quantitative claims (market data, financial projections)
- Domain Reviewer: OPTIONAL — regulatory compliance, risk assessment validity
- Interaction Mapper: SKIP

### Synthesis Focus
ROI analysis, competitive advantage assessment, go/no-go recommendation.

### Additional Agent Rules
- Distinguish between projections and historical data
- Note data freshness — business data ages quickly
- Cross-reference company claims with independent sources

---

## Domain: health

### Scout Streams
- **Stream A (Analytical):** RCTs, meta-analyses, systematic reviews — the evidence base
- **Stream B (Contrarian):** Negative findings, null results, failed replications, minority views with evidence
- **Stream C (Pragmatic):** Dose-response, practical protocols, cost-effectiveness, adherence

Deep level adds:
- **Stream D (Systems-thinking):** Interactions, feedback loops, second-order effects, cumulative risks
- **Stream E (Mechanistic):** Molecular/physiological mechanisms, pathways, bioavailability

### Exa Tools
- Primary: `mcp__exa__web_search_exa`
- WebFetch for: PubMed abstracts, clinical trial results, WHO/FDA guidelines

### Conditional Agents
- Statistician: ALWAYS MANDATORY — verify study methodology
- Domain Reviewer: ALWAYS MANDATORY — dosage safety, contraindications, drug interactions
- Interaction Mapper: MANDATORY for deep level — nutrient×nutrient, nutrient×genetics, nutrient×medication

### Synthesis Focus
Evidence-based protocol with safety profile, monitoring plan, confidence per recommendation.

### Additional Agent Rules
- NEVER recommend without citing evidence grade
- Always note sample size and study design
- Distinguish clinically meaningful from statistically significant
- Flag anything requiring physician consultation
- If user profile exists — personalize recommendations
- Conservative: if in doubt, recommend caution

---

## Domain: security

### Scout Streams
- **Stream A (Analytical):** Threat models, attack vectors, vulnerability landscape
- **Stream B (Contrarian):** Security theater, false sense of security, overlooked vectors
- **Stream C (Pragmatic):** Best practices, tools, implementation guides, compliance checklists

Deep level adds:
- **Stream D (Systems-thinking):** Defense in depth, supply chain risks, emergent threats
- **Stream E (Mechanistic):** Exploit mechanics, cryptographic details, protocol internals

### Exa Tools
- Primary: `mcp__exa__web_search_exa` + `mcp__exa__get_code_context_exa`
- WebFetch for: CVE details, security advisories, NIST guidelines

### Conditional Agents
- Statistician: ALWAYS MANDATORY — verify vulnerability severity, incident statistics
- Domain Reviewer: ALWAYS MANDATORY — verify recommendations don't create new vulnerabilities
- Interaction Mapper: OPTIONAL — for complex multi-layer security architectures

### Synthesis Focus
Threat model, prioritized hardening checklist, compliance mapping, incident response considerations.

### Additional Agent Rules
- Verify CVE numbers and severity ratings
- Note if vulnerabilities are patched and in which versions
- Distinguish theoretical vs exploited-in-the-wild
- Always include remediation steps
- Check OWASP, NIST, CIS benchmarks as authoritative sources

---

## Domain: general

### Scout Streams
- **Stream A (Analytical):** Landscape overview, key concepts, taxonomy
- **Stream B (Contrarian):** Criticisms, alternative viewpoints, limitations
- **Stream C (Pragmatic):** Practical applications, actionable insights, real-world examples

Deep level adds:
- **Stream D (Systems-thinking):** Connections to other domains, second-order effects
- **Stream E (Mechanistic):** Underlying mechanisms, cause-and-effect chains

### Exa Tools
- Primary: `mcp__exa__web_search_exa`
- WebFetch for: specific pages from search results

### Conditional Agents
- Statistician: if ≥10 quantitative claims
- Domain Reviewer: SKIP
- Interaction Mapper: SKIP

### Synthesis Focus
Comprehensive overview with actionable insights and clear next steps.

### Additional Agent Rules
- Balance breadth and depth appropriately
- Note when topic crosses into specialized domains
