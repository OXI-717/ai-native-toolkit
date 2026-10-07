"""Audited operator actions. Keyword synchronization never checks paid positions."""

import json
from datetime import date, datetime, timezone

from seo_observer.serp_storage import ensure_schema


def audit(con, tenant, project, event, details):
    con.execute(
        "INSERT INTO serp_audit(tenant,provider_project,event,recorded_at,details_json) VALUES(?,?,?,?,?)",
        (
            tenant,
            str(project),
            event,
            datetime.now(timezone.utc).isoformat(),
            json.dumps(details, ensure_ascii=False),
        ),
    )


def reset_uncertain(storage, *, tenant, project_id, week, confirm_not_charged, reason):
    """Operator must verify the provider did not charge before releasing a week."""
    if not confirm_not_charged or not reason.strip():
        raise ValueError("Reset requires --confirm-not-charged and --reason")
    date.fromisoformat(week + "-1")
    ensure_schema(storage)
    with storage.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        key = (tenant, str(project_id), week)
        row = con.execute(
            "SELECT * FROM serp_check_runs WHERE tenant=? AND provider_project=? AND iso_week=?",
            key,
        ).fetchone()
        if row is None or row["status"] != "uncertain":
            raise ValueError("Only an uncertain week can be reset")
        audit(
            con,
            tenant,
            project_id,
            "uncertain_reset",
            {"previous": dict(row), "reason": reason, "confirmed_not_charged": True},
        )
        con.execute(
            "DELETE FROM serp_check_runs WHERE tenant=? AND provider_project=? AND iso_week=?",
            key,
        )
    return {"ok": True, "status": "reset", "week": week}


def normalized(text):
    return " ".join(text.casefold().split())


def keyword_plan(inventory, expected):
    """Prefer existing clean IDs, then rename annotated IDs, then remove duplicates."""
    from seo_observer.keyword_clusters import split_keyword_comment

    if not expected:
        raise ValueError("Refusing an empty configured population")
    available = set(expected)
    plan = {"remove": [], "rename": [], "add": []}
    ordered = sorted(
        inventory, key=lambda r: (normalized(r["name"]) not in expected, int(r["id"]))
    )
    for row in ordered:
        exact = normalized(row["name"])
        clean = (
            exact
            if exact in expected
            else normalized(split_keyword_comment(row["name"])[0])
        )
        if clean in available:
            available.remove(clean)
            if row["name"] != clean:
                plan["rename"].append(
                    {"id": row["id"], "before": row["name"], "after": clean}
                )
        else:
            plan["remove"].append({"id": row["id"], "name": row["name"]})
    plan["add"] = sorted(available)
    return plan


def sync_keywords(storage, config, market, client, *, apply=False):
    from seo_observer.serp_collection import market_keywords

    storage.bootstrap()
    ensure_schema(storage)
    project = market.fields["project_id"]
    tenant = config.project.namespace
    shared = sorted(
        m.id
        for m in config.markets
        if m.provider.startswith("topvisor_")
        and str(m.fields.get("project_id")) == str(project)
    )
    if len(shared) > 1:
        result = {
            "ok": False,
            "status": "shared_project",
            "project_id": str(project),
            "markets": shared,
        }
        with storage.connect() as con:
            audit(con, tenant, project, "keyword_sync_refused", result)
        return result
    expected = set(market_keywords(config, market))
    inventory = client.keyword_inventory(project_id=project)
    plan = keyword_plan(inventory, expected)
    examples = []
    # Round-robin examples show each kind of change within the ten-item cap.
    for index in range(10):
        for action, changes in plan.items():
            if index < len(changes):
                change = changes[index]
                examples.append(
                    {
                        "action": action,
                        **(change if isinstance(change, dict) else {"name": change}),
                    }
                )
    result = {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "market": market.id,
        "project_id": str(project),
        "existing": len(inventory),
        "expected": len(expected),
        "counts": {k: len(v) for k, v in plan.items()},
        "examples": examples[:10],
    }
    with storage.connect() as con:
        audit(
            con,
            tenant,
            project,
            "keyword_sync_started" if apply else "keyword_sync_dry_run",
            {"result": result, "plan": plan},
        )
    if not apply:
        return result
    completed = []
    try:
        if str(client.project_state(project_id=project).get("status_positions")) != "0":
            raise ValueError("Cannot synchronize while positions are being checked")
        # Delete duplicates first so renaming cannot collide with another stored name.
        for row in plan["remove"]:
            client.delete_keyword(project_id=project, keyword_id=row["id"])
            completed.append({"action": "remove", "id": row["id"]})
        for row in plan["rename"]:
            client.rename_keyword(
                project_id=project, keyword_id=row["id"], name=row["after"]
            )
            completed.append({"action": "rename", "id": row["id"]})
        if plan["add"]:
            client.import_keywords(project_id=project, names=plan["add"])
            completed.append({"action": "add", "count": len(plan["add"])})
        actual = client.keyword_inventory(project_id=project)
        if (
            len(actual) != len(expected)
            or {normalized(r["name"]) for r in actual} != expected
        ):
            raise ValueError("Keyword synchronization verification failed")
    except Exception:
        with storage.connect() as con:
            audit(
                con,
                tenant,
                project,
                "keyword_sync_failed",
                {"completed": completed, "plan": plan},
            )
        raise
    with storage.connect() as con:
        audit(
            con,
            tenant,
            project,
            "keyword_sync_applied",
            {"completed": completed, "result": result},
        )
    return result
