#!/bin/bash
# Demo 3: Codex на голом репо — другой AI понимает проект без настройки
# Полностью изолированный: чистый HOME, чистый клон

set -e

SUFFIX=$(head -c 4 /dev/urandom | xxd -p)
DEMO_DIR="$HOME/demo-runs/codex-$SUFFIX"
CLEAN_HOME="$HOME/demo-runs/codex-home-$SUFFIX"
mkdir -p "$HOME/demo-runs"
REPO_URL="https://github.com/OXI-717/ai-native-toolkit.git"

echo "=== Demo 3: Codex на голом репо ==="
echo "Создаём полностью изолированную среду..."
echo ""

# Клонируем репу
git clone --quiet "$REPO_URL" "$DEMO_DIR"

# Создаём чистый HOME (только codex auth)
mkdir -p "$CLEAN_HOME"
if [ -f "$HOME/.codex/auth.json" ]; then
  mkdir -p "$CLEAN_HOME/.codex"
  cp "$HOME/.codex/auth.json" "$CLEAN_HOME/.codex/auth.json"
  echo "Скопирован auth.json для Codex"
fi

echo "Рабочая директория: $DEMO_DIR"
echo "Чистый HOME: $CLEAN_HOME"
echo ""
echo "--- Запуск Codex ---"
echo "Выполни:"
echo ""
echo "  cd $DEMO_DIR"
echo "  HOME=$CLEAN_HOME codex exec \\"
echo "    \"What is this repo? What plugins does it contain?\""
echo ""
echo "Или более сложный запрос:"
echo ""
echo "  HOME=$CLEAN_HOME codex exec \\"
echo "    \"Explain how the review plugin works. What agents does it run in parallel?\""
echo ""
echo "--- Ожидаемый результат ---"
echo "  За ~15 сек Codex перечислит все 6 плагинов"
echo "  и опишет архитектуру review пайплайна"
echo ""
echo "==========================================="
echo "  cd $DEMO_DIR && HOME=$CLEAN_HOME codex exec \"...\""
echo "==========================================="
