"""A05b_plan.yaml の後処理（tools/scene_plan.py の後に流す: python3 tools/a05b_plan_post.py）。2026-11-27、生成の前の点検の直しを入れるために作った。
- 静止画: plan_spec.yaml の stills（カット/start|end ごとの参照と指示文）で差し替える。構図の参照は下絵の最初と最後のコマ（gen/sketch/*_previs.png）
- 動画: 指示文を plan_spec の prompts＋口パクの文（lip）＋終わりの画の文＋末尾（tails か tail）で組み直す。人物の参照は正面の切り抜き
- 口パクの mp3（カットの長さ、台詞をカット内の時刻に置く）と、021 の下絵を 15.0 秒（360 コマ）に縮めた版を tmp/hf/a05b/ に作る
"""
import pathlib
import re
import subprocess

import yaml

ROOT = pathlib.Path("/home/user/witch-and-one-hundred-knight")
SC = "A05b"
D = ROOT / f"episodes/A/scenes/{SC}"
TMP = ROOT / "tmp/hf/a05b"


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def frame(mp4, which, dst):
    if which == "start":
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-frames:v", "1", str(dst)]
    else:
        cmd = ["ffmpeg", "-v", "error", "-y", "-sseof", "-0.3", "-i", str(mp4), "-update", "1", str(dst)]
    subprocess.run(cmd, check=True)


def fill(text, frag):
    def rep(m):
        key, _, arg = m.group(1).partition(":")
        if key in ("COMP", "COMPX"):
            return " ".join(frag[key].replace("{n}", arg).split())
        if key == "FIRST":
            return frag["FIRST"].replace("{n}", arg)
        if key == "BG":
            ns = arg.split(",")
            imgs = ("image " if len(ns) == 1 else "images ") + ", ".join(ns)
            return frag["BG"].replace("{imgs}", imgs).replace("{verb}", "shows" if len(ns) == 1 else "show").replace("{its}", "its" if len(ns) == 1 else "their")
        return frag[key].strip()
    out = re.sub(r"\{([A-Z_]+(?::[0-9,]+)?)\}", rep, text)
    assert "{" not in out, out
    return " ".join(out.split())


def main():
    spec = yaml.safe_load((D / "gen" / "plan_spec.yaml").read_text(encoding="utf-8"))
    frag = spec["fragments"]
    p = D / "gen" / f"{SC}_plan.yaml"
    raw = p.read_text(encoding="utf-8")
    head = "".join(l for l in raw.splitlines(True) if l.startswith("#"))
    plan = yaml.safe_load(raw)
    for it in plan["stills"]["items"]:
        if it.get("skip"):
            continue
        cut, which = it["cut"], it["which"]
        frame(ROOT / f"previs/{SC}/{cut}.mp4", which, D / "gen" / "sketch" / f"{cut}_{which}_previs.png")
        ov = spec["stills"][f"{cut}/{which}"]
        start = f"episodes/A/scenes/{SC}/stills/start/{cut}_start_gpt-image-2.5_01.png"
        it["refs"] = [start if r == "{START}" else r for r in ov["refs"]]
        it["prompt"] = fill(ov["prompt"], frag)
        n_used = max(int(x) for x in re.findall(r"images? ((?:\d+, )*\d+)", it["prompt"]) for x in x.split(", "))
        assert n_used <= len(it["refs"]), (cut, which, n_used, len(it["refs"]))
        for r in it["refs"]:
            assert r == start or (ROOT / r).exists(), r
    shots = {c.stem: yaml.safe_load(c.read_text(encoding="utf-8")) for c in (D / "shots").glob("*.yaml")}
    voice = {l["id"]: float(l["at"]) for l in yaml.safe_load((D / "voice.yaml").read_text(encoding="utf-8"))["lines"]}
    for v in plan["videos"]["items"]:
        cut = v["cut"]
        sh = shots[cut]
        who = [pathlib.Path(r).name.split("_sheet_01")[0].split("_front")[0] for r in v["image_references"]]   # 何度流しても同じ（scene_plan.py の sheet か、後処理後の front）
        v["image_references"] = [f"characters/{w}/views/{w}_front.png" for w in who]
        for r in v["image_references"]:
            assert (ROOT / r).exists(), r
        if v.get("audio_reference"):   # カットの長さ（生成の秒）の mp3 に、台詞をカット内の時刻で置く
            out = TMP / "audio" / f"{cut}_lines.mp3"
            out.parent.mkdir(parents=True, exist_ok=True)
            ins, flt = [], []
            for i, wav in enumerate(v["audio_reference"]):
                lid = pathlib.Path(wav).name.split("_")[0]
                off = max(0.0, voice[lid] - float(sh["tc"]))
                ins += ["-i", str(ROOT / wav)]
                flt.append(f"[{i}:a]adelay={int(round(off * 1000))}:all=1[a{i}]")
            mix = "".join(f"[a{i}]" for i in range(len(v["audio_reference"])))
            flt.append(f"{mix}amix=inputs={len(v['audio_reference'])}:normalize=0,apad=whole_dur={v['duration']},atrim=0:{v['duration']}[o]")
            subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(flt), "-map", "[o]", "-ac", "1", "-ar", "44100", "-b:a", "192k", str(out)], check=True)
            v["audio_reference_mp3"] = str(out.relative_to(ROOT))
            v["audio_note"] = "原作の台詞を voice.yaml の at − カットの tc の時刻に置き、カットの生成の秒の長さの無音に重ねた mp3（mono 44.1kHz 192k）"
        src = ROOT / v["previs"]
        d = dur(src)
        if d > 15.0:   # Seedance の最長 15 秒。下絵を縮めて 15 秒で作り、組み立てで元の長さに伸ばす
            out = TMP / "previs" / f"{cut}_15s.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            k = 15.0 / d
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", f"setpts={k:.6f}*PTS", "-r", "24", "-frames:v", "360", "-an", str(out)], check=True)
            v["previs_sent"] = str(out.relative_to(ROOT))
            v["retime"] = (f"下絵 {d:.2f}秒 → 15.0秒（360 コマ、ffmpeg setpts={k:.6f}*PTS -r 24 -frames:v 360）。"
                           f"組み立てで {float(sh['sec']):.1f}秒に伸ばす（setpts=PTS*{1 / k:.4f}）")
        end = v.get("end_image")
        lip = spec.get("lip", {}).get(cut) if v.get("audio_reference") else None
        v["prompt"] = " ".join(x for x in (spec["prompts"][cut].strip(), lip, "The shot ends on the given last frame." if end else None,
                                           " ".join((spec.get("tails", {}).get(cut) or spec["tail"]).split())) if x)
        v["output"] = f"episodes/A/scenes/{SC}/video/{cut}_seedance_2_0_480p_01.mp4"
        v["via"] = "higgsfield"
        v["aspect_ratio"] = "16:9"
    vids = plan["videos"]
    vids["send"] = ("1回目 013〜018 の6本、2回目 019〜021 の3本（同時に動かせるのは6本まで。7本目は 429 で受け付けられず課金なし）。"
                    "開始・終わりの画・人物の切り抜き・下絵・mp3 は media_upload で上げたファイルの ID を渡す。mode=fast、resolution=480p、aspect_ratio=16:9、generate_audio=false。"
                    "プリセットの推薦（IN THE DARK など）で止まったら declined_preset_id を付けて同じ内容で出し直す（ジョブも課金も作られていない）。送る前に estimate_video_cost で 1秒1クレジットかを確かめる")
    plan["credits_estimate"]["note"] = ("作り直しは承認を取り直す（自動では作らない）。枠の目安 +15〜25（長いカット1本と静止画数枚）、上限の目安 約130。"
                                        "残高 1,200（2026-11-27、Plus）")
    plan["note"] = "生成には、この計画の prompt と refs だけを使う（ショットファイルの staging_en は前の文のまま残ることがある）"
    p.write_text(head + yaml.safe_dump(plan, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    print("ok", p)


if __name__ == "__main__":
    main()
