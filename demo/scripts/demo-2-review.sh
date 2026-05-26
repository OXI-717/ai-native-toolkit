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
echo "--- Внутри claude ---"
echo "Вставь промпт (не слэш-команда — Claude Code v2.1.150 ломает routing /review --no-fix"
echo "в single-agent, поэтому идём через явный Agent tool):"
echo ""
cat <<'PROMPT'
  Сделай мульти-агентный security audit этой папки. Через инструмент Agent
  заспавни параллельно четырёх агентов с subagent_type:
  - security-scanner (уязвимости OWASP, IDOR, XSS, mass assignment, secrets)
  - code-reviewer (качество кода, конвенции, плохие практики)
  - bug-hunter (логические баги, race conditions, обработка null)
  - architecture-reviewer (структура, циклические зависимости, separation of concerns)
  Каждый ревьюит весь проект под своим углом. Собери результаты в единый
  отчёт с категоризацией Critical (score >= 90) / Important (80-89).
PROMPT
echo ""
echo "Ожидай ~2 минуты. Комментируй пока агенты работают:"
echo "  • 4 Sonnet-агента стартовали параллельно"
echo "  • Каждый специалист по своей теме"
echo "  • Видны в правой панели Claude Code со счётчиками токенов"
echo "  • После их завершения main-агент сведёт находки в один отчёт"
echo ""
echo "--- Ожидаемый результат ---"
echo "  ~25 уникальных находок: ~18 Critical + ~7 Important"
echo ""
echo "--- Cleanup после демо ---"
echo "  rm -rf $BASE"
echo ""
echo "==========================================="
echo "  cd $VULN_DIR && claude --permission-mode acceptEdits"
echo "==========================================="
