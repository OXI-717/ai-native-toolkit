#!/bin/bash
# Demo 2: /review full --no-fix на уязвимом приложении
# ПОЛНАЯ ИЗОЛЯЦИЯ: чистый HOME (auth тянется из Keychain), чистый клон.

set -e

SUFFIX=$(head -c 4 /dev/urandom | xxd -p)
DEMO_DIR="$HOME/demo-runs/review-$SUFFIX"
CLEAN_HOME="$HOME/demo-runs/review-home-$SUFFIX"
mkdir -p "$HOME/demo-runs"
REPO_URL="https://github.com/OXI-717/ai-native-toolkit.git"

echo "=== Demo 2: /review full --no-fix ==="
echo "Создаём полностью изолированную среду..."
echo ""

# Клонируем репу
git clone --quiet "$REPO_URL" "$DEMO_DIR"

# Чистый HOME для claude — auth подтянется из macOS Keychain
mkdir -p "$CLEAN_HOME/.claude"

# Глобальные настройки чистого HOME: marketplace + plugin enabled
cat > "$CLEAN_HOME/.claude/settings.json" <<'EOF'
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
    "review@ai-native-toolkit": true
  }
}
EOF

VULN_DIR="$DEMO_DIR/demo/vulnerable-saas"
echo "Рабочая директория: $VULN_DIR"
echo "Чистый HOME:        $CLEAN_HOME"
echo "Файлов в проекте:   $(find "$VULN_DIR" -name '*.ts' -o -name '*.tsx' -o -name '*.js' | wc -l | tr -d ' ')"
echo ""
echo "--- Запуск ---"
echo "Выполни:"
echo ""
echo "  cd $VULN_DIR && HOME=$CLEAN_HOME claude"
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
echo "  rm -rf $DEMO_DIR $CLEAN_HOME"
echo ""
echo "==========================================="
echo "  cd $VULN_DIR && HOME=$CLEAN_HOME claude"
echo "==========================================="
