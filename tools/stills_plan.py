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
CREDITS = {"nano_banana_pro": 2.0}   # 2k、16:9 の1枚（logs/generations.jsonl の実績）
STYLE = ("2D TV anime style, clean line art, cel shading, rich but soft colors, 16:9. "
         "No text, no captions, no speech bubbles, no manga symbols (no anger marks, sweat drops or question marks).")


def build(st, cut, which="start"):
    c = st.bk["cuts"][cut]
    shot = yaml.safe_load((st.dir / "shots" / f"{cut}.yaml").read_text(encoding="utf-8"))
    b = shot["blocking"]
    text = b["staging_en"] if which == "start" else b.get("staging_en_end", b["staging_en"])
    refs, n = [], 0
    fr = st.frame(cut, which)
    for e in fr["people"]:
        who = e["who"]
        n += 1
        refs.append(f"refs/{who}_sheet_01.png")
        name = st.lay["people"][who]["en"].split(" (")[0]
        text = text.replace(st.lay["people"][who]["en"], f"{name} (character sheet: image {n})")
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
              f"Keep each character's design, colors and proportions identical to their character sheet. {STYLE}")
    return {"cut": cut, "which": which, "kind": c["kind"], "frame": c["frame"], "refs": refs, "prompt": prompt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("cuts", nargs="+")
    ap.add_argument("--model", default="nano_banana_pro")
    ap.add_argument("--out", default="gen/stills_plan.yaml")
    a = ap.parse_args()
    st = staging.Stage(a.scene_dir)
    items = [build(st, cut) for cut in a.cuts]
    total = CREDITS.get(a.model, 0) * len(items)
    out = {"model": a.model, "resolution": "2k", "aspect_ratio": "16:9", "count": len(items), "credits_estimate": total,
           "status": "承認待ち", "items": items}
    p = st.dir / a.out
    p.write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    for it in items:
        print(it["cut"], it["kind"], it["frame"], len(it["refs"]), re.sub(r"\s+", " ", it["prompt"])[:160])
    print("合計", len(items), "枚", total, "クレジット", p)


if __name__ == "__main__":
    main()
