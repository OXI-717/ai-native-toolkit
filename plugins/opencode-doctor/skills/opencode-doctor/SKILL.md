---
name: opencode-doctor
description: Use when every opencode command returns HTTP 400 Invalid request. Triggers — "сессия opencode сломалась", "opencode 400".
---

# opencode-doctor — починка отравленных сессий opencode

## Симптомы

- Сессия opencode на **любой** промпт (включая пустое «продолжаем») отвечает
  `APIError: Invalid request Error` / `400 invalid_request_error`.
- В логе (`~/.local/share/opencode/log/opencode.log`) — `stream error ...
  AI_APICallError: Invalid request Error` подряд, `isRetryable: false`.
- Контекст сессии **не** при лимите (проверь лимит модели в
  `~/.cache/opencode/models.json`, прежде чем списывать на переполнение).

### Сначала отличи от других похожих сбоев

Смотри текст ошибки в данных сообщения (`message.data.error.data`):

- **`403 "usage limit ... billing cycle"`** — исчерпана квота провайдера
  (kimi-for-coding и др.). Это НЕ отрава: жди обновления квоты или смени модель.
  Пустое error-сообщение в истории при этом безвредно.
- **`400 invalid_request_error` на каждый запрос** — отрава истории, этот ранбук.
- **Разовый 400/529, после которого сессия продолжила работать** — транзиент,
  ничего не делать.

## Механика (инцидент 2026-08-17)

1. `read` по PDF/бинарнику встраивает `data:application/pdf;base64` аттачмент в
   историю; Anthropic-совместимые эндпоинты (kimi-for-coding) его не принимают
   → первый 400. **Подтверждённая первопричина.**
2. Каждый упавший запрос оставляет assistant-сообщение с `error` и **0 parts**
   (пустой контент). Такие сообщения встречаются во многих сессиях и, судя по
   всему, фильтруются opencode при сборке запроса — но в инциденте они шли
   вперемешку с аттачментом, поэтому doctor считает их suspect и чинит заодно
   (безвредно): лучше избыточная структурная валидность, чем мёртвая сессия.

## Починка скриптом (основной путь)

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-<installed-opencode-doctor-plugin-root>}"
DOCTOR="$PLUGIN_ROOT/bin/opencode-doctor"
python3 "$DOCTOR" check <ses_id>      # диагностика (exit 1 = отравлена)
python3 "$DOCTOR" fix <ses_id>        # бэкап БД + ремонт + верификация
python3 "$DOCTOR" check --all         # массовый аудит всех сессий
```

`fix` делает: бэкап `opencode.db` (sqlite backup API) в
`~/.local/share/opencode/backups/<ts>/`, зачистку `state.attachments` у parts
с data-аттачментами, замену пустых error-assistant сообщений текстовыми
заглушками (сохраняет чередование ролей, user-сообщения не удаляются).

После починки проверь боевым запросом:

```bash
opencode run --session <ses_id> "Тест: ответь одним словом — ОК. Ничего не выполняй."
```

Если сессия была открыта в TUI в момент починки — перезапусти TUI: opencode
может держать историю в памяти.

Если CLI недоступен, не изменяй базу вручную: установи плагин или найди
`bin/opencode-doctor` рядом с этим навыком. Для исправления используй только
`fix`, который делает резервную копию через SQLite backup API.

## Профилактика

- Не читать PDF/бинарники read-инструментом на сторонних Anthropic-совместимых
  эндпоинтах: `pdftotext` → читать `.txt`.
- Не ретраить APIError вслепую больше одного раза: повторный 400 → стоп,
  доклад пользователю.
- Длинные исследования писать на диск инкрементально.
