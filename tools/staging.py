#!/usr/bin/env python3
"""立ち位置・向き・移動とカメラ割り（blocking.yaml）から、各カットの始めと終わりに何がどう映るかを計算する（演出ノート P-04）。

A01 の tools/blocking.py（居間専用）を、屋外にも使えるように一般化したもの。人物ごとに「位置」と「体の向き」を持ち、
カットの中の移動（move）とカットの頭での置き直し（set）を扱う。遠景は方位（bearing）ごとに書く。

  python tools/staging.py episodes/A/scenes/A01b

出力（<シーン>/gen/）:
  staging_report.md     カット表（カメラ、始めと終わりの画面の左右・向き・目線、背景）と、確かめた結果（軸、飛び、重なり）
  staging_report.yaml   同じ内容と、生成の指示文に使う英語の文（start / end）
  staging_map_<区間>.png 区間ごとの上から見た図（人物の位置と向き、移動の矢印、カメラ）
  staging_cuts.png      カットごとの小さな図の一覧

座標: x は西 0 → 東、y は北 0 → 南。方位は北 0・東 90・南 180・西 270（時計回り）。
blocking.yaml の書き方はファイルの頭のコメントを見る。
"""
import argparse
import math
import pathlib

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = next((f for f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                          "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc") if pathlib.Path(f).exists()), None)
COLORS = {"metallica": (255, 150, 40), "bisco": (90, 150, 255), "hundred-knight": (160, 220, 255), "arlecchino": (220, 120, 220)}
SHORT = {"metallica": "メ", "bisco": "ビ", "hundred-knight": "百", "arlecchino": "ア"}


# ---------- 幾何 ----------

def norm(v):
    L = math.hypot(*v) or 1.0
    return (v[0] / L, v[1] / L)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def bearing_of(v):
    """ベクトル（x 東、y 南）の方位（北 0、時計回り）。"""
    return math.degrees(math.atan2(v[0], -v[1])) % 360


def vec_of(b):
    r = math.radians(b)
    return (math.sin(r), -math.cos(r))


def fov_half(lens):
    """横の画角の半分（35mm 判、横 36mm）。"""
    return math.atan(18.0 / float(lens))


def ang_diff(a, b):
    return (a - b + 180) % 360 - 180


def tilt_of(c, cam, look):
    """カメラの上下の傾き（ラジアン、上が正）。tilt: up / down、または look_h（見る点の高さ）から決める。"""
    ch = c["cam"][2]
    if c.get("tilt") == "down":   # 見る点の地面から 0.8m を画面の中心にする
        return math.atan2(0.8 - ch, max(0.5, math.dist(cam, look)))
    if c.get("tilt") == "up":
        return 0.28
    if c.get("look_h") is not None:
        return math.atan2(c["look_h"] - ch, math.dist(cam, look))
    return 0.0


# ---------- 場面 ----------

class Stage:
    def __init__(self, scene_dir):
        self.dir = ROOT / scene_dir
        self.bk = yaml.safe_load((self.dir / "blocking.yaml").read_text(encoding="utf-8"))
        self.lay = yaml.safe_load((ROOT / self.bk["layout"]).read_text(encoding="utf-8"))
        self.W, self.D = self.lay["size"]["w"], self.lay["size"]["d"]
        self.order = list(self.bk["cuts"])
        self.states = self._run()

    def place(self, v):
        if v is None:
            return None
        if isinstance(v, str):
            if v in self.bk["places"]:
                return tuple(self.bk["places"][v])
            p = self.lay.get("props", {}).get(v)
            if p and "at" in p:
                return tuple(p["at"])
            raise KeyError(f"場所が見つからない: {v}")
        return tuple(v)

    def face_of(self, f, who, pos):
        """向きの指定（数値の方位、人物名、場所名、[x, y]）を方位にする。"""
        if isinstance(f, (int, float)):
            return float(f) % 360
        t = pos.get(f) if isinstance(f, str) and f in pos else self.place(f)
        p = pos[who]
        return bearing_of((t[0] - p[0], t[1] - p[1]))

    def _run(self):
        """初めの状態から、カットごとに set（頭で置き直す）と move（カットの中で動く）を当て、始めと終わりの状態を作る。

        向きを "@人物名" で書くと、その人物を目で追い続ける（相手が動けば向きも一緒に変わる）。数値や場所で向きを書くと追うのをやめる。
        """
        cur_pos, cur_face, track = {}, {}, {}

        def set_face(who, f):
            if isinstance(f, str) and f.startswith("@"):
                track[who] = f[1:]
                return self.face_of(f[1:], who, cur_pos)
            track.pop(who, None)
            return self.face_of(f, who, cur_pos)

        def retrack():
            for who, t in track.items():
                cur_face[who] = self.face_of(t, who, cur_pos)

        for who, s in self.bk["start"].items():
            cur_pos[who] = self.place(s["at"])
        for who, s in self.bk["start"].items():
            cur_face[who] = set_face(who, s["face"])
        out = {}
        for cut in self.order:
            c = self.bk["cuts"][cut] or {}
            jumps = []
            before = dict(cur_face)
            for who, s in (c.get("set") or {}).items():
                if "at" in s:
                    np_ = self.place(s["at"])
                    if cur_pos.get(who) is None or math.dist(np_, cur_pos[who]) > 0.05:
                        jumps.append((who, "位置"))
                    cur_pos[who] = np_
            for who, s in (c.get("set") or {}).items():
                if "face" in s:
                    cur_face[who] = set_face(who, s["face"])
            retrack()
            for who in (c.get("set") or {}):
                if who in before and abs(ang_diff(cur_face[who], before[who])) > 20 and (who, "位置") not in jumps:
                    jumps.append((who, "向き"))
            start = ({k: v for k, v in cur_pos.items()}, dict(cur_face))
            for who, s in (c.get("move") or {}).items():
                if "to" in s:
                    cur_pos[who] = self.place(s["to"])
            for who, s in (c.get("move") or {}).items():
                if "face" in s:
                    cur_face[who] = set_face(who, s["face"])
            retrack()
            end = ({k: v for k, v in cur_pos.items()}, dict(cur_face))
            out[cut] = {"start": start, "end": end, "jumps": jumps}
        return out

    # ---------- 1枚の画の計算 ----------

    def horizon_at(self, b, cam=None, look=None):
        """方位 b の遠景。layout に near があり、見る点がその近くでカメラが決まった向きなら、近くの背景（玄関など）を返す。"""
        if cam is not None and look is not None:
            for n in self.lay.get("near", []):
                lo, hi = n["from"], n["to"]
                if math.dist(look, n["at"]) <= n["r"] and math.dist(cam, n["at"]) <= n.get("cam_max", 1e9) and ((lo <= b < hi) if lo <= hi else (b >= lo or b < hi)):
                    return n
        for h in self.lay["horizon"]:
            lo, hi = h["from"], h["to"]
            if (lo <= b < hi) if lo <= hi else (b >= lo or b < hi):
                return h
        return None

    def frame(self, cut, which):
        c = self.bk["cuts"][cut]
        pos, face = self.states[cut][which]
        cam = tuple(c["cam"][:2])
        look = c.get("look_end", c["look"]) if which == "end" else c["look"]
        look = pos[look] if isinstance(look, str) and look in pos else self.place(look)
        d = norm((look[0] - cam[0], look[1] - cam[1]))
        right = (-d[1], d[0])
        lens = c.get("lens_end", c["lens"]) if which == "end" else c["lens"]
        half = fov_half(lens)
        th = math.tan(half)
        only = c.get("only")
        people, hidden = [], []
        tilt = tilt_of(c, cam, look)
        vhalf = math.atan(th * 9 / 16)
        for who, p in pos.items():
            if p is not None and only is not None and who not in only:
                # 映さないことにした人物が、実際には画面に入っていないか（高さも見る）
                v = (p[0] - cam[0], p[1] - cam[1])
                z = dot(v, d)
                if z > 0.2 and abs(dot(v, right) / z / th) < 0.95:
                    top = math.atan2(self.lay["people"][who]["h"] - c["cam"][2], z) - tilt
                    bot = math.atan2(-c["cam"][2], z) - tilt
                    if top > -vhalf * 0.95 and bot < vhalf * 0.95:
                        hidden.append(who)
            if p is None or (only is not None and who not in only):
                continue
            v = (p[0] - cam[0], p[1] - cam[1])
            z = dot(v, d)
            if z <= 0.2:
                continue
            s = dot(v, right) / z / th
            if abs(s) > 1.1:
                continue
            e = {"who": who, "s": round(s, 2), "dist": round(z, 2)}
            e["side"] = "左" if s < -0.33 else ("右" if s > 0.33 else "中央")
            # 体の向き: 人物からカメラへの向きと、体の向きの差
            fb = face[who]
            to_cam = bearing_of((cam[0] - p[0], cam[1] - p[1]))
            diff = abs(ang_diff(fb, to_cam))
            fv = vec_of(fb)
            sx = dot(fv, right)
            lr = "画面右" if sx > 0 else "画面左"
            if diff < 30:
                e["body"] = "正面"
            elif diff < 70:
                e["body"] = f"斜め前（{lr}向き）"
            elif diff < 115:
                e["body"] = f"横顔（{lr}向き）"
            elif diff < 150:
                e["body"] = f"斜め後ろ（{lr}向き）"
            else:
                e["body"] = "背中"
            g = (c.get("gaze_end") or c.get("gaze") or {}) if which == "end" else (c.get("gaze") or {})
            g = g.get(who)
            if g is not None:
                if isinstance(g, str) and g in self.lay["people"]:
                    e["gaze_to"] = g
                gb = self.face_of(g, who, pos)
                gv = vec_of(gb)
                gx, gz = dot(gv, right), dot(gv, d)
                if abs(gx) < 0.25:
                    e["gaze"] = "カメラの方" if gz < 0 else "奥"
                else:
                    e["gaze"] = ("画面右" if gx > 0 else "画面左") + ("・手前寄り" if gz < -0.35 else ("・奥寄り" if gz > 0.35 else ""))
            people.append(e)
        people.sort(key=lambda e: e["s"])
        cb = bearing_of(d)
        bg = {}
        for k, a in (("left", -math.degrees(half) * 0.9), ("center", 0.0), ("right", math.degrees(half) * 0.9)):
            h = self.horizon_at((cb + a) % 360, cam, look)
            bg[k] = h
        if c.get("bg"):   # カットで背景を決めたとき（遠くのピラー、俯瞰など）。layout の views の名前
            bg = {"left": None, "center": self.lay["views"][c["bg"]], "right": None}
        props = []
        for name, pr in self.lay.get("props", {}).items():
            if "at" not in pr:
                continue
            v = (pr["at"][0] - cam[0], pr["at"][1] - cam[1])
            z = dot(v, d)
            if z > 0.3 and abs(dot(v, right) / z / th) <= 1.0:
                props.append(pr["ja"])
        return {"people": people, "bg": bg, "props": props, "bearing": round(cb), "cam": list(c["cam"]), "lens": lens, "hidden": hidden}

    # ---------- 言葉 ----------

    def area_at(self, p):
        """点 p がどの地面の区分にあるか（後に書いたものが上）。"""
        hit = None
        for a in self.lay.get("areas", []):
            if a["kind"] == "rect":
                ok = a["x"][0] <= p[0] <= a["x"][1] and a["y"][0] <= p[1] <= a["y"][1]
            elif a["kind"] == "circle":
                ok = math.dist(p, a["center"]) <= a["r"]
            else:
                q, ok = a["points"], False
                for i in range(len(q)):
                    (x1, y1), (x2, y2) = q[i], q[i - 1]
                    if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
                        ok = not ok
            if ok:
                hit = a
        return hit

    def where(self, c):
        x, y, h = c["cam"]
        hw = "地面すれすれ" if h < 0.5 else ("高い位置" if h > 3.0 else ("低め" if h < 1.1 else ""))
        if self.lay.get("areas"):
            a = self.area_at((x, y))
            return (a["ja"] + ("の上" if a["ja"].endswith("沼") else "") if a else "場の外") + (f"・{hw}" if hw else "")
        if y < 0.4:
            s = "崖の外（沼の上）"
        elif y < 2.2:
            s = "崖の縁"
        elif y > 9.0:
            s = "高台の南（森側）"
        else:
            s = "高台の" + ("西" if x < 5.5 else ("東" if x > 10.5 else "中ほど"))
        return s + (f"・{hw}" if hw else "")

    def staging_en(self, cut, fr, which):
        c = self.bk["cuts"][cut]
        en = {k: v["en"] for k, v in self.lay["people"].items()}
        body_en = {"正面": "facing the camera", "背中": "with the back to the camera"}

        def where(s):
            return ("at the far left" if s < -0.6 else "on the left" if s < -0.2 else "in the center" if s <= 0.2
                    else "on the right" if s <= 0.6 else "at the far right") + " of the frame"
        parts = []
        poses = (c.get("pose_end") or c.get("pose") or {}) if which == "end" else (c.get("pose") or {})
        for e in fr["people"]:
            b = e["body"]
            if b in body_en:
                bt = body_en[b]
            else:
                side = "right" if "画面右" in b else "left"
                bt = {"斜め": "in three-quarter view", "横顔": "in profile", "斜め後": "seen from behind at an angle"}[
                    "斜め後" if b.startswith("斜め後ろ") else ("横顔" if b.startswith("横顔") else "斜め")] + f", turned toward screen {side}"
            s = f"{en[e['who']]}, {poses.get(e['who'], 'standing')}, {where(e['s'])}, {bt}"
            if e["dist"] > 6.0 and c.get("frame") not in ("大引き", "引き"):
                s += ", small in the background"
            g = e.get("gaze")
            if e.get("gaze_to"):   # 相手の名前で書く（画面の外でも）
                s += ", looking at " + en[e["gaze_to"]].split(" (")[0] + ("" if any(x["who"] == e["gaze_to"] for x in fr["people"]) else " (off screen, toward screen " + ("right" if g.startswith("画面右") else "left" if g.startswith("画面左") else "center") + ")")
            elif g:
                s += ", looking " + ("toward the camera" if g == "カメラの方" else "into the distance away from the camera" if g == "奥"
                                     else "toward screen " + ("right" if g.startswith("画面右") else "left"))
            parts.append(s)
        seen = []
        for k in ("left", "center", "right"):
            h = fr["bg"][k]
            if h and h["en"] not in seen:
                seen.append(h["en"])
        txt = ""
        n = len(parts)
        if n:
            txt += ("In frame (exactly one person): " if n == 1 else f"In frame (exactly {n} people, from left to right): ") + "; ".join(parts) + ". No other people. "
        else:
            txt += "No people in frame. "
        h = c["cam"][2]
        if c.get("tilt") == "up" or (h < 0.6 and c.get("frame") in ("全身", "膝上", "ローアングル")):
            txt += "Low angle looking up; the morning sky fills the upper part of the frame. "
        if c.get("tilt") == "down" or h > 3.0:
            txt += "High angle looking down. "
        txt += "Behind: " + ", then ".join(seen) + "."
        if self.lay.get("light_en"):   # 時刻と光（背景画像の時刻と違うときも、こちらに合わせる）
            txt += " " + self.lay["light_en"]
        return txt

    # ---------- 確かめる ----------

    def checks(self):
        res = {"axis": [], "jumps": [], "overlap": [], "offscreen": [], "hidden": []}
        for ax in self.bk.get("axes", []):
            i0, i1 = self.order.index(ax["cuts"][0]), self.order.index(ax["cuts"][1])
            for cut in self.order[i0:i1 + 1]:
                c = self.bk["cuts"][cut]
                if c.get("axis_free") or c.get("insert"):
                    continue
                pos = self.states[cut]["start"][0]
                a, b = pos.get(ax["a"]), pos.get(ax["b"])
                if a is None or b is None or math.dist(a, b) < 0.3:
                    continue
                lv = norm((b[0] - a[0], b[1] - a[1]))

                def side(p):
                    return lv[0] * (p[1] - a[1]) - lv[1] * (p[0] - a[0])
                sc, ss = side(c["cam"][:2]), side(ax["side"])
                if not (sc * ss > 0 or abs(sc) < 0.35):
                    res["axis"].append({"axis": ax["name"], "cut": cut, "offset": round(sc, 2)})
        prev_vis = set()
        for cut in self.order:
            st = self.states[cut]
            if self.bk["cuts"][cut].get("insert"):
                continue
            fr_s = self.frame(cut, "start")
            vis_s = {e["who"] for e in fr_s["people"]}
            for which, fr in (("start", fr_s), ("end", self.frame(cut, "end"))):
                for who in fr["hidden"]:
                    if who not in (self.bk["cuts"][cut].get("hidden_ok") or []):
                        res["hidden"].append({"cut": cut, "which": which, "who": who})
            for who, kind in st["jumps"]:
                if who in prev_vis or who in vis_s:
                    res["jumps"].append({"cut": cut, "who": who, "kind": kind,
                                         "note": "前のカットの終わりか、このカットの始めに映っている"})
                else:
                    res["offscreen"].append({"cut": cut, "who": who, "kind": kind})
            for which in ("start", "end"):
                pos = st[which][0]
                ws = [w for w in pos if pos[w] is not None]
                for i in range(len(ws)):
                    for j in range(i + 1, len(ws)):
                        if math.dist(pos[ws[i]], pos[ws[j]]) < 0.45:
                            res["overlap"].append({"cut": cut, "which": which, "who": [ws[i], ws[j]],
                                                   "d": round(math.dist(pos[ws[i]], pos[ws[j]]), 2)})
            prev_vis = {e["who"] for e in self.frame(cut, "end")["people"]}
        return res


# ---------- 図 ----------

def draw_site(st, d, P, k, font):
    W, D = st.W, st.D
    d.rectangle([*P(0, 0.4), *P(W, D)], fill=(52, 58, 40), outline=(120, 110, 80), width=2)
    d.rectangle([*P(0, -1.6), *P(W, 0.4)], fill=(40, 150, 70))
    d.text(P(W / 2, -0.8), "翠の沼（崖の下）", font=font, fill=(10, 40, 20), anchor="mm")
    for name, pr in st.lay.get("props", {}).items():
        if "at" not in pr:
            continue
        x, y = P(*pr["at"])
        col = {"dead_tree": (120, 90, 60), "stump": (140, 110, 70), "mushrooms": (220, 60, 50), "path_head": (160, 130, 90)}.get(name, (200, 200, 200))
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=col)
    # 遠景の方位（中心から）
    cx, cy = W / 2, D / 2
    for h in st.lay["horizon"]:
        b = (h["from"] + ((h["to"] - h["from"]) % 360) / 2) % 360
        v = vec_of(b)
        tx, ty = P(cx + v[0] * (W / 2 + 0.9), cy + v[1] * (D / 2 + 1.6))
        d.text((tx, ty), h["ja"][:10], font=font, fill=(170, 170, 170), anchor="mm")


def draw_person(d, P, who, p, fb, font, ghost=False):
    x, y = P(*p)
    col = COLORS.get(who, (255, 255, 255))
    if ghost:
        col = tuple(int(c * 0.45) for c in col)
    v = vec_of(fb)
    d.line([x, y, x + v[0] * 34, y + v[1] * 34], fill=col, width=5)
    d.ellipse([x - 15, y - 15, x + 15, y + 15], fill=col, outline="black", width=3)
    d.text((x, y), SHORT.get(who, "?"), font=font, fill="black", anchor="mm")


def draw_camera(d, P, st, cut, k, font, col, label, which="start"):
    c = st.bk["cuts"][cut]
    pos = st.states[cut][which][0]
    cam = tuple(c["cam"][:2])
    look = c["look"]
    look = pos[look] if isinstance(look, str) and look in pos else st.place(look)
    b = bearing_of((look[0] - cam[0], look[1] - cam[1]))
    half = math.degrees(fov_half(c["lens"]))
    L = 2.6
    a = P(*cam)
    for s in (-half, half):
        v = vec_of(b + s)
        d.line([a, P(cam[0] + v[0] * L, cam[1] + v[1] * L)], fill=col, width=3)
    d.ellipse([a[0] - 8, a[1] - 8, a[0] + 8, a[1] + 8], fill=col)
    d.text((a[0], a[1] - 20), label, font=font, fill=col, anchor="mm")


def draw_areas(st, d, P, font):
    """layout の areas（沼・岸・デッキなど）を上から見た図に描く。"""
    for a in st.lay.get("areas", []):
        col = tuple(a.get("color", [80, 80, 80]))
        if a["kind"] == "rect":
            d.rectangle([*P(a["x"][0], a["y"][0]), *P(a["x"][1], a["y"][1])], fill=col)
        elif a["kind"] == "poly":
            d.polygon([P(*q) for q in a["points"]], fill=col)
        elif a["kind"] == "circle":
            cx, cy = P(*a["center"])
            r = P(a["center"][0] + a["r"], 0)[0] - cx
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    for a in st.lay.get("areas", []):
        c = a.get("center") or (a["x"][0] + (a["x"][1] - a["x"][0]) / 2, a["y"][0] + (a["y"][1] - a["y"][0]) / 2) if a["kind"] != "poly" else \
            (sum(q[0] for q in a["points"]) / len(a["points"]), sum(q[1] for q in a["points"]) / len(a["points"]))
        if a["kind"] == "rect" and a["x"][1] - a["x"][0] > 15:
            continue
        if a["kind"] == "circle" and any(b is not a and b["kind"] == "circle" and b["center"] == a["center"] and b["r"] < a["r"]
                                         for b in st.lay["areas"]):
            c = (a["center"][0], a["center"][1] - a["r"] + 0.45)   # 輪（デッキ）の名前は上の縁に
        d.text(P(*c), a["ja"], font=font, fill=(20, 20, 20), anchor="mm")


def arrow(d, a, b, col, width=4):
    d.line([a, b], fill=col, width=width)
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    for s_ in (2.6, -2.6):
        d.line([b, (b[0] - 16 * math.cos(ang + s_ / 6), b[1] - 16 * math.sin(ang + s_ / 6))], fill=col, width=width)


def render(st, out_dir, sections):
    fbig = ImageFont.truetype(FONT, 30)
    f = ImageFont.truetype(FONT, 17)
    fs = ImageFont.truetype(FONT, 15)
    k = 95
    ymax = st.bk.get("map_ymax", st.D)
    palette = [(255, 208, 96), (127, 223, 255), (255, 159, 208), (160, 255, 144), (255, 255, 255), (192, 160, 255), (255, 120, 120), (120, 255, 220)]
    files = []
    for n, sec in enumerate(sections, start=1):
        a, b = sec["cuts"]
        cuts = st.order[st.order.index(a):st.order.index(b) + 1]
        W, H = int(st.W * k) + 260, int((ymax + 2.2) * k) + 190
        im = Image.new("RGB", (W, H), (27, 29, 34))
        d = ImageDraw.Draw(im)
        d.text((W // 2, 26), f"{st.bk['scene']} {sec['name']}（{a[-3:]}〜{b[-3:]}、上から見た図）", font=fbig, fill="white", anchor="mm")
        d.text((W // 2, 62), "丸＝人物（線は体の向き）、薄い丸＝区間の初めの位置、矢印＝カットの中の移動、扇＝カメラ（番号はカット）", font=fs, fill=(190, 190, 190), anchor="mm")
        ox, oy = 130, 90 + int(1.9 * k)
        P = lambda x, y: (ox + x * k, oy + y * k)
        if st.lay.get("areas"):
            draw_areas(st, d, P, f)
            d.text(P(st.W / 2, -0.5), "↑北", font=f, fill=(200, 200, 200), anchor="mm")
        else:
            d.rectangle([*P(0, 0.4), *P(st.W, ymax)], fill=(52, 58, 40), outline=(120, 110, 80), width=2)
            d.rectangle([*P(0, -1.6), *P(st.W, 0.4)], fill=(40, 150, 70))
            d.text(P(st.W / 2, -0.8), "翠の沼（崖の下）　↑北", font=f, fill=(10, 40, 20), anchor="mm")
        d.text(P(st.W / 2, ymax + 0.5), "↓南", font=f, fill=(170, 170, 170), anchor="mm")
        for name, pr in st.lay.get("props", {}).items():
            if "at" not in pr or pr["at"][1] > ymax:
                continue
            x, y = P(*pr["at"])
            d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(120, 90, 60))
            d.text((x, y + 20), pr["ja"][:8], font=fs, fill=(170, 150, 120), anchor="mm")
        s0 = st.states[cuts[0]]["start"]
        for who, p in s0[0].items():
            if p is not None:
                draw_person(d, P, who, p, s0[1][who], f, ghost=True)
        n_arrow = 0
        for cut in cuts:
            for who in (st.bk["cuts"][cut].get("move") or {}):
                p0, p1 = st.states[cut]["start"][0][who], st.states[cut]["end"][0][who]
                if math.dist(p0, p1) > 0.1:
                    # 行きと帰りが重ならないよう、進む向きの右へ少しずらす。via（経由点）があれば折れ線で描く
                    v = norm((p1[0] - p0[0], p1[1] - p0[1]))
                    off = (-v[1] * 0.12, v[0] * 0.12)
                    via = [st.place(q) for q in (st.bk["cuts"][cut]["move"][who].get("via") or [])]
                    pts = [p0] + via + [p1]
                    pts = [(q[0] + off[0], q[1] + off[1]) for q in pts]
                    for q0, q1 in zip(pts[:-2], pts[1:-1]):
                        d.line([P(*q0), P(*q1)], fill=COLORS[who], width=4)
                    a0, a1 = pts[-2], pts[-1]
                    arrow(d, P(*a0), P(*a1), COLORS[who])
                    mx, my = P((a0[0] + a1[0]) / 2 + off[0] * 2, (a0[1] + a1[1]) / 2 + off[1] * 2)
                    d.text((mx, my), cut[-3:], font=fs, fill=COLORS[who], anchor="mm")
                    n_arrow += 1
        s1 = st.states[cuts[-1]]["end"]
        for who, p in s1[0].items():
            if p is not None:
                draw_person(d, P, who, p, s1[1][who], f)
        for i, cut in enumerate(cuts):
            c = st.bk["cuts"][cut]
            if c.get("insert"):
                continue
            col = palette[i % len(palette)]
            cam = tuple(c["cam"][:2])
            pos = st.states[cut]["start"][0]
            look = c["look"]
            look = pos[look] if isinstance(look, str) and look in pos else st.place(look)
            bb = bearing_of((look[0] - cam[0], look[1] - cam[1]))
            half = math.degrees(fov_half(c["lens"]))
            A = P(*cam)
            for s_ in (-half, half):
                v = vec_of(bb + s_)
                d.line([A, P(cam[0] + v[0] * 1.3, cam[1] + v[1] * 1.3)], fill=col, width=2)
            d.ellipse([A[0] - 7, A[1] - 7, A[0] + 7, A[1] + 7], fill=col)
            d.text((A[0] + 10, A[1] - 12), cut[-3:], font=fs, fill=col, anchor="lm")
        p = out_dir / f"staging_map_{n}.png"
        im.save(p)
        files.append(p)
    # カットごとの小さな図
    cols = 8
    kk = 22
    cw, ch = int(st.W * kk) + 20, int((st.D + 2.4) * kk) + 30
    rows = (len(st.order) + cols - 1) // cols
    im = Image.new("RGB", (cols * cw, rows * ch), (20, 22, 26))
    d = ImageDraw.Draw(im)
    for i, cut in enumerate(st.order):
        ox, oy = (i % cols) * cw + 10, (i // cols) * ch + 26 + int(1.6 * kk)
        P = lambda x, y, ox=ox, oy=oy: (ox + x * kk, oy + y * kk)
        if st.lay.get("areas"):
            for a in st.lay["areas"]:
                d.polygon([P(*q) for q in _outline(a, 24)], fill=tuple(a.get("color", [80, 80, 80])))
        else:
            d.rectangle([*P(0, 0.4), *P(st.W, st.D)], fill=(52, 58, 40))
            d.rectangle([*P(0, -1.2), *P(st.W, 0.4)], fill=(40, 150, 70))
        c = st.bk["cuts"][cut]
        d.text(((i % cols) * cw + 8, (i // cols) * ch + 4), f"{cut[-3:]} {c.get('frame', '')}", font=fs, fill="white")
        pos, face = st.states[cut]["end"]
        for who, p in pos.items():
            if p is None:
                continue
            x, y = P(*p)
            v = vec_of(face[who])
            d.line([x, y, x + v[0] * 12, y + v[1] * 12], fill=COLORS[who], width=2)
            d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=COLORS[who])
        if not c.get("insert"):
            cam = tuple(c["cam"][:2])
            look = c["look"]
            look = pos[look] if isinstance(look, str) and look in pos else st.place(look)
            b = bearing_of((look[0] - cam[0], look[1] - cam[1]))
            half = math.degrees(fov_half(c["lens"]))
            for s in (-half, half):
                v = vec_of(b + s)
                d.line([P(*cam), P(cam[0] + v[0] * 5, cam[1] + v[1] * 5)], fill=(255, 230, 120), width=1)
            x, y = P(*cam)
            d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 230, 120))
    p = out_dir / "staging_cuts.png"
    im.save(p)
    files.append(p)
    return files


# ---------- カメラから見た略図 ----------

def _clip_near(pts, zmin=0.1):
    """カメラ座標の多角形を、カメラの前（z >= zmin）だけに切る。"""
    out = []
    for i in range(len(pts)):
        a, b = pts[i - 1], pts[i]
        ina, inb = a[2] >= zmin, b[2] >= zmin
        if ina != inb:
            t = (zmin - a[2]) / (b[2] - a[2])
            out.append(tuple(a[k] + (b[k] - a[k]) * t for k in range(3)))
        if inb:
            out.append(b)
    return out


def _outline(a, n=72):
    if a["kind"] == "rect":
        (x0, x1), (y0, y1) = a["x"], a["y"]
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if a["kind"] == "circle":
        cx, cy = a["center"]
        return [(cx + a["r"] * math.cos(2 * math.pi * i / n), cy + a["r"] * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return [tuple(q) for q in a["points"]]


def _dense(pts, step=0.5):
    out = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        n = max(1, int(math.dist(a, b) / step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
    return out


def sketch_areas(st, d, cam, ch, d2, right, ct, stt, f, W, H, hz):
    """略図に、地面の区分（沼・岸・デッキ・橋）と屋敷の壁と屋根、玄関の扉を透視で描く。"""
    def cc(p, h):
        v = (p[0] - cam[0], p[1] - cam[1])
        x, z0, y0 = dot(v, right), dot(v, d2), h - ch
        return (x, -z0 * stt + y0 * ct, z0 * ct + y0 * stt)

    def poly(pts3, col, outline=None):
        c3 = _clip_near(pts3)
        if len(c3) >= 3:
            d.polygon([(W / 2 + f * q[0] / q[2], H / 2 - f * q[1] / q[2]) for q in c3], fill=col, outline=outline)
    d.rectangle([0, hz, W, H], fill=(40, 170, 80))   # 地平線までの沼
    hs = st.lay.get("house")
    for a in st.lay.get("areas", []):
        if a["kind"] == "rect" and a["x"][1] - a["x"][0] > 15:
            continue
        if hs and a["kind"] == "circle" and abs(a["r"] - hs["r"]) < 1e-6:
            continue
        poly([cc(p, 0.0) for p in _dense(_outline(a))], tuple(a.get("color", [80, 80, 80])), outline=(30, 30, 20))
    if not hs:
        return
    # 屋敷: 円柱の壁と円錐の屋根（遠い面から描く）
    cx, cy = hs["center"]
    n = 48
    segs = []
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        p0 = (cx + hs["r"] * math.cos(a0), cy + hs["r"] * math.sin(a0))
        p1 = (cx + hs["r"] * math.cos(a1), cy + hs["r"] * math.sin(a1))
        segs.append((math.dist(((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2), cam), p0, p1))
    segs.sort(reverse=True)
    for _, p0, p1 in segs:
        poly([cc(p0, 0), cc(p1, 0), cc(p1, hs["h"]), cc(p0, hs["h"])], (120, 70, 40), outline=(60, 35, 20))
    top = cc((cx, cy), hs["h"] + hs["roof"])
    for _, p0, p1 in segs:
        poly([cc(p0, hs["h"]), cc(p1, hs["h"]), top], (210, 110, 50), outline=(120, 60, 20))
    door = st.lay.get("props", {}).get("door")
    if door and dot((door["at"][0] - cam[0], door["at"][1] - cam[1]), (0, -1)) > 0:   # 南から見たときだけ
        x, y = door["at"]
        poly([cc((x - 0.5, y + 0.02), 0), cc((x + 0.5, y + 0.02), 0), cc((x + 0.5, y + 0.02), 2.2), cc((x - 0.5, y + 0.02), 2.2)],
             (50, 30, 25), outline=(0, 0, 0))


def sketch(st, cut, which, path, W=1536, H=864):
    """カメラの位置・向き・レンズ・高さから、人物（色の人形）と崖の縁・遠景の方位を透視で描く。構図の参照に使う。"""
    c = st.bk["cuts"][cut]
    pos, face = st.states[cut][which]
    cam = tuple(c["cam"][:2])
    ch = c["cam"][2]
    look = c.get("look_end", c["look"]) if which == "end" else c["look"]
    look = pos[look] if isinstance(look, str) and look in pos else st.place(look)
    d2 = norm((look[0] - cam[0], look[1] - cam[1]))
    right = (-d2[1], d2[0])
    lens = c.get("lens_end", c["lens"]) if which == "end" else c["lens"]
    f = (W / 2) / math.tan(fov_half(lens))
    tilt = tilt_of(c, cam, look)
    ct, stt = math.cos(tilt), math.sin(tilt)

    def proj(p, h):
        v = (p[0] - cam[0], p[1] - cam[1])
        x, z0 = dot(v, right), dot(v, d2)
        y0 = h - ch
        z = z0 * ct + y0 * stt
        y = -z0 * stt + y0 * ct
        if z <= 0.05:
            return None
        return (W / 2 + f * x / z, H / 2 - f * y / z, z)
    im = Image.new("RGB", (W, H), (150, 180, 200))
    d = ImageDraw.Draw(im)
    # 地平線と地面
    hz = min(H, max(0, H / 2 + f * math.tan(tilt)))
    d.rectangle([0, hz, W, H], fill=(70, 80, 55))
    fnt = ImageFont.truetype(FONT, 26)
    fs = ImageFont.truetype(FONT, 20)
    if st.lay.get("areas"):
        sketch_areas(st, d, cam, ch, d2, right, ct, stt, f, W, H, hz)
    # 崖の縁（y=0.4 の線）と、その向こうの沼（高台の配置だけ）
    pts = []
    for x in ([] if st.lay.get("areas") else [i * 0.25 for i in range(0, int(st.W * 4) + 1)]):
        q = proj((x, 0.4), 0.0)
        if q:
            pts.append((q[0], q[1]))
    if len(pts) > 1:
        poly = pts + [(pts[-1][0], max(0, hz)), (pts[0][0], max(0, hz))]
        d.polygon(poly, fill=(40, 170, 80))
        d.line(pts, fill=(30, 30, 20), width=4)
    # 遠景の方位の名前（地平線の少し上）
    half = math.degrees(fov_half(lens))
    cb = bearing_of(d2)
    seen = []
    for k_, a in (("left", -half * 0.7), ("center", 0.0), ("right", half * 0.7)):
        h_ = (st.lay["views"][c["bg"]] if c.get("bg") else None) or st.horizon_at((cb + a) % 360, cam, look)
        if h_ and h_["ja"] not in seen:
            seen.append(h_["ja"])
            x = W / 2 + f * math.tan(math.radians(a))
            d.text((x, max(30, hz - 40)), h_["ja"], font=fs, fill=(20, 40, 60), anchor="mm")
    # 近くの物
    for name, pr in st.lay.get("props", {}).items():
        if "at" not in pr or name == "door":
            continue
        ph = pr.get("h", 4.0 if name == "dead_tree" else 0.5)
        b = proj(tuple(pr["at"]), 0.0)
        t = proj(tuple(pr["at"]), ph)
        if b and t and -200 < b[0] < W + 200:
            wpx = max(6, f * (0.5 if ph > 2 else 0.3) / b[2])
            d.rectangle([b[0] - wpx / 2, min(t[1], b[1]), b[0] + wpx / 2, max(t[1], b[1])], fill=(90, 70, 50))
            d.text((b[0], t[1] - 14), pr["ja"][:6], font=fs, fill=(60, 40, 30), anchor="mm")
    # 人物（遠い順に描く）
    order = []
    only = c.get("only")
    for who, p in pos.items():
        if p is None or (only is not None and who not in only):
            continue
        b = proj(p, 0.0)
        if b:
            order.append((b[2], who, p))
    for z, who, p in sorted(order, reverse=True):
        hgt = st.lay["people"][who]["h"]
        b, t = proj(p, 0.0), proj(p, hgt)
        if not (b and t):
            continue
        w = f * (0.55 if who != "arlecchino" else 0.8) / b[2]
        col = COLORS[who]
        d.rounded_rectangle([b[0] - w / 2, t[1], b[0] + w / 2, b[1]], radius=int(w / 2.2) + 1, fill=col, outline=(20, 20, 20), width=3)
        # 顔の向き（頭の位置に、体の向きの矢印）
        hv = vec_of(face[who])
        sx = dot(hv, right)
        sz = dot(hv, d2)
        hy = t[1] + (b[1] - t[1]) * 0.1
        L = w * 0.9
        d.line([b[0], hy, b[0] + sx * L, hy + sz * L * 0.3], fill=(0, 0, 0), width=5)
        label = st.lay["people"][who]["ja"]
        body = "背中" if sz > 0.6 else ("正面" if sz < -0.6 else ("→" if sx > 0 else "←"))
        d.text((b[0], t[1] - 18), f"{label}（{body}）", font=fnt, fill=(0, 0, 0), anchor="mm")
    d.rectangle([0, 0, W, 40], fill=(0, 0, 0))
    d.text((12, 20), f"{cut} {'始め' if which == 'start' else '終わり'}  {c.get('frame', '')}  {lens}mm  高さ{ch}m  向き{round(cb)}°",
           font=fnt, fill="white", anchor="lm")
    im.save(path)


def sketches(st, out_dir):
    sd = out_dir / "sketch"
    sd.mkdir(exist_ok=True)
    made = []
    for cut in st.order:
        c = st.bk["cuts"][cut]
        if c.get("insert"):
            continue
        sketch(st, cut, "start", sd / f"{cut}_start.png")
        made.append(sd / f"{cut}_start.png")
        if c.get("move") or c.get("look_end") or c.get("lens_end"):
            sketch(st, cut, "end", sd / f"{cut}_end.png")
            made.append(sd / f"{cut}_end.png")
    # 一覧（スマホで見る用）
    th = [Image.open(p).resize((384, 216)) for p in made]
    cols = 4
    rows = (len(th) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 384, rows * 216), (0, 0, 0))
    for i, t in enumerate(th):
        sheet.paste(t, ((i % cols) * 384, (i // cols) * 216))
    for part in range((rows + 11) // 12):
        sheet.crop((0, part * 12 * 216, cols * 384, min(rows, (part + 1) * 12) * 216)).save(out_dir / f"staging_sketches_{part + 1}.jpg", quality=80)
    return made


# ---------- 本体 ----------

def describe(fr):
    if not fr["people"]:
        return "人物なし"
    out = []
    for e in fr["people"]:
        s = f"{SHORT[e['who']]}（{e['side']}・{e['body']}"
        if e.get("gaze"):
            s += f"・目線 {e['gaze']}"
        out.append(s + "）")
    return "、".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    a = ap.parse_args()
    st = Stage(a.scene_dir)
    scene = yaml.safe_load((st.dir / "scene.yaml").read_text(encoding="utf-8"))
    out_dir = st.dir / "gen"
    out_dir.mkdir(exist_ok=True)
    rows, data, missing = [], {}, {}
    for cut in st.order:
        c = st.bk["cuts"][cut]
        if c.get("insert"):
            rows.append(f"| {cut[-3:]} | {c.get('frame', '')} | インサート | {c['insert']} | | |")
            data[cut] = {"insert": c["insert"]}
            continue
        fs_, fe = st.frame(cut, "start"), st.frame(cut, "end")
        moved = bool(c.get("move")) or c.get("look_end") or c.get("lens_end") or c.get("gaze_end")
        bgs = []
        for h in fs_["bg"].values():
            if h and all((h["ja"], h.get("panel")) != (x["ja"], x.get("panel")) for x in bgs):
                bgs.append(h)
        if "panel" in (bgs[0] if bgs else {}):
            bgt = "／".join(f"{h['ja']}〔{h['panel'] or '要用意'}〕" for h in bgs)
            for h in bgs:
                if not h.get("panel"):
                    missing.setdefault(h["ja"], []).append(cut[-3:])
        else:
            bgt = "／".join(h["ja"] for h in bgs)
        cam = f"{st.where(c)}・{c['lens']}mm・{fs_['bearing']}°"
        rows.append(f"| {cut[-3:]} | {c.get('frame', '')} | {cam} | {describe(fs_)} | {describe(fe) if moved else '（同じ）'} | {bgt} |")
        data[cut] = {"cam": c["cam"], "lens": c["lens"], "bearing": fs_["bearing"], "frame": c.get("frame"),
                     "start": {"people": fs_["people"], "bg": bgt, "props": fs_["props"], "staging": st.staging_en(cut, fs_, "start")},
                     "end": {"people": fe["people"], "staging": st.staging_en(cut, fe, "end")} if moved else None,
                     "move": c.get("move"), "note": c.get("note")}
    ck = st.checks()
    L = [f"# {st.bk['scene']} 立ち位置とカメラ割り（tools/staging.py が blocking.yaml から計算）", "",
         "人物: メ＝メタリカ、ビ＝ビスコ、ア＝アルレッキーノ、百＝百騎兵。向きは体の向き（正面＝カメラを向く）。目線は見る相手の方向。",
         "カメラの ° は向いている方位（" + st.lay.get("compass", "北＝沼 0、東 90、南＝森 180、西 270") + "）。背景の〔〕は使う設定画のパネル。", "",
         "| カット | 画 | カメラ（どこから・レンズ・向き） | 始め（左→右） | 終わり（動く場合） | 背景 |",
         "| --- | --- | --- | --- | --- | --- |"] + rows
    if missing:
        L += ["", "## 設定画にない背景（ユーザーに用意してもらう）", ""]
        L += [f"- {k}: {'・'.join(v)}（{len(v)}カット）" for k, v in missing.items()]
    L += ["", "## 確かめた結果", ""]
    L.append(f"- 会話の軸をまたいだカット: {len(ck['axis'])}" + ("" if not ck["axis"] else "（" + "、".join(f"{x['cut'][-3:]} {x['axis']}" for x in ck["axis"]) + "）"))
    L.append(f"- 映っている人物の位置・向きの飛び（カットの間で説明なしに変わる）: {len(ck['jumps'])}" + ("" if not ck["jumps"] else "（" + "、".join(f"{x['cut'][-3:]} {SHORT[x['who']]}の{x['kind']}" for x in ck["jumps"]) + "）"))
    L.append(f"- 画面の外で動いたもの（映っていない間の移動・向き替え）: {len(ck['offscreen'])}" + ("" if not ck["offscreen"] else "（" + "、".join(f"{x['cut'][-3:]} {SHORT[x['who']]}の{x['kind']}" for x in ck["offscreen"]) + "）"))
    hid = {}
    for x in ck["hidden"]:
        hid.setdefault(x["cut"][-3:], set()).add(SHORT[x["who"]])
    L.append(f"- 映さないことにしたのに画面に入る人物: {len(hid)}カット" + ("" if not hid else "（" + "、".join(f"{k} {''.join(sorted(v))}" for k, v in hid.items()) + "）"))
    L.append(f"- 人物の重なり（0.45m 未満）: {len(ck['overlap'])}" + ("" if not ck["overlap"] else "（" + "、".join(f"{x['cut'][-3:]}" for x in ck["overlap"]) + "）"))
    (out_dir / "staging_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (out_dir / "staging_report.yaml").write_text(yaml.safe_dump({"cuts": data, "checks": ck}, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    files = render(st, out_dir, scene.get("sections", []))
    files += sketches(st, out_dir)[:0]
    print(out_dir / "staging_report.md")
    for p in files:
        print(p)
    print("軸", len(ck["axis"]), "飛び", len(ck["jumps"]), "画面外", len(ck["offscreen"]), "重なり", len(ck["overlap"]), "隠れ", len(ck["hidden"]))
    for k in ("axis", "jumps", "overlap", "hidden"):
        for x in ck[k]:
            print(" ", k, x)


if __name__ == "__main__":
    main()
