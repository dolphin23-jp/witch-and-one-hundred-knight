#!/usr/bin/env python3
"""下絵の作り直しの前と後を比べる動画（2026-11-08）。Blender は使わない（ffmpeg）。

  python tools/previs_compare.py compare CUTS.txt OUT.mp4 [--before-rev 4fa404bd]
      前（左、Git の --before-rev の版の previs/<場面>/<カット>.mp4）と後（右、今の previs/<場面>/<カット>.mp4）を並べ、原作の台詞を重ねる
  python tools/previs_compare.py chain CUTS.txt OUT.mp4
      カットを、前後のカット（今の版）と続けて。線（イマジナリーライン）のつながりを見る

CUTS.txt は 1 行に「場面 カット…」（例: `A17 A17-026 A17-028`）。chain は全部のカットの前後をつなぐ。
OUT と同じ所に、スマホで見る小さい版（_small.mp4、幅 3/4・低い画質）も作る。台詞は voice.yaml の置き場所どおり（BGM は入れない）。
mp4 は Git に入れない（2026-11-08 オーナー判断 (b)。小さい版だけ Git）。
"""
import argparse
import glob
import pathlib
import subprocess
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = next((f for f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
             if pathlib.Path(f).exists()), None)


def shot(sc, cut):
    return yaml.safe_load((ROOT / f"episodes/A/scenes/{sc}/shots/{cut}.yaml").read_text(encoding="utf-8"))


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def voice_track(sc, t0, t1, dst):
    """場面の時刻 t0〜t1 にかかる原作の台詞を、voice.yaml の置き場所どおりに重ねた wav。"""
    lines = (yaml.safe_load((ROOT / f"episodes/A/scenes/{sc}/voice.yaml").read_text(encoding="utf-8")) or {}).get("lines") or []
    src = ROOT / "audio" / sc / "original"
    ins, flt = [], []
    for l in lines:
        wav = next(iter(sorted(glob.glob(str(src / f"{l['id']}_*.wav")))), None)
        if not wav:
            continue
        at, dur = float(l["at"]), duration(wav)
        if at + dur < t0 or at > t1:
            continue
        k = len(ins) // 2
        ins += ["-i", wav]
        off = at - t0
        base = f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        flt.append(base + (f"adelay={int(off * 1000)}|{int(off * 1000)}[a{k}]" if off >= 0 else f"atrim=start={-off},asetpts=PTS-STARTPTS[a{k}]"))
    n = len(ins) // 2
    if n == 0:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{t1 - t0}", str(dst)], check=True)
        return
    mix = "".join(f"[a{k}]" for k in range(n)) + f"amix=inputs={n}:normalize=0,apad,atrim=0:{t1 - t0}[out]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(flt + [mix]), "-map", "[out]", "-ar", "48000", str(dst)], check=True)


def label(text, h):
    t = text.replace(":", "\\:").replace("'", "")
    font = f"fontfile={FONT}:" if FONT else ""
    return f"drawtext={font}text='{t}':x=10:y=10:fontsize={max(16, h // 18)}:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=6"


def clip_filter(sec, w, h, txt):
    """sec 秒にそろえ（短ければ最後のコマを止める）、w×h・12 コマ/秒にして文字を入れる。"""
    return f"scale={w}:{h},fps=12,tpad=stop_mode=clone:stop_duration={sec},trim=0:{sec},setpts=PTS-STARTPTS,{label(txt, h)}"


def encode(parts, out):
    out = pathlib.Path(out)
    small = out.with_name(out.stem + "_small.mp4")
    with tempfile.TemporaryDirectory() as td:
        lst = pathlib.Path(td) / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:v", "libx264", "-crf", "22",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-vf", "scale=trunc(iw*3/8)*2:trunc(ih*3/8)*2", "-c:v", "libx264", "-crf", "34",
                    "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-movflags", "+faststart", str(small)], check=True)
    print(f"{out}（{out.stat().st_size / 1e6:.1f}MB）、{small}（{small.stat().st_size / 1e6:.1f}MB）")


def read_cuts(path):
    return [l.split() for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def compare(cuts, out, before_rev):
    with tempfile.TemporaryDirectory() as td:
        work = pathlib.Path(td)
        parts = []
        for row in cuts:
            sc = row[0]
            for cut in row[1:]:
                sh = shot(sc, cut)
                sec, tc = float(sh["sec"]), float(sh["tc"])
                before = work / f"{cut}_before.mp4"
                before.write_bytes(subprocess.run(["git", "show", f"{before_rev}:previs/{sc}/{cut}.mp4"], cwd=ROOT, capture_output=True, check=True).stdout)
                wav = work / f"{cut}.wav"
                voice_track(sc, tc, tc + sec, wav)
                dst = work / f"{cut}.mp4"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(before), "-i", str(ROOT / f"previs/{sc}/{cut}.mp4"), "-i", str(wav), "-filter_complex",
                                f"[0:v]{clip_filter(sec, 640, 360, '前  ' + cut)}[l];[1:v]{clip_filter(sec, 640, 360, '後  ' + str(sh.get('shot', ''))[:22])}[r];"
                                "[l][r]hstack=inputs=2[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-b:a", "160k", "-shortest", str(dst)], check=True)
                parts.append(dst)
                print(cut, flush=True)
        encode(parts, out)


def chain(cuts, out):
    with tempfile.TemporaryDirectory() as td:
        work = pathlib.Path(td)
        parts = []
        for row in cuts:
            sc = row[0]
            order = list((yaml.safe_load((ROOT / f"episodes/A/scenes/{sc}/blocking.yaml").read_text(encoding="utf-8")) or {})["cuts"])
            for cut in row[1:]:
                i = order.index(cut)
                for c in order[max(0, i - 1):i + 2]:
                    sh = shot(sc, c)
                    sec, tc = float(sh["sec"]), float(sh["tc"])
                    wav = work / f"{cut}_{c}.wav"
                    voice_track(sc, tc, tc + sec, wav)
                    dst = work / f"{cut}_{c}.mp4"
                    txt = f"{c}{'  （直した）' if c == cut else ''}  {str(sh.get('shot', ''))[:20]}"
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(ROOT / f"previs/{sc}/{c}.mp4"), "-i", str(wav), "-filter_complex",
                                    f"[0:v]{clip_filter(sec, 854, 480, txt)}[v]", "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "18",
                                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", str(dst)], check=True)
                    parts.append(dst)
                print(cut, flush=True)
        encode(parts, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("compare", "chain"))
    ap.add_argument("cuts")
    ap.add_argument("out")
    ap.add_argument("--before-rev", default="4fa404bd", help="前の版の Git のコミット（既定は 2026-11-08 の作り直しの前の main）")
    a = ap.parse_args()
    cuts = read_cuts(a.cuts)
    if a.mode == "compare":
        compare(cuts, a.out, a.before_rev)
    else:
        chain(cuts, a.out)


if __name__ == "__main__":
    main()
