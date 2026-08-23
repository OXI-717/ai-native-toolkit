---
allowed-tools: Read, Write, Edit, MultiEdit, Bash, LS, Glob, Grep
argument-hint: "[workspace path or project path]"
description: Interview the user and generate a personal/team Claude Code context structure
---

Use the `setup-my-context` skill to help the user build their own agent
working structure. If an argument is provided, treat it as the target workspace
or project path. Otherwise, ask for the target path before writing files.

Input argument:

```text
$ARGUMENTS
```

Do not copy someone else's private project structure. Generate only the files
and rules that fit the user's answers.
