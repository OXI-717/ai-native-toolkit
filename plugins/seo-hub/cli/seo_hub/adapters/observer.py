from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Awaitable, Callable

from seo_hub.adapters.base import AdapterResult
from seo_hub.errors import SeoHubError
from seo_hub.run_models import ArtifactRef
from seo_hub.subprocesses import CommandResult, run_json_command


class ObserverAdapterError(SeoHubError, ValueError):
    code = "OBSERVER_ADAPTER_ERROR"


ObserverRunner = Callable[..., Awaitable[CommandResult]]


READ_ONLY_COMMANDS = frozenset({"doctor", "snapshot", "ai-readiness", "report", "opportunities", "outcomes"})
COMMANDS_WITH_OUTPUT_DIR = frozenset({"snapshot", "ai-readiness", "report"})
OBSERVER_ENV_ALLOWLIST = frozenset({"SEO_OBSERVER_HOME", "PATH"})


class ObserverAdapter:
    def __init__(
        self,
        *,
        binary: str = "seo-observer",
        runner: ObserverRunner = run_json_command,
        timeout_seconds: float = 60,
        max_stdout_bytes: int = 2_000_000,
        evidence_roots: tuple[Path, ...] = (),
    ) -> None:
        self.binary = binary
        self._runner = runner
        self.timeout_seconds = timeout_seconds
        self.max_stdout_bytes = max_stdout_bytes
        self.evidence_roots = evidence_roots

    async def read(
        self,
        command: str,
        *,
        config: str | Path,
        output_dir: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> AdapterResult:
        if command not in READ_ONLY_COMMANDS:
            raise ObserverAdapterError(f"observer command is not allowlisted for read-only runs: {command}")
        if command in {"snapshot", "report"} and Path(config).is_file():
            return await self._read_existing(command, Path(config), output_dir=output_dir, env=env)
        argv = [self.binary, command, "--config", str(config)]
        if output_dir is not None and command in COMMANDS_WITH_OUTPUT_DIR:
            argv.extend(["--output-dir", str(output_dir)])
        argv.append("--json")
        result = await self._runner(
            argv,
            env=env or dict(os.environ),
            allowed_env=OBSERVER_ENV_ALLOWLIST,
            timeout_seconds=self.timeout_seconds,
            max_stdout_bytes=self.max_stdout_bytes,
        )
        return _adapter_result(command, result)

    async def _read_existing(self, command: str, config: Path, *, output_dir, env) -> AdapterResult:
        binary = shutil.which(self.binary, path=(env or os.environ).get("PATH"))
        if binary is None:
            return _existing_failure(command, "OBSERVER_API_UNAVAILABLE", "Installed Observer executable was not found.")
        try:
            with Path(binary).open(encoding="utf-8") as stream:
                shebang = stream.readline(4096).strip()
            python = Path(shebang.removeprefix("#!"))
            if not shebang.startswith("#!/") or not python.is_file() or not python.name.startswith("python"):
                return _existing_failure(command, "OBSERVER_API_UNAVAILABLE", "Observer must have a Python console-script installation.")
        except (OSError, UnicodeError):
            return _existing_failure(command, "OBSERVER_API_UNAVAILABLE", "Installed Observer interpreter could not be resolved.")
        helper = Path(__file__).with_name("observer_evidence.py")
        argv = [str(python), "-B", str(helper), "--config", str(config)]
        for root in self.evidence_roots:
            argv.extend(["--root", str(root)])
        result = await self._runner(argv, env=env or dict(os.environ),
                                    allowed_env=OBSERVER_ENV_ALLOWLIST,
                                    timeout_seconds=self.timeout_seconds,
                                    max_stdout_bytes=self.max_stdout_bytes)
        payload = dict(result.payload)
        if payload.get("ok") is True and result.returncode == 0 and output_dir is not None:
            try:
                payload["artifacts"] = _write_existing_artifacts(command, Path(output_dir), payload)
            except OSError:
                return _existing_failure(command, "OBSERVER_OUTPUT_UNAVAILABLE", "Hub evidence summary could not be written.")
        return _adapter_result(command, CommandResult(result.argv, result.returncode, payload, result.stdout, result.stderr))


def _existing_failure(command: str, code: str, message: str) -> AdapterResult:
    return AdapterResult(name=f"observer.{command}", status="failed", quality="missing", error={"code": code, "message": message})


def _write_existing_artifacts(command: str, directory: Path, payload: dict) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    files = {command: (f"{command}.json", content)}
    if command == "report":
        lines = ["# Existing Observer Evidence", "", payload["summary"], ""]
        for item in payload.get("evidence", []):
            lines.append(f"- {item['source']} ({item['kind']}): {item['quality']}; observed {item.get('observed_at') or 'unknown'}; period end {item.get('effective_end') or 'not supplied'}.")
        files["report_markdown"] = ("report.md", "\n".join(lines) + "\n")
    artifacts = {}
    for name, (filename, text) in files.items():
        path = directory / filename
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        artifacts[f"{name}_path"] = str(path)
        artifacts[f"{name}_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return artifacts


def _adapter_result(command: str, result: CommandResult) -> AdapterResult:
    payload = result.payload
    ok = payload.get("ok") is True and result.returncode == 0
    artifacts = _artifact_refs(payload)
    source_status = _source_status(payload)
    if ok:
        return AdapterResult(
            name=f"observer.{command}",
            status="succeeded",
            quality=_quality(payload, default="complete"),
            artifacts=artifacts,
            source_status=source_status,
            command=result.argv,
        )
    return AdapterResult(
        name=f"observer.{command}",
        status="failed",
        quality=_quality(payload, default="missing"),
        artifacts=artifacts,
        source_status=source_status,
        error=_error(payload),
        command=result.argv,
    )


def _dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _source_status(payload: dict[str, object]) -> dict[str, object]:
    """Explicit `source_status` envelope first; the raw response only when the
    Observer sent neither that envelope nor artifact metadata (bare read of an
    existing file must keep `source_status["mode"]` where callers read it)."""
    envelope = payload.get("source_status")
    if isinstance(envelope, dict):
        return dict(envelope)
    if not isinstance(payload.get("artifacts"), dict):
        return dict(payload)
    return {}


def _quality(payload: dict[str, object], *, default: str) -> str:
    value = payload.get("quality") or payload.get("evidence_quality")
    return str(value) if value in {"complete", "partial", "stale", "not_comparable", "missing"} else default


def _error(payload: dict[str, object]) -> dict[str, object]:
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "OBSERVER_FAILED")
        message = str(error.get("message") or "observer command failed")
        return {"code": code, "message": message}
    return {"code": "OBSERVER_FAILED", "message": "observer command failed"}


def _artifact_refs(payload: dict[str, object]) -> list[ArtifactRef]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    refs: list[ArtifactRef] = []
    for key, value in sorted(artifacts.items()):
        if not key.endswith("_path") or not isinstance(value, str):
            continue
        stem = key[: -len("_path")]
        sha = artifacts.get(f"{stem}_sha256")
        if not isinstance(sha, str):
            continue
        refs.append(ArtifactRef(name=stem, path=value, sha256=sha))
    return refs
