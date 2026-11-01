#!/usr/bin/env python3
"""下絵の通し（previs/<シーン>/<シーン>_previs_reel.mp4）を作る。D-062 の 3D の段階で、ユーザーが場面ごとに動きと間を確かめる。

  python tools/previs_reel.py episodes/A/scenes/A05b

- カットの順と秒はショットファイル（tc・sec）。下絵の動画（previs/<シーン>/<カット>.mp4）をカットの秒に合わせてつなぐ
  （短ければ最後のコマで止め、長ければ切る）。左上にカットIDと秒を書く。
- 声は voice.yaml の置き場所に原作の台詞（audio/<シーン>/original/<台詞ID>_*.wav）を置く。
- シーンのフォルダに sound.yaml があれば、BGM・効果音・ちょっとした声を重ねる（D-068、2026-11-01）。書き方は sound_items() のコメント。
  footsteps.yaml（tools/footsteps.py が動きから書く足音）も重ねる。
  voice.yaml の bgm（原作の時刻のままの分離音）は使わない。
- ショットファイルの transition でワイプ・暗転を入れ、scene.yaml の comm で通信で話す人物の小窓を出す（D-069）。
- 854x480・24fps・H.264（スマホで見やすい大きさ）。
"""
import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def sound_items(scene_dir, shots):
    """sound.yaml の項目を、シーンの頭からの秒に直して返す。

    sound.yaml:
      bgm: / sfx: / voice:   それぞれ項目の並び。項目の書き方:
        src: audio/…           音のファイル（リポジトリからの道筋）
        cut: A06-002, t: 0.3   置く所（カットの頭からの秒）。at: 12.3 ならシーンの頭からの秒
        until: {cut: A06-007, t: 0.0}  ここで止める（無ければ dur か、音の終わりまで）。dur: 4.0 でも可
        from: 0.0              音の中のどこから鳴らすか（秒）
        loop: true             短い音を繰り返す（BGM のループ、火・風など）
        fade_in / fade_out     秒
        volume: 0.5            音量（1.0 がそのまま）
    """
    docs = [yaml.safe_load(f.read_text(encoding="utf-8")) or {} for f in (scene_dir / "sound.yaml", scene_dir / "footsteps.yaml") if f.exists()]
    if not docs:
        return []
    tc = {s["id"]: float(s["tc"]) for s in shots}
    total = sum(float(s["sec"]) for s in shots)

    def when(v):
        if isinstance(v, (int, float)):
            return float(v)
        return tc[v["cut"]] + float(v.get("t", 0.0))

    out = []
    for doc, kind in ((d, k) for d in docs for k in ("bgm", "sfx", "voice")):
        for it in doc.get(kind) or []:
            at = float(it["at"]) if "at" in it else when(it)
            end = when(it["until"]) if "until" in it else (at + float(it["dur"]) if "dur" in it else None)
            if end is None and it.get("loop"):   # 繰り返す音は、止める所が無ければシーンの終わりまで
                end = total
            if end is not None:
                end = min(end, total)
            out.append({**it, "kind": kind, "at0": at, "end0": end})
    return out


def join_with_transitions(work, shots, parts):
    """カットをつなぐ。ショットファイルの transition（このカットへの入り方、D-069）があれば、そこでワイプか暗転にする。
    transition: {kind: wipe, dir: left, sec: 0.8}   left は右から左へ、right は左から右へ（xfade の wipeleft・wiperight）
    transition: {kind: fade, sec: 0.8}               暗転（場面の最初のカットなら黒からの溶明）
    切り替えはこのカットの頭の sec 秒の間に、前のカットの最後のコマ（止め）から行う。シーンの長さと台詞の時刻は変わらない。"""
    def dur(f):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                             capture_output=True, text=True).stdout
        return float(out)

    # transition のあるカットで区切って、まとまりごとにつなぐ
    groups = []   # (ファイルの並び, 入り方)
    for s, pth in zip(shots, parts):
        if not groups or s.get("transition"):
            groups.append(([pth], s.get("transition")))
        else:
            groups[-1][0].append(pth)
    files = []
    for i, (g, _) in enumerate(groups):
        lst = work / f"g{i}.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in g))
        gf = work / f"g{i}.mp4"
        run("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", gf)
        files.append(gf)
    head = groups[0][1]
    if head and head.get("kind") == "fade":   # 場面の頭は黒から
        f0 = work / "g0f.mp4"
        run("-i", files[0], "-vf", f"fade=t=in:st=0:d={float(head.get('sec', 0.8))}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", f0)
        files[0] = f0
    if len(files) == 1:
        return files[0]
    ins, flt, last, acc = ["-i", files[0]], [], "[0:v]", dur(files[0])
    for i in range(1, len(files)):
        tr = groups[i][1]
        d = float(tr.get("sec", 0.8))
        if tr.get("kind") == "wipe":
            kind = {"left": "wipeleft", "right": "wiperight", "up": "wipeup", "down": "wipedown"}.get(tr.get("dir", "left"), "wipeleft")
        else:
            kind = "fadeblack"
        ins += ["-i", files[i]]
        flt.append(f"{last}tpad=stop_mode=clone:stop_duration={d}[p{i}]")
        flt.append(f"[p{i}][{i}:v]xfade=transition={kind}:duration={d}:offset={acc:.3f}[x{i}]")
        last = f"[x{i}]"
        acc += dur(files[i])
    out = work / "video.mp4"
    run(*ins, "-filter_complex", ";".join(flt), "-map", last, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", out)
    return out


def comm_window(work, scene_dir, shots, video, total):
    """通信で話す人物（メタリカ）の話し始めに、画面の右上に小窓（顔の枠）を出す（D-069）。
    scene.yaml の comm: {who: metallica, image: characters/metallica/views/metallica_face_front.png, sec: 2.5}。
    話し始め = その人物の台詞のうち、すぐ前の台詞が別の人物か、同じ人物の前の台詞から 8秒以上あいたもの。"""
    sy = yaml.safe_load((scene_dir / "scene.yaml").read_text(encoding="utf-8"))
    comm = sy.get("comm")
    vyp = scene_dir / "voice.yaml"
    if not comm or not vyp.exists():
        return video
    scene = shots[0]["scene"]
    who_of = {}
    for sp in (ROOT / "audio" / scene / "original" / "script.yaml",):
        if sp.exists():
            for l in (yaml.safe_load(sp.read_text(encoding="utf-8")) or {}).get("lines", []):
                who_of[l["id"]] = l.get("who")
    lines = (yaml.safe_load(vyp.read_text(encoding="utf-8")) or {}).get("lines", [])
    src_dir = ROOT / "audio" / scene / "original"
    starts, prev_who, prev_end = [], None, -99.0
    for l in lines:
        wav = next(src_dir.glob(f"{l['id']}_*.wav"), None)
        L = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)], capture_output=True, text=True).stdout) if wav else 2.0
        w = who_of.get(l["id"])
        if w == comm["who"] and (prev_who != w or l["at"] - prev_end > 8.0):
            starts.append(float(l["at"]))
        if w == comm["who"]:
            prev_end = l["at"] + L
        prev_who = w
    if not starts:
        return video
    from PIL import Image, ImageDraw
    face = Image.open(ROOT / comm["image"]).convert("RGB")
    W = 220
    face = face.resize((W, int(face.height * W / face.width)))
    pad = 6
    card = Image.new("RGBA", (W + 2 * pad, face.height + 2 * pad), (40, 20, 60, 235))
    card.paste(face, (pad, pad))
    ImageDraw.Draw(card).rectangle([1, 1, card.width - 2, card.height - 2], outline=(230, 190, 90, 255), width=3)
    png = work / "comm.png"
    card.save(png)
    d = float(comm.get("sec", 2.5))
    ins, flt, last = ["-i", video], [], "[0:v]"
    for i, t0 in enumerate(starts, 1):
        ins += ["-loop", "1", "-t", f"{d}", "-i", png]
        flt.append(f"[{i}:v]format=rgba,fade=t=in:st=0:d=0.25:alpha=1,fade=t=out:st={d - 0.35}:d=0.35:alpha=1,setpts=PTS+{t0}/TB[c{i}]")
        flt.append(f"{last}[c{i}]overlay=x=W-w-18:y=18:enable='between(t,{t0},{t0 + d})'[v{i}]")
        last = f"[v{i}]"
    out = work / "video_comm.mp4"
    run(*ins, "-filter_complex", ";".join(flt), "-map", last, "-t", f"{total}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", out)
    print(f"小窓（{comm['who']}）: " + "、".join(f"{t:.1f}秒" for t in starts))
    return out


def run(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], check=True)


def main():
    scene_dir = ROOT / sys.argv[1]
    shots = sorted((yaml.safe_load(p.read_text(encoding="utf-8")) for p in (scene_dir / "shots").glob("*.yaml")), key=lambda s: float(s["tc"]))
    scene = shots[0]["scene"]
    pv = ROOT / "previs" / scene
    work = pathlib.Path(tempfile.mkdtemp(prefix="reel_"))
    parts = []
    font = f":fontfile={FONT}" if pathlib.Path(FONT).exists() else ""
    for s in shots:
        src = pv / f"{s['id']}.mp4"
        if not src.exists():
            sys.exit(f"下絵がない: {src}")
        sec = float(s["sec"])
        out = work / f"{s['id']}.mp4"
        label = f"{s['id']}  {sec:g}秒"
        run("-i", src, "-vf", f"fps=24,scale=854:480,tpad=stop_mode=clone:stop_duration={sec},trim=duration={sec},setpts=PTS-STARTPTS,"
            f"drawtext=text='{label}'{font}:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.5:x=10:y=10",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", out)
        parts.append(out)
    total = sum(float(s["sec"]) for s in shots)
    video = join_with_transitions(work, shots, parts)
    video = comm_window(work, scene_dir, shots, video, total)
    vy = yaml.safe_load((scene_dir / "voice.yaml").read_text(encoding="utf-8")) if (scene_dir / "voice.yaml").exists() else {"lines": []}
    src_dir = ROOT / "audio" / scene / "original"
    ins, flt = [], []
    for k, l in enumerate(vy.get("lines", [])):
        wav = next(src_dir.glob(f"{l['id']}_*.wav"), None)
        if not wav:
            continue
        ins += ["-i", wav]
        flt.append(f"[{sum(1 for x in ins if x == '-i')}]aresample=44100,aformat=channel_layouts=stereo,adelay={round(l['at'] * 1000)}|{round(l['at'] * 1000)}[a{k}]")
    snd = sound_items(scene_dir, shots)
    for k, it in enumerate(snd):
        src = ROOT / it["src"]
        if not src.exists():
            sys.exit(f"音がない: {src}")
        if it.get("loop"):
            ins += ["-stream_loop", "-1"]
        ins += ["-i", src]
        idx = sum(1 for x in ins if x == "-i")
        chain = [f"atrim=start={float(it.get('from', 0.0))}" + (f":duration={it['end0'] - it['at0']:.3f}" if it["end0"] else ""), "asetpts=PTS-STARTPTS",
                 "aresample=44100", "aformat=channel_layouts=stereo"]
        if it.get("fade_in"):
            chain.append(f"afade=t=in:d={it['fade_in']}")
        if it.get("fade_out") and it["end0"]:
            chain.append(f"afade=t=out:st={max(0.0, it['end0'] - it['at0'] - it['fade_out']):.3f}:d={it['fade_out']}")
        chain.append(f"volume={it.get('volume', 1.0)}")
        ms = round(it["at0"] * 1000)
        chain.append(f"adelay={ms}|{ms}")
        flt.append(f"[{idx}]" + ",".join(chain) + f"[s{k}]")
    out = pv / f"{scene}_previs_reel.mp4"
    if ins:
        n = len(flt)
        mix = "".join(f"[a{k}]" for k in range(len(vy["lines"])) if any(f.endswith(f"[a{k}]") for f in flt)) + "".join(f"[s{k}]" for k in range(len(snd)))
        run("-i", video, *ins, "-filter_complex", ";".join(flt) + f";{mix}amix=inputs={n}:normalize=0,alimiter=limit=0.95,apad,atrim=0:{total}[o]",
            "-map", "0:v", "-map", "[o]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", out)
    else:
        run("-i", video, "-c", "copy", out)
    print(f"{out.relative_to(ROOT)}（{len(shots)}カット、{total:.1f}秒" + (f"、音 {len(snd)} 項目" if snd else "") + "）")


if __name__ == "__main__":
    main()
