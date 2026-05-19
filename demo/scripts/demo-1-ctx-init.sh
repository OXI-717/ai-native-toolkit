#!/bin/bash
# Demo 1: ctx-init — создание контекста за 10 секунд
# Изолированный запуск: клонирует репу в ~/demo-runs/, локальный scope плагина (только эта папка)

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

# Локальный scope: плагин подцепится ТОЛЬКО когда claude запущен из этой папки
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
    "ctx@ai-native-toolkit": true
  }
}
EOF

echo "Рабочая директория: $DEMO_DIR"
echo "Scope: только эта папка (.claude/settings.local.json)"
echo ""
echo "--- Шаг 1: Запусти claude из demo-папки ---"
echo "  cd $DEMO_DIR && claude"
echo ""
echo "Плагин ctx@ai-native-toolkit подхватится автоматически — /plugin install не нужен."
echo ""
echo "--- Шаг 2: Запусти /ctx-init ---"
echo "  /ctx-init"
echo ""
echo "Ответь на 3 вопроса:"
echo "  1. project name (например, SPACE-DEMO)"
echo "  2. description"
echo "  3. language: en или ru"
echo ""
echo "--- Шаг 3: Покажи результат ---"
echo "  ls -la AGENTS.md CLAUDE.md rules/"
echo "  cat AGENTS.md"
echo ""
echo "==========================================="
echo "  cd $DEMO_DIR && claude"
echo "==========================================="
