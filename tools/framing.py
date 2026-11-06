#!/usr/bin/env python3
"""構図の洗い出し（2026-11-07、下絵のアップグレードの続き）。tools/previs.py --audit の測定（gen/framing.yaml）を読んで、
話し手やカメラの見る先の顔が写っていない・切れている・隠れている・大きさがカットの画の大きさ（寄り・胸上・膝上・全身・引き）と合わない所を洗い出す。

  python tools/framing.py episodes/A/scenes/A12          # gen/framing_report.md と gen/framing_issues.yaml を書く
  python tools/framing.py episodes/A/scenes/A12 --quiet  # 1行の数だけ

見る先（主役）: そのコマのカメラの look が人物ならその人。そうでなければ、そのとき話している人（映っていれば）。
問題の種類:
  顔が外   主役の顔の真ん中が画面の外（コマの3割以上）
  顔が隠れ 主役の顔の前を物（ほかの人物・家具・壁）がふさぐ（コマの3割以上）
  頭が切れ 頭のてっぺんが画面の上で大きく切れる（寄りは 1.15、ほかは 1.06 を超える）
  足が切れ 全身なのに足元が画面の下で切れる
  大きさ   画の大きさと、顔（寄り・胸上）か体（膝上・全身・引き）の大きさが合わない
  話し手が見えない 台詞の間ずっと、話し手（映っている人）の顔が見えず、カメラもほかの人を見ていない
"""
import argparse
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
        occ = [s["people"][w]["occ"] for s, w, _ in subj_rows if w and s["people"][w]["in"] and s["people"][w]["occ"]]
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
        # 話し手が見えない（台詞の間ずっと）
        for (a, b, w, lid) in lines:
            if b < tc or a > tc + float(c["sec"]):
                continue
            seen = [s for s, _subj, _t in subj_rows if a - 0.1 <= tc + float(s["T"]) <= b + 0.1]
            if not seen or w not in seen[0]["people"]:
                continue
            vis = [s for s in seen if s["people"].get(w, {}).get("in") and not s["people"][w].get("occ")]
            other = [s for s in seen if s.get("look") in s["people"] and s.get("look") != w]
            if not vis and not other:
                found.append({"kind": "話し手が見えない", "who": [w], "line": lid})
        if found:
            issues[cut] = {"shot": c.get("shot"), "issues": found}
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
            + (f"、{i['line']}" if 'line' in i else "") + "）" for i in v["issues"]))
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
