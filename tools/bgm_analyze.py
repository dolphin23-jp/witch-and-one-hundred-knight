#!/usr/bin/env python3
"""原作の BGM（プレイ録画から声を分けた残り）の1曲を解析して、作り直し（編曲、D-078）の下書きを作る。

  $MUS/bin/python tools/bgm_analyze.py --id M01 --src tmp/src/part20_bgm.mp3 --from 4:36 --to 9:02 \
      --out audio/bgm/arranged/M01

やること:
  1. 区間を切り出し、ループの1周の長さを測る（クロマの似かたで目安 → 波形の相互相関でサンプル単位）。
     ゲームの BGM は同じ音源のくり返しなので、1周の長さは区間のどこで測ってもそろう。
  2. 何周分もの演奏の時刻を合わせて重ね、時間と高さの区画ごとに「中央値に近い演奏」を選んで、
     効果音・声の漏れの少ない1周（clean_loop.wav）を作る（たまにしか入らない音が消える）。
  3. 拍（1周の拍の数とずれ）、2拍ごとの和音、低音、16分音符ごとの旋律（Essentia の Melodia）を出す。
     時間に対して変わらない音（低いうなりなど）は引いてから和音を見る。
  4. 編曲の下書き（draft.yaml。小節ごとの和音と旋律）と、確かめるための図（analysis.png）を書く。
     下書きは自動の推定なので誤りがある。人（Claude）が見て直したものを arrangement.yaml にし、tools/arrange.py で鳴らす。

出力（--out）: clean_loop.wav（Git に入れない）、draft.yaml、analysis.png、analysis.json
重い作業用の venv（python3.11）: pip install essentia librosa soundfile pyyaml matplotlib
"""
import argparse
import json
import pathlib
import subprocess
import tempfile

import numpy as np
import yaml

NAMES = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
QUALS = {'': (0, 4, 7), 'm': (0, 3, 7), 'dim': (0, 3, 6), 'sus4': (0, 5, 7), '7': (0, 4, 7, 10),
         'm7': (0, 3, 7, 10), 'maj7': (0, 4, 7, 11), 'm7b5': (0, 3, 6, 10)}
MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def sec(s):
    if ':' in str(s):
        m, x = str(s).split(':')
        return int(m) * 60 + float(x)
    return float(s)


def note_name(m):
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def extract(src, a, b, path):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(a), '-to', str(b), '-i', src,
                    '-ac', '2', '-ar', '44100', path], check=True)


def find_period(mono, sr, approx=None):
    import librosa
    y = librosa.resample(mono, orig_sr=sr, target_sr=22050)
    s2 = 22050
    if approx is None:
        C = librosa.feature.chroma_cqt(y=y, sr=s2, hop_length=512)
        X = librosa.util.normalize(C, axis=0)
        fps = s2 / 512
        best = (-1, None)
        for lag in np.arange(10, min(140, len(y) / s2 / 2), 0.05):
            L = int(round(lag * fps))
            v = (X[:, :-L] * X[:, L:]).sum(0).mean()
            if v > best[0] * 1.01:      # 同じくらいなら短い方（2周分を1周と取らない）
                best = (v, lag)
        approx = best[1]

    def refine(t0, approx, sig, r, win=6.0, slack=1.0):
        a = sig[int(t0 * r):int((t0 + win) * r)]
        s0 = int((t0 + approx - slack) * r)
        b = sig[s0:s0 + len(a) + int(2 * slack * r)]
        if len(b) < len(a) + 1:
            return None
        c = np.correlate(b, a, mode='valid')
        return (s0 + int(np.argmax(c))) / r - t0

    dur = len(mono) / sr
    starts = [t for t in np.linspace(2, dur - approx - 8, 6) if t > 0]
    coarse = [refine(t, approx, y, s2) for t in starts]
    coarse = float(np.median([c for c in coarse if c]))
    fine = [refine(t, coarse, mono, sr, slack=0.01) for t in starts]
    return float(np.median([f for f in fine if f]))


def median_loop(y, sr, P, nfft=4096, hop=1024):
    """y: (ch, n)。P: 1周のサンプル数。何周分もの演奏から、区画ごとに中央値に近い演奏を選ぶ。"""
    import librosa
    m = 2 * nfft
    reps = []
    r = 0
    while r * P < y.shape[1]:
        a, b = r * P - m, (r + 1) * P + m
        pad = np.zeros((y.shape[0], P + 2 * m))
        valid = np.zeros(P + 2 * m, bool)
        lo, hi = max(a, 0), min(b, y.shape[1])
        if hi - lo < P * 0.15 + m:
            break
        pad[:, lo - a:hi - a] = y[:, lo:hi]
        valid[lo - a:hi - a] = True
        reps.append((pad, valid))
        r += 1
    Z, OK = [], []
    for pad, valid in reps:
        z = np.stack([librosa.stft(pad[c], n_fft=nfft, hop_length=hop) for c in range(pad.shape[0])])
        ok = np.array([f * hop - nfft // 2 >= 0 and f * hop + nfft // 2 <= len(valid)
                       and valid[f * hop - nfft // 2:f * hop + nfft // 2].all() for f in range(z.shape[2])])
        Z.append(z)
        OK.append(ok)
    Z, OK = np.stack(Z), np.stack(OK)
    mag = np.abs(Z).mean(1)
    magn = np.where(OK[:, None, :], mag, np.nan)
    with np.errstate(all='ignore'):
        med = np.nanmedian(magn, axis=0)
    dev = np.abs(np.log(magn + 1e-7) - np.log(med + 1e-7))
    dev = np.where(np.isnan(dev), np.inf, dev)
    pick = np.argmin(dev, axis=0)
    out = np.take_along_axis(Z, pick[None, None].repeat(Z.shape[1], 1), axis=0)[0]
    yo = np.stack([librosa.istft(out[c], hop_length=hop, n_fft=nfft, length=P + 2 * m) for c in range(out.shape[0])])
    return yo[:, m:m + P], len(reps)


def beat_grid(y, sr, P, bpb, lo=90, hi=200):
    import librosa
    oenv = librosa.onset.onset_strength(y=np.tile(y, 2), sr=sr, hop_length=256)
    fps = sr / 256
    best = (-1, 0, 0)
    for nb in range(int(P * lo / 60), int(P * hi / 60) + 1):     # 倍・半分の取り違えは --bpm で直す
        if nb % bpb:
            continue
        L = P / nb
        for ph in np.arange(0, L, 0.005):
            v = oenv[((np.arange(nb) * L + ph) * fps).astype(int)].mean()
            if v > best[0]:
                best = (v, nb, ph)
    return best[1], P / best[1], best[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--id', required=True)
    ap.add_argument('--src', required=True, help='BGMと効果音の分離音（mp3）')
    ap.add_argument('--from', dest='a', required=True)
    ap.add_argument('--to', dest='b', required=True)
    ap.add_argument('--period', type=float, help='1周の秒（目安）。なければ探す')
    ap.add_argument('--beats-per-bar', type=int, default=4)
    ap.add_argument('--bpm', default='90-200', help='探すテンポの範囲（倍・半分の取り違えを直すとき）')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    import librosa
    import soundfile as sf
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = f"{td}/src.wav"
        extract(args.src, sec(args.a), sec(args.b), wav)
        y, sr = sf.read(wav, always_2d=True)
    y = y.T
    period = find_period(y.mean(0), sr, args.period)
    P = int(round(period * sr))
    loop, nrep = median_loop(y, sr, P)
    sf.write(out / 'clean_loop.wav', loop.T, sr, subtype='PCM_24')
    print(f"1周 {period:.4f}秒、重ねた演奏 {nrep}（最後は途中まで）")

    mono = librosa.resample(loop.mean(0), orig_sr=sr, target_sr=22050)
    s2, hop = 22050, 256
    fps = s2 / hop
    lo, hi = (float(x) for x in args.bpm.split('-'))
    nb, L, ph = beat_grid(mono, s2, period, args.beats_per_bar, lo, hi)
    bpb = args.beats_per_bar
    nbar = nb // bpb
    print(f"{nb}拍（{nbar}小節）、{60 / L:.2f} BPM、拍の頭 {ph:.3f}秒")

    # 調
    yy = np.tile(mono, 2)
    X = np.abs(librosa.cqt(yy, sr=s2, fmin=librosa.note_to_hz('C1'), n_bins=36 * 7, bins_per_octave=36, hop_length=hop))
    X = np.maximum(X - np.percentile(X, 30, axis=1, keepdims=True), 0)   # 変わらない音（うなり）を引く
    S = X[1::3]                                                          # 半音ごと、C1（MIDI 24）から
    chroma_all = np.zeros(12)
    for i, v in enumerate(S[24:60].mean(1)):
        chroma_all[i % 12] += v
    keys = sorted([(np.corrcoef(chroma_all, np.roll(MAJ, k))[0, 1], k, 'major') for k in range(12)]
                  + [(np.corrcoef(chroma_all, np.roll(MIN, k))[0, 1], k, 'minor') for k in range(12)], reverse=True)
    kroot, kmode = keys[0][1], keys[0][2]
    tonic_minor = kroot if kmode == 'minor' else (kroot + 9) % 12
    scale = {(tonic_minor + i) % 12 for i in (0, 2, 3, 5, 7, 8, 10, 11)}   # 短調の音階＋導音
    diat = {(tonic_minor + i) % 12 for i in (0, 2, 3, 5, 7, 8, 10)}
    print(f"調 {NAMES[kroot]} {kmode}（音階 {[NAMES[p] for p in sorted(scale)]}）")

    tmpl = {}
    for r in range(12):
        for q, iv in QUALS.items():
            pcs = [(r + i) % 12 for i in iv]
            v = np.zeros(12)
            v[pcs] = 1
            bonus = 0.04 if all(p in diat or (p == (tonic_minor + 11) % 12) for p in pcs) else 0.0
            tmpl[NAMES[r] + q] = (v / np.linalg.norm(v), bonus, pcs)

    def fr(a, b):
        return slice(int(a * fps), max(int(b * fps), int(a * fps) + 1))

    halves = []
    for h in range(nb // 2):
        a = ph + h * 2 * L
        c = S[24:60, fr(a, a + 2 * L)].mean(1)
        ch = np.zeros(12)
        for i, v in enumerate(c):
            ch[i % 12] += v
        ch /= np.linalg.norm(ch) + 1e-9
        sc = sorted(((float(ch @ t) + bo, k) for k, (t, bo, _) in tmpl.items()), reverse=True)
        low = S[7:31, fr(a, a + L)].mean(1)          # G1〜F#3、半分の頭の1拍
        halves.append({'chord': sc[0][1], 'score': round(sc[0][0], 2), 'alt': sc[1][1], 'bass': int(np.argmax(low)) + 31})

    # 旋律（Melodia）
    import essentia.standard as es
    a44 = np.tile(librosa.resample(loop.mean(0), orig_sr=sr, target_sr=44100).astype(np.float32), 2)
    f0, conf = es.PredominantPitchMelodia(frameSize=2048, hopSize=128, minFrequency=180, maxFrequency=1600,
                                          voicingTolerance=0.2, guessUnvoiced=False)(es.EqualLoudness()(a44))
    t = np.arange(len(f0)) * 128 / 44100
    m = np.where(f0 > 0, 69 + 12 * np.log2(np.maximum(f0, 1) / 440), np.nan)
    L16 = L / 4
    mel = []
    for k in range(nb * 4):
        a = ph + k * L16
        sel = (t >= a) & (t < a + L16)
        v = m[sel]
        v = v[~np.isnan(v)]
        mel.append(int(round(np.median(v))) if len(v) and len(v) >= 0.5 * sel.sum() else None)

    # 音階の外の音は、その時の和音の音（半音以内）か、近い音階の音へ
    def snap(p, chord_pcs):
        if p % 12 in scale:
            return p
        for d in (-1, 1):
            if (p + d) % 12 in chord_pcs:
                return p + d
        for d in (-1, 1):
            if (p + d) % 12 in scale:
                return p + d
        return p

    for k, p in enumerate(mel):
        if p is not None:
            mel[k] = snap(p, tmpl[halves[min(k // 8, len(halves) - 1)]['chord']][2])
    # 16分1つだけの孤立した音は消す（前の音をのばす）
    for k in range(1, len(mel) - 1):
        if mel[k] is not None and mel[k - 1] != mel[k] and mel[k + 1] != mel[k] and \
                (mel[k - 1] is None or abs(mel[k] - mel[k - 1]) >= 5):
            mel[k] = mel[k - 1]

    bars = {}
    for bi in range(nbar):
        toks = []
        prev = mel[bi * 16 - 1] if bi else None
        for k in range(bi * 16, bi * 16 + 16):
            p = mel[k]
            if p is None:
                toks.append('.')
            elif p == prev and k % 16 != 0:
                toks.append('-')
            else:
                toks.append(note_name(p))
            prev = p
        hs = halves[bi * bpb // 2:(bi + 1) * bpb // 2]
        bars[bi + 1] = {'chords': [h['chord'] for h in hs], 'bass': [note_name(h['bass']) for h in hs],
                        'mel': ' '.join(toks)}

    draft = {
        'id': args.id,
        'note': '自動の下書き（tools/bgm_analyze.py）。和音は2拍ずつ、旋律は16分音符16個（音名＝鳴らす、-＝のばす、.＝休み）。'
                '低音は2拍の頭の1拍で一番強い低い音（打楽器でずれることがある）。直したものを arrangement.yaml にする',
        'source': {'file': str(args.src), 'from': args.a, 'to': args.b},
        'loop': {'sec': round(period, 6), 'beats': nb, 'bars': nbar, 'beats_per_bar': bpb,
                 'bpm': round(60 / L, 3), 'first_beat_sec': round(float(ph), 4)},
        'key': f"{NAMES[kroot]} {kmode}",
        'bars': bars,
    }
    (out / 'draft.yaml').write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False, width=200), encoding='utf-8')
    json.dump({'halves': halves, 'mel16': mel, 'period': period, 'beats': nb, 'phase': ph},
              open(out / 'analysis.json', 'w'))

    # 確かめる図: 旋律の推定（赤）と和音の推定
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = int(np.ceil(nbar / 9))
    fig, axs = plt.subplots(rows, 1, figsize=(22, 3.6 * rows))
    axs = np.atleast_1d(axs)
    sal = S[36:72]       # C3..B5
    for r, ax in enumerate(axs):
        b0, b1 = r * 9, min((r + 1) * 9, nbar)
        t0, t1 = ph + b0 * bpb * L, ph + b1 * bpb * L
        ax.imshow(np.log(sal[:, fr(t0, t1)] + 1e-3), aspect='auto', origin='lower', cmap='Greys',
                  extent=[t0, t1, 47.5, 83.5])
        sel = (t >= t0) & (t <= t1)
        ax.plot(t[sel], m[sel], 'r.', ms=1.5)
        for bi in range(b0, b1):
            x = ph + bi * bpb * L
            ax.axvline(x, color='b', lw=1)
            ax.text(x + 0.02, 81.5, f"{bi + 1}", color='b', fontsize=11)
            for j, c in enumerate(bars[bi + 1]['chords']):
                ax.text(x + j * 2 * L + 0.05, 49, c, color='darkgreen', fontsize=10)
        ax.set_xlim(t0, t1)
        ax.set_yticks(range(48, 84, 6))
        ax.set_yticklabels([note_name(v) for v in range(48, 84, 6)])
    plt.tight_layout()
    plt.savefig(out / 'analysis.png', dpi=55)
    print(f"→ {out}/draft.yaml, analysis.png")


if __name__ == '__main__':
    main()
