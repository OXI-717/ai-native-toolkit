---
name: review
description: |
  Use when the user invokes /review, asks for the team-based
  code review workflow, wants multi-pass or multi-agent review, or wants the
  review → fix → build/test → re-review loop. Supports Codex by using native
  subagents only when the user explicitly asks for the multi-agent/team review
  behavior; otherwise run the same reviewer roles locally.
---

# Review

Run a rigorous code review with optional fix loop:

1. Detect scope.
2. Review through specialized roles.
3. Score and filter findings.
4. Optionally fix.
5. Build/test.
6. Re-review fixed changes.
7. Report remaining risks.

## Runtime Selection

**Claude Code:** if the slash command `/review` is available, it remains the
canonical implementation — follow `${CLAUDE_PLUGIN_ROOT}/commands/review.md`.
If the command file is not found, fall back to this SKILL.md.

**Codex or portable skill mode:** follow this SKILL.md. If the user explicitly
asked for `review`, `/review`, "multi-agent review", "team review",
or equivalent delegation, use Codex subagents for independent
review roles when available. For a plain "review this" request, do not delegate;
run the roles as local passes in this session.

Do not use Claude-only TeamCreate/TaskCreate/AskUserQuestion APIs in Codex.

## Arguments

Accept the same user-facing shape as `/review`:

```text
review [scope] [aspects] [--ask] [--no-fix]
```

Scopes:

- `staged`: `git diff --cached`
- `unstaged`: `git diff`
- `all`: `git diff HEAD`
- `unpushed`: `git diff @{u}..HEAD`
- `last` or `HEAD`: `git diff HEAD~1..HEAD`
- `pr` or `pr N`: `gh pr diff [N]`
- `full` or `project`: whole-codebase review
- paths: review only those files/directories

Auto-detect scope when omitted:

1. unstaged changes
2. staged changes
3. unpushed commits
4. last commit
5. current PR
6. otherwise stop with "No changes to review."

Aspects:

- `code`: conventions, maintainability, local instructions
- `bugs`: logic bugs, regressions, security-adjacent defects
- `tests`: missing behavioral coverage
- `errors`: swallowed errors, weak error handling, silent failure
- `simplify`: needless complexity and duplication
- `adversarial`: design challenges, hidden assumptions, tradeoffs
- `all`: all except `adversarial`

## Review Workflow

First gather:

- relevant diff or file list
- changed file paths
- latest commit context
- root and nested `AGENTS.md` / `CLAUDE.md` instructions when present
- test/build hints from package manager files, Makefile, `pyproject.toml`,
  `go.mod`, `Cargo.toml`, etc.

Then run selected reviewer roles. Use parallel subagents only when explicitly
permitted by the user request; otherwise run each role locally. Each role reports
only actionable findings with confidence >= 80.

Reviewer role prompts:

- **code-reviewer:** local instructions, conventions, readability, API contract
  consistency.
- **bug-hunter:** incorrect behavior, edge cases, state/race issues, security
  defects, migration/data-loss risks.
- **test-analyzer:** missing or weak tests for changed behavior and regression
  risk.
- **error-auditor:** swallowed exceptions, ignored return values, bad retries,
  logs that hide failures.
- **simplifier:** remove complexity only when it clearly reduces risk or code
  surface. Do not report style preferences.
- **adversarial-reviewer:** challenge assumptions. Categorize as CHALLENGE,
  RISK, or SMELL.

For delegated review, assign concrete non-overlapping roles. Ask subagents for
findings only, not edits. Continue local work while they run, then aggregate.

## Finding Standard

Keep only findings that are:

- actionable
- tied to a file/line or specific code path
- likely enough to matter (confidence >= 80)
- not just style, preference, missing comments, or speculative cleanup

Output findings first, ordered by severity:

```markdown
## Critical Issues
1. **[role]** Description — `file:line` — confidence N — fix: ...

## Important Issues
1. **[role]** Description — `file:line` — confidence N — fix: ...

## Tests
- Missing coverage or test command gaps.

## Adversarial Challenges
- **[CHALLENGE/RISK/SMELL]** Description — `file:line` — confidence N.
```

If no findings survive filtering, say the reviewed scope looks clean and mention
test gaps or commands not run.

## Fix Loop

Default behavior: fix findings unless the user passed `--no-fix` or the scope is
a remote PR that should stay read-only. If `--ask` is present, ask before editing.

When fixing:

1. Apply minimal targeted edits only for accepted findings.
2. Do not refactor beyond the issue.
3. Run the most relevant build/test commands.
4. Re-review only the files changed by fixes.
5. Repeat up to 3 total fix/re-review loops.

If a finding cannot be fixed safely in the loop, leave it as remaining risk and
explain why. Create GitHub issues only when the repository has `gh` configured
and the user asked for issue creation or the original `/review` workflow clearly
requires it.

## Full Project Mode

For `full` / `project`, map the codebase before reviewing:

```bash
find . -type f \( -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.jsx" -o -name "*.py" -o -name "*.go" -o -name "*.rs" -o -name "*.java" -o -name "*.sh" -o -name "*.yml" -o -name "*.yaml" -o -name "*.json" -o -name "*.toml" \) \
  -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/dist/*" -not -path "*/build/*" -not -path "*/.next/*" -not -path "*/vendor/*" -not -path "*/__pycache__/*" -not -path "*/target/*" \
  | head -500
```

Split by directory/domain. Review chunks plus cross-cutting architecture and
security concerns. Keep output focused on real defects, architectural risks, and
high-value simplifications.

## Build/Test Detection

Prefer existing project commands. Detect conservatively:

- `package.json`: `npm test`, `npm run build` when scripts exist
- `Makefile`: `make test`, `make build`, or `make`
- `pyproject.toml` / `setup.py`: `python -m pytest` or repo command
- `go.mod`: `go test ./...`, `go build ./...`
- `Cargo.toml`: `cargo test`, `cargo build`
- `CMakeLists.txt`: configured build/test commands if present
- nginx configs: `nginx -t` when available

If commands are missing or unsafe for the current environment, say so.

## Final Report

End with:

- reviewed scope
- issues fixed and remaining
- build/test results
- files modified
- verdict: `CLEAN`, `ISSUES REMAINING`, or `NEEDS MANUAL REVIEW`

Keep the report concise. Findings and residual risk matter more than describing
the process.
