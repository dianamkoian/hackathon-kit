#!/usr/bin/env bash
# Делает тестовую звуковую дорожку из текста голосом macOS.
#
# Зачем: чтобы проверять распознавание речи, нужен звук, у которого заранее
# известен правильный текст. Тогда видно не "оно что-то распознало", а
# насколько точно. Файл не хранится в репозитории — он создаётся этой командой.
#
#   bash scripts/make_audio_fixture.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VOICE=${VOICE:-Milena}          # русский голос macOS
OUT=${1:-evals/audio/meeting.wav}
TEXT_FILE=evals/audio/meeting.txt

mkdir -p "$(dirname "$OUT")"

cat > "$TEXT_FILE" <<'TXT'
Анна: я отправлю обновлённую презентацию клиенту в четверг. Борис: принято. Марина: я возьму на себя переписывание документации, сделаю к пятнице.
TXT

# 16 килогерц, один канал, 16 бит — ровно то, что ждёт распознавание речи.
# В таком виде файл читается стандартными средствами, без лишних программ.
say -v "$VOICE" -o "$OUT" --data-format=LEI16@16000 -f "$TEXT_FILE"

python3 - "$OUT" <<'PY'
import sys, wave
with wave.open(sys.argv[1]) as w:
    seconds = w.getnframes() / w.getframerate()
    print(f"готово: {sys.argv[1]} — {seconds:.1f} сек, "
          f"{w.getframerate()} Гц, каналов: {w.getnchannels()}")
PY
