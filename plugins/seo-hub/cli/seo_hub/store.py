from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterator

from seo_hub.errors import SeoHubError
from seo_hub.run_models import ArtifactRef, RunManifest, SourceStatus


class StoreError(SeoHubError, ValueError):
    code = "RUN_STORE_ERROR"


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RunStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)

    def _safe_id(self, value: str, name: str) -> str:
        if not _SAFE_ID.fullmatch(value) or ".." in Path(value).parts:
            raise StoreError(f"invalid {name}: {value}")
        return value

    def project_dir(self, project_id: str) -> Path:
        return self.data_dir / self._safe_id(project_id, "project_id")

    def run_dir(self, project_id: str, run_id: str) -> Path:
        return self.project_dir(project_id) / "runs" / self._safe_id(run_id, "run_id")

    def save_manifest(self, manifest: RunManifest) -> Path:
        run_dir = self.run_dir(manifest.project_id, manifest.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        final = run_dir / "manifest.json"
        tmp = run_dir / f".manifest.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(manifest.to_json(), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, final)
        return final

    def load_manifest(self, project_id: str, run_id: str) -> RunManifest:
        path = self.run_dir(project_id, run_id) / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StoreError(f"run manifest not found: {project_id}/{run_id}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"failed to read run manifest: {project_id}/{run_id}: {exc}") from exc
        return _manifest_from_json(payload)

    def load_idempotent_run_id(self, project_id: str, idempotency_key: str) -> str:
        path = self._idempotency_path(project_id, idempotency_key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            run_id = str(payload["run_id"])
        except FileNotFoundError as exc:
            raise StoreError(f"idempotency key not found: {project_id}") from exc
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise StoreError(f"failed to read idempotency key: {project_id}: {exc}") from exc
        return self._safe_id(run_id, "run_id")

    def save_idempotent_run_id(self, project_id: str, idempotency_key: str, run_id: str) -> Path:
        path = self._idempotency_path(project_id, idempotency_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "idempotency_key_sha256": hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
            "run_id": self._safe_id(run_id, "run_id"),
        }
        tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path

    @staticmethod
    def _recorded_owner_is_alive(fd: int) -> bool:
        """Is the PID recorded in an already-flocked lock file still running?

        Secondary, conservative guard only: it can refuse an acquisition but never
        grant one, so an unreadable or nonsensical value means "no live owner" and
        the advisory lock stays the authority.
        """
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            recorded = os.read(fd, 64).decode("utf-8", errors="replace").strip()
            owner_pid = int(recorded)
        except (OSError, ValueError):
            return False
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OverflowError:
            # A value pid_t cannot hold can never name a live process.
            return False
        return True

    def _idempotency_path(self, project_id: str, idempotency_key: str) -> Path:
        if not idempotency_key:
            raise StoreError("invalid idempotency_key: empty")
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self.project_dir(project_id) / "idempotency" / f"{digest}.json"

    def list_manifests(self, project_id: str) -> list[RunManifest]:
        runs_dir = self.project_dir(project_id) / "runs"
        if not runs_dir.exists():
            return []
        manifests: list[RunManifest] = []
        for path in sorted(runs_dir.glob("*/manifest.json"), reverse=True):
            try:
                manifests.append(_manifest_from_json(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return sorted(manifests, key=lambda item: item.created_at, reverse=True)

    @contextlib.contextmanager
    def project_lock(self, project_id: str) -> Iterator[Path]:
        """Serialize runs of one project across processes.

        Ownership is held by an advisory `flock` on an open descriptor, not by the
        existence of the file. Existence-based locking needs a check-then-unlink
        reclamation for a lock left by a dead owner, and two workers can interleave
        there: one recreates the lock while the other is still between its check and
        its `unlink()`, so the second deletes a *live* lock and both enter the
        critical section. An advisory lock is released by the kernel when its owner
        dies, so a dead owner needs no reclamation at all.

        For the same reason the file is never unlinked on release: a waiter may
        already hold a descriptor to it, and removing the path would leave that
        waiter locking an orphan inode while the next acquirer creates a fresh one.
        The file stays behind, empty, and costs nothing.
        """
        project_dir = self.project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        lock_path = project_dir / ".run.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StoreError(f"project is already locked: {project_id}") from exc
            # The advisory lock is held from here on, so this read and the write
            # below cannot interleave with another acquirer.
            if self._recorded_owner_is_alive(fd):
                raise StoreError(f"project is already locked: {project_id}")
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            try:
                yield lock_path
            finally:
                # Clear the recorded owner before releasing: a leftover PID could be
                # reused by an unrelated process and then read as a live owner.
                with contextlib.suppress(OSError):
                    os.ftruncate(fd, 0)
        finally:
            os.close(fd)


def _manifest_from_json(payload: object) -> RunManifest:
    if not isinstance(payload, dict):
        raise StoreError("run manifest payload must be a JSON object")
    sources: dict[str, SourceStatus] = {}
    for name, raw in (payload.get("sources") if isinstance(payload.get("sources"), dict) else {}).items():
        if not isinstance(raw, dict):
            continue
        artifacts = [
            ArtifactRef(
                name=str(item["name"]),
                path=str(item["path"]),
                sha256=str(item["sha256"]),
                bytes=item.get("bytes") if isinstance(item.get("bytes"), int) else None,
                media_type=str(item["media_type"]) if item.get("media_type") is not None else None,
            )
            for item in raw.get("artifacts", [])
            if isinstance(item, dict) and {"name", "path", "sha256"} <= set(item)
        ]
        sources[str(name)] = SourceStatus(
            name=str(raw.get("name") or name),
            status=str(raw.get("status") or "failed"),  # type: ignore[arg-type]
            quality=str(raw.get("quality") or "missing"),  # type: ignore[arg-type]
            artifacts=artifacts,
            source_status=dict(raw.get("source_status")) if isinstance(raw.get("source_status"), dict) else {},
            error=dict(raw.get("error")) if isinstance(raw.get("error"), dict) else None,
            started_at=str(raw["started_at"]) if raw.get("started_at") is not None else None,
            finished_at=str(raw["finished_at"]) if raw.get("finished_at") is not None else None,
            command=[str(item) for item in raw.get("command", [])] if isinstance(raw.get("command"), list) else [],
        )
    return RunManifest(
        run_id=str(payload["run_id"]),
        project_id=str(payload["project_id"]),
        mode=str(payload["mode"]),
        state=str(payload["state"]),  # type: ignore[arg-type]
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
        sources=sources,
        schema_version=int(payload.get("schema_version") or 1),
    )
