#!/usr/bin/env python3
"""プレイ録画の「BGMと効果音」の分離音から、どの時刻にどの曲が流れているかを、既存の曲の一覧（audio/bgm/catalog.yaml の M01〜）と照らし合わせて出す（D-068）。

  python tools/bgm_split.py --stem tmp/src/part22_bgm.mp3 --part 22 [--from 0:00 --to 25:36] \
      [--ref tmp/src/part21_bgm.mp3:21] --out tmp/bgm_p22.json

やること:
  1. 0.1秒ごとのクロマ（音の高さの12の並び）と強さを出す
  2. 8秒の窓（1秒ずつずらす）を、既存の曲の窓（0.5秒ずつずらす）と比べ、曲ごとの似かた（0〜1）を出す。
     既存の曲の窓は、試聴用の切り出し（audio/bgm/catalog/Mxx.ogg）と、--ref で渡したパートの分離音のうち catalog.yaml の plays に
     書いてある範囲（試聴用の45秒より長く、曲の別の所も入る）
  3. 「どの曲が流れているか」を、似かたの合計が大きく、曲の切り替えが少なくなるように決める（Viterbi）。
     どの曲にも --match 未満の所は「ほか」、静かな所は「静か」
  4. 「ほか」の区間どうしを、同じ似かたでまとめて新しい曲の候補にする
結果は JSON（区間の始め・終わり・曲・似かた・2番目の候補）。catalog.yaml へ足すのは人が見てから。
効果音が混ざっているので、似かたは目安。耳で確かめていない（heard: false）。
"""
import argparse
import glob
import json
import pathlib
import re
import subprocess

import numpy as np
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SR = 22050
HOP = 2205          # 0.1秒
NFFT = 8192
W = 80              # 窓 8秒（0.1秒×80）


def load(path, start=None, dur=None):
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if dur is not None:
        cmd += ["-t", str(dur)]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype=np.float32)


def features(x):
    """0.1秒ごとのクロマ（12、正規化）と強さ。"""
    n = max(1, (len(x) - NFFT) // HOP + 1)
    win = np.hanning(NFFT).astype(np.float32)
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    sel = (freqs > 55) & (freqs < 4200)
    pc = (np.round(12 * np.log2(freqs[sel] / 440.0)) % 12).astype(int)
    chroma = np.zeros((n, 12), np.float32)
    rms = np.zeros(n, np.float32)
    for i in range(n):
        seg = x[i * HOP:i * HOP + NFFT]
        if len(seg) < NFFT:
            seg = np.pad(seg, (0, NFFT - len(seg)))
        p = np.abs(np.fft.rfft(seg * win)) ** 2
        chroma[i] = np.bincount(pc, weights=p[sel], minlength=12)
        rms[i] = np.sqrt(np.mean(seg ** 2))
    chroma = chroma / (np.linalg.norm(chroma, axis=1, keepdims=True) + 1e-9)
    return chroma, rms


def windows(chroma10, step):
    """8秒の窓のクロマを step（0.1秒単位）ずつずらして並べる（1つの窓は 8×12 に縮めて、平均を引いて正規化）。"""
    out = []
    for s in range(0, len(chroma10) - W + 1, step):
        v = chroma10[s:s + W].reshape(8, 10, 12).mean(1).ravel()
        v = v - v.mean()
        out.append(v / (np.linalg.norm(v) + 1e-9))
    return np.array(out).reshape(-1, 96)


def tc(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


def parse(s):
    m, x = s.split(":")
    return int(m) * 60 + float(x)


def refs(cat_items, ref_args):
    """既存の曲ごとの窓。試聴用の切り出し＋（あれば）--ref のパートの plays の範囲。"""
    out = {}
    for f in sorted(glob.glob(str(ROOT / "audio/bgm/catalog/M*.ogg"))):
        c, _ = features(load(f))
        out[pathlib.Path(f).stem] = [windows(c, 5)]
    for ra in ref_args or []:
        path, part = ra.rsplit(":", 1)
        c_all, _ = features(load(path))
        for it in cat_items:
            for pl in it.get("plays", []):
                m = re.match(rf"p{part} (\d+:\d+)-(\d+:\d+)$", pl)
                if not m:
                    continue
                a, b = int(parse(m.group(1)) * 10), int(parse(m.group(2)) * 10)
                if b - a >= W:
                    out.setdefault(it["id"], []).append(windows(c_all[a:b], 5))
    return {k: np.vstack(v) for k, v in out.items()}


def viterbi(E, penalty):
    """E: 時刻×状態 の得点。切り替えに penalty を払って、得点の合計が最大の並びを返す。"""
    T, K = E.shape
    score = E[0].copy()
    back = np.zeros((T, K), int)
    for t in range(1, T):
        best = int(np.argmax(score))
        stay = score
        move = score[best] - penalty
        back[t] = np.where(stay >= move, np.arange(K), best)
        score = np.maximum(stay, move) + E[t]
    path = [int(np.argmax(score))]
    for t in range(T - 1, 0, -1):
        path.append(back[t][path[-1]])
    return path[::-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    ap.add_argument("--part", type=int, required=True)
    ap.add_argument("--from", dest="t0", default="0:00")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--ref", action="append", help="分離音:パート番号（catalog.yaml の plays の範囲を既存の曲の窓に足す）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--match", type=float, default=0.6, help="既存の曲と同じとみなす似かた（これ未満は「ほか」）")
    ap.add_argument("--penalty", type=float, default=3.0, help="曲を切り替える重さ（似かたの秒数ぶん）")
    a = ap.parse_args()
    t0 = parse(a.t0)
    dur = parse(a.t1) - t0 if a.t1 else None
    ch, rms = features(load(a.stem, t0, dur))
    cat_items = yaml.safe_load((ROOT / "audio/bgm/catalog.yaml").read_text(encoding="utf-8"))["items"]
    R = refs(cat_items, a.ref)
    names = sorted(R)
    X = windows(ch, 10)                                   # 1秒ずつ。窓 i は i〜i+8秒
    S = np.stack([(X @ R[k].T).max(1) for k in names], 1)  # 時刻×曲
    T = len(X)
    loud = np.array([rms[i * 10:i * 10 + W].mean() for i in range(T)])
    quiet = loud < np.median(loud) * 0.15
    E = np.hstack([S, np.full((T, 1), a.match), np.zeros((T, 1))])
    E[quiet, -1] = 1.0
    E[quiet, :-1] -= 0.5
    labels = names + ["ほか", "静か"]
    path = viterbi(E, a.penalty)
    segs = []
    s = 0
    for t in range(1, T + 1):
        if t == T or path[t] != path[s]:
            k = path[s]
            mean = S[s:t].mean(0)
            order = np.argsort(-mean)
            end = t + 8 if t == T else t + 4        # 窓の中央でつなぐ（最後は窓の終わりまで）
            start = 0 if s == 0 else s + 4
            segs.append({"from": tc(t0 + start), "to": tc(t0 + end), "sec": end - start, "track": labels[k],
                         "sim": round(float(mean[k]), 3) if k < len(names) else None,
                         "best": [[names[j], round(float(mean[j]), 3)] for j in order[:3]], "_i": (s, t)})
            s = t
    # 「ほか」の区間どうしをまとめる
    groups = []
    for sg in segs:
        if sg["track"] != "ほか" or sg["sec"] < 8:
            continue
        Xi = X[sg["_i"][0]:sg["_i"][1]]
        for g in groups:
            m = (Xi @ g["X"].T).max(1)
            if np.mean(np.sort(m)[len(m) // 4:]) >= a.match:
                sg["new"] = g["id"]
                g["X"] = np.vstack([g["X"], Xi])
                break
        else:
            groups.append({"id": len(groups) + 1, "X": Xi})
            sg["new"] = len(groups)
    for sg in segs:
        sg.pop("_i")
    pathlib.Path(a.out).write_text(json.dumps({"part": a.part, "stem": a.stem, "segments": segs}, ensure_ascii=False, indent=1), encoding="utf-8")
    for sg in segs:
        lab = sg["track"] + (str(sg["new"]) if sg.get("new") else "")
        print(f"p{a.part} {sg['from']:>5s}-{sg['to']:>5s} {sg['sec']:4d}秒 {lab:6s} " + " ".join(f"{k}:{v:.2f}" for k, v in sg["best"]))


if __name__ == "__main__":
    main()
