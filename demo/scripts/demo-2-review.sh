#!/bin/bash
# Demo 2: /review full --no-fix на уязвимом приложении
# Изолированный запуск: копирует vulnerable-saas в /tmp

set -e

DEMO_DIR="/tmp/demo-review-$$"
REPO_URL="https://github.com/OXI-717/ai-native-toolkit.git"

echo "=== Demo 2: /review full --no-fix ==="
echo "Создаём изолированную среду..."
echo ""

# Клонируем репу, переходим в vulnerable-saas
git clone --quiet "$REPO_URL" "$DEMO_DIR"
cd "$DEMO_DIR/demo/vulnerable-saas"

echo "Рабочая директория: $DEMO_DIR/demo/vulnerable-saas"
echo "Файлов в проекте: $(find . -name '*.ts' -o -name '*.tsx' -o -name '*.js' | wc -l | tr -d ' ')"
echo ""
echo "--- Шаг 1: Установка плагина (если ещё не установлен) ---"
echo "  /plugin marketplace add OXI-717/ai-native-toolkit"
echo "  /plugin install review@ai-native-toolkit"
echo ""
echo "--- Шаг 2: Запуск review ---"
echo "Выполни в Claude Code:"
echo ""
echo "  /review full --no-fix"
echo ""
echo "Ожидай ~2 минуты. Комментируй пока агенты работают:"
echo "  • 5 Sonnet-агентов стартовали параллельно"
echo "  • chunk-reviewers разбили файлы на группы"
echo "  • arch-reviewer и security-scanner смотрят на всё целиком"
echo "  • Haiku-скореры перепроверяют (confidence ≥80)"
echo ""
echo "--- Ожидаемый результат ---"
echo "  31 raw → 25 unique после dedup → 18 Critical + 7 Important"
echo ""
echo "==========================================="
echo "Claude Code запускай из: $DEMO_DIR/demo/vulnerable-saas"
echo "  cd $DEMO_DIR/demo/vulnerable-saas && claude"
echo "==========================================="
