"""立ち位置とカメラ割り（blocking.yaml）から、各カットで何が映るかを計算し、図と表を作る（演出ノート P-04）。

  python tools/blocking.py episodes/A/scenes/A01

出力（<シーン>/gen/）:
  blocking_report.md    カット表（カメラ、画面の左右に誰がいるか、目線の向き、背景、軸の確認）
  blocking_report.yaml  同じ内容と、生成の指示文に足す英語の文（staging）
  blocking_map_<区間>.png  区間ごとの上から見た図（人物の位置とカメラ）
  blocking_cuts.png     カットごとの小さな図の一覧

座標の決まり: x は西 0 → 東、y は奥（北）0 → 手前（南）。カメラの右 = 向きを時計回りに90度回した方向。
"""
import argparse
import math
import pathlib

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
COLORS = {"metallica": (255, 150, 40), "bisco": (90, 150, 255), "hundred-knight": (160, 220, 255), "arlecchino": (220, 120, 220)}
SHORT = {"metallica": "メ", "bisco": "ビ", "hundred-knight": "百", "arlecchino": "ア"}
SECTIONS = [("1", "区間① 朝食〜登場", "A01-002", "A01-011"), ("3a", "区間③ メタリカ↔ビスコ", "A01-012", "A01-017b"),
            ("3b", "区間③ 丸窓・紅茶・指差し", "A01-018", "A01-031"), ("4", "区間④ ソファ〜玄関", "A01-032", "A01-040a")]


def fov_half(lens):
    return math.atan(18.0 / float(lens))


def norm(v):
    L = math.hypot(*v) or 1.0
    return (v[0] / L, v[1] / L)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


class Scene:
    def __init__(self, scene_dir):
        self.dir = ROOT / scene_dir
        self.bk = yaml.safe_load((self.dir / "blocking.yaml").read_text(encoding="utf-8"))
        self.lay = yaml.safe_load((ROOT / self.bk["layout"]).read_text(encoding="utf-8"))
        self.W, self.D = self.lay["size"]["w"], self.lay["size"]["d"]
        self.order = list(self.bk["cuts"])

    def place(self, v):
        if v is None:
            return None
        if isinstance(v, str):
            return tuple(self.bk["places"][v])
        return tuple(v)

    def positions(self, cut):
        c = self.bk["cuts"][cut]
        pos = dict(self.bk["beats"][c["beat"]])
        pos.update(c.get("pos", {}))
        return {k: self.place(v) for k, v in pos.items() if v is not None}

    def target(self, v, pos):
        if isinstance(v, str):
            if v in pos:
                return pos[v]
            if v == "hk":
                return tuple(self.bk["places"]["hk"])
            return self.place(v)
        return tuple(v)

    def wall_hit(self, p, d, outside=False):
        hits = []
        for name, t in (("W", (0 - p[0]) / d[0] if d[0] else None), ("E", (self.W - p[0]) / d[0] if d[0] else None),
                        ("N", (0 - p[1]) / d[1] if d[1] else None), ("S", (self.D - p[1]) / d[1] if d[1] else None)):
            if t is None or t <= 1e-6:
                continue
            x, y = p[0] + d[0] * t, p[1] + d[1] * t
            if -1e-6 <= x <= self.W + 1e-6 and -1e-6 <= y <= self.D + 1e-6:
                hits.append((t, name, x, y))
        if not hits:
            return None
        t, name, x, y = (max if outside else min)(hits)
        s = x if name in ("N", "S") else y
        for f in self.lay["walls"][name]:
            if f["range"][0] - 1e-6 <= s <= f["range"][1] + 1e-6:
                return {"wall": name, "at": round(s, 2), "ja": f["ja"], "en": f["en"]}
        return {"wall": name, "at": round(s, 2), "ja": "壁", "en": "wall"}

    def analyze(self, cut):
        c = self.bk["cuts"][cut]
        if c.get("outside"):
            return {"id": cut, "outside": c["outside"]}
        pos = self.positions(cut)
        cam = tuple(c["cam"][:2])
        look = self.target(c["look"], pos)
        d = norm((look[0] - cam[0], look[1] - cam[1]))
        right = (-d[1], d[0])
        half = fov_half(c["lens"])
        th = math.tan(half)
        only = c.get("only")
        people = []
        for who, p in pos.items():
            if only is not None and who not in only:
                continue
            v = (p[0] - cam[0], p[1] - cam[1])
            z = dot(v, d)
            if z <= 0.15:
                continue
            s = dot(v, right) / z / th
            if abs(s) > 1.08:
                continue
            e = {"who": who, "s": round(s, 2), "dist": round(z, 2)}
            e["side"] = "左" if s < -0.33 else ("右" if s > 0.33 else "中央")
            g = c.get("gaze", {}).get(who)
            if g is not None:
                t = self.target(g, pos)
                gv = norm((t[0] - p[0], t[1] - p[1]))
                gx, gz = dot(gv, right), dot(gv, d)
                if abs(gx) < 0.25:
                    e["gaze"] = "カメラの方" if gz < 0 else "奥（カメラに背を向ける）"
                else:
                    e["gaze"] = ("画面右" if gx > 0 else "画面左") + ("・手前寄り" if gz < -0.3 else ("・奥寄り" if gz > 0.3 else ""))
            people.append(e)
        people.sort(key=lambda e: e["s"])
        outside = bool(c.get("outside_window"))
        bg = {k: self.wall_hit(cam, norm((d[0] * math.cos(a) - d[1] * math.sin(a), d[1] * math.cos(a) + d[0] * math.sin(a))), outside)
              for k, a in (("left", -half * 0.9), ("center", 0.0), ("right", half * 0.9))}
        props = []
        for name, pr in self.lay["props"].items():
            at = pr.get("center") or pr.get("at")
            if at is None:
                at = [(pr["x"][0] + pr["x"][1]) / 2, (pr["y"][0] + pr["y"][1]) / 2]
            v = (at[0] - cam[0], at[1] - cam[1])
            z = dot(v, d)
            if z > 0.2 and abs(dot(v, right) / z / th) <= 1.0 and "ja" in pr:
                props.append(pr["ja"])
        # テーブルが手前に入るか（カメラと見る点の間をテーブルが横切る）
        tb = self.lay["props"]["table"]
        fg_table = False
        tc, r = tb["center"], tb["r"]
        # 手前のテーブル: カメラから見る方向にテーブルがあり、いちばん近い縁まで 1.2m 以内
        v = (tc[0] - cam[0], tc[1] - cam[1])
        edge = math.hypot(*v) - r
        if dot(v, d) > 0 and edge < 1.2 and abs(dot(v, right)) < r + 0.6 and c.get("only") != []:
            fg_table = True
        out = {"id": cut, "cam": list(c["cam"]), "lens": c["lens"], "frame": c.get("frame"), "people": people,
               "bg": {k: (v["ja"] if v else None) for k, v in bg.items()}, "props": props, "note": c.get("note"),
               "where": self.where(cam, c), "toward": bg["center"]["ja"] if bg["center"] else "", "fg_table": fg_table}
        out["staging"] = self.staging(cut, c, people, bg, fg_table)
        return out

    def where(self, cam, c):
        """カメラの位置を言葉にする（テーブル基準、左＝西・右＝東・奥＝北・手前＝南）。"""
        if c.get("outside_window"):
            return "丸窓の外"
        x, y = cam
        h = c["cam"][2]
        hw = "床すれすれ" if h < 0.5 else ("高い位置" if h > 2.5 else ("低め" if h < 1.0 else ""))
        tc, r = self.lay["props"]["table"]["center"], self.lay["props"]["table"]["r"]
        if x < 1.4 and y < 2.0 and h > 2.5:
            s = "階段の上の踊り場"
        elif x < 1.6 and y > 4.8:
            s = "階段の下"
        elif y > 5.0:
            s = "部屋の手前" + ("（玄関の角）" if x > 6.0 else "")
        elif math.hypot(x - tc[0], y - tc[1]) < r:
            s = "テーブルの上"
        elif y < 1.3 and 3.0 <= x <= 5.8:
            s = "ソファの前"
        else:
            dx, dy = x - tc[0], y - tc[1]
            ew = "左" if dx < -0.5 else ("右" if dx > 0.5 else "")
            ns = "奥" if dy < -0.5 else ("手前" if dy > 0.5 else "")
            s = "テーブルの" + (ew + ns or "脇")
        return s + (f"・{hw}" if hw else "")

    def staging(self, cut, c, people, bg, fg_table=False):
        en = {k: v["en"] for k, v in self.lay["people"].items()}
        side_en = {"左": "on the left of the frame", "右": "on the right of the frame", "中央": "in the center of the frame"}
        gaze_en = {"カメラの方": "looking toward the camera", "奥（カメラに背を向ける）": "facing away from the camera"}
        parts = []
        for e in people:
            s = f"{en[e['who']]} {side_en[e['side']]}"
            if e["dist"] > 4.0 and c.get("frame") not in ("引き",):
                s += ", small in the background"
            g = e.get("gaze")
            if g:
                if g in gaze_en:
                    s += ", " + gaze_en[g]
                else:
                    s += ", looking toward screen " + ("right" if g.startswith("画面右") else "left")
            parts.append(s)
        seen = []
        for k in ("left", "center", "right"):
            if bg[k] and bg[k]["en"] not in seen:
                seen.append(bg[k]["en"])
        txt = ""
        if parts:
            n = len(parts)
            txt += "In frame (exactly " + ("one person" if n == 1 else f"{n} people") + "): " + "; ".join(parts) + ". No other people. "
        elif c.get("only") == []:
            txt += "No people in frame. "
        if fg_table:
            txt += "The edge of the round table with plates is in the foreground at the bottom of the frame. "
        txt += "Behind: " + ", then ".join(seen) + "."
        return txt

    def axis_checks(self):
        res = []
        for ax in self.bk.get("axes", []):
            i0, i1 = self.order.index(ax["cuts"][0]), self.order.index(ax["cuts"][1])
            for cut in self.order[i0:i1 + 1]:
                c = self.bk["cuts"][cut]
                if c.get("outside") or c.get("outside_window") or c.get("axis_free"):
                    continue
                pos = self.positions(cut)
                if ax["a"] not in pos or ax["b"] not in pos:
                    continue
                a, b = pos[ax["a"]], pos[ax["b"]]
                lv = norm((b[0] - a[0], b[1] - a[1]))

                def side(p):
                    return lv[0] * (p[1] - a[1]) - lv[1] * (p[0] - a[0])
                sc, ss = side(c["cam"][:2]), side(ax["side"])
                ok = sc * ss > 0 or abs(sc) < 0.35
                res.append({"axis": ax["name"], "cut": cut, "ok": ok, "offset": round(sc, 2)})
        return res


def draw_room(sc, d, ox, oy, k, font):
    W, D = sc.W, sc.D
    P = lambda x, y: (ox + x * k, oy + y * k)
    d.rectangle([*P(0, 0), *P(W, D)], fill=(42, 38, 33), outline=(216, 201, 168), width=3)
    wallcol = {"階段": (140, 106, 68), "丸窓": (159, 216, 255), "本棚": (120, 80, 40), "ソファ": (160, 40, 40), "玄関": (63, 191, 95), "薬瓶": (110, 90, 140)}
    for name, feats in sc.lay["walls"].items():
        for f in feats:
            col = next((v for kk, v in wallcol.items() if kk in f["ja"]), (90, 80, 70))
            a, b = f["range"]
            seg = {"N": (P(a, 0), P(b, 0)), "S": (P(a, D), P(b, D)), "W": (P(0, a), P(0, b)), "E": (P(W, a), P(W, b))}[name]
            d.line(seg, fill=col, width=9)
    t = sc.lay["props"]["table"]
    cx, cy = P(*t["center"])
    d.ellipse([cx - t["r"] * k, cy - t["r"] * k, cx + t["r"] * k, cy + t["r"] * k], fill=(176, 58, 46))
    s = sc.lay["props"]["sofa"]
    d.rectangle([*P(s["x"][0], s["y"][0]), *P(s["x"][1], s["y"][1])], fill=(130, 30, 30))
    for i in range(8):
        d.rectangle([*P(0.05, 0.5 + i * 0.62), *P(1.2, 0.5 + i * 0.62 + 0.5)], outline=(140, 106, 68), width=2)
    bx, by = P(*sc.lay["props"]["books"]["at"])
    d.rectangle([bx - 6, by - 6, bx + 6, by + 6], fill=(150, 120, 80))
    dx, dy = P(*sc.lay["props"]["dolls"]["at"])
    d.ellipse([dx - 6, dy - 6, dx + 6, dy + 6], fill=(120, 150, 110))
    return P


def person(d, P, who, p, font, label=None):
    x, y = P(*p)
    col = COLORS.get(who, (255, 255, 255))
    d.ellipse([x - 16, y - 16, x + 16, y + 16], fill=col, outline="black", width=3)
    d.text((x, y), label or SHORT.get(who, "?"), font=font, fill="black", anchor="mm")


def camera(d, P, k, c, font, col, label):
    cx, cy = P(*c["cam"][:2])
    return cx, cy


def render(sc, reports, out_dir):
    fbig = ImageFont.truetype(FONT, 30)
    f = ImageFont.truetype(FONT, 18)
    fs = ImageFont.truetype(FONT, 15)
    k = 135
    palette = [(255, 208, 96), (127, 223, 255), (255, 159, 208), (160, 255, 144), (255, 255, 255), (192, 160, 255), (255, 120, 120), (120, 255, 220)]
    files = []
    for key, title, a, b in SECTIONS:
        i0, i1 = sc.order.index(a), sc.order.index(b)
        cuts = [cu for cu in sc.order[i0:i1 + 1] if not sc.bk["cuts"][cu].get("outside")]
        W, H = int(sc.W * k) + 220, int((sc.D + 1.2) * k) + 170
        im = Image.new("RGB", (W, H), (27, 29, 34))
        d = ImageDraw.Draw(im)
        d.text((W // 2, 30), f"A01 {title}（上から見た図）", font=fbig, fill="white", anchor="mm")
        P = draw_room(sc, d, 110, 150, k, f)
        d.text(P(sc.W / 2, -0.35), "奥（ソファ・丸窓）", font=f, fill=(170, 170, 170), anchor="mm")
        d.text(P(sc.W / 2, sc.D + 0.3), "手前（玄関側）", font=f, fill=(170, 170, 170), anchor="mm")
        d.text(P(-0.5, 3.0), "階段", font=f, fill=(232, 200, 144), anchor="mm")
        d.text(P(sc.W + 0.5, 2.7), "本棚", font=f, fill=(232, 200, 144), anchor="mm")
        d.text(P(6.9, sc.D - 0.35), "玄関", font=f, fill=(127, 255, 159), anchor="mm")
        # カメラ
        for n, cu in enumerate(cuts):
            c = sc.bk["cuts"][cu]
            col = palette[n % len(palette)]
            cam = tuple(c["cam"][:2])
            pos = sc.positions(cu)
            look = sc.target(c["look"], pos)
            dv = norm((look[0] - cam[0], look[1] - cam[1]))
            half = fov_half(c["lens"])
            for sgn in (-1, 1):
                a = sgn * half
                v = (dv[0] * math.cos(a) - dv[1] * math.sin(a), dv[1] * math.cos(a) + dv[0] * math.sin(a))
                d.line([P(*cam), P(cam[0] + v[0] * 0.6, cam[1] + v[1] * 0.6)], fill=col, width=3)
            x, y = P(*cam)
            d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=col)
            d.text((x - dv[0] * 24, y - dv[1] * 24), cu[4:], font=f, fill=col, anchor="mm", stroke_width=2, stroke_fill="black")
        # 人物（区間の中で動く人は位置ごとに番号）
        seen = {}
        for cu in cuts:
            for who, p in sc.positions(cu).items():
                seen.setdefault(who, [])
                if p not in seen[who]:
                    seen[who].append(p)
        for who, ps in seen.items():
            for j, p in enumerate(ps):
                if j:
                    d.line([P(*ps[j - 1]), P(*p)], fill=COLORS[who], width=2)
            for j, p in enumerate(ps):
                person(d, P, who, p, fs, SHORT[who] + (str(j + 1) if len(ps) > 1 else ""))
        yy = int(150 + (sc.D + 0.45) * k)
        legend = "  ".join(f"{SHORT[w]}={sc.lay['people'][w]['ja']}" for w in SHORT)
        d.text((W // 2, yy + 20), legend + "（数字は区間の中での位置の順）", font=f, fill=(220, 220, 220), anchor="mm")
        d.text((W // 2, yy + 48), "点＝カメラの位置、2本の線＝画角、番号＝カット", font=f, fill=(220, 220, 220), anchor="mm")
        path = out_dir / f"blocking_map_{key}.png"
        im.save(path)
        files.append(path)
    # カットごとの小さな図
    kk = 34
    tw, th = int(sc.W * kk) + 20, int(sc.D * kk) + 44
    cuts = [cu for cu in sc.order if not sc.bk["cuts"][cu].get("outside")]
    cols = 8
    sheet = Image.new("RGB", (tw * cols, th * ((len(cuts) + cols - 1) // cols)), (27, 29, 34))
    for n, cu in enumerate(cuts):
        tile = Image.new("RGB", (tw, th), (27, 29, 34))
        d = ImageDraw.Draw(tile)
        P = draw_room(sc, d, 10, 32, kk, fs)
        c = sc.bk["cuts"][cu]
        pos = sc.positions(cu)
        for who, p in pos.items():
            x, y = P(*p)
            d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=COLORS[who])
        cam = tuple(c["cam"][:2])
        look = sc.target(c["look"], pos)
        dv = norm((look[0] - cam[0], look[1] - cam[1]))
        half = fov_half(c["lens"])
        pts = [P(*cam)]
        for a in (-half, half):
            v = (dv[0] * math.cos(a) - dv[1] * math.sin(a), dv[1] * math.cos(a) + dv[0] * math.sin(a))
            pts.append(P(cam[0] + v[0] * 3.0, cam[1] + v[1] * 3.0))
        d.polygon(pts, outline=(255, 255, 0))
        d.text((6, 4), f"{cu[4:]} {c.get('frame', '')}", font=fs, fill="white")
        sheet.paste(tile, ((n % cols) * tw, (n // cols) * th))
    sheet.save(out_dir / "blocking_cuts.png")
    files.append(out_dir / "blocking_cuts.png")
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    a = ap.parse_args()
    sc = Scene(a.scene_dir)
    out_dir = sc.dir / "gen"
    out_dir.mkdir(exist_ok=True)
    reports = [sc.analyze(cu) for cu in sc.order]
    axes = sc.axis_checks()
    bad = [x for x in axes if not x["ok"]]
    ja = {k: v["ja"] for k, v in sc.lay["people"].items()}
    LENS = {24: "広角", 28: "広角", 35: "標準", 50: "やや望遠", 85: "望遠"}
    rows = ["| カット | 画 | カメラ（どこから・レンズ） | 画面の中（左→右）と目線 | 背景（左・中・右） |", "| --- | --- | --- | --- | --- |"]
    for r in reports:
        if r.get("outside"):
            rows.append(f"| {r['id'][4:]} | — | 外 | — | {r['outside']} |")
            continue
        ppl = "、".join(f"{ja[e['who']]}（{e['side']}{'・' + e['gaze'] + 'を見る' if e.get('gaze') else ''}）" for e in r["people"]) or "人物なし"
        bgs = []
        for k in ("left", "center", "right"):
            v = r["bg"][k]
            if v and v not in bgs:
                bgs.append(v)
        c = r["cam"]
        fg = "（手前にテーブル）" if r.get("fg_table") else ""
        rows.append(f"| {r['id'][4:]} | {r['frame']} | {r['where']}・{LENS.get(r['lens'], str(r['lens']) + 'mm')}{fg} | {ppl} | {'／'.join(bgs)} |")
    md = ["# A01 立ち位置とカメラ割り（tools/blocking.py が blocking.yaml から計算）", "",
          "目線の「画面右／左」は、その人が見る相手の方向。会話の二人は、同じ区間の中で逆向きになっていれば軸をまたいでいない。", "",
          *rows, "", "## 会話の軸の確認", ""]
    md += [f"- {x['axis']}: {x['cut'][4:]} が軸の反対側（{x['offset']}）" for x in bad] or ["- すべて軸の同じ側から撮っている"]
    (out_dir / "blocking_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out_dir / "blocking_report.yaml").write_text(yaml.safe_dump({"cuts": reports, "axes": axes}, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    files = render(sc, reports, out_dir)
    print(f"{len(reports)}カット、軸の反対側 {len(bad)}")
    for x in bad:
        print("  軸:", x)
    for f in files:
        print(f.relative_to(ROOT))


if __name__ == "__main__":
    main()
