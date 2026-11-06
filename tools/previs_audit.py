"""構図の測定（2026-11-07、下絵のアップグレードの続き）。previs.py --audit から呼ぶ。

各カットを 0.75 秒ごとに動かして（描かない）、映っている人物ごとに
  x, y   顔（頭の球の真ん中）の画面の位置（0〜1、左下が 0）
  frac   顔の縦の大きさ（あごから頭のてっぺんまで）が画面の高さに占める割合（寄り 0.25〜、胸上 0.13〜、膝上 0.07〜、全身 0.03〜 が目安）
  top    頭のてっぺんの y（1 を超えると頭が画面の上で切れる）
  fy     足元の y（0 より下なら足が画面の下で切れる）
  hz・bz 顔の真ん中と足元の高さ（m、Blender の z。tools/framing_fix.py が見る高さを決めるのに使う）
  wx・wy 顔の場面の x・y（m）。コマの cam はカメラの場面の x・y・高さ
  in     顔が画面の中にあるか
  occ    カメラと顔の間をふさぐ物の名前（なければ null。半透明の物・効果・本人の帽子や髪は数えない）
  probe  見る先の顔が隠れているコマだけ: カメラを左右・上下にずらして顔が見える、いちばん小さいずれ {dx, dy, dz}（tools/framing_fix.py が使う）
と、カメラの見る先（look）・カットの画の大きさ（frame）・レンズを、episodes/<話>/scenes/<場面>/gen/framing.yaml に書く。
読むのは tools/framing.py（話し手の顔が写っていない・切れている・隠れている・大きさが画の大きさと合わないカットを洗い出す）。

2026-11-07 にこれを作ったきっかけ: A12-011「寄り・メタリカ」が、前の描き方でも新しい描き方でも胴体しか写っていなかった
（階段の上（z 1.0）に立つメタリカを、見る高さ 1.5m（腰）で見ていた）。
"""
import datetime
import pathlib

import bpy
import yaml
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

import previs_rig

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _root_of(o):
    while o.parent is not None:
        o = o.parent
    return o


def _translucent(o):
    for s in getattr(o, "material_slots", []) or []:
        m = s.material
        if m is not None and float(m.diffuse_color[3]) < 0.6:
            return True
    return o.name.startswith(("fx", "FX", "TRAIL"))


def _occluder(scene, dg, origin, target, own_root, stop_short):
    d = target - origin
    dist = d.length
    if dist < 1e-4:
        return None
    dirn = d.normalized()
    o = origin.copy()
    remaining = dist - stop_short
    for _ in range(10):
        if remaining <= 0.01:
            return None
        hit, loc, _n, _i, obj, _m = scene.ray_cast(dg, o, dirn, distance=remaining)
        if not hit:
            return None
        step = (loc - o).length + 0.01
        o = loc + dirn * 0.01
        remaining -= step
        if obj is None:
            continue
        if _root_of(obj) is own_root:
            return None   # 本人（帽子・髪・腕）に当たった
        if obj.hide_render or _translucent(obj):
            continue
        return obj.name
    return None


def _cam_state(plan, t):
    """その時刻のカメラの見る先と画の大きさ（cam_keys の、t より前の最後のキー）。"""
    c = plan.c
    keys = c.get("cam_keys")
    look, frame = c.get("look"), c.get("frame")
    if keys:
        for k in sorted(keys, key=lambda k: float(k.get("t", 0.0))):
            if float(k.get("t", 0.0)) <= t + 1e-6:
                look = k.get("look", look)
                frame = k.get("frame", frame)
    return (look if isinstance(look, str) else None), frame


def measure(scene, cam, figs, plan, lay, t, T):
    dg = bpy.context.evaluated_depsgraph_get()
    origin = cam.matrix_world.translation.copy()
    look, frame = _cam_state(plan, t)
    cw = cam.matrix_world.translation
    out = {"T": round(T, 2), "t": round(t, 2), "look": look, "frame": frame, "lens": round(float(cam.data.lens), 1),
           "cam": [round(cw.x, 2), round(-cw.y, 2), round(cw.z, 2)], "people": {}}
    for who in plan.shown:
        rg = previs_rig.RIGS.get(who)
        if rg is None or who not in figs:
            continue
        hr = float(rg.hr)   # 巨大メタリカも SHAPE の hr がもう大きい（rg.giant は掛けない）
        hm = rg.head.matrix_world
        c, top, chin = hm @ Vector((0, 0, hr)), hm @ Vector((0, 0, 2 * hr)), hm @ Vector((0, 0, 0))
        feet = figs[who][0].matrix_world.translation.copy()
        vc, vt, vb, vf = (world_to_camera_view(scene, cam, p) for p in (c, top, chin, feet))
        inside = vc.z > 0 and 0.0 <= vc.x <= 1.0 and 0.0 <= vc.y <= 1.0
        occ = _occluder(scene, dg, origin, c, figs[who][0], hr * 1.05) if vc.z > 0 else None
        out["people"][who] = {"x": round(vc.x, 3), "y": round(vc.y, 3), "z": round(vc.z, 2), "frac": round(abs(vt.y - vb.y), 3),
                              "top": round(vt.y, 3), "fy": round(vf.y, 3), "in": bool(inside), "occ": occ, "hz": round(c.z, 2), "bz": round(feet.z, 2),
                              "wx": round(c.x, 2), "wy": round(-c.y, 2),
                              "h": float(lay["people"][who]["h"])}
    if look in out["people"] and out["people"][look]["occ"] and look in figs:   # 見る先の顔が隠れている: カメラを少しずらして見える所を探す
        out["probe"] = _probe(scene, dg, cam, previs_rig.RIGS[look], figs[look][0])
    return out


def _probe(scene, dg, cam, rg, own_root):
    """カメラを左右（画面の右の向き）・上下にずらして、顔が隠れない、いちばん小さいずれ（Blender の z と、場面の x・y の m）。見つからなければ None。"""
    hr = float(rg.hr)
    face = rg.head.matrix_world @ Vector((0, 0, hr))
    origin = cam.matrix_world.translation.copy()
    right = (cam.matrix_world.to_quaternion() @ Vector((1, 0, 0)))
    right.z = 0.0
    right = right.normalized() if right.length > 1e-6 else Vector((1, 0, 0))
    cands = sorted({(dx, dz) for dx in (0.0, 0.15, -0.15, 0.3, -0.3, 0.45, -0.45, 0.6, -0.6, 0.8, -0.8) for dz in (0.0, 0.15, 0.3, -0.15, 0.45)},
                   key=lambda v: abs(v[0]) + abs(v[1]) * 1.2)
    for dx, dz in cands:
        if dx == 0.0 and dz == 0.0:
            continue
        o = origin + right * dx + Vector((0, 0, dz))
        if _occluder(scene, dg, o, face, own_root, hr * 1.05) is None:
            d = right * dx
            return {"dx": round(d.x, 2), "dy": round(-d.y, 2), "dz": round(dz, 2)}   # 場面の y は Blender の -y
    return None


def run(st, lay, scene_dir, cuts, fps, size, samples, out_dir, work, render_cut):
    res = {}
    for cut in cuts:
        shot = yaml.safe_load((ROOT / scene_dir / "shots" / f"{cut}.yaml").read_text(encoding="utf-8"))
        rows = []
        render_cut(st, lay, cut, float(shot["sec"]), fps, size, samples, out_dir, work, audit=rows)
        res[cut] = {"sec": float(shot["sec"]), "shot": shot.get("shot"), "samples": rows}
        print(f"{cut}: {len(rows)} コマを測った", flush=True)
    dst = ROOT / scene_dir / "gen" / "framing.yaml"
    old = {}
    if dst.exists():   # 一部のカットだけ測ったときは、前の結果に重ねる
        try:
            old = (yaml.safe_load(dst.read_text(encoding="utf-8")) or {}).get("cuts") or {}
        except Exception:
            old = {}
    old.update(res)
    order = [c for c in st.order if c in old] + [c for c in old if c not in st.order]
    data = {"note": "tools/previs.py --audit が書く（tools/previs_audit.py）。読むのは tools/framing.py",
            "made": datetime.date.today().isoformat(), "cuts": {c: old[c] for c in order}}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    print(f"{dst.relative_to(ROOT)}（{len(res)} カット）")
