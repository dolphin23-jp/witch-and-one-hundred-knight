#!/usr/bin/env python3
"""静止画を部品に分けて、コードで動かす（2.5D のリミテッドアニメ。2026-11-04 の試作）。

  $VENV/bin/python tools/celanim.py prep    cel/A02/A02-002.yaml   # 人物の切り抜き・背景の穴埋め・奥行き（cel/<場面>/build/）
  $VENV/bin/python tools/celanim.py render  cel/A02/A02-002.yaml   # 動かして書き出す（cel/<場面>/<カット>_v01.mp4、原作の声と BGM 付き）
  $VENV/bin/python tools/celanim.py compare cel/A02/A02-002.yaml   # Seedance 版と上下に並べる（<カット>_compare.mp4）
  $VENV/bin/python tools/celanim.py check   cel/A02/A02-002.yaml   # 0.5秒ごとのコマを1枚に並べる（<カット>_check.jpg）

$VENV は `python3 -m venv` に bpy pyyaml pillow numpy opencv-python-headless onnxruntime scipy を入れたもの。
モデル（ONNX、CPU で動く）は環境変数 CEL_MODELS のフォルダに置く:
  isnetis.onnx   アニメ画の人物の切り抜き（skytnt/anime-seg）
  lama_fp32.onnx 穴埋め（Carve/LaMa-ONNX）
  da2_small.onnx 奥行き（onnx-community/depth-anything-v2-small）

考え方（docs/log.md 2026-11-04 の「コードで動かす方式」）:
- 背景は人物を抜いて穴を埋め、奥行きの推定で立体にしてカメラを動かす（視差）。
- 人物は切り抜いた層（セル）。呼吸・体の揺れ・頭の小さな動き・髪の揺れ・口パク・まばたきをコードで変形・差し替える。
- 人物は2コマ打ち（1秒12回の更新）、カメラは毎コマ（24fps）。
- 原作の声の音量で口を開け閉めし、頭を小さく動かす。
"""
import argparse
import math
import os
import pathlib
import subprocess
import sys
import wave

import cv2
import numpy as np
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODELS = pathlib.Path(os.environ.get("CEL_MODELS", ROOT / "tmp" / "models"))
W, H, FPS = 1920, 1080, 24
CHAR_FPS = 12          # 人物は2コマ打ち


# ---------------------------------------------------------------- 準備（切り抜き・穴埋め・奥行き）

def _ort(name):
    import onnxruntime as ort
    p = MODELS / name
    if not p.exists():
        sys.exit(f"モデルがない: {p}（CEL_MODELS を確かめる）")
    so = ort.SessionOptions()
    so.intra_op_num_threads = os.cpu_count() or 4
    return ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])


def seg_mask(rgb):
    """アニメ画の人物の切り抜き（0..1）。skytnt/anime-seg の推論と同じ前処理。"""
    s = 1024
    img = rgb.astype(np.float32) / 255
    h0, w0 = img.shape[:2]
    h, w = (s, int(s * w0 / h0)) if h0 > w0 else (int(s * h0 / w0), s)
    ph, pw = s - h, s - w
    x = np.zeros([s, s, 3], np.float32)
    x[ph // 2:ph // 2 + h, pw // 2:pw // 2 + w] = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    m = _ort("isnetis.onnx").run(None, {"img": x.transpose(2, 0, 1)[None]})[0][0][0]
    m = m[ph // 2:ph // 2 + h, pw // 2:pw // 2 + w]
    return cv2.resize(m, (w0, h0), interpolation=cv2.INTER_CUBIC).clip(0, 1)


def inpaint(rgb, hole):
    """穴（hole=1）を LaMa で埋める。穴のまわりを切り出して 512 で埋め、元の大きさに戻して穴の中だけ使う。"""
    sess = _ort("lama_fp32.onnx")
    ys, xs = np.nonzero(hole)
    if len(xs) == 0:
        return rgb.copy()
    h0, w0 = hole.shape
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    side = int(max(x1 - x0, y1 - y0) * 1.35) + 64
    side = min(side, max(h0, w0))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    bx0 = int(np.clip(cx - side // 2, 0, max(0, w0 - side)))
    by0 = int(np.clip(cy - side // 2, 0, max(0, h0 - side)))
    bw, bh = min(side, w0), min(side, h0)
    crop = rgb[by0:by0 + bh, bx0:bx0 + bw]
    hc = hole[by0:by0 + bh, bx0:bx0 + bw]
    im = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    mk = (cv2.resize(hc.astype(np.float32), (512, 512), interpolation=cv2.INTER_AREA) > 0.01).astype(np.float32)
    out = sess.run(None, {"image": im.transpose(2, 0, 1)[None], "mask": mk[None, None]})[0][0].transpose(1, 2, 0)
    if out.max() <= 1.5:
        out = out * 255
    out = cv2.resize(out.clip(0, 255).astype(np.uint8), (bw, bh), interpolation=cv2.INTER_CUBIC)
    # 粗さをごまかす: 元の解像度の細かい揺らぎ（ノイズ）を少し足す
    noise = np.random.default_rng(0).normal(0, 2.0, out.shape).astype(np.float32)
    out = (out.astype(np.float32) + noise).clip(0, 255).astype(np.uint8)
    res = rgb.copy()
    a = cv2.GaussianBlur(hc.astype(np.float32), (0, 0), 3)[..., None]
    res[by0:by0 + bh, bx0:bx0 + bw] = (crop * (1 - a) + out * a).astype(np.uint8)
    return res


def depth_map(rgb):
    """奥行き（0=遠い 1=近い）。Depth Anything V2 small。"""
    sess = _ort("da2_small.onnx")
    h0, w0 = rgb.shape[:2]
    tw = 518
    th = int(round(tw * h0 / w0 / 14)) * 14
    x = cv2.resize(rgb, (tw, th), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    d = sess.run(None, {"pixel_values": x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
    d = cv2.resize(d, (w0, h0), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
    d = ((d - lo) / max(hi - lo, 1e-6)).clip(0, 1)
    return cv2.GaussianBlur(d, (0, 0), 6)       # 境目で背景が裂けないよう、なめらかにする


def load_rgb(p):
    im = cv2.imread(str(ROOT / p), cv2.IMREAD_COLOR)
    if im is None:
        sys.exit(f"画像がない: {p}（tools/archive.py restore で戻す）")
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def save_rgb(p, rgb):
    p.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(p), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def build_dir(spec):
    return ROOT / "cel" / spec["scene_id"] / "build" / spec["cut"]


def prep(spec):
    bd = build_dir(spec)
    bd.mkdir(parents=True, exist_ok=True)
    for name, pl in spec["plates"].items():
        rgb = load_rgb(pl["src"])
        m = seg_mask(rgb)
        for x0, y0, x1, y1 in pl.get("seg_box", []):    # 小さい人物: まわりを切り出して拡大して切り抜く
            m[y0:y1, x0:x1] = np.maximum(m[y0:y1, x0:x1], seg_mask(rgb[y0:y1, x0:x1]))
        for box in pl.get("not_char", []):          # 人物と間違えた所（柱など）を外す
            x0, y0, x1, y1 = box
            m[y0:y1, x0:x1] = 0
        alpha = ((m - 0.25) / 0.5).clip(0, 1)
        hole = cv2.dilate((m > 0.08).astype(np.uint8), np.ones((25, 25), np.uint8))
        bg = inpaint(rgb, hole)
        dep = depth_map(bg)
        cv2.imwrite(str(bd / f"{name}_alpha.png"), (alpha * 255).astype(np.uint8))
        save_rgb(bd / f"{name}_bg.png", bg)
        cv2.imwrite(str(bd / f"{name}_depth.png"), (dep * 255).astype(np.uint8))
        print(f"{name}: 人物 {alpha.mean() * 100:.1f}%、穴埋め・奥行き → {bd}")


# ---------------------------------------------------------------- 時間の関数

def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def keyed(keys, t, field, default=0.0):
    """キー（t と値）の間をなめらかにつなぐ。ease: linear なら直線。"""
    ks = [k for k in keys if field in k]
    if not ks:
        return default
    if t <= ks[0]["t"]:
        return ks[0][field]
    for a, b in zip(ks, ks[1:]):
        if a["t"] <= t <= b["t"]:
            u = (t - a["t"]) / max(b["t"] - a["t"], 1e-6)
            e = b.get("ease", "smooth")
            u = u if e == "linear" else (u ** 3 if e == "in" else (1 - (1 - u) ** 3 if e == "out" else ease(u)))
            return a[field] + (b[field] - a[field]) * u
    return ks[-1][field]


def voice_env(spec):
    """カットの中の声の音量（1/48秒ごと、0..1）。話している人ごと。"""
    sd = ROOT / spec["scene"]
    vy = yaml.safe_load(open(sd / "voice.yaml"))
    tc = spec["tc"]
    n = int(spec["sec"] * 48) + 1
    env = {}
    for ln in vy["lines"]:
        at = ln["at"] - tc
        if at >= spec["sec"] or at < -30:
            continue
        files = sorted((ROOT / "audio" / vy["scene"] / "original").glob(f"{ln['id']}_*.wav"))
        if not files:
            continue
        who = files[0].stem.split("_", 1)[1]
        with wave.open(str(files[0])) as wf:
            sr, ch, sw = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
            a = np.frombuffer(wf.readframes(wf.getnframes()), dtype={2: np.int16, 4: np.int32}[sw]).astype(np.float32)
        a = a.reshape(-1, ch).mean(1)
        hop = sr // 48
        rms = np.sqrt(np.convolve(a ** 2, np.ones(hop) / hop, mode="same")[::hop])
        rms = rms / (np.percentile(rms, 98) + 1e-6)
        e = env.setdefault(who, np.zeros(n, np.float32))
        i0 = int(round(at * 48))
        for i, v in enumerate(rms):
            j = i0 + i
            if 0 <= j < n:
                e[j] = max(e[j], min(v, 1.0))
    return env


def env_at(env, who, t, smooth=0):
    e = env.get(who)
    if e is None:
        return 0.0
    i = int(t * 48)
    lo, hi = max(0, i - smooth), min(len(e), i + smooth + 1)
    return float(e[lo:hi].mean()) if hi > lo else 0.0


# ---------------------------------------------------------------- 口とまばたき（差し替えの絵をコードで描く）

def _aa_draw(canvas, fn, ss=4):
    """4倍で描いて縮める（ふちをなめらかに）。canvas は RGBA float。fn(img) が 4倍の RGBA に描く。"""
    h, w = canvas.shape[:2]
    big = np.zeros((h * ss, w * ss, 4), np.float32)
    fn(big, ss)
    small = cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA)
    a = small[..., 3:4]
    canvas[..., :3] = canvas[..., :3] * (1 - a) + small[..., :3] * a
    return canvas


def mouth_variants(cel, m):
    """口の差し替え: closed / half / open。m: {center, w, kind: line|open, ...}。cel は RGBA uint8（板の座標）。"""
    cx, cy = m["center"]
    mw = m["w"]
    r = int(mw * 1.6)
    x0, y0 = cx - r, cy - r
    patch = cel[y0:y0 + 2 * r, x0:x0 + 2 * r].astype(np.float32)
    out = {}
    line_c = tuple(float(v) for v in m.get("line", [88, 48, 52]))
    inner_c = tuple(float(v) for v in m.get("inner", [118, 46, 56]))
    tongue_c = tuple(float(v) for v in m.get("tongue", [214, 118, 122]))
    if m["kind"] == "open":          # 元の絵が開いた口: 閉じた口は肌で埋めて線を描く
        hole = np.zeros(patch.shape[:2], np.uint8)
        cv2.ellipse(hole, (r, r), (int(mw * 0.62), int(m["h"] * 0.62)), 0, 0, 360, 255, -1)
        skin = cv2.inpaint(patch[..., :3].astype(np.uint8), hole, 6, cv2.INPAINT_TELEA).astype(np.float32)
        base = patch.copy()
        base[..., :3] = skin
        out["open"] = patch.copy()

        def closed_line(img, ss):
            pts = np.array([[r - mw * 0.32, r + 1], [r, r + 3], [r + mw * 0.32, r + 1]]) * ss
            cv2.polylines(img, [pts.astype(np.int32)], False, (*line_c, 1.0), int(2.2 * ss), cv2.LINE_AA)
        out["closed"] = _aa_draw(base.copy(), closed_line)
        half = base.copy()
        # 開いた口の絵を縦に縮めて重ねる
        oh = int(m["h"] * 1.3)
        src = patch[r - oh:r + oh, r - int(mw * 0.8):r + int(mw * 0.8)]
        sq = cv2.resize(src, (src.shape[1], max(2, int(src.shape[0] * 0.5))), interpolation=cv2.INTER_AREA)
        hm = np.zeros(src.shape[:2], np.float32)
        cv2.ellipse(hm, (src.shape[1] // 2, src.shape[0] // 2), (int(mw * 0.62), int(m["h"] * 0.62)), 0, 0, 360, 1.0, -1)
        hm = cv2.resize(cv2.GaussianBlur(hm, (0, 0), 1.2), (sq.shape[1], sq.shape[0]))[..., None]
        yy = r - sq.shape[0] // 2
        xx = r - sq.shape[1] // 2
        reg = half[yy:yy + sq.shape[0], xx:xx + sq.shape[1]]
        reg[..., :3] = reg[..., :3] * (1 - hm) + sq[..., :3] * hm
        out["half"] = half
    else:                            # 元の絵が閉じた口（線）: 線の下に開いた口を描く
        out["closed"] = patch.copy()
        for name, hh in (("half", m["h"] * 0.5), ("open", m["h"])):
            def draw(img, ss, hh=hh):
                c = (int((r + m.get("dx", 0)) * ss), int((r + hh * 0.42) * ss))
                ax = (int(mw * 0.36 * ss), int(hh * 0.5 * ss))
                cv2.ellipse(img, c, ax, 0, 0, 360, (*inner_c, 1.0), -1, cv2.LINE_AA)
                if hh > m["h"] * 0.7:      # 舌（下）と上の歯（白い帯）
                    cv2.ellipse(img, (c[0], c[1] + int(ax[1] * 0.55)), (int(ax[0] * 0.6), int(ax[1] * 0.45)), 0, 180, 360,
                                (*tongue_c, 1.0), -1, cv2.LINE_AA)
                    cv2.ellipse(img, (c[0], c[1] - int(ax[1] * 0.78)), (int(ax[0] * 0.72), int(ax[1] * 0.2)), 0, 0, 180,
                                (246.0, 242.0, 240.0, 1.0), -1, cv2.LINE_AA)
                cv2.ellipse(img, c, ax, 0, 0, 360, (*line_c, 1.0), int(1.1 * ss), cv2.LINE_AA)
                pts = np.array([[r - mw * 0.5, r], [r, r - 1], [r + mw * 0.5, r]]) * ss
                cv2.polylines(img, [pts.astype(np.int32)], False, (*line_c, 1.0), int(2.0 * ss), cv2.LINE_AA)
            out[name] = _aa_draw(patch.copy(), draw)
    return {k: (v.clip(0, 255).astype(np.uint8), (x0, y0)) for k, v in out.items()}


def blink_variant(cel, eyes):
    """目を閉じた絵: 目の中を肌で埋め、閉じたまぶたの線を描く。eyes: [{box, lid:[[x,y]...], w}]"""
    res = []
    for e in eyes:
        x0, y0, x1, y1 = e["box"]
        pad = 12
        patch = cel[y0 - pad:y1 + pad, x0 - pad:x1 + pad].astype(np.float32)
        hole = np.zeros(patch.shape[:2], np.uint8)
        poly = (np.array(e["hole"]) - [x0 - pad, y0 - pad]).astype(np.int32)
        cv2.fillPoly(hole, [poly], 255)
        if "skin" in e:
            skin_c = np.array(e["skin"], np.float32)
        else:                       # まわりの明るい肌の色の中央値
            ring = cv2.dilate(hole, np.ones((31, 31), np.uint8)) - cv2.dilate(hole, np.ones((9, 9), np.uint8))
            px = patch[..., :3][ring > 0]
            px = px[px.mean(1) > 200]
            skin_c = np.median(px, 0) if len(px) else np.array([250, 226, 214], np.float32)
        filled = patch.copy()
        hm = cv2.GaussianBlur(hole.astype(np.float32) / 255, (0, 0), 2.0)[..., None]
        # まわりの肌の色で塗る（ぼかした肌の色＋少しの陰）
        filled[..., :3] = filled[..., :3] * (1 - hm) + skin_c * hm
        lid = (np.array(e["lid"], np.float32) - [x0 - pad, y0 - pad])

        def draw(img, ss):
            cv2.polylines(img, [(lid * ss).astype(np.int32)], False, (40, 34, 52, 1.0), int(e.get("w", 5) * ss), cv2.LINE_AA)
        filled = _aa_draw(filled, draw)
        res.append((filled.clip(0, 255).astype(np.uint8), (x0 - pad, y0 - pad)))
    return res


def paste(cel, patch, at):
    x, y = at
    h, w = patch.shape[:2]
    cel[y:y + h, x:x + w] = patch


# ---------------------------------------------------------------- 効果（背景と空気）

def fbm(h, w, seed, octaves=5, base=6):
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        n = base * 2 ** o
        g = rng.random((n, n * w // h + 1)).astype(np.float32)
        acc += cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC) * amp
        tot += amp
        amp *= 0.5
    acc /= tot
    return (acc - acc.min()) / (acc.max() - acc.min() + 1e-6)


class Motes:
    """空気中に漂う光の粒や砂粒（画面の座標）。"""

    def __init__(self, n, seed, color, size, speed, region=(0, 0, W, H), alpha=0.5):
        rng = np.random.default_rng(seed)
        self.p = rng.random((n, 2)) * [region[2] - region[0], region[3] - region[1]] + region[:2]
        self.v = (rng.random((n, 2)) - 0.5) * 2 * speed + np.array(speed if isinstance(speed, list) else [0, 0])
        self.s = rng.uniform(size[0], size[1], n)
        self.ph = rng.random(n) * 6.28
        self.color = np.array(color, np.float32) / 255
        self.alpha = alpha
        self.region = region

    def draw(self, img, t, drift=(0, 0)):
        x0, y0, x1, y1 = self.region
        pos = self.p + self.v * t + np.array(drift)
        pos[:, 0] = x0 + np.mod(pos[:, 0] - x0, x1 - x0)
        pos[:, 1] = y0 + np.mod(pos[:, 1] - y0, y1 - y0)
        for (x, y), s, ph in zip(pos, self.s, self.ph):
            a = self.alpha * (0.6 + 0.4 * math.sin(t * 2.0 + ph))
            r = int(s * 3)
            xi, yi = int(x), int(y)
            if r < 1 or xi - r < 0 or yi - r < 0 or xi + r >= W or yi + r >= H:
                continue
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
            g = np.exp(-(xx ** 2 + yy ** 2) / (2 * s * s)).astype(np.float32)[..., None] * a
            reg = img[yi - r:yi + r + 1, xi - r:xi + r + 1]
            reg[:] = reg * (1 - g) + self.color * g


# ---------------------------------------------------------------- 描画

class Plate:
    def __init__(self, spec, name, pl):
        bd = build_dir(spec)
        self.name = name
        self.pl = pl
        self.bg = cv2.cvtColor(cv2.imread(str(bd / f"{name}_bg.png")), cv2.COLOR_BGR2RGB)
        self.depth = cv2.imread(str(bd / f"{name}_depth.png"), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255
        src = load_rgb(pl["src"])
        a = cv2.imread(str(bd / f"{name}_alpha.png"), cv2.IMREAD_GRAYSCALE)
        self.cel = np.dstack([src, a])
        self.h, self.w = self.bg.shape[:2]
        self.masks = {}
        for fx in pl.get("bg_fx", []):
            if fx["type"] == "glow":
                hsv = cv2.cvtColor(self.bg, cv2.COLOR_RGB2HSV).astype(np.float32)
                hlo, hhi = fx["hue"]
                m = ((hsv[..., 0] >= hlo) & (hsv[..., 0] <= hhi) & (hsv[..., 1] > fx.get("sat", 90)) & (hsv[..., 2] > fx.get("val", 120)))
                self.masks[id(fx)] = cv2.GaussianBlur(m.astype(np.float32), (0, 0), fx.get("blur", 8))
        for an in pl.get("anim", []):
            if an["type"] in ("hair", "flame", "glint"):
                x0, y0, x1, y1 = an["box"]
                hsv = cv2.cvtColor(src, cv2.COLOR_RGB2HSV).astype(np.float32)
                sel = np.ones(src.shape[:2], bool)
                if "vmax" in an:
                    sel &= hsv[..., 2] <= an["vmax"]
                if "hue" in an:
                    sel &= (hsv[..., 0] >= an["hue"][0]) & (hsv[..., 0] <= an["hue"][1])
                if "smin" in an:
                    sel &= hsv[..., 1] >= an["smin"]
                if "smax" in an:
                    sel &= hsv[..., 1] <= an["smax"]
                if "vmin" in an:
                    sel &= hsv[..., 2] >= an["vmin"]
                m = np.zeros(src.shape[:2], np.float32)
                m[y0:y1, x0:x1] = sel[y0:y1, x0:x1]
                m *= (a.astype(np.float32) / 255) if an["type"] != "flame" else 1
                self.masks[id(an)] = cv2.GaussianBlur(m, (0, 0), an.get("blur", 10))
        self.cels = {}
        self.mouth = None
        if pl.get("mouth"):
            self.mouth = mouth_variants(self.cel, pl["mouth"])
        self.blink = blink_variant(self.cel, pl["blink"]["eyes"]) if pl.get("blink") else None

    def cel_for(self, mouth, eyes_closed, base=None):
        key = (mouth, eyes_closed, id(base) if base is not None else 0)
        if key in self.cels:
            return self.cels[key]
        c = (base if base is not None else self.cel).copy()
        if self.mouth and mouth in self.mouth and base is None:
            p, at = self.mouth[mouth]
            paste(c, p, at)
        if eyes_closed and self.blink and base is None:
            for p, at in self.blink:
                paste(c, p, at)
        if len(self.cels) > 24:
            self.cels.clear()
        self.cels[key] = c
        return c


def flame_disp(X, Y, m, an, t):
    w = 2 * math.pi * an["hz"] * t
    fx = m * an["amp"] * (np.sin(Y / 23 + w) + 0.5 * np.sin(Y / 9 - w * 1.7)).astype(np.float32)
    fy = m * an["amp"] * 0.8 * (np.sin(X / 31 - w * 1.3) + 0.4 * np.sin(X / 13 + w * 2.1)).astype(np.float32) - m * an.get("rise", 0.0)
    return fx, fy


def smooth_box(X, Y, box, feather):
    x0, y0, x1, y1 = box
    fx = np.clip(np.minimum(X - x0, x1 - X) / feather, 0, 1)
    fy = np.clip(np.minimum(Y - y0, y1 - Y) / feather, 0, 1)
    return (fx * fy).astype(np.float32)


def rot_disp(X, Y, c, deg):
    th = math.radians(deg)
    dx, dy = X - c[0], Y - c[1]
    # 後ろ向きの写像: 回した後の点 → 元の点（-θ で回す）
    sx = c[0] + dx * math.cos(-th) - dy * math.sin(-th)
    sy = c[1] + dx * math.sin(-th) + dy * math.cos(-th)
    return sx - X, sy - Y


class Renderer:
    def __init__(self, spec):
        self.spec = spec
        self.plates = {n: Plate(spec, n, pl) for n, pl in spec["plates"].items()}
        self.env = voice_env(spec)
        u, v = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        self.u, self.v = u - W / 2, v - H / 2
        self.mist = {}
        self.motes = []
        for i, fx in enumerate(spec.get("screen_fx", [])):
            if fx["type"] == "motes":
                self.motes.append((fx, Motes(fx["n"], 10 + i, fx["color"], fx["size"], fx["speed"], tuple(fx.get("region", (0, 0, W, H))), fx.get("alpha", 0.5))))
        self.swap = {}       # 板の人物を別の板の人物に差し替える（姿勢 A→B）
        for sw in spec.get("swaps", []):
            src = self.plates[sw["to"]]
            dst = self.plates[sw["plate"]]
            Hm = align(dst, src, sw.get("exclude", []))
            warped = cv2.warpPerspective(src.cel, Hm, (dst.w, dst.h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
            self.swap[sw["plate"]] = (sw, warped, Hm)
            # 差し替えた後の口とまばたきは、差し替え先の板の座標の指定を変換して使う
            if src.mouth:
                mv = {}
                for k, (p, at) in src.mouth.items():
                    full = src.cel.copy()
                    full[at[1]:at[1] + p.shape[0], at[0]:at[0] + p.shape[1], :3] = p[..., :3]
                    mv[k] = cv2.warpPerspective(full, Hm, (dst.w, dst.h), borderValue=(0, 0, 0, 0))
                self.swap[sw["plate"]] = (sw, warped, Hm, mv)

    def camera(self, pname, t):
        keys = self.spec["camera"][pname]
        pl = self.plates[pname]
        c = {f: keyed(keys, t, f, d) for f, d in (("cx", pl.w / 2), ("cy", pl.h / 2), ("zoom", 1.0), ("tx", 0.0), ("ty", 0.0),
                                                     ("dz", 0.0), ("roll", 0.0), ("shake", 0.0))}
        if c["shake"]:
            c["cx"] += c["shake"] * (math.sin(t * 13.1) + 0.6 * math.sin(t * 29.7 + 1))
            c["cy"] += c["shake"] * (math.sin(t * 11.3 + 2) + 0.6 * math.sin(t * 31.1))
        return c

    def base_maps(self, pl, cam):
        s = pl.w / (W * cam["zoom"])
        X = cam["cx"] + self.u * s
        Y = cam["cy"] + self.v * s
        if cam["roll"]:
            th = math.radians(cam["roll"])
            dx, dy = X - cam["cx"], Y - cam["cy"]
            X, Y = cam["cx"] + dx * math.cos(th) - dy * math.sin(th), cam["cy"] + dx * math.sin(th) + dy * math.cos(th)
        return X.astype(np.float32), Y.astype(np.float32)

    def render_plate(self, pname, t, tc):
        pl = self.plates[pname]
        P = pl.pl
        cam = self.camera(pname, t)
        X, Y = self.base_maps(pl, cam)
        # --- 背景（奥行きで視差）
        D = cv2.remap(pl.depth, X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        par = P.get("bg_parallax", 1.0)
        bx = X - D * cam["tx"] * par - (X - cam["cx"]) * D * cam["dz"] * par
        by = Y - D * cam["ty"] * par - (Y - cam["cy"]) * D * cam["dz"] * par
        for fx in P.get("bg_fx", []):
            if fx["type"] == "haze":         # 陽炎: 遠い所ほど揺らぐ
                far = np.clip((fx.get("far", 0.45) - D) / fx.get("far", 0.45), 0, 1)
                by = by + fx["amp"] * far * np.sin(Y / fx.get("wave", 9.0) + t * fx.get("hz", 7.0)).astype(np.float32)
                bx = bx + fx["amp"] * 0.5 * far * np.sin(Y / (fx.get("wave", 9.0) * 1.7) - t * fx.get("hz", 7.0) * 0.8).astype(np.float32)
        for an in P.get("anim", []):
            if an["type"] == "flame" and an.get("on_bg"):      # 背景の側にある炎（切り抜きに入らなかった分）も揺らす
                m = cv2.remap(pl.masks[id(an)], X, Y, cv2.INTER_LINEAR)
                fdx, fdy = flame_disp(X, Y, m, an, math.floor(t * CHAR_FPS) / CHAR_FPS)
                bx, by = bx - fdx, by - fdy
        img = cv2.remap(pl.bg, bx, by, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT).astype(np.float32) / 255
        for fx in P.get("bg_fx", []):
            if fx["type"] == "glow":         # 沼や魔法陣の光の明滅
                m = cv2.remap(pl.masks[id(fx)], bx, by, cv2.INTER_LINEAR)[..., None]
                ph = cv2.remap(pl.depth, bx * 0.5, by * 0.5, cv2.INTER_LINEAR)[..., None]   # 場所ごとに少し位相をずらす
                k = fx["amp"] * (0.5 + 0.5 * np.sin(t * 2 * math.pi * fx["hz"] + ph * 6))
                img = img * (1 + m * k) + m * k * np.array(fx.get("tint", [0.1, 0.35, 0.1]), np.float32)
            if fx["type"] == "mist":         # 霧: 流れるノイズ
                key = id(fx)
                if key not in self.mist:
                    self.mist[key] = fbm(H * 2, W * 2, 7 + len(self.mist))
                nz = self.mist[key]
                ox = int((t * fx.get("speed", 30)) % W)
                n = nz[H // 2:H // 2 + H, ox:ox + W]
                y0, y1 = fx["band"]
                band = np.clip(1 - np.abs((self.v + H / 2 - (y0 + y1) / 2) / ((y1 - y0) / 2)), 0, 1)
                a = (np.clip(n - 0.35, 0, 1) * 1.6 * band * fx["alpha"])[..., None]
                img = img * (1 - a) + np.array(fx.get("color", [235, 240, 245]), np.float32) / 255 * a
            if fx["type"] == "sun":          # 太陽のまわりの光のゆらぎ
                sx, sy = fx["at"]
                s = pl.w / (W * cam["zoom"])
                px, py = (sx - cam["cx"]) / s + W / 2, (sy - cam["cy"]) / s + H / 2
                r2 = ((self.u + W / 2 - px) ** 2 + (self.v + H / 2 - py) ** 2) / (fx["r"] ** 2)
                g = np.exp(-r2) * fx["amp"] * (0.85 + 0.15 * math.sin(t * 3.1))
                img = img + g[..., None] * np.array([1.0, 0.95, 0.8], np.float32)
        # --- 人物（セル）
        tq = math.floor(t * CHAR_FPS) / CHAR_FPS          # 2コマ打ち
        who = P.get("speaker")
        lev = env_at(self.env, who, tq, smooth=1) if who else 0.0
        mouth = "closed"
        if who and lev > P.get("mouth_th", [0.12, 0.42])[0]:
            mouth = "open" if lev > P.get("mouth_th", [0.12, 0.42])[1] else "half"
        closed = any(b - 0.001 <= tq < b + P["blink"].get("len", 0.12) for b in P["blink"]["at"]) if P.get("blink") else False
        cel = pl.cel_for(mouth, closed)
        sw = self.swap.get(pname)
        smear = 0.0
        if sw:
            swd = sw[0]
            if tq >= swd["at"]:
                base = sw[1]
                if len(sw) > 3 and who:
                    base = sw[3].get(mouth, base)
                cel = base
                k = (tq - swd["at"]) / max(swd.get("settle", 0.35), 1e-3)
                smear = 0.0
            elif tq >= swd["at"] - 1.0 / CHAR_FPS:
                smear = 1.0
        kick = 0.0                    # 溜め（切り替えの直前に少し沈む）と揺れ戻し（切り替えの後）
        if sw:
            at = sw[0]["at"]
            if at - 2.0 / CHAR_FPS <= tq < at:
                kick = sw[0].get("anticip", 6.0)
            elif tq >= at:
                u = tq - at
                kick = -sw[0].get("overshoot", 10.0) * math.exp(-u / 0.14) * math.cos(2 * math.pi * u / 0.32)
        cmaps = self.char_maps(pl, P, cam, X, Y, tq, lev, kick)
        if smear and sw:
            # ぶれのコマ: A と B を重ね（不透明のまま）、動きの方向にぼかす
            a1 = cv2.remap(cel, *cmaps, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)).astype(np.float32) / 255
            a2 = cv2.remap(sw[1], *cmaps, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)).astype(np.float32) / 255
            A1, A2 = a1[..., 3:4], a2[..., 3:4]
            pre = a1[..., :3] * A1 * 0.45 + a2[..., :3] * A2 * 0.55
            al = A1 * 0.45 + A2 * 0.55
            dx, dy = sw[0].get("smear", [0, -1])
            L = 31
            ker = np.zeros((L, L), np.float32)
            cv2.line(ker, (L // 2 - int(dx * L / 2), L // 2 - int(dy * L / 2)), (L // 2 + int(dx * L / 2), L // 2 + int(dy * L / 2)), 1.0, 1)
            ker /= ker.sum()
            pre_b = cv2.filter2D(pre, -1, ker)
            al_b = cv2.filter2D(al[..., 0], -1, ker)[..., None]
            cov = np.clip(cv2.filter2D(np.maximum(A1, A2)[..., 0], -1, ker) * 1.25, 0, 1)[..., None]
            layer = np.concatenate([pre_b / np.maximum(al_b, 1e-4), cov], axis=2)
        else:
            layer = cv2.remap(cel, *cmaps, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)).astype(np.float32) / 255
        a = layer[..., 3:4]
        img = img * (1 - a) + layer[..., :3] * a
        # 人物の効果（炎の明るさ・目の光）
        for an in P.get("anim", []):
            if an["type"] == "glint":
                m = cv2.remap(pl.masks[id(an)], *cmaps, cv2.INTER_LINEAR)[..., None]
                k = an["amp"] * (0.5 + 0.5 * math.sin(tq * 2 * math.pi * an["hz"]))
                img = img + m * k * np.array(an.get("tint", [1.0, 0.6, 0.2]), np.float32)
        return img.clip(0, 1)

    def char_maps(self, pl, P, cam, X, Y, t, lev, kick=0.0):
        cp = P.get("char_parallax", P.get("char_depth", 0.85))
        sx = X - cp * cam["tx"] - (X - cam["cx"]) * cp * cam["dz"]
        sy = Y - cp * cam["ty"] - (Y - cam["cy"]) * cp * cam["dz"] - kick
        dx = np.zeros_like(X)
        dy = np.zeros_like(Y)
        for an in P.get("anim", []):
            ty = an["type"]
            if ty == "breathe":        # 呼吸: 胸から上が少し上がる
                w = smooth_box(sx, sy, an["box"], an.get("feather", 80))
                s = 0.5 + 0.5 * math.sin(2 * math.pi * an["hz"] * t)
                dy += w * an["amp"] * (an["pivot_y"] - sy).clip(0, None) * s
            elif ty == "sway":         # 体の揺れ（足元を中心に回る）
                ddx, ddy = rot_disp(sx, sy, an["pivot"], an["deg"] * math.sin(2 * math.pi * an["hz"] * t + an.get("phase", 0)))
                w = smooth_box(sx, sy, an["box"], an.get("feather", 80)) if "box" in an else 1.0
                dx += w * ddx
                dy += w * ddy
            elif ty == "head":         # 頭の小さな動き（話すとうなずく）
                d = an["deg"] * (lev * math.sin(2 * math.pi * 1.7 * t) * 0.6 + lev * 0.5) + an.get("idle", 0.3) * math.sin(2 * math.pi * 0.21 * t)
                ddx, ddy = rot_disp(sx, sy, an["pivot"], d)
                ny = an["nod"] * lev
                w = smooth_box(sx, sy, an["box"], an.get("feather", 60))
                dx += w * ddx
                dy += w * (ddy - ny)
            elif ty == "hair":         # 髪の揺れ（根元から先へ大きく）
                m = cv2.remap(pl.masks[id(an)], sx, sy, cv2.INTER_LINEAR)
                y0, y1 = an["root_y"], an["tip_y"]
                f = np.clip((sy - y0) / (y1 - y0), 0, 1)
                dx += m * f * an["amp"] * np.sin(2 * math.pi * an["hz"] * t - sy / 90).astype(np.float32)
            elif ty == "flame":        # 炎のゆらぎ（上へ流れる）
                m = cv2.remap(pl.masks[id(an)], sx, sy, cv2.INTER_LINEAR)
                fdx, fdy = flame_disp(sx, sy, m, an, t)
                dx += fdx
                dy += fdy
            elif ty == "jaw":          # 横顔などの口パク: 口の線より下（あご）を声に合わせて下げる
                w = smooth_box(sx, sy, an["box"], an.get("feather", 30))
                ramp = np.clip((sy - an["line_y"]) / an.get("ramp", 14), 0, 1)
                op = an["amp"] * (lev if lev > 0.1 else 0.0) - an.get("close", 0.0) * (1 if lev <= 0.1 else 0)
                dy += w * ramp * op
                dx += w * ramp * op * an.get("dx", 0.0)
            elif ty == "bob":          # 走りの上下（1歩ごとに跳ねる）
                w = smooth_box(sx, sy, an["box"], an.get("feather", 40))
                ph = 2 * math.pi * an["hz"] * t + an.get("phase", 0)
                dy += w * (-an["amp"] * abs(math.sin(ph / 2)) + an["amp"] * 0.5)
                dx += w * an.get("sway", 0) * math.sin(ph / 2)
        return (sx - dx).astype(np.float32), (sy - dy).astype(np.float32)

    def frame(self, t):
        show = self.spec["show"]
        cur = [s for s in show if s["t"] <= t + 1e-6][-1]
        idx = show.index(cur)
        img = self.render_plate(cur["plate"], t, 0)
        if idx > 0:
            prev = show[idx - 1]
            fade = cur.get("fade", 0.0)
            if fade and t < cur["t"] + fade:
                k = ease((t - cur["t"]) / fade)
                img = self.render_plate(prev["plate"], t, 0) * (1 - k) + img * k
        flash = [f for f in self.spec.get("flash", []) if f["t"] <= t < f["t"] + f.get("len", 2 / FPS)]
        if flash:              # 透過光（白く飛ばして絵を受け渡す）。bump は山なりに強くなって消える
            f = flash[0]
            k = f.get("amp", 0.7) * (math.sin(math.pi * (t - f["t"]) / f["len"]) if f.get("shape") == "bump" else 1.0)
            col = np.array(f.get("color", [1.0, 0.97, 0.88]), np.float32)
            img = img * (1 - k) + col * k
        out = img
        for fx, mo in self.motes:
            if fx.get("from", 0) <= t < fx.get("to", 1e9):
                mo.draw(out, t)
        g = self.spec.get("grade")
        if g:
            out = out * np.array(g.get("gain", [1, 1, 1]), np.float32) + np.array(g.get("lift", [0, 0, 0]), np.float32)
            if g.get("vignette"):
                r2 = (self.u / (W / 2)) ** 2 + (self.v / (H / 2)) ** 2
                out = out * (1 - g["vignette"] * r2[..., None] * 0.5)
        return (out.clip(0, 1) * 255).astype(np.uint8)


def align(dst, src, exclude):
    """src の板を dst の板に重ねる変換（背景の特徴点、人物は除く）。"""
    def gray_bg(p):
        g = cv2.cvtColor(load_rgb(p.pl["src"]), cv2.COLOR_RGB2GRAY)
        m = (p.cel[..., 3] < 20).astype(np.uint8) * 255
        for x0, y0, x1, y1 in exclude:
            m[y0:y1, x0:x1] = 0
        return g, m
    g1, m1 = gray_bg(dst)
    g2, m2 = gray_bg(src)
    orb = cv2.ORB_create(6000)
    k1, d1 = orb.detectAndCompute(g1, m1)
    k2, d2 = orb.detectAndCompute(g2, m2)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    ms = sorted(bf.match(d2, d1), key=lambda m: m.distance)[:800]
    p2 = np.float32([k2[m.queryIdx].pt for m in ms])
    p1 = np.float32([k1[m.trainIdx].pt for m in ms])
    Hm, inl = cv2.findHomography(p2, p1, cv2.RANSAC, 4.0)
    print(f"重ね合わせ: 特徴点 {len(ms)}、合致 {int(inl.sum())}")
    return Hm


# ---------------------------------------------------------------- 音

def cut_audio(spec, out):
    sd = ROOT / spec["scene"]
    vy = yaml.safe_load(open(sd / "voice.yaml"))
    tc, sec = spec["tc"], spec["sec"]
    b = vy["bgm"]
    args = ["ffmpeg", "-loglevel", "error", "-y", "-stream_loop", "-1", "-ss", f"{b.get('from', 0) + tc:.3f}", "-i", str(ROOT / b["src"])]
    filt = [f"[0:a]atrim=0:{sec},volume={b.get('volume', 1.0)},aformat=sample_rates=48000:channel_layouts=stereo[b]"]
    lab = ["[b]"]
    n = 1
    for ln in vy["lines"]:
        at = ln["at"] - tc
        if not (-0.05 <= at < sec):
            continue
        files = sorted((ROOT / "audio" / vy["scene"] / "original").glob(f"{ln['id']}_*.wav"))
        if not files:
            continue
        args += ["-i", str(files[0])]
        ms = int(max(at, 0) * 1000)
        filt.append(f"[{n}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[v{n}]")
        lab.append(f"[v{n}]")
        n += 1
    filt.append(f"{''.join(lab)}amix=inputs={len(lab)}:normalize=0:duration=first,atrim=0:{sec}[a]")
    args += ["-filter_complex", ";".join(filt), "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(args, check=True)


# ---------------------------------------------------------------- コマンド

def load_spec(p):
    spec = yaml.safe_load(open(p))
    shot = yaml.safe_load(open(ROOT / spec["scene"] / "shots" / f"{spec['cut']}.yaml"))
    spec.setdefault("tc", float(shot["tc"]))
    spec.setdefault("sec", float(shot["sec"]))
    spec["scene_id"] = pathlib.Path(spec["scene"]).name
    spec["shot"] = shot
    return spec


def render(spec, ver):
    r = Renderer(spec)
    od = ROOT / "cel" / spec["scene_id"]
    od.mkdir(parents=True, exist_ok=True)
    silent = build_dir(spec) / "video.mp4"
    aud = build_dir(spec) / "audio.m4a"
    n = int(round(spec["sec"] * FPS))
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                           "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(silent)],
                          stdin=subprocess.PIPE)
    mb = spec.get("motion_blur", [])
    for i in range(n):
        t = i / FPS
        sub = next((m["samples"] for m in mb if m["from"] <= t < m["to"]), 1)
        if sub > 1:     # 速いカメラの動きはコマの中を何枚か描いて平均する（ぶれ）
            acc = np.zeros((H, W, 3), np.float32)
            for k in range(sub):
                acc += r.frame(t + k / (FPS * sub)).astype(np.float32)
            f = (acc / sub).astype(np.uint8)
        else:
            f = r.frame(t)
        ff.stdin.write(f.tobytes())
        if i % 24 == 0:
            print(f"  {t:.1f}s / {spec['sec']}s", flush=True)
    ff.stdin.close()
    ff.wait()
    cut_audio(spec, aud)
    out = od / f"{spec['cut']}_{ver}.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(silent), "-i", str(aud), "-c:v", "copy", "-c:a", "copy", "-shortest", str(out)], check=True)
    print(f"書き出し: {out.relative_to(ROOT)}")
    return out


def compare(spec, ver):
    od = ROOT / "cel" / spec["scene_id"]
    ours = od / f"{spec['cut']}_{ver}.mp4"
    sd = ROOT / spec["shot"]["video"]
    font = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
    sec = spec["sec"]
    lab = "drawtext=fontfile={f}:text='{t}':x=16:y=12:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8"
    fc = (f"[0:v]scale=960:540,setsar=1,fps=24,tpad=stop_mode=clone:stop_duration={sec},trim=0:{sec},setpts=PTS-STARTPTS,"
          + lab.format(f=font, t="Seedance 2.0（今まで、480p）") + "[a];"
          + f"[1:v]scale=960:540,setsar=1,trim=0:{sec},setpts=PTS-STARTPTS," + lab.format(f=font, t="コードで動かす（試作、0クレジット）") + "[b];"
          + "[a][b]vstack[v]")
    out = od / f"{spec['cut']}_compare.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(sd), "-i", str(ours), "-filter_complex", fc, "-map", "[v]", "-map", "1:a",
                    "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(out)], check=True)
    print(f"比較: {out.relative_to(ROOT)}")


def check(spec, ver):
    od = ROOT / "cel" / spec["scene_id"]
    ours = od / f"{spec['cut']}_{ver}.mp4"
    out = od / f"{spec['cut']}_check.jpg"
    n = int(spec["sec"] * 2)
    cols = 4
    rows = math.ceil(n / cols)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(ours), "-vf",
                    f"fps=2,scale=480:-1,drawtext=fontfile=/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc:text='%{{pts\\:hms}}':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.5,tile={cols}x{rows}",
                    "-frames:v", "1", "-q:v", "3", str(out)], check=True)
    print(f"確認画像: {out.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prep", "render", "compare", "check", "frame"])
    ap.add_argument("spec")
    ap.add_argument("--ver", default="v01")
    ap.add_argument("--t", type=float, nargs="*", default=[0.0])
    a = ap.parse_args()
    spec = load_spec(a.spec)
    if a.cmd == "prep":
        prep(spec)
    elif a.cmd == "render":
        render(spec, a.ver)
    elif a.cmd == "compare":
        compare(spec, a.ver)
    elif a.cmd == "check":
        check(spec, a.ver)
    elif a.cmd == "frame":          # 指定した秒のコマを書き出す（確かめ用）
        r = Renderer(spec)
        for t in a.t:
            p = build_dir(spec) / f"frame_{t:05.2f}.jpg"
            cv2.imwrite(str(p), cv2.cvtColor(r.frame(t), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
            print(p)


if __name__ == "__main__":
    main()
