#!/usr/bin/env python3
"""blocking.yaml の計算（tools/staging.py）を、ショットファイルの blocking に書き込む。静止画の計画（tools/stills_plan.py）が読む。

  python tools/shot_blocking.py episodes/A/scenes/A02

書き込む項目: start・end（誰がどこにどう映るか、日本語）、background、panels（背景に添付する画像の名前）、
staging_en・staging_en_end（指示文に入る英語。芝居は blocking.yaml の pose・pose_end）、sketch（略図）。
ショットファイルのほかの項目は変えない。
"""
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import staging  # noqa: E402

NAME = {"metallica": "メ", "bisco": "ビ", "arlecchino": "ア", "hundred-knight": "百"}


def people_ja(fr):
    return "、".join(f"{staging.SHORT.get(e['who'], NAME.get(e['who'], e['who']))}（{e['side']}・{e['body']}" + (f"・目線 {e['gaze']}" if e.get("gaze") else "") + "）"
                    for e in fr["people"]) or "（人物なし）"


def main():
    st = staging.Stage(sys.argv[1])
    for cut in st.order:
        p = st.dir / "shots" / f"{cut}.yaml"
        if not p.exists():
            continue
        head = "".join(l for l in p.read_text(encoding="utf-8").splitlines(True) if l.startswith("#"))
        shot = yaml.safe_load(p.read_text(encoding="utf-8"))
        fs, fe = st.frame(cut, "start"), st.frame(cut, "end")
        bg = [h for h in (fs["bg"].get(k) for k in ("left", "center", "right")) if h]
        names = list(dict.fromkeys(h["ja"] + (f"〔{h['panel']}〕" if h.get("panel") else "") for h in bg))
        panels = list(dict.fromkeys(h["panel"] for h in bg if h.get("panel")))
        rel = st.dir.relative_to(ROOT)
        shot["blocking"] = {
            "start": people_ja(fs), "end": people_ja(fe), "background": "／".join(names), "panels": panels,
            "staging_en": st.staging_en(cut, fs, "start"), "staging_en_end": st.staging_en(cut, fe, "end"),
            "sketch": [f"{rel}/gen/sketch/{cut}_start.png", f"{rel}/gen/sketch/{cut}_end.png"]}
        p.write_text(head + yaml.safe_dump(shot, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
        print(cut, shot["blocking"]["start"], "→", shot["blocking"]["end"], panels)


if __name__ == "__main__":
    main()
