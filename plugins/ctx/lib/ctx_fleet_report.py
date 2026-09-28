"""Atomic daily fleet reports and serialized change-only delivery."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
from datetime import date


def atomic(path, text):
    fd, temp = tempfile.mkstemp(prefix='.ctx-fleet-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def valid(report):
    if not isinstance(report, dict) or report.get('schema_version') != 1 or not isinstance(report.get('issues'), list):
        raise ValueError('Invalid fleet baseline schema; refusing to reset alert history')
    if any(not isinstance(i, dict) or not isinstance(i.get('id'), str) for i in report['issues']):
        raise ValueError('Invalid finding identity in baseline')
    return report


def safe(text):
    return str(text).replace('\n', ' ').replace('\r', ' ').replace('`', "'")


def markdown(report):
    lines = ['# ctx-lint fleet', '',
             f"Context roots (including subprojects): {len(report['projects'])}; findings: {len(report['issues'])}.", '',
             'Static audit. Dynamic command hooks are not executed; their output is unknown.',
             'Unavailable host sources are listed explicitly. Token counts are estimates.', '',
             '## Coverage', '']
    for row in report['coverage']:
        lines.append(f"- {safe(row['project'])}: `{safe(row['path'])}` — {safe(row['status'])}")
    lines.extend(['', '## Measurements', ''])
    for project in report['projects']:
        info = project.get('info', {})
        lines.append(f"- `{safe(project['path'])}`: AGENTS {json.dumps(info.get('agents_budget', {}))}; Codex metadata {json.dumps(info.get('metadata', {}))}")
    for hook in report.get('hooks', []):
        lines.append(f"- `{safe(hook['path'])}` {hook['event']}: static prompt {hook['prompt_chars']} chars; dynamic outputs unknown: {hook['dynamic_hooks']}")
    lines.extend(['', '## Findings', ''])
    for finding in report['issues']:
        lines.append(f"- **{safe(finding['type'])}** · `{safe(finding['project'])}` · {safe(finding.get('description', ''))} (`{finding['id']}`)")
    return '\n'.join(lines) + '\n'


def publish(report, directory: Path, day: str, send=None):
    date.fromisoformat(day)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        report = valid(report() if callable(report) else report)
        latest = directory / 'latest.json'
        previous = valid(json.loads(latest.read_text(encoding='utf-8'))) if latest.exists() else None
        # Missing state amid existing reports is corruption, not a fresh baseline.
        if previous is None and list(directory.glob('????-??-??.json')):
            raise ValueError('latest.json missing while daily reports exist')
        old = {i['id'] for i in previous['issues']} if previous else set()
        new = [i for i in report['issues'] if i['id'] not in old] if previous else []
        path = directory / f'{day}.md'
        text = markdown(report)
        text += f"\nBaseline: {previous is None}; new findings: {len(new)}.\n"
        atomic(path, text)
        atomic(directory / f'{day}.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        if new and send is not None:
            # Compact bounded message; the full report remains on disk.
            message = f'# ctx-lint: новые нарушения ({len(new)})\n\n'
            message += '\n'.join(f"• {safe(i['type'])}: {safe(i['project'])[:100]}" for i in new[:12])
            message += f'\n\nОтчёт: `{path}`'
            send(message)
        atomic(latest, json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        return dict(path=str(path), new=[i['id'] for i in new], baseline=previous is None,
                    notified=bool(new and send is not None))
