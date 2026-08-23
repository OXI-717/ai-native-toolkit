# Bootstrap Interview

Use this interview to produce the smallest useful structure. Ask fewer questions
when the answer is visible from existing files.

## Questions

1. What is the target path?
2. Is this for one person, a small team, or a larger organization?
3. What kind of work happens here: code, documents, research, sales, operations,
   support, or mixed?
4. What tools are already authoritative: GitHub, Jira, Linear, Obsidian, Slack,
   Telegram, Google Drive, local files, or something else?
5. What must never be done without approval: deploy, spend money, message
   clients, modify production data, merge PRs, delete files, rotate credentials?
6. Where do secrets live, and what names may agents see?
7. What are three repeated mistakes or tedious prompts from recent work?
8. What does "done" mean for a typical task?
9. Who reviews or accepts work?
10. How much automation is comfortable now: L0-L2 context only, L3 delegation, or
    L4 PR pipeline?

## Autonomous Defaults

When the user explicitly says to proceed independently:

- choose L0-L2 as the minimum;
- continue to L3-L4 only for a code repository or a clearly technical workflow;
- use conservative rules: no production mutation, no secret exposure, no merge
  without acceptance;
- create drafts instead of overwriting existing context files;
- preserve local conventions discovered in the target directory.

## Output

Produce a short "context setup brief" before writing files:

```markdown
## Context Setup Brief

Target: <path>
Audience: <person/team>
Work type: <code/docs/research/mixed>
Target level: L<N>
Authoritative systems: <tools>
No-approval boundaries: <actions>
Files to create/change:
- <path>
```

If the user approves or has already delegated authority, write the files and then
report completed levels.
