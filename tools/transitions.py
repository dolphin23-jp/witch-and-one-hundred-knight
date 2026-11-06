#!/usr/bin/env python3
"""場面の境目の確認用の動画（2026-11-07、下絵のアップグレードの続き）。episodes/A/transitions.yaml のつなぎ方で、
前の場面の終わり（5秒）と次の場面の頭（5秒）をつなぎ、previs/transitions/<前>-<次>.mp4 を作る。全部を続けた transitions_all.mp4 も作る。

  python tools/transitions.py              # すべての境目
  python tools/transitions.py A02 A04      # A02→A04 だけ（前の場面の名前を並べる）

元の動画: 下絵の通し previs/<場面>/<場面>_previs_reel.mp4。なければ Animatic の書き出し animatic/exports/<場面>_v*.mp4 のいちばん新しいもの。
どちらもない場面（A01 など）の境目は飛ばす。
音: lead（J カット）は次の場面の音を先に出す（次の場面の画の頭を lead 秒だけ削って、音と画を合わせたまま）。
    tail（L カット）は前の場面の音を残す（前の場面の画の終わりを tail 秒だけ削る）。
"""
import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
TA = TB = 5.0
XF = {"dissolve": "fade", "black": "fadeblack", "white": "fadewhite", "iris": "circleclose"}
JA = {"cut": "カット", "dissolve": "溶かす", "black": "暗転", "white": "白く飛ばす", "wipe": "ワイプ", "iris": "丸く閉じる"}


def source(scene):
    p = ROOT / "previs" / scene / f"{scene}_previs_reel.mp4"
    if p.exists():
        return p
    ex = sorted((ROOT / "animatic" / "exports").glob(f"{scene}_v*.mp4"))
    return ex[-1] if ex else None


def duration(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
    return float(out.strip())


def has_audio(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
    return bool(out.strip())


def clip(b, out):
    A, B = source(b["from"]), source(b["to"])
    if A is None or B is None:
        return None
    kind = b.get("kind", "cut")
    d = float(b.get("sec", 0.0)) if kind != "cut" else 0.0
    au = b.get("audio") or {}
    lead, tail = float(au.get("lead", 0.0)), float(au.get("tail", 0.0))
    da = duration(A)
    a0 = max(0.0, da - TA)
    LA = (da - a0) - tail             # 前の場面の画の長さ
    LB = TB - lead                    # 次の場面の画の長さ
    off = LA - d                      # 次の場面の画が始まる所
    b_audio_at = off - lead           # 次の場面の音の頭（その場面の 0 秒）が来る所
    label = f"{b['from']} → {b['to']}  {JA.get(kind, kind)}" + (f" {d:g}秒" if d else "") + \
            (f"  音を先に {lead:g}秒" if lead else "") + (f"  音を残す {tail:g}秒" if tail else "")
    fv = (f"[0:v]trim=start={a0:.3f}:end={a0 + LA:.3f},setpts=PTS-STARTPTS,fps=24,scale=854:480,format=yuv420p[va];"
          f"[1:v]trim=start={lead:.3f}:end={TB:.3f},setpts=PTS-STARTPTS,fps=24,scale=854:480,format=yuv420p[vb];")
    if kind == "cut":
        fv += "[va][vb]concat=n=2:v=1:a=0[vx];"
    else:
        tr = XF.get(kind) or ("wipeleft" if b.get("dir", "left") == "left" else "wiperight" if b.get("dir") == "right" else "wipe" + b.get("dir", "left"))
        fv += f"[va][vb]xfade=transition={tr}:duration={d:.3f}:offset={off:.3f}[vx];"
    fv += (f"[vx]drawtext=text='{label}':fontfile={FONT}:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.55:x=(w-text_w)/2:y=h-46[v]")
    fa = ""
    if has_audio(A) and has_audio(B):
        fo = max(0.05, (d / 2 if kind == "black" else d) + tail)
        fi = max(0.05, (d / 2 if kind == "black" else d) + lead)
        fa = (f";[0:a]atrim=start={a0:.3f},asetpts=PTS-STARTPTS,aresample=44100,aformat=channel_layouts=stereo,"
              f"afade=t=out:st={max(0.0, (da - a0) - fo):.3f}:d={fo:.3f}[aa];"
              f"[1:a]atrim=end={TB:.3f},asetpts=PTS-STARTPTS,aresample=44100,aformat=channel_layouts=stereo,"
              f"afade=t=in:d={fi:.3f},adelay={round(max(0.0, b_audio_at) * 1000)}|{round(max(0.0, b_audio_at) * 1000)}[ab];"
              f"[aa][ab]amix=inputs=2:normalize=0:duration=longest[a]")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(A), "-i", str(B), "-filter_complex", fv + fa, "-map", "[v]"]
    if fa:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p", "-t", f"{LA + LB - d:.3f}", str(out)]
    subprocess.run(cmd, check=True)
    return out


def main():
    spec = yaml.safe_load((ROOT / "episodes" / "A" / "transitions.yaml").read_text(encoding="utf-8"))
    pick = set(sys.argv[1:])
    dst = ROOT / "previs" / "transitions"
    dst.mkdir(parents=True, exist_ok=True)
    made, skipped = [], []
    for b in spec["boundaries"]:
        if pick and b["from"] not in pick:
            continue
        out = dst / f"{b['from']}-{b['to']}.mp4"
        r = clip(b, out)
        (made if r else skipped).append(f"{b['from']}→{b['to']}")
        if r:
            print(f"{out.relative_to(ROOT)}  {b.get('kind')}", flush=True)
    if not pick:   # 全部を続けた1本（場面の順）
        parts = [dst / f"{b['from']}-{b['to']}.mp4" for b in spec["boundaries"] if (dst / f"{b['from']}-{b['to']}.mp4").exists()]
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            for p in parts:
                f.write(f"file '{p}'\n")
            lst = f.name
        allp = dst / "transitions_all.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(allp)], check=True)
        print(f"{allp.relative_to(ROOT)}（{len(parts)} か所、{duration(allp):.0f}秒）")
    if skipped:
        print("元の動画がなくて飛ばした境目: " + "、".join(skipped))


if __name__ == "__main__":
    main()
