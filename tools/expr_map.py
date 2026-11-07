#!/usr/bin/env python3
"""表情の当たり方の一覧（2026-11-07、下絵のアップグレードの続き）。描かずに、カットごとに人物の表情がどう決まるかを書き出す。

  python tools/expr_map.py episodes/A/scenes/A17           # 気持ちの山なのに主役が「ふつう」のカットだけ
  python tools/expr_map.py episodes/A/scenes/A17 --all     # 全カット

表情の決め方は previs.py の face_state と同じ（tools/previs_face.py）:
  keys の expr → 話している台詞の文の感動詞と記号 → カットの画の名前（shot）と芝居（acting）の文で、その人が主語の区切りにある言葉
  → 立ち位置の設計（blocking の act）の始め・終わりの文 → ふつう
「気持ちの山」: 画の名前か芝居の文に気持ちの言葉（泣く・怒る・笑う・驚く・照れる…、previs_face.WORDS）があるカット。
主役: 画の名前に最初に出てくる人。その人が台詞でも芝居でも keys でも「ふつう」なら、印（!）を付ける。
"""
import argparse
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import previs_face  # noqa: E402

JA = {"neutral": "ふつう", "smile": "笑み", "grin": "にやり", "angry": "怒り", "shout": "叫ぶ", "sad": "悲しみ", "surprised": "驚き",
      "tired": "疲れ", "shy": "照れ", "annoyed": "呆れ"}


def scene_map(scene_dir):
    bk = yaml.safe_load((scene_dir / "blocking.yaml").read_text(encoding="utf-8"))
    lay = yaml.safe_load((ROOT / bk["layout"]).read_text(encoding="utf-8")) or {}
    extra = ROOT / "world" / "ch7-people-extra.yaml"
    people = dict(lay.get("people") or {})
    if extra.exists():
        for w, p in ((yaml.safe_load(extra.read_text(encoding="utf-8")) or {}).get("people") or {}).items():
            people.setdefault(w, p)
    texts = previs_face._texts(scene_dir)
    rows = []
    for cut, c in bk["cuts"].items():
        sp = scene_dir / "shots" / f"{cut}.yaml"
        if not sp.exists():
            continue
        sh = yaml.safe_load(sp.read_text(encoding="utf-8"))
        keys = c.get("keys") if isinstance(c.get("keys"), dict) else {}
        shown = list(c.get("only") or keys.keys())
        names = {w: [str((people.get(w) or {}).get("ja", "")).split("（")[0]] for w in shown}
        label, acting = str(sh.get("shot") or ""), str(sh.get("acting") or "")
        act = previs_face.acting_expr(label + "。" + acting, names)
        key_ex = {w: [k["expr"] for k in ks if isinstance(k, dict) and k.get("expr")] for w, ks in keys.items()}
        lines = []
        for d in sh.get("dialogue") or []:
            m = re.match(r"(L\d+)", str(d))
            if m:
                lid = m.group(1)
                lines.append((lid, previs_face.expr_from_speech(texts.get(lid, ""))))
        mood = previs_face.expr_from_text(label) or previs_face.expr_from_text(acting)
        subj = None
        pos = [(label.find(n[0]), w) for w, n in names.items() if n and n[0] and n[0] in label]
        if pos:
            subj = min(pos)[1]
        bact = c.get("act") if isinstance(c.get("act"), dict) else {}   # 立ち位置の設計の始め・終わりの文（previs.py と同じく、ほかで決まらない人の補い）
        act_se = {**previs_face.acting_expr(str(bact.get("end") or ""), names), **previs_face.acting_expr(str(bact.get("start") or ""), names)}
        subj_ex = (key_ex.get(subj) or [None])[0] if subj else None
        subj_ex = subj_ex or (act.get(subj) if subj else None) or (act_se.get(subj) if subj else None)
        flag = bool(mood and subj and not subj_ex and not any(e for _l, e in lines))
        rows.append({"cut": cut, "shot": label, "mood": mood, "subj": subj, "act": act, "keys": {w: v for w, v in key_ex.items() if v},
                     "lines": lines, "flag": flag})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dirs", nargs="+")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    for sd in a.scene_dirs:
        sd = ROOT / sd if not pathlib.Path(sd).is_absolute() else pathlib.Path(sd)
        rows = scene_map(sd)
        flagged = [r for r in rows if r["flag"]]
        print(f"{sd.name}: {len(rows)} カット、気持ちの山なのに主役がふつう {len(flagged)}")
        for r in (rows if a.all else flagged):
            ex = "・".join(f"{w}:{JA[e]}" for w, e in r["act"].items())
            ln = " ".join(f"{l}:{JA.get(e, '-') if e else '-'}" for l, e in r["lines"])
            print(f"  {'!' if r['flag'] else ' '} {r['cut']} {r['shot']}  山={JA.get(r['mood'], '-') if r['mood'] else '-'} 主役={r['subj']}  芝居[{ex}] 台詞[{ln}]"
                  + (f" keys{r['keys']}" if r["keys"] else ""))


if __name__ == "__main__":
    main()
