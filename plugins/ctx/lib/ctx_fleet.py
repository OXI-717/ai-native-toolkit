"""Read-only BB fleet context audit. Never executes discovered hooks or models."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import yaml
import rule_audit

LIB = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('fleet_ctx_lint', LIB / 'ctx-lint.py')
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)
EXCLUDED = lint.EXCLUDE_DIR_NAMES | {
    '.worktrees', '.task-runner', '_workspaces', '.trash',
    'archive', '_archive', '_backups', '_legacy', '_handoff',
}


def linked_worktree(path):
    marker = path / '.git'
    if not marker.is_file():
        return False
    text = marker.read_text(encoding='utf-8').strip()
    if not text.startswith('gitdir: '):
        return False
    gitdir = path / text.removeprefix('gitdir: ')
    # Submodules also have a .git file; only linked worktrees have commondir.
    return (gitdir / 'commondir').is_file()


def source_exclusion(raw, resolved):
    if any(part in EXCLUDED for path in (Path(raw), resolved) for part in path.parts):
        return 'excluded_directory'
    if any(linked_worktree(path) for path in (resolved, *resolved.parents)):
        return 'linked_git_worktree'
    return None


def issue(kind, subject, description, severity='warn'):
    return dict(type=kind, subject=str(subject), description=description, severity=severity)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def discover(projects, host_id=None):
    if not isinstance(projects, list) or not projects:
        raise ValueError('BB inventory must be a nonempty project list')
    roots, coverage, scanned = set(), [], set()
    for project in projects:
        if not isinstance(project, dict) or not isinstance(project.get('sources'), list):
            raise ValueError('Invalid BB project/source schema')
        for source in project['sources']:
            if not isinstance(source, dict):
                raise ValueError('Invalid BB source')
            if source.get('type') != 'local_path':
                continue
            raw = source.get('path')
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise ValueError('BB local_path must be absolute')
            row = dict(project=project.get('id'), path=raw, host=source.get('hostId'))
            if source.get('hostId') and source['hostId'] != host_id:
                coverage.append(dict(row, status='unavailable', reason='different_or_unknown_host'))
                continue
            root = Path(raw).resolve()
            row['path'] = str(root)
            reason = source_exclusion(raw, root)
            if reason:
                coverage.append(dict(row, status='excluded', reason=reason))
                continue
            if not root.is_dir():
                coverage.append(dict(row, status='unavailable'))
                continue
            coverage.append(dict(row, status='scanned'))
            if root in scanned:
                continue
            scanned.add(root)
            count = 0
            def fail(exc):
                raise exc
            for directory, names, files in os.walk(root, followlinks=False, onerror=fail):
                if linked_worktree(Path(directory)):
                    names[:] = []
                    continue
                names[:] = sorted(n for n in names if n not in EXCLUDED and not Path(directory, n).is_symlink())
                count += 1
                if count > 100000:
                    raise ValueError(f'Directory scan limit exceeded: {root}')
                if 'AGENTS.md' in files:
                    roots.add(Path(directory).resolve())
    if not roots:
        raise ValueError('No accessible BB projects with AGENTS.md; refusing empty baseline')
    return sorted(roots), sorted(coverage, key=lambda r: (str(r['project']), r['path']))


def _check_project(root):
    report = lint.check_project(root)
    findings = report['issues']
    agents = root / 'AGENTS.md'
    fm = lint.parse_frontmatter(agents)
    limits, _ = lint._budget_limits(fm)
    files, truncated = lint.walk_import_tree(agents, return_truncated=True)
    body_chars, _ = lint._agents_body_sections(agents.read_text(encoding='utf-8'))
    if body_chars > limits['agents_body_chars'] and not any(i['type'] == 'agents_md_inline_heavy' for i in findings):
        findings.append(issue('agents_md_inline_heavy', agents, f"AGENTS own text {body_chars} chars > {limits['agents_body_chars']}"))
    tokens = sum(f['tokens'] for f in files)
    report['info']['agents_budget'] = dict(files=len(files), tokens=tokens, complete=not truncated)
    if truncated:
        findings.append(issue('agents_tree_incomplete', agents, 'AGENTS import tree exceeded traversal limit'))
    if tokens > limits['total_tokens']:
        findings.append(issue('agents_budget_exceeded', agents, f"AGENTS tree ~{tokens} tokens > {limits['total_tokens']}"))
    for file in files:
        path = Path(file['path'])
        text = path.read_text(encoding='utf-8')
        if file['chars'] > limits['per_file_chars']:
            findings.append(issue('agents_import_oversized', path, f"{file['chars']} chars > {limits['per_file_chars']}"))
        for raw in lint._import_targets(text):
            if path == agents.resolve() and any(i.get('raw') == '@' + raw and i['type'] in {'broken_import', 'env_var_in_import'} for i in findings):
                continue
            if '${' in raw:
                findings.append(issue('env_import_unresolved', f'{path}:{raw}', 'Environment variable import is not portable'))
                continue
            target = lint.resolve_import(raw, path.parent)
            if not target.is_file():
                findings.append(issue('broken_import', f'{path}:{raw}', f'Missing import {raw}', 'error'))
        # Inline Markdown local file links outside fences. URLs/anchors are not fetched.
        visible = unfenced(text)
        for raw in re.findall(r'\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\)', visible):
            raw = raw.strip('<>')
            parsed = urlsplit(raw)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = lint.resolve_import(unquote(parsed.path), path.parent)
            if not target.exists():
                findings.append(issue('broken_link', f'{path}:{raw}', f'Missing local link {raw}'))
    audit = rule_audit.audit_project(root)
    report['info']['rules'] = {k: audit[k] for k in ('shared_rules_dir', 'present_rule_count', 'unchanged_rule_count', 'status')}
    for changed in audit['rules']:
        findings.append(issue('shared_rule_drift', changed['project_path'], f"Copy differs from {changed['shared_path']}"))
    for error in audit['errors']:
        findings.append(issue('rule_audit_error', root / 'rules', error, 'error'))
    return report


def project_status(findings):
    return 'error' if any(i.get('severity') == 'error' for i in findings) else ('warn' if findings else 'ok')


def check_project(root):
    try:
        report = _check_project(root)
    except (OSError, ValueError, RuntimeError) as exc:
        report = dict(path=str(root), info={'complete': False}, issues=[
            issue('fleet_project_unreadable', root, f'{type(exc).__name__}: {exc}', 'error')])
    report['status'] = project_status(report['issues'])
    return report


def unfenced(text):
    visible, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
        else:
            visible.append(line)
    return '\n'.join(visible)


def check_hooks(paths):
    findings, info = [], []
    for path in sorted(set(paths)):
        try:
            hooks = read_json(path).get('hooks', {})
            if not isinstance(hooks, dict):
                raise ValueError('hooks must be an object')
            for event in ('SessionStart', 'UserPromptSubmit'):
                groups = hooks.get(event, [])
                if not isinstance(groups, list):
                    raise ValueError('hook event must be a list')
                chars, unknown = 0, 0
                for group in groups:
                    for hook in group['hooks']:
                        subject = f'{path}:{event}:' + hashlib.sha256(json.dumps(hook, sort_keys=True).encode()).hexdigest()[:16]
                        if hook.get('type') == 'prompt' and isinstance(hook.get('prompt'), str):
                            chars += len(hook['prompt'])
                        else:
                            unknown += 1
                            findings.append(issue('hook_output_unmeasured', subject, f'{event}: dynamic hook output cannot be measured statically; command was not executed'))
                if groups:
                    info.append(dict(path=str(path), event=event, prompt_chars=chars, dynamic_hooks=unknown))
                if chars > 4000:
                    findings.append(issue('hook_prompt_budget_exceeded', f'{path}:{event}', f'{event}: {chars} static prompt chars > 4000'))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            findings.append(issue('hooks_unreadable', path, f'Cannot audit hooks: {exc}', 'error'))
    return findings, info


def check_metadata(root):
    manifest = root / '.agents/plugins/marketplace.json'
    findings, chars, count = [], 0, 0
    if not manifest.exists():
        return [], dict(status='not_applicable', chars=0, skills=0)
    try:
        plugins = read_json(manifest)['plugins']
        if not isinstance(plugins, list):
            raise ValueError('plugins must be a list')
        for plugin in plugins:
            folder = (root / plugin['source']['path']).resolve()
            if not folder.is_relative_to(root.resolve()) or not folder.is_dir():
                raise ValueError('plugin source missing or outside repository')
            for path in sorted(folder.glob('skills/*/agents/openai.yaml')):
                interface = yaml.safe_load(path.read_text(encoding='utf-8'))['interface']
                description = interface.get('short_description', '')
                prompt = interface.get('default_prompt', '')
                if not isinstance(description, str) or not isinstance(prompt, str):
                    raise ValueError(f'Expected string metadata: {path}')
                size = len(description) + len(prompt)
                chars += size
                count += 1
                if len(description) > 120 or len(prompt) > 80:
                    findings.append(issue('skill_metadata_oversized', path, f'description={len(description)}/120; prompt={len(prompt)}/80 chars'))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        findings.append(issue('metadata_unreadable', manifest, f'Cannot audit Codex metadata: {exc}', 'error'))
    if chars > 7000:
        findings.append(issue('skill_metadata_budget_exceeded', manifest, f'{chars} metadata chars > 7000'))
    return findings, dict(chars=chars, skills=count, status='partial' if any(i['severity'] == 'error' for i in findings) else 'measured')


def normalize(findings, project):
    unique = {}
    for finding in findings:
        finding = dict(finding, project=str(project))
        subject = finding.get('subject') or {key: finding[key] for key in ('path', 'raw', 'resolved', 'name') if key in finding}
        # Descriptions and measured values are deliberately absent from identity.
        key = json.dumps([str(project), finding['type'], subject], ensure_ascii=False)
        finding['id'] = hashlib.sha256(key.encode()).hexdigest()[:24]
        unique[finding['id']] = finding
    return sorted(unique.values(), key=lambda i: (i['project'], i['type'], i['id']))


def collect(projects, home=None, host_id=None):
    roots, coverage = discover(projects, host_id=host_id)
    reports, findings = [], []
    hook_paths = set()
    home = home or Path.home()
    profile = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(home / '.claude'))).expanduser()
    for path in [profile / 'settings.json', profile / 'settings.local.json']:
        if path.exists():
            hook_paths.add(path)
    for root in roots:
        report = check_project(root)
        metadata_issues, metadata = check_metadata(root)
        report['info']['metadata'] = metadata
        report['issues'].extend(metadata_issues)
        report['issues'] = normalize(report['issues'], root)
        report['status'] = project_status(report['issues'])
        findings.extend(report['issues'])
        reports.append(report)
        hook_paths.update(p for p in [root / '.claude/settings.json', root / '.claude/settings.local.json'] if p.exists())
        hook_paths.update(root.glob('plugins/*/hooks/hooks.json'))
    hooks, hook_info = check_hooks(hook_paths)
    findings.extend(normalize(hooks, '@hooks'))
    for row in coverage:
        if row['status'] == 'unavailable':
            findings.extend(normalize([issue('source_unavailable', row['path'], 'Source not available on this host; not audited')], row['project']))
    return dict(schema_version=1, projects=reports, coverage=coverage, hooks=hook_info,
                issues=sorted(findings, key=lambda i: (i['project'], i['type'], i['id'])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--projects-json', type=Path, help='Explicit BB inventory fixture (otherwise bb project list)')
    parser.add_argument('--report-dir', type=Path)
    parser.add_argument('--host-id', help='Executing BB host ID; required for unattended hosted inventory')
    parser.add_argument('--notify-script', type=Path, help='send_draft.py; requires --report-dir')
    args = parser.parse_args(argv)
    if args.notify_script and not args.report_dir:
        parser.error('--notify-script requires --report-dir')
    try:
        report = {}
        def gather():
            if args.projects_json:
                projects = read_json(args.projects_json)
            else:
                proc = subprocess.run(['bb', 'project', 'list', '--include-personal', '--json'], capture_output=True, text=True, timeout=30, check=True)
                projects = json.loads(proc.stdout)
            host_id = args.host_id
            if not host_id and not args.projects_json:
                status = subprocess.run(['bb', 'status', '--json'], capture_output=True, text=True, timeout=30, check=True)
                host_id = ((json.loads(status.stdout).get('thread') or {}).get('environment') or {}).get('hostId')
                if not host_id:
                    raise ValueError('Executing host unknown: pass --host-id for unattended runs')
            report.update(collect(projects, host_id=host_id))
            return report
        if args.report_dir:
            from ctx_fleet_report import publish
            def send(message):
                subprocess.run([sys.executable, str(args.notify_script), '--markdown', message], capture_output=True, text=True, timeout=120, check=True)
            report['publication'] = publish(gather, args.report_dir.expanduser(), datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat(), send if args.notify_script else None)
        else:
            gather()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.report_dir:
            print(json.dumps(dict(report=report['publication']['path'], projects=len(report['projects']), findings=len(report['issues']), new=len(report['publication']['new']), baseline=report['publication']['baseline']), ensure_ascii=False))
        else:
            from ctx_fleet_report import markdown
            print(markdown(report))
        return 0  # Findings are audit data; operational failures use exit 2.
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'ctx-lint fleet: {exc}', file=sys.stderr)
        return 2
