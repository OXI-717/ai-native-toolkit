#!/usr/bin/env bash
# Render a scenario to mp4. Usage: make-video.sh <scenario.yaml> [--allow-js] [--keep] [--overwrite]
set -euo pipefail
ENGINE="$(cd "$(dirname "$0")" && pwd)"
SCENARIO="${1:?usage: make-video.sh <scenario.yaml> [flags]}"; shift || true
FLAGS=("$@")

for bin in node ffmpeg ffprobe; do command -v "$bin" >/dev/null || { echo "ERROR: $bin not found in PATH" >&2; exit 1; }; done

echo "▶ 1/2 driving browser (Playwright)"
RECORD_OUT="$(node "$ENGINE/record.mjs" "$SCENARIO" ${FLAGS[@]+"${FLAGS[@]}"})"
WEBM="$(printf '%s\n' "$RECORD_OUT" | sed -n 's/^VIDEO=//p' | tail -1)"
OUT="$(printf '%s\n' "$RECORD_OUT" | sed -n 's/^OUT=//p' | tail -1)"
[ -f "$WEBM" ] || { echo "ERROR: webm not produced" >&2; exit 1; }
[ -n "$OUT" ] || OUT="$(dirname "$SCENARIO")/out/$(basename "${SCENARIO%.*}").mp4"
mkdir -p "$(dirname "$OUT")"

echo "▶ 2/2 encoding mp4 → $OUT"
DUR="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$WEBM" 2>/dev/null || echo 0)"
ffmpeg -y -loglevel error -i "$WEBM" -movflags +faststart -pix_fmt yuv420p \
  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" "$OUT"

case " ${FLAGS[*]:-} " in *" --keep "*) : ;; *) rm -f "$WEBM"; rmdir "$(dirname "$WEBM")" 2>/dev/null || true ;; esac
echo "✔ done: $OUT ($(du -h "$OUT" | cut -f1), ${DUR}s)"
