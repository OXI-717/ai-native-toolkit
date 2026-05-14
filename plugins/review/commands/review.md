---
description: "Teams-based code review with fix loop: Review → Fix → Build/Test → Re-Review"
argument-hint: "[scope] [aspects] [--ask] [--no-fix] — scope: staged|unstaged|last|pr|full|<files>. Default: auto-fix"
allowed-tools: ["Bash", "Glob", "Grep", "Read", "Edit", "Write", "Task", "TeamCreate", "TeamDelete", "TaskCreate", "TaskUpdate", "TaskList", "SendMessage", "AskUserQuestion"]
---

# Teams-based Code Review with Fix Loop

Run a comprehensive code review, automatically fix all issues found, verify fixes with build/test, and re-review until clean. Fully autonomous by default — no user confirmation needed.

## Full Cycle

```
┌─────────────────────────────────────────────────────┐
│  MULTI-REPO: if changes in multiple repos detected, │
│  iterate each repo through PHASE 1-5 autonomously   │
├─────────────────────────────────────────────────────┤
│  PHASE 1: REVIEW                                    │
│  Parse scope → Gather diff → Spawn review agents    │
│  → Confidence scoring → Report                      │
├─────────────────────────────────────────────────────┤
│  PHASE 2: FIX  (if issues found)                    │
│  Ask user → Spawn fixer agent → Apply fixes         │
├─────────────────────────────────────────────────────┤
│  PHASE 3: BUILD & TEST                              │
│  Detect build system → Run build → Run tests        │
├─────────────────────────────────────────────────────┤
│  PHASE 4: RE-REVIEW                                 │
│  Quick review of fixes only → Verify no regressions │
│  → If new issues → back to PHASE 2 (max 3 loops)   │
├─────────────────────────────────────────────────────┤
│  PHASE 5: FINAL REPORT & CLEANUP                    │
│  Summary of all iterations → Shutdown team          │
└─────────────────────────────────────────────────────┘
```

## Arguments

**Input:** $ARGUMENTS

**Flags:**
- `--ask` — Ask user after review whether to fix (interactive choice)
- `--no-fix` — Review only, no fix loop (report and stop)
- Default (no flag): auto-fix all found issues without asking

---

# AUTONOMOUS MULTI-REPO BEHAVIOR

**This is NOT a separate phase — it's a behavioral rule for the agent.**

When during scope detection you discover changes span multiple repositories (e.g. you checked git status and found unpushed commits in several sibling repos), you MUST:

1. **Never ask the user which repo to review** — review ALL of them automatically
2. **Never say "too many changes, pick one"** — handle the volume yourself
3. **Sort repos by change count** (ascending) and iterate from smallest to largest
4. **Run the full PHASE 1 → PHASE 5 cycle independently for each repo**
5. **Report progress** as you go: `"--- Reviewing [repo-name] ([N changes]) ---"`

After all repos are done, present a combined summary:

```markdown
# Multi-Repo Review Summary

| Repo | Issues Found | Fixed | Remaining | Verdict |
|------|-------------|-------|-----------|---------|
| repo-a | 2 | 2 | 0 | CLEAN |
| repo-b | 5 | 4 | 1 | ISSUES REMAINING |
| repo-c | 12 | 10 | 2 | ISSUES REMAINING |

Total: 19 issues found, 16 fixed, 3 remaining
GitHub Issues created: [links]
```

**Scope per repo**: The auto-detect waterfall (Step 1) applies independently to each repo. Flags (`--ask`, `--no-fix`) and aspects apply uniformly to all repos.

---

# PHASE 1: REVIEW

## Step 1: Parse Arguments

### Scope

**Explicit scope** — if user specifies a keyword or path:

- **`staged`** — `git diff --cached`
- **`unstaged`** — `git diff`
- **`all`** — `git diff HEAD` (staged + unstaged)
- **`unpushed`** — `git diff @{u}..HEAD` (all unpushed commits)
- **`last`** or **`HEAD`** — `git diff HEAD~1..HEAD` (last commit)
- **`pr`** or **`pr #N`** — `gh pr diff [N]`
- **`full`** or **`project`** — **Full project review** (see FULL PROJECT MODE below)
- **`<file paths>`** — specific files/directories

**Auto-detect scope** — if NO explicit scope given, waterfall:

1. `git diff --name-only` → **if files exist** → unstaged
2. `git diff --cached --name-only` → **if files exist** → staged
3. `git rev-list --count @{u}..HEAD 2>/dev/null` → **if ahead > 0** → unpushed commits (`git diff @{u}..HEAD`)
4. `git log -1 --format="%s" 2>/dev/null` → **if commit exists** → last commit
5. `gh pr view --json state -q .state 2>/dev/null` → **if PR exists** → PR
6. **Nothing found** → "No changes to review" → stop

Always tell user which scope was auto-detected:
> "Auto-detected: reviewing unstaged changes (3 files modified)"
> "No local changes. Reviewing last commit: `feat(api): Add CORS headers`"

### Aspects

- **`code`** — code-reviewer (CLAUDE.md compliance, quality)
- **`bugs`** — bug-hunter (bugs, git history context)
- **`tests`** — test-analyzer (test coverage gaps)
- **`errors`** — error-auditor (silent failures, error handling)
- **`simplify`** — simplifier (code simplification)
- **`adversarial`** — adversarial-reviewer (challenges assumptions, tradeoffs, architectural decisions)
- **`all`** (default) — all agents except adversarial (must be explicitly requested)

Multiple: `/review staged code bugs errors`

## Step 2: Gather Changes

```bash
# Get the diff based on scope
# Also collect:
git log -1 --format="%H %s (%an, %ad)"   # commit context
```

- Find all relevant CLAUDE.md files (root + changed directories)
- Get changed file paths
- For last-commit mode: include commit message and metadata

## Step 3: Create Team

```
TeamCreate: team_name="code-review", description="Review + Fix cycle"
```

## Step 4: Create Tasks & Spawn Review Agents

For each applicable aspect, create a task (TaskCreate) and spawn a **Sonnet** agent in parallel:

| Agent | Focus |
|-------|-------|
| code-reviewer | CLAUDE.md compliance, conventions, quality |
| bug-hunter | Bugs, logic errors, security, git history |
| test-analyzer | Test coverage gaps, behavioral coverage |
| error-auditor | Silent failures, error handling |
| simplifier | Code clarity, redundancy |
| adversarial-reviewer | Challenges assumptions, tradeoffs, architectural decisions (only when `adversarial` aspect requested) |

Each agent:
- Reads its task with full diff + CLAUDE.md content
- Performs review with confidence scoring (0-100)
- Reports only issues with confidence >= 80
- Updates task as completed, sends results to team lead

### Adversarial Reviewer

Only spawned when `adversarial` aspect is explicitly requested. NOT included in `all`.

Unlike other agents that look for concrete defects, the adversarial-reviewer **challenges decisions**:

- **Wrong abstraction**: "This is used once — why is it a separate class/function?"
- **Missing failure modes**: "What happens when X times out / returns null / is 10x larger?"
- **Hidden coupling**: "A depends on B's internal behavior — intentional?"
- **Scalability traps**: "This is O(n²) — acceptable at current scale but what about growth?"
- **Over-engineering**: "3 layers of indirection for a CRUD endpoint?"
- **Under-engineering**: "No retry logic on an external API call that can fail?"
- **Assumption gaps**: "This assumes single-threaded execution — documented anywhere?"

Output format: same confidence scoring (0-100), but issues are categorized as:
- **CHALLENGE** (not a bug, but a design question worth answering)
- **RISK** (concrete risk from an unvalidated assumption)
- **SMELL** (architectural smell that may indicate deeper problems)

## Step 5: Collect & Score

1. Wait for all agents to report
2. For each issue found, launch parallel **Haiku** scorer agents:
   - Re-evaluate confidence 0-100
   - For CLAUDE.md issues: verify CLAUDE.md actually specifies the rule
   - Return score + justification
3. Filter: keep only issues with final score >= 80

## Step 6: Review Report

Present the review report:

```markdown
# Review Report (Iteration 1)

**Scope**: [scope] | **Files**: [count] | **Agents**: [list]

## Critical Issues (90-100)
1. **[agent]** description (score: N) — `file:line` — fix: ...

## Important Issues (80-89)
1. **[agent]** description (score: N) — `file:line` — fix: ...

## Simplification Suggestions
1. `file:lines` — suggestion

## Adversarial Challenges (if adversarial aspect active)
1. **[CHALLENGE/RISK/SMELL]** description (score: N) — `file:line` — question: ...

## Summary: X critical, Y important, Z filtered
```

**If no issues found** → report "Code looks clean" → skip to PHASE 5.

**If `--no-fix` flag** → report only → skip to PHASE 5.

---

# FULL PROJECT MODE

When scope is **`full`** or **`project`**, the review covers the **entire codebase**, not just a diff. This is a deeper, architectural review.

## How it differs from diff-based review

| Aspect | Diff review | Full project review |
|--------|-----------|-------------------|
| Input | git diff / changed lines | All source files |
| Focus | "Did this change introduce issues?" | "What issues exist in the codebase?" |
| Agents | 5 standard agents | 5 standard + architecture agent |
| Output | Line-level issues | File-level + architectural issues |
| Fix scope | Only changed code | Any file in project |

## Step F1: Map the project

1. Read CLAUDE.md (root + any nested)
2. Discover project structure:
```bash
# Get all source files (exclude node_modules, .git, build artifacts, vendor)
find . -type f \( -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.jsx" -o -name "*.py" -o -name "*.go" -o -name "*.rs" -o -name "*.java" -o -name "*.c" -o -name "*.cpp" -o -name "*.h" -o -name "*.vue" -o -name "*.svelte" -o -name "*.rb" -o -name "*.php" -o -name "*.cs" -o -name "*.swift" -o -name "*.kt" -o -name "*.conf" -o -name "*.yaml" -o -name "*.yml" -o -name "*.toml" -o -name "*.json" -o -name "*.sh" \) \
  -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/dist/*" -not -path "*/build/*" -not -path "*/.next/*" -not -path "*/vendor/*" -not -path "*/__pycache__/*" -not -path "*/target/*" \
  | head -500
```
3. Count files per directory, identify main source dirs
4. Detect tech stack from package.json / go.mod / Cargo.toml / etc.
5. Report to user: "Project: N source files, tech stack: [X], main dirs: [Y]"

## Step F2: Split work by directory/domain

Divide the project files into **logical chunks** for agents. Split by:

1. **Directory boundaries** — each top-level src dir is a chunk
2. **File count balance** — no chunk should have more than ~30 files
3. **Domain cohesion** — keep related files together (e.g., component + its test + its types)

Example split for a Next.js app:
```
Chunk 1: src/app/ (routes, layouts, pages)
Chunk 2: src/components/ (UI components)
Chunk 3: src/lib/ + src/utils/ + src/types/ (shared code)
Chunk 4: src/api/ or api routes (backend)
Chunk 5: config files (package.json, tsconfig, tailwind, etc.)
```

## Step F3: Create Tasks & Spawn Agents

For full project mode, spawn **more agents** to cover the codebase:

**Per-chunk agents** (1 per chunk, Sonnet):
- Each gets assigned a chunk of files to review
- Reviews for: bugs, error handling, code quality, CLAUDE.md compliance, security
- Each agent reads ALL files in its chunk (not just diffs)

**Cross-cutting agents** (always spawned, Sonnet):
- **architecture-reviewer**: Reviews overall project structure, dependency graph, separation of concerns, module boundaries, circular dependencies, dead code
- **security-scanner**: Scans entire project for: hardcoded secrets, injection vulnerabilities, auth bypasses, CORS issues, insecure dependencies, exposed endpoints

**Architecture reviewer prompt** should check:
- Are modules/directories well-organized?
- Are there circular dependencies?
- Is there dead code (unused exports, unreachable functions)?
- Are concerns properly separated (UI vs logic vs data)?
- Are there god files/functions (too large, too many responsibilities)?
- Is error handling consistent across the project?
- Are there inconsistent patterns (doing the same thing differently in different places)?
- Is the dependency tree clean (no unnecessary deps, no outdated deps)?

**Security scanner prompt** should check:
- Hardcoded API keys, tokens, passwords, secrets in source code
- SQL/NoSQL injection vectors
- XSS vulnerabilities (unescaped user input in templates)
- Auth/authz bypasses (missing middleware, unchecked permissions)
- CORS misconfigurations
- Insecure HTTP usage (should be HTTPS)
- Exposed debug endpoints or admin routes
- Dependency vulnerabilities: `npm audit` / `pip audit` / `cargo audit`
- Sensitive data in logs
- Missing rate limiting on public endpoints

## Step F4: Aggregate Results

Same as standard review: collect all issues, run Haiku confidence scoring, filter >= 80.

**Additional output sections for full project mode:**

```markdown
## Architecture Assessment
- Structure: [good/needs improvement/poor]
- Observations: [findings from architecture-reviewer]
- Recommendations: [suggestions]

## Security Assessment
- Risk level: [low/medium/high/critical]
- Findings: [from security-scanner]
- Dependency audit: [npm audit / pip audit results]

## Code Health Metrics
- Total files reviewed: N
- Issues per file (density): X
- Most problematic areas: [dirs/files with most issues]
- Cleanest areas: [dirs/files with no issues]
```

## Step F5: Continue to Fix loop

After the full project report, the standard PHASE 2-5 flow applies:
- Ask user which issues to fix
- Fix → Build → Test → Re-Review
- Same max 3 iteration loop

**Note**: For full project mode, auto-fix applies to ALL found issues — no confirmation needed. Use `--ask` only if you explicitly want interactive control, or `--no-fix` for report only.

---

# PHASE 2: FIX

## Step 7: Confirm Fixes

**If `--ask` flag**: Ask the user what to do with AskUserQuestion (see below).

**Default (no flag)**: proceed directly to fixing all issues.

**AskUserQuestion prompt (only when `--ask`):**

> "Found X issues. What would you like to do?"
> - **Fix all** — Fix all Critical and Important issues
> - **Fix critical only** — Fix only Critical (90-100) issues
> - **Pick issues** — Let me choose which to fix (then list issues with checkboxes)
> - **Skip fixes** — Just show the report, don't fix anything → skip to PHASE 5

## Step 8: Apply Fixes

Spawn a **fixer** agent (Sonnet, general-purpose) as a teammate:

- Give it the full list of issues to fix with file paths, line numbers, descriptions, and suggested fixes
- Give it CLAUDE.md content for project conventions
- The fixer reads each file, applies minimal targeted fixes, reports what it changed
- **The fixer does NOT refactor or improve beyond the specific issues**

After fixer completes, collect the list of:
- Files modified
- Changes applied per issue
- Risk assessment per fix (LOW/MEDIUM/HIGH)

---

# PHASE 3: BUILD & TEST

## Step 9: Detect Build System

Auto-detect the project's build/test commands by checking (in order):

```bash
# Check what exists in the project root
ls package.json Makefile Dockerfile docker-compose.yml Cargo.toml go.mod pyproject.toml setup.py CMakeLists.txt 2>/dev/null
```

**Detection rules:**

| File Found | Build Command | Test Command |
|-----------|---------------|--------------|
| `package.json` | `npm run build` (if build script exists) | `npm test` (if test script exists) |
| `Makefile` | `make` or `make build` | `make test` |
| `Cargo.toml` | `cargo build` | `cargo test` |
| `go.mod` | `go build ./...` | `go test ./...` |
| `pyproject.toml` | — | `pytest` or `python -m pytest` |
| `setup.py` | — | `python -m pytest` |
| `docker-compose.yml` | `docker-compose build` | — |
| `Dockerfile` | `docker build .` | — |
| `CMakeLists.txt` | `cmake --build build` | `ctest --test-dir build` |
| `nginx.conf` or `*.conf` in nginx dir | `nginx -t` (config test) | — |

If multiple build systems found, use the most relevant one for the changed files.

**For nginx configs**: always run `nginx -t` if available (even inside docker):
```bash
docker exec <nginx-container> nginx -t 2>&1 || nginx -t 2>&1
```

## Step 10: Run Build

Run the detected build command. Capture output.

- **If build succeeds** → continue to tests
- **If build fails** → report build failure → go back to PHASE 2 with build errors as new issues (counts as 1 loop iteration)

## Step 11: Run Tests

Run the detected test command. Capture output.

- **If tests pass** → continue to PHASE 4
- **If tests fail** → report test failures → go back to PHASE 2 with test failures as new issues (counts as 1 loop iteration)
- **If no tests detected** → skip, continue to PHASE 4 with warning

---

# PHASE 4: RE-REVIEW

## Step 12: Quick Re-Review

Run a **focused re-review** on only the files modified by the fixer. This is a lighter review:

- Spawn 2 agents in parallel (Sonnet):
  - **code-reviewer**: check fixes are correct and follow conventions
  - **bug-hunter**: check fixes don't introduce new bugs

- Scope: `git diff` of just the fixed files (compare against state before fixes)

## Step 13: Evaluate Re-Review

- **If no new issues found** → fixes are clean → proceed to PHASE 5
- **If new issues found** → increment loop counter
  - **If loop counter < 3** → go back to PHASE 2 with new issues
  - **If loop counter >= 3** → stop, report remaining issues, proceed to PHASE 5 with warning

---

# PHASE 5: FINAL REPORT & CLEANUP

## Step 14: Final Report

```markdown
# Final Code Review Report

**Project**: [project path]
**Scope**: [original scope]
**Iterations**: [N]

## Issues Found & Fixed

| # | Issue | Agent | Score | Status | File |
|---|-------|-------|-------|--------|------|
| 1 | [desc] | bug-hunter | 95 | FIXED | `file:line` |
| 2 | [desc] | code-reviewer | 88 | FIXED | `file:line` |
| 3 | [desc] | error-auditor | 82 | SKIPPED | `file:line` |

## Build & Test Results
- Build: PASS/FAIL
- Tests: PASS/FAIL/SKIPPED (N tests, M passed, K failed)

## Files Modified
- `path/to/file1.py` (2 fixes applied)
- `path/to/file2.js` (1 fix applied)

## Remaining Issues (if any)
- [issues that couldn't be fixed after 3 iterations]
- GitHub Issues: [links to created issues]

## Verdict
[CLEAN / ISSUES REMAINING / NEEDS MANUAL REVIEW]
```

## Step 15: Create GitHub Issues for Unfixed Problems

**If there are ANY remaining (unfixed/skipped) issues** → automatically create GitHub issues for each one.

For each unfixed issue, run:

```bash
gh issue create \
  --title "[auto-review] <short description> in <file>" \
  --label "auto-review" \
  --body "$(cat <<'EOF'
## Auto-Review Finding

**Source**: automated code review (`/review`)
**Agent**: <agent-name> (confidence: <score>)
**File**: `<file:line>`
**Severity**: <CRITICAL/IMPORTANT>

## Description

<detailed description of the issue>

## Suggested Fix

<suggested fix from the review agent>

## Why Not Auto-Fixed

<reason: max iterations reached / complex refactor needed / fix introduced regressions>

---
*Created automatically by code review. Label: `auto-review`*
EOF
)"
```

**Before creating issues:**
1. Check if label `auto-review` exists: `gh label list --search "auto-review"`. If not — create it:
   ```bash
   gh label create "auto-review" --description "Issues found by automated code review" --color "D93F0B"
   ```
2. Group related issues in the same file into a single GitHub issue (don't create 5 issues for the same file)

**Add issue links to the final report** in the "Remaining Issues" section.

**If ALL issues were fixed** → skip this step.

## Step 16: Cleanup

1. Shut down all teammate agents via SendMessage(type: "shutdown_request")
2. Wait briefly for shutdowns
3. Delete the team via TeamDelete
4. Report created GitHub issue links to user

---

# NOTES

- **Max loop iterations**: 3 (review → fix → re-review cycles). After 3 loops, stop and report remaining issues.
- **Fully autonomous**: Auto-fix ALL issues without asking. Use `--ask` only for explicit interactive control. Never apply fixes to PR scope (read-only).
- **Multi-repo**: If you detect changes in multiple repos during scope analysis, iterate ALL of them autonomously — smallest first. NEVER ask user which repo to review, NEVER say "too many changes". Just do them all.
- **Unpushed commits**: Auto-detected via `git rev-list @{u}..HEAD`. Common in multi-repo workflows where commits exist but haven't been pushed.
- **Build/test detection**: If no build system is found, skip build/test phase with a note.
- **Agent model mix**: Sonnet for analysis and fixing, Haiku for confidence scoring.
- **Read-only scopes**: For `pr` and `last` scope, fixes are applied to local working copy. The user decides whether to commit.
- Use `gh` for all GitHub interactions
- Always read CLAUDE.md files first
- Fixer agent uses Edit tool, not Write — minimal changes only

---

# EXAMPLES

```
/review                          # Auto-detect scope, all agents, auto-fix (default)
/review --ask                    # Auto-detect scope, all agents, ask before fixing
/review --no-fix                 # Auto-detect scope, all agents, report only
/review staged                   # Staged changes, all agents, auto-fix
/review staged code bugs         # Staged, only code-reviewer + bug-hunter, auto-fix
/review last                     # Last commit, auto-fix
/review last --ask               # Last commit, ask before fixing
/review pr                       # Current PR, all agents (read-only, no fix)
/review pr 42                    # PR #42
/review full                     # Full project review — all files, architecture + security
/review full --no-fix            # Full project review, report only
/review project bugs errors      # Full project, only bug-hunter + error-auditor
/review src/api/ tests/          # Specific directories
/review src/app.py bugs errors   # Specific file, bug-hunter + error-auditor
/review all                      # Staged + unstaged, auto-fix everything
```

### Auto-detect flow:

```
/review
  ↓ git diff --name-only → files? → review unstaged
  ↓ git diff --cached --name-only → files? → review staged
  ↓ git rev-list @{u}..HEAD → ahead? → review unpushed commits
  ↓ git log -1 → commit? → review last commit
  ↓ gh pr view → PR? → review PR
  ↓ "No changes to review"

  * If multiple repos have changes → iterate ALL autonomously (see MULTI-REPO section)
```

### Fix loop:

```
Review → Issues found?
  ├─ No  → "Code is clean" → Done
  ├─ Yes → --no-fix? → Report only → Done
  └─ Yes → Fix → Build → Test → Re-Review
               ├─ Clean? → Done
               └─ New issues? → Loop (max 3x)
```
