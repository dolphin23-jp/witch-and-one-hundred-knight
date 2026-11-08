#!/usr/bin/env python3
"""構図の洗い出し（2026-11-07、下絵のアップグレードの続き）。tools/previs.py --audit の測定（gen/framing.yaml）を読んで、
話し手やカメラの見る先の顔が写っていない・切れている・隠れている・大きさがカットの画の大きさ（寄り・胸上・膝上・全身・引き）と合わない所を洗い出す。

  python tools/framing.py episodes/A/scenes/A12          # gen/framing_report.md と gen/framing_issues.yaml を書く
  python tools/framing.py episodes/A/scenes/A12 --quiet  # 1行の数だけ

見る先（主役）: そのコマのカメラの look が人物ならその人。そうでなければ、そのとき話している人（映っていれば）。
問題の種類:
  顔が外   主役の顔の真ん中が画面の外（コマの3割以上）
  顔が隠れ 主役の顔の前を物（ほかの人物・家具・壁）がふさぐ（コマの3割以上）。本人の帽子のつばは、寄り・胸上・膝上のときだけ数える
  頭が切れ 頭のてっぺんが画面の上で大きく切れる（寄りは 1.15、ほかは 1.06 を超える）
  足が切れ 全身なのに足元が画面の下で切れる
  大きさ   画の大きさと、顔（寄り・胸上）か体（膝上・全身・引き）の大きさが合わない
  話し手が見えない 台詞の間ずっと、話し手（映っている人）の顔が見えず、カメラもほかの人を見ていない
  顔が向こう向き 寄り・胸上・膝上で、主役の顔がカメラの反対を向いている（顔の向きとカメラの角度 105 度より大きい、コマの半分以上）。
                 カットの説明（画・芝居・カメラ・姿勢）に、その人の背中・後ろ姿・振り向くなどとあれば数えない（2026-11-07、A08-005 で見つけた）
  顔の高さ     寄り・胸上・膝上で、主役の顔の真ん中が画面の下 3 割より下か、上 8% より上（コマの半分以上）。A31-005「胸上・巨大メタリカ」で、
               見る高さが顔より 6m 上で、顔が画面の下の端にあった（2026-11-07）
  線を越える   イマジナリーライン（180度ルール）。主役が見ている相手との二人を結ぶ線の、カメラがいる側を、カットの順に追う。
               前のカットで決まった側と反対の側から次のカットが始まると、向き合う二人が同じ向きを見ているように見える。
               線の上（線から 0.5m 以内か、二人の真ん中から見て 20 度以内）のカットと、カットの中でカメラが線を越えるカットは、そこで側を決め直す。二人が 1.5m 以上動いたら決め直す。
               説明に「回り込」「反対側」「逆側」「どんでん」などとあれば数えない。景色（二人から 2m より離れた場所）を見るキーがあるカット
               （A08b-029 のティトの横顔から村の外の砂地へ振る、など）と、カットの中で二人のどちらかが 1.5m 以上動くカットは数えず、そこで側を決め直す（2026-11-07）
"""
import argparse
import math
import pathlib
import re
import sys
import wave

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # tools/staging.py・tools/motion.py（足元の位置）

# 画の大きさの目安: 寄り・胸上は顔の縦の大きさ（画面の高さに対する割合。人の頭と背の比 0.16 のとき）、膝上・全身・引きは体（頭のてっぺん〜足元）
RULES = [("大引き", "body", 0.0, 0.32), ("寄り", "head", 0.24, 0.95), ("胸上", "head", 0.12, 0.5), ("膝上", "body", 0.6, 1.75),
         ("全身", "body", 0.15, 1.04), ("引き", "body", 0.0, 0.65)]   # 全身の下限は戦いの広めの全身（砂食みを入れる）を許す


def frame_rule(label):
    if not label:
        return None
    lab = str(label).split("→")[-1]
    for key, kind, lo, hi in RULES:
        if key in lab:
            return key, kind, lo, hi
    return None


BACK_EN = ("from behind", "back to", "from the back", "walking away", "turning away", "turns away", "facing away", "back view", "over her shoulder", "over his shoulder")
BACK_JA = ("後ろ姿", "背を向", "振り向", "振り返", "背面", "そむけ", "背け", "顔をそら", "目をそら", "そっぽ")   # A37-002「顔をそむける」
AWAY = 105.0   # 顔の向きとカメラの角度（度）。これより大きいと顔が写らない
FACE_FRAMES = ("寄り", "胸上", "膝上")


def occ_of(s, w):
    """コマ s で w の顔をふさぐ物。本人の帽子のつばは、寄り・胸上・膝上のときだけ数える（引きで帽子の上から見えるのは自然）。"""
    o = (s["people"].get(w) or {}).get("occ")
    if o and str(o).endswith("brim"):
        rule = frame_rule(s.get("frame"))
        if not rule or rule[0] not in FACE_FRAMES:
            return None
    return o


def back_ok(sh, bc, who, ja):
    """カットの説明が、その人の背中を見せると言っているか（顔が向こう向きでもよいカット）。"""
    texts = [str(sh.get("shot") or ""), str(sh.get("acting") or "")]
    cam = sh.get("camera")
    texts.append(str(cam.get("move") or "") if isinstance(cam, dict) else str(cam or ""))
    for k in ("act", "blocking"):
        v = sh.get(k)
        if isinstance(v, dict):
            texts += [str(x) for x in v.values() if isinstance(x, str)]
    if bc:
        texts.append(str(bc.get("camera") or ""))
        a = bc.get("act")
        if isinstance(a, dict):
            texts += [str(x) for x in a.values() if isinstance(x, str)]
        for k in ("pose", "pose_end"):
            pz = bc.get(k)
            if isinstance(pz, dict) and who in pz and any(w in str(pz[who]).lower() for w in BACK_EN):
                return True
    t = "\n".join(texts)
    if ja and (re.search(rf"{re.escape(ja)}の(背中|後ろ姿|背後|後ろ)", t) or re.search(rf"(^|[、・\s（]){re.escape(ja[0])}（[^）]*(背中|後ろ)", t)):
        return True
    return any(w in t for w in BACK_JA)


CROSS_OK = ("回り込", "反対側", "逆側", "どんでん", "ドンデン", "裏側", "反対から", "線を越")
LINE_NEUTRAL = 0.5   # m。カメラが二人の線からこれより近いと、線の上（どちらの側でもない）
LINE_ANGLE = 20.0    # 度。二人の真ん中から見て、カメラが線からこれより小さい角度なら、線の上（線の延長から撮る正面・背中越しの俯瞰など）
LINE_MOVED = 1.5     # m。カットの中で二人のどちらかがこれより動いたら、側を決め直す（背の高い人物は背に合わせて広げる）
LINE_GAZE = 50.0     # 度。主役の顔の向きと相手の方向の差がこれより小さければ、その人を見ている
LINE_STALE = 4       # カット。基準のカットとの間にこれより多くのカットがあれば、越えていても「要確認」（見る人は覚えていない。2026-11-08）
LINE_TURN = 30.0     # 度。カットの間に二人の線の向きがこれより回ったら、側を決め直す（2026-11-08）
LINE_SHIFT = 2.5     # m。カットの間に二人の真ん中がこれより動いたら、側を決め直す（2026-11-08）


def _bearing(dx, dy):
    return math.degrees(math.atan2(dx, -dy)) % 360.0


def _adiff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _text_of(sh, bc):
    t = [str(sh.get("shot") or ""), str(sh.get("acting") or "")]
    cam = sh.get("camera")
    t.append(str(cam.get("move") or "") if isinstance(cam, dict) else str(cam or ""))
    for v in (sh.get("act"), (bc or {}).get("act")):
        if isinstance(v, dict):
            t += [str(x) for x in v.values() if isinstance(x, str)]
    t.append(str((bc or {}).get("camera") or ""))
    return "\n".join(t)


def _xy(m, feet=False):
    """測定の人物の場面の x・y。feet なら足元（測定にあれば。なければ顔）。"""
    if feet and "bx" in m:
        return float(m["bx"]), float(m["by"])
    return float(m["wx"]), float(m["wy"])


def _side_of(A, B, cam):
    """カメラ（x, y）が、A→B の線のどちら側か（線の上は 0）。"""
    L = math.hypot(B[0] - A[0], B[1] - A[1])
    if L < 1e-6:
        return 0
    cx, cy = float(cam[0]), float(cam[1])
    d = ((B[0] - A[0]) * (cy - A[1]) - (B[1] - A[1]) * (cx - A[0])) / L
    along = ((cx - (A[0] + B[0]) / 2) * (B[0] - A[0]) + (cy - (A[1] + B[1]) / 2) * (B[1] - A[1])) / L
    if abs(d) < LINE_NEUTRAL or math.degrees(math.atan2(abs(d), abs(along))) < LINE_ANGLE:
        return 0
    return 1 if d > 0 else -1


def _line_turned(st, A, B):
    """前に側を決めたときの線と、今の線の向きの差（度）と、真ん中の動き（m）。"""
    a0 = math.degrees(math.atan2(st["B"][1] - st["A"][1], st["B"][0] - st["A"][0]))
    a1 = math.degrees(math.atan2(B[1] - A[1], B[0] - A[0]))
    m0 = ((st["A"][0] + st["B"][0]) / 2, (st["A"][1] + st["B"][1]) / 2)
    m1 = ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2)
    return _adiff(a0, a1), math.hypot(m1[0] - m0[0], m1[1] - m0[1])


def line_issues(data, shots, bk, lines, trace=None, blocks=None, feet=None):
    """イマジナリーライン: カットの順に、主役と見ている相手の線のどちら側にカメラがいるかを追い、飛び越えたカットを返す。

    2026-11-08 の直し（docs/previs_review/root_fix_phase0.md）:
      相手は、カットの中で主役が主役のコマの多数決（前は最初のコマの顔の向きだけ）。前後2カットまでに映る人から選ぶ。
      側は、すべてのコマで見る（前は最初と最後だけ）。途中で越えて戻るカットも、カットの中で越えたとみなす。
      基準のカットとの間に LINE_STALE より多くのカットがあれば「線を越える（要確認）」にする（前は何カット前でも比べた）。
      大引きで二人がともに画面に入れば、そこで側を決め直す（前は飛ばすだけ）。
      映っていない人の位置は、前のカットなら終わりのコマ、後のカットなら始めのコマ（前は真ん中のコマで、退場の途中の位置になった）。
      カットの間の置き直しでは、二人の線が LINE_TURN 度より回るか、真ん中が LINE_SHIFT m より動いたときだけ決め直す（前は、どちらかが 1.5m 動けば決め直した）。
      二人の線・動いたかは、feet（blocking から求めた足元。feet_index）があれば足元で測る（前は顔の位置で、巨大メタリカが体を傾けるだけで線が回った）。
    trace（リスト）を渡すと、カットごとの判断を dict で足す。blocks（dict）を渡すと、二人ごとの側の並び（決め直すまでの区間）を入れる。"""
    cuts = [c for c in (data.get("cuts") or {}) if (data["cuts"][c].get("samples") or [])]
    first, last = {}, {}   # カットごとの人物の位置（顔の x・y）。始めのコマと終わりのコマ
    for c in cuts:
        rows = data["cuts"][c]["samples"]
        first[c] = {w: _xy(m) for w, m in rows[0]["people"].items() if "wx" in m}
        last[c] = {w: _xy(m) for w, m in rows[-1]["people"].items() if "wx" in m}

    def where(w, i, t=0.0):   # 映っていない人は、いちばん近いカットの位置（前のカットは終わり、後のカットは始め）
        if feet is not None:
            p_ = feet(cuts[i], w, t)
            if p_ is not None:
                return p_
        for d in range(1, len(cuts)):
            j = i - d
            if 0 <= j and w in last[cuts[j]]:
                return last[cuts[j]][w]
            j = i + d
            if j < len(cuts) and w in first[cuts[j]]:
                return first[cuts[j]][w]
        return None

    everyone = sorted({w for c in cuts for w in first[c]} | {w for c in cuts for w in last[c]})
    order = {c: k for k, c in enumerate(cuts)}
    state, out = {}, {}
    seq = {}   # 二人 → [区間 [(カット, 始めの側, 終わりの側, 印)]]

    def log(pair, row, new_block=False):
        if blocks is None:
            return
        segs = seq.setdefault(pair, [])
        if new_block or not segs:
            segs.append([])
        segs[-1].append(row)

    for i, c in enumerate(cuts):
        rows = data["cuts"][c]["samples"]
        sh = shots.get(c) or {}
        tc = float(sh.get("tc", 0.0))
        txt = _text_of(sh, (bk.get("cuts") or {}).get(c))
        s0, s1 = rows[0], rows[-1]
        rule = frame_rule(s0.get("frame"))
        tr = {"cut": c} if trace is not None else None
        if rule and rule[0] == "大引き":   # 大引きで二人がともに画面に入れば、そこで側を決め直す
            for pair, st in list(state.items()):
                pa, pb = s1["people"].get(pair[0]) or {}, s1["people"].get(pair[1]) or {}
                if pa.get("in") and pb.get("in") and "wx" in pa and "wx" in pb:
                    A, B = _xy(pa), _xy(pb)
                    state[pair] = {"side": _side_of(A, B, s1["cam"]), "A": A, "B": B, "cut": c}
                    log(pair, (c, None, state[pair]["side"], "大引きで見せ直し"), new_block=True)
                    if tr is not None:
                        tr.setdefault("reestablish", []).append(list(pair))
            if tr is not None:
                tr["skip"] = "大引き"
                trace.append(tr)
            continue

        def subject(s):
            if s.get("look") in s["people"]:
                return s["look"]
            T = tc + float(s["T"])
            return next((w for (a, b, w, _i) in lines if a - 0.1 <= T <= b + 0.1 and w in s["people"]), None)

        S = subject(s0) or subject(s1)
        if not S or "fh" not in (s0["people"].get(S) or {}):
            if tr is not None:
                tr["skip"] = "主役がいない"
                trace.append(tr)
            continue
        # 相手: 主役が主役のコマごとに、顔の向きから 50 度以内の人を数える
        votes = {}
        near = {w for j in range(max(0, i - 2), min(len(cuts), i + 3)) for w in (set(first[cuts[j]]) | set(last[cuts[j]]))}   # 前後2カットまでに映る人だけ（入れ替わった人形、沼の前のテレッサなどを数えない）
        for s in rows:
            if subject(s) != S or "fh" not in (s["people"].get(S) or {}):
                continue
            pS_ = _xy(s["people"][S])
            for w in everyone:
                if w == S or w not in near:
                    continue
                q = _xy(s["people"][w]) if "wx" in (s["people"].get(w) or {}) else where(w, i, float(s["t"]))
                if q is None:
                    continue
                dist = math.hypot(q[0] - pS_[0], q[1] - pS_[1])
                if dist < 0.3 or dist > 6.0:
                    continue
                dd = _adiff(float(s["people"][S]["fh"]), _bearing(q[0] - pS_[0], q[1] - pS_[1]))
                if dd < LINE_GAZE:
                    n_, sum_ = votes.get(w, (0, 0.0))
                    votes[w] = (n_ + 1, sum_ + dd)
        if not votes:
            if tr is not None:
                tr.update({"S": S, "skip": "見ている相手がいない"})
                trace.append(tr)
            continue
        P = min(votes, key=lambda w: (-votes[w][0], votes[w][1] / votes[w][0]))
        pair = tuple(sorted((S, P)))

        def ends(s):   # そのコマの二人の位置（pair の順）。足元がわかれば足元
            def one(w):
                if feet is not None:
                    p_ = feet(c, w, float(s["t"]))
                    if p_ is not None:
                        return p_
                return _xy(s["people"][w]) if "wx" in (s["people"].get(w) or {}) else where(w, i, float(s["t"]))
            return one(pair[0]), one(pair[1])

        sides = []
        for s in rows:
            pa, pb = ends(s)
            sides.append(_side_of(pa, pb, s["cam"]) if pa is not None and pb is not None else 0)
        A, B = ends(s0)
        a0, a1 = sides[0], sides[-1]
        inside = 1 in sides and -1 in sides   # カットの中で線を越える（越えて戻るものも）
        why = None
        for k in ((bk.get("cuts") or {}).get(c) or {}).get("cam_keys") or []:   # 二人から離れた場所（景色）を見るキー
            lk = k.get("look")
            pt = lk if isinstance(lk, list) and len(lk) == 2 else ((bk.get("places") or {}).get(lk) if isinstance(lk, str) and lk not in everyone else None)
            if isinstance(pt, (list, tuple)) and len(pt) >= 2 and min(math.hypot(float(pt[0]) - A[0], float(pt[1]) - A[1]),
                                                                      math.hypot(float(pt[0]) - B[0], float(pt[1]) - B[1])) > 2.0:
                why = "景色を見るキー"

        def moved(w):   # カットの中で動く（歩きながら・回りながらの会話は、線が動く）。足元があれば足元で、背の高い人物は背に合わせて広げる
            if feet is not None:
                fa_, fb_ = feet(c, w, float(s0["t"])), feet(c, w, float(s1["t"]))
                if fa_ is not None and fb_ is not None:
                    return math.dist(fa_, fb_) > LINE_MOVED
            a_, b_ = s0["people"].get(w) or {}, s1["people"].get(w) or {}
            if "wx" not in a_ or "wx" not in b_:
                return False
            lim = LINE_MOVED * max(1.0, float(a_.get("h", 1.6)) / 4.0)
            return math.dist(_xy(a_, True), _xy(b_, True)) > lim
        if moved(S) or moved(P):
            why = "カットの中で動く"
        st = state.get(pair)
        if tr is not None:
            tr.update({"S": S, "P": P, "votes": {w: v[0] for w, v in votes.items()}, "sides": sides,
                       "ref": (st or {}).get("cut"), "ref_side": (st or {}).get("side"),
                       "gap": (order[c] - order[st["cut"]] - 1) if st else None})
        if st:
            turned, shift = _line_turned(st, A, B)
            if turned > LINE_TURN or shift > LINE_SHIFT:
                if tr is not None:
                    tr["reset"] = f"二人の線が {turned:.0f} 度回り、真ん中が {shift:.1f}m 動いた"
                st = None
        if st is None or st["side"] == 0 or a0 == 0 or why:
            if tr is not None:
                tr["reset"] = tr.get("reset") or why or ("前の側がない" if st is None else "線の上")
                trace.append(tr)
            state[pair] = {"side": a1, "A": A, "B": B, "cut": c}
            log(pair, (c, a0, a1, tr["reset"] if tr is not None else "決め直し"), new_block=True)
            continue
        gap = order[c] - order[st["cut"]] - 1
        if a0 != st["side"] and not any(w in txt for w in CROSS_OK):
            stale = gap > LINE_STALE
            kind = "線を越える（要確認）" if stale else "線を越える"
            out.setdefault(c, []).append({"kind": kind, "who": list(pair), "after": st["cut"], "gap": gap,
                                          "line": [[round(A[0], 2), round(A[1], 2)], [round(B[0], 2), round(B[1], 2)]]})
            if tr is not None:
                tr["flag"] = kind
                trace.append(tr)
            log(pair, (c, a0, a1, kind))
            if stale or inside:   # 基準が古い・カットの中で越えるなら、終わりの側で続ける
                state[pair] = {"side": a1, "A": A, "B": B, "cut": c}
            continue   # 直せば前の側のまま
        if tr is not None:
            tr["ok"] = "同じ側" if a0 == st["side"] else "説明に回り込みなど"
            trace.append(tr)
        log(pair, (c, a0, a1, "カットの中で越える" if inside else None))
        state[pair] = {"side": a1 if a1 != 0 else st["side"], "A": A, "B": B, "cut": c}
    if blocks is not None:
        blocks.update(seq)
    return out


def speakers(scene_dir, shots):
    """台詞の (始め, 終わり, 話し手, 台詞ID)。場面の頭からの秒。話し手は audio/<場面>/original/<ID>_<話し手>.wav の名前から。"""
    vy = scene_dir / "voice.yaml"
    if not vy.exists():
        return []
    scene = scene_dir.name
    src = ROOT / "audio" / scene / "original"
    out = []
    for l in (yaml.safe_load(vy.read_text(encoding="utf-8")) or {}).get("lines") or []:
        wav = next(iter(sorted(src.glob(f"{l['id']}_*.wav"))), None) if src.exists() else None
        if wav is None:
            continue
        who = wav.stem.split("_", 1)[1]
        try:
            with wave.open(str(wav)) as w:
                dur = w.getnframes() / float(w.getframerate())
        except Exception:
            dur = 2.0
        out.append((float(l["at"]), float(l["at"]) + dur, who, l["id"]))
    return out


def inherited_frame(rows, cam_keys):
    """画の大きさの引き継ぎ（2026-11-08、A17-028）: frame のないカメラのキーは前のキーの画の大きさを引き継ぐので、
    クレーンで引くのに最初の「寄り」のまま測られる。主役（frame を書いたキーの look の人物）の大きさが、
    frame を書いたキーの 0.4 倍未満か 2.5 倍より大きく変わるキーがあれば、知らせる dict（なければ None）。"""
    ck = sorted(cam_keys or [], key=lambda k: float(k.get("t", 0.0)))
    fk = None
    bad_keys, ratios, bad_who, bad_frame = [], [], None, ""
    for k in ck:
        if k.get("frame"):
            fk = k
            continue
        if fk is None or not isinstance(fk.get("look"), str):
            continue
        who_ = fk["look"]
        rule = frame_rule(fk["frame"])
        if not rule:
            continue

        def size_at(t_):
            s_ = min(rows, key=lambda r: abs(float(r["t"]) - t_))
            m_ = s_["people"].get(who_) or {}
            if "frac" not in m_ or "fy" not in m_:
                return None
            return float(m_["frac"]) if rule[1] == "head" else float(m_["top"]) - float(m_["fy"])
        a_, b_ = size_at(float(fk.get("t", 0.0))), size_at(float(k.get("t", 0.0)))
        if a_ and b_ and a_ > 1e-3 and not (0.4 <= b_ / a_ <= 2.5):
            bad_keys.append(k.get("t"))
            ratios.append(round(b_ / a_, 2))
            bad_who, bad_frame = who_, str(fk["frame"])
    if not bad_keys:
        return None
    return {"kind": "画の大きさの引き継ぎ", "who": [bad_who], "keys": bad_keys, "frame": bad_frame, "ratio": ratios[-1]}


def feet_index(scene_dir, data):
    """blocking.yaml から、(カット, 人物, 時刻) → 足元の場面の x・y を返す関数（tools/staging.py と tools/motion.py。Blender なし）。
    keys のある人物はキーの補間（tools/previs.py と同じ motion.PersonTrack）、ない人物はカットの始め（後半は終わり）の立ち位置。
    読めなければ None（顔の位置で測る）。生き物に乗る（on）位置は見ない。"""
    try:
        import staging   # noqa: PLC0415
        import motion    # noqa: PLC0415
        st = staging.Stage(scene_dir)
    except Exception:
        return None
    secs = {c: float(v.get("sec") or 0.0) for c, v in (data.get("cuts") or {}).items()}
    tracks = {}

    def fn(cut, who, t):
        c = st.bk["cuts"].get(cut)
        s = st.states.get(cut)
        if c is None or s is None:
            return None
        if (cut, who) not in tracks:
            ks = (c.get("keys") or {}).get(who)
            try:
                tracks[(cut, who)] = motion.PersonTrack(ks, st.place) if ks else None
            except Exception:
                tracks[(cut, who)] = None
        tr = tracks[(cut, who)]
        if tr is not None:
            try:
                p_ = tr.at(float(t))[0]
                return float(p_[0]), float(p_[1])
            except Exception:
                pass
        pos = s["end"][0] if float(t) >= secs.get(cut, 0.0) / 2 else s["start"][0]
        p_ = pos.get(who)
        return (float(p_[0]), float(p_[1])) if p_ is not None else None
    return fn


def measure_status(scene_dir, data):
    """測定（gen/framing.yaml）のカットごとの版（2026-11-08、tools/manifest.py）: 最新 / 入力が古い / 道具が古い / 記録なし。
    入力が古い: 測った後で、そのカットの設計（blocking・ショットファイル・台詞など）が変わった。道具が古い: 測った後で下絵か測定の道具が変わった。
    記録なし: 2026-11-08 より前の測定（ハッシュを書いていない）。"""
    try:
        import manifest   # noqa: PLC0415
        si = manifest.SceneInputs(scene_dir)
        th = manifest.tool_hash(manifest.AUDIT_TOOLS)
    except Exception:
        return {}
    out = {}
    for cut, v in (data.get("cuts") or {}).items():
        try:
            cur = si.input_hash(cut)
        except Exception:
            cur = None
        out[cut] = manifest.compare(v, cur, th) if cur else "記録なし"
    return out


def analyze(scene_dir, trace=None, blocks=None, notes=None):
    fr = scene_dir / "gen" / "framing.yaml"
    if not fr.exists():
        sys.exit(f"測定がない: {fr}（先に python tools/previs.py {scene_dir} --audit）")
    data = yaml.safe_load(fr.read_text(encoding="utf-8"))
    shots = {}
    for f in sorted((scene_dir / "shots").glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        shots[d["id"]] = d
    lines = speakers(scene_dir, shots)
    bk, ja = {}, {}
    try:
        bk = yaml.safe_load((scene_dir / "blocking.yaml").read_text(encoding="utf-8")) or {}
        lay = yaml.safe_load((ROOT / bk["layout"]).read_text(encoding="utf-8")) or {}
        ja = {w: str(p.get("ja") or "") for w, p in (lay.get("people") or {}).items()}
    except Exception:
        pass
    issues = {}
    for cut, c in (data.get("cuts") or {}).items():
        sh = shots.get(cut) or {}
        tc = float(sh.get("tc", 0.0))
        found = []
        rows = c.get("samples") or []
        if not rows:
            continue
        subj_rows = []
        for s in rows:
            T = tc + float(s["T"])
            talking = [w for (a, b, w, _i) in lines if a - 0.1 <= T <= b + 0.1]
            subj = s.get("look") if s.get("look") in s["people"] else next((w for w in talking if w in s["people"]), None)
            subj_rows.append((s, subj, talking))
        n = len(rows)
        # 顔が外・隠れ・切れ
        out_n = sum(1 for s, w, _ in subj_rows if w and not s["people"][w]["in"])
        occ = [occ_of(s, w) for s, w, _ in subj_rows if w and s["people"][w]["in"] and occ_of(s, w)]
        cut_n = 0
        for s, w, _ in subj_rows:
            if not w or not s["people"][w]["in"]:
                continue
            rule = frame_rule(s.get("frame"))
            lim = 1.15 if rule and rule[0] == "寄り" else 1.06
            if s["people"][w]["top"] > lim:
                cut_n += 1
        whos = sorted({w for _, w, _ in subj_rows if w})
        if out_n >= max(1, 0.3 * n):
            found.append({"kind": "顔が外", "who": whos, "frames": f"{out_n}/{n}"})
        if len(occ) >= max(1, 0.3 * n):
            found.append({"kind": "顔が隠れ", "who": whos, "by": sorted(set(occ)), "frames": f"{len(occ)}/{n}"})
        if cut_n >= max(1, 0.3 * n):
            found.append({"kind": "頭が切れ", "who": whos, "frames": f"{cut_n}/{n}"})
        # 大きさ・足
        bad_size, feet = [], 0
        for s, w, _ in subj_rows:
            if not w or not s["people"][w]["in"]:
                continue
            rule = frame_rule(s.get("frame"))
            if not rule:
                continue
            m = s["people"][w]
            if "fy" not in m:   # 足元を測る前の測定
                continue
            key, kind, lo, hi = rule
            if kind == "head":
                k = max(1.0, min(2.6, (m["frac"] / max(1e-3, m["top"] - m["fy"])) / 0.16)) if m["top"] - m["fy"] > 0.05 else 1.0
                v = m["frac"]
                lo2, hi2 = lo, hi * k   # 頭の大きい人（百騎兵）は上限だけ広げる
            else:
                v = m["top"] - m["fy"]
                lo2, hi2 = lo, hi
            if not (lo2 <= v <= hi2):
                bad_size.append((key, round(v, 2), round(lo2, 2), round(hi2, 2)))
            if key == "全身" and m["fy"] < -0.04:
                feet += 1
        if len(bad_size) >= max(1, 0.5 * n):
            key, v, lo2, hi2 = bad_size[len(bad_size) // 2]
            found.append({"kind": "大きさ", "who": whos, "frame": key, "measured": v, "expect": [lo2, hi2],
                          "what": "顔" if frame_rule(key)[1] == "head" else "体"})
        if feet >= max(1, 0.5 * n):
            found.append({"kind": "足が切れ", "who": whos, "frames": f"{feet}/{n}"})
        # 顔の高さ（寄り・胸上・膝上で、顔が画面の下の端・上の端）
        ys = {}
        for s, w, _ in subj_rows:
            if not w or not s["people"][w]["in"]:
                continue
            rule = frame_rule(s.get("frame"))
            if rule and rule[0] in FACE_FRAMES:
                ys.setdefault(w, []).append(float(s["people"][w]["y"]))
        for w, v in ys.items():
            bad = [y for y in v if y < 0.3 or y > 0.92]
            if len(bad) >= max(1, 0.5 * n):
                found.append({"kind": "顔の高さ", "who": [w], "frames": f"{len(bad)}/{n}", "y": round(sorted(v)[len(v) // 2], 2)})
        # 顔が向こう向き（寄り・胸上・膝上）
        away, seen_w = {}, {}
        for s, w, _ in subj_rows:
            if not w or not s["people"][w]["in"] or "fa" not in s["people"][w]:
                continue
            rule = frame_rule(s.get("frame"))
            if rule and rule[0] in FACE_FRAMES:
                seen_w[w] = seen_w.get(w, 0) + 1   # その人が主役のコマ（場所を見るカットでは、話している間だけ。A25-023 で、話す時間が短いと全体の半分に届かず見逃した）
                if float(s["people"][w]["fa"]) > AWAY:
                    away[w] = away.get(w, 0) + 1
        for w, k_ in away.items():
            if k_ >= max(2, 0.5 * seen_w.get(w, n)) and not back_ok(sh, (bk.get("cuts") or {}).get(cut) or {}, w, ja.get(w, "")):
                fas = sorted(float(s["people"][w]["fa"]) for s, ww, _ in subj_rows if ww == w and "fa" in s["people"][w])
                found.append({"kind": "顔が向こう向き", "who": [w], "frames": f"{k_}/{n}", "angle": round(fas[len(fas) // 2])})
        # 話し手が見えない（台詞の間ずっと）
        for (a, b, w, lid) in lines:
            if b < tc or a > tc + float(c["sec"]):
                continue
            seen = [s for s, _subj, _t in subj_rows if a - 0.1 <= tc + float(s["T"]) <= b + 0.1]
            if not seen or w not in seen[0]["people"]:
                continue
            vis = [s for s in seen if s["people"].get(w, {}).get("in") and not occ_of(s, w)]
            other = [s for s in seen if s.get("look") in s["people"] and s.get("look") != w]
            if not vis and not other:
                found.append({"kind": "話し手が見えない", "who": [w], "line": lid})
        inh = inherited_frame(rows, ((bk.get("cuts") or {}).get(cut) or {}).get("cam_keys"))
        if inh and notes is not None:   # 知らせ（問題の数には入れない。2026-11-08）
            notes[cut] = {"shot": c.get("shot"), "notes": [inh]}
        if found:
            issues[cut] = {"shot": c.get("shot"), "issues": found}
    for cut, found in line_issues(data, shots, bk, lines, trace=trace, blocks=blocks, feet=feet_index(scene_dir, data)).items():
        issues.setdefault(cut, {"shot": data["cuts"][cut].get("shot"), "issues": []})["issues"] += found
    order = list(data.get("cuts") or {})
    issues = {c: issues[c] for c in order if c in issues}
    return data, issues


def report(scene_dir, issues, total, line_blocks=None, notes=None, status=None):
    kinds = {}
    for v in issues.values():
        for i in v["issues"]:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    md = [f"# {scene_dir.name} 構図の洗い出し（tools/framing.py）", "",
          f"測ったカット {total}、問題のあるカット {len(issues)}。" + "、".join(f"{k} {v}" for k, v in kinds.items()), ""]
    if status:   # 測定の版（2026-11-08）
        cnt = {}
        for v in status.values():
            cnt[v] = cnt.get(v, 0) + 1
        md += ["測定の版（tools/manifest.py）: " + "、".join(f"{k} {v}" for k, v in sorted(cnt.items())) +
               "。最新でない測定の印は、今の設計と合っていないことがある（python tools/previs.py <場面> <カット>… --audit で測り直す）", ""]
        old = [c for c, v in status.items() if v == "入力が古い"]
        if old:
            md += ["入力が古い測定のカット: " + "、".join(old), ""]
    for cut, v in issues.items():
        md.append(f"- **{cut}**（{v['shot']}）: " + "／".join(
            f"{i['kind']}（{'・'.join(i.get('who') or [])}" + (f"、{i['frames']}" if 'frames' in i else "")
            + (f"、{'・'.join(i['by'])}" if 'by' in i else "") + (f"、{i['frame']} の{i['what']}の大きさ {i['measured']}、目安 {i['expect'][0]}〜{i['expect'][1]}" if i['kind'] == '大きさ' else "")
            + (f"、{i['line']}" if 'line' in i and not i['kind'].startswith('線を越える') else "") + (f"、顔とカメラの角度 {i['angle']} 度" if 'angle' in i else "")
            + (f"、{i['after']} と反対の側から" if 'after' in i else "") + (f"（間 {i['gap']} カット）" if i.get('gap') else "") + (f"、顔の高さ {i['y']}（下 0・上 1）" if 'y' in i else "") + "）" for i in v["issues"]))
    if line_blocks:
        md += ["", "## 線の側の並び（印のある区間。どちらの側にそろえるかは区間ごとに1つの判断）", ""] + line_blocks
    if notes:
        md += ["", "## 知らせ（問題の数に入れない）", ""]
        for cut, v in notes.items():
            for i in v["notes"]:
                md.append(f"- **{cut}**（{v['shot']}）: 画の大きさの引き継ぎ（{'・'.join(i['who'])}、frame のないキー {'・'.join(str(t) for t in i['keys'])} 秒で、"
                          f"{i['frame']} の大きさが {i['ratio']} 倍に変わる。キーに frame を書くと、その区間は書いた大きさで測る）")
    (scene_dir / "gen" / "framing_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (scene_dir / "gen" / "framing_issues.yaml").write_text(yaml.safe_dump(issues, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    return kinds


def _sign(v):
    return {1: "＋", -1: "－", 0: "線上"}.get(v, "・")


def explain(scene_dir, cuts):
    """線の判断の中身（主役・相手・基準のカット・間のカット数・コマごとの側）を出す（2026-11-08）。"""
    tr = []
    analyze(scene_dir, trace=tr)
    want = set(cuts)
    for r in tr:
        if want and r["cut"] not in want:
            continue
        if "skip" in r and "S" not in r:
            print(f"{r['cut']}: 判断しない（{r['skip']}）" + (f"。見せ直し: {r['reestablish']}" if r.get("reestablish") else ""))
            continue
        if "P" not in r:
            print(f"{r['cut']}: 主役 {r.get('S')}、判断しない（{r.get('skip')}）")
            continue
        sides = "".join({1: "+", -1: "-", 0: "0"}[v] for v in r["sides"])
        ref = f"基準 {r['ref']}（側 {_sign(r['ref_side'])}、間 {r['gap']} カット）" if r.get("ref") else "基準なし"
        res = r.get("flag") or (f"決め直し（{r['reset']}）" if r.get("reset") else r.get("ok", ""))
        print(f"{r['cut']}: 主役 {r['S']}・相手 {r['P']}（票 {r['votes']}）、コマごとの側 {sides}、{ref} → {res}")


def block_lines(blocks, issues):
    """二人ごとの側の並びのうち、印のある区間だけを、報告の文にする。"""
    out = []
    for pair, segs in blocks.items():
        for seg in segs:
            if not any(row[3] and str(row[3]).startswith("線を越える") for row in seg):
                continue
            plus = sum(1 for row in seg if row[1] == 1)
            minus = sum(1 for row in seg if row[1] == -1)
            body = "／".join(f"{row[0]} {_sign(row[1])}" + (f" {row[3]}" if row[3] else "") for row in seg)
            out.append(f"- {'↔'.join(pair)}（{seg[0][0]}〜{seg[-1][0]}）: {body}。始めの側 ＋{plus}・－{minus} カット")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dirs", nargs="+")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--explain", nargs="?", const="", help="線の判断の中身を出す（カットを , で区切る。省くと全カット）。報告は書かない")
    a = ap.parse_args()
    for sd in a.scene_dirs:
        sd = ROOT / sd if not pathlib.Path(sd).is_absolute() else pathlib.Path(sd)
        if a.explain is not None:
            explain(sd, [c for c in a.explain.split(",") if c])
            continue
        blocks, notes = {}, {}
        data, issues = analyze(sd, blocks=blocks, notes=notes)
        status = measure_status(sd, data)
        kinds = report(sd, issues, len(data.get("cuts") or {}), block_lines(blocks, issues), notes, status)
        print(f"{sd.name}: {len(data.get('cuts') or {})} カット中 {len(issues)} カット  " + "、".join(f"{k} {v}" for k, v in kinds.items()))
        if not a.quiet:
            for cut, v in issues.items():
                print(f"  {cut}: " + "／".join(i["kind"] for i in v["issues"]))


if __name__ == "__main__":
    main()
