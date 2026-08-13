#!/usr/bin/env python3
"""
PreCompact Hook: Auto-save compressed session context before compaction.

Fires before /compact or auto-compact. Reads transcript JSONL,
extracts key info, saves compressed handoff to ~/.claude/handoff/.

Input: JSON on stdin from Claude Code hook system.
Output: none (writes files only).
"""
import sys
import json
import os
import re
from pathlib import Path
from datetime import datetime
from collections import Counter

HANDOFF_DIR = Path.home() / ".claude" / "handoff"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
MAX_USER_MESSAGES = 20
MAX_ASSISTANT_SNIPPETS = 15
# The heavy pass (snippets, files, commands) stays capped — those live in the recent
# turns. User requests are cheap and define the whole arc of a session, so they are
# collected across the ENTIRE transcript: with a 2000-line cap a long session silently
# lost its own beginning, and nothing said so.
MAX_TRANSCRIPT_LINES = 6000
SNIPPET_MAX_CHARS = 300


def _log(msg):
    """Debug log to /tmp/context-handoff.log."""
    try:
        with open("/tmp/context-handoff.log", "a") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass


def pane_slug():
    """Filesystem-safe id of the tmux pane this session runs in, or "" outside tmux.

    The pane is the only identifier that survives /clear: the session id is regenerated,
    while the operator stays in the same pane. TMUX_PANE looks like "%19".
    """
    raw = os.environ.get("TMUX_PANE", "").strip()
    if not raw:
        return ""
    slug = re.sub(r"[^A-Za-z0-9_-]", "", raw.replace("%", "p"))
    return slug[:32]


def cwd_to_project_hash(cwd):
    """Convert an absolute cwd path to Claude's project-directory slug."""
    return cwd.replace("/", "-")


def find_transcript(session_id, cwd):
    """Find transcript JSONL file."""
    # Try project-hash directory first
    if cwd:
        project_hash = cwd_to_project_hash(cwd)
        project_dir = CLAUDE_PROJECTS / project_hash
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate

    # Search all project dirs
    for jsonl in CLAUDE_PROJECTS.rglob(f"{session_id}.jsonl"):
        return jsonl

    # Fallback: most recent transcript in matching project dir
    if cwd:
        project_hash = cwd_to_project_hash(cwd)
        project_dir = CLAUDE_PROJECTS / project_hash
        if project_dir.exists():
            transcripts = sorted(
                project_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if transcripts:
                return transcripts[0]

    return None


def parse_transcript(path):
    """Extract key info from JSONL transcript."""
    user_messages = []
    assistant_snippets = []
    files_modified = set()
    files_read = set()
    tools_used = []
    bash_commands = []

    all_lines = []
    try:
        with open(path, "r") as f:
            all_lines = f.readlines()
    except Exception as e:
        _log(f"Error reading transcript: {e}")
        return None

    truncated = len(all_lines) > MAX_TRANSCRIPT_LINES
    lines = all_lines[-MAX_TRANSCRIPT_LINES:] if truncated else all_lines
    if truncated:
        # Say it out loud: a silently clipped handoff looks complete but is not.
        _log(f"transcript {len(all_lines)} lines > cap {MAX_TRANSCRIPT_LINES}; "
             f"heavy pass limited to the last {MAX_TRANSCRIPT_LINES}")

    # Cheap pre-pass over everything for user requests only. The substring test avoids
    # json.loads on the bulk of the file, which is tool output.
    early_requests = []
    if truncated:
        for line in all_lines[:-MAX_TRANSCRIPT_LINES]:
            # Substring pre-filter to avoid json.loads on the bulk of the file (mostly
            # tool output). Match just the value: the separator spacing depends on the
            # writer, and keying on '"type":"user"' silently matched nothing the moment
            # the JSON was written with spaces.
            if '"user"' not in line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("toolUseResult") or entry.get("isMeta"):
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            for t in _extract_texts(message.get("content", "")):
                t = t.strip()
                if len(t) > 5 and not t.startswith("<"):
                    early_requests.append(t)

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        # JSONL format: entry["type"] = "user"|"assistant",
        # actual message data in entry["message"]["role"] and entry["message"]["content"]
        entry_type = entry.get("type", "")
        message = entry.get("message", {})
        if not isinstance(message, dict):
            continue

        role = message.get("role", entry_type)
        content = message.get("content", "")
        is_tool_result = bool(entry.get("toolUseResult"))
        is_meta = bool(entry.get("isMeta"))

        # User messages (skip tool results, meta, and system/command noise)
        if role == "user" and not is_tool_result and not is_meta:
            texts = _extract_texts(content)
            for t in texts:
                t = t.strip()
                if not t or len(t) <= 5:
                    continue
                # Skip JSON blobs, command outputs, system messages
                if t.startswith("{") or t.startswith("["):
                    continue
                if t.startswith("<") and (">" in t[:50]):
                    continue  # XML tags: <command-*>, <local-command-*>, <task-notification>, etc.
                if t.startswith("This session is being continued"):
                    continue
                # Skip slash command echoes
                if re.match(r"^/\w+", t):
                    continue
                user_messages.append(t[:SNIPPET_MAX_CHARS])

        # Assistant content: tool uses + text
        if role == "assistant":
            blocks = content if isinstance(content, list) else []
            for block in blocks:
                if not isinstance(block, dict):
                    continue

                # Text snippets (skip noise)
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text and len(text) > 20:
                        # Skip low-value snippets
                        lower = text.lower()
                        if any(
                            skip in lower
                            for skip in (
                                "no response requested",
                                "no response needed",
                                "session restored",
                                "using handoff skill",
                                "using skill",
                            )
                        ):
                            continue
                        assistant_snippets.append(text[:SNIPPET_MAX_CHARS])

                # Tool uses
                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    tools_used.append(tool_name)

                    # File paths (skip handoff artifacts)
                    for key in ("file_path", "path", "file"):
                        fp = tool_input.get(key, "")
                        if isinstance(fp, str) and "/" in fp:
                            if "/.claude/handoff/" in fp:
                                continue
                            if tool_name in ("Write", "Edit", "MultiEdit"):
                                files_modified.add(fp)
                            elif tool_name == "Read":
                                files_read.add(fp)

                    # Bash commands
                    if tool_name == "Bash":
                        cmd = tool_input.get("command", "")
                        if cmd:
                            bash_commands.append(cmd[:150])

    _log(
        f"Parsed: {len(user_messages)} user msgs, {len(assistant_snippets)} snippets, "
        f"{len(files_modified)} modified, {len(files_read)} read, "
        f"{len(tools_used)} tools, {len(bash_commands)} cmds"
    )

    if early_requests:
        # Oldest first, so the handoff reads as the arc of the session.
        user_messages[:0] = _dedup(early_requests)[-MAX_USER_MESSAGES:]

    return {
        "user_messages": _dedup(user_messages[-MAX_USER_MESSAGES:]),
        "assistant_snippets": assistant_snippets[-MAX_ASSISTANT_SNIPPETS:],
        "files_modified": sorted(files_modified),
        "files_read": sorted(files_read - files_modified),
        "tools_used": tools_used,
        "bash_commands": bash_commands[-10:],
    }


def _extract_texts(content):
    """Extract text strings from message content (str or list)."""
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return texts
    return []


def _dedup(messages, threshold=0.7):
    """Remove near-duplicate messages."""
    result = []
    seen = set()
    for msg in messages:
        key = msg[:60].lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(msg)
    return result


def get_git_branch(cwd):
    """Get current git branch."""
    try:
        import subprocess

        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def build_handoff(data, session_id, cwd, branch):
    """Build compressed handoff markdown."""
    now = datetime.now().isoformat(timespec="minutes")
    lines = [
        "# Context Handoff",
        "",
        f"- session: {session_id}",
        f"- cwd: {cwd}",
    ]
    if branch:
        lines.append(f"- branch: {branch}")
    lines.extend([f"- saved: {now}", "- method: auto (PreCompact)", ""])

    # User requests
    if data["user_messages"]:
        lines.append("## User Requests")
        lines.append("")
        for msg in data["user_messages"]:
            # Compress: first line only, trim
            first_line = msg.split("\n")[0].strip()
            lines.append(f"- {first_line}")
        lines.append("")

    # Files modified
    if data["files_modified"]:
        lines.append("## Files Modified")
        lines.append("")
        for f in data["files_modified"]:
            lines.append(f"- {f}")
        lines.append("")

    # Files read (context)
    if data["files_read"]:
        lines.append("## Files Read")
        lines.append("")
        for f in data["files_read"][:15]:
            lines.append(f"- {f}")
        lines.append("")

    # Key bash commands
    if data["bash_commands"]:
        lines.append("## Commands Run")
        lines.append("")
        for cmd in data["bash_commands"]:
            lines.append(f"- `{cmd}`")
        lines.append("")

    # Assistant key snippets (compressed)
    if data["assistant_snippets"]:
        lines.append("## Key Decisions")
        lines.append("")
        for snippet in data["assistant_snippets"][-10:]:
            first_line = snippet.split("\n")[0].strip()
            if len(first_line) > 20:
                lines.append(f"- {first_line}")
        lines.append("")

    # Tool usage summary
    if data["tools_used"]:
        counts = Counter(data["tools_used"])
        lines.append("## Tool Usage")
        lines.append("")
        for tool, count in counts.most_common(10):
            lines.append(f"- {tool}: {count}")
        lines.append("")

    return "\n".join(lines)


def main():
    try:
        input_raw = sys.stdin.read()
        input_data = json.loads(input_raw) if input_raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        input_data = {}

    # Log raw input keys for debugging (first run shows exact field names)
    _log(f"PreCompact: raw keys={list(input_data.keys())}")

    # Claude Code may use session_id or sessionId
    session_id = (
        input_data.get("session_id")
        or input_data.get("sessionId")
        or input_data.get("session", {}).get("id")
        or "unknown"
    )
    cwd = (
        input_data.get("cwd")
        or input_data.get("workingDirectory")
        or input_data.get("workspace", {}).get("current_dir")
        or os.getcwd()
    )

    _log(f"PreCompact: session={session_id} cwd={cwd}")

    # Find transcript
    transcript = find_transcript(session_id, cwd)
    if not transcript:
        _log("PreCompact: no transcript found, skipping")
        sys.exit(0)

    _log(f"PreCompact: transcript={transcript}")

    # Parse
    data = parse_transcript(transcript)
    if not data:
        _log("PreCompact: parse failed, skipping")
        sys.exit(0)

    # Build handoff
    branch = get_git_branch(cwd)
    handoff = build_handoff(data, session_id, cwd, branch)

    # Save
    project_hash = cwd_to_project_hash(cwd)
    handoff_project_dir = HANDOFF_DIR / project_hash
    handoff_project_dir.mkdir(parents=True, exist_ok=True)

    # Per-session file
    (handoff_project_dir / f"{session_id}.md").write_text(handoff)

    # Per-pane file. /clear mints a NEW session id, so a session-keyed file can never be
    # found afterwards — but the tmux pane survives, and the next session in that pane is
    # exactly the one entitled to this handoff. Without it, restore falls back to the
    # project-wide HANDOFF.md, which a PARALLEL session in the same repo may have written
    # (two live sessions per directory is routine here). Inheriting a neighbour's handoff
    # is worse than inheriting none: an empty context is obvious, a plausible wrong one
    # sends the session confidently down someone else's task.
    pane = pane_slug()
    if pane:
        (handoff_project_dir / f"pane-{pane}.md").write_text(handoff)

    # Latest handoff for this project
    latest = handoff_project_dir / "HANDOFF.md"
    latest.write_text(handoff)

    # Metadata
    meta = {
        "session_id": session_id,
        "pane": pane,
        "cwd": cwd,
        "branch": branch,
        "timestamp": datetime.now().isoformat(),
        "method": "auto",
        "transcript": str(transcript),
    }
    (handoff_project_dir / "HANDOFF.meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False)
    )

    _log(f"PreCompact: saved handoff ({len(handoff)} chars) to {latest}")


if __name__ == "__main__":
    main()
