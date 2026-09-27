#!/usr/bin/env python3
"""ショットファイルと blocking から、静止画の生成計画（指示文・添付する画像・本数・費用）を作る。生成はしない（承認を取るための表）。

  python tools/stills_plan.py episodes/A/scenes/A01b A01b-001 A01b-006 ... [--out gen/stills_plan_pilot.yaml]

人物の見た目は設定画（refs/<人物>_sheet_01）で固定し、指示文には人物の見た目を書かない（CLAUDE.md 守ること3）。
背景は、用意画像（refs/*_bg_*）か設定画のパネルの切り抜き（world/<場所>/panel_*.png）。構図はカメラから見た略図（gen/sketch）を添付する。
"""
import argparse
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import staging  # noqa: E402

FRAME_EN = {"大引き": "extreme wide shot", "引き": "wide shot", "全身": "full shot (whole body)", "膝上": "medium-long shot (knees up)",
            "胸上": "medium close-up (chest up)", "寄り": "close-up", "大寄り": "extreme close-up"}
PANEL_FILE = {"用意画像 bg_01": "refs/witch-house-front_bg_01.png", "用意画像 bg_02": "refs/witch-house-front_bg_02.png",
              "枯れ木": "world/witch-house-front/panel_dead-tree.png", "沼の水面": "world/witch-house-front/panel_swamp-water.png"}
CREDITS = {"nano_banana_pro": 2.0, "gpt_image_2_5": 1.0}   # 2k、16:9 の1枚（logs/generations.jsonl の実績。GPT Image 2.5 は quality medium）
STYLE = ("2D TV anime style, clean line art, cel shading, rich but soft colors, 16:9. "
         "No text, no captions, no speech bubbles, no manga symbols (no anger marks, sweat drops or question marks).")


CLOSE = ("寄り", "大寄り", "胸上")


def view_files(who, body, frame):
    """体の向き（staging の body）に合う設定画の切り抜き（characters/<人物>/views、characters/views.yaml）。"""
    if body.startswith("正面"):
        v = ["front"]
    elif body.startswith("斜め前"):
        v = ["front", "side"]
    elif body.startswith("横顔"):
        v = ["side"]
    elif body.startswith("斜め後ろ"):
        v = ["side", "back"]
    else:
        v = ["back"]
    if frame in CLOSE and not body.startswith("背中"):
        v.insert(0, "face_side" if body.startswith("横顔") else "face_front")
    return [f"characters/{who}/views/{who}_{x}.png" for x in v]


def build_cel(st, cut, who, which="start"):
    """1人だけの絵（透明な背景）。3人以上の画は人物を1人ずつ作って重ねる（ユーザー判断 2026-09-27）。"""
    c = st.bk["cuts"][cut]
    fr = st.frame(cut, which)
    e = next(x for x in fr["people"] if x["who"] == who)
    pl = st.lay["people"][who]
    name = pl["en"].split(" (")[0]
    poses = (c.get("pose_end") or c.get("pose") or {}) if which == "end" else (c.get("pose") or {})
    pose = poses.get(who, "standing")
    for w in ("small and blurred in the background at the foot of the bridge", "standing beside Bisco, ", "standing beside Bisco"):   # 場所の言葉は1人の絵では使わない
        pose = pose.replace(w, "")
    pose = pose.strip(", ") or "standing naturally"
    files = view_files(who, e["body"], "全身")
    body_en = {"正面": "facing the viewer", "背中": "seen from behind"}.get(e["body"])
    if not body_en:
        side = "right" if "画面右" in e["body"] else "left"
        kind = "seen from behind at an angle" if e["body"].startswith("斜め後ろ") else ("in profile" if e["body"].startswith("横顔") else "in three-quarter view")
        body_en = f"{kind}, turned toward the {side} side of the picture"
    h = c["cam"][2]
    eye = "seen from a low camera looking up" if h < 0.6 else ("seen from slightly above" if h > 2.0 else "seen at eye level")
    gaze = ""
    if e.get("gaze_to"):
        gaze = f", looking toward the {'right' if e['gaze'].startswith('画面右') else 'left'} side of the picture"
    prompt = (f"A single full-body character cel of {name} (character reference: image{'s' if len(files) > 1 else ''} "
              f"{', '.join(str(i + 1) for i in range(len(files)))}), {pose}, {body_en}{gaze}, {eye}. "
              f"Height {pl['h']} m" + (" (small, only about waist-high to an adult)" if who == "hundred-knight" else "") + ". "
              f"Transparent background: no scenery, no ground, no floor shadow, no other characters, no text. "
              f"Soft early-morning light. Keep the design, colors and proportions identical to the reference. {STYLE}")
    return {"cut": cut, "who": who, "which": which, "refs": files, "prompt": prompt, "body": e["body"], "background": "transparent"}


def build(st, cut, which="start"):
    c = st.bk["cuts"][cut]
    shot = yaml.safe_load((st.dir / "shots" / f"{cut}.yaml").read_text(encoding="utf-8"))
    b = shot["blocking"]
    text = b["staging_en"] if which == "start" else b.get("staging_en_end", b["staging_en"])
    refs, n = [], 0
    fr = st.frame(cut, which)
    for e in fr["people"]:
        who = e["who"]
        files = view_files(who, e["body"], c["frame"])
        idx = list(range(n + 1, n + 1 + len(files)))
        n += len(files)
        refs += files
        name = st.lay["people"][who]["en"].split(" (")[0]
        text = text.replace(st.lay["people"][who]["en"], f"{name} (character reference: image{'s' if len(idx) > 1 else ''} {', '.join(map(str, idx))})")
    if fr["people"]:   # 身長（百騎兵が大きくなりすぎないように。ユーザーの指摘 2026-09-27）
        sizes = []
        for e in fr["people"]:
            pl = st.lay["people"][e["who"]]
            name = pl["en"].split(" (")[0]
            sizes.append(f"{name} {pl['h']} m" + (" (small, only about waist-high to Metallica and Bisco)" if e["who"] == "hundred-knight" else ""))
        text += " Heights: " + "; ".join(sizes) + "."
    bgs = [PANEL_FILE[p] for p in b["panels"] if p in PANEL_FILE]
    bg_idx = []
    for f in dict.fromkeys(bgs):
        n += 1
        refs.append(f)
        bg_idx.append(n)
    n += 1
    sk = f"episodes/A/scenes/A01b/gen/sketch/{cut}_{which}.png"
    refs.append(sk)
    x, y, h = c["cam"]
    cam = f"Camera: {FRAME_EN.get(c['frame'], c['frame'])}, {c['lens']}mm lens, camera height {h} m"
    if c.get("tilt") == "up" or h < 0.5:
        cam += ", low angle looking up"
    if c.get("tilt") == "down" or h > 3.0:
        cam += ", high angle looking down"
    prompt = (f"{cam}. {text} "
              f"Background and setting: follow image{'s' if len(bg_idx) > 1 else ''} {', '.join(map(str, bg_idx))} (same place, same props and layout). "
              f"Composition: image {n} is a rough layout sketch with colored placeholder figures; follow its camera angle, framing and the positions and "
              f"facing of the characters, but do not draw the placeholders, labels or flat colors. "
              f"Keep each character's design, colors and proportions identical to their character reference images. {STYLE}")
    return {"cut": cut, "which": which, "kind": c["kind"], "frame": c["frame"], "refs": refs, "prompt": prompt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("cuts", nargs="+")
    ap.add_argument("--model", default="nano_banana_pro")
    ap.add_argument("--out", default="gen/stills_plan.yaml")
    ap.add_argument("--cels", nargs="*", default=[], help="人物を1人ずつ作って重ねるカット")
    a = ap.parse_args()
    st = staging.Stage(a.scene_dir)
    items = [build(st, cut) for cut in a.cuts]
    cels = [build_cel(st, cut, e["who"]) for cut in a.cels for e in st.frame(cut, "start")["people"]]
    total = CREDITS.get(a.model, 0) * (len(items) + len(cels))
    out = {"model": a.model, "resolution": "2k", "aspect_ratio": "16:9", "count": len(items), "credits_estimate": total,
           "status": "承認待ち", "items": items, "cels": cels}
    p = st.dir / a.out
    p.write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    for it in items:
        print(it["cut"], it["kind"], it["frame"], len(it["refs"]), re.sub(r"\s+", " ", it["prompt"])[:160])
    for it in cels:
        print(it["cut"], "cel", it["who"], it["body"], len(it["refs"]))
    print("合計", len(items) + len(cels), "枚", total, "クレジット", p)


if __name__ == "__main__":
    main()
