#!/usr/bin/env bash
# Один раз после клонирования.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== проверки перед пушем =="
git config core.hooksPath .githooks
echo "включены: перед каждым push прогоняется смоук и сверка чисел в README"

echo
echo "== твоё имя в коммитах =="
email=$(git config user.email || true)
if [ -z "$email" ]; then
  echo "НЕ ЗАДАНО. Выполни, подставив свою почту:"
  echo "  git config user.email ты@example.com"
  echo "  git config user.name \"Твоё Имя\""
  echo "Историю потом не переписать, а как команда делила работу — отдельный критерий."
else
  echo "коммиты подписываются как: $(git config user.name || echo '?') <$email>"
fi

echo
echo "== зависимости =="
echo "если ещё не ставила: pip install -r requirements.txt"

echo
echo "== проверка, что всё работает =="
python3 -m evals.run | head -6
