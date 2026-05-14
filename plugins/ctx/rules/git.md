# Rule: git

- **Never** force push without an explicit user request.
- **Never** run `git commit --amend` on already-pushed commits. Use a new commit instead.
- **Never** skip pre-commit hooks (`--no-verify`) without a request.
- Do not run `git reset --hard` or `git checkout --` without confirmation — these are destructive.
- One logical change = one commit. Do not mix refactoring with feature work.
- Commit message explains **why**, not **what** (the code shows what).
- If a pre-commit hook fails: fix the problem, re-stage, make a **new** commit (not amend).

## Worktree placement

- Create git worktrees **inside `<repo>/.worktrees/<branch-name>/`**, not in a global directory like `~/.config/superpowers/worktrees/`.
- Always add `.worktrees/` to the repo's `.gitignore` (once) — otherwise git will try to include it in the index.
- If the repo already has a worktree path convention, follow it instead of creating a parallel one.
- The global path `~/.config/superpowers/worktrees/` must **not** be used (breaks repo locality, complicates cleanup, hides state from the user).
- When cleaning up merged branches: `git worktree remove <path>` + `git branch -d <branch>` for tidiness.
