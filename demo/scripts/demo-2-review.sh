#!/bin/bash
# Demo 2: /review full --no-fix на уязвимом приложении
# Изолированный клон в ./demo-runs/, локальный scope плагина через .claude/settings.local.json
# (auth берётся из твоего настоящего claude)

set -e

SUFFIX=$(head -c 4 /dev/urandom | xxd -p)
BASE="$PWD/demo-runs"
DEMO_DIR="$BASE/review-$SUFFIX"
mkdir -p "$BASE"
REPO_URL="https://github.com/OXI-717/ai-native-toolkit.git"

echo "=== Demo 2: /review full --no-fix ==="
echo "Создаём изолированный клон..."
echo ""

# Клонируем репу
git clone --quiet "$REPO_URL" "$DEMO_DIR"

VULN_DIR="$DEMO_DIR/demo/vulnerable-saas"

# Локальный scope в vulnerable-saas: плагин активен ТОЛЬКО когда claude запущен из этой папки
mkdir -p "$VULN_DIR/.claude"
cat > "$VULN_DIR/.claude/settings.local.json" <<'EOF'
{
  "extraKnownMarketplaces": {
    "ai-native-toolkit": {
      "source": {
        "source": "github",
        "repo": "OXI-717/ai-native-toolkit"
      }
    }
  },
  "enabledPlugins": {
    "review@ai-native-toolkit": true,
    "oxi-review@oxi-skills": false,
    "oxi-code-review@oxi-skills": false
  }
}
EOF

echo "Рабочая директория: $VULN_DIR"
echo "Scope: .claude/settings.local.json (только эта папка)"
echo "Файлов в проекте:   $(find "$VULN_DIR" -name '*.ts' -o -name '*.tsx' -o -name '*.js' | wc -l | tr -d ' ')"
echo ""
echo "--- Запуск ---"
echo "Выполни:"
echo ""
echo "  cd $VULN_DIR && claude --permission-mode acceptEdits"
echo ""
echo "--permission-mode acceptEdits чтобы не запрашивал подтверждения и не блокировал Agent."
echo "Плагин review@ai-native-toolkit подхватится автоматически — /plugin install не нужен."
echo ""
echo "--- Внутри claude: ДВА ШАГА для сравнительного демо ---"
echo ""
echo "ШАГ 1 — single-agent baseline через слэш-команду:"
echo ""
echo "  /review full --no-fix"
echo ""
echo "  В v2.1.150 эта команда падает в single-agent режим (main-сессия сама"
echo "  читает файлы и пишет отчёт). Это работает как контрольная точка."
echo "  Ожидание: ~1-2 минуты, результат — около 17 находок."
echo ""
echo "ШАГ 2 — мульти-агентный прогон через Agent tool (вставь промпт):"
echo ""
cat <<'PROMPT'
  Теперь сделай тот же ревью, но мульти-агентный. Через инструмент Agent
  заспавни параллельно ПЯТЬ агентов с subagent_type:
  - security-scanner (уязвимости, IDOR, XSS, mass assignment, secrets, CVE в зависимостях)
  - code-reviewer (качество кода, strict false, any[], плохие практики)
  - bug-hunter (логические баги, race conditions, null-dereference, обработка null)
  - error-auditor (тихие проглатываемые исключения, отсутствие try/catch, inconsistent error shapes)
  - architecture-reviewer (структура, DTO/middleware-слои, циклические зависимости, разделение ответственностей)
  Каждый ревьюит весь проект под своим углом независимо. Собери результаты
  в единый отчёт. Покажи прирост находок по сравнению с первым проходом.
PROMPT
echo ""
echo "Ожидание: ~2-3 минуты."
echo ""
echo "Комментировать пока агенты работают:"
echo "  • 5 Sonnet-агентов стартовали параллельно — видны в правой панели"
echo "  • Каждый специалист — свой угол зрения"
echo "  • Они работают изолированно, главный агент собирает результаты"
echo ""
echo "--- Ожидаемый результат ---"
echo "  Single-agent: ~17 находок"
echo "  Multi-agent:  ~30 находок"
echo "  Прирост ~76% — за счёт:"
echo "    • architecture-reviewer: DTO/middleware/сервисный слой"
echo "    • bug-hunter: null-dereference на session, optimistic delete"
echo "    • code-reviewer: strict: false как root-cause всех null-багов, any[]"
echo "    • security-scanner: конкретная CVE в next@14.2.5 (нужно 14.2.30+)"
echo "    • error-auditor: bare await req.json() без try/catch, inconsistent error shapes"
echo ""
echo "--- Cleanup после демо ---"
echo "  rm -rf $BASE"
echo ""
echo "==========================================="
echo "  cd $VULN_DIR && claude --permission-mode acceptEdits"
echo "==========================================="
