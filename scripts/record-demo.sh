#!/usr/bin/env bash
# Превращает запись экрана с macOS в GIF, который GitHub показывает прямо в README.
#
#   bash scripts/record-demo.sh ~/Desktop/запись.mov docs/demo.gif
#
# GIF, а не mp4: markdown на GitHub не проигрывает видео внутри страницы, а
# жюри смотрит страницу, не скачивает файлы.
set -euo pipefail

IN=${1:?нужен путь к .mov (Cmd+Shift+5 пишет на рабочий стол)}
OUT=${2:-docs/demo.gif}
FPS=${FPS:-12}
WIDTH=${WIDTH:-1000}

# Ищем ffmpeg: сначала обычный, потом тот, что приезжает питоновским пакетом.
# Второй путь нужен на свежих macOS, где Homebrew отказывается собирать из-за
# устаревших инструментов разработчика Apple.
FFMPEG=$(command -v ffmpeg || true)
if [ -z "$FFMPEG" ]; then
  FFMPEG=$(python3 -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2>/dev/null || true)
fi
if [ -z "$FFMPEG" ]; then
  echo "нужен ffmpeg. Любой из двух способов:"
  echo "  pip install imageio-ffmpeg      (без пароля, работает всегда)"
  echo "  brew install ffmpeg             (нужны свежие Command Line Tools)"
  exit 1
fi
[ -f "$IN" ] || { echo "нет файла: $IN"; exit 1; }
mkdir -p "$(dirname "$OUT")"

PALETTE=$(mktemp -t palette).png
trap 'rm -f "$PALETTE"' EXIT

# Два прохода: своя палитра на 256 цветов даёт заметно чище картинку на
# тексте и интерфейсе, чем конвертация одним проходом.
"$FFMPEG" -loglevel error -y -i "$IN" \
  -vf "fps=$FPS,scale=$WIDTH:-1:flags=lanczos,palettegen=stats_mode=diff" "$PALETTE"
"$FFMPEG" -loglevel error -y -i "$IN" -i "$PALETTE" \
  -lavfi "fps=$FPS,scale=$WIDTH:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3" \
  "$OUT"

SIZE_MB=$(python3 -c "import os,sys; print(f'{os.path.getsize(sys.argv[1])/1048576:.1f}')" "$OUT")
echo "готово: $OUT (${SIZE_MB} МБ, ${FPS} fps, ширина ${WIDTH}px)"
awk -v s="$SIZE_MB" 'BEGIN { if (s+0 > 10) print "больше 10 МБ — GitHub будет грузить его медленно. Уменьшите: FPS=10 WIDTH=800 bash scripts/record-demo.sh ..." }'
