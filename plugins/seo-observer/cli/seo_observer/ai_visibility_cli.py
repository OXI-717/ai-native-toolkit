"""Local artifact-only AI visibility commands."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

from seo_observer.actions import (
    ActionError,
    load_action,
    action_from_dict,
    action_evidence_from_snapshots,
    compute_action_verdict,
)
from seo_observer.ai_visibility_storage import ingest_ai_visibility
from seo_observer.config import ConfigError, compute_config_hash
from seo_observer.snapshots import write_snapshot
from seo_observer.storage import StorageError


def register_commands(subparsers, common, selectors):
    root = subparsers.add_parser("ai-visibility", parents=[common, selectors])
    commands = root.add_subparsers(dest="ai_visibility_command", required=True)
    ingest = commands.add_parser("import", parents=[common, selectors])
    ingest.add_argument("--input", required=True, type=Path)
    ingest.add_argument("--property-id", required=True)
    ingest.add_argument("--period-id", required=True)
    ingest.add_argument("--observed-at", required=True)
    ingest.set_defaults(handler=handle)
    snapshot = commands.add_parser("snapshot", parents=[common, selectors])
    snapshot.add_argument("--period-id", required=True)
    snapshot.add_argument("--generated-at", required=True)
    snapshot.add_argument("--output-dir", required=True, type=Path)
    snapshot.set_defaults(handler=handle)
    evaluate = commands.add_parser("evaluate", parents=[common, selectors])
    evaluate.add_argument("--action", required=True, type=Path)
    evaluate.add_argument("--window-id", required=True)
    evaluate.add_argument("--baseline", required=True, type=Path)
    evaluate.add_argument("--observation", required=True, type=Path)
    evaluate.add_argument("--as-of", required=True)
    evaluate.set_defaults(handler=handle)


def read_document(path):
    if path.stat().st_size > 20_000_000:
        raise ValueError("Input artifact exceeds 20 MB")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Input artifact must be a JSON object")
    return value


def handle(args):
    from seo_observer.cli import (
        _selected_config_and_storage,
        _emit_payload,
        _emit_error,
        _structured_error_payload,
    )

    try:
        config, storage = _selected_config_and_storage(args)
        project_id = config.project.namespace
        command = args.ai_visibility_command
        if command == "import":
            storage.upsert_project_config(
                config, config_hash=compute_config_hash(config)
            )
            receipt = ingest_ai_visibility(
                storage,
                read_document(args.input),
                project_id=project_id,
                property_id=args.property_id,
                reporting_period_id=args.period_id,
                observed_at=args.observed_at,
            )
            payload = {
                "ok": True,
                "status": "ok",
                "command": "ai-visibility import",
                "evidence_id": receipt["evidence_id"],
                "quality": receipt["quality"],
                "period_id": args.period_id,
            }
        elif command == "snapshot":
            written = write_snapshot(
                storage,
                output_root=args.output_dir,
                project_id=project_id,
                reporting_period_id=args.period_id,
                generated_at=args.generated_at,
            )
            payload = {
                "ok": True,
                "status": "ok",
                "command": "ai-visibility snapshot",
                "snapshot_path": str(written.path),
                "snapshot_hash": written.snapshot["manifest"]["snapshot_hash"],
            }
        else:
            action = (
                action_from_dict(read_document(args.action))
                if args.action.suffix == ".json"
                else load_action(args.action)
            )
            if action.project_id != project_id:
                raise ValueError("Action belongs to another project")
            if len(action.expected_signals) != 1 or not all(
                s.metric_path.startswith("ai_visibility.platform_metrics.")
                for s in action.expected_signals
            ):
                raise ValueError("Expected exactly one AI platform action signal")
            windows = [w for w in action.windows if w.window_id == args.window_id]
            if len(windows) != 1:
                raise ValueError("Unknown or ambiguous action window")
            baseline = read_document(args.baseline)
            observation = read_document(args.observation)
            evidence = action_evidence_from_snapshots(
                action,
                windows[0],
                baseline,
                observation,
                as_of=args.as_of,
                confounders=list(action.confounders),
            )
            result = compute_action_verdict(action, windows[0], evidence)
            payload = {
                "ok": True,
                "status": "ok",
                "command": "ai-visibility evaluate",
                "verdict": dataclasses.asdict(result),
            }
    except ConfigError as exc:
        return _emit_error(args, exc)
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ActionError,
        StorageError,
        sqlite3.Error,
    ) as exc:
        payload = _structured_error_payload(
            "AI_VISIBILITY_FAILED",
            "Local AI visibility operation failed.",
            {"error_type": type(exc).__name__, "error": str(exc)},
        )
    return _emit_payload(args, payload)
