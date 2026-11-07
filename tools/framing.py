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
LINE_MOVED = 1.5     # m。二人のどちらかがこれより動いたら、側を決め直す
LINE_GAZE = 50.0     # 度。主役の顔の向きと相手の方向の差がこれより小さければ、その人を見ている


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


def line_issues(data, shots, bk, lines, trace=None):
    """イマジナリーライン: カットの順に、主役と見ている相手の線のどちら側にカメラがいるかを追い、飛び越えたカットを返す。"""
    cuts = [c for c in (data.get("cuts") or {}) if (data["cuts"][c].get("samples") or [])]
    pos = {}   # カットごとの人物の位置（顔の x・y）
    for c in cuts:
        rows = data["cuts"][c]["samples"]
        mid = rows[len(rows) // 2]
        pos[c] = {w: (float(m["wx"]), float(m["wy"])) for w, m in mid["people"].items() if "wx" in m}

    def where(w, i):   # 映っていない人は、いちばん近いカットの位置
        for d in range(len(cuts)):
            for j in (i - d, i + d):
                if 0 <= j < len(cuts) and w in pos[cuts[j]]:
                    return pos[cuts[j]][w]
        return None

    everyone = sorted({w for c in cuts for w in pos[c]})
    state, out = {}, {}
    for i, c in enumerate(cuts):
        rows = data["cuts"][c]["samples"]
        sh = shots.get(c) or {}
        tc = float(sh.get("tc", 0.0))
        txt = _text_of(sh, (bk.get("cuts") or {}).get(c))
        s0, s1 = rows[0], rows[-1]
        rule = frame_rule(s0.get("frame"))
        if rule and rule[0] == "大引き":
            continue

        def subject(s):
            if s.get("look") in s["people"]:
                return s["look"]
            T = tc + float(s["T"])
            return next((w for (a, b, w, _i) in lines if a - 0.1 <= T <= b + 0.1 and w in s["people"]), None)

        S = subject(s0) or subject(s1)
        if not S or "fh" not in (s0["people"].get(S) or {}):
            continue
        pS = (float(s0["people"][S]["wx"]), float(s0["people"][S]["wy"]))
        best = None
        for w in everyone:
            if w == S:
                continue
            q = where(w, i)
            if q is None:
                continue
            dist = math.hypot(q[0] - pS[0], q[1] - pS[1])
            if dist < 0.3 or dist > 6.0:
                continue
            dd = _adiff(float(s0["people"][S]["fh"]), _bearing(q[0] - pS[0], q[1] - pS[1]))
            if dd < LINE_GAZE and (best is None or dd < best[0]):
                best = (dd, w, q)
        if best is None:
            continue
        P, pP = best[1], best[2]
        pair = tuple(sorted((S, P)))
        A, B = (pS, pP) if pair[0] == S else (pP, pS)
        L = math.hypot(B[0] - A[0], B[1] - A[1])

        def side(s):   # 線の上（線から 0.5m 以内か、二人の真ん中から見て線から 20 度以内）は 0
            cx, cy = float(s["cam"][0]), float(s["cam"][1])
            d = ((B[0] - A[0]) * (cy - A[1]) - (B[1] - A[1]) * (cx - A[0])) / L
            along = ((cx - (A[0] + B[0]) / 2) * (B[0] - A[0]) + (cy - (A[1] + B[1]) / 2) * (B[1] - A[1])) / L
            if abs(d) < LINE_NEUTRAL or math.degrees(math.atan2(abs(d), abs(along))) < LINE_ANGLE:
                return 0
            return 1 if d > 0 else -1

        a0, a1 = side(s0), side(s1)
        scenery = False   # 二人から離れた場所（景色）を見るキー
        for k in ((bk.get("cuts") or {}).get(c) or {}).get("cam_keys") or []:
            lk = k.get("look")
            pt = lk if isinstance(lk, list) and len(lk) == 2 else ((bk.get("places") or {}).get(lk) if isinstance(lk, str) and lk not in everyone else None)
            if isinstance(pt, (list, tuple)) and len(pt) >= 2 and min(math.hypot(float(pt[0]) - A[0], float(pt[1]) - A[1]),
                                                                      math.hypot(float(pt[0]) - B[0], float(pt[1]) - B[1])) > 2.0:
                scenery = True
        def moved(w):   # カットの中で 1.5m 以上動く（歩きながら・回りながらの会話は、線が動く）
            a_, b_ = s0["people"].get(w) or {}, s1["people"].get(w) or {}
            return "wx" in a_ and "wx" in b_ and math.hypot(a_["wx"] - b_["wx"], a_["wy"] - b_["wy"]) > LINE_MOVED
        if moved(S) or moved(P):
            scenery = True
        st = state.get(pair)
        if trace is not None:
            trace.append((c, S, P, a0, a1, (st or {}).get("side"), (st or {}).get("cut")))
        if st and (math.hypot(A[0] - st["A"][0], A[1] - st["A"][1]) > LINE_MOVED or math.hypot(B[0] - st["B"][0], B[1] - st["B"][1]) > LINE_MOVED):
            st = None
        if st is None or st["side"] == 0 or a0 == 0 or scenery:
            state[pair] = {"side": a1, "A": A, "B": B, "cut": c}
            continue
        if a0 != st["side"] and not any(w in txt for w in CROSS_OK):
            out.setdefault(c, []).append({"kind": "線を越える", "who": list(pair), "after": st["cut"],
                                          "line": [[round(A[0], 2), round(A[1], 2)], [round(B[0], 2), round(B[1], 2)]]})
            if a1 != a0:   # カットの中で戻る（越えて戻る）なら、終わりの側で続ける
                state[pair] = {"side": a1, "A": A, "B": B, "cut": c}
            continue   # 直せば前の側のまま
        state[pair] = {"side": a1 if a1 != 0 else st["side"], "A": A, "B": B, "cut": c}
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


def analyze(scene_dir):
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
        away = {}
        for s, w, _ in subj_rows:
            if not w or not s["people"][w]["in"] or "fa" not in s["people"][w]:
                continue
            rule = frame_rule(s.get("frame"))
            if rule and rule[0] in FACE_FRAMES and float(s["people"][w]["fa"]) > AWAY:
                away[w] = away.get(w, 0) + 1
        for w, k_ in away.items():
            if k_ >= max(1, 0.5 * n) and not back_ok(sh, (bk.get("cuts") or {}).get(cut) or {}, w, ja.get(w, "")):
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
        if found:
            issues[cut] = {"shot": c.get("shot"), "issues": found}
    for cut, found in line_issues(data, shots, bk, lines).items():
        issues.setdefault(cut, {"shot": data["cuts"][cut].get("shot"), "issues": []})["issues"] += found
    order = list(data.get("cuts") or {})
    issues = {c: issues[c] for c in order if c in issues}
    return data, issues


def report(scene_dir, issues, total):
    kinds = {}
    for v in issues.values():
        for i in v["issues"]:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    md = [f"# {scene_dir.name} 構図の洗い出し（tools/framing.py）", "",
          f"測ったカット {total}、問題のあるカット {len(issues)}。" + "、".join(f"{k} {v}" for k, v in kinds.items()), ""]
    for cut, v in issues.items():
        md.append(f"- **{cut}**（{v['shot']}）: " + "／".join(
            f"{i['kind']}（{'・'.join(i.get('who') or [])}" + (f"、{i['frames']}" if 'frames' in i else "")
            + (f"、{'・'.join(i['by'])}" if 'by' in i else "") + (f"、{i['frame']} の{i['what']}の大きさ {i['measured']}、目安 {i['expect'][0]}〜{i['expect'][1]}" if i['kind'] == '大きさ' else "")
            + (f"、{i['line']}" if 'line' in i and i['kind'] != '線を越える' else "") + (f"、顔とカメラの角度 {i['angle']} 度" if 'angle' in i else "")
            + (f"、{i['after']} と反対の側から" if 'after' in i else "") + (f"、顔の高さ {i['y']}（下 0・上 1）" if 'y' in i else "") + "）" for i in v["issues"]))
    (scene_dir / "gen" / "framing_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (scene_dir / "gen" / "framing_issues.yaml").write_text(yaml.safe_dump(issues, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    return kinds


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dirs", nargs="+")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    for sd in a.scene_dirs:
        sd = ROOT / sd if not pathlib.Path(sd).is_absolute() else pathlib.Path(sd)
        data, issues = analyze(sd)
        kinds = report(sd, issues, len(data.get("cuts") or {}))
        print(f"{sd.name}: {len(data.get('cuts') or {})} カット中 {len(issues)} カット  " + "、".join(f"{k} {v}" for k, v in kinds.items()))
        if not a.quiet:
            for cut, v in issues.items():
                print(f"  {cut}: " + "／".join(i["kind"] for i in v["issues"]))


if __name__ == "__main__":
    main()
