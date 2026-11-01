"""下絵動画の真っ黒なコマ（カメラが壁・岩・草むらの中）を探す。使い方: python tools/black_check.py A13 A14 …（previs/<シーン>/<シーン>-*.mp4 を見る）。
A07-003（井戸の底の闇）は演出どおりの暗さ。"""
import sys, glob, subprocess, numpy as np
for sc in sys.argv[1:]:
    for f in sorted(glob.glob(f"previs/{sc}/{sc}-*.mp4")):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", f, "-vf", "fps=4,scale=64:36,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 36, 64)
        m = a.reshape(len(a), -1).mean(1)
        bad = [i / 4 for i, v in enumerate(m) if v < 8]
        if bad:
            print(f, f"{len(bad)}/{len(m)} 黒", bad[:6])
print("ok")
