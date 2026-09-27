#!/bin/bash
# Demo 2: /team-review full --no-fix на уязвимом приложении
# Изолированный клон в ./demo-runs/, локальный scope плагина через .claude/settings.local.json
# (auth берётся из твоего настоящего claude)

set -e

SUFFIX=$(head -c 4 /dev/urandom | xxd -p)
BASE="$PWD/demo-runs"
DEMO_DIR="$BASE/team-review-$SUFFIX"
mkdir -p "$BASE"
REPO_URL="https://github.com/OXI-717/ai-native-toolkit.git"

echo "=== Demo 2: /team-review full --no-fix ==="
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
    "team-review@ai-native-toolkit": true
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
echo "Плагин team-review@ai-native-toolkit подхватится автоматически — /plugin install не нужен."
echo ""
echo "--- Внутри claude: ДВА ШАГА для сравнительного демо ---"
echo ""
echo "ШАГ 1 — контрольная точка: ad-hoc ревью одним проходом (вставь промпт):"
echo ""
cat <<'PROMPT'
  Сделай code review всего проекта в этой папке сам, одним проходом в этой
  сессии — без субагентов и без плагинов. Найди уязвимости и баги, выдай
  список находок с файлом и строкой.
PROMPT
echo ""
echo "  Один агент читает все файлы и пишет отчёт сам — то, что получается"
echo "  без специальной инфраструктуры. Это контрольная точка."
echo "  Ожидание: ~1-2 минуты, результат — около 17 находок."
echo ""
echo "ШАГ 2 — мульти-агентный прогон плагином:"
echo ""
echo "  /team-review full --no-fix"
echo ""
echo "  Плагин сам поднимает параллельных Sonnet-агентов по ролям:"
echo "  ревьюеры по чанкам проекта плюс architecture-reviewer и"
echo "  security-scanner на весь проект (роли из commands/team-review.md),"
echo "  затем Haiku-скореры фильтруют находки по confidence >= 80."
echo ""
echo "Ожидание: ~2-3 минуты."
echo ""
echo "Комментировать пока агенты работают:"
echo "  • Sonnet-агенты стартовали параллельно — видны в правой панели"
echo "  • Каждый специалист — свой угол зрения"
echo "  • Они работают изолированно, главный агент собирает результаты"
echo ""
echo "--- Ожидаемый результат ---"
echo "  Один проход:       ~17 находок"
echo "  /team-review full: ~25 находок"
echo "  Прирост ~50% — за счёт:"
echo "    • architecture-reviewer: DTO/middleware/сервисный слой"
echo "    • chunk-ревьюеры: null-dereference на session, optimistic delete"
echo "    • chunk-ревьюеры: strict: false как root-cause всех null-багов, any[]"
echo "    • security-scanner: конкретная CVE в next@14.2.5 (нужно 14.2.35+)"
echo "    • chunk-ревьюеры: bare await req.json() без try/catch, inconsistent error shapes"
echo ""
echo "--- Cleanup после демо ---"
echo "  rm -rf $BASE"
echo ""
echo "==========================================="
echo "  cd $VULN_DIR && claude --permission-mode acceptEdits"
echo "==========================================="
