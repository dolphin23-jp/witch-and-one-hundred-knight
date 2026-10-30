#!/usr/bin/env python3
"""下絵の通し（previs/<シーン>/<シーン>_previs_reel.mp4）を作る。D-062 の 3D の段階で、ユーザーが場面ごとに動きと間を確かめる。

  python tools/previs_reel.py episodes/A/scenes/A05b

- カットの順と秒はショットファイル（tc・sec）。下絵の動画（previs/<シーン>/<カット>.mp4）をカットの秒に合わせてつなぐ
  （短ければ最後のコマで止め、長ければ切る）。左上にカットIDと秒を書く。
- 声は voice.yaml の置き場所に原作の台詞（audio/<シーン>/original/<台詞ID>_*.wav）を置く。BGM は入れない。
- 854x480・24fps・H.264（スマホで見やすい大きさ）。
"""
import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


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
        flt.append(f"[{len(ins) // 2}]aresample=44100,aformat=channel_layouts=stereo,adelay={round(l['at'] * 1000)}|{round(l['at'] * 1000)}[a{k}]")
    out = pv / f"{scene}_previs_reel.mp4"
    if ins:
        n = len(flt)
        mix = "".join(f"[a{k}]" for k in range(len(vy["lines"])) if any(f.endswith(f"[a{k}]") for f in flt))
        run("-i", video, *ins, "-filter_complex", ";".join(flt) + f";{mix}amix=inputs={n}:normalize=0,apad,atrim=0:{total}[o]",
            "-map", "0:v", "-map", "[o]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", out)
    else:
        run("-i", video, "-c", "copy", out)
    print(f"{out.relative_to(ROOT)}（{len(shots)}カット、{total:.1f}秒）")


if __name__ == "__main__":
    main()
