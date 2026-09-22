from __future__ import annotations

import html
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from seo_hub.auth import Principal, require_scope
from seo_hub.client import HubAPI


TEMPLATE_DIR = Path(__file__).parent / "templates"
LABELS = {
    "observer": "SEO-отчёт", "credentials": "Доступ к данным",
    "elmo": "Elmo", "openseo": "OpenSEO",
    "observer.report": "SEO-отчёт", "observer.actions": "Рекомендации SEO",
    "elmo.ai_visibility": "Видимость в ИИ (Elmo)",
    "openseo.evidence": "Данные поиска (OpenSEO)",
    "queued": "В очереди", "running": "Выполняется", "succeeded": "Завершён",
    "partial": "Частично", "failed": "Ошибка", "cancelled": "Отменён",
    "skipped": "Пропущен", "complete": "Полные", "stale": "Устаревшие",
    "not_comparable": "Несопоставимые", "missing": "Нет данных",
    "google_search_console": "Google Search Console", "yandex_metrica": "Яндекс Метрика",
    "yandex_webmaster": "Яндекс Вебмастер", "ga4": "Google Analytics",
    "serp": "Поисковая выдача", "observer.snapshot": "Снимок SEO",
    "observer.doctor": "Проверка настроек", "observer.ai-readiness": "Готовность к ИИ-поиску",
    "comparable": "Сопоставимые", "insufficient_coverage": "Недостаточное покрытие",
    "provisional": "Предварительные выводы", "confirmed": "Подтверждено", "unknown": "Не подтверждено",
}


def render_index(api: HubAPI, principal: Principal) -> str:
    require_scope(principal, "ui")
    payload = api.projects(principal)
    rows = []
    for project in payload["projects"]:
        status = api.project_status(project["id"], principal)
        readiness = _readiness(status["readiness"], status["history"])
        latest = status["latest_run"]
        latest_cell = _run_link(project["id"], latest) if latest else "Запусков пока нет"
        links = " ".join(filter(None, (
            _external_link(project['deep_links']['elmo'], 'Elmo'),
            _external_link(project['deep_links']['openseo'], 'OpenSEO'),
        ))) or '<span class="muted">Нет доступных ссылок</span>'
        rows.append(
            "<tr>"
            f"<td><a href=\"/projects/{_segment(project['id'])}\">{_esc(project['label'])}</a></td>"
            f"<td>{readiness}</td><td>{latest_cell}</td>"
            f"<td>{links}</td>"
            "</tr>"
        )
    return _page("Проекты", '<h1>Проекты</h1><table><thead><tr>'
                 '<th scope="col">Проект</th><th scope="col">Источники данных</th>'
                 '<th scope="col">Последний запуск</th><th scope="col">Сервисы</th>'
                 '</tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def render_project(api: HubAPI, principal: Principal, project_id: str) -> str:
    require_scope(principal, "ui")
    payload = api.project_status(project_id, principal)
    history = "".join(
        f"<li>{_run_link(project_id, run)}</li>"
        for run in payload["history"]
    )
    readiness = _readiness(payload["readiness"], payload["history"])
    latest = payload.get("latest_run")
    selected = None
    first = None
    for run in payload["history"]:
        candidate = api.report(project_id, principal, run_id=run["run_id"])
        if first is None:
            first = candidate
        if _has_renderable_report(candidate.get("report")):
            selected = candidate
            break
    selected = selected or first
    overview = _report_body(selected) if selected else ""
    if latest and selected and latest["run_id"] != selected["run"]["run_id"]:
        overview = f'<p class="notice">Последний запуск: {_label(latest["state"])}. Отчёта нет; показана предыдущая сводка.</p>' + overview
    body = (f"<h1>{_esc(payload['project']['label'])}</h1>" + overview
            + f'<section><h2>Подключения</h2>{readiness}</section>'
            + '<details class="history"><summary>История запусков</summary>'
            + (f'<ul class="run-history">{history}</ul>' if history else '<p>Запусков пока нет.</p>') + '</details>')
    return _page(payload["project"]["label"], _project_nav(api) + body)


def render_run(api: HubAPI, principal: Principal, project_id: str, run_id: str) -> str:
    require_scope(principal, "ui")
    payload = api.report(project_id, principal, run_id=run_id)
    label = next(project.label for project in api.registry.projects if project.id == project_id)
    body = (
        _project_nav(api)
        + f"<p><a href=\"/projects/{_segment(project_id)}\">История проекта</a></p>"
        + f"<h1>{_esc(label)}</h1>" + _report_body(payload)
    )
    return _page(run_id, body)


def _number(value: Any, *, percent: bool = False) -> str:
    if type(value) not in (int, float) or not math.isfinite(value):
        return "Нет данных"
    return f"{value * 100 if percent else value:,.2f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",") + ("%" if percent else "")


def _evidence_table(entries: list[dict[str, Any]]) -> str:
    rows = []
    ranks = []
    for entry in entries:
        name = _label(entry.get("source", "missing"))
        basket = entry.get("keyword_set_id") or entry.get("reporting_period_id")
        if basket:
            name += f'<small>{_esc(basket)}</small>'
        quality = entry.get("quality", "missing")
        tone = "warning" if quality in {"stale", "missing", "not_comparable"} else "neutral"
        observed = _date(entry["observed_at"]) if isinstance(entry.get("observed_at"), str) else "Дата неизвестна"
        start, end = entry.get("effective_start"), entry.get("effective_end")
        if start or end:
            observed += f'<small>Период: {_esc(start or "?")} — {_esc(end or "?")}</small>'
        coverage = entry.get("coverage") if isinstance(entry.get("coverage"), dict) else {}
        ratio = coverage.get("coverage")
        coverage_html = _number(ratio, percent=True)
        if type(ratio) in (float, int) and math.isfinite(ratio) and 0 <= ratio <= 1:
            coverage_html += f'<progress max="1" value="{ratio}" aria-label="Покрытие"></progress>'
        if coverage.get("observed_slots") is not None and coverage.get("expected_slots") is not None:
            coverage_html += f'<small>{_number(coverage["observed_slots"])} / {_number(coverage["expected_slots"])}</small>'
        count = _number(entry.get("row_count"))
        rows.append(f'<tr><th scope="row">{name}</th><td><span class="status {tone}">{_label(quality)}</span></td>'
                    f'<td>{observed}</td><td>{coverage_html}</td><td>{count}</td></tr>')
        owned = entry.get("owned") if isinstance(entry.get("owned"), list) else []
        for index, metrics in enumerate(owned):
            if not isinstance(metrics, dict):
                continue
            ranks.append(f'<tr><th scope="row">{name}' + (f'<small>Объект {index + 1}</small>' if len(owned) > 1 else '')
                         + f'</th><td>{_number(metrics.get("best_rank"))}</td><td>{_number(metrics.get("median_rank"))}</td>'
                         + f'<td>{_number(metrics.get("coverage"), percent=True)}</td>'
                         + f'<td>{_label(entry.get("comparability", "unknown"))}<small>{_label(entry.get("conclusion_status", "unknown"))}</small></td></tr>')
    body = '<section><h2>Данные источников</h2>' + _table(
        ("Источник / выборка", "Качество", "Дата измерения", "Покрытие", "Записей"), rows) + '</section>'
    if ranks:
        body += '<section><h2>Позиции в поиске</h2>' + _table(
            ("Выборка", "Лучшая позиция", "Медианная позиция", "Покрытие", "Ограничения выводов"), ranks) + '</section>'
    return body


def _table(headings: tuple[str, ...], rows: list[str]) -> str:
    return '<div class="table-scroll" tabindex="0" role="region" aria-label="' + _esc(headings[0]) + '"><table class="evidence-table"><thead><tr>' + ''.join(
        f'<th scope="col">{heading}</th>' for heading in headings) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


def _evidence_entries(report: Any) -> list[dict[str, Any]]:
    entries = report.get("evidence") if isinstance(report, dict) and report.get("schema") == "seo-hub.observer_evidence.v1" else None
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _summary_text(report: Any) -> str | None:
    if not isinstance(report, dict):
        return None
    summary = report.get("summary")
    if isinstance(summary, str):
        return summary
    # `summary: []` is not a summary: without the emptiness check it renders as an
    # empty string, which counts as "renderable" and stops the search for an older
    # report that actually has text.
    if isinstance(summary, list) and summary and all(isinstance(part, str) for part in summary):
        return "\n\n".join(summary)
    return None


def _has_renderable_report(report: Any) -> bool:
    return bool(_evidence_entries(report)) or _summary_text(report) is not None


def _report_body(payload: dict[str, Any]) -> str:
    run = payload["run"]
    report = payload.get("report")
    body = f'<p class="run-meta">Сводка от {_date(run["created_at"])} · {_label(run["state"])}</p>'
    entries = _evidence_entries(report)
    summary = _summary_text(report)
    if entries:
        stale = sum(entry.get("quality") == "stale" for entry in entries)
        missing = sum(entry.get("quality", "missing") == "missing" for entry in entries)
        body += '<dl class="metrics">' + ''.join(
            f'<div><dt>{label}</dt><dd>{value}</dd></div>' for label, value in (
                ("Наборов данных", len(entries)), ("Устаревших", stale), ("Без подтверждённых данных", missing))) + '</dl>'
        if stale or missing:
            body += '<p class="notice">Есть устаревшие или неподтверждённые данные. Текущие результаты по ним не определены.</p>'
        body += _evidence_table(entries)
    elif summary is not None:
        body += '<section><h2>Отчёт</h2>' + ''.join(f'<p>{_esc(part)}</p>' for part in summary.split("\n\n") if part.strip()) + '</section>'
    else:
        body += '<p title="No report content available">Для этого запуска пока нет текста отчёта.</p>'
    source_rows = []
    for name, source in run.get("sources", {}).items():
        error = source.get("error")
        error_text = error.get("message", error.get("code", "Ошибка источника")) if isinstance(error, dict) else error
        source_rows.append(f'<tr title="{_esc(name)}: {_esc(source["status"])}, quality {_esc(source["quality"])}">'
                           f'<th scope="row">{_label(name)}</th><td>{_source_label(source)}'
                           + (f'<p class="source-error">{_esc(error_text)}</p>' if error_text else '') + '</td></tr>')
    body += '<section><h2>Состояние проверок</h2>' + _table(("Проверка", "Результат"), source_rows) + '</section>'
    body += '<details class="technical"><summary>Технические данные</summary><pre>' + _esc(
        json.dumps(payload, ensure_ascii=False, indent=2)) + '</pre></details>'
    return body


def _page(title: str, body: str) -> str:
    template = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")
    return template.replace("{{ title }}", _esc(title)).replace("{{ body }}", body)


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _segment(value: Any) -> str:
    return quote(str(value), safe="")


def _project_nav(api: HubAPI) -> str:
    links = "".join(
        f"<a href=\"/projects/{_segment(project.id)}\">{_esc(project.label)}</a>"
        for project in api.registry.projects
    )
    return f'<nav aria-label="Проекты"><a href="/">Все проекты</a>{links}</nav>'


def _external_link(url: str, label: str) -> str:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        safe = (parsed.scheme in {"http", "https"} and bool(hostname)
                and hostname != "invalid" and not hostname.endswith(".invalid"))
    except ValueError:
        safe = False
    if not safe:
        return ""
    return f'<a href="{_esc(url)}">{_esc(label)}</a>'


def _label(value: str) -> str:
    return _esc(LABELS.get(value, value))


def _source_label(source: dict[str, Any]) -> str:
    if source["status"] == "succeeded" and source["quality"] == "missing":
        return "Подключено, нет измерений"
    return f"{_label(source['status'])}. Данные: {_label(source['quality'])}"


def _readiness(values: dict[str, bool], history: list[dict[str, Any]]) -> str:
    source_names = {"observer": "observer.report", "elmo": "elmo.ai_visibility", "openseo": "openseo.evidence"}
    rows = []
    for key, value in values.items():
        name = source_names.get(key)
        source = next((run["sources"][name] for run in history if name in run.get("sources", {})), None)
        label = _source_label(source) if source else ("Готово" if value else "Не настроено")
        available = (source["status"] == "succeeded" and source["quality"] != "missing") if source else value
        rows.append(
            f'<li title="{_esc(key)}:{"ready" if value else "missing"}">'
            f'{_label(key)}: <span class="{"available" if available else "muted"}">{label}</span></li>'
        )
    return '<ul class="readiness">' + "".join(rows) + '</ul>'


def _date(value: str) -> str:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _esc(value)
    zone = instant.strftime("%z")
    zone_label = "UTC" if zone == "+0000" else (f"UTC{zone[:3]}:{zone[3:]}" if zone else "")
    return f'<time datetime="{_esc(value)}">{instant:%d.%m.%Y %H:%M} {zone_label}</time>'


def _run_link(project_id: str, run: dict[str, Any]) -> str:
    return (
        f'<a href="/projects/{_segment(project_id)}/runs/{_segment(run["run_id"])}">{_esc(run["run_id"])}</a>'
        f'<div class="run-meta" title="{_esc(run["state"])}">{_label(run["state"])} · {_date(run["created_at"])}</div>'
    )
