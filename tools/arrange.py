#!/usr/bin/env python3
"""原作の BGM を作り直した編曲（D-078）を、編曲の指定（arrangement.yaml）から MIDI にして鳴らす。

  $MUS/bin/python tools/arrange.py audio/bgm/arranged/M01/arrangement.yaml

指定の作り方: tools/bgm_analyze.py の下書き（draft.yaml。小節ごとの和音と旋律）を、Claude が見て直したもの。
  bars: {小節: {chords: [2拍ずつの和音], mel: "16分音符16個（音名＝鳴らす、-＝のばす、.＝休み）", bass: [任意。低音の音名]}}
  order: 鳴らす小節の並び（例 [27-34, 1-26]）。1周（ループ）はこの並び
  sections: 強弱（pp・p・mp・mf・f、"p>mf" は次第に強く）
  ending: 1周のあとに足す終わり（和音の並びと、ゆるめる割合）
  style: piano（左手は2拍ごとの分散和音、右手は旋律と、長い音の下に和音の音。旋律のない2拍は右手で和音をそっと置く）

出力（指定と同じフォルダ）:
  <id>_<style>_<version>.mid / .mp3   1周＋終わり（聞く用）
  <id>_<style>_<version>_loop.ogg     切れ目なくくり返せる1周（場面に敷く用。前の周の余韻を頭に重ねてある）
音色は MuseScore General（apt の musescore-general-soundfont）、鳴らすのは fluidsynth。音量は平均 -22dB（既存のループと同じ）。
"""
import argparse
import pathlib
import random
import re
import subprocess
import tempfile

import numpy as np
import yaml

NAMES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
QUALS = {'': (0, 4, 7), 'm': (0, 3, 7), 'dim': (0, 3, 6), 'sus4': (0, 5, 7), 'sus2': (0, 2, 7), 'aug': (0, 4, 8),
         '7': (0, 4, 7, 10), 'm7': (0, 3, 7, 10), 'maj7': (0, 4, 7, 11), 'm7b5': (0, 3, 6, 10), 'dim7': (0, 3, 6, 9),
         'add9': (0, 4, 7, 14), 'madd9': (0, 3, 7, 14)}
DYN = {'pp': 38, 'p': 50, 'mp': 61, 'mf': 72, 'f': 86}
SF = '/usr/share/sounds/sf3/MuseScore_General.sf3'


def pc(name):
    p = NAMES[name[0]]
    for ch in name[1:]:
        p += {'b': -1, '#': 1}.get(ch, 0)
    return p % 12


def midi(note):
    m = re.fullmatch(r'([A-G][b#]?)(-?\d)', note)
    return pc(m.group(1)) + 12 * (int(m.group(2)) + 1)


def chord(sym):
    """'Ebmaj7' / 'Cm' / 'Fm7/Eb' → (根音の音高クラス, [音程], 低音の音高クラス)"""
    base, _, slash = sym.partition('/')
    m = re.fullmatch(r'([A-G][b#]?)(.*)', base)
    root = pc(m.group(1))
    ivs = QUALS[m.group(2)]
    return root, list(ivs), pc(slash) if slash else root


def expand_order(order):
    out = []
    for item in order:
        a, _, b = str(item).partition('-')
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def place(pcl, lo, hi):
    """音高クラスを lo〜hi の中の一番低い高さに"""
    m = lo + (pcl - lo) % 12
    return m if m <= hi else m - 12


class Score:
    def __init__(self, bpm, seed=7, period=None):
        self.beat = 60.0 / bpm
        self.notes = []   # (start, end, pitch, vel, hand)
        self.pedal = []   # (time, value)
        self.seed = seed
        self.period = period

    def add(self, s, e, p, v, hand):
        # 人の弾くような小さな揺れ。1周の中の位置で決めるので、どの周も同じ（ループの継ぎ目がそろう）
        pos = round(s % self.period, 3) if self.period else round(s, 3)
        rng = random.Random(f"{self.seed}:{pos}:{p}:{hand}")
        j = rng.uniform(-0.008, 0.008)
        v = int(np.clip(v + rng.randint(-5, 5), 15, 120))
        self.notes.append((max(0.0, s + j), e + j, int(p), v, hand))


def vel_at(sections, bar_idx, nbars_total):
    for s in sections:
        if s['_a'] <= bar_idx <= s['_b']:
            d = str(s['dyn'])
            if '>' in d:
                a, b = d.split('>')
                f = (bar_idx - s['_a']) / max(1, s['_b'] - s['_a'])
                return DYN[a] + (DYN[b] - DYN[a]) * f
            return DYN[d]
    return DYN['mp']


def build(spec, passes=1, with_ending=True):
    bpm = float(spec['bpm'])
    bpb = int(spec.get('beats_per_bar', 4))
    order = expand_order(spec['order'])
    sc = Score(bpm, spec.get('seed', 7), period=len(order) * bpb * 60.0 / bpm)
    B = sc.beat
    bars = {int(k): v for k, v in spec['bars'].items()}
    seq = order * passes
    # 強弱: 並びの中の位置で
    secs = []
    for s in spec.get('sections', []):
        idx = [i for i, b in enumerate(order) if s['from'] <= b <= s['to']]
        secs.append({**s, '_a': min(idx), '_b': max(idx)})
    t = 0.0
    held = None   # 小節をまたいでのびる右手の音 [start, pitch, vel]
    for i, bno in enumerate(seq):
        bar = bars[bno]
        dyn = vel_at(secs, i % len(order), len(order))
        chords = bar['chords']
        half = bpb // len(chords) if len(chords) else bpb
        toks = bar['mel'].split()
        assert len(toks) == 16, f"小節 {bno} の旋律が16個でない"
        slot = bpb * B / 16
        # 右手の旋律
        mel_events = []
        for k, tok in enumerate(toks):
            if tok == '-':
                continue
            if held is not None:
                mel_events.append((held[0], t + k * slot, held[1], held[2]))
                held = None
            if tok == '.':
                continue
            held = [t + k * slot, midi(tok), dyn + 8]
        # 小節の終わりで、次の小節の頭が '-' でなければ切る
        nxt = seq[i + 1] if i + 1 < len(seq) else None
        if held is not None and (nxt is None or bars[nxt]['mel'].split()[0] != '-'):
            mel_events.append((held[0], t + bpb * B, held[1], held[2]))
            held = None
        for s, e, p, v in mel_events:
            sc.add(s, e - 0.02, p, v, 'rh')
        # 左手と、旋律のない2拍の右手の和音
        for j, sym in enumerate(chords):
            root, ivs, bass_pc = chord(sym)
            h0 = t + j * half * B
            pcs = [(root + x) % 12 for x in ivs]
            bass_name = (bar.get('bass') or [None] * len(chords))[j]
            hint = (bar.get('bass_hint') or [None] * len(chords))[j]
            if not bass_name and hint and bass_pc == root and pc(hint[:-1]) in pcs:
                b = place(pc(hint[:-1]), 36, 47)    # 推定の低音が和音の音なら転回形に
            else:
                b = midi(bass_name) if bass_name else place(bass_pc, 36, 47)
            r = place(root, b, b + 11)              # 根音（低音が転回のときは低音より上）
            third = ivs[1]                          # 3度（sus4 なら4度）
            if len(ivs) > 3 and ivs[3] < 12:        # 7の和音: 低音・5度・7度・10度
                arc = [b, r + 7, r + ivs[3], r + 12 + third]
            else:                                   # 3和音: 低音・5度・10度・5度
                arc = [b, r + 7, r + 12 + third, r + 7]
            for q in range(half * 2):               # 8分音符で
                p = arc[q % 4] if (q < 4 or q % 4) else r + 12
                sc.add(h0 + q * B / 2, h0 + half * B - 0.03, p, dyn - (0 if q == 0 else 10), 'lh')
            sc.pedal += [(h0 + 0.03, 127), (h0 + half * B - 0.01, 0)]
            # 旋律がこの2拍に1つもないとき、右手で和音をそっと置く
            sl = toks[j * 16 * half // bpb:(j + 1) * 16 * half // bpb]
            sounding = any(x != '.' for x in sl)
            if not sounding:
                voic = sorted({place(x, 60, 71) for x in pcs[:4]})   # 7の和音は7度も
                for p in voic:
                    sc.add(h0 + 0.02, h0 + half * B - 0.03, p, dyn - 14, 'pad')
        # 長い旋律の音の下に和音の音を1つ
        for s, e, p, v in mel_events:
            if s < t - 1e-6 or e - s < B * 0.95 or abs(((s - t) / B) - round((s - t) / B)) > 0.05:
                continue
            j = min(int((s - t) / (half * B)), len(chords) - 1)
            root, ivs, _ = chord(chords[j])
            cands = [p - d for d in range(3, 10) if (p - d) % 12 in [(root + x) % 12 for x in ivs] and p - d >= 55]
            if cands:
                sc.add(s + 0.01, e - 0.02, cands[0], v - 16, 'rh2')
        t += bpb * B
    if held is not None:
        sc.add(held[0], t, held[1], held[2], 'rh')
    loop_end = t
    if with_ending and spec.get('ending'):
        en = spec['ending']
        rit = float(en.get('rit', 0.85))
        for k, sym in enumerate(en['chords']):
            beats = float(en.get('beats', [2] * len(en['chords']))[k])
            stretch = 1 / (rit ** (k + 1))
            dur = beats * B * stretch
            root, ivs, bass_pc = chord(sym)
            last = k == len(en['chords']) - 1
            dyn = DYN[en.get('dyn', 'p')]
            b = place(bass_pc, 36, 47)
            lh = [b, b + 12] if last else [b, b + 7, b + 12 + ivs[1]]
            for n, p in enumerate(lh):
                sc.add(t + n * 0.06, t + dur + (2.5 if last else 0), p, dyn, 'lh')
            rh = sorted(place((root + x) % 12, 63, 74) for x in ivs[:3])
            if last:
                rh = rh + [rh[0] + 12]
            for n, p in enumerate(rh):
                sc.add(t + 0.1 + n * 0.07, t + dur + (2.5 if last else 0), p, dyn - 4, 'rh')
            sc.pedal += [(t + 0.03, 127), (t + dur + (3.0 if last else 0) - 0.01, 0)]
            t += dur
    return sc, loop_end, t


def write_midi(sc, path, bpm):
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    inst = pretty_midi.Instrument(program=0, name='Piano')
    for s, e, p, v, _ in sorted(sc.notes):
        inst.notes.append(pretty_midi.Note(velocity=v, pitch=p, start=s, end=max(e, s + 0.05)))
    for tm, val in sorted(sc.pedal):
        inst.control_changes.append(pretty_midi.ControlChange(number=64, value=val, time=max(0.0, tm)))
    pm.instruments.append(inst)
    pm.write(str(path))


def render(mid, wav):
    subprocess.run(['fluidsynth', '-ni', '-g', '0.7', '-r', '44100', '-o', 'synth.reverb.active=1',
                    '-o', 'synth.reverb.room-size=0.55', '-o', 'synth.reverb.level=0.5',
                    '-F', str(wav), SF, str(mid)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def loudness_gain(wav, target=-22.0, peak_max=-1.5):
    r = subprocess.run(['ffmpeg', '-v', 'info', '-i', str(wav), '-af', 'volumedetect', '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    mean = float(re.search(r'mean_volume: (-?[\d.]+)', r).group(1))
    peak = float(re.search(r'max_volume: (-?[\d.]+)', r).group(1))
    return min(target - mean, peak_max - peak)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('spec')
    args = ap.parse_args()
    spec_path = pathlib.Path(args.spec)
    spec = yaml.safe_load(spec_path.read_text(encoding='utf-8'))
    out = spec_path.parent
    stem = f"{spec['id']}_{spec.get('style', 'piano')}_{spec.get('version', 'v01')}"
    bpm = float(spec['bpm'])
    with tempfile.TemporaryDirectory() as td:
        # 聞く用: 1周＋終わり
        sc, loop_end, end = build(spec, passes=1, with_ending=True)
        write_midi(sc, out / f"{stem}.mid", bpm)
        render(out / f"{stem}.mid", f"{td}/full.wav")
        g = loudness_gain(f"{td}/full.wav")
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', f"{td}/full.wav", '-af',
                        f"volume={g:.2f}dB,afade=t=out:st={end + 2.0:.2f}:d=2.5", '-t', f"{end + 4.6:.2f}",
                        '-c:a', 'libmp3lame', '-b:a', '192k', str(out / f"{stem}.mp3")], check=True)
        # 場面に敷く用: 2周鳴らして2周目を切り出す（1周目の余韻が頭に重なり、切れ目なく回る）
        sc2, _, _ = build(spec, passes=3, with_ending=False)
        write_midi(sc2, f"{td}/loop.mid", bpm)
        render(f"{td}/loop.mid", f"{td}/loop3.wav")
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f"{loop_end:.6f}", '-t', f"{loop_end:.6f}",
                        '-i', f"{td}/loop3.wav", '-af', f"volume={g:.2f}dB", '-c:a', 'libopus', '-b:a', '128k',
                        str(out / f"{stem}_loop.ogg")], check=True)
    print(f"1周 {loop_end:.3f}秒（{len(expand_order(spec['order']))}小節）、終わりまで {end:.1f}秒、音量 {g:+.1f}dB")
    print(f"→ {out / stem}.mid / .mp3 / _loop.ogg")


if __name__ == '__main__':
    main()
