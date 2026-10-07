"""Growth export publisher: versioned immutable builds + atomic symlink swaps.

Writes ``growth.json`` / ``index.html`` / (weekly only) ``report.pdf`` /
``brief.md`` / ``receipt.json`` into ``<out>/builds/<kind>-<window>-<hash12>``
and repoints ``current`` / ``weekly/<start>_<end>`` / ``latest-weekly``
symlinks atomically. A failed build never replaces a published one; its
receipt lands in ``<out>/failed/``. Build directories are never modified or
deleted while referenced — POSIX ``os.replace`` refuses non-empty dirs, so
immutability plus symlink swaps is the whole atomicity story.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from seo_observer.channels import ChannelsConfig
from seo_observer.config import PanelConfig, KeywordSet, MarketConfig
from seo_observer.keyword_clusters import parse_keyword_file, clean_cluster
from seo_observer.growth import build_growth, derive_growth
from seo_observer.growth_render import render_brief, render_panel_html, render_dashboard_pages
from seo_observer.report_rendering import render_pdf_from_html
from seo_observer.storage import SEOStorage


RECEIPT_SCHEMA_VERSION = 1
WEEKLY_FINALIZE_DELAY_DAYS = 3
CURRENT_DAYS = 7
CURRENT_WIDE_DAYS = 28
TREND_WEEKS = 12
CURRENT_KEEP_UNLINKED = 2
WEEKLY_RETENTION_DAYS = 30
FAILED_RETENTION_DAYS = 30

# Build timestamps never change the data version: a rebuild of the same facts
# must land on the same hash. ``sources.*.collected_at`` stays in the hash —
# a re-collected day inside the same window IS new data.
_HASH_TIME_KEYS = frozenset({"generated_at", "produced_at"})


def select_finalized_week(today: date) -> tuple[date, date]:
    """Latest Mon–Sun week whose Sunday is at least 3 days before today."""
    latest_end = today - timedelta(days=WEEKLY_FINALIZE_DELAY_DAYS)
    end = latest_end - timedelta(days=(latest_end.weekday() + 1) % 7)
    return end - timedelta(days=6), end


def growth_hash(growth: dict[str, Any]) -> str:
    """sha256 of the canonical growth payload minus build-time fields."""
    stripped = _strip_time_fields(growth)
    canonical = json.dumps(stripped, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _strip_time_fields(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _strip_time_fields(value)
            for key, value in node.items()
            if key not in _HASH_TIME_KEYS
        }
    if isinstance(node, list):
        return [_strip_time_fields(item) for item in node]
    return node


def _utc_stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_compact(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _history_weeks(before: date) -> list[tuple[date, date]]:
    """The 12 most recent Mon–Sun weeks ending strictly before ``before``."""
    last_sunday = before - timedelta(days=1)
    last_sunday -= timedelta(days=(last_sunday.weekday() + 1) % 7)
    return [
        (last_sunday - timedelta(days=7 * i + 6), last_sunday - timedelta(days=7 * i))
        for i in reversed(range(TREND_WEEKS))
    ]


def _verify_layout(out_dir: Path) -> Path:
    """Resolve the export root and reject symlinked managed directories.

    Every directory the publisher writes into must be a real directory whose
    realpath stays inside the export root; a symlinked ``builds``/``weekly``/
    ``failed`` would let writes and retention deletes escape the root.
    """
    root = Path(os.path.realpath(out_dir))
    managed = (out_dir, out_dir / "builds", out_dir / "weekly", out_dir / "failed")
    for directory in managed:
        try:
            mode = os.lstat(directory).st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise _ExportLayoutUnsafe(directory, "exists but is not a real directory")
        real = Path(os.path.realpath(directory))
        if directory != out_dir and root not in real.parents:
            raise _ExportLayoutUnsafe(
                directory, "resolves outside the export root"
            )
    return root


def _is_real_dir_inside(entry: Path, root: Path) -> bool:
    try:
        if not stat.S_ISDIR(os.lstat(entry).st_mode):
            return False
        return root in Path(os.path.realpath(entry)).parents
    except OSError:
        return False


def _is_real_file_inside(entry: Path, root: Path) -> bool:
    try:
        if not stat.S_ISREG(os.lstat(entry).st_mode):
            return False
        return root in Path(os.path.realpath(entry)).parents
    except OSError:
        return False


def _swap_link(link: Path, target: str) -> None:
    """Atomically point ``link`` at ``target`` (create or replace)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f"{link.name}.tmp-{uuid.uuid4().hex}")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, link)


def _link_build_name(link: Path) -> str | None:
    if not link.is_symlink():
        return None
    return Path(os.readlink(link)).name


def _keyword_clusters(
    keyword_sets: list[KeywordSet], markets: tuple[MarketConfig, ...],
) -> dict[str, dict[str, str | None]]:
    """Resolve configured markets to the search source available on dashboard rows.

    Unscoped legacy sets are shared fallbacks. If multiple markets on the same
    engine disagree, retain an explicit ambiguity instead of picking file order.
    """
    sources = {"google": "google_search_console", "yandex": "yandex_webmaster"}
    market_sources = {m.id: sources.get(m.search_engine) for m in markets}
    clusters: dict[str, dict[str, str | None]] = {}
    for keyword_set in keyword_sets:
        source = market_sources.get(keyword_set.market) if keyword_set.market else "*"
        if source is None:
            # An unresolved market must never become a cross-engine fallback.
            continue
        scoped = clusters.setdefault(source, {})
        for keyword in parse_keyword_file(keyword_set.path):
            if keyword.cluster:
                query = " ".join(keyword.keyword.casefold().split())
                cluster = clean_cluster(keyword.cluster)
                if query in scoped and scoped[query] != cluster:
                    scoped[query] = None
                else:
                    scoped[query] = cluster
    return clusters


def export_growth(
    storage: SEOStorage,
    *,
    project_id: str,
    kind: str,
    out_dir: Path,
    channels: ChannelsConfig = ChannelsConfig(),
    panel: PanelConfig = PanelConfig(),
    keyword_sets: list[KeywordSet] | None = None,
    markets: tuple[MarketConfig, ...] = (),
    end_date: date | None = None,
    week_start: date | None = None,
    panel_url: str | None = None,
    no_pdf: bool = False,
    today: date | None = None,
    now: datetime | None = None,
    locale: str = "ru",
    pdf_renderer: Callable[[Path, Path], None] | None = None,
) -> dict[str, Any]:
    # Resolved lazily so tests can monkeypatch ``render_pdf_from_html``.
    if pdf_renderer is None and not no_pdf:
        pdf_renderer = render_pdf_from_html
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    produced_at = _utc_stamp(now)

    if kind == "current":
        end = end_date or (today - timedelta(days=1))
        start = end - timedelta(days=CURRENT_DAYS - 1)
        window_label = end.isoformat()
        prev = (start - timedelta(days=CURRENT_DAYS), start - timedelta(days=1))
        wide_start = end - timedelta(days=CURRENT_WIDE_DAYS - 1)
        extra_windows = {
            "28d": (
                (wide_start, end),
                (wide_start - timedelta(days=CURRENT_WIDE_DAYS), wide_start - timedelta(days=1)),
            )
        }
        history_from = start
    elif kind == "weekly":
        if week_start is not None:
            if week_start.weekday() != 0:
                raise ValueError(
                    f"--week-start must be a Monday, got {week_start.isoformat()}"
                )
            start = week_start
            end = start + timedelta(days=6)
        else:
            start, end = select_finalized_week(today)
        window_label = f"{start.isoformat()}_{end.isoformat()}"
        prev = (start - timedelta(days=7), start - timedelta(days=1))
        extra_windows = None
        history_from = start
    else:
        raise ValueError(f"unknown export kind: {kind!r}")

    try:
        root = _verify_layout(out_dir)
    except _ExportLayoutUnsafe as exc:
        return {
            "kind": kind,
            "tenant": project_id,
            "ok": False,
            "unchanged": False,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "error": {
                "code": "EXPORT_LAYOUT_UNSAFE",
                "message": (
                    "Export root contains an unsafe directory; "
                    "nothing was written."
                ),
                "details": {"path": str(exc.path), "reason": exc.reason},
            },
        }

    history = [
        build_growth(storage, project_id=project_id, start=w_start, end=w_end, channels=channels)
        for w_start, w_end in _history_weeks(history_from)
    ]
    growth = build_growth(storage, project_id=project_id, start=start, end=end, channels=channels)
    previous = build_growth(
        storage, project_id=project_id, start=prev[0], end=prev[1], channels=channels
    )
    extras = None
    if extra_windows:
        extras = {
            name: (
                build_growth(storage, project_id=project_id, start=w[0], end=w[1], channels=channels),
                build_growth(storage, project_id=project_id, start=p[0], end=p[1], channels=channels),
            )
            for name, (w, p) in extra_windows.items()
        }
    growth["derived"] = derive_growth(growth, previous, history, extra_windows=extras)
    if kind == "current" and locale == "ru":
        # Raw comparison/detail windows are part of the immutable identity too.
        # Each window's KPIs reuse exactly the same derive_growth calculations.
        for wide, wide_previous in (extras or {}).values():
            wide["derived"] = derive_growth(wide, wide_previous, history)
        growth["dashboard"] = {
            "previous": previous,
            "history": history,
            **{
                name: {"current": wide, "previous": wide_previous}
                for name, (wide, wide_previous) in (extras or {}).items()
            },
        }
    if "dashboard" in growth:
        growth["dashboard"]["keyword_clusters"] = _keyword_clusters(keyword_sets or [], markets)
    from seo_observer.serp_storage import load_measurements
    growth["serp"] = load_measurements(storage, project_id, through=end.isoformat())
    growth["generated_at"] = produced_at
    digest = growth_hash(growth)
    # The immutable build identity covers the render options too: an export
    # produced with --no-pdf must not satisfy a later run that wants a PDF,
    # and a changed panel_url or locale must produce a new build directory.
    render_options: dict[str, Any] = {
        "locale": locale, "dashboard_version": 14, "panel": asdict(panel)
    }
    if kind == "weekly":
        render_options["pdf"] = not no_pdf
        render_options["panel_url"] = panel_url
    build_key = json.dumps(
        {"growth_hash": digest, "options": render_options},
        sort_keys=True,
        separators=(",", ":"),
    )
    hash12 = hashlib.sha256(build_key.encode("utf-8")).hexdigest()[:12]

    builds_dir = out_dir / "builds"
    build_name = f"{kind}-{window_label}-{hash12}"
    build_dir = builds_dir / build_name
    base = {
        "kind": kind,
        "tenant": project_id,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "growth_hash": digest,
    }

    # ``current`` never renders a PDF; the marker is "disabled", not a failure.
    pdf_state: dict[str, Any] = {"ok": False, "error": "disabled"}
    files: list[str] = ["growth.json", "index.html"]
    if kind == "weekly":
        files.append("brief.md")

    def receipt(*, pdf: dict[str, Any], files_written: list[str]) -> dict[str, Any]:
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "tenant": project_id,
            "kind": kind,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "produced_at": produced_at,
            "growth_hash": digest,
            "files": files_written,
            "pdf": pdf,
            "sources": {
                name: {
                    "state": entry.get("state"),
                    "required": bool(entry.get("required")),
                    "collected_at": entry.get("collected_at"),
                    "timezone": entry.get("timezone"),
                }
                for name, entry in sorted((growth.get("sources") or {}).items())
            },
        }

    if build_dir.is_dir():
        # Same data version already published: refresh links only.
        unchanged = _links_current(out_dir, kind, window_label, build_name)
        publish_error = _publish_or_receipt(
            out_dir, kind, window_label, start, build_name, now, base,
            receipt(pdf={"ok": False, "error": None}, files_written=[]),
        )
        if publish_error is not None:
            return publish_error
        _apply_retention(out_dir, now=now, root=root)
        return {**base, "ok": True, "unchanged": unchanged,
                "build": str(build_dir), "receipt_path": str(build_dir / "receipt.json")}

    builds_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = builds_dir / f".tmp-{uuid.uuid4().hex}"
    tmp_dir.mkdir()
    try:
        growth_text = (
            json.dumps(growth, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        (tmp_dir / "growth.json").write_text(growth_text, encoding="utf-8")
        title = f"{project_id} growth {start.isoformat()} — {end.isoformat()}"
        if kind == "current" and locale == "ru":
            pages = render_dashboard_pages(
                growth, title=panel.title, generated_at=produced_at, panel=panel
            )
            for name, html_text in pages.items():
                path = tmp_dir / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(html_text, encoding="utf-8")
            files.extend(name for name in pages if name != "index.html")
        else:
            html_text = render_panel_html(
                growth, title=title, generated_at=produced_at, locale=locale
            )
            (tmp_dir / "index.html").write_text(html_text, encoding="utf-8")
        if kind == "weekly":
            brief_text = render_brief(growth, panel_url=panel_url, locale=locale)
            (tmp_dir / "brief.md").write_text(brief_text, encoding="utf-8")
            if no_pdf:
                pdf_state = {"ok": False, "error": "disabled"}
            elif pdf_renderer is not None:
                try:
                    pdf_renderer(tmp_dir / "index.html", tmp_dir / "report.pdf")
                except Exception as exc:
                    pdf_state = {
                        "ok": False,
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                else:
                    pdf_state = {"ok": True, "error": None}
            else:
                pdf_state = {"ok": False, "error": "disabled"}
        final_files = list(files)
        if pdf_state["ok"]:
            final_files.insert(2, "report.pdf")
        final_files.append("receipt.json")
        (tmp_dir / "receipt.json").write_text(
            json.dumps(receipt(pdf=pdf_state, files_written=final_files),
                       ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        if kind == "weekly" and not pdf_state["ok"] and not no_pdf:
            raise _ExportPdfFailed(pdf_state["error"])
        os.rename(tmp_dir, build_dir)
    except _ExportPdfFailed:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        failed_path = _write_failed_receipt(
            out_dir, kind, window_label, now, receipt(pdf=pdf_state, files_written=files)
        )
        _apply_retention(out_dir, now=now, root=root)
        return {
            **base,
            "ok": False,
            "unchanged": False,
            "failed_receipt_path": str(failed_path),
            "error": {
                "code": "EXPORT_PDF_FAILED",
                "message": "Weekly export was not published: PDF rendering failed.",
                "details": {"pdf": pdf_state},
            },
        }
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        failed_path = _write_failed_receipt(
            out_dir,
            kind,
            window_label,
            now,
            {
                **receipt(pdf={"ok": False, "error": None}, files_written=[]),
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
            },
        )
        _apply_retention(out_dir, now=now, root=root)
        return {
            **base,
            "ok": False,
            "unchanged": False,
            "failed_receipt_path": str(failed_path),
            "error": {
                "code": "EXPORT_BUILD_FAILED",
                "message": "Export build failed before publication.",
                "details": {"error_type": exc.__class__.__name__, "error": str(exc)},
            },
        }

    publish_error = _publish_or_receipt(
        out_dir, kind, window_label, start, build_name, now, base,
        receipt(pdf=pdf_state, files_written=final_files),
    )
    if publish_error is not None:
        return publish_error
    _apply_retention(out_dir, now=now, root=root)
    return {
        **base,
        "ok": True,
        "unchanged": False,
        "build": str(build_dir),
        "receipt_path": str(build_dir / "receipt.json"),
        "files": final_files,
        "pdf": pdf_state,
    }


class _ExportPdfFailed(RuntimeError):
    pass


class _ExportLayoutUnsafe(RuntimeError):
    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def _links_current(out_dir: Path, kind: str, window_label: str, build_name: str) -> bool:
    """True when every link for this export already points at the build."""
    if kind == "current":
        return _link_build_name(out_dir / "current") == build_name
    week_link = out_dir / "weekly" / window_label
    return (
        _link_build_name(week_link) == build_name
        and _link_build_name(out_dir / "latest-weekly") is not None
    )


def _planned_swaps(
    out_dir: Path,
    kind: str,
    window_label: str,
    start: date,
    build_name: str,
) -> list[tuple[Path, str]]:
    """All link swaps for this export, in order."""
    if kind == "current":
        return [(out_dir / "current", f"builds/{build_name}")]
    weekly_dir = out_dir / "weekly"
    swaps = [(weekly_dir / window_label, f"../builds/{build_name}")]
    latest_name = build_name
    latest_start = start
    if weekly_dir.is_dir():
        for entry in sorted(weekly_dir.iterdir()):
            if not entry.is_symlink() or entry.name == window_label:
                continue
            try:
                entry_start = date.fromisoformat(entry.name.split("_", 1)[0])
            except ValueError:
                continue
            if entry_start > latest_start:
                latest_start = entry_start
                latest_name = Path(os.readlink(entry)).name
    swaps.append((out_dir / "latest-weekly", f"builds/{latest_name}"))
    return swaps


def _publish_links(swaps: list[tuple[Path, str]]) -> None:
    """Apply every swap; on any failure restore prior links and clean temps."""
    swapped: list[tuple[Path, str | None]] = []
    try:
        for link, target in swaps:
            if link.is_symlink():
                previous: str | None = os.readlink(link)
            elif link.exists():
                raise OSError(f"refusing to replace non-symlink {link}")
            else:
                previous = None
            _swap_link(link, target)
            swapped.append((link, previous))
    except Exception:
        for link, previous in reversed(swapped):
            try:
                if previous is None:
                    link.unlink()
                else:
                    _swap_link(link, previous)
            except OSError:
                pass
        for link, _target in swaps:
            if not link.parent.is_dir():
                continue
            for tmp in link.parent.glob(f"{link.name}.tmp-*"):
                try:
                    if tmp.is_symlink():
                        tmp.unlink()
                except OSError:
                    pass
        raise


def _publish_or_receipt(
    out_dir: Path,
    kind: str,
    window_label: str,
    start: date,
    build_name: str,
    now: datetime,
    base: dict[str, Any],
    failure_receipt: dict[str, Any],
) -> dict[str, Any] | None:
    """Swap all publication links; return a failure payload or ``None``."""
    try:
        _publish_links(_planned_swaps(out_dir, kind, window_label, start, build_name))
    except Exception as exc:
        failed_path = _write_failed_receipt(
            out_dir,
            kind,
            window_label,
            now,
            {
                **failure_receipt,
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
            },
        )
        _apply_retention(out_dir, now=now, root=Path(os.path.realpath(out_dir)))
        return {
            **base,
            "ok": False,
            "unchanged": False,
            "failed_receipt_path": str(failed_path),
            "error": {
                "code": "EXPORT_PUBLISH_FAILED",
                "message": (
                    "Export build completed but publication failed; "
                    "previous links were restored."
                ),
                "details": {"error_type": exc.__class__.__name__, "error": str(exc)},
            },
        }
    return None


def _write_failed_receipt(
    out_dir: Path,
    kind: str,
    window_label: str,
    now: datetime,
    receipt: dict[str, Any],
) -> Path:
    failed_dir = out_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    path = failed_dir / f"{kind}-{window_label}-{_utc_compact(now)}.receipt.json"
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _apply_retention(out_dir: Path, *, now: datetime, root: Path) -> None:
    builds = out_dir / "builds"
    if builds.is_dir():
        current_linked = _link_build_name(out_dir / "current")
        current_builds = sorted(
            (
                p
                for p in builds.iterdir()
                if p.name.startswith("current-")
                and p.name != current_linked
                and _is_real_dir_inside(p, root)
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in current_builds[CURRENT_KEEP_UNLINKED:]:
            shutil.rmtree(stale, ignore_errors=True)

        weekly_linked = set()
        weekly_dir = out_dir / "weekly"
        if weekly_dir.is_dir():
            for entry in weekly_dir.iterdir():
                name = _link_build_name(entry)
                if name:
                    weekly_linked.add(name)
        latest = _link_build_name(out_dir / "latest-weekly")
        if latest:
            weekly_linked.add(latest)
        weekly_cutoff = now.timestamp() - WEEKLY_RETENTION_DAYS * 86400
        for entry in builds.iterdir():
            if (
                entry.name.startswith("weekly-")
                and entry.name not in weekly_linked
                and _is_real_dir_inside(entry, root)
                and entry.stat().st_mtime < weekly_cutoff
            ):
                shutil.rmtree(entry, ignore_errors=True)

    failed_dir = out_dir / "failed"
    if failed_dir.is_dir():
        cutoff = now.timestamp() - FAILED_RETENTION_DAYS * 86400
        for entry in failed_dir.iterdir():
            if (
                _is_real_file_inside(entry, root)
                and entry.stat().st_mtime < cutoff
            ):
                entry.unlink()
