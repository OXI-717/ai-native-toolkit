"""Encrypted SQLite backup for the tenant runtime.

Takes a consistent online snapshot via the sqlite3 backup API, encrypts it
with ``age`` for a recipient public key, optionally ships it offsite with
``rclone``, and keeps only the newest ``keep`` encrypted files locally. The
plaintext snapshot never touches disk: the database is backed up into an
in-memory SQLite database, serialized, and piped to ``age`` on stdin, so a
SIGTERM or OOM kill cannot strand plaintext. At the start of every run,
leftover ``.work-*`` directories and ``*.db`` files written by older
versions are removed from the backup dir.

Every external process runs through ``runner`` with a timeout and its exit
code is checked; failures raise :class:`BackupError` with a stable code so
the CLI can emit a structured JSON error.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

BACKUP_NAME_PREFIX = "observer-"
BACKUP_NAME_SUFFIX = ".db.age"


class BackupError(Exception):
    """A failed backup step; ``code`` is a stable machine-readable error id."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _fs_error(operation: str, exc: OSError) -> BackupError:
    """A failed local filesystem step; carries the operation and strerror only."""
    return BackupError("BACKUP_FAILED", f"{operation} failed: {exc.strerror or 'I/O error'}")


def _tool_error(code: str, tool: str, exc: BaseException | None = None,
                proc: subprocess.CompletedProcess | None = None) -> BackupError:
    """Build a safe error: tool name plus exit code or ``timed out`` only.

    Tool stderr is never included — rclone echoes the remote's connection
    string, including any env-supplied credentials, into its diagnostics.
    """
    if exc is not None:
        detail = "timed out" if isinstance(exc, subprocess.TimeoutExpired) else "could not be executed"
    else:
        detail = f"exited with code {proc.returncode}"
    return BackupError(code, f"{tool} failed: {detail}")


def _snapshot_bytes(db_path: Path) -> bytes:
    """Return a consistent copy of ``db_path`` serialized in memory.

    A plain ``sqlite3.connect`` is used on purpose: a read-only URI cannot
    create the ``-shm`` file of a WAL database with no live writer, which is
    exactly the state on first run or right after a restore.
    """
    src = sqlite3.connect(db_path)
    try:
        mem = sqlite3.connect(":memory:")
        try:
            src.backup(mem)
            return mem.serialize()
        finally:
            mem.close()
    finally:
        src.close()


def _cleanup_stale_work(backup_dir: Path) -> None:
    """Remove plaintext leftovers written by older backup versions.

    Only runtime-owned names are swept: ``.work-*`` snapshot directories of
    older versions. Arbitrary ``*.db`` files are never deleted, so a
    misconfigured ``backup_dir`` cannot destroy a database. Symlinks are
    never followed.
    """
    for entry in backup_dir.iterdir():
        if entry.is_symlink():
            continue
        if entry.is_dir() and entry.name.startswith(".work-"):
            shutil.rmtree(entry, ignore_errors=True)


def _apply_retention(backup_dir: Path, keep: int) -> list[str]:
    """Delete all but the ``keep`` newest ``observer-*.db.age`` regular files."""
    candidates = sorted(
        (p for p in backup_dir.glob(f"{BACKUP_NAME_PREFIX}*{BACKUP_NAME_SUFFIX}")
         if p.is_file() and not p.is_symlink()),
        key=lambda p: p.name,
        reverse=True,
    )
    kept = [p.name for p in candidates[:keep]]
    for stale in candidates[keep:]:
        stale.unlink()
    return kept


def run_backup(*, db_path: Path, backup_dir: Path, recipient: str, remote: str | None,
               now: datetime, keep: int = 3, runner: Callable = subprocess.run,
               timeout_s: float = 600) -> dict:
    """Snapshot ``db_path``, encrypt with ``age`` and optionally upload with ``rclone``.

    Returns ``{"ok": True, "file": name, "uploaded": bool, "kept": [...]}`` or
    raises :class:`BackupError`. On upload failure the local encrypted copy is
    kept (it is valid) and retention is skipped.
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    # Check before connecting: sqlite3.connect would silently create an empty
    # database at a missing path.
    if not db_path.is_file():
        raise BackupError("BACKUP_FAILED", f"database not found: {db_path}")

    real_db = Path(os.path.realpath(db_path))
    real_backup = Path(os.path.realpath(backup_dir))
    if real_backup == real_db.parent or real_backup in real_db.parents:
        raise BackupError(
            "BACKUP_CONFIG_INVALID",
            "backup directory must not contain the database; use a separate directory",
        )

    try:
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _cleanup_stale_work(backup_dir)
    except OSError as exc:
        raise _fs_error("prepare backup directory", exc) from exc
    try:
        data = _snapshot_bytes(db_path)
    except (sqlite3.Error, OSError) as exc:
        raise BackupError("BACKUP_FAILED", f"snapshot failed: {exc}") from exc

    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = backup_dir / f"{BACKUP_NAME_PREFIX}{stamp}{BACKUP_NAME_SUFFIX}"
    encrypted_tmp = backup_dir / f".{final.name}.tmp-{uuid.uuid4().hex}"
    try:
        proc = runner(["age", "-r", recipient, "-o", str(encrypted_tmp)],
                      input=data, timeout=timeout_s, check=False, capture_output=True)
    except (subprocess.TimeoutExpired, OSError) as exc:
        encrypted_tmp.unlink(missing_ok=True)
        raise _tool_error("BACKUP_ENCRYPT_FAILED", "age", exc=exc) from exc
    if proc.returncode != 0:
        encrypted_tmp.unlink(missing_ok=True)
        raise _tool_error("BACKUP_ENCRYPT_FAILED", "age", proc=proc)
    try:
        os.replace(encrypted_tmp, final)
    except OSError as exc:
        raise _fs_error("finalize backup file", exc) from exc

    uploaded = False
    if remote:
        try:
            proc = runner(["rclone", "copyto", str(final), f"{remote}/{final.name}",
                           "--retries", "3"],
                          timeout=timeout_s, check=False, capture_output=True)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise _tool_error("BACKUP_UPLOAD_FAILED", "rclone", exc=exc) from exc
        if proc.returncode != 0:
            raise _tool_error("BACKUP_UPLOAD_FAILED", "rclone", proc=proc)
        uploaded = True

    try:
        kept = _apply_retention(backup_dir, keep)
    except OSError as exc:
        raise _fs_error("apply retention", exc) from exc
    return {"ok": True, "file": final.name, "uploaded": uploaded, "kept": kept}
