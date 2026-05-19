#!/bin/bash
# Demo 1: ctx-init — создание контекста за 10 секунд
# Изолированный запуск: клонирует репу в ~/demo-runs/, работает в чистой копии

set -e

SUFFIX=$(head -c 4 /dev/urandom | xxd -p)
DEMO_DIR="$HOME/demo-runs/ctx-init-$SUFFIX"
mkdir -p "$HOME/demo-runs"
REPO_URL="https://github.com/OXI-717/ai-native-toolkit.git"

echo "=== Demo 1: ctx-init ==="
echo "Создаём изолированную среду..."
echo ""

# Клонируем репу
git clone --quiet "$REPO_URL" "$DEMO_DIR"
cd "$DEMO_DIR"

echo "Рабочая директория: $DEMO_DIR"
echo ""
echo "--- Шаг 1: Установка плагина ---"
echo "Выполни в Claude Code:"
echo ""
echo "  /plugin marketplace add OXI-717/ai-native-toolkit"
echo "  /plugin install ctx@ai-native-toolkit"
echo ""
echo "--- Шаг 2: Запуск ctx-init ---"
echo "Выполни:"
echo ""
echo "  /ctx-init"
echo ""
echo "--- Шаг 3: Покажи результат ---"
echo "  ls -la AGENTS.md CLAUDE.md rules/"
echo ""
echo "  cat AGENTS.md"
echo ""
echo "==========================================="
echo "Claude Code запускай из: $DEMO_DIR"
echo "  cd $DEMO_DIR && claude"
echo "==========================================="
