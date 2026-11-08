"""構図の測定（2026-11-07、下絵のアップグレードの続き）。previs.py --audit から呼ぶ。

各カットを 0.75 秒ごとに動かして（描かない）、映っている人物ごとに
  x, y   顔（頭の球の真ん中）の画面の位置（0〜1、左下が 0）
  frac   顔の縦の大きさ（あごから頭のてっぺんまで）が画面の高さに占める割合（寄り 0.25〜、胸上 0.13〜、膝上 0.07〜、全身 0.03〜 が目安）
  top    頭のてっぺんの y（1 を超えると頭が画面の上で切れる）
  fy     足元の y（0 より下なら足が画面の下で切れる）
  hz・bz 顔の真ん中と足元の高さ（m、Blender の z。tools/framing_fix.py が見る高さを決めるのに使う）
  wx・wy 顔の場面の x・y（m）。コマの cam はカメラの場面の x・y・高さ
  in     顔が画面の中にあるか
  occ    カメラと顔の間をふさぐ物の名前（なければ null。半透明の物・効果・本人の髪や腕は数えない。本人の帽子のつばは数える）
  fa     顔の向きとカメラの向きの角度（度。0 で正面、90 で真横、90 を超えると顔が向こうを向いて写らない）
  fh     顔の向き（場面の向き、北 0・東 90。tools/framing_fix.py がカメラを顔の前へ回すのに使う）
  probe  見る先の顔が隠れているコマだけ: カメラを左右・上下にずらして顔が見える、いちばん小さいずれ {dx, dy, dz}（tools/framing_fix.py が使う）
  fay    顔の向きとカメラの向きの、横（水平）の角度（度、2026-11-08）。tools/framing.py の「顔が向こう向き」と tools/framing_fix.py の回り込みは、この物差し
  fap    縦の角度（度、2026-11-08）。カメラが顔の向きの線より上なら正（見下ろす）、下なら負（見上げる）
  lm     顔の目印（2026-11-08。tools/previs_face.py が作る両目と口の物）: {le: 左目, re: 右目, mo: 口} → ok（見える）・out（画面の外）・
         back（顔の裏）か、ふさぐ物の名前。本人の帽子・つば・手・腕も数える（手で口を隠す芝居は、tools/framing.py が問題にしない）。顔のない人物は書かない
  bx・by 足元の場面の x・y（m、2026-11-08。tools/framing.py が線の動きを足元で測る）
と、カメラの見る先（look）・カットの画の大きさ（frame）・レンズ・カメラの向き（yaw、北 0・東 90。tools/framing.py が視線の左右を見るのに使う）を、episodes/<話>/scenes/<場面>/gen/framing.yaml に書く。
カットごとに、測ったときの入力と道具のハッシュ（input_hash・tool_hash、tools/manifest.py）・測った時刻（made）・fps も書く（2026-11-08）。
一部のカットだけ測ると前の結果に重ねるので、tools/framing.py がカットごとに今の入力と比べ、古い測定を知らせる。
読むのは tools/framing.py（話し手の顔が写っていない・切れている・隠れている・大きさが画の大きさと合わないカットを洗い出す）。

2026-11-07 にこれを作ったきっかけ: A12-011「寄り・メタリカ」が、前の描き方でも新しい描き方でも胴体しか写っていなかった
（階段の上（z 1.0）に立つメタリカを、見る高さ 1.5m（腰）で見ていた）。
"""
import datetime
import math
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


OWN_COVER = ("brim", "hat", "_hand", "_fore", "_cone")   # 目印への線で、本人の物でも数えるもの（帽子・つば・手・腕。2026-11-08）


def _occluder(scene, dg, origin, target, own_root, stop_short, own_counts=None):
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
        if _root_of(obj) is own_root:   # 本人（髪・腕）に当たった。帽子のつばだけは隠す物に数える（A08-007、2026-11-07）
            if own_counts and any(k in obj.name for k in own_counts):   # 目印への線では帽子・手・腕も数える（2026-11-08）
                return obj.name
            return obj.name if obj.name.endswith("brim") else None
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


def measure(scene, cam, figs, plan, lay, t, T, faces=None):
    dg = bpy.context.evaluated_depsgraph_get()
    origin = cam.matrix_world.translation.copy()
    look, frame = _cam_state(plan, t)
    cw = cam.matrix_world.translation
    cf = cam.matrix_world.to_3x3() @ Vector((0, 0, -1))   # カメラの向き（カメラは -Z を見る）
    out = {"T": round(T, 2), "t": round(t, 2), "look": look, "frame": frame, "lens": round(float(cam.data.lens), 1),
           "cam": [round(cw.x, 2), round(-cw.y, 2), round(cw.z, 2)], "yaw": round(math.degrees(math.atan2(cf.x, cf.y)) % 360.0), "people": {}}
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
        if occ is None and vc.z > 0:   # 目の高さの顔の面の点への線でも調べる（頭の真ん中への線は帽子のつばの下を通っても、目はつばで隠れる。A37-008、2026-11-07）
            eye = hm @ Vector((0, hr * 0.95, hr * 1.12))
            if (origin - eye).dot(hm.to_3x3() @ Vector((0, 1, 0))) > 0:   # 顔がカメラの側を向いているときだけ
                occ = _occluder(scene, dg, origin, eye, figs[who][0], 0.03)
        fw = hm.to_3x3() @ Vector((0, 1, 0))   # 顔の向き（顔は頭の +Y の側）
        tc = origin - c
        fa = math.degrees(fw.angle(tc)) if fw.length > 1e-6 and tc.length > 1e-6 else 0.0
        fh = math.degrees(math.atan2(fw.x, fw.y)) % 360.0   # 場面の向き（北 0・東 90。layout は y が南、Blender は y が北。staging.bearing_of と同じ）
        fay, fap = _yaw_pitch(fw, tc)
        out["people"][who] = {"x": round(vc.x, 3), "y": round(vc.y, 3), "z": round(vc.z, 2), "frac": round(abs(vt.y - vb.y), 3),
                              "top": round(vt.y, 3), "fy": round(vf.y, 3), "in": bool(inside), "occ": occ, "hz": round(c.z, 2), "bz": round(feet.z, 2),
                              "wx": round(c.x, 2), "wy": round(-c.y, 2), "fa": round(fa), "fh": round(fh), "fay": round(fay), "fap": round(fap),
                              "bx": round(feet.x, 2), "by": round(-feet.y, 2), "h": float(lay["people"][who]["h"])}
        lm = _landmarks(scene, dg, cam, origin, (faces or {}).get(who), c, figs[who][0]) if vc.z > 0 else None
        if lm:
            out["people"][who]["lm"] = lm
    if look in out["people"] and out["people"][look]["occ"] and look in figs:   # 見る先の顔が隠れている: カメラを少しずらして見える所を探す
        out["probe"] = _probe(scene, dg, cam, previs_rig.RIGS[look], figs[look][0])
    return out


def _yaw_pitch(fw, tc):
    """顔の向き fw とカメラへの向き tc（どちらも Blender の向き）の、横の角度（度、0〜180）と縦の角度（度、カメラが上なら正）。"""
    fx, fy_ = fw.x, fw.y
    cx, cy = tc.x, tc.y
    hf, hc = math.hypot(fx, fy_), math.hypot(cx, cy)
    if hf < 1e-6 or hc < 1e-6:
        yaw = 0.0
    else:
        yaw = math.degrees(math.acos(max(-1.0, min(1.0, (fx * cx + fy_ * cy) / (hf * hc)))))
    pitch = math.degrees(math.atan2(tc.z, max(1e-6, hc))) - math.degrees(math.atan2(fw.z, max(1e-6, hf)))
    return yaw, pitch


def _landmarks(scene, dg, cam, origin, face, center, own_root):
    """両目と口の目印が見えるか（2026-11-08）。face は tools/previs_face.add_face の dict（目 eyes・口 mouth の物）、center は頭の球の真ん中。
    裏かどうかは目印ごとに、頭の真ん中から目印への向き（顔の面の外向き）で決める（真横の顔でも、手前の目は見える）。"""
    if not face:
        return None
    pts = []
    eyes = face.get("eyes") or []
    for name, o in zip(("le", "re"), eyes[:2]):
        pts.append((name, o))
    if face.get("mouth") is not None:
        pts.append(("mo", face["mouth"]))
    if not pts:
        return None
    hr = float(face.get("hr", 0.13))
    out = {}
    for name, o in pts:
        p = o.matrix_world.translation.copy()
        v = world_to_camera_view(scene, cam, p)
        if not (v.z > 0 and 0.0 <= v.x <= 1.0 and 0.0 <= v.y <= 1.0):
            out[name] = "out"
            continue
        nrm = p - center
        if nrm.length < 1e-6 or (origin - p).dot(nrm) <= 0:   # 顔の面の裏（カメラから見て頭の向こう側）
            out[name] = "back"
            continue
        occ = _occluder(scene, dg, origin, p, own_root, hr * 0.3, own_counts=OWN_COVER)
        out[name] = occ or "ok"
    return out


def _probe(scene, dg, cam, rg, own_root):
    """カメラを左右（画面の右の向き）・上下にずらして、顔が隠れない、いちばん小さいずれ（Blender の z と、場面の x・y の m）。見つからなければ None。"""
    hr = float(rg.hr)
    face = rg.head.matrix_world @ Vector((0, 0, hr))
    origin = cam.matrix_world.translation.copy()
    right = (cam.matrix_world.to_quaternion() @ Vector((1, 0, 0)))
    right.z = 0.0
    right = right.normalized() if right.length > 1e-6 else Vector((1, 0, 0))
    cands = sorted({(dx, dz) for dx in (0.0, 0.15, -0.15, 0.3, -0.3, 0.45, -0.45, 0.6, -0.6, 0.8, -0.8) for dz in (0.0, 0.15, 0.3, -0.15, 0.45, -0.3)},   # -0.3 は帽子のつばの下から顔を見る（2026-11-07）
                   key=lambda v: abs(v[0]) + abs(v[1]) * 1.2)
    for dx, dz in cands:
        if dx == 0.0 and dz == 0.0:
            continue
        o = origin + right * dx + Vector((0, 0, dz))
        if _occluder(scene, dg, o, face, own_root, hr * 1.05) is None:
            d = right * dx
            return {"dx": round(d.x, 2), "dy": round(-d.y, 2), "dz": round(dz, 2)}   # 場面の y は Blender の -y
    return None


def run(st, lay, scene_dir, cuts, fps, size, samples, out_dir, work, render_cut, hashes=None, tool_h=None):
    res = {}
    for cut in cuts:
        shot = yaml.safe_load((ROOT / scene_dir / "shots" / f"{cut}.yaml").read_text(encoding="utf-8"))
        rows = []
        render_cut(st, lay, cut, float(shot["sec"]), fps, size, samples, out_dir, work, audit=rows)
        res[cut] = {"sec": float(shot["sec"]), "shot": shot.get("shot"),
                    "input_hash": (hashes or {}).get(cut), "tool_hash": tool_h, "fps": fps,
                    "made": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "samples": rows}
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
    try:
        shown = dst.relative_to(ROOT)
    except ValueError:   # リポジトリの外の写しの場面を測ったとき（テスト。2026-11-08）
        shown = dst
    print(f"{shown}（{len(res)} カット）")
