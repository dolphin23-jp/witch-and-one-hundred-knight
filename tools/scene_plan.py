#!/usr/bin/env python3
"""シーンの生成計画（gen/<シーン>_plan.yaml）を作る。生成はしない（承認を取るための表）。tools/a02_plan.py を場面に依らない形にしたもの。

  python3 tools/scene_plan.py episodes/A/scenes/A04

場面ごとの中身（動画の指示文、終わりの画を作らないカット、口パクの参照、動画のモード）は gen/plan_spec.yaml に書く:
  video: {model: seedance_2_0, mode: fast, resolution: 480p, credits_per_sec: 1}
  tail: 動画の指示文の最後に足す文（絵柄・光・漫符なし）
  no_end: {A04-007: 理由}        終わりの静止画を作らないカット（動画は開始の画だけで作る）
  cels: [A04-xxx]                3人以上が映るカット（人物を1人ずつ作って重ねる。CLAUDE.md）
  speaker: {A04-006: luckiny}    口の見える話し手（その台詞の声を口パクの参照に渡す）
  prompts: {A04-001: "..."}      動画の指示文（芝居とカメラだけ。見た目は書かない）
"""
import math
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import staging  # noqa: E402
import stills_plan as sp  # noqa: E402


def main():
    rel = sys.argv[1].rstrip("/")
    st = staging.Stage(rel)
    D = st.dir
    scene = st.bk["scene"]
    spec = yaml.safe_load((D / "gen" / "plan_spec.yaml").read_text(encoding="utf-8"))
    vid = spec["video"]
    no_end = spec.get("no_end") or {}
    cels_cuts = spec.get("cels") or []
    speaker = spec.get("speaker") or {}
    stills, videos = [], []
    for cut in st.order:
        out = lambda w, n=1: f"{rel}/stills/{w}/{cut}_{w}_gpt-image-2.5_0{n}.png"   # noqa: E731
        if cut in cels_cuts:
            sys.exit(f"{cut}: 3人以上のカットの計画（人物を1人ずつ作って重ねる）はまだこの道具にない。tools/a02_plan.py を参照")
        it = sp.build(st, cut, "start")
        it["output"] = out("start")
        stills.append(it)
        if cut in no_end:
            stills.append({"cut": cut, "which": "end", "skip": no_end[cut]})
            continue
        it2 = sp.build(st, cut, "end")
        sk_end = f"{rel}/gen/sketch/{cut}_end.png"
        if not (D / "gen" / "sketch" / f"{cut}_end.png").exists():   # 位置もカメラも変わらないカットは開始の略図を使う
            it2["refs"] = [f"{rel}/gen/sketch/{cut}_start.png" if r == sk_end else r for r in it2["refs"]]
        it2["refs"].append(out("start"))
        it2["prompt"] = it2["prompt"].replace(" Keep each character", f" Image {len(it2['refs'])} is the first frame of the same shot (made first): keep its background rendering, light and the way the characters are drawn. Keep each character")
        it2["output"] = out("end")
        stills.append(it2)
    for cut in st.order:
        shot = yaml.safe_load((D / "shots" / f"{cut}.yaml").read_text(encoding="utf-8"))
        dur = min(15, max(4, math.ceil(float(shot["sec"]) - 0.05)))
        lines = [d.split("「")[0] for d in (shot.get("dialogue") or [])]
        sv = speaker.get(cut)
        end = None if cut in no_end else f"{rel}/stills/end/{cut}_end_gpt-image-2.5_01.png"
        who = sorted({e["who"] for w in ("start", "end") for e in st.frame(cut, w)["people"]})
        for n in (st.bk["cuts"][cut].get("creatures") or {}):   # 生き物（D-060）: 動画の指示文に出てくるときは設定画も添える
            if n.replace("-", " ") in spec["prompts"][cut].replace("-", " "):
                who.append(n)
        sv_lines = [l for l in lines if list((ROOT / "audio" / scene / "original").glob(f"{l}_{sv}.wav"))] if sv else []
        v = {"cut": cut, "sec": shot["sec"], "duration": dur, "credits": dur * vid["credits_per_sec"],
             "start_image": f"{rel}/stills/start/{cut}_start_gpt-image-2.5_01.png", "end_image": end,
             "image_references": [f"refs/{w}_sheet_01.{'jpg' if (ROOT / 'refs' / f'{w}_sheet_01.jpg').exists() else 'png'}" for w in who],
             "previs": f"previs/{scene}/{cut}.mp4",
             "audio_reference": ([f"audio/{scene}/original/{l}_{sv}.wav" for l in sv_lines] if sv_lines else None),   # 複数のときは voice.yaml の間でつないだ1本の mp3 にして渡す
             "prompt": spec["prompts"][cut] + (" Sync the speaking character's mouth to the line in audio 1 whenever the face is visible." if sv_lines else "")
             + " The shot ends on the given last frame." * (end is not None) + " " + spec["tail"]}
        videos.append(v)
    cs = len([s for s in stills if not s.get("skip")])
    cv = sum(v["credits"] for v in videos)
    plan = {"status": "承認待ち（生成なし・クレジットなし）",
            "scene": f"{scene}。{len(st.order)}カット {sum(float(yaml.safe_load((D / 'shots' / f'{c}.yaml').read_text(encoding='utf-8'))['sec']) for c in st.order):.1f}秒",
            "credits_estimate": {"stills": cs, "videos": cv, "total": cs + cv},
            "order": "区間ごとに、静止画（開始→終わり）→ Claude が点検 → 動画。崩れた静止画の動画は作らずに報告。失敗しても自動では作り直さない",
            "stills": {"model": "gpt_image_2_5", "quality": "medium", "resolution": "2k", "aspect_ratio": "16:9", "note": "1枚1クレジット。終わりは開始の画を添えて作る", "items": stills},
            "videos": {**vid, "generate_audio": False, "note": spec.get("video_note", ""), "items": videos}}
    p = D / "gen" / f"{scene}_plan.yaml"
    p.write_text(f"# {scene} の生成計画（承認を取るための表。tools/scene_plan.py が gen/plan_spec.yaml と blocking から作る）\n"
                 + yaml.safe_dump(plan, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    for v in videos:
        print(v["cut"], v["sec"], v["duration"], v["credits"], v["audio_reference"] or "")
    print("stills", cs, "videos", cv, "total", cs + cv, p)


if __name__ == "__main__":
    main()
