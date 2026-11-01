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


# 音のつり合い（D-068、2026-11-01 ユーザー「足音がまだ大きい。効果音のバランスをよく気にして」）。
# 素材ごとの大きさの違いをならしてから volume を掛ける。volume 1.0 は「台詞と同じ大きさ」:
#   単発の効果音（loop でない sfx）は、いちばん大きい所（peak）を台詞の山 REF_PEAK に合わせる
#   BGM と、鳴り続ける効果音（loop の sfx）は、平均（mean）を台詞の平均 REF_MEAN に合わせる
#   voice（原作の声）はそのまま
# 目安: BGM 0.28（台詞より約 11dB 小さい）、環境音 0.15〜0.2、足音 0.15〜0.22、扉などの音 0.4〜0.6
REF_PEAK, REF_MEAN = -15.0, -28.5
_LEVELS = {}


def levels(path):
    if path not in _LEVELS:
        out = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
        mean = float(out.split("mean_volume:")[1].split("dB")[0])
        peak = float(out.split("max_volume:")[1].split("dB")[0])
        _LEVELS[path] = (mean, peak)
    return _LEVELS[path]


def norm_gain(kind, it, path):
    """素材の大きさをならす係数（dB）。"""
    if kind == "voice":
        return 0.0
    mean, peak = levels(path)
    if kind == "bgm" or it.get("loop"):
        return REF_MEAN - mean
    return REF_PEAK - peak


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
        rate: 0.7              音の高さと速さ（0.7 で低く遅く。生き物の声を大きく聞かせる）
        echo: cave             響き（cave: 地下空洞の残響）。false で切る
      echo: {from: {cut: A07-005, t: 0.0}, kind: cave}   シーンの頭に書くと、その時刻より後の効果音（足音も）に響きを付ける（A07、2026-11-01）
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

    echo = docs[0].get("echo") if (scene_dir / "sound.yaml").exists() else None
    echo_t = when(echo["from"]) if echo else None
    out = []
    for doc, kind in ((d, k) for d in docs for k in ("bgm", "sfx", "voice")):
        for it in doc.get(kind) or []:
            at = float(it["at"]) if "at" in it else when(it)
            if echo and kind != "bgm" and "echo" not in it and at >= echo_t:
                it = {**it, "echo": echo.get("kind", "cave")}
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


def comm_starts(scene_dir, shots):
    """通信で話す人物（scene.yaml の comm.who）の話し始めの秒の並び。
    話し始め = その人物の台詞のうち、すぐ前の台詞が別の人物か、同じ人物の前の台詞から 8秒以上あいたもの。
    comm.cuts（カットIDの並び）があれば、そのカットの中の最初の台詞だけ（ずっと話し続ける場面で、場所が変わった所にも出す）。"""
    sy = yaml.safe_load((scene_dir / "scene.yaml").read_text(encoding="utf-8"))
    comm = sy.get("comm")
    vyp = scene_dir / "voice.yaml"
    if not comm or not vyp.exists():
        return None, []
    scene = shots[0]["scene"]
    who_of = {}
    sp = ROOT / "audio" / scene / "original" / "script.yaml"
    if sp.exists():
        for l in (yaml.safe_load(sp.read_text(encoding="utf-8")) or {}).get("lines", []):
            who_of[l["id"]] = l.get("who")
    lines = (yaml.safe_load(vyp.read_text(encoding="utf-8")) or {}).get("lines", [])
    src_dir = ROOT / "audio" / scene / "original"
    if comm.get("cuts"):   # 出すカットを決めてあるとき（A07、2026-11-01）: そのカットの中の、その人物の最初の台詞の少し前
        spans = {s["id"]: (float(s["tc"]), float(s["tc"]) + float(s["sec"])) for s in shots}
        starts = []
        for cut in comm["cuts"]:
            a, b = spans[cut]
            first = next((l for l in lines if a <= float(l["at"]) < b and who_of.get(l["id"]) == comm["who"]), None)
            if first:
                starts.append(max(0.0, float(first["at"]) - 0.35))
        return comm, starts
    starts, prev_who, prev_end = [], None, -99.0
    for l in lines:
        wav = next(src_dir.glob(f"{l['id']}_*.wav"), None)
        L = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
                                 capture_output=True, text=True).stdout) if wav else 2.0
        w = who_of.get(l["id"])
        if w == comm["who"] and (prev_who != w or l["at"] - prev_end > 8.0):
            starts.append(max(0.0, float(l["at"]) - 0.35))   # ノイズの分だけ声より少し前から
        if w == comm["who"]:
            prev_end = l["at"] + L
        prev_who = w
    return comm, starts


def comm_frames(work, comm, fps=24):
    """小窓の動き（D-069、2026-11-01 ユーザー「無線のようにザザっとノイズが走って、ブラウン管テレビの電源がつくように出る」）。
    0〜0.15秒: 枠の大きさの砂嵐 → 0.15〜0.40秒: 真ん中の白い横線が上下に開いて顔が出る（ノイズが残って消えていく）
    → 表示（ときどき横に走るノイズの帯と、うすい走査線）→ 最後の 0.35秒: 上下に閉じて横線 → 点 → 消える。
    PNG の連番（透明つき）を返す。"""
    import random
    from PIL import Image, ImageDraw
    rnd = random.Random(3)
    face = Image.open(ROOT / comm["image"]).convert("RGB")
    W = 220
    face = face.resize((W, int(face.height * W / face.width)))
    pad = 6
    CW, CH = W + 2 * pad, face.height + 2 * pad
    card = Image.new("RGBA", (CW, CH), (40, 20, 60, 235))
    card.paste(face, (pad, pad))
    sl = ImageDraw.Draw(card)
    for y in range(pad, CH - pad, 3):   # うすい走査線
        sl.line([(pad, y), (CW - pad, y)], fill=(0, 0, 0, 40))
    ImageDraw.Draw(card).rectangle([1, 1, CW - 2, CH - 2], outline=(230, 190, 90, 255), width=3)

    def noise(w, h, alpha=255):
        im = Image.effect_noise((w, h), 90).convert("L").point(lambda v: min(255, int(v * 1.3)))
        return Image.merge("RGBA", (im, im, im, Image.new("L", (w, h), alpha)))

    d = float(comm.get("sec", 2.5))
    n = int(round(d * fps))
    fdir = work / "comm"
    fdir.mkdir(exist_ok=True)
    for k in range(n):
        t = k / fps
        fr = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        if t < 0.15:                                   # 砂嵐
            fr.paste(noise(CW, CH, 200), (0, 0))
        elif t < 0.40:                                 # 横線から上下に開く
            u = (t - 0.15) / 0.25
            h = max(3, int(CH * u))
            y0 = (CH - h) // 2
            fr.paste(card.crop((0, y0, CW, y0 + h)), (0, y0))
            nz = noise(CW, h, int(170 * (1 - u)))
            fr.alpha_composite(nz, (0, y0))
            ImageDraw.Draw(fr).rectangle([0, y0, CW - 1, y0 + 1], fill=(255, 255, 255, 230))
            ImageDraw.Draw(fr).rectangle([0, y0 + h - 2, CW - 1, y0 + h - 1], fill=(255, 255, 255, 230))
        elif t < d - 0.35:                             # 表示中。ときどき横に走るノイズの帯
            fr.paste(card, (0, 0))
            if rnd.random() < 0.18:
                bh = rnd.randint(4, 14)
                by = rnd.randint(pad, CH - pad - bh)
                fr.alpha_composite(noise(CW - 2 * pad, bh, 150), (pad, by))
        else:                                          # 上下に閉じて横線 → 点 → 消える
            u = (t - (d - 0.35)) / 0.35
            if u < 0.6:
                h = max(3, int(CH * (1 - u / 0.6)))
                y0 = (CH - h) // 2
                fr.paste(card.crop((0, y0, CW, y0 + h)).resize((CW, h)), (0, y0))
                ImageDraw.Draw(fr).rectangle([0, CH // 2 - 1, CW - 1, CH // 2 + 1], fill=(255, 255, 255, 220))
            else:
                v = (u - 0.6) / 0.4
                w = max(2, int(CW * (1 - v)))
                ImageDraw.Draw(fr).rectangle([(CW - w) // 2, CH // 2 - 1, (CW + w) // 2, CH // 2 + 1], fill=(255, 255, 255, int(230 * (1 - v))))
        fr.save(fdir / f"{k:03d}.png")
    return fdir


def comm_window(work, scene_dir, shots, video, total):
    """通信で話す人物（メタリカ）の話し始めに、画面の右上へ小窓を出す（D-069）。scene.yaml の
    comm: {who: metallica, image: characters/metallica/views/metallica_face_front.png, sec: 2.5}。出方は comm_frames()。"""
    comm, starts = comm_starts(scene_dir, shots)
    if not starts:
        return video
    fdir = comm_frames(work, comm)
    ins, flt, last = ["-i", video], [], "[0:v]"
    for i, t0 in enumerate(starts, 1):
        ins += ["-framerate", "24", "-i", str(fdir / "%03d.png")]
        flt.append(f"[{i}:v]format=rgba,setpts=PTS+{t0}/TB[c{i}]")
        flt.append(f"{last}[c{i}]overlay=x=W-w-18:y=18:eof_action=pass[v{i}]")
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
    comm, cstarts = comm_starts(scene_dir, shots)   # 通信の小窓のノイズの音（D-069）
    for t0 in cstarts:
        d = float(comm.get("sec", 2.5))
        snd.append({"src": "audio/sfx/radio_static_on.ogg", "kind": "sfx", "at0": t0, "end0": t0 + 0.45, "volume": 0.3})
        snd.append({"src": "audio/sfx/radio_static_off.ogg", "kind": "sfx", "at0": t0 + d - 0.3, "end0": t0 + d - 0.05, "volume": 0.2})
    for k, it in enumerate(snd):
        src = ROOT / it["src"]
        if not src.exists():
            sys.exit(f"音がない: {src}")
        if it.get("loop"):
            ins += ["-stream_loop", "-1"]
        ins += ["-i", src]
        idx = sum(1 for x in ins if x == "-i")
        rate = float(it.get("rate", 1.0))
        chain = [f"atrim=start={float(it.get('from', 0.0))}" + (f":duration={(it['end0'] - it['at0']) * rate:.3f}" if it["end0"] else ""), "asetpts=PTS-STARTPTS",
                 "aresample=44100", "aformat=channel_layouts=stereo"]
        if rate != 1.0:   # 低く遅く（長さも 1/rate 倍になる）
            chain += [f"asetrate={round(44100 * rate)}", "aresample=44100"]
        if it.get("echo") in ("cave", True):   # 地下空洞の響き（短い反射を重ねる）
            chain.append("aecho=0.85:0.7:70|150|260|410:0.32|0.22|0.14|0.08")
        if it.get("fade_in"):
            chain.append(f"afade=t=in:d={it['fade_in']}")
        if it.get("fade_out") and it["end0"]:
            chain.append(f"afade=t=out:st={max(0.0, it['end0'] - it['at0'] - it['fade_out']):.3f}:d={it['fade_out']}")
        g = norm_gain(it["kind"], it, src)
        chain.append(f"volume={it.get('volume', 1.0) * 10 ** (g / 20):.4f}")
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
