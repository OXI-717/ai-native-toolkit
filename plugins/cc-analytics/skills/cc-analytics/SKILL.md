---
name: cc-analytics
description: Use when user asks for Claude Code usage stats, weekly analytics, project activity summary, or wants to see what projects were worked on. Triggers on "аналитика", "статистика claude", "cc stats", "weekly report", "что делал"
---

# Claude Code Analytics

Generate HTML report of Claude Code usage from `~/.claude/history.jsonl`.

## Data Sources

- **History:** `~/.claude/history.jsonl` — prompts with timestamps and project paths
- **Git:** Remote URLs and commit counts per project

## Output

Single HTML file with terminal aesthetic:
- ASCII art header
- Summary stats (projects, prompts, commits, days)
- Project table with remote links
- ASCII bar chart

## Generation Script

Run this Python script to generate the report:

```python
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from collections import defaultdict

SKIP_DIRS = {'node_modules', '.venv', 'venv', '.worktrees', 'dist', 'build', '__pycache__'}

def _repo_stats(path, week_ago):
    """remote + commit count for a single git repo."""
    try:
        result = subprocess.run(['git', '-C', path, 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=5)
        remote = result.stdout.strip() if result.returncode == 0 else None
        if remote:
            remote = remote.replace('git@github.com:', 'github.com/').replace('.git', '').replace('https://', '')

        result = subprocess.run(['git', '-C', path, 'rev-list', '--count', f'--since={week_ago}', 'HEAD'],
                                capture_output=True, text=True, timeout=5)
        commits = int(result.stdout.strip()) if result.returncode == 0 else 0
        return remote, commits
    except Exception:
        return None, 0

def _nested_repos(path, depth=2):
    """Git repos below a container directory (the project dir itself is not a repo)."""
    found = []
    def walk(cur, level):
        if level > depth:
            return
        try:
            entries = os.listdir(cur)
        except OSError:
            return
        for name in entries:
            if name.startswith('.') or name in SKIP_DIRS:
                continue
            sub = os.path.join(cur, name)
            if not os.path.isdir(sub) or os.path.islink(sub):
                continue
            if os.path.exists(os.path.join(sub, '.git')):
                found.append(sub)      # a repo's own subdirs are not scanned further
            else:
                walk(sub, level + 1)
    walk(path, 1)
    return found

def assign_repos(projects):
    """Map each container project to the repos it alone accounts for.

    Containers nest (a work root holds a per-client folder, which holds the repos), so the same
    repo is reachable from several projects and its commits would be counted once per
    project. A repo belongs to the deepest container that sees it, and to no one if it is
    a project in its own right — that project reports it directly.
    """
    own_repos = {os.path.realpath(p) for p in projects
                 if os.path.exists(os.path.join(p, '.git'))}
    owner = {}                             # repo realpath -> owning container
    for project in projects:
        if os.path.realpath(project) in own_repos:
            continue
        for repo in _nested_repos(project):
            key = os.path.realpath(repo)
            if key in own_repos:
                continue
            current = owner.get(key)
            if current is None or len(project) > len(current):
                owner[key] = project
    assigned = defaultdict(list)
    for repo, project in owner.items():
        assigned[project].append(repo)
    return assigned

def get_git_info(path, container_repos=()):
    """Commit stats for a project dir.

    A project dir is often a container (several repos side by side) rather than a repo
    itself — working from it used to report zero commits and hide the week's real work.
    For containers, sum the commits of the repos assigned to it by `assign_repos`.
    """
    if not os.path.isdir(path):
        return None, 0
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

    if os.path.exists(os.path.join(path, '.git')):
        return _repo_stats(path, week_ago)

    repos = list(container_repos)
    if not repos:
        return None, 0
    # Two git calls per repo, all I/O-bound: sequential scanning of a large container
    # takes minutes, threads bring it back to seconds.
    total = 0
    busiest = (None, -1)               # remote of the repo with the most commits
    with ThreadPoolExecutor(max_workers=16) as pool:
        for remote, commits in pool.map(lambda r: _repo_stats(r, week_ago), repos):
            total += commits
            if commits > busiest[1]:
                busiest = (remote, commits)
    return busiest[0], total

# Parse history
history = []
with open(os.path.expanduser('~/.claude/history.jsonl'), 'r') as f:
    for line in f:
        try:
            history.append(json.loads(line))
        except:
            pass

# Filter last N days (default 7)
days = 7
now = datetime.now()
cutoff = (now - timedelta(days=days)).timestamp() * 1000

projects = defaultdict(lambda: {'prompts': [], 'sessions': set()})
for entry in history:
    ts = entry.get('timestamp', 0)
    if ts >= cutoff:
        project = entry.get('project', 'unknown')
        projects[project]['prompts'].append(entry)
        projects[project]['sessions'].add(datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d'))

# Collect data
home = os.path.expanduser('~')

def short_path(path):
    """Render an absolute project path relative to the current user's home."""
    return '~' + path[len(home):] if path == home or path.startswith(home + os.sep) else path

results = []
total_commits = 0
container_repos = assign_repos(projects)
for project, data in projects.items():
    remote, commits = get_git_info(project, container_repos.get(project, ()))
    total_commits += commits
    results.append({
        'name': os.path.basename(project) or short_path(project),
        'folder': short_path(project),
        'remote': remote,
        'prompts': len(data['prompts']),
        'sessions': len(data['sessions']),
        'commits': commits
    })

results.sort(key=lambda x: -x['prompts'])
max_prompts = results[0]['prompts'] if results else 1
```

## HTML Template

Use terminal aesthetic with:
- Monospace system fonts: `'SF Mono', 'Monaco', 'Inconsolata', monospace`
- Dark background: `#0d0d0d`
- Muted colors: `#b0b0b0` (text), `#555` (dim), `#4ec9b0` (cyan), `#ce9178` (orange)
- ASCII box-drawing for header
- `$ command --flags` style section headers
- ASCII bar chart using `█` characters

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>claude-analytics</title>
  <style>
    body {
      font-family: 'SF Mono', 'Monaco', 'Inconsolata', monospace;
      background: #0d0d0d;
      color: #b0b0b0;
      font-size: 14px;
      line-height: 1.6;
      padding: 24px;
    }
    .container { max-width: 900px; margin: 0 auto; }
    .header { color: #6a9955; margin-bottom: 24px; }
    .dim { color: #555; }
    .bright { color: #e0e0e0; }
    .cyan { color: #4ec9b0; }
    .orange { color: #ce9178; }
    .row {
      display: grid;
      grid-template-columns: 24px 200px 1fr 80px 80px 60px;
      gap: 8px;
      padding: 6px 0;
      border-bottom: 1px solid #1a1a1a;
    }
    .row:hover { background: #141414; }
    a { color: #555; text-decoration: none; }
    a:hover { color: #888; }
    .stat-box { display: inline-block; margin-right: 32px; }
    .stat-value { font-size: 28px; color: #e0e0e0; }
    .stat-label { color: #555; font-size: 12px; }
  </style>
</head>
<body>
  <div class="container">
    <pre class="header">
┌─────────────────────────────────────────────────────────────────┐
│   ██████╗██╗      █████╗ ██╗   ██╗██████╗ ███████╗              │
│  ██╔════╝██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝              │
│  ██║     ██║     ███████║██║   ██║██║  ██║█████╗                │
│  ██║     ██║     ██╔══██║██║   ██║██║  ██║██╔══╝                │
│  ╚██████╗███████╗██║  ██║╚██████╔╝██████╔╝███████╗              │
│   ╚═════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝              │
│   Weekly Analytics Report                                       │
│   {start_date} .. {end_date}                                    │
└─────────────────────────────────────────────────────────────────┘
</pre>
    <!-- Stats, table, chart sections -->
  </div>
</body>
</html>
```

## Bar Chart Generation

```python
def make_bar(value, max_val, width=40):
    filled = int((value / max_val) * width)
    return '█' * filled

# Example output:
# cohorts          ████████████████████████████████████████ 194
# ai-whisper       █████████████████████████████████████▋ 183
```

## Usage

1. User asks for analytics: "покажи статистику cc", "weekly report", "что делал за неделю"
2. Run Python script to collect data
3. Generate HTML with template
4. Save to `~/claude-analytics.html`
5. Open in browser: `open ~/claude-analytics.html`

## Customization

- **Period:** Change `days = 7` to desired range
- **Output path:** Change save location
- **Colors:** Adjust CSS variables
- **Columns:** Add/remove metrics in grid
