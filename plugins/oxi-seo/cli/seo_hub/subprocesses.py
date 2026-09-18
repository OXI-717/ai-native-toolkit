from __future__ import annotations

import asyncio
import json
import os
import signal
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from seo_hub.errors import SeoHubError


class CommandError(SeoHubError, RuntimeError):
    code = "COMMAND_ERROR"


class CommandTimeout(CommandError):
    code = "COMMAND_TIMEOUT"


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    payload: dict[str, Any]
    stdout: str
    stderr: str


async def _read_limited(stream: asyncio.StreamReader, limit: int, name: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise CommandError(f"{name} exceeded stdout limit" if name == "stdout" else f"{name} exceeded stderr limit")
        chunks.append(chunk)


async def _terminate_process_group(proc: asyncio.subprocess.Process, pgid: int) -> None:
    """Signal the whole group the command was launched into.

    The leader having exited does NOT mean the group is empty: a descendant that
    inherited stdout/stderr keeps the reader tasks pending, which is exactly the
    state cancellation and output-limit paths run in. Returning early on
    `proc.returncode is not None` therefore skipped cleanup in the one case that
    needed it, so the group id is captured at spawn instead of being looked up
    from a leader that may already be gone.
    """
    leader_running = proc.returncode is None
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        if not leader_running:
            # Nothing to await — give the group a moment to act on SIGTERM before
            # escalating, then let SIGKILL finish it.
            await asyncio.sleep(0.05)
            continue
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.5)
            return
        except asyncio.TimeoutError:
            continue


def _allowed_environment(
    *,
    env: Mapping[str, str] | None,
    allowed_env: set[str] | frozenset[str] | None,
) -> dict[str, str] | None:
    if allowed_env is None:
        return None
    merged = dict(os.environ)
    if env:
        merged.update({str(key): str(value) for key, value in env.items()})
    return {key: merged[key] for key in sorted(allowed_env) if key in merged}


async def run_json_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    allowed_env: set[str] | frozenset[str] | None = None,
    timeout_seconds: float = 30,
    max_stdout_bytes: int = 2_000_000,
    max_stderr_bytes: int = 256_000,
    cwd: str | os.PathLike[str] | None = None,
) -> CommandResult:
    if not argv:
        raise CommandError("argv must not be empty")
    proc = await asyncio.create_subprocess_exec(
        *[str(item) for item in argv],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_allowed_environment(env=env, allowed_env=allowed_env),
        cwd=cwd,
        start_new_session=True,
    )
    # `start_new_session=True` makes the child a group leader, so the group id is
    # its pid — known now, and still valid after the leader is reaped.
    pgid = proc.pid
    assert proc.stdout is not None
    assert proc.stderr is not None
    stdout_task = asyncio.create_task(_read_limited(proc.stdout, max_stdout_bytes, "stdout"))
    stderr_task = asyncio.create_task(_read_limited(proc.stderr, max_stderr_bytes, "stderr"))
    wait_task = asyncio.create_task(proc.wait())
    try:
        stdout, stderr, _ = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task, wait_task), timeout=timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        stdout_task.cancel()
        stderr_task.cancel()
        wait_task.cancel()
        await _terminate_process_group(proc, pgid)
        raise CommandTimeout(f"command timed out after {timeout_seconds}s: {list(argv)!r}") from exc
    except CommandError:
        stdout_task.cancel()
        stderr_task.cancel()
        wait_task.cancel()
        await _terminate_process_group(proc, pgid)
        raise
    except asyncio.CancelledError:
        stdout_task.cancel()
        stderr_task.cancel()
        wait_task.cancel()
        await _terminate_process_group(proc, pgid)
        raise

    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise CommandError(f"malformed JSON from command: {exc}") from exc
    if not isinstance(payload, dict):
        raise CommandError("command JSON must be an object")
    return CommandResult(
        argv=[str(item) for item in argv],
        returncode=int(proc.returncode or 0),
        payload=payload,
        stdout=stdout_text,
        stderr=stderr_text,
    )
