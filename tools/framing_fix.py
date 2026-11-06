#!/usr/bin/env python3
"""構図を自動で直す（2026-11-07、下絵のアップグレードの続き）。tools/framing.py が見つけた「顔が外・頭が切れ・足が切れ・大きさ」を、
カメラの位置はそのままで、見る高さ（look_h）とレンズ（lens）だけ変えて直す。顔が隠れるカットは、測定の probe（顔が見える、いちばん小さいずれ）だけカメラをずらす。
話し手が見えないは直さない（カメラの位置から考え直す）。

  python tools/framing_fix.py episodes/A/scenes/A12            # 直して gen/framing_fix.yaml に記録
  python tools/framing_fix.py episodes/A/scenes/A12 --dry-run  # 直す中身を見るだけ

直し方（人物を見るキーごと。そのキーの時刻にいちばん近い測定のコマで）:
  見る高さ = 顔の真ん中の高さ − 背 × {寄り 0.03・胸上 0.12・膝上 0.24・全身と引き 0.42}
  レンズ    = 大きさが目安の外なら、今のレンズ × 目安の真ん中 ÷ 測った大きさ（18〜135mm。18mm で足りなければカメラの位置から考え直す）。目安の中なら変えない
  画の大きさは、キーの frame（なければ前のキー、なければカットの frame。「全身→寄り」は最初のキーが全身、ほかは寄り）
cam_keys の look・lens・look_h は書いていないキーへ引き継がれる（motion.py）ので、直したカットはすべてのキーに見る高さとレンズを書く（直さないキーは今までの値）。
直した後は python tools/previs.py <場面> <カット>… --audit で測り直し、python tools/framing.py で確かめる。
"""
import argparse
import datetime
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import framing  # noqa: E402

FIX = {"顔が外", "頭が切れ", "足が切れ", "大きさ", "顔が隠れ"}
TARGET = {"寄り": ("head", 0.42), "胸上": ("head", 0.24), "膝上": ("body", 1.0), "全身": ("body", 0.72), "引き": ("body", 0.38), "大引き": ("body", 0.18)}
OFF = {"寄り": 0.03, "胸上": 0.12, "膝上": 0.24, "全身": 0.42, "引き": 0.42, "大引き": 0.42}


def _fl(x):
    t = yaml.safe_dump(x, default_flow_style=True, allow_unicode=True, width=10000, sort_keys=False)
    return "\n".join(y for y in t.splitlines() if y.strip() != "...").strip()


def _block_range(text, cut):
    a = text.index(f"\n  {cut}:\n") + 1
    nxt = re.search(r"\n  [A-Z]\d+[a-z]?-\d+[a-z]?:\n", text[a + 3:])
    b = a + 3 + nxt.start() + 1 if nxt else len(text)
    return a, b


def write_cam_keys(path, new_keys):
    """blocking.yaml の、カットの cam_keys の行だけを書き換える（ほかの行・アンカーはそのまま）。"""
    text = path.read_text(encoding="utf-8")
    for cut, keys in new_keys.items():
        a, b = _block_range(text, cut)
        block = text[a:b]
        lines = block.split("\n")
        i = next((k for k, l in enumerate(lines) if re.match(r"^\s*cam_keys:\s*(#.*)?$", l)), None)   # 後ろに注記があってもよい
        if i is None:   # 1行に書いた cam_keys: [...]
            i = next(k for k, l in enumerate(lines) if re.match(r"^\s*cam_keys:\s*\[", l))
            ind = len(lines[i]) - len(lines[i].lstrip())
            lines[i:i + 1] = [" " * ind + "cam_keys:"] + [f"{' ' * (ind + 2)}- {_fl(k)}" for k in keys]
            text = text[:a] + "\n".join(lines) + text[b:]
            continue
        ind = len(lines[i]) - len(lines[i].lstrip())
        j = i + 1
        item_pre, dash = None, None
        while j < len(lines):   # 項目（「- 」で始まる行）と、その続きの行（項目より深い字下げ）
            l = lines[j]
            if not l.strip():
                j += 1
                continue
            cur = len(l) - len(l.lstrip())
            if dash is None:
                if l.lstrip().startswith("- ") and cur >= ind:
                    dash, item_pre = cur, l[:cur]
                    j += 1
                    continue
                break
            if (cur == dash and l.lstrip().startswith("- ")) or cur > dash:
                j += 1
                continue
            break
        item_pre = item_pre or " " * (ind + 2)
        lines[i + 1:j] = [f"{item_pre}- {_fl(k)}" for k in keys]
        text = text[:a] + "\n".join(lines) + text[b:]
    yaml.safe_load(text)
    path.write_text(text, encoding="utf-8")


def label_for(k_frame, prev, cut_frame, idx, n):
    if k_frame:
        return k_frame
    if prev:
        return prev
    if cut_frame and "→" in str(cut_frame):
        parts = str(cut_frame).split("→")
        return parts[0] if idx == 0 else parts[-1]
    return cut_frame


def fix_scene(scene_dir, dry=False):
    data, issues = framing.analyze(scene_dir)
    bk_path = scene_dir / "blocking.yaml"
    bk = yaml.safe_load(bk_path.read_text(encoding="utf-8"))
    people = (yaml.safe_load((ROOT / bk["layout"]).read_text(encoding="utf-8")) or {}).get("people") or {}
    out, log = {}, {}
    for cut, v in issues.items():
        if not any(i["kind"] in FIX for i in v["issues"]):
            continue
        c = bk["cuts"][cut]
        keys = [dict(k) for k in sorted(c.get("cam_keys") or [], key=lambda k: float(k.get("t", 0.0)))]
        if not keys:
            continue
        samples = data["cuts"][cut]["samples"]
        prev = {"look": None, "lens": 35.0, "look_h": None, "frame": None}
        changed = []
        for idx, k in enumerate(keys):
            look = k.get("look", prev["look"])
            lens0 = float(k.get("lens", prev["lens"]))
            lh0 = k.get("look_h", prev["look_h"])
            lab = label_for(k.get("frame"), prev["frame"], c.get("frame"), idx, len(keys))
            s = min(samples, key=lambda s: abs(float(s["t"]) - float(k.get("t", 0.0))))
            m = s["people"].get(look) if isinstance(look, str) else None
            subj = look if m is not None else None
            if m is None:   # 場所を見るキー: 見る点から 2m 以内の、問題の人物（なければいちばん近い人）を主役とみなす
                pt = look if isinstance(look, (list, tuple)) else (bk.get("places") or {}).get(look) if isinstance(look, str) else None
                whos = [w for i in v["issues"] for w in (i.get("who") or [])]
                if pt is not None:
                    best = None
                    for w, mm in s["people"].items():
                        if "wx" not in mm:
                            continue
                        dd = ((mm["wx"] - float(pt[0])) ** 2 + (mm["wy"] - float(pt[1])) ** 2) ** 0.5
                        key_ = (0 if w in whos else 1, dd)
                        if dd <= 2.0 and (best is None or key_ < best[0]):
                            best = (key_, w)
                    if best:
                        subj = best[1]
                        m = s["people"][subj]
            rule = framing.frame_rule(lab)
            new_lh, new_lens = lh0, lens0
            changed_cam = False
            if m is not None and rule is not None:
                key = rule[0]
                h = float(m.get("h") or people.get(subj, {}).get("h", 1.6))
                new_lh = round(float(m["hz"]) - OFF[key] * h, 2)
                kind, tgt = TARGET[key]
                size = float(m["frac"]) if kind == "head" else float(m["top"]) - float(m.get("fy", m["top"] - 1.0))
                lo, hi = rule[2], rule[3]
                if kind == "head":
                    kk = max(1.0, min(2.6, (m["frac"] / max(1e-3, m["top"] - m.get("fy", 0.0))) / 0.16)) if m["top"] - m.get("fy", 0.0) > 0.05 else 1.0
                    hi *= kk
                    tgt = min(tgt * (1.0 + 0.35 * (kk - 1.0)), hi * 0.8)
                if size > 0.01 and not (lo <= size <= hi):
                    want = lens0 * tgt / size
                    new_lens = round(max(18.0, min(135.0, want)))   # 18mm より広くはしない（ゆがみが強すぎる）
                    cm = k.get("cam")
                    if want < 18.0 and isinstance(cm, list) and len(cm) >= 3 and "wx" in m and s.get("cam"):   # まだ大きすぎる: カメラを主役から引く（1.6m まで）
                        dx, dy = cm[0] - m["wx"], cm[1] - m["wy"]
                        dist = (dx * dx + dy * dy) ** 0.5
                        if dist > 0.2:
                            nd = min(dist + 1.6, dist * 18.0 / max(1.0, want))
                            k["cam"] = [round(m["wx"] + dx * nd / dist, 2), round(m["wy"] + dy * nd / dist, 2), cm[2]]
                            changed_cam = True
            elif lh0 is None and m is not None:   # 画の大きさがわからない人物のキー: 今までの既定（背の 0.75）を書く
                new_lh = round(float(m.get("bz", m["hz"] - 0.9 * float(m.get("h", 1.6)))) + 0.75 * float(m.get("h", 1.6)), 2)
            elif lh0 is None:   # 場所を見るキーの既定は 0
                new_lh = 0.0
            moved = None
            pr = s.get("probe") if m is not None and m.get("occ") else None
            if pr and ("顔が隠れ" in [i["kind"] for i in v["issues"]]):   # 顔が隠れる: カメラを少しずらす（測定の probe）
                cm = k.get("cam")
                if isinstance(cm, list) and len(cm) >= 3:
                    k["cam"] = [round(cm[0] + pr["dx"], 2), round(cm[1] + pr["dy"], 2), round(cm[2] + pr["dz"], 2)]
                    moved = pr
                elif isinstance(cm, dict) and "follow" in cm:
                    off = list(cm.get("offset", [0.0, 0.0]))
                    k["cam"] = dict(cm, offset=[round(off[0] + pr["dx"], 2), round(off[1] + pr["dy"], 2)], z=round(float(cm.get("z", 1.2)) + pr["dz"], 2))
                    moved = pr
            if changed_cam:
                moved = moved or {"dolly": k["cam"]}
            if new_lh != lh0 or new_lens != lens0 or moved:
                changed.append({"t": k.get("t"), "look": look, "frame": lab, "look_h": [lh0, new_lh], "lens": [lens0, new_lens]} | ({"moved": moved} if moved else {}))
            k["look"] = look
            k["look_h"] = new_lh
            k["lens"] = new_lens
            prev = {"look": look, "lens": lens0, "look_h": lh0, "frame": lab}
        if changed:
            out[cut] = keys
            log[cut] = {"issues": [i["kind"] for i in v["issues"]], "keys": changed}
    if out and not dry:
        write_cam_keys(bk_path, out)
        logp = scene_dir / "gen" / "framing_fix.yaml"
        old = (yaml.safe_load(logp.read_text(encoding="utf-8")) or {}) if logp.exists() else {}
        old[datetime.date.today().isoformat()] = log
        logp.write_text(yaml.safe_dump(old, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dirs", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    for sd in a.scene_dirs:
        sd = ROOT / sd if not pathlib.Path(sd).is_absolute() else pathlib.Path(sd)
        log = fix_scene(sd, a.dry_run)
        print(f"{sd.name}: {len(log)} カットのカメラを直した" + ("（見るだけ）" if a.dry_run else "") + ("  " + " ".join(log) if log else ""))


if __name__ == "__main__":
    main()
