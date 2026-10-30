#!/usr/bin/env python3
"""人物を1人ずつ作った絵（透明な背景、セル）を、blocking の位置・大きさ・前後で背景に重ねる（アニメの背景とセルの作り方）。

  python tools/composite.py episodes/A/scenes/A01b A01b-034 --cels <dir> --plate refs/witch-house-front_bg_01.png --out <png>

人物の足元の位置と背の高さは、tools/staging.py の略図と同じ透視の計算で決める（カメラの位置・向き・レンズ・高さ）。
奥の人物ほど先に描く。blur_far を指定すると、遠い人物をぼかす（062 の「奥はぼけている」など）。
"""
import argparse
import math
import pathlib
import sys

from PIL import Image, ImageFilter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import staging  # noqa: E402

# セルの不透明な部分の高さが、身長の何倍か（帽子や頭の炎の分）
EXTRA = {"metallica": 1.25, "bisco": 1.05, "arlecchino": 1.1, "hundred-knight": 1.5}


def layout(st, cut, which="start", W=1920, H=1080):
    c = st.bk["cuts"][cut]
    pos, face = st.states[cut][which]
    camv = (c.get("cam_end") if which == "end" and c.get("cam_end") else c["cam"])   # カメラ本体が動くカット（previs.py の cam_end）は、終わりの位置で重ねる
    cam = tuple(camv[:2])
    ch = camv[2]
    look = c.get("look_end", c["look"]) if which == "end" else c["look"]
    look = pos[look] if isinstance(look, str) and look in pos else st.place(look)
    d2 = staging.norm((look[0] - cam[0], look[1] - cam[1]))
    right = (-d2[1], d2[0])
    lens = c.get("lens_end", c["lens"]) if which == "end" else c["lens"]
    f = (W / 2) / math.tan(staging.fov_half(lens))
    tilt = staging.tilt_of(c, cam, look)
    ct, stt = math.cos(tilt), math.sin(tilt)

    def proj(p, h):
        v = (p[0] - cam[0], p[1] - cam[1])
        x, z0, y0 = staging.dot(v, right), staging.dot(v, d2), h - ch
        z = z0 * ct + y0 * stt
        y = -z0 * stt + y0 * ct
        return None if z <= 0.05 else (W / 2 + f * x / z, H / 2 - f * y / z, z)
    out = []
    for e in st.frame(cut, which)["people"]:
        p = pos[e["who"]]
        b, t = proj(p, 0.0), proj(p, st.lay["people"][e["who"]]["h"])
        out.append({"who": e["who"], "x": b[0], "foot": b[1], "height": b[1] - t[1], "z": b[2]})
    return sorted(out, key=lambda o: -o["z"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("cut")
    ap.add_argument("--cels", required=True, help="<dir>/<cut>_<人物>.png")
    ap.add_argument("--plate", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--blur_far", type=float, default=0.0, help="この距離(m)より遠い人物をぼかす")
    ap.add_argument("--which", default="start", choices=["start", "end"], help="カットの始めと終わりのどちらの立ち位置で重ねるか")
    ap.add_argument("--from_h", type=float, default=0.0, help="セルが膝上などのとき、セルの下端の高さ(m)")
    a = ap.parse_args()
    st = staging.Stage(a.scene_dir)
    W, H = 1920, 1080
    plate = Image.open(a.plate).convert("RGBA")
    s = max(W / plate.width, H / plate.height)
    plate = plate.resize((round(plate.width * s), round(plate.height * s)))
    plate = plate.crop(((plate.width - W) // 2, (plate.height - H) // 2, (plate.width - W) // 2 + W, (plate.height - H) // 2 + H))
    for o in layout(st, a.cut, a.which, W=W, H=H):
        cel = Image.open(pathlib.Path(a.cels) / f"{a.cut}_{o['who']}.png").convert("RGBA")
        bb = cel.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
        cel = cel.crop(bb)
        h = st.lay["people"][o["who"]]["h"]
        part = (h - a.from_h) / h
        k = o["height"] * part * EXTRA.get(o["who"], 1.1) / cel.height
        cel = cel.resize((max(1, round(cel.width * k)), max(1, round(cel.height * k))), Image.LANCZOS)
        o["foot"] -= o["height"] * a.from_h / h
        if a.blur_far and o["z"] > a.blur_far:
            cel = cel.filter(ImageFilter.GaussianBlur(3))
        plate.alpha_composite(cel, (round(o["x"] - cel.width / 2), round(o["foot"] - cel.height)))
        print(o["who"], round(o["x"]), round(o["foot"]), round(o["height"]), round(o["z"], 2))
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    plate.convert("RGB").save(a.out)


if __name__ == "__main__":
    main()
