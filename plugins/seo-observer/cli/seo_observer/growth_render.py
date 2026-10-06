"""growth_v1 renderers: self-contained HTML panel, inline SVG chart, brief text.

Pure functions over ``build_growth``/``derive_growth`` output — no network, no
JS, no external resources. Every string coming from data is HTML-escaped; the
brief is plain Markdown without Telegram HTML tags.
"""

from __future__ import annotations

import html
import heapq
from datetime import datetime
from typing import Any

from seo_observer.growth import _fully_covered, _kpi_source_sets
from seo_observer.money import format_minor


_KPI_ORDER = (
    "nonbrand_impressions",
    "nonbrand_clicks",
    "organic_visits",
    "registrations",
    "payments",
    "revenue_minor",
    "visit_to_signup",
)
_TREND_METRIC = "organic_visits"
_TOP_QUERIES = 10
_TOP_PAGES = 10

_STRINGS = {
    "ru": {
        "section_summary": "Итог",
        "section_channels": "Каналы и воронка",
        "section_search": "Поиск",
        "section_pages": "Страницы",
        "section_actions": "Действия",
        "section_data": "Данные",
        "no_data": "нет данных",
        "excluded_noise": "исключено как шум: {visits} визитов",
        "mixpanel_sample": "по Mixpanel, выборка {n} из {m}",
        "by_registration_channel": "по каналу регистрации",
        "estimate": "оценка",
        "banner_not_live": "Не все обязательные источники живые: {sources}",
        "window_7d": "7 дней",
        "window_28d": "28 дней",
        "delta_prev": "к прошлому",
        "delta_avg4": "к среднему 4",
        "what_changed": "Что изменилось",
        "generated_at": "Сформировано: {at}",
        "metric": "Показатель",
        "nonbrand_impressions": "Показы (небренд)",
        "nonbrand_clicks": "Клики (небренд)",
        "organic_visits": "Визиты из органики",
        "revenue_minor": "Выручка",
        "visit_to_signup": "Визит → регистрация",
        "channel": "Канал",
        "visits": "Визиты",
        "registrations": "Регистрации",
        "payments": "Оплаты",
        "impressions": "Показы",
        "clicks": "Клики",
        "position": "Позиция",
        "query": "Запрос",
        "page": "Страница",
        "brand": "бренд",
        "nonbrand": "небренд",
        "trend": "Тренд 12 недель: органика",
        "source": "Источник",
        "state": "Состояние",
        "required": "Обязательный",
        "collected_at": "Собрано",
        "timezone": "Таймзона",
        "days_covered": "Покрыто дней",
        "action_date": "Дата",
        "action_type": "Тип",
        "action_description": "Описание",
        "verdict": "Вердикт",
        "relative_delta": "Эффект",
        "change_from_to": "{frm} → {to}",
        "kind_query": "запрос",
        "kind_page": "страница",
        "kind_channel": "канал",
        "yes": "да",
        "no": "нет",
        "brief_header": "Итог за {start} — {end}",
        "brief_nonbrand": "Небренд: {imp} показов, {clk} кликов ({delta})",
        "brief_brand": "Бренд: {imp} показов, {clk} кликов",
        "brief_organic": "Органика: {visits} визитов",
        "brief_registrations": "Регистрации: {count} ({sample})",
        "brief_payments": "Оплаты: {count}, выручка: {revenue}",
        "brief_visit_to_signup": "Визит → регистрация: {value}{estimate}",
        "brief_panel": "Панель: {url}",
        "brief_no_data": "нет данных",
        "unsupported_currency": "валюта не поддерживается ({currency})",
        "revenue_currency": "{amount} {currency}",
        "coverage_partial": "неполные данные",
        "search_data_through": "данные поиска по {date} ({source})",
        "channel_direct": "Прямые",
        "channel_app": "Приложение",
        "channel_organic_search": "Органический поиск",
        "channel_organic_social": "Соцсети",
        "channel_paid_search": "Платный поиск",
        "channel_paid_social": "Платные соцсети",
        "channel_referral": "Переходы",
        "channel_email": "Почта",
        "channel_ai_assistant": "AI-ассистенты",
        "channel_internal": "Внутренние",
        "channel_unassigned": "Без канала",
        "action_content": "контент",
        "action_technical": "техническое",
        "verdict_positive": "позитивный",
        "verdict_negative": "негативный",
        "verdict_neutral": "нейтральный",
        "verdict_inconclusive": "неубедительный",
        "verdict_not_ready": "ещё рано",
        "brief_sources": "Источники: {status}",
        "sources_all_live": "все живые",
        "brief_coverage": "Покрытие: {covered} из {days} дней",
        "brief_top_channels": "Топ каналов: {channels}",
        "dashboard_summary": "Сводка",
        "dashboard_positions": "Позиции",
        "dashboard_demand": "Спрос и страницы",
        "dashboard_traffic": "Трафик",
        "dashboard_money": "Регистрации и деньги",
        "dashboard_yandex": "Яндекс",
        "dashboard_metrica": "Яндекс.Метрика",
        "dashboard_auth": "База: регистрации",
        "dashboard_pay": "База: оплаты",
        "dashboard_empty_table": "<p class=\"caption\">Нет данных за выбранный период</p>",
        "dashboard_no_data": "Нет данных",
        "dashboard_unknown_currency": "Валюта неизвестна",
        "dashboard_search_visits": "Визиты из поиска",
        "dashboard_complete": "Данные полные",
        "dashboard_partial": "Часть данных",
        "dashboard_kpi_card": "<a href=\"{0}\"><b class=\"value\">{1}</b></a><p>Было {2} → стало {3}</p><p>{4} · {5}</p><p class=\"caption\">Покрыто дней: {6}</p>",
        "dashboard_trend_note": "<p class=\"caption\">Последние 12 недель. Пропуски не заменяются нулём; доступные значения могут быть неполными.</p>",
        "dashboard_empty": "<p>Нет данных</p>",
        "dashboard_trends": "Как менялись показатели",
        "dashboard_unmeasured": "Не замерено",
        "dashboard_beyond_100": "За 100",
        "dashboard_query_filter": "<div class=\"filters\"><label>Найти запрос <input data-filter type=\"search\"></label>",
        "dashboard_engine_filter": "<label>Поисковик <select data-engine-filter><option value=\"\">Все</option><option value=\"google_search_console\">Google</option><option value=\"yandex_webmaster\">Яндекс</option></select></label>",
        "dashboard_position_filter": "<label>Средняя позиция <select data-bucket-filter><option value=\"\">Все</option>",
        "dashboard_positions_note": "<p class=\"caption\">Средняя позиция взвешена по показам. Это данные кабинетов Google/Яндекса, а не отдельный замер выдачи. Таблицы — доступная выборка; их суммы не равны общим итогам поиска.</p>",
        "dashboard_buckets_note": "<p class=\"caption\">Группы описывают средние позиции, не долю сайта в ТОПе. Непоказанные запросы не считаются «за 100». Кластеры в этом экспорте не заданы.</p>",
        "dashboard_unknown_date": "неизвестную дату",
        "dashboard_search_through": "<p class=\"caption\">Данные таблиц по {0}. Всего запросов: {1}. ",
        "dashboard_observed_changes": "Изменения относятся к наблюдаемой выборке.</p>",
        "dashboard_incomparable": "Периоды неполные или свежесть не подтверждена: изменение не вычисляется.</p>",
        "dashboard_average_position": "Средняя позиция",
        "dashboard_query_count": "Запросов",
        "dashboard_before": "Было",
        "dashboard_after": "Стало",
        "dashboard_improvement": "Улучшение",
        "dashboard_impressions_change": "Показы: было → стало",
        "dashboard_clicks_change": "Клики: было → стало",
        "dashboard_queries_limit": "<p class=\"caption\">Показаны 500 запросов с наибольшим числом показов; распределение рассчитано по всей выборке.</p>",
        "dashboard_opportunities": "<h3>Возможности роста</h3><p class=\"caption\">Эвристика: ≥20 показов, средняя позиция 4–20 и CTR &lt;5%. Это список для проверки, не прогноз.</p>",
        "dashboard_pages_heading": "<h3>Страницы</h3>",
        "dashboard_query_pages_missing": "<p class=\"caption\">Связь страницы с запросами не собирается в этом источнике.</p>",
        "dashboard_page_queries_limit": "<p class=\"caption\">Показаны 100 из {0} запросов с наибольшим числом показов.</p>",
        "dashboard_pages_missing": "<p>Нет данных о страницах</p>",
        "dashboard_pages_limit": "<p class=\"caption\">Показаны 100 страниц с наибольшим числом показов.</p>",
        "dashboard_other_channel": "Другой канал",
        "dashboard_channels": "Каналы",
        "dashboard_visits_before": "Было визитов",
        "dashboard_visits_after": "Стало визитов",
        "dashboard_traffic_note": "<p class=\"caption\">Наблюдаемые визиты. Визиты можно складывать; уникальных пользователей по страницам нельзя складывать. Пустые страницы входа не входят в список страниц, но учитываются в каналах. Ноль без строки показан только при полном покрытии периода.</p>",
        "dashboard_app_visits": "<p>{0} визитов в продукт. Исключены из привлечения.</p>",
        "dashboard_app_missing": "<p>Отдельных данных нет. Разделение требует настроенных путей приложения.</p>",
        "dashboard_sources": "Источники",
        "dashboard_source_medium": "Источник / тип перехода",
        "dashboard_landing_pages": "Страницы входа",
        "dashboard_landing_note": "<p class=\"caption\">До 100 страниц с наибольшим числом визитов; без объединения со срезом источников, такой связи в экспорте нет.</p>",
        "dashboard_noise": "<p class=\"caption\">Исключено как шум: {0} визитов.</p>",
        "dashboard_other_outcome": "Другой результат",
        "dashboard_all_channels": "Все каналы",
        "dashboard_conversion_note": "<p class=\"caption\">Визит → регистрация — отношение общих регистраций к визитам по существующему контракту показателей, не атрибуция регистраций каналу.</p>",
        "dashboard_server_daily": "По дням · База",
        "dashboard_signup_channel": "Канал регистрации",
        "dashboard_count": "Количество",
        "dashboard_amount": "Сумма",
        "dashboard_payment_note": "<p class=\"caption\">Первые и повторные оплаты: разделение не собирается. Суммы разных валют не складываются.</p>",
        "dashboard_mixpanel_channels": "Каналы регистраций · Mixpanel",
        "dashboard_mixpanel_note": "<p class=\"caption\">Выборка событий, не полный итог базы. Не прибавляется к регистрациям.</p>",
        "dashboard_signup_events": "События регистрации",
        "dashboard_received": "Данные получены",
        "dashboard_stale": "Устарели",
        "dashboard_stale_suffix": " · Устарели",
        "dashboard_age_days": "{0} дней",
        "dashboard_covered_days": "{0} из {1}",
        "dashboard_freshness": "Свежесть источников",
        "dashboard_data_through": "Данные по",
        "dashboard_lag": "Задержка",
        "dashboard_collected": "Получено",
        "dashboard_timezone": "Часовой пояс",
        "dashboard_site_checks": "Проверки сайта",
        "dashboard_checks_missing": "<p>Диагностика Яндекса: данные не собираются</p><p class=\"caption\">Индексация, технический аудит и ссылки: нет подтверждённого набора в этом экспорте.</p>",
        "dashboard_summary_note": "<p class=\"caption\">Поиск — визиты GA4. Регистрации и оплаты — все каналы из базы; это не сквозная воронка.</p>",
        "dashboard_nav": "<nav aria-label=\"Разделы\">",
        "dashboard_7d_link": ">7 дней</a>",
        "dashboard_28d_link": ">28 дней</a>",
        "dashboard_window_comparison": "{0} — {1} против {2} — {3}",
        "dashboard_freshness_open": "<p class=\"caption\">Свежесть и полнота: ",
        "dashboard_source_freshness": "{0}: по {1}, {2}/{3} дней",
        "dashboard_status_link": ". <a href=\"status.html\">Состояние</a></p>",
        "dashboard_document": "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{0} · {1}</title><style>{2}{3}</style></head><body><main><div class=\"brand\">{4} · Рост</div>{5}<div class=\"periods\">{6}</div><h1>{7}</h1><p class=\"meta\">{8} · Собрано {9}</p>{10}{11}</main>{12}</body></html>",
    },
    "en": {
        "section_summary": "Summary",
        "section_channels": "Channels & funnel",
        "section_search": "Search",
        "section_pages": "Pages",
        "section_actions": "Actions",
        "section_data": "Data",
        "no_data": "no data",
        "excluded_noise": "excluded as noise: {visits} visits",
        "mixpanel_sample": "per Mixpanel, sample {n} of {m}",
        "by_registration_channel": "by registration channel",
        "estimate": "estimate",
        "banner_not_live": "Some required sources are not live: {sources}",
        "window_7d": "7 days",
        "window_28d": "28 days",
        "delta_prev": "vs previous",
        "delta_avg4": "vs avg of 4",
        "what_changed": "What changed",
        "generated_at": "Generated: {at}",
        "metric": "Metric",
        "nonbrand_impressions": "Impressions (non-brand)",
        "nonbrand_clicks": "Clicks (non-brand)",
        "organic_visits": "Organic visits",
        "revenue_minor": "Revenue",
        "visit_to_signup": "Visit → signup",
        "channel": "Channel",
        "visits": "Visits",
        "registrations": "Registrations",
        "payments": "Payments",
        "impressions": "Impressions",
        "clicks": "Clicks",
        "position": "Position",
        "query": "Query",
        "page": "Page",
        "brand": "brand",
        "nonbrand": "non-brand",
        "trend": "12-week trend: organic",
        "source": "Source",
        "state": "State",
        "required": "Required",
        "collected_at": "Collected at",
        "timezone": "Timezone",
        "days_covered": "Days covered",
        "action_date": "Date",
        "action_type": "Type",
        "action_description": "Description",
        "verdict": "Verdict",
        "relative_delta": "Effect",
        "change_from_to": "{frm} → {to}",
        "kind_query": "query",
        "kind_page": "page",
        "kind_channel": "channel",
        "yes": "yes",
        "no": "no",
        "brief_header": "Summary for {start} — {end}",
        "brief_nonbrand": "Non-brand: {imp} impressions, {clk} clicks ({delta})",
        "brief_brand": "Brand: {imp} impressions, {clk} clicks",
        "brief_organic": "Organic: {visits} visits",
        "brief_registrations": "Registrations: {count} ({sample})",
        "brief_payments": "Payments: {count}, revenue: {revenue}",
        "brief_visit_to_signup": "Visit → signup: {value}{estimate}",
        "brief_panel": "Panel: {url}",
        "brief_no_data": "no data",
        "unsupported_currency": "unsupported currency ({currency})",
        "revenue_currency": "{amount} {currency}",
        "coverage_partial": "partial data",
        "search_data_through": "search data through {date} ({source})",
        "channel_direct": "Direct",
        "channel_app": "App",
        "channel_organic_search": "Organic search",
        "channel_organic_social": "Organic social",
        "channel_paid_search": "Paid search",
        "channel_paid_social": "Paid social",
        "channel_referral": "Referral",
        "channel_email": "Email",
        "channel_ai_assistant": "AI assistants",
        "channel_internal": "Internal",
        "channel_unassigned": "Unassigned",
        "action_content": "content",
        "action_technical": "technical",
        "verdict_positive": "positive",
        "verdict_negative": "negative",
        "verdict_neutral": "neutral",
        "verdict_inconclusive": "inconclusive",
        "verdict_not_ready": "not ready yet",
        "brief_sources": "Sources: {status}",
        "sources_all_live": "all live",
        "brief_coverage": "Coverage: {covered} of {days} days",
        "brief_top_channels": "Top channels: {channels}",
        "dashboard_summary": "Summary",
        "dashboard_positions": "Positions",
        "dashboard_demand": "Demand and pages",
        "dashboard_traffic": "Traffic",
        "dashboard_money": "Registrations and revenue",
        "dashboard_yandex": "Yandex",
        "dashboard_metrica": "Yandex.Metrica",
        "dashboard_auth": "Database: registrations",
        "dashboard_pay": "Database: payments",
        "dashboard_empty_table": "<p class=\"caption\">No data for the selected period</p>",
        "dashboard_no_data": "No data",
        "dashboard_unknown_currency": "Unknown currency",
        "dashboard_search_visits": "Visits from search",
        "dashboard_complete": "Complete data",
        "dashboard_partial": "Partial data",
        "dashboard_kpi_card": "<a href=\"{0}\"><b class=\"value\">{1}</b></a><p>Previously {2} → now {3}</p><p>{4} · {5}</p><p class=\"caption\">Days covered: {6}</p>",
        "dashboard_trend_note": "<p class=\"caption\">Last 12 weeks. Missing values are not replaced with zero; available values may be incomplete.</p>",
        "dashboard_empty": "<p>No data</p>",
        "dashboard_trends": "How metrics changed",
        "dashboard_unmeasured": "Not measured",
        "dashboard_beyond_100": "Beyond 100",
        "dashboard_query_filter": "<div class=\"filters\"><label>Find a query <input data-filter type=\"search\"></label>",
        "dashboard_engine_filter": "<label>Search engine <select data-engine-filter><option value=\"\">All</option><option value=\"google_search_console\">Google</option><option value=\"yandex_webmaster\">Yandex</option></select></label>",
        "dashboard_position_filter": "<label>Average position <select data-bucket-filter><option value=\"\">All</option>",
        "dashboard_positions_note": "<p class=\"caption\">Average position is weighted by impressions. These are Google/Yandex console data, not separate SERP measurements. Tables show an available sample; their sums do not equal search totals.</p>",
        "dashboard_buckets_note": "<p class=\"caption\">Groups describe average positions, not the site's share of top results. Unreported queries are not counted as beyond 100. Clusters are not defined in this export.</p>",
        "dashboard_unknown_date": "an unknown date",
        "dashboard_search_through": "<p class=\"caption\">Table data through {0}. Total queries: {1}. ",
        "dashboard_observed_changes": "Changes refer to the observed sample.</p>",
        "dashboard_incomparable": "Periods are incomplete or freshness is unconfirmed: changes are not calculated.</p>",
        "dashboard_average_position": "Average position",
        "dashboard_query_count": "Queries",
        "dashboard_before": "Previously",
        "dashboard_after": "Now",
        "dashboard_improvement": "Improvement",
        "dashboard_impressions_change": "Impressions: previously → now",
        "dashboard_clicks_change": "Clicks: previously → now",
        "dashboard_queries_limit": "<p class=\"caption\">Showing the 500 queries with the most impressions; the distribution uses the full sample.</p>",
        "dashboard_opportunities": "<h3>Growth opportunities</h3><p class=\"caption\">Heuristic: ≥20 impressions, average position 4–20 and CTR &lt;5%. This is a checklist, not a forecast.</p>",
        "dashboard_pages_heading": "<h3>Pages</h3>",
        "dashboard_query_pages_missing": "<p class=\"caption\">Page-to-query relationships are not collected by this source.</p>",
        "dashboard_page_queries_limit": "<p class=\"caption\">Showing 100 of {0} queries with the most impressions.</p>",
        "dashboard_pages_missing": "<p>No page data</p>",
        "dashboard_pages_limit": "<p class=\"caption\">Showing the 100 pages with the most impressions.</p>",
        "dashboard_other_channel": "Other channel",
        "dashboard_channels": "Channels",
        "dashboard_visits_before": "Previous visits",
        "dashboard_visits_after": "Current visits",
        "dashboard_traffic_note": "<p class=\"caption\">Observed visits. Visits can be summed; unique users across pages cannot. Empty landing pages are omitted from the page list but included in channels. A missing row is shown as zero only with complete period coverage.</p>",
        "dashboard_app_visits": "<p>{0} product visits. Excluded from acquisition.</p>",
        "dashboard_app_missing": "<p>No separate data. This breakdown requires configured application paths.</p>",
        "dashboard_sources": "Sources",
        "dashboard_source_medium": "Source / medium",
        "dashboard_landing_pages": "Landing pages",
        "dashboard_landing_note": "<p class=\"caption\">Up to 100 pages with the most visits; not joined to the source breakdown, as that relationship is absent from the export.</p>",
        "dashboard_noise": "<p class=\"caption\">Excluded as noise: {0} visits.</p>",
        "dashboard_other_outcome": "Other outcome",
        "dashboard_all_channels": "All channels",
        "dashboard_conversion_note": "<p class=\"caption\">Visit → registration is total registrations divided by visits under the existing metric contract, not registration attribution to a channel.</p>",
        "dashboard_server_daily": "By day · Database",
        "dashboard_signup_channel": "Registration channel",
        "dashboard_count": "Count",
        "dashboard_amount": "Amount",
        "dashboard_payment_note": "<p class=\"caption\">First and repeat payments: the breakdown is not collected. Amounts in different currencies are not summed.</p>",
        "dashboard_mixpanel_channels": "Registration channels · Mixpanel",
        "dashboard_mixpanel_note": "<p class=\"caption\">Event sample, not the complete Database total. Not added to registrations.</p>",
        "dashboard_signup_events": "Registration events",
        "dashboard_received": "Data received",
        "dashboard_stale": "Stale",
        "dashboard_stale_suffix": " · Stale",
        "dashboard_age_days": "{0} days",
        "dashboard_covered_days": "{0} of {1}",
        "dashboard_freshness": "Source freshness",
        "dashboard_data_through": "Data through",
        "dashboard_lag": "Lag",
        "dashboard_collected": "Collected",
        "dashboard_timezone": "Time zone",
        "dashboard_site_checks": "Site checks",
        "dashboard_checks_missing": "<p>Yandex diagnostics: data is not collected</p><p class=\"caption\">Indexing, technical audit and links: no verified dataset in this export.</p>",
        "dashboard_summary_note": "<p class=\"caption\">Search means GA4 visits. Registrations and payments cover all channels in the Database; this is not an attributed funnel.</p>",
        "dashboard_nav": "<nav aria-label=\"Sections\">",
        "dashboard_7d_link": ">7 days</a>",
        "dashboard_28d_link": ">28 days</a>",
        "dashboard_window_comparison": "{0} — {1} versus {2} — {3}",
        "dashboard_freshness_open": "<p class=\"caption\">Freshness and completeness: ",
        "dashboard_source_freshness": "{0}: through {1}, {2}/{3} days",
        "dashboard_status_link": ". <a href=\"status.html\">Status</a></p>",
        "dashboard_document": "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{0} · {1}</title><style>{2}{3}</style></head><body><main><div class=\"brand\">{4} · Growth</div>{5}<div class=\"periods\">{6}</div><h1>{7}</h1><p class=\"meta\">{8} · Collected {9}</p>{10}{11}</main>{12}</body></html>",
    },
}

_CSS = """
  :root { color-scheme: light; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0;
         background: #f4f5f7; color: #1c2333; }
  main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 16px; margin: 0 0 12px; }
  .meta { color: #6b7280; font-size: 12px; margin-bottom: 20px; }
  .banner { background: #fde8e8; border: 1px solid #f5b5b5; color: #8a1f1f;
            padding: 10px 14px; border-radius: 8px; margin-bottom: 16px;
            font-size: 13px; }
  section { background: #fff; border: 1px solid #e3e5ea; border-radius: 10px;
            padding: 16px 18px; margin-bottom: 16px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #eceef2; }
  th { color: #6b7280; font-weight: 600; font-size: 12px; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr.noise td { color: #9aa1ad; font-style: italic; }
  .caption { color: #6b7280; font-size: 12px; margin: 6px 0 0; }
  .changed { list-style: none; padding: 0; margin: 8px 0 0; font-size: 13px; }
  .changed li { padding: 3px 0; }
  .up { color: #0a7d3b; } .down { color: #b42318; } .new { color: #6b7280; }
  svg { display: block; margin: 4px 0 0; }
  .tag { display: inline-block; font-size: 11px; padding: 0 6px;
         border-radius: 6px; background: #eef2f6; color: #475467; }
"""


def _strings(locale: str) -> dict[str, str]:
    try:
        return _STRINGS[locale]
    except KeyError:
        raise ValueError(f"unsupported locale: {locale!r}") from None


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _fmt_int(value: int | float) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_delta(value: float | None, s: dict[str, str]) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def _fmt_abs_change(name: str, diff: int | float, kpi: dict[str, Any], s: dict[str, str]) -> str:
    sign = "+" if diff > 0 else ""
    if name == "revenue_minor":
        currency = kpi.get("currency") or kpi.get("previous_currency")
        amount = format_minor(diff, currency)
        if amount is None:
            return _esc(s["unsupported_currency"].format(currency=currency or "—"))
        return s["revenue_currency"].format(amount=sign + amount, currency=_esc(currency))
    if name == "visit_to_signup":
        return f"{sign}{diff * 100:.1f} pp"
    return f"{sign}{_fmt_int(diff)}"


def _fmt_kpi_delta(
    name: str,
    kpi: dict[str, Any],
    *,
    pct_field: str,
    abs_field: str,
    s: dict[str, str],
) -> str:
    """Delta cell: the percentage when the comparison was eligible and the
    base is healthy, else the absolute change the deriver produced. When
    neither field is set the comparison was withheld — render a dash and
    never recompute a difference from value/previous here."""
    if kpi.get("value") is None:
        return "—"
    pct = kpi.get(pct_field)
    if pct is not None:
        return _fmt_delta(pct, s)
    diff = kpi.get(abs_field)
    if diff is None:
        return "—"
    return _fmt_abs_change(name, diff, kpi, s)


def _fmt_ts(value: Any) -> str:
    """ISO timestamp -> ``YYYY-MM-DD HH:MM <zone>``; raw text on parse failure."""
    text = str(value or "")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return f"{dt:%Y-%m-%d %H:%M} {dt.tzname() or 'UTC'}"


def _label(s: dict[str, str], prefix: str, raw: Any) -> str:
    """Localized label for a slug/enum value; falls back to the raw value."""
    key = f"{prefix}_{raw}"
    return s.get(key, str(raw))


def _fmt_kpi(name: str, kpi: dict[str, Any], s: dict[str, str]) -> str:
    value = kpi.get("value")
    if value is None:
        return _esc(s["no_data"])
    if name == "visit_to_signup":
        return _fmt_pct(float(value))
    if name == "revenue_minor":
        currency = kpi.get("currency")
        amount = format_minor(value, currency)
        if amount is None:
            return _esc(s["unsupported_currency"].format(currency=currency or "—"))
        return s["revenue_currency"].format(amount=amount, currency=_esc(currency))
    if isinstance(value, float):
        return _fmt_int(value)
    return _fmt_int(int(value))


def _signup_channel_split(derived: dict[str, Any]) -> tuple[dict[str, int], int | None]:
    raw = derived.get("signup_channels") or {}
    signups = {k: int(v) for k, v in raw.items() if k != "sample_of"}
    sample_of = raw.get("sample_of")
    return signups, int(sample_of) if sample_of is not None else None


_SEARCH_FACT_SOURCES = ("google_search_console", "yandex_webmaster")
_TRAFFIC_FACT_SOURCES = ("ga4", "yandex_metrica")
_EVENT_FACT_SOURCES = ("mixpanel",)


def _fact_source_available(growth: dict[str, Any], names: tuple[str, ...]) -> bool:
    """True when at least one named source produced covered days or facts."""
    sources = growth.get("sources") or {}
    for name in names:
        entry = sources.get(name)
        if not entry:
            continue
        if int(entry.get("days_covered") or 0) > 0 or int(
            entry.get("days_with_facts") or 0
        ) > 0:
            return True
    return False


def svg_line_chart(
    series: list[tuple[str, float | None]], *, width: int = 560, height: int = 120
) -> str:
    """Inline SVG line chart; ``None`` values break the line."""
    pad_l, pad_r, pad_t, pad_b = 8, 8, 10, 16
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    values = [float(v) for _label, v in series if v is not None]
    data_lo = min(values, default=0.0)
    data_hi = max(values, default=1.0)
    lo, hi = data_lo, data_hi
    if hi == lo:
        hi = lo + 1.0
    # Vertical margin so extreme points are not flush with the chart edges.
    margin = (hi - lo) * 0.1
    lo -= margin
    hi += margin
    n = len(series)
    span = max(n - 1, 1)

    def point(index: int, value: float) -> tuple[float, float]:
        x = pad_l + plot_w * index / span
        y = pad_t + plot_h * (1 - (value - lo) / (hi - lo))
        return x, y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img">'
    ]
    if values:
        parts.append(
            f'<text x="{pad_l}" y="{pad_t}" font-size="9" fill="#9aa1ad">'
            f"{_fmt_int(data_hi)}</text>"
        )
        parts.append(
            f'<text x="{pad_l}" y="{pad_t + plot_h}" font-size="9" '
            f'fill="#9aa1ad">{_fmt_int(data_lo)}</text>'
        )
    runs: list[list[int]] = []
    for index, (_label, value) in enumerate(series):
        if value is None:
            continue
        if runs and index == runs[-1][-1] + 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    for run in runs:
        # A lone point between gaps is drawn as a dot below, not a path.
        if len(run) < 2:
            continue
        commands = []
        for index in run:
            x, y = point(index, float(series[index][1]))
            commands.append(f"{'M' if not commands else 'L'}{x:.1f},{y:.1f}")
        parts.append(
            f'<path d="{"".join(commands)}" fill="none" stroke="#2563eb" '
            f'stroke-width="1.5"/>'
        )
    for index, (_label, value) in enumerate(series):
        if value is None:
            continue
        x, y = point(index, float(value))
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.8" fill="#2563eb"/>')
    if n:
        first, last = _esc(series[0][0]), _esc(series[-1][0])
        parts.append(
            f'<text x="{pad_l}" y="{height - 3}" font-size="9" fill="#9aa1ad">'
            f"{first}</text>"
        )
        parts.append(
            f'<text x="{width - pad_r}" y="{height - 3}" font-size="9" '
            f'fill="#9aa1ad" text-anchor="end">{last}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _banner(growth: dict[str, Any], s: dict[str, str]) -> str:
    # A provisional search reporting lag (successful requests, zero rows on
    # the trailing days) is not an outage: the red banner is for sources that
    # failed, went stale or produced no data at all in the window — and for
    # required search sources with a real gap (``search_gap`` set by the
    # builder) or no total facts at all, which day coverage alone cannot
    # distinguish from a measured zero.
    search = growth.get("search") or {}
    missing = sorted(
        name
        for name, entry in (growth.get("sources") or {}).items()
        if entry.get("required")
        and (
            entry.get("state") in {"failed", "stale", "unsupported"}
            # Only search reporting lag is exempt from "partial": an
            # incomplete required non-search feed (GA4, outcomes) is an outage.
            or (name not in _SEARCH_FACT_SOURCES and entry.get("state") == "partial")
            # A failed latest request is an outage even when older requests
            # left totals: only a successful provisional lag is exempt.
            or (
                entry.get("state") == "partial"
                and entry.get("last_transport_status") not in (None, "success")
            )
            or (
                int(entry.get("days_covered") or 0) == 0
                and int(entry.get("days_with_facts") or 0) == 0
            )
            or (
                name in _SEARCH_FACT_SOURCES
                and (
                    search.get(name) is None
                    or not (search.get(name) or {}).get(
                        "totals_available", True
                    )
                    or bool((search.get(name) or {}).get("search_gap"))
                )
            )
        )
    )
    if not missing:
        return ""
    text = s["banner_not_live"].format(
        sources=", ".join(_esc(name) for name in missing)
    )
    return f'<div class="banner">{text}</div>'


def _kpi_table(growth: dict[str, Any], s: dict[str, str]) -> str:
    derived = growth.get("derived") or {}
    kpis = derived.get("kpis") or {}
    wide = (derived.get("kpis_by_window") or {}).get("28d") or {}
    wide_col = f'<th class="num">{_esc(s["window_28d"])}</th>' if wide else ""
    rows = []
    for name in _KPI_ORDER:
        kpi = kpis.get(name) or {}
        label = _esc(s.get(name, name))
        if kpi.get("estimate"):
            label += f' <span class="tag">{_esc(s["estimate"])}</span>'
        value_text = _fmt_kpi(name, kpi, s)
        if kpi.get("coverage") == "partial":
            value_text += f' <span class="tag">{_esc(s["coverage_partial"])}</span>'
        cells = [
            f"<td>{label}</td>",
            f'<td class="num">{value_text}</td>',
        ]
        if wide:
            wide_kpi = wide.get(name) or {}
            wide_text = _fmt_kpi(name, wide_kpi, s)
            if wide_kpi.get("coverage") == "partial":
                wide_text += (
                    f' <span class="tag">{_esc(s["coverage_partial"])}</span>'
                )
            cells.append(f'<td class="num">{wide_text}</td>')
        cells.append(
            '<td class="num">'
            + _fmt_kpi_delta(
                name, kpi, pct_field="delta_pct", abs_field="delta_abs", s=s
            )
            + "</td>"
        )
        cells.append(
            '<td class="num">'
            + _fmt_kpi_delta(
                name,
                kpi,
                pct_field="delta_vs_avg4_pct",
                abs_field="delta_vs_avg4_abs",
                s=s,
            )
            + "</td>"
        )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    table = (
        "<table><thead><tr>"
        f"<th>{_esc(s['metric'])}</th>"
        f'<th class="num">{_esc(s["window_7d"])}</th>'
        f"{wide_col}"
        f'<th class="num">{_esc(s["delta_prev"])}</th>'
        f'<th class="num">{_esc(s["delta_avg4"])}</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )
    through_notes = []
    for source in sorted(growth.get("search") or {}):
        through = ((growth["search"] or {}).get(source) or {}).get(
            "data_through"
        )
        if not through:
            continue
        try:
            formatted = f"{datetime.fromisoformat(str(through)):%d.%m}"
        except ValueError:
            formatted = str(through)
        through_notes.append(
            s["search_data_through"].format(
                source=_esc(source), date=_esc(formatted)
            )
        )
    if through_notes:
        table += f'<p class="caption">{"; ".join(through_notes)}</p>'
    return table


def _what_changed_list(growth: dict[str, Any], s: dict[str, str]) -> str:
    items = (growth.get("derived") or {}).get("what_changed") or []
    if not items:
        return ""
    lis = []
    for item in items:
        kind_raw = str(item.get("kind"))
        kind = _esc(_label(s, "kind", kind_raw))
        frm, to = int(item.get("from") or 0), int(item.get("to") or 0)
        cls = "up" if to > frm else ("down" if to < frm else "new")
        change = s["change_from_to"].format(frm=_fmt_int(frm), to=_fmt_int(to))
        if frm == 0:
            cls = "new"
        subject = item.get("subject")
        if kind_raw == "channel":
            subject = _label(s, "channel", subject)
        lis.append(
            f'<li><span class="{cls}">{kind}</span> {_esc(subject)}: '
            f"{_esc(change)}</li>"
        )
    return (
        f'<p class="caption">{_esc(s["what_changed"])}</p>'
        f'<ul class="changed">{"".join(lis)}</ul>'
    )


def _channels_section(growth: dict[str, Any], s: dict[str, str]) -> str:
    traffic = growth.get("traffic") or {}
    derived = growth.get("derived") or {}
    visits_by_channel: dict[str, int] = {}
    for row in traffic.get("daily") or []:
        channel = str(row.get("channel"))
        visits_by_channel[channel] = visits_by_channel.get(channel, 0) + int(
            row.get("visits") or 0
        )
    signups, sample_of = _signup_channel_split(derived)
    payments_by_channel: dict[str, int] = {}
    for row in (growth.get("outcomes") or {}).get("server") or []:
        if row.get("outcome_id") == "paid_purchase":
            channel = str(row.get("channel"))
            payments_by_channel[channel] = payments_by_channel.get(channel, 0) + int(
                row.get("count") or 0
            )
    channels = sorted(set(visits_by_channel) | set(signups) | set(payments_by_channel))
    signup_total = sum(signups.values())
    # An absent channel is a measured zero only when a traffic source fully
    # covered the window; partial traffic data cannot certify "no visits".
    window_days = int((growth.get("window") or {}).get("days") or 0)
    traffic_fully_covered = window_days > 0 and any(
        int((growth.get("sources") or {}).get(name, {}).get("days_covered") or 0)
        == window_days
        and (growth.get("sources") or {}).get(name, {}).get("state") != "partial"
        for name in _TRAFFIC_FACT_SOURCES
    )
    mixpanel_available = bool(signups) or _fact_source_available(
        growth, _EVENT_FACT_SOURCES
    )
    reg_head = _esc(s["registrations"])
    pay_head = f'{_esc(s["payments"])} ({_esc(s["by_registration_channel"])})'
    rows = []
    for channel in channels:
        if channel in visits_by_channel:
            visits_text = _fmt_int(visits_by_channel[channel])
        elif traffic_fully_covered:
            visits_text = _fmt_int(0)
        else:
            visits_text = _esc(s["no_data"])
        rows.append(
            "<tr>"
            f"<td>{_esc(_label(s, 'channel', channel))}</td>"
            f'<td class="num">{visits_text}</td>'
            f'<td class="num">{_fmt_int(signups.get(channel, 0)) if channel in signups else _esc(s["no_data"])}</td>'
            f'<td class="num">{_fmt_int(payments_by_channel.get(channel, 0)) if channel in payments_by_channel else _esc(s["no_data"])}</td>'
            "</tr>"
        )
    noise = int((traffic.get("excluded_noise") or {}).get("visits") or 0)
    if noise:
        noise_text = s["excluded_noise"].format(visits=_fmt_int(noise))
        rows.append(f'<tr class="noise"><td colspan="4">{_esc(noise_text)}</td></tr>')
    table = (
        "<table><thead><tr>"
        f"<th>{_esc(s['channel'])}</th>"
        f'<th class="num">{_esc(s["visits"])}</th>'
        f'<th class="num">{reg_head}</th>'
        f'<th class="num">{pay_head}</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )
    caption = s["mixpanel_sample"].format(
        n=_fmt_int(signup_total) if mixpanel_available else _esc(s["no_data"]),
        m=_fmt_int(sample_of) if sample_of is not None else _esc(s["no_data"]),
    )
    trend = (derived.get("trend_12w") or [])
    series = [
        (str(week.get("week_start") or ""), week.get(_TREND_METRIC))
        for week in trend
    ]
    chart = svg_line_chart(series) if series else ""
    trend_cap = (
        f'<p class="caption">{_esc(s["trend"])}</p>' if series else ""
    )
    return table + f'<p class="caption">{_esc(caption)}</p>' + trend_cap + chart


def _brand_totals(growth: dict[str, Any]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for source, block in (growth.get("search") or {}).items():
        brand_imp = brand_clk = total_imp = total_clk = 0
        has_total = False
        for day in block.get("daily") or []:
            total = day.get("total")
            if total:
                has_total = True
            total = total or {}
            brand = day.get("brand") or {}
            total_imp += int(total.get("impressions") or 0)
            total_clk += int(total.get("clicks") or 0)
            brand_imp += int(brand.get("impressions") or 0)
            brand_clk += int(brand.get("clicks") or 0)
        totals[str(source)] = {
            "total_impressions": total_imp,
            "total_clicks": total_clk,
            "brand_impressions": brand_imp,
            "brand_clicks": brand_clk,
            "has_total": has_total,
        }
    return totals


def _search_section(growth: dict[str, Any], s: dict[str, str]) -> str:
    totals = _brand_totals(growth)
    summary_rows = []
    for source in sorted(totals):
        t = totals[source]
        if t["has_total"]:
            cells = (
                f'<td class="num">{_fmt_int(t["total_impressions"] - t["brand_impressions"])}</td>'
                f'<td class="num">{_fmt_int(t["total_clicks"] - t["brand_clicks"])}</td>'
                f'<td class="num">{_fmt_int(t["brand_impressions"])}</td>'
                f'<td class="num">{_fmt_int(t["brand_clicks"])}</td>'
            )
        else:
            cells = f'<td class="num">{_esc(s["no_data"])}</td>' * 4
        summary_rows.append(
            f"<tr><td>{_esc(source)}</td>{cells}</tr>"
        )
    summary_table = (
        "<table><thead><tr>"
        f"<th>{_esc(s['source'])}</th>"
        f'<th class="num">{_esc(s["impressions"])} ({_esc(s["nonbrand"])})</th>'
        f'<th class="num">{_esc(s["clicks"])} ({_esc(s["nonbrand"])})</th>'
        f'<th class="num">{_esc(s["impressions"])} ({_esc(s["brand"])})</th>'
        f'<th class="num">{_esc(s["clicks"])} ({_esc(s["brand"])})</th>'
        f"</tr></thead><tbody>{''.join(summary_rows)}</tbody></table>"
    )
    query_rows = []
    queries = []
    for source, block in (growth.get("search") or {}).items():
        for q in block.get("queries") or []:
            queries.append((str(source), q))
    queries.sort(
        key=lambda pair: (-int(pair[1].get("impressions") or 0), str(pair[1].get("query")))
    )
    for source, q in queries[:_TOP_QUERIES]:
        tag = (
            f' <span class="tag">{_esc(s["brand"])}</span>' if q.get("is_brand") else ""
        )
        position = q.get("position")
        pos_text = f"{float(position):.1f}" if position is not None else _esc(s["no_data"])
        query_rows.append(
            "<tr>"
            f"<td>{_esc(q.get('query'))}{tag}</td>"
            f"<td>{_esc(source)}</td>"
            f'<td class="num">{_fmt_int(int(q.get("impressions") or 0))}</td>'
            f'<td class="num">{_fmt_int(int(q.get("clicks") or 0))}</td>'
            f'<td class="num">{pos_text}</td>'
            "</tr>"
        )
    query_table = ""
    if query_rows:
        query_table = (
            "<table><thead><tr>"
            f"<th>{_esc(s['query'])}</th>"
            f"<th>{_esc(s['source'])}</th>"
            f'<th class="num">{_esc(s["impressions"])}</th>'
            f'<th class="num">{_esc(s["clicks"])}</th>'
            f'<th class="num">{_esc(s["position"])}</th>'
            f"</tr></thead><tbody>{''.join(query_rows)}</tbody></table>"
        )
    return summary_table + query_table


def _pages_section(growth: dict[str, Any], s: dict[str, str]) -> str:
    pages: dict[str, dict[str, int]] = {}
    for block in (growth.get("search") or {}).values():
        for row in block.get("pages") or []:
            page = str(row.get("page"))
            entry = pages.setdefault(page, {"impressions": 0, "clicks": 0, "visits": 0})
            entry["impressions"] += int(row.get("impressions") or 0)
            entry["clicks"] += int(row.get("clicks") or 0)
    for row in (growth.get("traffic") or {}).get("landing_pages") or []:
        page = str(row.get("page"))
        entry = pages.setdefault(page, {"impressions": 0, "clicks": 0, "visits": 0})
        entry["visits"] += int(row.get("visits") or 0)
    ordered = sorted(
        pages.items(), key=lambda kv: (-(kv[1]["impressions"] + kv[1]["visits"]), kv[0])
    )[:_TOP_PAGES]
    rows = []
    for page, m in ordered:
        rows.append(
            "<tr>"
            f"<td>{_esc(page)}</td>"
            f'<td class="num">{_fmt_int(m["impressions"])}</td>'
            f'<td class="num">{_fmt_int(m["clicks"])}</td>'
            f'<td class="num">{_fmt_int(m["visits"])}</td>'
            "</tr>"
        )
    if not rows:
        return f'<p class="caption">{_esc(s["no_data"])}</p>'
    return (
        "<table><thead><tr>"
        f"<th>{_esc(s['page'])}</th>"
        f'<th class="num">{_esc(s["impressions"])}</th>'
        f'<th class="num">{_esc(s["clicks"])}</th>'
        f'<th class="num">{_esc(s["visits"])}</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _actions_section(growth: dict[str, Any], s: dict[str, str]) -> str:
    actions = sorted(
        growth.get("actions") or [],
        key=lambda a: str(a.get("changed_at") or ""),
        reverse=True,
    )
    if not actions:
        return f'<p class="caption">{_esc(s["no_data"])}</p>'
    rows = []
    for action in actions:
        delta = action.get("relative_delta")
        delta_text = _fmt_pct(float(delta)) if delta is not None else _esc(s["no_data"])
        verdict = action.get("verdict")
        verdict_text = (
            _esc(_label(s, "verdict", verdict)) if verdict else _esc(s["no_data"])
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(str(action.get('changed_at') or '')[:10])}</td>"
            f"<td>{_esc(_label(s, 'action', action.get('action_type')))}</td>"
            f"<td>{_esc(action.get('description'))}</td>"
            f"<td>{verdict_text}</td>"
            f'<td class="num">{delta_text}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        f"<th>{_esc(s['action_date'])}</th>"
        f"<th>{_esc(s['action_type'])}</th>"
        f"<th>{_esc(s['action_description'])}</th>"
        f"<th>{_esc(s['verdict'])}</th>"
        f'<th class="num">{_esc(s["relative_delta"])}</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _data_section(growth: dict[str, Any], s: dict[str, str]) -> str:
    rows = []
    for name in sorted(growth.get("sources") or {}):
        entry = growth["sources"][name]
        rows.append(
            "<tr>"
            f"<td>{_esc(name)}</td>"
            f"<td>{_esc(entry.get('state'))}</td>"
            f"<td>{_esc(s['yes']) if entry.get('required') else _esc(s['no'])}</td>"
            f"<td>{_esc(_fmt_ts(entry.get('collected_at'))) if entry.get('collected_at') else _esc(s['no_data'])}</td>"
            f"<td>{_esc(entry.get('timezone')) if entry.get('timezone') else _esc(s['no_data'])}</td>"
            f'<td class="num">{int(entry.get("days_covered") or 0)}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        f"<th>{_esc(s['source'])}</th>"
        f"<th>{_esc(s['state'])}</th>"
        f"<th>{_esc(s['required'])}</th>"
        f"<th>{_esc(s['collected_at'])}</th>"
        f"<th>{_esc(s['timezone'])}</th>"
        f'<th class="num">{_esc(s["days_covered"])}</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def render_panel_html(
    growth: dict[str, Any],
    *,
    title: str,
    generated_at: str,
    locale: str = "ru",
) -> str:
    """Self-contained growth panel HTML (inline CSS/SVG, no JS)."""
    s = _strings(locale)
    sections = [
        ("section_summary", _kpi_table(growth, s) + _what_changed_list(growth, s)),
        ("section_channels", _channels_section(growth, s)),
        ("section_search", _search_section(growth, s)),
        ("section_pages", _pages_section(growth, s)),
        ("section_actions", _actions_section(growth, s)),
        ("section_data", _data_section(growth, s)),
    ]
    body = "".join(
        f'<section><h2>{_esc(s[key])}</h2>{content}</section>'
        for key, content in sections
    )
    return f"""<!doctype html>
<html lang="{_esc(locale)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(title)}</title>
  <style>{_CSS}
  </style>
</head>
<body>
<main>
  <h1>{_esc(title)}</h1>
  <div class="meta">{_esc(s["generated_at"].format(at=_fmt_ts(generated_at)))} · {_esc((growth.get("window") or {}).get("start", ""))} — {_esc((growth.get("window") or {}).get("end", ""))}</div>
  {_banner(growth, s)}
  {body}
</main>
</body>
</html>
"""


def _kpi_raw(growth: dict[str, Any], name: str) -> Any:
    return ((growth.get("derived") or {}).get("kpis") or {}).get(name, {}).get("value")


def _brief_value(growth: dict[str, Any], name: str, s: dict[str, str]) -> str:
    kpi = ((growth.get("derived") or {}).get("kpis") or {}).get(name) or {}
    if kpi.get("value") is None:
        return s["brief_no_data"]
    return _fmt_kpi(name, kpi, s)


def render_brief(
    growth: dict[str, Any], *, panel_url: str | None, locale: str = "ru"
) -> str:
    """Markdown brief (10–15 lines). Plain text — no Telegram HTML tags."""
    s = _strings(locale)
    window = growth.get("window") or {}
    derived = growth.get("derived") or {}
    kpis = derived.get("kpis") or {}
    totals = _brand_totals(growth)
    # Enabled search sources always emit a block; one that never produced
    # a ``total`` fact is absent data, not a measured zero.
    # Brand totals exist only when some search block carries totals; covered
    # days or query-only facts are not a measured zero.
    search_available = any(t["has_total"] for t in totals.values())
    mixpanel_available = bool(
        (derived.get("signup_channels") or {}).keys() - {"sample_of"}
    ) or _fact_source_available(growth, _EVENT_FACT_SOURCES)
    brand_imp = sum(t["brand_impressions"] for t in totals.values())
    brand_clk = sum(t["brand_clicks"] for t in totals.values())
    delta = _fmt_kpi_delta(
        "nonbrand_impressions",
        kpis.get("nonbrand_impressions") or {},
        pct_field="delta_pct",
        abs_field="delta_abs",
        s=s,
    )
    signups, sample_of = _signup_channel_split(derived)
    sample = s["mixpanel_sample"].format(
        n=_fmt_int(sum(signups.values())) if mixpanel_available else s["brief_no_data"],
        m=_fmt_int(sample_of) if sample_of is not None else s["brief_no_data"],
    )
    lines = [
        s["brief_header"].format(
            start=window.get("start", ""), end=window.get("end", "")
        ),
        s["brief_nonbrand"].format(
            imp=_brief_value(growth, "nonbrand_impressions", s),
            clk=_brief_value(growth, "nonbrand_clicks", s),
            delta=delta,
        ),
        s["brief_brand"].format(
            imp=_fmt_int(brand_imp) if search_available else s["brief_no_data"],
            clk=_fmt_int(brand_clk) if search_available else s["brief_no_data"],
        ),
        s["brief_organic"].format(visits=_brief_value(growth, "organic_visits", s)),
        s["brief_registrations"].format(
            count=_brief_value(growth, "registrations", s), sample=sample
        ),
        s["brief_payments"].format(
            count=_brief_value(growth, "payments", s),
            revenue=_brief_value(growth, "revenue_minor", s),
        ),
        s["brief_visit_to_signup"].format(
            value=_brief_value(growth, "visit_to_signup", s),
            estimate=(
                f" ({s['estimate']})"
                if (kpis.get("visit_to_signup") or {}).get("estimate")
                else ""
            ),
        ),
    ]
    for item in (derived.get("what_changed") or [])[:2]:
        kind = _label(s, "kind", item.get("kind"))
        subject = item.get("subject")
        if str(item.get("kind")) == "channel":
            subject = _label(s, "channel", subject)
        lines.append(
            f"· {kind}: {subject} "
            f"({item.get('from')} → {item.get('to')})"
        )
    # Source status, coverage and channel mix keep first-run or
    # non-comparable briefs informative instead of falling below 10 lines.
    sources = growth.get("sources") or {}
    if sources and all(
        entry.get("state") == "live" for entry in sources.values()
    ):
        status_text = s["sources_all_live"]
    elif sources:
        status_text = ", ".join(
            f"{name}={entry.get('state') or '?'}"
            for name, entry in sorted(sources.items())
        )
    else:
        status_text = s["brief_no_data"]
    lines.append(s["brief_sources"].format(status=status_text))
    covered_min = (
        min(int(entry.get("days_covered") or 0) for entry in sources.values())
        if sources
        else 0
    )
    lines.append(
        s["brief_coverage"].format(
            covered=covered_min, days=window.get("days") or 0
        )
    )
    visits_by_channel: dict[str, int] = {}
    for row in (growth.get("traffic") or {}).get("daily") or []:
        channel = str(row.get("channel"))
        visits_by_channel[channel] = visits_by_channel.get(channel, 0) + int(
            row.get("visits") or 0
        )
    if visits_by_channel:
        channels_text = ", ".join(
            f"{_label(s, 'channel', name)} {_fmt_int(count)}"
            for name, count in sorted(
                visits_by_channel.items(), key=lambda kv: (-kv[1], kv[0])
            )[:3]
        )
    else:
        channels_text = s["brief_no_data"]
    lines.append(s["brief_top_channels"].format(channels=channels_text))
    if panel_url:
        lines.append(s["brief_panel"].format(url=panel_url))
    return "\n".join(lines) + "\n"


def _page_query_details(block: dict[str, Any], pages: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    # One pass, with at most 100 retained query rows per displayed page.
    heaps: dict[str, list[Any]] = {row['page']: [] for row in pages}
    counts = dict.fromkeys(heaps, 0)
    for ordinal, row in enumerate(block.get('query_pages') or []):
        page = row['page']
        if page not in heaps:
            continue
        counts[page] += 1
        entry = (row['impressions'], row['clicks'], -ordinal, row)
        heap = heaps[page]
        if len(heap) < 100:
            heapq.heappush(heap, entry)
        elif entry > heap[0]:
            heapq.heapreplace(heap, entry)
    return ({page: [item[3] for item in sorted(heap, reverse=True)] for page, heap in heaps.items()}, counts)


# Compatibility facade for callers of the original current-panel renderer.
# Import lazily: dashboard uses the shared formatting helpers above.
def render_dashboard_pages(growth, *, title, generated_at, panel=None):
    from seo_observer.growth_dashboard import render_dashboard_pages as render
    return render(growth, title=title, generated_at=generated_at, panel=panel)


def position_buckets(queries):
    from seo_observer.growth_dashboard import position_buckets as partition
    return partition(queries)


def _detail_comparable(current, previous, source):
    from seo_observer.growth_dashboard import _detail_comparable as comparable
    return comparable(current, previous, source)


def _dash_through(growth, source):
    from seo_observer.growth_dashboard import _dash_through as through
    return through(growth, source)
