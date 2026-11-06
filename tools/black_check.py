"""下絵動画の真っ黒なコマ（カメラが壁・岩・草むらの中）と、ほぼ一色のコマを探す。使い方: python tools/black_check.py A13 A14 …（previs/<シーン>/<シーン>-*.mp4 を見る）。
A07-003（井戸の底の闇）は演出どおりの暗さ。
一色: 画面の明るさのばらつきが小さいコマ（2026-11-06 の下絵のアップグレードで足した）。カメラが円い壁の外に出た（A31-003）、
霧や砂の塊の中に入った、大きな物が画面をふさいだ、などで出る。霧に包まれるカットなど、演出どおりのこともあるので目で確かめる。"""
import sys, glob, subprocess, numpy as np
for sc in sys.argv[1:]:
    for f in sorted(glob.glob(f"previs/{sc}/{sc}-*.mp4")):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", f, "-vf", "fps=4,scale=64:36,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 36, 64).astype(np.float32)
        m = a.reshape(len(a), -1).mean(1)
        bad = [i / 4 for i, v in enumerate(m) if v < 8]
        if bad:
            print(f, f"{len(bad)}/{len(m)} 黒", bad[:6])
        sd = a[:, 4:32, :].reshape(len(a), -1).std(1)   # 上下の黒帯を除く
        flat = [i / 4 for i, (v, s) in enumerate(zip(m, sd)) if v >= 8 and s < 6.0]
        if flat:
            print(f, f"{len(flat)}/{len(m)} 一色に近い", flat[:6])
print("ok")
