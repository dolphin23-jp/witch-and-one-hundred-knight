#!/usr/bin/env python3
"""blocking.yaml と小さな計画表から、新しいシーンのショットファイル（shots/<カット>.yaml）を作る（D-062 の 3D の段階）。

  python3 tools/new_shots.py episodes/A/scenes/A08b --plan plan.yaml [--force]

計画表（YAML）:
  header: ショットファイルの頭に書くコメント（1行ずつのリスト）
  background: beast-village            # ショットファイルの background
  audio: audio/A08/original            # 台詞の文（script.yaml）を読む所
  cuts:
    A08b-009: {sec: 10.6, shot: 引き・ティト・4人, lines: [L224, L225]}
    A08b-036: {sec: 9.6, shot: ..., lines: [L265], lead: 1.5}

blocking.yaml から取るもの: kind、act（start・do・end）、camera（カメラの動きの文）、最初の cam_keys（位置・見る所・レンズ）、
映る人物（only、なければ start の全員）と生き物（creatures）→ refs。tc は計画表の順に秒を足していく。
すでにあるファイルは --force のときだけ書き直す。書いた後は tools/voice_yaml.py と tools/shot_blocking.py を回す。
"""
import argparse
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    d = ROOT / a.scene_dir
    bk = yaml.safe_load((d / "blocking.yaml").read_text(encoding="utf-8"))
    plan = yaml.safe_load(pathlib.Path(a.plan).read_text(encoding="utf-8"))
    script = yaml.safe_load((ROOT / plan["audio"] / "script.yaml").read_text(encoding="utf-8"))
    texts = {l["id"]: l["text"].strip() for l in (script if isinstance(script, list) else script["lines"])}
    (d / "shots").mkdir(parents=True, exist_ok=True)
    tc = float(plan.get("tc0", 0.0))
    people = list(bk["start"])
    for cut, p in plan["cuts"].items():
        c = bk["cuts"][cut]
        sec = float(p["sec"])
        shown = c.get("only") if c.get("only") is not None else people
        ck = (c.get("cam_keys") or [{}])[0]
        act = c.get("act") or {}
        lead = p.get("lead", 0.3)
        shot = {
            "id": cut, "scene": bk["scene"], "tc": round(tc, 2), "sec": sec, "kind": c.get("kind", "M"),
            "shot": p["shot"], "acting": act.get("do"),
            "dialogue": [f"{l}{p.get('tag', {}).get(l, '')}「{texts[l]}」" for l in p.get("lines", [])],
            "sound": p.get("sound"), "notes": p.get("notes"),
            "camera": {"pos": ck.get("cam"), "look": ck.get("look"), "lens": ck.get("lens"), "move": c.get("camera")},
            "background": plan["background"], "image": None,
            "refs": list(shown) + list((c.get("creatures") or {}).keys()),
            "overlays": [], "fallback": None,
            "act": {"start": act.get("start"), "end": act.get("end")},
            "timing": {"lead": lead},
        }
        if c.get("slow"):
            shot["slow"] = c["slow"]
        out = d / "shots" / f"{cut}.yaml"
        tc += sec
        if out.exists() and not a.force:
            print(f"{cut}: ある（--force で書き直す）")
            continue
        head = "".join(f"# {h}\n" for h in plan.get("header", []))
        out.write_text(head + yaml.safe_dump(shot, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
        print(f"{cut}: tc {shot['tc']} 秒 {sec}")
    print(f"計 {tc - float(plan.get('tc0', 0.0)):.1f} 秒")


if __name__ == "__main__":
    main()
