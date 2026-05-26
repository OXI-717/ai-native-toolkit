#!/bin/bash
# Demo 3: Codex на голом репо — другой AI понимает проект без настройки
# Полностью изолированный: чистый HOME, чистый клон

set -e

SUFFIX=$(head -c 4 /dev/urandom | xxd -p)
BASE="$PWD/demo-runs"
DEMO_DIR="$BASE/codex-$SUFFIX"
CLEAN_HOME="$BASE/codex-home-$SUFFIX"
mkdir -p "$BASE"
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
echo "Сначала перейди в demo-папку:"
echo ""
echo "  cd $DEMO_DIR"
echo ""
echo "--- Команда 1: что за репо и какие плагины (русский ответ) ---"
echo ""
echo "  HOME=$CLEAN_HOME codex exec \\"
echo "    \"Что это за репо? Какие плагины он содержит? Опиши каждый одной строкой. Отвечай на русском.\""
echo ""
echo "--- Команда 2: разбор архитектуры мульти-агентного review (русский ответ) ---"
echo ""
echo "  HOME=$CLEAN_HOME codex exec \\"
echo "    \"Объясни как работает плагин review. Каких агентов он запускает параллельно и зачем? Отвечай на русском.\""
echo ""
echo "--- Ожидаемый результат ---"
echo "  За ~30 сек Codex прочитает AGENTS.md и SKILL.md, перечислит все 6 плагинов"
echo "  и опишет архитектуру review-пайплайна — на русском, без настроек, без кеша."
echo ""
echo "==========================================="
echo "  cd $DEMO_DIR && HOME=$CLEAN_HOME codex exec \"...\""
echo "==========================================="
