#!/usr/bin/env python3
"""下絵の通し（previs/<シーン>/<シーン>_previs_reel.mp4）を作る。D-062 の 3D の段階で、ユーザーが場面ごとに動きと間を確かめる。

  python tools/previs_reel.py episodes/A/scenes/A05b

- カットの順と秒はショットファイル（tc・sec）。下絵の動画（previs/<シーン>/<カット>.mp4）をカットの秒に合わせてつなぐ
  （短ければ最後のコマで止め、長ければ切る）。左上にカットIDと秒を書く。
- 声は voice.yaml の置き場所に原作の台詞（audio/<シーン>/original/<台詞ID>_*.wav）を置く。
- シーンのフォルダに sound.yaml があれば、BGM・効果音・ちょっとした声を重ねる（D-067、2026-11-01）。書き方は sound_items() のコメント。
  voice.yaml の bgm（原作の時刻のままの分離音）は使わない。
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
    sy = scene_dir / "sound.yaml"
    if not sy.exists():
        return []
    tc = {s["id"]: float(s["tc"]) for s in shots}
    total = sum(float(s["sec"]) for s in shots)
    doc = yaml.safe_load(sy.read_text(encoding="utf-8")) or {}

    def when(v):
        if isinstance(v, (int, float)):
            return float(v)
        return tc[v["cut"]] + float(v.get("t", 0.0))

    out = []
    for kind in ("bgm", "sfx", "voice"):
        for it in doc.get(kind) or []:
            at = float(it["at"]) if "at" in it else when(it)
            end = when(it["until"]) if "until" in it else (at + float(it["dur"]) if "dur" in it else None)
            if end is None and it.get("loop"):   # 繰り返す音は、止める所が無ければシーンの終わりまで
                end = total
            if end is not None:
                end = min(end, total)
            out.append({**it, "kind": kind, "at0": at, "end0": end})
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
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    video = work / "video.mp4"
    run("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video)
    total = sum(float(s["sec"]) for s in shots)
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
