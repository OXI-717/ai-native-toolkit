---
name: infocompressor
description: |
  Use when the user needs to compress, condense, or summarize lengthy content while preserving ALL
  critical details. Triggers: "compress this", "make it shorter but keep everything", "create a
  cheat sheet", "dense summary", "reference format", "compact version", "information-dense". NOT
  for casual summaries—only when user explicitly wants maximum density with zero data loss.
---
# Text Compression Skill

Transform verbose documents into dense, human-readable reference specs.

## Core Principles

- **Style:** Spec/reference guide — imperative, terse, scannable
- **Format:** Markdown with minimal nesting
- **Redundancy:** Zero. State each fact once.
- **Notation:** Plain language with minimal intuitive symbols.
- **Target:** 40-60% reduction for most technical documents.
- **DRY:** Don't repeat. Reference existing docs instead of duplicating.
- **Progressive disclosure:** Link to details, don't inline everything.

---

## Referencing Other Documents

When information exists elsewhere, reference it — don't copy it.

**Syntax:**
- `@DocumentName` — mention another doc in same system
- `[Label](URL)` — link to external resource
- Imperative prefix: "Read @Auth for token handling"

**Patterns:**

| Instead of... | Write... |
|---------------|----------|
| Copying auth flow from another doc | "Auth flow: Read @Authentication" |
| Repeating error format spec | "Error format: See @APIConventions#errors" |
| Duplicating setup instructions | "Setup: Follow @GettingStarted first" |

**When to reference vs inline:**

| Situation | Action |
|-----------|--------|
| Info exists in canonical source | Reference it |
| Info is 1-2 lines, critical to flow | Inline it |
| Reader needs context to proceed | Inline minimum, reference full |
| Info changes frequently | Reference (single update point) |

**Example:**

```markdown
## Create Order

Purpose: Submit new order for processing

`POST /orders`

Prerequisites: Read @Authentication for token setup

**Input:**
- items: array of {sku, qty}
- shipping: address object (see @DataTypes#address)

**Flow:**
Validate → Check inventory (@InventoryService) → Reserve → Confirm

**Errors:**
Standard error format. See @APIConventions#errors
- 400: invalid items
- 409: insufficient stock
```

---

## Information Priority

Preserve in order of criticality. Never lose critical items.

| Priority | Category | Examples |
|----------|----------|----------|
| Critical | Identifiers | Names, IDs, versions, dates, codes |
| Critical | Values | Numbers, thresholds, percentages, limits |
| Critical | Entities | People, systems, components, orgs |
| High | Relationships | X depends on Y, A contains B |
| High | Causality | If X then Y, triggers, because |
| High | Actions | What was done, chosen, rejected |
| Medium | Sequences | Order, steps, workflows |
| Medium | Constraints | Limits, conditions, exceptions |
| Low | Context | Background, history, rationale |

---

## Representation Selection

Pick the **first** format that fits:

| Data Shape | Use |
|------------|-----|
| Single fact | Inline text |
| List of items (no attributes) | Bullet points |
| Named properties | `key: value` pairs |
| Simple sequence (no branching) | Numbered list |
| Sequence with branching | MermaidJS flowchart |
| 2+ attributes per item | Table |

---

## Language Rules

### Sentence Construction

- **Imperative mood.** "Validate input" not "The input should be validated"
- **Present tense.** "System returns error" not "System will return"
- **Active voice.** "User submits form" not "Form is submitted by user"
- **3-7 words per statement** when possible

### Words to Cut

| Remove | Replace With |
|--------|--------------|
| In order to | *(delete, use infinitive)* |
| It is necessary to | Must |
| Due to the fact that | Because |
| In the event that | If |
| At this point in time | Now |
| Has the ability to | Can |
| Is responsible for | *(verb directly)* |
| Basically, essentially, actually | *(delete)* |
| Please note that | *(delete)* |
| The fact that | *(delete)* |
| In cases where | When / If |
| There is/are | *(rephrase)* |
| It should be noted | *(delete)* |
| As a matter of fact | *(delete)* |
| For the purpose of | For / To |
| In the process of | *(delete, use -ing verb)* |
| On a daily basis | Daily |
| At the present time | Now / Currently |
| In the near future | Soon |
| A large number of | Many |
| The vast majority of | Most |
| In spite of the fact that | Although / Despite |
| Perhaps, might, it seems | *(delete or commit)* |
| Moving on to | *(delete)* |
| As mentioned earlier | *(delete)* |

### Standard Qualifiers

Use consistently throughout:

- **Must** — required, will fail without
- **Should** — recommended, best practice
- **May** — optional, allowed
- **Never** — prohibited

### Relationship Notation

Use these intuitive symbols inline when clearer than prose:

| Symbol | Meaning | Example |
|--------|---------|---------|
| `→` | leads to, causes, then, outputs | `Input → Validate → Store` |
| `←` | depends on, requires, from | `Service ← Database` |
| `↔` | bidirectional | `Client ↔ Server` |
| `\|` | or, alternative | `JSON \| XML` |
| `+` | and, combined | `Auth + Logging` |
| `?` `:` | if-then-else | `Valid ? Process : Reject` |
| `..` | range | `1..100`, `A..Z` |

### Pattern Transformations

Convert verbose patterns to compact forms:

| Verbose Pattern | Compressed |
|-----------------|------------|
| "X results in Y" | X → Y |
| "X depends on Y" | X ← Y |
| "X and Y communicate" | X ↔ Y |
| "If X then Y, otherwise Z" | X ? Y : Z |
| "from X to Y" | X..Y |
| "X or Y" | X \| Y |
| "X followed by Y followed by Z" | X → Y → Z |
| "X contains A, B, and C" | X: A, B, C |
| "the value ranges from 10 to 100" | value: 10..100 |

---

## Structure Templates

### Concept / Definition

```markdown
## Term Name

Short definition in one sentence.

- property: value
- property: value
- related: Other Concept
```

### Process / Flow (with branching)

```markdown
## Process Name

Brief purpose (1 line).

​```mermaid
flowchart LR
    A[Start] --> B{Condition?}
    B -->|yes| C[Action 1]
    B -->|no| D[Action 2]
    C --> E[End]
    D --> E
​```

**Errors:**
- Condition X: return 400
- Condition Y: return 401
```

### Process / Flow (linear)

```markdown
## Process Name

1. Actor does action
2. System validates
3. System creates record
4. Return confirmation
```

Or inline for simple flows:

```markdown
## Process Name

Input → Validate → Transform → Store → Respond
```

### Constraint / Rule

```markdown
## Rule Name

Applies to: [scope]

- condition: description
- condition: description

Violation: what happens
```

### API Endpoint

```markdown
## Endpoint Name

Purpose: what business problem this solves (1 line)

`METHOD /path/{param}`

**Input:**
- param (path): description
- field (body): type, constraints

**Output:** description of success response

**Errors:**
- 400: invalid input
- 401: not authenticated
- 404: resource not found
```

### Configuration / Options (simple)

```markdown
## Config Section

- timeout: 30s
- retries: 3
- cache: enabled
```

### Configuration / Options (with attributes)

```markdown
## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| timeout | int | 30 | Seconds before abort |
| retries | int | 3 | Max retry attempts |
```

### Decision Record

```markdown
## Decision Name

Context: brief situation

| Option | Outcome |
|--------|---------|
| A | consequence |
| B | consequence |

Chosen: A — reason
```

---

## Compression Pipeline

### Step 1: Segment

Read document. Tag each chunk:

- **Concept** — defines something
- **Flow** — describes sequence
- **Rule** — states constraint
- **Config** — lists settings
- **Example** — shows usage

### Step 2: Normalize

- Pick one term per concept. Use everywhere.
- Convert to active voice, present tense.
- Replace verbose phrases (see Words to Cut).

### Step 3: Structure

- Match each segment to a template.
- Extract only required fields.
- Discard prose that doesn't fit.

### Step 4: Compress

- Shorten sentences to 3-7 words.
- Remove articles (a, an, the) where clear.
- Use bullets over paragraphs.
- Use key-value over bullets when named.
- Use flowchart over list when branching.
- Apply relationship notation where clearer than words.
- Remove hedging — commit to statements or omit.
- Remove transitions between sections.
- Remove attribution when source is obvious.
- Replace duplicated content with @references to canonical source.

### Step 5: Validate

- [ ] All requirements preserved?
- [ ] All error/edge cases listed?
- [ ] All config options present?
- [ ] No duplicate information?
- [ ] Can reader reconstruct full meaning?

---

## Density Levels

Choose based on audience:

| Level | Reduction | Use For |
|-------|-----------|---------|
| Light | 30-40% | General audience, first-time readers |
| Medium | 40-60% | Technical audience, reference docs |
| Heavy | 60-75% | Expert audience, quick lookup, cheat sheets |

Default to **Medium** for most technical documentation.

---

## Example

### Before (147 words)

```
User Authentication Process

When a user wants to log in to the system, they need to provide their 
email address and password. The system will first check if the email 
exists in the database. If the email is not found, an error message 
will be displayed indicating that the account does not exist.

If the email is found, the system will then verify the password. The 
password is checked against the hashed version stored in the database. 
If the password is incorrect, the system will increment the failed 
login counter and return an error. After 5 failed attempts, the 
account will be temporarily locked for 15 minutes.

If the password is correct, the system will generate a session token 
and return it to the user. This token must be included in all 
subsequent requests.
```

### After (67 words, 54% reduction)

```markdown
## Login

Purpose: Authenticate user, issue session token for API access

`POST /auth/login`

**Input:**
- email: string
- password: string

**Flow:**

​```mermaid
flowchart TD
    A[Receive credentials] --> B{Email exists?}
    B -->|no| C[404]
    B -->|yes| D{Password valid?}
    D -->|no| E[Increment fails]
    E --> F{Fails >= 5?}
    F -->|yes| G[Lock 15 min]
    F -->|no| H[401]
    G --> H
    D -->|yes| I[Generate token]
    I --> J[Return token]
​```

**Rules:**
- Lock after: 5 failed attempts
- Lock duration: 15 minutes
- Token required for all subsequent requests

**Errors:**
- 404: email not found
- 401: invalid password
- 423: account locked
```

---

## Final Checklist

Before delivering compressed document:

**Preservation (Critical)**
- [ ] All numerical values present?
- [ ] All identifiers/codes present?
- [ ] All named entities present?
- [ ] All causal relationships captured?
- [ ] All decision points captured?
- [ ] Sequence order maintained?

**Quality**
- [ ] Every sentence under 10 words?
- [ ] No passive voice?
- [ ] No filler phrases?
- [ ] No hedging language?
- [ ] Simplest representation used?
- [ ] No repeated information?
- [ ] References used instead of duplication?
- [ ] All edge cases covered?

**Readability**
- [ ] Readable without original document?
- [ ] No ambiguity introduced?
- [ ] Notation used consistently?
- [ ] Compression ratio 40-60%?