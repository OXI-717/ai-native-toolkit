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
echo "  cd $VULN_DIR && claude"
echo ""
echo "Плагин review@ai-native-toolkit подхватится автоматически — /plugin install не нужен."
echo ""
echo "--- Внутри claude ---"
echo "  /review full --no-fix"
echo ""
echo "Ожидай ~2 минуты. Комментируй пока агенты работают:"
echo "  • 5 Sonnet-агентов стартовали параллельно"
echo "  • chunk-reviewers разбили файлы на группы"
echo "  • arch-reviewer и security-scanner смотрят на всё целиком"
echo "  • Haiku-скореры перепроверяют (confidence >=80)"
echo ""
echo "--- Ожидаемый результат ---"
echo "  31 raw → 25 unique после dedup → 18 Critical + 7 Important"
echo ""
echo "--- Cleanup после демо ---"
echo "  rm -rf $BASE"
echo ""
echo "==========================================="
echo "  cd $VULN_DIR && claude"
echo "==========================================="
