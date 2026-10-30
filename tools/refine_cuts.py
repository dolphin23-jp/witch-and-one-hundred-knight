#!/usr/bin/env python3
"""切り出した台詞の範囲を、台詞の文を手がかりに短い範囲だけ書き起こし直して見直す（extract_lines.py の後に使う）。

extract_lines.py は声の音量で終わりを決めるので、分離した声に効果音などが漏れていると、台詞の後ろが何秒も長くなる。
続く台詞の境目も推定になる。この道具は、問題のある台詞ごとに、その前後だけを脚本の文を initial_prompt にして
Whisper で書き起こし、脚本の文と1文字ずつ突き合わせて、最初の語の開始と最後の語の終わりから範囲（cut）を提案する。

  python tools/refine_cuts.py --vocals tmp/src/part18_voice.mp3 --script audio/A09/original/script.yaml \
      --csv audio/A09/original/lines.csv --ids L281 L288 …            # ID を指定
  python tools/refine_cuts.py … --auto                                  # ◎以外と、長さが不自然なものを全部
  # 声が見つからなかった台詞など、窓を手で決めるとき: --window L285=11:50.0-11:57.0

結果は --out（JSON）に書く。script.yaml の cut へ写すのは人が見てから（--apply で追記もできる）。
提案は目安で、最終判断はユーザーが聞いて決める。

必要なもの: extract_lines.py と同じ（faster-whisper, pykakasi, soundfile, pyyaml）。
"""
import argparse
import csv
import json
import pathlib
import re
import subprocess
import sys

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import extract_lines as el  # noqa: E402

HALLU = ("ご視聴ありがとうございました", "ありがとうございました", "チャンネル登録")


def tc(t):
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def parse(s):
    m, x = s.split(":")
    return int(m) * 60 + float(x)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vocals", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--ids", nargs="*", default=[])
    ap.add_argument("--auto", action="store_true", help="◎以外と、字数から見て長すぎる台詞を対象にする")
    ap.add_argument("--window", action="append", default=[], help="ID=開始-終了 窓を手で決める")
    ap.add_argument("--pad", type=float, default=1.5, help="窓の余白（秒）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--whisper", default="large-v3")
    a = ap.parse_args()

    spec = yaml.safe_load(open(a.script, encoding="utf-8"))
    lines = {l["id"]: l for l in spec["lines"]}
    rows = {r["id"]: r for r in csv.DictReader(open(a.csv, encoding="utf-8"))}
    wins = {}
    for w in a.window:
        i, rng = w.split("=")
        s, e = rng.split("-")
        wins[i] = (parse(s), parse(e))

    targets = list(a.ids)
    if a.auto:
        for i, l in lines.items():
            if l.get("use", True) is False or i in targets:
                continue
            r = rows.get(i, {})
            n = len(re.sub(r"[（）()「」“”、。！？…・\s]", "", l["text"]))
            long = r.get("dur") and float(r["dur"]) > 1.6 * (n * 0.17 + 0.4)
            if r.get("mark") != "◎" or long:
                targets.append(i)
    targets += [i for i in wins if i not in targets]

    from faster_whisper import WhisperModel
    model = WhisperModel(a.whisper, device="cpu", compute_type="int8")
    el.reading("")
    result = {}
    for k, i in enumerate(targets, 1):
        l, r = lines[i], rows.get(i, {})
        if i in wins:
            t0, t1 = wins[i]
        elif r.get("start"):
            t0, t1 = parse(r["start"]) - a.pad, parse(r["end"]) + a.pad
        else:
            print(f"[{k}/{len(targets)}] {i}: 範囲がないので --window で指定してください", flush=True)
            continue
        t0 = max(0.0, t0)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.2f}", "-t", f"{t1 - t0:.2f}", "-i", a.vocals, "-ac", "1",
                              "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
        x = np.frombuffer(raw, dtype=np.float32)
        segs, _ = model.transcribe(x, language="ja", word_timestamps=True, beam_size=5, vad_filter=False,
                                   condition_on_previous_text=False, initial_prompt=l["text"],
                                   no_speech_threshold=0.9, log_prob_threshold=-3.0)
        wh = []
        for s in segs:
            if any(h in s.text for h in HALLU) and len(s.text) < 20:
                continue
            wh.append({"words": [{"s": w.start, "e": w.end, "w": w.word, "p": w.probability} for w in (s.words or [])]})
        chars = el.transcript_chars(wh)
        if not chars:
            print(f"[{k}/{len(targets)}] {i}: 声が書き起こせませんでした", flush=True)
            result[i] = {"error": "no speech", "window": [tc(t0), tc(t1)]}
            continue
        a_read = el.reading(l["text"])
        b_read = "".join(c["c"] for c in chars)
        m = el.align(a_read, b_read)
        pos = [p for p in m if p is not None]
        cover = len(pos) / max(1, len(a_read))
        if not pos:
            result[i] = {"error": "no match", "window": [tc(t0), tc(t1)], "heard": b_read[:40]}
            print(f"[{k}/{len(targets)}] {i}: 突き合わせできませんでした", flush=True)
            continue
        first, last = chars[min(pos)], chars[max(pos)]
        s_abs, e_abs = t0 + first["s"], t0 + last["e"]
        result[i] = {"cut": [tc(max(t0, s_abs - 0.12)), tc(e_abs + 0.30)], "speech": [tc(s_abs), tc(e_abs)],
                     "cover": round(cover, 2), "dur": round(e_abs - s_abs + 0.42, 2), "window": [tc(t0), tc(t1)],
                     "old": [r.get("start"), r.get("end")]}
        print(f"[{k}/{len(targets)}] {i} 旧 {r.get('start')}-{r.get('end')} → 新 {result[i]['cut'][0]}-{result[i]['cut'][1]} "
              f"({result[i]['dur']}秒、一致 {cover:.2f})", flush=True)
        pathlib.Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    pathlib.Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
