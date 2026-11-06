#!/usr/bin/env python3
"""戦いの効果と、時間の操作（2026-11-06 の下絵のアップグレード）。tools/previs.py から呼ぶ。

アニメの戦いの見せ方（溜めと解放、当たる瞬間の閃光と止め、集中線、破片と土煙）を、下絵で見せるための道具。
blocking.yaml のカットの fx に書く（t はカットの頭からの秒。timewarp があるときは「場面の時刻」）。

  立体の効果（場面の中に置く）:
    {kind: impact, t: 1.2, at: sand-eater, z: 2.0, size: 1.5, color: [255, 240, 200]}
        当たる瞬間の閃光。とがった星形がぱっと開いてしぼむ（0.18秒）。輪が広がる。at は人物・生き物（頭）・場所・[x, y]・[x, y, z]
    {kind: shock, t: 1.2, at: hk, r: 4.0, dur: 0.5}          地面を走る衝撃の輪
    {kind: debris, t: 1.2, at: [x, y], n: 18, speed: 7, size: 0.25, up: 0.8, color: [...]}   飛び散る岩・殻のかけら（重さで落ちる）
    {kind: dust, t: 1.2, at: hk, r: 1.5, h: 1.0, dur: 1.2}    着地・叩きつけの土煙（広がって薄れる）
    {kind: aura, t: [0.5, 3.0], at: hundred-knight, r: 0.6, h: 1.2, color: [...]}   体のまわりの光（力をためる・目が光る）
    {kind: charge, t: [0.0, 1.5], at: [x, y, z], r: 2.0, n: 14, color: [...]}       光の粒が一点へ集まる（魔力をためる）
    {kind: beam, t: [1.0, 1.6], at: [x, y, z], to: [x, y, z], r: 0.3, color: [...]}  光の帯（光線・雷の柱）
    {kind: slashmark, t: 1.2, at: [x, y, z], r: 1.4, face: 90, tilt: 30, dur: 0.25}  宙に残る三日月の斬撃の跡
    {kind: sparks, t: 1.2, at: [x, y, z], n: 16, speed: 6, dur: 0.35, dir: 90, spread: 80}  火花の筋（剣が殻に当たる。dir は飛ぶ方位）
    {kind: wall, t: [0.6, 2.4], at: [x, y, z], face: 180, r: 1.2, color: [...]}   魔法の壁（六角の半透明の板。face は板の向く方位）。t の終わりで消える（割れる時は debris と重ねる）
    {kind: orb, t: [0.3, 3.0], path: [[t, x, y, z], ...], r: 1.0, color: [...], alpha: 0.85}  動く光の玉・砂の拳・火の玉（path の間をなめらかに動く）
  画面の効果（描いた後のコマに重ねる。2D）:
    {kind: speedlines, t: [1.0, 1.6], focus: hundred-knight, inner: 0.32, n: 70, color: [255, 255, 255]}
        集中線。focus（人物・場所・center）のまわりを空けて、画面の端から線を引く。コマごとに線が変わる
    {kind: flowlines, t: [0.4, 1.0], angle: 0, n: 40}        流線（速さ）。angle は画面上の向き（度、0 で横）
    {kind: impact_frame, t: 1.2, frames: 2, mode: invert}    当たった瞬間の数コマを、白か白黒反転か白黒の強い明暗（mono）に
    {kind: letterbox, t: [0, 4], h: 0.12}                    上下の黒帯（決めの画）
  剣の軌跡: 剣を持つ人物（百騎兵）は、剣先が速く動く間、自動で軌跡（白い三日月の帯）を描く。カットに trail: false で消す

時間の操作（カットの timewarp。t は場面の時刻＝キーの時刻）:
  timewarp:
    - {t: 1.20, hold: 0.10}          # ヒットストップ: 1.20秒で 0.10秒止める（止まっている間はカメラが小さく揺れる。shake で揺れの大きさ）
    - {t: [1.3, 2.1], rate: 0.3}     # スロー（0.3倍の速さ）
    - {t: [2.5, 3.0], rate: 2.0}     # 速回し
  ショットファイルの秒（出来上がりの長さ）はそのまま。場面の時刻は止め・スローの分だけ短くなる。キーはそこまで書けばよい。
"""
import math
import random

import bpy
from mathutils import Matrix, Vector

KINDS_3D = ("impact", "shock", "debris", "dust", "aura", "charge", "beam", "slashmark", "sparks", "wall", "orb")
KINDS_2D = ("speedlines", "flowlines", "impact_frame", "letterbox")


def _rng(i, salt=0):
    return random.Random(1000 * (i + 1) + salt)


def _alpha_mat(P, name, rgb, alpha=1.0, glow=1.0):
    m = P.make_mat(name, rgb, emit=glow)
    m.diffuse_color = (*P.linear(rgb), alpha)
    m["fx_rgb"] = list(rgb)
    m["fx_alpha"] = alpha
    try:
        m.blend_method = "BLEND"
    except Exception:
        pass
    return m


def _set_alpha(m, a):
    c = m.diffuse_color
    m.diffuse_color = (c[0], c[1], c[2], max(0.0, min(1.0, a)))


def _col(fx, default):
    return tuple(v / 255 for v in fx.get("color", default))


def _star_mesh(name, spikes=12, r_in=0.35, r_out=1.0, seed=0):
    rnd = random.Random(seed)
    verts = [(0.0, 0.0, 0.0)]
    n = spikes * 2
    for k in range(n):
        a = 2 * math.pi * k / n
        rr = r_out * (0.65 + 0.35 * rnd.random()) if k % 2 == 0 else r_in
        verts.append((rr * math.cos(a), 0.0, rr * math.sin(a)))
    faces = [(0, 1 + k, 1 + (k + 1) % n) for k in range(n)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    return me


def _obj(name, me, mat):
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(mat)
    o.location = (0, 0, -500)
    return o


def build(P, fx, i):
    """立体の効果の物を作る。効果でなければ None。"""
    kind = fx.get("kind")
    if kind not in KINDS_3D:
        return None
    if kind == "impact":
        col = _col(fx, [255, 246, 215])
        star = _obj(f"fx{i}_star", _star_mesh(f"fx{i}_starm", int(fx.get("spikes", 12)), seed=i), _alpha_mat(P, f"fx{i}_starmat", col, 0.95, 3.0))
        core = _obj(f"fx{i}_core", _star_mesh(f"fx{i}_corem", 8, 0.5, 0.55, seed=i + 7), _alpha_mat(P, f"fx{i}_coremat", (1.0, 1.0, 1.0), 1.0, 4.0))
        bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.04, major_segments=40, minor_segments=6)
        ring = bpy.context.object
        ring.name = f"fx{i}_ring"
        ring.data.materials.append(_alpha_mat(P, f"fx{i}_ringmat", col, 0.8, 2.0))
        ring.location = (0, 0, -500)
        return [star, core, ring]
    if kind == "shock":
        bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.06, major_segments=48, minor_segments=6)
        ring = bpy.context.object
        ring.name = f"fx{i}_shock"
        ring.data.materials.append(_alpha_mat(P, f"fx{i}_shockmat", _col(fx, [250, 235, 200]), 0.75, 1.5))
        ring.location = (0, 0, -500)
        return [ring]
    if kind == "debris":
        rnd = _rng(i, 3)
        col = _col(fx, [120, 92, 70])
        m = P.make_mat(f"fx{i}_deb", col)
        objs = []
        for j in range(int(fx.get("n", 16))):
            s = float(fx.get("size", 0.22)) * (0.4 + rnd.random())
            o = P.prim("cube", m, f"fx{i}_deb{j}", None, (0, 0, -500), size=1.0)
            o.scale = (s, s * (0.6 + 0.6 * rnd.random()), s * (0.5 + 0.5 * rnd.random()))
            objs.append(o)
        return objs
    if kind == "dust":
        col = _col(fx, [205, 180, 140])
        m = _alpha_mat(P, f"fx{i}_dust", col, 0.6, 0.0)
        objs = []
        for j in range(int(fx.get("n", 10))):
            o = P.prim("sphere", m, f"fx{i}_dust{j}", None, (0, 0, -500), radius=1.0)
            objs.append(o)
        return objs
    if kind == "aura":
        m = _alpha_mat(P, f"fx{i}_aura", _col(fx, [140, 210, 255]), 0.35, 2.0)
        o = P.prim("sphere", m, f"fx{i}_aura", None, (0, 0, -500), radius=1.0)
        m2 = _alpha_mat(P, f"fx{i}_aura2", _col(fx, [140, 210, 255]), 0.22, 2.0)
        o2 = P.prim("sphere", m2, f"fx{i}_aura2", None, (0, 0, -500), radius=1.0)
        return [o, o2]
    if kind == "charge":
        m = _alpha_mat(P, f"fx{i}_charge", _col(fx, [180, 230, 255]), 0.95, 3.0)
        objs = [P.prim("sphere", m, f"fx{i}_ch{j}", None, (0, 0, -500), radius=1.0) for j in range(int(fx.get("n", 14)))]
        core = P.prim("sphere", _alpha_mat(P, f"fx{i}_chcore", (1, 1, 1), 0.9, 4.0), f"fx{i}_chcore", None, (0, 0, -500), radius=1.0)
        return objs + [core]
    if kind == "beam":
        m = _alpha_mat(P, f"fx{i}_beam", _col(fx, [200, 235, 255]), 0.85, 3.0)
        o = P.prim("cyl", m, f"fx{i}_beam", None, (0, 0, -500), radius=1.0, depth=1.0, vertices=16)
        mc = _alpha_mat(P, f"fx{i}_beamcore", (1, 1, 1), 1.0, 4.0)
        oc = P.prim("cyl", mc, f"fx{i}_beamcore", None, (0, 0, -500), radius=1.0, depth=1.0, vertices=12)
        return [o, oc]
    if kind == "sparks":
        m = _alpha_mat(P, f"fx{i}_spk", _col(fx, [255, 236, 160]), 1.0, 4.0)
        return [P.prim("cyl", m, f"fx{i}_spk{j}", None, (0, 0, -500), radius=1.0, depth=1.0, vertices=6) for j in range(int(fx.get("n", 16)))]
    if kind == "wall":
        m = _alpha_mat(P, f"fx{i}_wall", _col(fx, [170, 225, 255]), float(fx.get("alpha", 0.5)), 1.5)
        o = P.prim("cyl", m, f"fx{i}_wall", None, (0, 0, -500), radius=1.0, depth=1.0, vertices=6)
        mr = _alpha_mat(P, f"fx{i}_wallrim", _col(fx, [210, 245, 255]), 0.9, 2.5)
        bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.035, major_segments=6, minor_segments=4)
        rim = bpy.context.object
        rim.name = f"fx{i}_wallrim"
        rim.data.materials.append(mr)
        rim.location = (0, 0, -500)
        return [o, rim]
    if kind == "orb":
        m = _alpha_mat(P, f"fx{i}_orb", _col(fx, [200, 160, 100]), float(fx.get("alpha", 0.9)), float(fx.get("glow", 0.5)))
        o = P.prim("sphere", m, f"fx{i}_orb", None, (0, 0, -500), radius=1.0)
        m2 = _alpha_mat(P, f"fx{i}_orb2", _col(fx, [200, 160, 100]), 0.3, float(fx.get("glow", 0.5)))
        o2 = P.prim("sphere", m2, f"fx{i}_orb2", None, (0, 0, -500), radius=1.0)
        return [o, o2]
    if kind == "slashmark":
        n = 24
        verts, faces = [], []
        for k in range(n + 1):
            a = math.radians(-70 + 140 * k / n)
            w = math.sin(math.pi * k / n) * 0.22 + 0.01
            verts.append((math.cos(a), 0.0, math.sin(a)))
            verts.append(((1 - w) * math.cos(a), 0.0, (1 - w) * math.sin(a)))
        for k in range(n):
            faces.append((2 * k, 2 * k + 2, 2 * k + 3, 2 * k + 1))
        me = bpy.data.meshes.new(f"fx{i}_slashm")
        me.from_pydata(verts, [], faces)
        me.update()
        return [_obj(f"fx{i}_slash", me, _alpha_mat(P, f"fx{i}_slashmat", _col(fx, [235, 250, 255]), 0.95, 3.0))]
    return None


def _cam_facing(o, cam, p):
    """平たい物（XZ 面）をカメラへ向ける。"""
    d = cam.matrix_world.translation - p
    if d.length < 1e-6:
        return
    q = d.to_track_quat("-Y", "Z")
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = q


def pose(P, fx, objs, t, ctx):
    """t（場面の時刻）での効果の形。効果でなければ False。"""
    kind = fx.get("kind")
    if kind not in KINDS_3D:
        return False
    cam = ctx["cam"]
    hide = Vector((0, 0, -500))
    tt = fx.get("t", 0.0)
    t0 = float(tt[0] if isinstance(tt, (list, tuple)) else tt)
    t1 = float(tt[1]) if isinstance(tt, (list, tuple)) else t0 + float(fx.get("dur", 0.25))
    p = Vector(ctx["where"](fx.get("at", [0, 0]), t0 if kind in ("impact", "shock", "debris", "dust", "slashmark") else t, fx.get("z")))
    if kind == "impact":
        dur = float(fx.get("dur", 0.2))
        u = (t - t0) / dur
        star, core, ring = objs
        if u < 0 or u > 1.0:
            for o in objs:
                o.location = hide
            return True
        size = float(fx.get("size", 1.2))
        k = (1.0 - u) ** 0.6 if u > 0.15 else 0.75 + 0.25 * u / 0.15
        for o, f in ((star, 1.0), (core, 0.55)):
            o.location = p
            _cam_facing(o, cam, p)
            o.rotation_quaternion = o.rotation_quaternion @ Matrix.Rotation(0.4 * u + 0.45 * (int(t * 24) % 7) * f, 4, "Y").to_quaternion()
            o.scale = (size * f * k, 1.0, size * f * k)
        ring.location = p
        _cam_facing(ring, cam, p)
        ring.rotation_quaternion = ring.rotation_quaternion @ Matrix.Rotation(math.pi / 2, 4, "X").to_quaternion()
        rr = size * (0.3 + 1.6 * u)
        ring.scale = (rr, rr, rr)
        _set_alpha(ring.data.materials[0], 0.85 * (1 - u))
        _set_alpha(star.data.materials[0], 0.95 * (1 - u * u))
        return True
    if kind == "shock":
        dur = float(fx.get("dur", 0.5))
        u = (t - t0) / dur
        ring = objs[0]
        if u < 0 or u > 1:
            ring.location = hide
            return True
        r = float(fx.get("r", 3.0)) * (0.15 + 0.85 * (1 - (1 - u) ** 2))
        ring.location = (p.x, p.y, p.z + 0.08)   # 置き場所の高さ（[x, y] なら地面、[x, y, z] ならその高さ。峰の上など）
        ring.rotation_euler = (0, 0, 0)
        ring.scale = (r, r, max(0.2, 1.4 * (1 - u)))
        _set_alpha(ring.data.materials[0], 0.8 * (1 - u))
        return True
    if kind == "debris":
        rnd = _rng(int(fx.get("seed", 0)) + len(objs), 5)
        sp = float(fx.get("speed", 6.0))
        up = float(fx.get("up", 0.7))
        g = 9.8 * float(fx.get("gravity", 1.0))
        dirb = fx.get("dir")   # 飛ぶ向き（方位、度）。なければ全方向
        spread = math.radians(float(fx.get("spread", 70 if dirb is not None else 180)))
        for j, o in enumerate(objs):
            a = (math.radians(90 - float(dirb)) if dirb is not None else 0.0) + (rnd.random() * 2 - 1) * spread
            el = math.radians(15 + 70 * up * rnd.random())
            v = sp * (0.45 + 0.75 * rnd.random())
            vx, vy, vz = v * math.cos(el) * math.cos(a), v * math.cos(el) * math.sin(a), v * math.sin(el)
            spin = (rnd.random() * 2 - 1) * 14
            dt = t - t0
            if dt < 0:
                o.location = hide
                continue
            gz = float(fx["floor"]) if fx.get("floor") is not None else ctx["ground"](p)   # 落ちて止まる高さ（floor。峰の上の広場など）
            z = p.z + vz * dt - 0.5 * g * dt * dt
            if z < gz:   # 地面に落ちたら止まる
                tl = (vz + math.sqrt(max(0.0, vz * vz + 2 * g * (p.z - gz)))) / g
                dt = tl
                z = gz + o.scale.z * 0.5
            o.location = (p.x + vx * dt, p.y + vy * dt, z)
            o.rotation_euler = (spin * dt, spin * 0.7 * dt, spin * 0.4 * dt)
        return True
    if kind == "dust":
        dur = float(fx.get("dur", 1.2))
        u = (t - t0) / dur
        rnd = _rng(len(objs), 9)
        r = float(fx.get("r", 1.5))
        h = float(fx.get("h", 0.8))
        gz = p.z
        for j, o in enumerate(objs):
            a = 2 * math.pi * j / len(objs) + rnd.random() * 0.6
            if u < 0 or u > 1:
                o.location = hide
                continue
            e = 1 - (1 - u) ** 2.2
            d = r * (0.25 + 0.95 * e) * (0.7 + 0.5 * rnd.random())
            s = (0.2 + 0.45 * e) * r * 0.32 * (0.7 + 0.6 * rnd.random())
            o.location = (p.x + d * math.cos(a), p.y + d * math.sin(a), gz + h * (0.15 + 0.7 * e) * (0.5 + rnd.random() * 0.5))
            o.scale = (s, s, s * 0.75)
        if objs:
            _set_alpha(objs[0].data.materials[0], 0.42 * (1 - u) ** 1.5 if 0 <= u <= 1 else 0.0)
        return True
    if kind == "aura":
        a, b = objs
        if t < t0 or t > t1:
            a.location = b.location = hide
            return True
        u = (t - t0) / max(1e-6, t1 - t0)
        env = min(1.0, (t - t0) / 0.2, (t1 - t) / 0.25)
        r = float(fx.get("r", 0.6))
        h = float(fx.get("h", 1.2))
        pul = 1.0 + 0.08 * math.sin(2 * math.pi * 5 * t)
        base = Vector((p.x, p.y, p.z + h * 0.5))
        a.location = base
        a.scale = (r * pul, r * pul, h * 0.6 * pul)
        b.location = base
        b.scale = (r * 1.35 * pul, r * 1.35 * pul, h * 0.75 * pul)
        _set_alpha(a.data.materials[0], 0.38 * env)
        _set_alpha(b.data.materials[0], 0.2 * env)
        return True
    if kind == "charge":
        n = len(objs) - 1
        core = objs[-1]
        if t < t0 or t > t1:
            for o in objs:
                o.location = hide
            return True
        u = (t - t0) / max(1e-6, t1 - t0)
        rnd = _rng(n, 13)
        R = float(fx.get("r", 2.0))
        for j, o in enumerate(objs[:-1]):
            ph = (u * 1.8 + j / n) % 1.0
            a = 2 * math.pi * rnd.random()
            el = math.asin(rnd.random() * 2 - 1)
            d = R * (1 - ph)
            o.location = (p.x + d * math.cos(el) * math.cos(a), p.y + d * math.cos(el) * math.sin(a), p.z + d * math.sin(el))
            s = 0.035 * R * (0.5 + ph)
            o.scale = (s, s, s)
        cs = 0.08 * R * (0.4 + 1.2 * u) * (1 + 0.15 * math.sin(2 * math.pi * 7 * t))
        core.location = p
        core.scale = (cs, cs, cs)
        return True
    if kind == "beam":
        o, oc = objs
        if t < t0 or t > t1:
            o.location = oc.location = hide
            return True
        q = Vector(ctx["where"](fx.get("to", [0, 0]), t, fx.get("to_z")))
        u = (t - t0) / max(1e-6, t1 - t0)
        grow = min(1.0, (t - t0) / max(1e-6, float(fx.get("grow", 0.12))))
        end = p + (q - p) * grow
        d = end - p
        L = max(1e-3, d.length)
        r = float(fx.get("r", 0.3)) * (1 + 0.2 * math.sin(2 * math.pi * 9 * t)) * min(1.0, (t1 - t) / 0.15)
        for ob, rr in ((o, r), (oc, r * 0.45)):
            ob.location = p + d / 2
            ob.rotation_mode = "QUATERNION"
            ob.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d.normalized())
            ob.scale = (rr, rr, L)
        return True
    if kind == "wall":
        o, rim = objs
        if t < t0 or t > t1:
            o.location = rim.location = hide
            return True
        g = min(1.0, (t - t0) / 0.2)
        g = 1 - (1 - g) ** 3
        r = float(fx.get("r", 1.2)) * g
        rot = (math.pi / 2, 0, -math.radians(float(fx.get("face", 0))))
        for ob, sc in ((o, (r, r, 0.06)), (rim, (r, r, r))):
            ob.location = p
            ob.rotation_mode = "XYZ"
            ob.rotation_euler = rot
            ob.scale = sc
        return True
    if kind == "orb":
        o, o2 = objs
        if t < t0 or t > t1:
            o.location = o2.location = hide
            return True
        path = fx.get("path") or [[t0, *fx.get("at", [0, 0, 0])]]
        a = path[0]
        b = path[-1]
        for x0, x1 in zip(path, path[1:]):
            if x0[0] <= t <= x1[0]:
                a, b = x0, x1
                break
        else:
            a = b = path[-1] if t > path[-1][0] else path[0]
        u = 0.0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
        u = u * u * (3 - 2 * u)
        q = [a[k + 1] + (b[k + 1] - a[k + 1]) * u for k in range(3)]
        pos_ = Vector(ctx["where"]([q[0], q[1], q[2]], t))
        g = min(1.0, (t - t0) / 0.25, (t1 - t) / 0.15)
        r = float(fx.get("r", 1.0)) * max(0.05, g) * (1 + 0.06 * math.sin(2 * math.pi * 6 * t))
        o.location = o2.location = pos_
        o.scale = (r, r, r)
        o2.scale = (r * 1.35, r * 1.35, r * 1.35)
        return True
    if kind == "sparks":
        dur = float(fx.get("dur", 0.35))
        rnd = _rng(int(fx.get("seed", 0)) + len(objs), 17)
        sp = float(fx.get("speed", 6.0))
        dirb = fx.get("dir")
        spread = math.radians(float(fx.get("spread", 80 if dirb is not None else 180)))
        times = fx.get("times") or [t0]   # 何度か散らす（引っかく・しがみつく間）
        for j, o in enumerate(objs):
            a = (math.radians(90 - float(dirb)) if dirb is not None else 0.0) + (rnd.random() * 2 - 1) * spread
            el = math.radians(-20 + 80 * rnd.random())
            v = sp * (0.5 + 0.8 * rnd.random())
            tj = times[j % len(times)]
            dt = t - tj
            if dt < 0 or dt > dur:
                o.location = hide
                continue
            vel = Vector((v * math.cos(el) * math.cos(a), v * math.cos(el) * math.sin(a), v * math.sin(el)))
            pos_ = p + vel * dt + Vector((0, 0, -4.9 * dt * dt))
            L = max(0.05, 0.06 * vel.length) * (1 - dt / dur)
            o.location = pos_ - vel.normalized() * L * 0.5
            o.rotation_mode = "QUATERNION"
            o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(vel.normalized())
            o.scale = (0.012, 0.012, L)
        return True
    if kind == "slashmark":
        o = objs[0]
        dur = float(fx.get("dur", 0.25))
        u = (t - t0) / dur
        if u < 0 or u > 1:
            o.location = hide
            return True
        r = float(fx.get("r", 1.4)) * (1 + 0.15 * u)
        o.location = p
        o.rotation_mode = "XYZ"
        o.rotation_euler = (math.radians(float(fx.get("tilt", 0))), 0, -math.radians(float(fx.get("face", 0))))
        o.scale = (r, r, r)
        _set_alpha(o.data.materials[0], 0.95 * (1 - u) ** 0.7)
        return True
    return False


# ---------- 剣の軌跡 ----------

class Trail:
    """剣先の通り道の帯。少し前の時刻の剣の位置を、人物の姿勢を当て直して取る。"""

    def __init__(self, P, who, rig, n=10, span=0.09, min_speed=7.0):
        self.who, self.rig, self.n, self.span, self.min_speed = who, rig, n, span, min_speed
        me = bpy.data.meshes.new(f"TRAIL_{who}_m")
        nv = 2 * (n + 1)
        me.from_pydata([(0, 0, 0)] * nv, [], [(2 * k, 2 * k + 2, 2 * k + 3, 2 * k + 1) for k in range(n)])
        me.update()
        self.mat = _alpha_mat(P, f"TRAIL_{who}_mat", (0.92, 0.97, 1.0), 0.78, 3.0)
        self.obj = bpy.data.objects.new(f"TRAIL_{who}", me)
        bpy.context.collection.objects.link(self.obj)
        self.obj.data.materials.append(self.mat)
        self.obj.hide_render = True

    def update(self, t, pose_at, active=True):
        """t の剣の帯。pose_at(tt) はその人物を時刻 tt の姿勢にする（最後に t へ戻す）。active が偽（走る・歩く間）なら描かない。"""
        rg = self.rig
        if not active:
            self.obj.hide_render = True
            return
        import previs_rig
        fk = previs_rig.fk

        def sample(tt):
            pose_at(tt)
            return (fk(rg.tip).translation.copy(), fk(rg.blade_mid).translation.copy(), fk(rg.pelvis).translation.copy())
        # まず端の2つだけ見て、剣先が速く動いていなければ帯を作らない（ほとんどのコマはここで終わる）
        a0, _, p0 = sample(t - self.span)
        a1, _, p1 = sample(t)
        if ((a1 - p1) - (a0 - p0)).length / self.span < self.min_speed * 0.6:
            self.obj.hide_render = True
            return
        tips, mids, pel = [], [], []
        for j in range(self.n, -1, -1):
            a, m, pv = sample(t - self.span * j / self.n)
            tips.append(a)
            mids.append(m)
            pel.append(pv)
        # 体の動きを除いた剣先の速さ
        rel = sum(((tips[k + 1] - pel[k + 1]) - (tips[k] - pel[k])).length for k in range(self.n))
        speed = rel / self.span
        if speed < self.min_speed:
            self.obj.hide_render = True
            return
        me = self.obj.data
        for k in range(self.n + 1):
            f = k / self.n   # 0 が古い、1 が今
            inner = tips[k] + (mids[k] - tips[k]) * (0.08 + 0.5 * f * f)
            me.vertices[2 * k].co = tips[k]
            me.vertices[2 * k + 1].co = inner
        me.update()
        self.obj.hide_render = False
        _set_alpha(self.mat, min(0.85, 0.35 + speed / 40.0))


# ---------- 時間の操作 ----------

class TimeWarp:
    """出来上がりの時刻 T → 場面の時刻 s。止め（hold）・スロー・速回し（rate）。"""

    def __init__(self, spec):
        ev = []
        for e in spec or []:
            if "hold" in e:
                ev.append((float(e["t"]), float(e["t"]), 0.0, float(e["hold"]), float(e.get("shake", 1.2))))
            else:
                a, b = e["t"]
                ev.append((float(a), float(b), float(e.get("rate", 0.5)), 0.0, float(e.get("shake", 0.0))))
        ev.sort()
        self.pts = [(0.0, 0.0, 1.0)]   # (T, s, この点から先の傾き)
        self.holds = []
        T, s = 0.0, 0.0
        for a, b, rate, hold, shake in ev:
            if a > s:
                T += a - s
                s = a
                self.pts.append((T, s, 1.0))
            if hold > 0:
                self.holds.append((T, T + hold, shake))
                self.pts.append((T, s, 0.0))
                T += hold
                self.pts.append((T, s, 1.0))
            else:
                self.pts.append((T, s, rate))
                T += (b - a) / max(1e-6, rate)
                s = b
                self.pts.append((T, s, 1.0))
        self.active = bool(ev)

    def scene_t(self, T):
        if not self.active:
            return T
        T0, s0, k = self.pts[0]
        for Ta, sa, ka in self.pts:
            if Ta <= T:
                T0, s0, k = Ta, sa, ka
            else:
                break
        return s0 + (T - T0) * k

    def hold_shake(self, T):
        """止めている間のカメラの揺れ（度）。"""
        for a, b, amp in self.holds:
            if a <= T <= b and amp > 0:
                e = math.sin(math.pi * (T - a) / max(1e-6, b - a)) ** 0.5
                return amp * e * math.sin(91.0 * T), amp * e * math.cos(67.0 * T + 0.7)
        return 0.0, 0.0


# ---------- 画面の効果（2D） ----------

def post(path, fxs, t, frame_i, ctx):
    """描いたコマ（PNG）に、集中線・流線・反転などを重ねる。"""
    todo = []
    for fx in fxs:
        k = fx.get("kind")
        if k not in KINDS_2D:
            continue
        tt = fx.get("t", 0.0)
        if k == "impact_frame":
            t0 = float(tt[0] if isinstance(tt, (list, tuple)) else tt)
            frames = int(fx.get("frames", 2))
            if 0 <= (frame_i - round(t0 * ctx["fps"])) < frames:
                todo.append(fx)
            continue
        a, b = (float(tt[0]), float(tt[1])) if isinstance(tt, (list, tuple)) else (float(tt), float(tt) + 0.5)
        if a <= t <= b:
            todo.append(fx)
    if not todo:
        return
    from PIL import Image, ImageDraw, ImageOps
    im = Image.open(path).convert("RGB")
    W, H = im.size
    for fx in todo:
        k = fx["kind"]
        if k == "speedlines":
            focus = fx.get("focus", "center")
            if focus == "center":
                fx_, fy_ = W / 2, H / 2
            else:
                v = ctx["project"](focus, t, fx.get("z"))
                fx_, fy_ = (v[0] * W, (1 - v[1]) * H) if v else (W / 2, H / 2)
            rnd = random.Random(frame_i * 7 + 3)
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(ov)
            col = tuple(fx.get("color", [255, 255, 255]))
            inner = float(fx.get("inner", 0.32)) * min(W, H)
            R = math.hypot(W, H)
            for j in range(int(fx.get("n", 70))):
                a = rnd.random() * 2 * math.pi
                r0 = inner * (1.0 + 0.6 * rnd.random())
                wdt = math.radians(0.25 + 0.9 * rnd.random())
                p0 = (fx_ + r0 * math.cos(a), fy_ + r0 * math.sin(a))
                p1 = (fx_ + R * math.cos(a - wdt), fy_ + R * math.sin(a - wdt))
                p2 = (fx_ + R * math.cos(a + wdt), fy_ + R * math.sin(a + wdt))
                d.polygon([p0, p1, p2], fill=(*col, int(150 + 90 * rnd.random())))
            im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
        elif k == "flowlines":
            rnd = random.Random(frame_i * 11 + 5)
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(ov)
            ang = math.radians(float(fx.get("angle", 0.0)))
            ca, sa = math.cos(ang), math.sin(ang)
            col = tuple(fx.get("color", [255, 255, 255]))
            for j in range(int(fx.get("n", 40))):
                cx, cy = rnd.random() * W, rnd.random() * H
                L = (0.15 + 0.45 * rnd.random()) * W
                wd = 1 + int(3 * rnd.random())
                d.line([(cx - ca * L / 2, cy - sa * L / 2), (cx + ca * L / 2, cy + sa * L / 2)], fill=(*col, int(110 + 100 * rnd.random())), width=wd)
            im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
        elif k == "impact_frame":
            mode = fx.get("mode", "invert")
            if mode == "invert":
                im = ImageOps.invert(im)
            elif mode == "white":
                im = Image.blend(im, Image.new("RGB", (W, H), (255, 255, 255)), 0.75)
            elif mode == "mono":   # アニメの「インパクトのコマ」: 白黒の強い明暗
                g = ImageOps.grayscale(im)
                g = g.point(lambda v: 255 if v > 92 else 0)
                im = ImageOps.invert(g).convert("RGB") if fx.get("invert", True) else g.convert("RGB")
        elif k == "letterbox":
            h = int(float(fx.get("h", 0.12)) * H)
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, W, h], fill=(0, 0, 0))
            d.rectangle([0, H - h, W, H], fill=(0, 0, 0))
    im.save(path)
