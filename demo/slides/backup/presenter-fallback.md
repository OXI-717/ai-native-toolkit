# Presenter Fallback — если live demo не пошло

Используй ЭТОТ документ если интернет/демо упало во время выступления. Открывай в отдельной вкладке заранее.

---

## Если упал Demo 1 (ctx-init)

**Скажи:**
> «Если бы сейчас интернет был стабилен, я бы показал как ctx-init за десять секунд создаёт стандартную структуру контекста: AGENTS.md с моими правилами, CLAUDE.md с импортом, и папку rules/ из девяти файлов — пять общих и четыре под этот конкретный проект.»

**Покажи скриншот:** `backup-screenshots/slide-08-demo-ctxinit.png`

**Структура которая создаётся:**
```
AGENTS.md          # frontmatter + @-импорты из rules/
CLAUDE.md          # одна строка: @AGENTS.md
rules/
  ├── language.md       # язык ответов AI
  ├── dates.md          # ISO формат
  ├── git.md            # никаких force push без спроса
  ├── no-secrets.md     # запрет на .env, credentials.json
  ├── no-public-names.md
  ├── stack.md          # стек проекта (заполняешь сам)
  ├── testing.md        # как запускать тесты
  ├── boundaries.md     # что не трогать
  └── focus.md          # текущий фокус
```

---

## Если упал Demo 2 (review)

**Скажи:**
> «У меня здесь предварительно прогнанный отчёт того же мульти-агентного ревью. На этом демо-проекте Next.js плюс Supabase автоматический пайплайн нашёл 25 уникальных уязвимостей: восемнадцать критичных и семь важных. Среди критичных — захардкоженный service-role ключ в исходниках, IDOR на каждом API-роуте, mass assignment с захватом роли admin, stored XSS, wildcard CORS.»

**Покажи pre-baked отчёт:** `demo/vulnerable-saas/artifacts/review-report.md` (6.8KB, готов к показу)

**Ключевые числа для запоминания:**
- ~32 raw findings → 25 unique после дедуп → 18 Critical + 7 Important
- 5 параллельных Sonnet-агентов: security-scanner, bug-hunter, code-reviewer, error-auditor, architecture-reviewer
- Время прогона: ~2 минуты
- Обнаружил CVE в зависимостях (Next 14.2.5 → нужно ≥14.2.32)

---

## Если упал Demo 3 (codex)

**Скажи:**
> «Третий демо — другой агент полностью, в данном случае Codex. Подаём ему голый репо без какой-либо настройки. За тридцать секунд он перечисляет все шесть плагинов с корректными описаниями. Тот же AGENTS.md, другой инструмент, идентичное понимание — это и есть суть переносимости контекста.»

**Покажи скриншот:** `backup-screenshots/slide-17-demo-codex.png`

**Ожидаемый output Codex:**
```
This repo is AI Native Toolkit — open-source plugins for Claude Code/Codex.

- ctx: Bootstraps project context via AGENTS.md, rules, and templates
- review: Multi-agent code review with confidence filtering
- pentest: Black-box web app security audits (L0-L3)
- context-handoff: Preserves session context across /compact and /clear
- statusline: Status bar with usage limits and context %
- gh-issues: GitHub Issues as persistent AI session memory
```

---

## Если упал весь Claude Code / Codex

**Скажи:**
> «Похоже техника капризничает прямо сейчас — продолжу рассказывать концептуально, а живое демо запишу и выложу следом в комментариях к мероприятию.»

Переключайся на слайды + speaker notes, иди по структуре доклада дальше.

**После доклада** — запиши скринкаст всех трёх демо и выложи как комментарий к Luma-событию + в свой канал.

---

## Если упал HTTP-сервер слайдов

**Команда быстрого восстановления:**
```bash
cd <repo>/demo/slides
python3 -m http.server 8765
```

Если порт 8765 занят — попробуй 8766, 8080. Открыть `http://localhost:8765/`.

---

## Pre-flight чеклист за 15 минут до выступления

- [ ] `cd <repo>/demo/slides && python3 -m http.server 8765` — слайды
- [ ] Открыть `http://localhost:8765/` в браузере, нажать `s` — speaker view
- [ ] Зашерить через Zoom **окно с основными слайдами**, не speaker view
- [ ] iTerm: открыть в `/tmp/ainative-tests/demo-runs/...` для каждой demo-папки (3 вкладки)
- [ ] Проверить что Claude Max лимиты не съедены (`/usage` в Claude)
- [ ] Проверить интернет: пинг github.com
- [ ] Скриншоты и review-report.md открыть в отдельной вкладке как backup
