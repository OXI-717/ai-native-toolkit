#!/bin/bash
# Demo 1: ctx-init — создание контекста за 10 секунд
# Изолированный клон в ./demo-runs/, локальный scope плагина через .claude/settings.local.json
# (auth берётся из твоего настоящего claude)

set -e

SUFFIX=$(head -c 4 /dev/urandom | xxd -p)
BASE="$PWD/demo-runs"
DEMO_DIR="$BASE/ctx-init-$SUFFIX"
mkdir -p "$BASE"
REPO_URL="https://github.com/OXI-717/ai-native-toolkit.git"

echo "=== Demo 1: ctx-init ==="
echo "Создаём изолированный клон..."
echo ""

# Клонируем репу
git clone --quiet "$REPO_URL" "$DEMO_DIR"

# Локальный scope: плагин активен ТОЛЬКО когда claude запущен из этой папки
mkdir -p "$DEMO_DIR/.claude"
cat > "$DEMO_DIR/.claude/settings.local.json" <<'EOF'
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
    "ctx@ai-native-toolkit": true,
    "oxi-ctx@oxi-skills": false
  }
}
EOF

echo "Рабочая директория: $DEMO_DIR"
echo "Scope: .claude/settings.local.json (только эта папка)"
echo ""
echo "--- Запуск ---"
echo "Выполни:"
echo ""
echo "  cd $DEMO_DIR && claude"
echo ""
echo "Плагин ctx@ai-native-toolkit подхватится автоматически — /plugin install не нужен."
echo ""
echo "--- Внутри claude ---"
echo "  /ctx-init"
echo ""
echo "Ответь на 3 вопроса:"
echo "  1. project (например, SPACE-DEMO)"
echo "  2. description"
echo "  3. language: en или ru"
echo ""
echo "--- Покажи результат ---"
echo "  ls -la AGENTS.md CLAUDE.md rules/"
echo "  cat AGENTS.md"
echo ""
echo "--- Cleanup после демо ---"
echo "  rm -rf $BASE"
echo ""
echo "==========================================="
echo "  cd $DEMO_DIR && claude"
echo "==========================================="
