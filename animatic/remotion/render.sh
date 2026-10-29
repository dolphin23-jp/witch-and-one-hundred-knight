#!/bin/sh
# シーンの Animatic を Remotion で書き出す（組み立ての実装、D-050）。
#   sh animatic/remotion/render.sh episodes/A/scenes/A01 A01_v09
# 1. tools/scene_export.py がショットファイルと voice.yaml から animatic/build/<シーン>/ を作る
# 2. Remotion で画（音なし）と音（WAV）を別々に書き出す
# 画の画質は crf 28（A01 の 3分21秒で約22MB。チャットで送れる30MB未満に収める）
# 3. ffmpeg でまとめる。Remotion の MP4 に直接 AAC を入れると声が約1フレーム遅れるため（2026-09-27 に確認）
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCENE_DIR=${1:?シーンのフォルダ（例: episodes/A/scenes/A01）}
S=$(basename "$SCENE_DIR"); NAME=${2:-$S}
cd "$ROOT"
python3 tools/scene_export.py "$SCENE_DIR" --overlays
cd animatic/remotion
[ -d node_modules ] || npm install --no-audit --no-fund
# Playwright の版でフォルダ名が変わるので探す（chrome-linux/headless_shell か chrome-headless-shell-linux64/chrome-headless-shell）
BROWSER=${REMOTION_BROWSER:-$(ls /opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell /opt/pw-browsers/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | tail -1)}
COMMON="--props=../build/${S}/scene.json --public-dir=../build/${S} --browser-executable=${BROWSER} --log=error"
TMP="../build/${S}/_render"; mkdir -p "$TMP"
npx remotion render src/index.ts Scene "$TMP/video.mp4" --codec=h264 --crf=28 --muted --concurrency=2 $COMMON
npx remotion render src/index.ts Scene "$TMP/audio.wav" --codec=wav $COMMON
mkdir -p ../out
ffmpeg -hide_banner -loglevel error -y -i "$TMP/video.mp4" -i "$TMP/audio.wav" -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest "../out/${NAME}.mp4"
rm -rf "$TMP"
echo "animatic/out/${NAME}.mp4"
