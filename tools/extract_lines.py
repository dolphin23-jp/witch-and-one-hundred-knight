#!/usr/bin/env python3
"""原作音源から台詞を1本ずつ切り出す（D-045）。

手順:
  1. 原作音源（mp3）から範囲を切り出す
  2. Demucs で声とそれ以外（BGM・効果音）に分ける
  3. Whisper で声を書き起こし、語ごとの時刻を取る
  4. 脚本の台詞と書き起こしを、読み（ひらがな）で1文字ずつ突き合わせる
  5. 声の音量で開始・終了を詰め、台詞ごとの WAV と一覧（lines.csv、lines.md）を書き出す

元の mp3、分離した音声、書き起こしは --work（既定は tmp/、Git の管理外）に置く。
リポジトリに入れるのは、切り出した台詞と一覧だけ。

使い方:
  python tools/extract_lines.py --src <パート16.mp3> --start 14:08 --duration 300 \
      --script audio/A01/original/script.yaml --out audio/A01/original

  # 試しに分離だけ（1分）
  python tools/extract_lines.py --src <mp3> --start 14:08 --duration 60 --separate-only --work tmp/test

必要なもの（重いので setup には入れていない）:
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  pip install demucs faster-whisper pykakasi soundfile pyyaml
"""
import argparse
import csv
import json
import pathlib
import re
import subprocess
import sys

import numpy as np
import soundfile as sf
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAMES = {"metallica": "メタリカ", "bisco": "ビスコ", "arlecchino": "アルレッキーノ"}

HOP = 0.01          # 音量を測る間隔（秒）
PRE_PAD = 0.08      # 声の前に残す余白
POST_PAD = 0.20     # 声の後ろに残す余白（残響）
FADE = 0.01


# ---------- 時刻 ----------

def parse_tc(s):
    parts = [float(p) for p in str(s).split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def fmt_tc(sec):
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}"


# ---------- 1〜3: 切り出し、分離、書き起こし ----------

def cut_region(src, start, dur, dst):
    if dst.exists():
        return
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(dur),
                    "-i", str(src), "-c:a", "pcm_s16le", str(dst)], check=True)


def separate(region, work, model):
    out = work / "sep" / model / region.stem / "vocals.wav"
    if not out.exists():
        subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", model,
                        "-o", str(work / "sep"), str(region)], check=True)
    return out


def transcribe(vocals, dst, model_name):
    if dst.exists():
        return json.loads(dst.read_text(encoding="utf-8"))
    from faster_whisper import WhisperModel
    m = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(str(vocals), language="ja", word_timestamps=True, beam_size=5,
                           condition_on_previous_text=False)
    res = []
    for s in segs:
        res.append({"start": s.start, "end": s.end, "text": s.text,
                    "words": [{"s": w.start, "e": w.end, "w": w.word, "p": w.probability} for w in (s.words or [])]})
        print(f"  {fmt_tc(s.start)}-{fmt_tc(s.end)} {s.text}", flush=True)
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


# ---------- 4: 読みで突き合わせる ----------

_kks = None


def reading(text):
    """ひらがなの読みにそろえる（「ヴァルプルギス」と「バルプルギス」、「犬コロ」と「犬ころ」を同じに扱う）。"""
    global _kks
    if _kks is None:
        import pykakasi
        _kks = pykakasi.kakasi()
    hira = "".join(x["hira"] for x in _kks.convert(text))
    hira = re.sub(r"[^ぁ-ゖー]", "", hira)
    for a, b in (("ゔぁ", "ば"), ("ゔぃ", "び"), ("ゔ", "ぶ"), ("ぢ", "じ"), ("づ", "ず"), ("を", "お")):
        hira = hira.replace(a, b)
    return hira


def align(a, b):
    """a（脚本）と b（書き起こし）を編集距離で1文字ずつ対応づける。a の各文字に対応する b の位置（なければ None）。"""
    n, m = len(a), len(b)
    D = np.zeros((n + 1, m + 1), dtype=np.int32)
    D[:, 0] = np.arange(n + 1)
    D[0, :] = 0   # 書き起こしの頭に余分があってもよい
    bb = np.frombuffer(b.encode("utf-32-le"), dtype=np.uint32) if m else np.zeros(0, np.uint32)
    for i in range(1, n + 1):
        ca = ord(a[i - 1])
        sub = D[i - 1, :-1] + (bb != ca)
        dele = D[i - 1, 1:] + 1
        row = np.minimum(sub, dele)
        # 挿入（b の余分）は左から順に伝える
        cur = np.empty(m + 1, dtype=np.int32)
        cur[0] = D[i, 0]
        best = np.concatenate(([cur[0]], row))
        # cur[j] = min(best[j], cur[j-1] + 1) を累積で求める
        idx = np.arange(m + 1)
        cur = np.minimum.accumulate(best - idx) + idx
        D[i] = cur
    # 書き起こしの末尾に余分があってもよい: 最終行の最小から戻る
    j = int(np.argmin(D[n]))
    i = n
    mapping = [None] * n
    while i > 0:
        if j > 0 and D[i, j] == D[i - 1, j - 1] + (a[i - 1] != b[j - 1]):
            if a[i - 1] == b[j - 1]:
                mapping[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif D[i, j] == D[i - 1, j] + 1:
            i -= 1
        elif j > 0 and D[i, j] == D[i, j - 1] + 1:
            j -= 1
        else:
            i, j = i - 1, j - 1
    return mapping


def transcript_chars(whisper):
    """書き起こしを読みの1文字ずつに分け、各文字に時刻と確からしさを付ける。

    Whisper は漢字1字ずつを語として返すことがあるので（「見」「ろ」）、読みは区間の文全体で取り、
    元の文字の位置から時刻を割り当てる。
    """
    reading("")   # 読みの辞書を読み込む
    chars = []
    wi = 0
    for seg in whisper:
        words = [w for w in seg["words"] if w["w"]]
        if not words:
            continue
        text, ot, op, ow = "", [], [], []
        for w in words:
            wi += 1
            n = len(w["w"])
            step = (w["e"] - w["s"]) / n
            for k in range(n):
                ot.append((w["s"] + k * step, w["s"] + (k + 1) * step))
                op.append(w["p"])
                ow.append((wi, w["w"]))
            text += w["w"]
        pos = 0
        for tok in _kks.convert(text):
            orig = tok["orig"]
            at = text.find(orig, pos) if orig else -1
            if at < 0:
                continue
            pos = at + len(orig)
            r = reading(orig)
            for q, c in enumerate(r):
                k = at + min(len(orig) - 1, q * len(orig) // len(r))
                chars.append({"c": c, "s": ot[k][0], "e": ot[k][1], "p": op[k], "wi": ow[k][0], "w": ow[k][1]})
    return chars


# ---------- 5: 声の音量で詰める ----------

def level_db(x, sr):
    hop = int(sr * HOP)
    win = hop * 3
    c = np.concatenate(([0.0], np.cumsum(x.astype(np.float64) ** 2)))
    starts = np.arange(0, max(1, len(x) - win + 1), hop)
    rms = np.sqrt((c[starts + win] - c[starts]) / win)
    return 20 * np.log10(rms + 1e-9)


def voiced_regions(db, thr, bridge=0.25, min_len=0.08):
    on = db > thr
    regs = []
    i = 0
    n = len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            regs.append([i * HOP, j * HOP])
            i = j
        else:
            i += 1
    merged = []
    for r in regs:
        if merged and r[0] - merged[-1][1] < bridge:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] >= min_len]


def cut_level(db, end):
    """声の区間の終わり直前の音量が、区間の中央値からどれだけ下がっているか（dB）。0 に近いほど急に切れている。"""
    k = int(round(end / HOP))
    seg = db[max(0, k - 80):k]
    last = db[max(0, k - 3):k]
    if len(seg) == 0 or len(last) == 0:
        return -99.0
    return float(last.max() - np.median(seg))


# ---------- 本体 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="原作音源（mp3）")
    ap.add_argument("--start", required=True, help="切り出す範囲の頭（例 14:08）")
    ap.add_argument("--duration", type=float, required=True, help="範囲の長さ（秒）")
    ap.add_argument("--script", help="台詞の一覧（YAML）")
    ap.add_argument("--out", help="台詞の書き出し先")
    ap.add_argument("--work", default=str(ROOT / "tmp" / "extract"), help="作業フォルダ（Git の管理外）")
    ap.add_argument("--demucs", default="htdemucs_ft")
    ap.add_argument("--whisper", default="large-v3")
    ap.add_argument("--separate-only", action="store_true")
    a = ap.parse_args()

    work = pathlib.Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    t0 = parse_tc(a.start)
    region = work / f"region_{int(t0)}_{int(a.duration)}.wav"
    print("切り出し", fmt_tc(t0), f"+{a.duration}秒")
    cut_region(a.src, t0, a.duration, region)
    print("分離", a.demucs)
    vocals = separate(region, work, a.demucs)
    if a.separate_only:
        print(vocals)
        return

    spec = yaml.safe_load(pathlib.Path(a.script).read_text(encoding="utf-8"))
    lines = spec["lines"]
    print("書き起こし", a.whisper)
    wh = transcribe(vocals, work / f"whisper_{vocals.parent.name}_{a.demucs}.json", a.whisper)

    # 突き合わせ（次のシーンの頭の台詞も入れて、最後の台詞の終わりを決める）
    all_lines = lines + ([spec["next_line"]] if spec.get("next_line") else [])
    tchars = transcript_chars(wh)
    tstr = "".join(c["c"] for c in tchars)
    sstr, owner = "", []
    for li, ln in enumerate(all_lines):
        r = reading(ln["text"])
        ln["_reading"] = r
        sstr += r
        owner += [li] * len(r)
    mapping = align(sstr, tstr)

    info = []
    for li, ln in enumerate(all_lines):
        pos = [mapping[k] for k in range(len(sstr)) if owner[k] == li]
        hit = [p for p in pos if p is not None]
        n = len(pos)
        d = {"hit": len(hit), "n": n, "cov": len(hit) / n if n else 0}
        if hit:
            d["s"], d["e"] = tchars[hit[0]]["s"], tchars[hit[-1]]["e"]
            d["tj0"], d["tj1"] = hit[0], hit[-1]
            d["p"] = float(np.mean([tchars[p]["p"] for p in hit]))
            tail = pos[-3:]
            d["tail_missing"] = sum(1 for p in tail if p is None)
            head = pos[:3]
            d["head_missing"] = sum(1 for p in head if p is None)
        info.append(d)

    # 声の音量
    x, sr = sf.read(str(vocals))
    mono = x.mean(axis=1) if x.ndim > 1 else x
    db = level_db(mono, sr)
    speech = np.percentile(db[db > -60], 75) if np.any(db > -60) else -30
    thr = max(-45.0, speech - 22)
    regs = voiced_regions(db, thr)
    peak = [float(db[int(r[0] / HOP):max(int(r[0] / HOP) + 1, int(r[1] / HOP))].max()) for r in regs]
    loud_thr = speech - 10   # 台詞として足すのは、話し声と同じくらいの大きさの声だけ

    # 台詞の核（書き起こしの範囲）を、声の区間の境目まで広げる
    spans = []
    for li, d in enumerate(info):
        if "s" not in d:
            spans.append(None)
            continue
        s, e = d["s"], d["e"]
        for r in regs:
            if r[0] <= s <= r[1]:
                s = min(s, r[0])
            if r[0] <= e <= r[1]:
                e = max(e, r[1])
        spans.append([s, e])
    # 隣の台詞と重なったら、書き起こしの境目で分ける
    for li in range(len(spans) - 1):
        a_, b_ = spans[li], spans[li + 1]
        if a_ and b_ and a_[1] > b_[0]:
            cut = (info[li]["e"] + info[li + 1]["s"]) / 2
            a_[1], b_[0] = cut, cut
            info[li]["joined"] = info[li + 1]["joined_prev"] = True

    # どの台詞にも入っていない声は、近い方の台詞に足す（「キヒヒ」「あん？」は書き起こしに出ないことが多い）。
    # 小さな音（息、物音、百騎兵の声など）と、台詞から 1.5 秒以上離れた声は足さずに一覧に残す。
    unassigned = []
    for r, pk in zip(regs, peak):
        if any(sp and r[0] < sp[1] and sp[0] < r[1] for sp in spans):
            continue
        if pk < loud_thr:
            unassigned.append((r, pk, "小さな音"))
            continue
        prev = max((i for i, sp in enumerate(spans) if sp and sp[1] <= r[0]), default=None)
        nxt = min((i for i, sp in enumerate(spans) if sp and sp[0] >= r[1]), default=None)
        gp = r[0] - spans[prev][1] if prev is not None else 1e9
        gn = spans[nxt][0] - r[1] if nxt is not None else 1e9
        if min(gp, gn) > 1.5:
            unassigned.append((r, pk, "台詞から離れた声"))
            continue
        k = nxt if gn <= gp else prev
        if k >= len(lines):
            unassigned.append((r, pk, "次のシーンの台詞の手前"))
            continue
        spans[k][0], spans[k][1] = min(spans[k][0], r[0]), max(spans[k][1], r[1])
        info[k].setdefault("added", []).append(r)

    # 書き出し
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    preview = []
    for li, ln in enumerate(lines):
        d, sp = info[li], spans[li]
        who = NAMES.get(ln["who"], ln["who"])
        if sp is None:
            rows.append({"id": ln["id"], "who": who, "file": "", "start": "", "end": "", "dur": "",
                         "match": "0.00", "mark": "×", "cut": "", "flags": "音声が見つからない", "heard": "", "text": ln["text"]})
            continue
        prev_end = spans[li - 1][1] if li > 0 and spans[li - 1] else 0.0
        next_start = spans[li + 1][0] if li + 1 < len(spans) and spans[li + 1] else len(mono) / sr
        cs = max(sp[0] - PRE_PAD, (prev_end + sp[0]) / 2, 0.0)
        ce = min(sp[1] + POST_PAD, (sp[1] + next_start) / 2, len(mono) / sr)
        seg = mono[int(cs * sr):int(ce * sr)].copy()
        f = int(FADE * sr)
        if len(seg) > 2 * f:
            seg[:f] *= np.linspace(0, 1, f)
            seg[-f:] *= np.linspace(1, 0, f)
        fname = f"{ln['id']}_{ln['who']}.wav"
        sf.write(str(out / fname), seg, sr, subtype="PCM_16")
        preview.append(seg)

        # 確からしさと印
        flags = []
        words = {}
        for k in range(d["tj0"], d["tj1"] + 1):
            words.setdefault(tchars[k]["wi"], tchars[k]["w"])
        heard = "".join(words.values())
        match = d["cov"]
        weak = False
        truncated = False
        miss = d.get("tail_missing", 0)
        if miss >= 2:
            # 書き起こしに語尾が出ていなくても、その長さの声が残っていれば途切れではない
            voiced_after = sum(min(r[1], sp[1]) - max(r[0], d["e"]) for r in regs if r[1] > d["e"] and r[0] < sp[1])
            if voiced_after >= 0.6 * 0.09 * miss:
                flags.append("語尾が書き起こしに出ない（声はある）")
                weak = True
            else:
                flags.append("語尾の声がない（途切れの疑い）")
                truncated = True
        gap_next = next_start - sp[1] if li + 1 < len(spans) and spans[li + 1] else 9.0
        if gap_next < 0.15:
            if cut_level(db, sp[1]) > -6:
                flags.append("次の台詞がすぐ始まり、語尾が急に切れている（途切れの疑い）")
                truncated = True
            else:
                flags.append("次の台詞との間が短い")
        if d.get("head_missing", 0) >= 2 and not d.get("added"):
            flags.append("頭が書き起こしに出ない")
            weak = True
        if d.get("joined") or d.get("joined_prev"):
            flags.append("隣の台詞と声が続いていて、境目は推定")
            weak = True
        if d.get("added"):
            flags.append("書き起こしにない声を頭か末尾に含めた（" + "、".join(fmt_tc(t0 + r[0]) for r in d["added"]) + "）")
        loud = [r for r, pk in zip(regs, peak) if pk >= loud_thr and sp[0] <= r[0] and r[1] <= sp[1]]
        for k in range(len(loud) - 1):
            g = loud[k + 1][0] - loud[k][1]
            if g > 1.0:
                flags.append(f"途中に{g:.1f}秒の無音（{fmt_tc(t0 + loud[k][1])}）")
        if match < 0.9:
            flags.append(f"書き起こしと脚本の読みが一部違う（一致 {match:.3f}。下の表で比べる）")
        if d["p"] < 0.6:
            flags.append(f"書き起こしの確からしさが低い（{d['p']:.2f}）")
        if match >= 0.9 and d["p"] >= 0.6 and not truncated and not weak:
            mark = "◎"
        elif match >= 0.8 and not truncated:
            mark = "○"
        else:
            mark = "△"
        rows.append({"id": ln["id"], "who": who, "file": fname,
                     "start": fmt_tc(t0 + cs), "end": fmt_tc(t0 + ce), "dur": f"{ce - cs:.2f}",
                     "match": f"{match:.2f}", "mark": mark, "cut": "途切れ?" if truncated else "",
                     "flags": "／".join(flags), "heard": heard.strip(), "text": ln["text"]})

    with (out / "lines.csv").open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    if preview:
        gap = np.zeros(int(0.5 * sr))
        allseg = np.concatenate([np.concatenate([s, gap]) for s in preview])
        tmpwav = work / "preview.wav"
        sf.write(str(tmpwav), allseg, sr, subtype="PCM_16")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmpwav), "-c:a", "aac",
                        "-b:a", "128k", str(out / f"{spec['scene']}_original_all.m4a")], check=True)

    total = sum(float(r["dur"]) for r in rows if r["dur"])
    # 一覧に残すのは、最初の台詞から次のシーンの台詞の頭までにある音だけ
    lo = min(sp[0] for sp in spans[:len(lines)] if sp)
    hi = spans[-1][0] if spec.get("next_line") and spans[-1] else len(mono) / sr
    unassigned = [u for u in unassigned if lo <= u[0][0] and u[0][1] <= hi]
    report = {
        "rows": rows, "total": total, "thr": thr, "speech": float(speech),
        "unassigned": [[fmt_tc(t0 + r[0]), fmt_tc(t0 + r[1]), round(pk, 1), why] for r, pk, why in unassigned],
        "next_line": ({"start": fmt_tc(t0 + spans[-1][0])} if spec.get("next_line") and spans[-1] else None),
    }
    (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(out / "lines.md", spec, rows, report, a)
    for r in rows:
        print(r["id"], r["mark"], r["cut"], r["start"], r["end"], r["dur"], r["match"], r["flags"], "|", r["heard"])
    print("合計", f"{total:.1f}秒")
    for u in report["unassigned"]:
        print("  台詞に入れなかった音:", u)
    print("次の台詞の頭:", report["next_line"])


def write_md(path, spec, rows, report, a):
    src = spec.get("source", {})
    marks = {m: sum(1 for r in rows if r["mark"] == m) for m in ("◎", "○", "△", "×")}
    cut = sum(1 for r in rows if r["cut"])
    L = [f"# {spec['scene']} 原作の台詞（{rows[0]['id']}〜{rows[-1]['id']}）", "",
         f"原作音源パート{src.get('part', '?')}の {fmt_tc(parse_tc(a.start))} から切り出し（`tools/extract_lines.py`、D-045）。"
         f"声の分離は Demucs `{a.demucs}`、書き起こしは Whisper `{a.whisper}`。",
         "時刻は原作音源パートの中の位置。ファイルは声だけ（BGM・効果音を除いたもの）、モノラル 44.1kHz。", "",
         f"- 台詞 {len(rows)} 本、合計 {report['total']:.1f} 秒（台詞と台詞の間は含まない）",
         f"- 印: ◎ {marks['◎']}、○ {marks['○']}、△ {marks['△']}、見つからない {marks['×']}、途切れの疑い {cut}",
         f"- 通して聞く: `{spec['scene']}_original_all.m4a`（台詞の間を0.5秒にそろえて並べたもの）", "",
         "| ID | 話者 | 開始 | 終了 | 長さ | 一致 | 印 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        mark = r["mark"] + (" 途切れ?" if r["cut"] else "")
        name = f"[{r['id']}]({r['file']})" if r["file"] else r["id"]
        L.append(f"| {name} | {r['who']} | {r['start']} | {r['end']} | {r['dur']} | {r['match']} | {mark} |")
    L += ["", "## 印の意味", "",
          "- **一致**: 脚本の読み（ひらがな）のうち、Whisper の書き起こしと合った文字の割合。"
          "「キヒヒ」「あん？」「う、」「フム」は Whisper が文字にしないので、声は入っていても一致が下がる。",
          "- **◎** 一致 0.90 以上で、語尾や境目に気になる点がない。",
          "- **○** 声はそろっているが、語尾が書き起こしに出ない・境目を推定したなど、聞いて確かめたい。",
          "- **△** 自信がない。聞いて確かめる。",
          "- **途切れ?** 語尾が途中で切れている疑い（語尾の声がない、または次の台詞がすぐ始まり声が急に切れている）。", "",
          "## 台詞ごとの注記", ""]
    for r in rows:
        if r["flags"]:
            L.append(f"- **{r['id']}** {r['flags']}")
    L += ["", "## 書き起こし（Whisper）と脚本", "",
          "| ID | 書き起こし | 脚本 |", "| --- | --- | --- |"]
    for r in rows:
        L.append(f"| {r['id']} | {r['heard']} | {r['text']} |")
    if report["unassigned"]:
        L += ["", "## 台詞に入れなかった音", "",
              "声の区間のうち、どの台詞にも含めなかったもの（息、物音、百騎兵の声などの可能性）。", "",
              "| 開始 | 終了 | 大きさ (dB) | 理由 |", "| --- | --- | --- | --- |"]
        for u in report["unassigned"]:
            L.append(f"| {u[0]} | {u[1]} | {u[2]} | {u[3]} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
