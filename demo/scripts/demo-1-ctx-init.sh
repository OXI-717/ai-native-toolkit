#!/bin/bash
# Demo 1: ctx-init — создание контекста за 10 секунд
# ПОЛНАЯ ИЗОЛЯЦИЯ: чистый HOME (auth тянется из Keychain), чистый клон.
# После запуска: rm -rf $CLEAN_HOME — никаких следов в твоём настоящем ~/.claude

set -e

SUFFIX=$(head -c 4 /dev/urandom | xxd -p)
BASE="$PWD/demo-runs"
DEMO_DIR="$BASE/ctx-init-$SUFFIX"
CLEAN_HOME="$BASE/ctx-init-home-$SUFFIX"
mkdir -p "$BASE"
REPO_URL="https://github.com/OXI-717/ai-native-toolkit.git"

echo "=== Demo 1: ctx-init ==="
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
    "ctx@ai-native-toolkit": true
  }
}
EOF

echo "Рабочая директория: $DEMO_DIR"
echo "Чистый HOME:        $CLEAN_HOME"
echo ""
echo "--- Запуск ---"
echo "Выполни:"
echo ""
echo "  cd $DEMO_DIR && HOME=$CLEAN_HOME claude"
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
echo "  rm -rf $DEMO_DIR $CLEAN_HOME"
echo ""
echo "==========================================="
echo "  cd $DEMO_DIR && HOME=$CLEAN_HOME claude"
echo "==========================================="
