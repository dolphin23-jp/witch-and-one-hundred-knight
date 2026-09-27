#!/usr/bin/env python3
"""原作音源（プレイ録画の mp3）を1パートまるごと、声と BGM・効果音に分け、Drive に置く（D-045）。

手順:
  1. Drive の「魔女と百騎兵 原作音源」から mp3 を読み込む（tools/drive.py、受け口経由）
  2. Demucs で声とそれ以外（BGM・効果音）に分ける
  3. Whisper で声を書き起こす（時刻つき。場面を探すための目安で、聞き間違いがある）
  4. mp3 にして Drive の「声とBGMの分離/パートN/」に置き、はじめに読むファイルを更新する
  5. 記録を audio/original/separated.yaml、書き起こしを audio/original/transcripts/partN.txt に残す

元の mp3 と分けた音声はリポジトリに入れない（作業フォルダは tmp/separate/、Git の管理外）。

使い方:
  python tools/separate.py --part 17
  python tools/separate.py --part 16 --part 17 --part 18      # 順に
  python tools/separate.py --src tmp/x.mp3 --name テスト --no-upload   # 手元のファイルで試す

必要なもの（重いので setup には入れていない。セッションごとに .venv に入れる）:
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  pip install demucs faster-whisper pyyaml
  受け口の鍵: 環境変数 DRIVE_BRIDGE_KEY（setup/drive_bridge.md）
"""
import argparse
import datetime
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import drive  # noqa: E402

DRIVE_DIR = "声とBGMの分離"
SRC_NAME = "魔女と百騎兵Revival パート{n}.mp3"
RECORD = ROOT / "audio" / "original" / "separated.yaml"
TRANSCRIPTS = ROOT / "audio" / "original" / "transcripts"
BITRATE = "192k"


def fmt_tc(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def run(cmd):
    subprocess.run(cmd, check=True)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


CHUNK_SEC = 600     # 長い音源は10分ずつに分けて分離する（29分をまるごと分けるとメモリ 15GB で足りなかった）
OVERLAP_SEC = 10    # 切れ目は重ねて、なめらかにつなぐ


def separate(wav, work, model):
    out = work / "sep" / model / wav.stem
    if (out / "vocals.wav").exists():
        return out / "vocals.wav", out / "no_vocals.wav"
    sec = duration(wav)
    if sec <= CHUNK_SEC * 1.5:
        run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", model, "-o", str(work / "sep"), str(wav)])
        return out / "vocals.wav", out / "no_vocals.wav"
    import numpy as np
    import soundfile as sf
    chunks = work / "chunks"
    chunks.mkdir(exist_ok=True)
    starts = list(range(0, int(sec), CHUNK_SEC))
    if sec - starts[-1] < 60 and len(starts) > 1:   # 最後が短すぎるときは一つ前にまとめる
        starts.pop()
    pieces = []
    for i, st in enumerate(starts):
        a = max(0, st - OVERLAP_SEC)
        b = starts[i + 1] + OVERLAP_SEC if i + 1 < len(starts) else sec
        name = f"c{i:02d}"
        src = chunks / f"{name}.wav"
        sep = chunks / "sep" / model / name
        if not (sep / "vocals.wav").exists():
            run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-ss", str(a), "-to", str(b),
                 "-c:a", "pcm_s16le", str(src)])
            print(f"  {i + 1}/{len(starts)}（{fmt_tc(a)}〜{fmt_tc(b)}）", flush=True)
            run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", model, "-o", str(chunks / "sep"), str(src)])
        pieces.append((a, sep))
    out.mkdir(parents=True, exist_ok=True)
    total = sf.info(str(wav)).frames
    for stem in ("vocals", "no_vocals"):
        acc, sr = None, None
        for a, sep in pieces:
            x, sr = sf.read(str(sep / f"{stem}.wav"), dtype="float32")
            if acc is None:
                acc = np.zeros((total, x.shape[1]), dtype="float32")
            i0 = int(round(a * sr))
            x = x[:total - i0]
            if i0 > 0:   # 重なり（前の片の終わり 2*OVERLAP）を直線でつなぐ
                n = min(int(2 * OVERLAP_SEC * sr), len(x))
                ramp = np.linspace(0, 1, n, dtype="float32")[:, None]
                acc[i0:i0 + n] *= 1 - ramp
                x = x.copy()
                x[:n] *= ramp
            acc[i0:i0 + len(x)] += x
        sf.write(str(out / f"{stem}.wav"), acc, sr, subtype="FLOAT")
    return out / "vocals.wav", out / "no_vocals.wav"


def transcribe(vocals, dst, model_name):
    if dst.exists():
        return json.loads(dst.read_text(encoding="utf-8"))
    from faster_whisper import WhisperModel
    m = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(str(vocals), language="ja", beam_size=5, vad_filter=True,
                           condition_on_previous_text=False)
    res = []
    for s in segs:
        res.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        print(f"  [{fmt_tc(s.start)}] {s.text.strip()}", flush=True)
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def transcript_text(label, src_name, segs, whisper_model):
    head = [f"{label} の声の書き起こし",
            f"元の音源: {src_name}",
            f"時刻は元の mp3 の中の位置（分:秒）。Whisper {whisper_model} による自動の書き起こしで、聞き間違いや抜けがある。",
            "場面を探すための目安として使う。", ""]
    return "\n".join(head + [f"[{fmt_tc(s['start'])}] {s['text']}" for s in segs]) + "\n"


def readme(record):
    lines = ["声とBGMの分離", "",
             "プレイ録画の音源（このフォルダの一つ上にある「魔女と百騎兵Revival パートN.mp3」）を、",
             "パートごとに声と、それ以外（BGM と効果音）に分けたもの。Claude が tools/separate.py で作った。", "",
             "パートN/ の中身",
             "  パートN_声.mp3            声だけ（BGM と効果音を除いたもの）",
             "  パートN_BGMと効果音.mp3   声を除いたもの（BGM と効果音が一緒に入っている）",
             "  パートN_書き起こし.txt    声の書き起こし。[分:秒] は元の mp3 の中の位置",
             "",
             "3つとも元の mp3 と同じ長さで、時刻はそのまま対応する。",
             "声を分ける道具は Demucs（htdemucs_ft）、書き起こしは Whisper。",
             "分離はきれいでも完全ではない。声の側に BGM がわずかに残ったり、叫び声や効果音の一部が逆側に入ったりする。",
             "台詞として使うときは、脚本と突き合わせて1本ずつ切り出す（tools/extract_lines.py、リポジトリの audio/<シーン>/original/）。",
             "", "パートの一覧", ""]
    for p in sorted(record.get("parts", []), key=lambda p: p["part"]):
        lines.append(f"  パート{p['part']}  {fmt_tc(p['sec'])}  （{p['made']}）")
    return "\n".join(lines) + "\n"


def load_record():
    if RECORD.exists():
        return yaml.safe_load(RECORD.read_text(encoding="utf-8")) or {}
    return {}


def save_record(rec):
    head = ("# プレイ録画の音源をパートごとに声と BGM・効果音に分けた記録（tools/separate.py が書く。手で編集しない）。\n"
            "# 分けた音声は Drive の「魔女と百騎兵 原作音源/声とBGMの分離/パートN/」にある。リポジトリには入れない。\n")
    RECORD.write_text(head + yaml.safe_dump(rec, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")


def process(label, src, work, args, part=None):
    work.mkdir(parents=True, exist_ok=True)
    wav = work / f"{work.name}.wav"
    if not wav.exists():
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-ac", "2", "-ar", "44100",
             "-c:a", "pcm_s16le", str(wav)])
    sec = duration(wav)
    print(f"[{label}] 分離（{args.demucs}、{fmt_tc(sec)}）", flush=True)
    vocals, rest = separate(wav, work, args.demucs)
    print(f"[{label}] 書き起こし（{args.whisper}）", flush=True)
    segs = transcribe(vocals, work / "transcript.json", args.whisper)

    out = work / "out"
    out.mkdir(exist_ok=True)
    files = {"voice": out / f"{label}_声.mp3", "bgm": out / f"{label}_BGMと効果音.mp3",
             "transcript": out / f"{label}_書き起こし.txt"}
    for key, stem in (("voice", vocals), ("bgm", rest)):
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(stem), "-c:a", "libmp3lame",
             "-b:a", BITRATE, str(files[key])])
    files["transcript"].write_text(transcript_text(label, src.name, segs, args.whisper), encoding="utf-8")

    if part is not None:
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        (TRANSCRIPTS / f"part{part}.txt").write_text(files["transcript"].read_text(encoding="utf-8"), encoding="utf-8")

    if args.no_upload or part is None:
        print(f"[{label}] 書き出し: {out}", flush=True)
        return

    ids = {}
    for key, path in files.items():
        f = drive.put(path, f"{DRIVE_DIR}/{label}", replace=True)
        ids[key] = f.get("id")

    rec = load_record()
    parts = [p for p in rec.get("parts", []) if p.get("part") != part]
    parts.append({"part": part, "src": src.name, "sec": round(sec, 2),
                  "made": datetime.date.today().isoformat(),
                  "demucs": args.demucs, "whisper": args.whisper, "bitrate": BITRATE,
                  "drive": f"{DRIVE_DIR}/{label}/",
                  "files": {k: {"name": files[k].name, "id": ids[k]} for k in files},
                  "transcript": f"audio/original/transcripts/part{part}.txt",
                  "segments": len(segs)})
    rec["parts"] = sorted(parts, key=lambda p: p["part"])
    save_record(rec)
    readme_path = ROOT / "tmp" / "separate" / "はじめに読んでください.txt"
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text(readme(rec), encoding="utf-8")
    drive.put(readme_path, DRIVE_DIR, replace=True)
    print(f"[{label}] Drive に置きました: {DRIVE_DIR}/{label}/", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", type=int, action="append", help="パートの番号（くり返し指定できる）")
    ap.add_argument("--src", help="手元の mp3（--part の代わりに試すとき。Drive には置かない）")
    ap.add_argument("--name", default="テスト", help="--src のときの名前")
    ap.add_argument("--demucs", default="htdemucs_ft")
    ap.add_argument("--whisper", default="large-v3-turbo")
    ap.add_argument("--no-upload", action="store_true", help="Drive に置かない（試すとき）")
    args = ap.parse_args()
    base = ROOT / "tmp" / "separate"

    if args.src:
        process(args.name, pathlib.Path(args.src), base / args.name, args)
        return
    if not args.part:
        ap.error("--part か --src を指定してください")
    for n in args.part:
        label = f"パート{n}"
        work = base / f"part{n}"
        src = work / SRC_NAME.format(n=n)
        if not src.exists():
            print(f"[{label}] Drive から読み込み", flush=True)
            item = drive.resolve(SRC_NAME.format(n=n))
            if not item:
                sys.exit(f"Drive に見つかりません: {SRC_NAME.format(n=n)}")
            drive.cmd_get(argparse.Namespace(path="id:" + item["id"], dst=str(src)))
        process(label, src, work, args, part=n)


if __name__ == "__main__":
    main()
