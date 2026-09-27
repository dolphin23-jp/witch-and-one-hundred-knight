#!/bin/sh
# A01 を Remotion で書き出す。 sh animatic/remotion/render.sh A01 A01_remotion
# 入力は tools/scene_export.py が作る animatic/build/<シーン>/（scene.json と素材）
set -e
cd "$(dirname "$0")"
S=${1:-A01}; NAME=${2:-${S}_remotion}
npx remotion render src/index.ts Scene "../out/${NAME}.mp4" \
  --props="../build/${S}/scene.json" --public-dir="../build/${S}" \
  --browser-executable=/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell \
  --concurrency=2 --codec=h264 --crf=20 --log=info
