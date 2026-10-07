"""CLI seams for weekly submission, free reads, and historical import."""

import json
import os
from datetime import date
from pathlib import Path

from seo_observer.serp_collection import budget_for, run_collection
from seo_observer.serp_storage import import_history
from seo_observer.topvisor import PostJsonHttp, TopvisorClient, TopvisorCredentials


def register_serp(subparsers, common, selectors):
    parser = subparsers.add_parser("serp", parents=[common, selectors])
    subs = parser.add_subparsers(dest="serp_command", required=True)
    collect = subs.add_parser("collect", parents=[common, selectors])
    collect.add_argument(
        "--weekly",
        action="store_true",
        help="Submit one budgeted paid run per ISO week",
    )
    collect.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Read existing snapshots only (YYYY-MM-DD)",
    )
    collect.add_argument("--state-file", type=Path, default=None)
    collect.set_defaults(handler=handle)
    imp = subs.add_parser("import", parents=[common, selectors])
    imp.add_argument(
        "--input",
        type=Path,
        action="append",
        required=True,
        help="serp-extract.json file or directory (recursive)",
    )
    imp.set_defaults(handler=handle)
    sync = subs.add_parser("sync-keywords", parents=[common, selectors])
    sync.add_argument("--market", required=True)
    sync.add_argument(
        "--apply",
        action="store_true",
        help="Confirm free project keyword edits; default is dry-run",
    )
    sync.set_defaults(handler=handle)
    reset = subs.add_parser("reset-uncertain", parents=[common, selectors])
    reset.add_argument("--market", required=True)
    reset.add_argument("--week", required=True)
    reset.add_argument("--confirm-not-charged", action="store_true")
    reset.add_argument("--reason", required=True)
    reset.set_defaults(handler=handle)
    rebuild = subs.add_parser(
        "rebuild",
        parents=[common, selectors],
        help="Recalculate saved SERPs and market rosters without API",
    )
    rebuild.set_defaults(handler=handle)
    price = subs.add_parser("estimate", parents=[common, selectors])
    price.set_defaults(handler=handle)


def client_for(market):
    from seo_observer.cli import _JsonHttpTransport

    user = os.environ.get(market.fields.get("user_id_env", "TOPVISOR_USER_ID"))
    key = os.environ.get(market.credential_env)
    if not user or not key:
        raise ValueError("TopVisor credential environment variables are missing")
    return TopvisorClient(
        PostJsonHttp(_JsonHttpTransport("https://api.topvisor.com")),
        TopvisorCredentials(user, key),
    )


def record_health(storage, config, path):
    markets = []
    for market in config.markets:
        if not market.provider.startswith("topvisor_"):
            continue
        enabled = bool(budget_for(market))
        states = storage.fetchall(
            "SELECT enabled_at FROM serp_budget_state WHERE tenant=? AND market=?",
            (config.project.namespace, market.id),
        )
        state = states[0] if states else {}
        last = storage.fetchone(
            "SELECT MAX(check_date) AS latest FROM serp_measurements WHERE project_id=? AND market=? AND historical=0",
            (config.project.namespace, market.id),
        )
        markets.append(
            {
                "enabled": enabled,
                "enabled_at": state.get("enabled_at"),
                "latest": last.get("latest"),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"markets": markets}), encoding="utf-8")
    os.replace(tmp, path)


def handle(args):
    from seo_observer.cli import _selected_config_and_storage

    try:
        config, storage = _selected_config_and_storage(args)
        if args.serp_command in ("sync-keywords", "reset-uncertain"):
            from seo_observer.serp_operations import reset_uncertain, sync_keywords

            market = next(
                m
                for m in config.markets
                if m.id == args.market and m.provider.startswith("topvisor_")
            )
            if args.serp_command == "sync-keywords":
                result = sync_keywords(
                    storage, config, market, client_for(market), apply=args.apply
                )
            else:
                storage.bootstrap()
                result = reset_uncertain(
                    storage,
                    tenant=config.project.namespace,
                    project_id=market.fields["project_id"],
                    week=args.week,
                    confirm_not_charged=args.confirm_not_charged,
                    reason=args.reason,
                )
        elif args.serp_command == "rebuild":
            from seo_observer.serp_storage import rebuild_measurements

            result = rebuild_measurements(storage, config)
        elif args.serp_command == "estimate":
            result = {
                "ok": True,
                "prices": [
                    {
                        "market": m.id,
                        "project_id": str(m.fields["project_id"]),
                        "price_rub": client_for(m).estimate_price(
                            project_id=m.fields["project_id"], do_snapshots=True
                        ),
                    }
                    for m in config.markets
                    if m.provider.startswith("topvisor_")
                ],
            }
        elif args.serp_command == "import":
            paths = sorted(
                {
                    p
                    for root in args.input
                    for p in (
                        root.rglob("serp-extract.json") if root.is_dir() else [root]
                    )
                }
            )
            if not paths:
                raise ValueError("No historical SERP artifacts found")
            result = import_history(storage, config, paths)
        else:
            if args.weekly and args.date:
                raise ValueError("--weekly cannot be combined with --date")
            try:
                result = run_collection(
                    storage,
                    config,
                    client_for,
                    weekly=args.weekly,
                    check_date=args.date.isoformat() if args.date else None,
                )
            finally:
                if args.state_file:
                    record_health(storage, config, args.state_file)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok", True) else 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary redacts provider errors
        # Never serialize provider exception messages: they can contain request details.
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {"code": "SERP_FAILED", "type": type(exc).__name__},
                },
                ensure_ascii=False,
            )
        )
        return 1
