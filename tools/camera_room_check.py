#!/usr/bin/env python3
"""描く前のカメラの衝突の確かめ（2026-11-08、previs の根本改善のフェーズ0）。報告だけで、blocking.yaml は直さない。

blocking.yaml のカメラの通り道（cam_keys を 0.25 秒ごと。follow も人物の動きから）を、配置（layout と layout_extra）の
「中に入れる部屋」と比べ、描く前に Python だけで次を見つける（Blender は使わない）:

  壁の中       カメラが壁の厚みの中（四角い部屋 room* の壁、議会室の円い壁 cwall。扉・窓・破れた所は除く）
  壁ごし       カメラから見る点までの線が、壁を通る（カメラが部屋の外から中を見る・中から外を見る。A14-007 を前へ回すと、
               カメラが部屋の壁の外に出て後半が壁一色になった、2026-11-07）。扉・窓・破れた所を通るのはよい。
               カメラと見る点の片方だけが部屋の中のときだけ数える（両方が外で、建物を手前に入れる引きの画は数えない）
  屋根ごし     カメラから見る点までの線が、四角い部屋の屋根（壁の高さ h）を通る
  天井より上   カメラが部屋の中で、屋根・天井の高さより上（四角い部屋の壁の高さ、議会室の円すいの天井）
  建物の中     カメラが中に入れない建物（layout の house の円柱、house* の箱）の中

見る点は、cam_keys の look（人物ならその人の足元の位置で、高さは look_h か背の 0.75、場所なら look_h か 0）。
光で出し入れする物（only_lit）は、そのカットの光のときだけ数える。家具・岩・木・人物との衝突は見ない（描いた後の tools/black_check.py）。
生き物に乗る（on）人物の位置と、follow の生き物の頭は、おおよそ（人物は keys の位置、生き物は頭の通り道）。

  python tools/camera_room_check.py episodes/A/scenes/A14            # 場面ごとに表示
  python tools/camera_room_check.py --all --md docs/previs_review/camera_room_check.md   # 全場面を確かめて報告を書く
"""
import argparse
import math
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import motion   # noqa: E402
import staging  # noqa: E402

STEP = 0.25      # 秒
WALL_T = 0.18    # 四角い部屋の壁の厚さ（tools/previs.py の build_room）
MARGIN = 0.0     # m。壁の中とみなす余り（壁のすぐ前で壁に背を向けるカメラは問題ないので、厚みの中だけ。A39-007）
NEAR_PLANE = 0.3  # m。見る点が壁の面からこれより近い（窓・扉そのものを見る）なら、壁ごしに数えない（A06-000 の窓明かりへ寄るクレーン）
CWALL_T = 0.4    # 円い壁の厚さ（tools/previs_council.py の build_wall。半径 r〜r+0.4）


# ---------- 配置から部屋を読む ----------

def _lit_ok(pr, lighting):
    return not pr.get("only_lit") or lighting in pr["only_lit"]


def enclosures(lay, lighting):
    """配置の、カメラが入ってはいけない・通して見てはいけない物。"""
    rooms, walls, ceils, solids = [], [], [], []
    for name, pr in (lay.get("props") or {}).items():
        if name.startswith("room") and "x" in pr and "y" in pr:
            rooms.append({"name": name, "ja": pr.get("ja", name), "x": [float(v) for v in pr["x"]], "y": [float(v) for v in pr["y"]],
                          "h": float(pr.get("h", 3.0)), "sides": list(pr.get("walls") or ["n", "s", "e", "w"]),
                          "openings": [dict(o) for o in pr.get("openings") or []]})
            continue
        if "at" not in pr or not _lit_ok(pr, lighting):
            continue
        if name.startswith("cwall"):
            walls.append({"name": name, "ja": pr.get("ja", name), "at": [float(v) for v in pr["at"]], "r": float(pr.get("r", 12.5)),
                          "h": float(pr.get("h", 9.0)), "gap": pr.get("gap") or [], "door": pr.get("door")})
        elif name.startswith("cceil"):
            ceils.append({"name": name, "ja": pr.get("ja", name), "at": [float(v) for v in pr["at"]], "r": float(pr.get("r", 12.8)),
                          "z": float(pr.get("z", 9.0)), "rise": float(pr.get("rise", 5.0)), "r_in": float(pr.get("r_in", 0.0)),
                          "gap": pr.get("gap") or []})
        elif name.startswith("house") and "w" in pr:
            solids.append({"kind": "box", "name": name, "ja": pr.get("ja", name), "at": [float(v) for v in pr["at"]],
                           "w": float(pr["w"]), "d": float(pr.get("d", pr["w"])), "h": float(pr.get("h", 3.0)), "rot": float(pr.get("rot", 0.0))})
    h = lay.get("house")
    if h:
        solids.append({"kind": "cyl", "name": "house", "ja": "家", "at": [float(v) for v in h["center"]], "r": float(h["r"]),
                       "h": float(h["h"]) + float(h.get("roof", 0.0))})
    return rooms, walls, ceils, solids


# ---------- 形の計算 ----------

def _in_ranges(b, ranges):
    for lo, hi in ranges or []:
        lo, hi = float(lo) % 360, float(hi) % 360
        if (lo <= b <= hi) if lo <= hi else (b >= lo or b <= hi):
            return True
    return False


def _dist_to_ranges(b, ranges):
    best = 999.0
    for lo, hi in ranges or []:
        for e in (float(lo), float(hi)):
            best = min(best, abs((b - e + 180) % 360 - 180))
    return best


def _bearing(cx, cy, x, y):   # 北 0・東 90（layout は y が南）
    return math.degrees(math.atan2(x - cx, -(y - cy))) % 360


def _room_walls(rm):
    """四角い部屋の壁: (side, 固定の軸 'x' か 'y', 固定の値, 沿う範囲 lo, hi)。"""
    (xa, xb), (ya, yb) = rm["x"], rm["y"]
    out = []
    for s in rm["sides"]:
        if s == "n":
            out.append((s, "y", ya, xa, xb))
        elif s == "s":
            out.append((s, "y", yb, xa, xb))
        elif s == "w":
            out.append((s, "x", xa, ya, yb))
        elif s == "e":
            out.append((s, "x", xb, ya, yb))
    return out


def _in_opening(rm, side, u, z):
    for o in rm["openings"]:
        if o.get("side") != side:
            continue
        sill = float(o.get("sill", 0.0))
        if abs(u - float(o["c"])) <= float(o["w"]) / 2 and sill <= z <= sill + float(o["h"]):
            return True
    return False


def check_point(C, L, rooms, walls, ceils, solids):
    """カメラ C（x, y, z）と見る点 L（x, y, z）。見つかった問題の (種類, 物の名前, 説明) の並び。"""
    found = []
    cx, cy, cz = C
    lx, ly, lz = L
    for rm in rooms:
        (xa, xb), (ya, yb), H = rm["x"], rm["y"], rm["h"]
        inside = xa < cx < xb and ya < cy < yb
        for side, ax, v, lo, hi in _room_walls(rm):
            c_fix, c_u = (cy, cx) if ax == "y" else (cx, cy)
            l_fix, l_u = (ly, lx) if ax == "y" else (lx, ly)
            if (abs(c_fix - v) < WALL_T / 2 + MARGIN and lo - WALL_T / 2 <= c_u <= hi + WALL_T / 2 and cz < H
                    and not _in_opening(rm, side, c_u, cz)):
                found.append(("壁の中", rm["ja"], f"{side} の壁"))
                continue
            l_inside = xa < lx < xb and ya < ly < yb
            if inside != l_inside and abs(l_fix - v) >= NEAR_PLANE and (c_fix - v) * (l_fix - v) < 0:   # 見る点までの線が壁の面を通る
                u = (v - c_fix) / (l_fix - c_fix)
                pu, pz = c_u + u * (l_u - c_u), cz + u * (lz - cz)
                if lo <= pu <= hi and 0.0 <= pz < H and not _in_opening(rm, side, pu, pz):
                    found.append(("壁ごし", rm["ja"], f"{side} の壁（{round(pu, 2)}、高さ {round(pz, 2)}m）"))
        if inside and cz >= H - 0.02:
            found.append(("天井より上", rm["ja"], f"高さ {round(cz, 2)}m（壁の高さ {H}m）"))
        elif cz >= H and lz < H and (cz - lz) > 1e-6 and xa < lx < xb and ya < ly < yb:   # 見る点が部屋の中で、屋根の面（壁の高さ）を通る
            u = (cz - H) / (cz - lz)
            px, py = cx + u * (lx - cx), cy + u * (ly - cy)
            if xa < px < xb and ya < py < yb:
                found.append(("屋根ごし", rm["ja"], f"屋根の上（{round(px, 2)}, {round(py, 2)}）から"))
    for w in walls:
        x0, y0 = w["at"]
        r, h = w["r"], w["h"]
        dc = math.hypot(cx - x0, cy - y0)

        def wall_h(b):   # 破れた所の縁は低い（tools/previs_council.py の build_wall のぎざぎざの最も低い所）
            if _in_ranges(b, w["gap"]):
                return 0.0
            d = _dist_to_ranges(b, w["gap"]) if w["gap"] else 999.0
            return h * 0.35 if d < 8 else (h * 0.75 if d < 14 else h)

        def door_at(b, z):
            dr = w.get("door")
            return bool(dr) and abs((b - float(dr["at"]) + 180) % 360 - 180) < math.degrees(float(dr.get("w", 3.0)) / 2 / r) and z < float(dr.get("h", 4.4))
        bc = _bearing(x0, y0, cx, cy)
        if r - MARGIN <= dc <= r + CWALL_T + MARGIN and cz < wall_h(bc) and not door_at(bc, cz):
            found.append(("壁の中", w["ja"], f"方位 {round(bc)} 度"))
            continue
        # 見る点までの線と、壁の真ん中の円（半径 r+0.2）の交わり。カメラと見る点の片方だけが中のとき
        if (dc < r) == (math.hypot(lx - x0, ly - y0) < r):
            continue
        dx, dy = lx - cx, ly - cy
        A = dx * dx + dy * dy
        if A < 1e-9:
            continue
        B = 2 * ((cx - x0) * dx + (cy - y0) * dy)
        Cc = (cx - x0) ** 2 + (cy - y0) ** 2 - (r + CWALL_T / 2) ** 2
        disc = B * B - 4 * A * Cc
        if disc <= 0:
            continue
        for u in sorted(((-B - math.sqrt(disc)) / (2 * A), (-B + math.sqrt(disc)) / (2 * A))):
            if not 0.0 < u < 1.0:
                continue
            px, py, pz = cx + u * dx, cy + u * dy, cz + u * (lz - cz)
            b = _bearing(x0, y0, px, py)
            if pz < wall_h(b) and not door_at(b, pz):
                found.append(("壁ごし", w["ja"], f"方位 {round(b)} 度、高さ {round(pz, 2)}m"))
                break
    for ce in ceils:
        x0, y0 = ce["at"]
        dc = math.hypot(cx - x0, cy - y0)
        if dc < ce["r"] and dc >= ce["r_in"] and not _in_ranges(_bearing(x0, y0, cx, cy), ce["gap"]):
            zc = ce["z"] + ce["rise"] * (1.0 - dc / ce["r"])
            if cz >= zc:
                found.append(("天井より上", ce["ja"], f"高さ {round(cz, 2)}m（天井 {round(zc, 2)}m）"))
    for so in solids:
        x0, y0 = so["at"]
        if so["kind"] == "cyl":
            if math.hypot(cx - x0, cy - y0) < so["r"] and cz < so["h"]:
                found.append(("建物の中", so["ja"], so["name"]))
            continue
        vx, vy = cx - x0, -(cy - y0)   # Blender の向き（y が北）。box は上から見て時計回りに rot 度
        a = math.radians(so["rot"])
        lxx, lyy = vx * math.cos(a) - vy * math.sin(a), vx * math.sin(a) + vy * math.cos(a)
        if abs(lxx) < so["w"] / 2 and abs(lyy) < so["d"] / 2 and cz < so["h"]:
            found.append(("建物の中", so["ja"], so["name"]))
    return found


# ---------- カメラの通り道 ----------

def cam_path(st, cut, sec, step=STEP):
    """(時刻, カメラ (x, y, z), 見る点 (x, y, z), 見る先が人物か) の並び。tools/previs.py の Plan.camera と同じ計算（揺れは除く）。"""
    c = st.bk["cuts"][cut] or {}
    lay = st.lay
    start_pos, end_pos = st.states[cut]["start"][0], st.states[cut]["end"][0]
    tracks = {}
    for w, ks in (c.get("keys") or {}).items():
        try:
            tracks[w] = motion.PersonTrack(ks, st.place)
        except Exception:
            pass
    creatures = {}
    for n in (c.get("creatures") or {}):
        try:
            creatures[n] = motion.CreatureTrack(motion.creature_spec(st.bk["cuts"], cut, n), st.place)
        except Exception:
            pass

    def pos_at(t):
        out = {}
        for w in start_pos:
            if w in tracks:
                out[w] = tuple(tracks[w].at(t)[0])
            else:
                p = start_pos[w] if t < sec / 2 else end_pos[w]
                if p is not None:
                    out[w] = tuple(p)
        return out

    def height_of(w, t):
        return float(tracks[w].at(t)[1]) if w in tracks else 0.0

    times = sorted({round(k * step, 3) for k in range(int(sec / step) + 1)} | {round(sec, 3)} |
                   {round(float(k.get("t", 0.0)), 3) for k in (c.get("cam_keys") or []) if 0.0 <= float(k.get("t", 0.0)) <= sec})
    out = []
    if c.get("cam_keys"):
        ct = motion.CamTrack([dict(k) for k in c["cam_keys"]])
        for t in times:
            pos = pos_at(t)
            heads = {n: tr.head(t) for n, tr in creatures.items()}
            pos2 = {**pos, **{n: h[:2] for n, h in heads.items()}}

            def look_pt(v, p):
                return tuple(p[v]) if isinstance(v, str) and v in p else tuple(st.place(v))

            def look_height(v):
                if isinstance(v, str) and v in heads:
                    return heads[v][2]
                if isinstance(v, str) and v in pos and v in lay["people"]:
                    x, y = pos[v]
                    return lay["people"][v]["h"] * 0.75 + height_of(v, t) + motion.ground_z(lay, x, y)
                return 0.0

            def base_z(name, x, y):
                return heads[name][2] if name in heads else motion.ground_z(lay, x, y)
            try:
                cam, look, lh, _lens = ct.at(t, pos2, look_pt, look_height, base_z)
            except Exception:
                continue
            ka, kb, _u = motion.segment(ct.k, t)
            person = all(isinstance(k.get("look"), str) and k["look"] in lay["people"] for k in (ka, kb))
            out.append((t, tuple(float(v) for v in cam[:3]), (float(look[0]), float(look[1]), float(lh)), person))
        return out
    if not c.get("cam"):
        return out
    t0, t1 = (c.get("cam_t") or [0.0, sec])
    cam0, cam1 = c["cam"], c.get("cam_end") or c["cam"]
    for t in times:
        u = min(1.0, max(0.0, (t - t0) / max(1e-6, t1 - t0)))
        u = u * u * (3 - 2 * u)
        cam = [a + (b - a) * u for a, b in zip(cam0, cam1)]
        pos = pos_at(t)
        lk = c.get("look_end", c["look"]) if u >= 0.5 else c["look"]
        try:
            p = pos[lk] if isinstance(lk, str) and lk in pos else st.place(lk)
        except Exception:
            continue
        lh = c.get("look_h")
        if lh is None:
            lh = lay["people"][lk]["h"] * 0.75 if isinstance(lk, str) and lk in lay["people"] else 0.0
        out.append((t, tuple(float(v) for v in cam[:3]), (float(p[0]), float(p[1]), float(lh)), isinstance(lk, str) and lk in lay["people"]))
    return out


def check_scene(scene_dir):
    """場面の全カット。{カット: [{kind, what, where, t: [始め, 終わり], cam}]}。"""
    st = staging.Stage(scene_dir)
    sd = ROOT / scene_dir if not pathlib.Path(scene_dir).is_absolute() else pathlib.Path(scene_dir)
    res = {}
    for cut in st.order:
        shp = sd / "shots" / f"{cut}.yaml"
        if not shp.exists():
            continue
        sec = float((yaml.safe_load(shp.read_text(encoding="utf-8")) or {}).get("sec") or 0.0)
        c = st.bk["cuts"][cut] or {}
        lighting = c.get("lighting") or st.bk.get("lighting")
        rooms, walls, ceils, solids = enclosures(st.lay, lighting)
        if not (rooms or walls or ceils or solids):
            continue
        hits = {}
        for t, C, L, person in cam_path(st, cut, sec):
            for kind, what, where in check_point(C, L, rooms, walls, ceils, solids):
                key = (kind, what)
                h = hits.setdefault(key, {"kind": kind, "what": what, "where": where, "t": [t, t], "cam": [round(v, 2) for v in C], "person": False})
                h["t"][1] = t
                h["person"] = h["person"] or person
        if hits:
            res[cut] = list(hits.values())
    return res


def scene_dirs_all():
    return sorted(str(p.parent.relative_to(ROOT)) for p in (ROOT / "episodes").glob("*/scenes/*/blocking.yaml"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dirs", nargs="*")
    ap.add_argument("--all", action="store_true", help="すべての場面")
    ap.add_argument("--md", help="報告を書く（Markdown）")
    a = ap.parse_args()
    dirs = scene_dirs_all() if a.all else a.scene_dirs
    lines, n_cut, n_hit, skipped = [], 0, 0, []
    for sd in dirs:
        try:
            res = check_scene(sd)
        except Exception as e:   # 古い書き方の場面（A01 など）は読めないことがある
            skipped.append(f"{pathlib.Path(sd).name}（{type(e).__name__}: {e}）")
            continue
        for cut, hs in res.items():
            n_cut += 1
            for h in hs:
                n_hit += 1
                who = "人物を見ている" if h["person"] else ("場所を見ている（建物そのものを見せる画なら問題ない）" if h["kind"] in ("壁ごし", "屋根ごし") else "場所を見ている")
                s = f"- **{cut}** {h['kind']}: {h['what']}（{h['where']}）、{h['t'][0]}〜{h['t'][1]} 秒、カメラ {h['cam']}、{who}"
                lines.append(s)
                print(s.replace("**", ""))
    head = f"{len(dirs)} 場面を確かめた。問題のあるカット {n_cut}、問題 {n_hit} 件。" + (f" 読めなかった場面: {'、'.join(skipped)}" if skipped else "")
    print(head)
    if a.md:
        md = ["# 描く前のカメラの衝突（tools/camera_room_check.py、報告だけ）", "",
              "blocking.yaml のカメラの通り道（0.25 秒ごと）を、配置の部屋（四角い部屋 room*・議会室の円い壁と天井）と中に入れない建物と比べた。"
              "直していない。描いた後の黒・一色の確かめ（tools/black_check.py）とは別。", "", head, ""] + lines
        p = ROOT / a.md
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
