"""ショットファイルから Animatic を組み立てる（仮の組み立て。ffmpeg で直接書き出す）。

  python tools/animatic.py episodes/A/scenes/A01            # animatic/out/A01.mp4 と A01_timeline.yaml
  python tools/animatic.py episodes/A/scenes/A01 --bgm audio/A01/bgm.wav

シーンに voice.yaml があるときは、原作の台詞（D-045）で組む（build_original）。
  声: audio/<シーン>/original/<台詞ID>_<人物>.wav を voice.yaml の秒に置く（息継ぎを広げる split も）。
  画: ショットファイルの tc で切り替え。image があれば静止画（K はゆっくり寄る）、なければ種別（S/K/M/A）で色を分けた仮の画面に画・動き・音を書く。
以下は voice.yaml がないとき（第2稿の AI の声）の組み立て。

- 画: ベイクオフで作った動画 > 静止画 > 仮の画面（カットID・画・芝居の文字）の順に使う。
- 声: audio/<シーン>/<カットID>_<番号>_<人物>.wav を、カットの頭から順に置く。
- 尺: max(ショットファイルの sec, 声の長さ + 前後の間)。声で伸びたカットは timeline に記録する。
  ショットファイルの timing.lead（声の前の間、秒）があれば使う。
- 字幕: 台詞を画面下に出す（確認用）。
- BGM: --bgm を渡すと、声の下に小さく敷く。music: off のカットは BGM を下げる。

HyperFrames か Remotion かは、静止画と漫符がそろった段階で試して決める（制作パイプライン）。
それまでの尺と間の確認用。
"""
import argparse
import pathlib
import subprocess
import sys
import tempfile
import textwrap

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
W, H, FPS = 1280, 720, 24
LEAD, TAIL, GAP = 0.3, 0.5, 0.25   # 声の前、声の後、台詞と台詞の間（秒）
NAMES = {"metallica": "メタリカ", "bisco": "ビスコ", "arlecchino": "アルレッキーノ", "hundred-knight": "百騎兵"}


def dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def text_file(tmp, name, text):
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return str(p).replace(":", "\\:")


def visual(shot):
    """使える画を探す: ベイクオフの動画 > 静止画 > なし"""
    b = shot.get("bakeoff_result") or {}
    for key in ("video", "image"):
        if b.get(key) and (ROOT / b[key]).exists():
            return key, ROOT / b[key]
    return None, None


def build(scene_dir, bgm, out_dir):
    scene_dir = ROOT / scene_dir
    shots = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((scene_dir / "shots").glob("*.yaml"))]
    scene = shots[0]["scene"]
    audio_dir = ROOT / "audio" / scene
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp())

    t = 0.0
    timeline, voices, segs = [], [], []
    for s in shots:
        lead = (s.get("timing") or {}).get("lead", LEAD)
        lines, cursor = [], lead
        for i, d in enumerate(s.get("dialogue") or [], 1):
            wav = audio_dir / f"{s['id']}_{i}_{d['who']}.wav"
            if not wav.exists():
                sys.exit(f"声がありません: {wav.relative_to(ROOT)}")
            L = dur(wav)
            lines.append({"who": d["who"], "line": d["line"], "start": round(cursor, 2), "end": round(cursor + L, 2)})
            voices.append((t + cursor, wav))
            cursor += L + GAP
        voice_need = (cursor - GAP + TAIL) if lines else 0
        sec = round(max(float(s["sec"]), voice_need), 2)
        timeline.append({"id": s["id"], "start": round(t, 2), "sec": sec, "planned": s["sec"],
                         "stretched": sec > float(s["sec"]), "music": "off" if s.get("music", "on") in ("off", False) else "on",
                         "lines": lines})

        # 画
        kind, src = visual(s)
        seg = tmp / f"{s['id']}.mp4"
        draws = [f"drawtext=fontfile={FONT}:text='{esc(s['id'])}':x=20:y=16:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=6"]
        if kind is None:
            body = "\n".join(textwrap.wrap(f"画: {s['shot']}", 34) + [""] + textwrap.wrap(f"芝居: {s['acting']}", 34))
            draws.append(f"drawtext=fontfile={FONT}:textfile='{text_file(tmp, s['id'] + '_b.txt', body)}':x=80:y=120:fontsize=30:line_spacing=12:fontcolor=0xdddddd")
        sid = s["id"]
        for n, l in enumerate(lines):
            sub = f"{NAMES.get(l['who'], l['who'])}「{l['line']}」"
            sub = "\n".join(textwrap.wrap(sub, 30))
            draws.append(f"drawtext=fontfile={FONT}:textfile='{text_file(tmp, sid + f'_{n}.txt', sub)}'"
                         f":x=(w-text_w)/2:y=h-text_h-40:fontsize=32:line_spacing=8:fontcolor=white:borderw=3:bordercolor=black"
                         f":enable='between(t,{l['start']},{l['end'] + 0.2})'")
        vf = ",".join([f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}",
                       f"tpad=stop_mode=clone:stop_duration={sec}"] + draws)
        if kind == "video":
            ff("-i", src, "-vf", vf, "-t", sec, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", seg)
        elif kind == "image":
            ff("-loop", "1", "-i", src, "-vf", vf, "-t", sec, "-c:v", "libx264", "-pix_fmt", "yuv420p", seg)
        else:
            ff("-f", "lavfi", "-i", f"color=c=0x1d2b24:s={W}x{H}:r={FPS}", "-vf", ",".join(draws), "-t", sec,
               "-c:v", "libx264", "-pix_fmt", "yuv420p", seg)
        segs.append(seg)
        t += sec

    total = round(t, 2)
    # 画をつなぐ
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in segs))
    video = tmp / "video.mp4"
    ff("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video)

    # 声を並べる（amix の入力が多いので 1 本ずつ adelay して重ねる）
    inputs, filt = [], []
    for k, (start, wav) in enumerate(voices):
        inputs += ["-i", wav]
        ms = int(start * 1000)
        filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{k}]")
    mix = "".join(f"[v{k}]" for k in range(len(voices)))
    filt.append(f"{mix}amix=inputs={len(voices)}:normalize=0,apad=whole_dur={total}[voice]")
    last = "[voice]"
    if bgm:
        n = len(voices)
        inputs += ["-stream_loop", "-1", "-i", ROOT / bgm]
        # music: off のカットで BGM を下げる
        offs = "+".join(f"between(t,{x['start']},{x['start'] + x['sec']})" for x in timeline if x["music"] in ("off", False)) or "0"
        filt.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total},"
                    f"volume='if({offs},0.03,0.22)':eval=frame[bgm]")
        filt.append("[voice][bgm]amix=inputs=2:normalize=0:duration=first[mix]")
        last = "[mix]"
    audio = tmp / "audio.m4a"
    ff(*inputs, "-filter_complex", ";".join(filt), "-map", last, "-t", total, "-c:a", "aac", "-b:a", "160k", audio)

    out = out_dir / f"{scene}.mp4"
    ff("-i", video, "-i", audio, "-map", "0:v", "-map", "1:a", "-c", "copy", "-shortest", out)
    tl = out_dir / f"{scene}_timeline.yaml"
    tl.write_text(yaml.safe_dump({"scene": scene, "total_sec": total, "cuts": timeline},
                                 allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"{out.relative_to(ROOT)}（{total}秒、{len(shots)}カット、声で伸びたカット {sum(x['stretched'] for x in timeline)}）")
    print(tl.relative_to(ROOT))


KIND = {"S": ("止め", "0x2a2f36"), "K": ("止め＋カメラ", "0x223040"), "M": ("小さな動き", "0x1d3326"), "A": ("見せ場", "0x5a3414")}


def build_original(scene_dir, out_dir, name=None):
    """原作の台詞（D-045）を voice.yaml の置き場所に並べ、ショットファイルの tc と sec で画面を切り替える。

    voice.yaml: lines: [{id: L01, at: 12.0, split: {at: 1.65, gap: 1.2}}, ...]
      at    = シーンの頭からの秒。split = 台詞の中の息継ぎを広げる位置（音声ファイル内の秒）と足す間（秒）
    声は audio/<シーン>/original/<id>_<人物>.wav。画面はショットの画・動き・音を書いた仮の画面（種別で色を分ける）。
    """
    scene_dir = ROOT / scene_dir
    vspec = yaml.safe_load((scene_dir / "voice.yaml").read_text(encoding="utf-8"))
    scene = vspec["scene"]
    shots = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((scene_dir / "shots").glob("*.yaml"))]
    shots.sort(key=lambda s: s["tc"])
    total = float(vspec["total_sec"])
    src_dir = ROOT / "audio" / scene / "original"
    script = {l["id"]: l for l in yaml.safe_load((src_dir / "script.yaml").read_text(encoding="utf-8"))["lines"]}
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp())

    # 声の置き場所
    pieces, subs = [], []
    for v in vspec["lines"]:
        wav = next(src_dir.glob(f"{v['id']}_*.wav"))
        L = dur(wav)
        sp = v.get("split")
        if sp:
            a, b = tmp / f"{v['id']}_a.wav", tmp / f"{v['id']}_b.wav"
            ff("-i", wav, "-t", sp["at"], a)
            ff("-ss", sp["at"], "-i", wav, b)
            pieces += [(v["at"], a), (v["at"] + sp["at"] + sp["gap"], b)]
            end = v["at"] + L + sp["gap"]
        else:
            pieces.append((v["at"], wav))
            end = v["at"] + L
        who = NAMES.get(script[v["id"]]["who"], script[v["id"]]["who"])
        subs.append((v["at"], end, f"{v['id']} {who}「{script[v['id']]['text']}」"))

    # 画面（カットごと）
    segs, timeline = [], []
    for i, s in enumerate(shots):
        start = float(s["tc"])
        nxt = float(shots[i + 1]["tc"]) if i + 1 < len(shots) else total
        sec = round(nxt - start, 2)
        label, color = KIND.get(s.get("kind"), ("", "0x1d2b24"))
        head = f"{s['id']}   {s.get('kind', '')} {label}   {sec:.1f}秒"
        body = "\n".join(textwrap.wrap(f"画: {s['shot']}", 36) + [""] + textwrap.wrap(f"動き: {s['acting']}", 36))
        foot = "\n".join(textwrap.wrap(" ／ ".join(x for x in [s.get("sound"), s.get("notes")] if x), 44))
        sid = s["id"]
        draws = [f"drawtext=fontfile={FONT}:textfile='{text_file(tmp, sid + '_h.txt', head)}':x=40:y=28:fontsize=34:fontcolor=white",
                 f"drawtext=fontfile={FONT}:textfile='{text_file(tmp, sid + '_b.txt', body)}':x=60:y=100:fontsize=29:line_spacing=10:fontcolor=0xe8e8e8"]
        if foot:
            draws.append(f"drawtext=fontfile={FONT}:textfile='{text_file(tmp, sid + '_f.txt', foot)}':x=60:y=h-250:fontsize=24:line_spacing=6:fontcolor=0xf0c878")
        seg = tmp / f"{sid}.mp4"
        img = ROOT / s["image"] if s.get("image") else None
        if img and img.exists():
            # 静止画があれば使う。K（止め＋カメラ）はゆっくり寄る。カットIDと種別を左上に小さく出す
            tag = f"drawtext=fontfile={FONT}:textfile='{text_file(tmp, sid + '_t.txt', head)}':x=20:y=18:fontsize=22" \
                  f":fontcolor=white:box=1:boxcolor={color}@0.85:boxborderw=8"
            if s.get("kind") == "K":
                n = max(1, int(sec * FPS))
                move = f"scale={W * 2}:{H * 2},zoompan=z='1+0.08*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={FPS}"
            else:
                move = f"scale={W}:{H},fps={FPS}"
            ff("-loop", "1", "-i", img, "-vf", f"{move},{tag}", "-t", sec, "-c:v", "libx264", "-pix_fmt", "yuv420p", seg)
        else:
            ff("-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:r={FPS}", "-vf", ",".join(draws), "-t", sec,
               "-c:v", "libx264", "-pix_fmt", "yuv420p", seg)
        segs.append(seg)
        timeline.append({"id": sid, "kind": s.get("kind"), "start": start, "sec": sec,
                         "image": s.get("image") if img and img.exists() else None})

    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in segs))
    video = tmp / "video.mp4"
    ff("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video)

    # 字幕とタイムコードを全体に重ねる
    draws = [f"drawtext=fontfile={FONT}:text='%{{pts\\:hms}}':x=w-tw-30:y=30:fontsize=26:fontcolor=white@0.8"]
    for k, (a, b, text) in enumerate(subs):
        t = "\n".join(text[i:i + 34] for i in range(0, len(text), 34))   # 空白で折らない
        draws.append(f"drawtext=fontfile={FONT}:textfile='{text_file(tmp, f'sub{k}.txt', t)}':x=(w-text_w)/2:y=h-text_h-36"
                     f":fontsize=30:line_spacing=6:fontcolor=white:borderw=3:bordercolor=black:enable='between(t,{a:.2f},{b + 0.15:.2f})'")
    video2 = tmp / "video2.mp4"
    ff("-i", video, "-vf", ",".join(draws), "-c:v", "libx264", "-pix_fmt", "yuv420p", video2)

    inputs, filt = [], []
    for k, (start, wav) in enumerate(pieces):
        inputs += ["-i", wav]
        ms = int(start * 1000)
        filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{k}]")
    filt.append("".join(f"[v{k}]" for k in range(len(pieces))) + f"amix=inputs={len(pieces)}:normalize=0,apad=whole_dur={total}[voice]")
    audio = tmp / "audio.m4a"
    ff(*inputs, "-filter_complex", ";".join(filt), "-map", "[voice]", "-t", total, "-c:a", "aac", "-b:a", "160k", audio)

    out = out_dir / f"{name or scene}.mp4"
    ff("-i", video2, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "copy", "-t", total, out)
    tl = out_dir / f"{name or scene}_timeline.yaml"
    tl.write_text(yaml.safe_dump({"scene": scene, "total_sec": total, "cuts": timeline,
                                  "voice": [{"text": t, "start": round(a, 2), "end": round(b, 2)} for a, b, t in subs]},
                                 allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"{out.relative_to(ROOT)}（{total}秒、{len(shots)}カット、台詞 {len(subs)} 本）")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scene_dir")
    p.add_argument("--bgm")
    p.add_argument("--out", default="animatic/out")
    p.add_argument("--name", help="書き出すファイル名（拡張子なし）")
    a = p.parse_args()
    if (ROOT / a.scene_dir / "voice.yaml").exists():
        build_original(a.scene_dir, ROOT / a.out, a.name)   # 原作の台詞（D-045）
    else:
        build(a.scene_dir, a.bgm, ROOT / a.out)


if __name__ == "__main__":
    main()
