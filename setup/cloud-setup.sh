#!/bin/bash
# claude.ai/code の環境設定 →「セットアップスクリプト」に、この中身を貼る。
# 初回だけ実行され、結果はキャッシュされる（約7日ごとに再実行）。
# 失敗しても起動を止めないよう、各行に || true を付けている。

apt-get update -y || true
apt-get install -y ffmpeg || true          # 動画の書き出し、フレームの抜き出し
apt-get install -y fonts-noto-cjk || true  # 日本語フォント（テロップ、HTMLの描画）
pip install --break-system-packages pyyaml || true
exit 0
