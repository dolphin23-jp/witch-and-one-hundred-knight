#!/bin/sh
# A01 を HyperFrames で書き出す。 sh animatic/hyperframes/render.sh A01 A01_hyperframes
# 入力は tools/scene_export.py が作る animatic/build/<シーン>/。index.html は build_html.py が作る
set -e
cd "$(dirname "$0")"
S=${1:-A01}; NAME=${2:-${S}_hyperframes}
python3 build_html.py "$S"
export HYPERFRAMES_BROWSER_PATH=/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell
export HYPERFRAMES_NO_UPDATE_CHECK=1 HYPERFRAMES_NO_TELEMETRY=1 HYPERFRAMES_NO_AUTO_INSTALL=1
npx hyperframes render "../build/${S}" -o "../out/${NAME}.mp4" --fps 24 --quality standard --workers 2
