#!/usr/bin/env python3
"""構図を自動で直す（2026-11-07、下絵のアップグレードの続き）。tools/framing.py が見つけた「顔が外・頭が切れ・足が切れ・大きさ」を、
カメラの位置はそのままで、見る高さ（look_h）とレンズ（lens）だけ変えて直す。顔が隠れるカットは、測定の probe（顔が見える、いちばん小さいずれ）だけカメラをずらす。
話し手が見えないは直さない（カメラの位置から考え直す）。
顔が向こう向き（寄り・胸上・膝上で主役の顔がカメラの反対を向く）は、カメラを主役の周りに、同じ距離・同じ高さのまま、
顔の向きから 40 度の所まで回す（今カメラがいる側へ。主役の視線の線を越えないので、話し相手との左右は変わらない。2026-11-07）。
線を越える（イマジナリーライン）は、そのカットのカメラのキーを、二人を結ぶ線で鏡写しにする（主役との距離・高さは同じまま、前のカットの側へ）。
ただし鏡写しで主役の顔とカメラの角度が 20 度より大きく増えて 60 度を超えるなら（主役が相手をまっすぐ見ていないカット）、直さない（表情を優先）。
寄り・胸上・膝上で本人の帽子のつばが顔を隠すときは、そのカットでその人を見るキーのカメラを、つばの下から顔が見える高さ（顔からの高さ ≤ 横の距離 × 0.04、ほぼ目の高さ）まで下げる。

  python tools/framing_fix.py episodes/A/scenes/A12            # 直して gen/framing_fix.yaml に記録
  python tools/framing_fix.py episodes/A/scenes/A12 --dry-run  # 直す中身を見るだけ
  python tools/framing_fix.py episodes/A/scenes/A12 --only 顔が向こう向き,線を越える   # その種類だけ直す（レンズは「大きさ」を含むときだけ変える）
  python tools/framing_fix.py episodes/A/scenes/A12 --cuts A12-003,A12-007              # そのカットだけ

直し方（人物を見るキーごと。そのキーの時刻にいちばん近い測定のコマで）:
  見る高さ = 顔を画面の決まった高さ（寄り 0.52・胸上 0.6・膝上 0.68・全身 0.74・引き 0.66）に置く高さ（そのキーのレンズとカメラからの距離で計算）
  レンズ    = 大きさが目安の外なら、今のレンズ × 目安の真ん中 ÷ 測った大きさ（元の 0.6〜1.8 倍・18mm 以上）。前のキーから引き継いだ画の大きさや、
              二人・肩ごし・肩なめ・「→」の画は、レンズを変えず見る高さだけ直す（A07-016 の 26mm → 135mm、A14-007「寄り（二人）」で二人目が外れた、2026-11-07）。目安の中なら変えない
  画の大きさは、キーの frame（なければ前のキー、なければカットの frame。「全身→寄り」は最初のキーが全身、ほかは寄り）
cam_keys の look・lens・look_h は書いていないキーへ引き継がれる（motion.py）ので、直したカットはすべてのキーに見る高さとレンズを書く（直さないキーは今までの値）。
直した後は python tools/previs.py <場面> <カット>… --audit で測り直し、python tools/framing.py で確かめる。
"""
import argparse
import datetime
import math
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import framing  # noqa: E402

FIX = {"顔が外", "頭が切れ", "足が切れ", "大きさ", "顔が隠れ", "顔が向こう向き", "線を越える", "顔の高さ"}
TURN_TO = 40.0   # 顔が向こう向きのとき、カメラを回して置く、顔の向きからの角度（度）
BRIM_SLOPE = 0.04   # 帽子のつばの下から顔が見える、カメラの顔からの高さ ÷ 横の距離（つばの半径 0.35〜0.38m、顔からつばまで 0.09m。少しうつむくだけでつばの前が下がるので、ほぼ目の高さ。A15-005 で 0.16 では足りなかった）


def _bearing(dx, dy):   # layout（x 東・y 南）のベクトルの方位（北 0・時計回り）。staging.bearing_of と同じ
    return math.degrees(math.atan2(dx, -dy)) % 360


def _mirror(p, a, b):
    """点 p（x, y）を、a と b を通る線で鏡写しにする。"""
    ux, uy = b[0] - a[0], b[1] - a[1]
    L2 = ux * ux + uy * uy
    k = ((p[0] - a[0]) * ux + (p[1] - a[1]) * uy) / L2
    fx, fy = a[0] + k * ux, a[1] + k * uy
    return 2 * fx - p[0], 2 * fy - p[1]


def _mirror_vec(v, a, b):
    """向き v（x, y）を、a→b の向きの線で鏡写しにする（follow の offset 用）。"""
    m = _mirror((v[0], v[1]), (0.0, 0.0), (b[0] - a[0], b[1] - a[1]))
    return m[0], m[1]


def mirror_cut(keys, line, places):
    """カットのカメラのキーを、線で鏡写しにする。見る先が場所の名前で線から離れていれば、鏡写しの点（[x, y]）に書き換える。"""
    a, b = line
    done = []
    for k in keys:
        cm = k.get("cam")
        if isinstance(cm, list) and len(cm) >= 3:
            x, y = _mirror((float(cm[0]), float(cm[1])), a, b)
            k["cam"] = [round(x, 2), round(y, 2)] + list(cm[2:])
            done.append(k.get("t"))
        elif isinstance(cm, dict) and "follow" in cm:
            off = list(cm.get("offset", [0.0, 0.0]))
            x, y = _mirror_vec((float(off[0]), float(off[1])), a, b)
            k["cam"] = dict(cm, offset=[round(x, 2), round(y, 2)])
            done.append(k.get("t"))
        lk = k.get("look")
        pt = lk if isinstance(lk, list) and len(lk) == 2 else (places.get(lk) if isinstance(lk, str) else None)
        if pt is not None and isinstance(pt, (list, tuple)) and len(pt) >= 2:
            x, y = _mirror((float(pt[0]), float(pt[1])), a, b)
            if abs(x - float(pt[0])) + abs(y - float(pt[1])) > 0.3:
                k["look"] = [round(x, 2), round(y, 2)]
    return done


def mirror_hides_face(keys, samples, line):
    """線で鏡写しにすると、カメラの見る人の顔とカメラの角度（横の向き）が 20 度より大きく増え、60 度を超えるか。
    主役が相手をまっすぐ見ていないと、鏡写しで顔の向きとの角度が変わる（A15-014・A30-025・A35-004 で、ほぼ正面から横顔や背中になった）。"""
    before, after = [], []
    for k in keys:
        cm, lk = k.get("cam"), k.get("look")
        if not (isinstance(cm, list) and len(cm) >= 2 and isinstance(lk, str)):
            continue
        s = min(samples, key=lambda s: abs(float(s["t"]) - float(k.get("t", 0.0))))
        m = s["people"].get(lk)
        if not m or "fh" not in m or "wx" not in m:
            continue
        mx, my = _mirror((float(cm[0]), float(cm[1])), line[0], line[1])
        for (x, y), acc in (((float(cm[0]), float(cm[1])), before), ((mx, my), after)):
            b = _bearing(x - m["wx"], y - m["wy"])
            acc.append(abs((b - float(m["fh"]) + 180.0) % 360.0 - 180.0))
    if not before:
        return False
    b0, a0 = sum(before) / len(before), sum(after) / len(after)
    return a0 - b0 > 20.0 and a0 > 60.0


def _turn(dx, dy, fh):
    """主役から見たカメラの向き (dx, dy) を、顔の向き fh から TURN_TO 度の所まで回す。回さなくてよければ None。"""
    r = math.hypot(dx, dy)
    if r < 0.1:
        return None
    b = _bearing(dx, dy)
    d = (b - fh + 180.0) % 360.0 - 180.0
    if abs(d) <= 100.0:
        return None
    nb = math.radians(fh + math.copysign(TURN_TO, d))
    return r * math.sin(nb), -r * math.cos(nb), round(math.copysign(TURN_TO, d) - d)
TARGET = {"寄り": ("head", 0.42), "胸上": ("head", 0.24), "膝上": ("body", 1.0), "全身": ("body", 0.72), "引き": ("body", 0.38), "大引き": ("body", 0.18)}
OFF = {"寄り": 0.03, "胸上": 0.12, "膝上": 0.24, "全身": 0.42, "引き": 0.42, "大引き": 0.42}   # カメラの位置が測定にないときだけ使う
FACE_Y = {"寄り": 0.52, "胸上": 0.6, "膝上": 0.68, "全身": 0.74, "引き": 0.66, "大引き": 0.6}   # 顔を置く画面の高さ（下 0・上 1）
HALF_SENSOR_H = 10.125   # mm（横 36mm・16:9 の縦の半分）


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


def fix_scene(scene_dir, dry=False, only=None, cuts=None):
    allowed = set(only) if only else set(FIX)
    data, issues = framing.analyze(scene_dir)
    if cuts:
        issues = {c: v for c, v in issues.items() if c in cuts}
    bk_path = scene_dir / "blocking.yaml"
    bk = yaml.safe_load(bk_path.read_text(encoding="utf-8"))
    people = (yaml.safe_load((ROOT / bk["layout"]).read_text(encoding="utf-8")) or {}).get("people") or {}
    out, log = {}, {}
    for cut, v in issues.items():
        if not any(i["kind"] in allowed for i in v["issues"]):
            continue
        c = bk["cuts"][cut]
        keys = [dict(k) for k in sorted(c.get("cam_keys") or [], key=lambda k: float(k.get("t", 0.0)))]
        if not keys:
            continue
        samples = data["cuts"][cut]["samples"]
        prev = {"look": None, "lens": 35.0, "look_h": None, "frame": None}
        changed = []
        cross = next((i for i in v["issues"] if i["kind"] == "線を越える"), None) if "線を越える" in allowed else None
        if cross and mirror_hides_face(keys, samples, cross["line"]):   # 鏡写しにすると主役の顔が見えなくなる: 直さない（A15-014 で 26 度 → 82 度になった）
            log[cut] = {"issues": [i["kind"] for i in v["issues"]], "keys": [], "note": "線を越えるが、鏡写しにすると主役の顔が横か後ろを向くので直さない"}
            continue
        if cross:   # イマジナリーライン: まずカメラを線で鏡写しにする（ほかの直しは、測り直してから）
            done = mirror_cut(keys, cross["line"], bk.get("places") or {})
            if done:
                out[cut] = keys
                log[cut] = {"issues": [i["kind"] for i in v["issues"]], "keys": [{"t": t, "moved": {"mirror": cross["line"], "after": cross.get("after")}} for t in done]}
            continue
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
                new_lh = round(float(m["hz"]) - OFF[key] * h, 2)   # レンズを決めた後で、距離がわかれば置き直す
                kind, tgt = TARGET[key]
                size = float(m["frac"]) if kind == "head" else float(m["top"]) - float(m.get("fy", m["top"] - 1.0))
                lo, hi = rule[2], rule[3]
                if kind == "head":
                    kk = max(1.0, min(2.6, (m["frac"] / max(1e-3, m["top"] - m.get("fy", 0.0))) / 0.16)) if m["top"] - m.get("fy", 0.0) > 0.05 else 1.0
                    hi *= kk
                    tgt = min(tgt * (1.0 + 0.35 * (kk - 1.0)), hi * 0.8)
                lab_s = str(k.get("frame") or c.get("frame") or "")
                inherited = not k.get("frame") and idx > 0
                multi = any(w in lab_s for w in ("二人", "三人", "四人", "肩", "なめ", "→", "群", "全員"))
                if size > 0.01 and not (lo <= size <= hi) and not inherited and not multi and "大きさ" in allowed:   # 引き継いだ画の大きさ・二人以上の画はレンズを変えない
                    want = lens0 * tgt / size
                    new_lens = round(max(18.0, min(135.0, lens0 * 1.8, max(lens0 * 0.6, want))))   # 元の 0.6〜1.8 倍、18mm より広くはしない
                    cm = k.get("cam")
                    if want < 18.0 and isinstance(cm, list) and len(cm) >= 3 and "wx" in m and s.get("cam"):   # まだ大きすぎる: カメラを主役から引く（1.6m まで）
                        dx, dy = cm[0] - m["wx"], cm[1] - m["wy"]
                        dist = (dx * dx + dy * dy) ** 0.5
                        if dist > 0.2:
                            nd = min(dist + 1.6, dist * 18.0 / max(1.0, want))
                            k["cam"] = [round(m["wx"] + dx * nd / dist, 2), round(m["wy"] + dy * nd / dist, 2), cm[2]]
                            changed_cam = True
                cam_now = k.get("cam") if isinstance(k.get("cam"), list) else s.get("cam")
                if cam_now and "wx" in m:   # 顔を画面の FACE_Y の高さに置く見る高さ（そのキーのレンズとカメラからの距離で）
                    d = ((cam_now[0] - m["wx"]) ** 2 + (cam_now[1] - m["wy"]) ** 2 + (cam_now[2] - m["hz"]) ** 2) ** 0.5
                    fh = 2.0 * d * HALF_SENSOR_H / float(new_lens)
                    new_lh = round(float(m["hz"]) - (FACE_Y[key] - 0.5) * fh, 2)
            elif lh0 is None and m is not None:   # 画の大きさがわからない人物のキー: 今までの既定（背の 0.75）を書く
                new_lh = round(float(m.get("bz", m["hz"] - 0.9 * float(m.get("h", 1.6)))) + 0.75 * float(m.get("h", 1.6)), 2)
            elif lh0 is None:   # 場所を見るキーの既定は 0
                new_lh = 0.0
            moved = None
            away_who = {w for i in v["issues"] if i["kind"] == "顔が向こう向き" for w in (i.get("who") or [])} if "顔が向こう向き" in allowed else set()
            if m is not None and subj in away_who and "fh" in m and "wx" in m:   # 顔が向こう向き: カメラを主役の顔の前の側へ回す
                cm = k.get("cam")
                if isinstance(cm, list) and len(cm) >= 3:
                    tr = _turn(cm[0] - m["wx"], cm[1] - m["wy"], float(m["fh"]))
                    if tr:
                        k["cam"] = [round(m["wx"] + tr[0], 2), round(m["wy"] + tr[1], 2), cm[2]]
                        moved = {"turn": tr[2]}
                elif isinstance(cm, dict) and "follow" in cm and cm.get("follow") == subj:
                    off = list(cm.get("offset", [0.0, 0.0]))
                    tr = _turn(float(off[0]), float(off[1]), float(m["fh"]))
                    if tr:
                        k["cam"] = dict(cm, offset=[round(tr[0], 2), round(tr[1], 2)])
                        moved = {"turn": tr[2]}
            brim_who = {w for i in v["issues"] if i["kind"] == "顔が隠れ" and any(str(b).endswith("brim") for b in (i.get("by") or []))
                        for w in (i.get("who") or [])}
            if (m is not None and not moved and subj in brim_who and "顔が隠れ" in allowed and rule is not None
                    and rule[0] in framing.FACE_FRAMES and isinstance(k.get("cam"), list) and len(k["cam"]) >= 3 and "wx" in m):
                # 本人の帽子のつばで顔が隠れる: つばの下から顔が見える高さまでカメラを下げる（顔からの高さ ≤ 横の距離 × BRIM_SLOPE）
                cm = k["cam"]
                D = ((cm[0] - m["wx"]) ** 2 + (cm[1] - m["wy"]) ** 2) ** 0.5
                zmax = float(m["hz"]) + BRIM_SLOPE * D
                if float(cm[2]) > zmax:
                    k["cam"] = [cm[0], cm[1], round(max(float(m["hz"]) - 0.05, zmax), 2)] + list(cm[3:])
                    moved = {"lower_for_brim": [cm[2], k["cam"][2]]}
            pr = s.get("probe") if m is not None and m.get("occ") and not moved else None
            if pr and "顔が隠れ" in allowed and ("顔が隠れ" in [i["kind"] for i in v["issues"]]):   # 顔が隠れる: カメラを少しずらす（測定の probe）
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
        day = datetime.date.today().isoformat()
        old[day] = {**(old.get(day) or {}), **log}   # 同じ日の前の記録に重ねる（上書きしない）
        logp.write_text(yaml.safe_dump(old, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dirs", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="直す種類（, で区切る）。省くと " + "・".join(sorted(FIX)))
    ap.add_argument("--cuts", help="直すカット（, で区切る）")
    a = ap.parse_args()
    only = [k for k in (a.only or "").split(",") if k] or None
    if only and not set(only) <= FIX:
        sys.exit("直せる種類は " + "・".join(sorted(FIX)))
    cuts = set(k for k in (a.cuts or "").split(",") if k) or None
    for sd in a.scene_dirs:
        sd = ROOT / sd if not pathlib.Path(sd).is_absolute() else pathlib.Path(sd)
        log = fix_scene(sd, a.dry_run, only, cuts)
        print(f"{sd.name}: {len(log)} カットのカメラを直した" + ("（見るだけ）" if a.dry_run else "") + ("  " + " ".join(log) if log else ""))


if __name__ == "__main__":
    main()
