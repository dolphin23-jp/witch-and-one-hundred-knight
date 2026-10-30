#!/usr/bin/env python3
"""blocking.yaml から、人形が動く下絵動画（プリビズ）を Blender（bpy）で作る（D-055）。

Seedance の「動きとカメラの参照動画」に渡すための、配置・向き・移動・カメラだけを写した動画。顔と衣装は写さない（それは設定画と静止画で固定する）。
tools/staging.py と同じ計算（位置、体の向き、目で追う相手、カメラ、レンズ）を使い、カットの秒数だけ時間を進める。
画面の操作はしない。bpy を Python から動かすだけ（D-035）。

  # セッションごとに作業用の venv に入れる（約20秒）
  python3 -m venv $VENV && $VENV/bin/pip install -q bpy pyyaml pillow
  $VENV/bin/python tools/previs.py episodes/A/scenes/A01b A01b-001 A01b-030 A01b-031

出力（previs/<シーン>/）:
  <カット>.mp4         下絵動画（既定 768x432、24fps、音なし）
  <カット>_check.jpg   上: 採用した静止画 / 下: 下絵の始め・中ほど・終わり。構図が合っているかの目視用

blocking.yaml に足した任意の項目（staging.py は読まない）:
  move.<人物>.t: [始め, 終わり]   移動する秒（カットの頭から）。省略はカット全体。動きは滑らかに始まり滑らかに止まる
  cam_end: [x, y, 高さ]          カメラ本体が動くとき（押し込み・引き）の終わりの位置
  cam_t: [始め, 終わり]          カメラが動く秒。省略はカット全体
  動きのキー（D-060）: keys（人物）・cam_keys（カメラ）・shake（揺れ）・creatures（砂食みなど）・fx（砂の噴き上げ・波紋）。
  書き方は tools/motion.py の頭のコメント。人物は砂丘（layout の dune*）の上では高さが上がる。

動きの確認（D-060）: --motion 0.5 で 0.5秒ごとのコマだけ描き、<カット>_motion.jpg（秒を書いて並べた画像）を作る。動画は作らない。

Blender の描画は Cycles（CPU）。GPU がないクラウド環境では EEVEE と Workbench は使えない。
"""
import argparse
import math
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import staging  # noqa: E402
import motion  # noqa: E402  動きのキー（D-060）

import bpy  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Vector  # noqa: E402


# ---------- 時間と動き ----------

def smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def lerp(a, b, u):
    return a + (b - a) * u


def lerp_bearing(a, b, u):
    return (a + staging.ang_diff(b, a) * u) % 360


def lerp_pt(a, b, u):
    return tuple(lerp(x, y, u) for x, y in zip(a, b))


def catmull(points, s):
    """点列を通る滑らかな曲線上の、弦の長さで s（0〜1）の位置。"""
    if len(points) == 1:
        return points[0]
    if len(points) == 2:
        return lerp_pt(points[0], points[1], s)
    seg = [math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total = sum(seg) or 1.0
    d = s * total
    i = 0
    while i < len(seg) - 1 and d > seg[i]:
        d -= seg[i]
        i += 1
    u = d / (seg[i] or 1.0)
    p0 = points[max(i - 1, 0)]
    p1, p2 = points[i], points[i + 1]
    p3 = points[min(i + 2, len(points) - 1)]
    return tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * u + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * u * u
                        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * u ** 3) for k in range(2))


class Plan:
    """1カットの、時刻ごとの人物の位置・体の向き・頭の向きとカメラ。"""

    def __init__(self, st, cut, sec):
        self.st, self.cut, self.sec = st, cut, sec
        self.c = st.bk["cuts"][cut]
        self.s = st.states[cut]
        self.start_pos, self.start_face = self.s["start"]
        self.end_pos, self.end_face = self.s["end"]
        self.people = list(self.start_pos)
        only = self.c.get("only")
        self.shown = [w for w in self.people if only is None or w in only]
        self.posture_t = self.c.get("posture_t", sec / 2)   # 姿勢が変わる秒（posture → posture_end）
        # 動きのキー（D-060）。keys のある人物は、時刻ごとの位置・高さ・姿勢をキーから取る
        self.tracks = {w: motion.PersonTrack(ks, st.place) for w, ks in (self.c.get("keys") or {}).items()}
        self.camtrack = motion.CamTrack(self.c["cam_keys"]) if self.c.get("cam_keys") else None
        self.creatures = {n: motion.CreatureTrack(s, st.place) for n, s in (self.c.get("creatures") or {}).items()}

    # 人物
    def _window(self, who):
        mv = (self.c.get("move") or {}).get(who) or {}
        t = mv.get("t") or [0.0, self.sec]
        return t[0], t[1]

    def _path(self, who):
        mv = (self.c.get("move") or {}).get(who) or {}
        pts = [tuple(self.start_pos[who])] + [tuple(v) for v in (mv.get("via") or [])] + [tuple(self.end_pos[who])]
        return pts

    def ease(self, who, t):
        t0, t1 = self._window(who)
        return smooth((t - t0) / max(1e-6, t1 - t0))

    def position(self, who, t):
        if who in self.tracks:
            return self.tracks[who].at(t)[0]
        if self.start_pos[who] == self.end_pos[who] and not (self.c.get("move") or {}).get(who, {}).get("via"):
            return tuple(self.start_pos[who])
        return catmull(self._path(who), self.ease(who, t))

    def positions(self, t):
        return {w: self.position(w, t) for w in self.people}

    def height(self, who, t):
        """地面からの高さ m（跳ぶ・埋まる）。キーのない人物は 0。"""
        return self.tracks[who].at(t)[1] if who in self.tracks else 0.0

    def pose_name(self, who, t):
        if who in self.tracks:
            return self.tracks[who].at(t)[2]
        pst = staging.posture_of(self.c, "end" if t >= self.posture_t else "start", who)
        return {"lying": "lie", "sitting": "sit", "kneeling": "kneel", "crouching": "crouch"}.get(pst, "stand")

    def face(self, who, t, pos):
        if who in self.tracks:
            kind, fa, fb, u = self.tracks[who].face_key(t)

            def resolve(f):
                if f is None:
                    return self.start_face[who]
                if isinstance(f, str) and f.startswith("@"):
                    f = f[1:]
                return self.st.face_of(f, who, pos)
            if kind == "dir":
                return fa
            if kind == "lerp":
                return lerp_bearing(resolve(fa), resolve(fb), u)
            return resolve(fa)
        e = self.ease(who, t)
        ts, te = self.s["track_start"].get(who), self.s["track_end"].get(who)
        if te:
            tracked = staging.bearing_of((pos[te][0] - pos[who][0], pos[te][1] - pos[who][1]))
            w = 1.0 if ts == te else e
            base = self.start_face[who] if ts != te else tracked
            return lerp_bearing(base, tracked, w)
        if ts:
            tracked = staging.bearing_of((pos[ts][0] - pos[who][0], pos[ts][1] - pos[who][1]))
            return lerp_bearing(tracked, self.end_face[who], e)
        return lerp_bearing(self.start_face[who], self.end_face[who], e)

    def head_offset(self, who, t, pos, body):
        """視線（gaze）に向かって頭だけ回す（体の向きとの差、±80°まで）。"""
        g0 = (self.c.get("gaze") or {}).get(who)
        if g0 is None:
            return 0.0
        g1 = (self.c.get("gaze_end") or {}).get(who, g0)
        b0 = self.st.face_of(g0, who, pos)
        b1 = self.st.face_of(g1, who, pos)
        b = lerp_bearing(b0, b1, smooth(t / max(self.sec, 1e-6)))
        return max(-80.0, min(80.0, staging.ang_diff(b, body)))

    # カメラ
    def _cam_t(self):
        t = self.c.get("cam_t") or [0.0, self.sec]
        return t[0], t[1]

    def camera(self, t, pos):
        c = self.c
        if self.camtrack:
            def look_pt(v, p):
                return tuple(p[v]) if isinstance(v, str) and v in p else tuple(self.st.place(v))

            def look_height(v):
                if isinstance(v, str) and v in self.st.lay["people"]:
                    return self.st.lay["people"][v]["h"] * 0.75 + (self.height(v, t) if v in pos else 0.0)
                return 0.0
            heads = {n: tr.head(t) for n, tr in self.creatures.items()}
            pos2 = {**pos, **{n: h[:2] for n, h in heads.items()}}

            def base_z(name, x, y):   # follow の高さの土台: 生き物は頭の高さ、人物はカメラの下の地面
                return heads[name][2] if name in heads else motion.ground_z(self.st.lay, x, y)

            def look_height2(v):
                if isinstance(v, str) and v in heads:
                    return heads[v][2]
                if isinstance(v, str) and v in pos:
                    x, y = pos[v]
                    return look_height(v) + motion.ground_z(self.st.lay, x, y)
                return look_height(v)
            cam, look, lh, lens = self.camtrack.at(t, pos2, look_pt, look_height2, base_z)
            pitch = math.atan2(lh - cam[2], max(0.3, math.dist(cam[:2], look)))
            yaw = staging.bearing_of((look[0] - cam[0], look[1] - cam[1]))
            sy, sp = motion.shake_at(c.get("shake"), t)
            return cam, yaw + sy, pitch + math.radians(sp), lens
        t0, t1 = self._cam_t()
        e = smooth((t - t0) / max(1e-6, t1 - t0))
        cam0 = c["cam"]
        cam1 = c.get("cam_end") or cam0
        cam = [lerp(a, b, e) for a, b in zip(cam0, cam1)]

        def pt(v):
            return tuple(pos[v]) if isinstance(v, str) and v in pos else tuple(self.st.place(v))
        look0 = pt(c["look"])
        look1 = pt(c.get("look_end", c["look"]))
        look = lerp_pt(look0, look1, e)
        lens = lerp(c["lens"], c.get("lens_end", c["lens"]), e)
        pitch = staging.tilt_of({**c, "cam": cam}, tuple(cam[:2]), look)   # 高さはその時刻のカメラの高さ（cam_end で動くとき）
        yaw = staging.bearing_of((look[0] - cam[0], look[1] - cam[1]))
        sy, sp = motion.shake_at(c.get("shake"), t)
        return cam, yaw + sy, pitch + math.radians(sp), lens


# ---------- Blender の場面 ----------

COLORS = {who: tuple(v / 255 for v in rgb) for who, rgb in staging.COLORS.items()}   # 略図と同じ色
# 人形の形。r: 体の半径、hr: 頭の半径、arm: 腕の太さ。背の高さは layout の people.<人物>.h
SHAPE = {
    "metallica": {"r": 0.16, "hr": 0.13, "arm": 0.045},
    "bisco": {"r": 0.15, "hr": 0.125, "arm": 0.045},
    "hundred-knight": {"r": 0.17, "hr": 0.20, "arm": 0.07},
    "arlecchino": {"r": 0.27, "hr": 0.21, "arm": 0.09},
    "luckiny": {"r": 0.14, "hr": 0.12, "arm": 0.04},
    "tito": {"r": 0.15, "hr": 0.125, "arm": 0.045},
    "water-merchant": {"r": 0.24, "hr": 0.14, "arm": 0.06},
}
SHAPE_DEFAULT = {"r": 0.16, "hr": 0.13, "arm": 0.05}


def linear(rgb):
    return tuple(c ** 2.2 for c in rgb)


def make_mat(name, rgb, rough=0.85, emit=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*linear(rgb), 1.0)
    b.inputs["Roughness"].default_value = rough
    if emit:
        b.inputs["Emission Color"].default_value = (*linear(rgb), 1.0)
        b.inputs["Emission Strength"].default_value = emit
    return m


def smooth_shade(obj):
    if obj.type == "MESH":
        for p in obj.data.polygons:
            p.use_smooth = True


def prim(kind, mat, name, parent=None, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1), **kw):
    if kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(vertices=28, **kw)
    elif kind == "cone":
        bpy.ops.mesh.primitive_cone_add(**{"vertices": 28, **kw})
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=28, ring_count=14, **kw)
    elif kind == "cube":
        bpy.ops.mesh.primitive_cube_add(**kw)
    o = bpy.context.object
    o.name = name
    o.data.materials.append(mat)
    o.parent = parent
    o.location = loc
    o.rotation_euler = rot
    o.scale = scale
    smooth_shade(o)
    return o


def bl(p, z=0.0):
    """layout の座標（x 東、y 南）→ Blender（x 東、y 北）。"""
    return (p[0], -p[1], z)


def poly_mesh(name, pts, z, mat):
    me = bpy.data.meshes.new(name)
    verts = [(x, -y, z) for x, y in pts]
    if sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(verts, verts[1:] + verts[:1])) < 0:   # 法線を上向きに
        verts.reverse()
    me.from_pydata(verts, [], [list(range(len(verts)))])
    me.update()
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(mat)
    return o


def box(mat, name, cx, cy, w, d, h, z0=0.0, rot=0.0):
    """底の中心 (cx, cy)・高さ z0 から、幅 w（東西）・奥行き d（南北）・高さ h の箱。rot は上から見て時計回りの度。"""
    return prim("cube", mat, name, loc=bl((cx, cy), z0 + h / 2), rot=(0, 0, -math.radians(rot)), scale=(w / 2, d / 2, h / 2), size=2.0)


def roof_on(mat, name, cx, cy, w, d, z0, rh, rot=0.0):
    """四角錐の屋根（下絵なので切妻ではなく寄棟の目安）。"""
    o = prim("cone", mat, name, loc=bl((cx, cy), z0 + rh / 2), rot=(0, 0, -math.radians(rot) + math.pi / 4), radius1=1.0, radius2=0.0, depth=rh, vertices=4)
    o.scale = (w / math.sqrt(2), d / math.sqrt(2), 1.0)
    o.rotation_euler = (0, 0, -math.radians(rot))
    o.data.transform(__import__("mathutils").Matrix.Rotation(math.pi / 4, 4, "Z"))
    return o


MATS = {}


def mat_of(key):
    cols = {"wood": (0.62, 0.42, 0.25), "roof": (0.78, 0.33, 0.2), "stonewall": (0.62, 0.6, 0.48), "fence": (0.38, 0.26, 0.16),
            "inwall": (0.5, 0.52, 0.42), "cloth": (0.75, 0.18, 0.15), "curtain": (0.82, 0.74, 0.6), "furn": (0.45, 0.3, 0.18),
            "bed": (0.7, 0.3, 0.2), "hearth": (0.45, 0.42, 0.38), "straw": (0.85, 0.72, 0.38)}
    if key not in MATS:
        MATS[key] = make_mat(f"m_{key}", cols[key])
    return MATS[key]


def point_light(name, x, y, z, energy, color=(1.0, 0.62, 0.3), radius=0.15):
    bpy.ops.object.light_add(type="POINT", location=bl((x, y), z))
    o = bpy.context.object
    o.name = name
    o.data.energy = energy
    o.data.color = color
    o.data.shadow_soft_size = radius
    return o


def build_span(name, pr):
    """from→to に沿った壁・柵・梁（高さ h、厚さ t、下端 z）。"""
    (x0, y0), (x1, y1) = pr["from"], pr["to"]
    L = math.dist((x0, y0), (x1, y1))
    rot = -math.degrees(math.atan2(-(y1 - y0), x1 - x0))   # 東西に長い箱を、from→to の向きに回す（box の rot は上から見て時計回り）
    key = "fence" if name.startswith("fence") else ("wood" if name.startswith("beam") else "stonewall")
    t = pr.get("t", 0.08 if key == "fence" else 0.4)
    if key == "fence":   # 柵は横木2本と杭
        for k, zz in enumerate((0.45, 0.85)):
            box(mat_of(key), f"ENV_{name}_rail{k}", (x0 + x1) / 2, (y0 + y1) / 2, L, t, 0.08, zz * pr.get("h", 1.0), rot)
        n = max(2, int(L / 1.5) + 1)
        for k in range(n):
            u = k / (n - 1)
            box(mat_of(key), f"ENV_{name}_post{k}", x0 + (x1 - x0) * u, y0 + (y1 - y0) * u, 0.12, 0.12, pr.get("h", 1.0))
        return
    box(mat_of(key), f"ENV_{name}", (x0 + x1) / 2, (y0 + y1) / 2, L, t, pr.get("h", 1.5), pr.get("z", 0.0), rot)


def build_room(name, pr):
    """中に入れる部屋: x・y の範囲の4つの壁（openings で扉・窓・出入口を開ける）、床、屋根。walls で作る壁を選べる。"""
    (xa, xb), (ya, yb) = pr["x"], pr["y"]
    H, T = pr.get("h", 3.0), 0.18
    sides = pr.get("walls") or ["n", "s", "e", "w"]
    m = mat_of("inwall")
    for side in sides:
        horiz = side in ("n", "s")
        a0, a1 = (xa, xb) if horiz else (ya, yb)
        fixed = {"n": ya, "s": yb, "w": xa, "e": xb}[side]
        ops = sorted([o for o in pr.get("openings", []) if o["side"] == side], key=lambda o: o["c"])
        cuts_ = [a0]
        pieces = []
        for o in ops:
            lo, hi = o["c"] - o["w"] / 2, o["c"] + o["w"] / 2
            pieces.append((cuts_[-1], lo, 0.0, H))
            sill = o.get("sill", 0.0)
            if sill > 0:
                pieces.append((lo, hi, 0.0, sill))
            top = sill + o["h"]
            if top < H:
                pieces.append((lo, hi, top, H))
            cuts_.append(hi)
        pieces.append((cuts_[-1], a1, 0.0, H))
        for k, (u0, u1, z0, z1) in enumerate(pieces):
            if u1 - u0 < 0.01:
                continue
            c = (u0 + u1) / 2
            if horiz:
                box(m, f"ENV_{name}_{side}{k}", c, fixed, u1 - u0, T, z1 - z0, z0)
            else:
                box(m, f"ENV_{name}_{side}{k}", fixed, c, T, u1 - u0, z1 - z0, z0)
    box(mat_of("hearth"), f"ENV_{name}_floor", (xa + xb) / 2, (ya + yb) / 2, xb - xa, yb - ya, 0.02)
    roof_on(mat_of("roof"), f"ENV_{name}_roof", (xa + xb) / 2, (ya + yb) / 2, xb - xa + 0.6, yb - ya + 0.6, H, pr.get("roof", 1.8))


def build_extra(name, pr, x, y, lit):
    """家・柱・家具・ランプなど（村とティトの家、A05b）。作ったら True。"""
    rot = pr.get("rot", 0)
    lamps = lit.get("lamps", 1.0) if lit else 1.0
    if name.startswith("house"):
        w, d, h = pr.get("w", 6), pr.get("d", 5), pr.get("h", 3)
        box(mat_of("wood"), f"ENV_{name}", x, y, w, d, h, 0.0, rot)
        roof_on(mat_of("roof"), f"ENV_{name}_roof", x, y, w + 0.6, d + 0.6, h, pr.get("roof", 1.8), rot)
        n = pr.get("windows", 0)
        if n:   # 窓明かり: 南と北の壁に、ランプの強さに合わせて光る窓（夕方は弱く、夜は強く）
            win = make_mat(f"{name}_win", (1.0, 0.7, 0.35), emit=1.5 * lamps)
            a = math.radians(rot)
            for k in range(n):
                u = (k + 1) / (n + 1) - 0.5
                for sy in (-1, 1):
                    dx, dy = u * w, sy * (d / 2 + 0.02)
                    wx, wy = x + dx * math.cos(a) - dy * math.sin(a), y + dx * math.sin(a) + dy * math.cos(a)
                    box(win, f"ENV_{name}_win{k}{sy}", wx, wy, 0.6, 0.03, 0.6, 1.2, rot)
        return True
    if name.startswith("horn"):   # 壁に掛けた角笛（真鍮の円錐）
        brass = make_mat(f"{name}_m", (0.85, 0.65, 0.25))
        prim("cone", brass, f"ENV_{name}", loc=bl((x, y), pr.get("z", 1.5)), rot=(0, math.pi / 2, 0), radius1=0.12, radius2=0.03, depth=0.6)
        return True
    if name.startswith("pillar"):
        r = pr.get("r", 0.3)
        box(mat_of("stonewall"), f"ENV_{name}", x, y, 2 * r, 2 * r, pr.get("h", 2.5))
        return True
    if name.startswith("lantern"):
        z = pr.get("z", 2.2)
        lampm = make_mat(f"{name}_m", (1.0, 0.8, 0.45), emit=3.0 * lamps if pr.get("energy", 30) else 0.0)
        if pr.get("energy", 30):
            prim("sphere", lampm, f"ENV_{name}", loc=bl((x, y), z), radius=0.09)
            point_light(f"LGT_{name}", x, y, z, pr.get("energy", 30) * lamps)
        return True
    if name.startswith("hearth"):
        box(mat_of("hearth"), f"ENV_{name}", x, y, pr.get("w", 0.6), pr.get("d", 1.4), pr.get("h", 1.1), 0.0, rot)
        fire = make_mat(f"{name}_fire", (1.0, 0.45, 0.1), emit=4.0)
        box(fire, f"ENV_{name}_fire", x - pr.get("w", 0.6) / 2 - 0.01, y, 0.02, 0.6, 0.35, 0.15)
        point_light(f"LGT_{name}", x - 0.5, y, 0.5, pr.get("energy", 100) * lamps, (1.0, 0.5, 0.2), 0.3)
        return True
    if name.startswith("stool"):
        prim("cyl", mat_of("furn"), f"ENV_{name}", loc=bl((x, y), 0.22), radius=0.18, depth=0.45)
        return True
    if name.startswith("curtain"):
        box(mat_of("curtain"), f"ENV_{name}", x, y, pr.get("w", 1.0), 0.04, pr.get("h", 1.9), 0.0, rot)
        return True
    for key, mk in (("table", "furn"), ("shelf", "furn"), ("bed", "bed"), ("crate", "straw")):
        if name.startswith(key):
            box(mat_of(mk), f"ENV_{name}", x, y, pr.get("w", 1.0), pr.get("d", 0.6), pr.get("h", 0.8), 0.0, rot)
            if key == "table":   # 赤い布
                box(mat_of("cloth"), f"ENV_{name}_cloth", x, y, pr.get("w", 1.0) * 0.6, pr.get("d", 0.6) + 0.06, 0.02, pr.get("h", 0.8))
            return True
    return False


def build_site(lay, lighting=None):
    """場所（world/*-layout.yaml）を、平らな色分けと簡単な立体で作る。lighting は layout の lighting の名前（夕方・夜など）。"""
    MATS.clear()   # カットごとに場面を作り直すので、前のカットの材質は使えない
    g = lay.get("ground") or {}
    gcol = tuple(v / 255 for v in g.get("color", [41, 158, 82]))
    swamp = make_mat("swamp", gcol)
    poly_mesh("ENV_swamp_far", [(-600, -600), (600, -600), (600, 600), (-600, 600)], -0.03, swamp)
    kinds = {"翠の沼": swamp}
    for i, a in enumerate(lay.get("areas", [])):
        col = tuple(min(1.0, v / 255 * 1.35) for v in a.get("color", [128, 128, 128]))   # 下絵で見分けやすいよう少し明るく
        m = kinds.get(a["ja"]) or make_mat(f"area{i}", col)
        z = 0.004 * i
        if a["kind"] == "rect":
            pts = [(a["x"][0], a["y"][0]), (a["x"][1], a["y"][0]), (a["x"][1], a["y"][1]), (a["x"][0], a["y"][1])]
        elif a["kind"] == "poly":
            pts = [tuple(q) for q in a["points"]]
        else:
            n = 48
            cx, cy = a["center"]
            pts = [(cx + a["r"] * math.cos(2 * math.pi * k / n), cy + a["r"] * math.sin(2 * math.pi * k / n)) for k in range(n)]
        poly_mesh(f"ENV_{a['ja']}", pts, z, m)
    h = lay.get("house")
    if h:
        wall = make_mat("wall", (0.55, 0.32, 0.18))
        roof = make_mat("roof", (0.95, 0.55, 0.2))
        cx, cy = h["center"]
        prim("cyl", wall, "ENV_house_wall", loc=bl((cx, cy), h["h"] / 2), radius=h["r"], depth=h["h"])
        prim("cone", roof, "ENV_house_roof", loc=bl((cx, cy), h["h"] + h["roof"] / 2), radius1=h["r"] + 0.5, radius2=0.0, depth=h["roof"])
        lantern = make_mat("lantern", (0.4, 1.0, 0.6), emit=2.0)
        prim("sphere", lantern, "ENV_house_top", loc=bl((cx, cy), h["h"] + h["roof"] + 0.3), radius=0.45)
    if (lay.get("props") or {}).get("door"):
        door = make_mat("door", (0.18, 0.1, 0.06))
        d = lay["props"]["door"]["at"]
        prim("cube", door, "ENV_door", loc=bl((d[0], d[1] - 0.05), 1.1), scale=(0.6, 0.12, 1.1), size=2.0)
    sand = make_mat("dune", tuple(min(1.0, v * 1.12) for v in gcol))
    rockm = make_mat("rock", (0.55, 0.3, 0.2))
    tentm = make_mat("tent", (0.8, 0.45, 0.25))
    bonem = make_mat("bone", (0.92, 0.9, 0.82))
    stone = make_mat("stone", (0.6, 0.58, 0.52))
    jar = make_mat("jar", (0.35, 0.45, 0.75))
    pole = make_mat("pole", (0.2, 0.2, 0.22))
    lamp = make_mat("lamp", (1.0, 0.85, 0.5), emit=1.5)
    trunk = make_mat("trunk", (0.38, 0.28, 0.2))
    red = make_mat("mush", (0.9, 0.2, 0.18))
    stump = make_mat("stump", (0.45, 0.33, 0.22))
    lit = (lay.get("lighting") or {}).get(lighting) or {}
    for name, pr in (lay.get("props") or {}).items():
        if "from" in pr:
            build_span(name, pr)
        elif name.startswith("room"):
            build_room(name, pr)
    for name, pr in (lay.get("props") or {}).items():
        if "at" not in pr or name == "door":
            continue
        x, y = pr["at"]
        if name.startswith("dune"):   # 砂丘: 平たい半球（r は半径、h は高さ）
            prim("sphere", sand, f"ENV_{name}", loc=bl((x, y), 0.0), radius=1.0, scale=(pr.get("r", 10), pr.get("r", 10) * pr.get("ry", 0.6), pr.get("h", 4)))
            continue
        if name.startswith("rock") or name.startswith("mesa"):
            hh = pr.get("h", 1.5)
            prim("cube", rockm, f"ENV_{name}", loc=bl((x, y), hh / 2), scale=(pr.get("r", hh * 0.6), pr.get("r", hh * 0.6) * 0.8, hh / 2))
            continue
        if name.startswith("tent"):
            hh = pr.get("h", 3.5)
            prim("cyl", tentm, f"ENV_{name}", loc=bl((x, y), hh * 0.3), radius=pr.get("r", 2.5), depth=hh * 0.6)
            prim("cone", tentm, f"ENV_{name}_roof", loc=bl((x, y), hh * 0.8), radius1=pr.get("r", 2.5) + 0.2, radius2=0.0, depth=hh * 0.45)
            continue
        if name.startswith("bones"):
            for k, (dx, dy) in enumerate(((0, 0), (0.6, 0.2), (-0.5, 0.3), (0.2, -0.5))):
                prim("cyl", bonem, f"ENV_{name}_{k}", loc=bl((x + dx, y + dy), 0.06), rot=(math.pi / 2, 0, k * 0.8), radius=0.05, depth=pr.get("r", 1.0))
            continue
        if name.startswith("skeleton"):   # 大きな獣の骨: 半分砂に埋まった弓なりのあばら（輪を縦に立てて下半分を砂に埋める）
            for k in range(6):
                bpy.ops.mesh.primitive_torus_add(major_radius=pr.get("h", 2.2) * (0.9 - 0.08 * abs(k - 2.5)), minor_radius=0.08,
                                                 location=bl((x + k * 0.7 - 1.8, y), 0.0), rotation=(math.pi / 2, 0, math.pi / 2))
                o = bpy.context.object
                o.name = f"ENV_{name}_{k}"
                o.data.materials.append(bonem)
            continue
        if name.startswith("well"):
            prim("cyl", stone, f"ENV_{name}", loc=bl((x, y), 0.4), radius=pr.get("r", 0.9), depth=0.8)
            if pr.get("glow"):   # 井戸の口: 暗い穴と、底のかすかな光
                hole = make_mat(f"{name}_hole", (0.02, 0.02, 0.02))
                prim("cyl", hole, f"ENV_{name}_hole", loc=bl((x, y), 0.805), radius=pr.get("r", 0.9) * 0.72, depth=0.01)
                g = make_mat(f"{name}_glow", tuple(v / 255 for v in pr["glow"]), emit=0.35)
                prim("cyl", g, f"ENV_{name}_glow", loc=bl((x, y), 0.81), radius=pr.get("r", 0.9) * 0.16, depth=0.005)
            continue
        if build_extra(name, pr, x, y, lit):
            continue
        if name.startswith("jar") or name.startswith("goods"):
            prim("sphere", jar, f"ENV_{name}", loc=bl((x, y), 0.4), radius=pr.get("r", 0.4))
            continue
        if name.startswith("lamp"):
            prim("cyl", pole, f"ENV_{name}", loc=bl((x, y), pr["h"] / 2), radius=0.05, depth=pr["h"])
            prim("sphere", lamp, f"ENV_{name}_light", loc=bl((x, y), pr["h"] + 0.1), radius=0.14)
        elif name.startswith("tree"):
            prim("cone", trunk, f"ENV_{name}", loc=bl((x, y), pr["h"] / 2), radius1=0.45, radius2=0.12, depth=pr["h"])
        elif name.startswith("mushrooms"):
            for k, (dx, dy) in enumerate(((0, 0), (0.3, 0.1), (-0.25, 0.2), (0.1, -0.3))):
                prim("sphere", red, f"ENV_{name}_{k}", loc=bl((x + dx, y + dy), 0.12), radius=0.16, scale=(1, 1, 0.7))
        elif name.startswith("stumps"):
            for k, dx in enumerate((-0.9, 0.0, 0.8)):
                prim("cyl", stump, f"ENV_{name}_{k}", loc=bl((x + dx, y), 0.3), radius=0.22, depth=0.6)
    world = bpy.data.worlds.new("sky")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    sky = lit.get("sky") or (lay.get("sky") or {}).get("color", [158, 199, 242])
    bg.inputs["Color"].default_value = (*linear(tuple(v / 255 for v in sky)), 1.0)
    bg.inputs["Strength"].default_value = lit.get("sky_strength", 1.4)
    bpy.context.scene.world = world
    # 朝の光: 東の低い所から
    bpy.ops.object.light_add(type="SUN")
    sun = bpy.context.object
    sun.name = "LGT_sun"
    sun.data.energy = lit.get("sun_energy", 4.0)
    if lit.get("sun_color"):
        sun.data.color = tuple(v / 255 for v in lit["sun_color"])
    sn = dict(lay.get("sun") or {})
    if lit:
        sn.update({"from": lit.get("sun_from", sn.get("from", 90)), "elev": lit.get("sun_elev", sn.get("elev", 25))})
    if sn:   # 太陽の方位（北 0・東 90）と高さ（°）
        sun.rotation_euler = (math.radians(90 - sn.get("elev", 25)), 0, math.radians(-sn.get("from", 90) + 180 - 90 + 90))
    else:
        sun.rotation_euler = (math.radians(65), 0, math.radians(-100))   # 東（+X）から西へ、高さ 25°


def build_person(who, h, torch=False):
    sh = SHAPE.get(who, SHAPE_DEFAULT)
    col = COLORS.get(who, (0.9, 0.9, 0.9))
    r, hr, arm = sh["r"], sh["hr"], sh["arm"]
    body_h = max(0.2, h - 2 * hr)
    root = bpy.data.objects.new(f"FIG_{who}", None)
    bpy.context.collection.objects.link(root)
    m_body = make_mat(f"{who}_body", col)
    m_dark = make_mat(f"{who}_dark", tuple(c * 0.45 for c in col))
    m_face = make_mat(f"{who}_nose", (1.0, 0.93, 0.8))
    prim("cyl", m_body, f"{who}_body", root, (0, 0, body_h / 2), radius=r, depth=body_h)
    al = body_h * 0.6
    arms = {}
    for sx in (-1, 1):   # 腕は肩を軸に回せるように、肩の位置の空の親に付ける
        piv = bpy.data.objects.new(f"SHOULDER_{who}{sx}", None)
        bpy.context.collection.objects.link(piv)
        piv.parent = root
        piv.location = (sx * (r + arm), 0, body_h * 0.92)
        prim("cyl", m_dark, f"{who}_arm{sx}", piv, (0, 0, -al / 2), radius=arm, depth=al)
        arms[sx] = piv
    root["arms"] = 1
    head = bpy.data.objects.new(f"HEAD_{who}", None)
    bpy.context.collection.objects.link(head)
    head.parent = root
    head.location = (0, 0, body_h)
    prim("sphere", m_body, f"{who}_head", head, (0, 0, hr), radius=hr, scale=(1, 1, 1.2 if who == "arlecchino" else 1))
    prim("cone", m_face, f"{who}_nose", head, (0, hr * 1.0, hr), (-math.pi / 2, 0, 0), radius1=hr * 0.3, radius2=0.0, depth=hr * 0.7)
    if who == "metallica":
        hat = make_mat("hat", (0.35, 0.2, 0.5))
        prim("cyl", hat, "hat_brim", head, (0, 0, hr * 1.7), radius=0.38, depth=0.025)
        prim("cone", hat, "hat_cone", head, (0, -0.03, hr * 1.7 + 0.3), (math.radians(8), 0, 0), radius1=0.25, radius2=0.0, depth=0.6)
        for sx in (-1, 1):   # 長い三つ編み（背中側を見分けるため）
            prim("cyl", m_dark, f"braid{sx}", head, (sx * 0.1, -hr * 0.9, -0.45), radius=0.04, depth=1.1)
    elif who == "hundred-knight":
        helmet = make_mat("hk_plume", (0.55, 0.85, 1.0), emit=0.6)
        prim("cone", helmet, "plume", head, (0, 0, hr * 2.0 + 0.1), radius1=0.07, radius2=0.0, depth=0.3)
    elif who == "bisco":
        prim("sphere", m_dark, "tail", root, (0, -r - 0.08, body_h * 0.4), radius=0.1, scale=(0.7, 1.4, 0.9))
    elif who == "arlecchino":
        horn = make_mat("horn", (0.72, 0.45, 0.2))
        for sx in (-1, 1):
            prim("cyl", horn, f"horn{sx}", head, (sx * hr * 0.55, 0, hr * 2.4 + 0.05), radius=0.03, depth=0.25)
    elif who in ("luckiny", "tito") or who.startswith("villager"):   # 獣人の角
        horn = make_mat(f"{who}_horn", (0.85, 0.8, 0.7))
        for sx in (-1, 1):
            prim("cone", horn, f"horn{sx}", head, (sx * hr * 0.5, 0, hr * 2.2), (0, sx * 0.25, 0), radius1=0.035, radius2=0.0, depth=0.3)
    if torch:   # 松明: 右手の先に火（点光源）。腕は apply_pose で上げる
        fire = make_mat(f"{who}_torch", (1.0, 0.55, 0.15), emit=4.0)
        stick = make_mat(f"{who}_stick", (0.3, 0.2, 0.12))
        prim("cyl", stick, f"{who}_torch_stick", arms[1], (0, 0, -al - 0.12), radius=0.025, depth=0.4)
        f = prim("sphere", fire, f"{who}_torch_fire", arms[1], (0, 0, -al - 0.36), radius=0.08, scale=(1, 1, 1.5))
        bpy.ops.object.light_add(type="POINT", location=(0, 0, 0))
        lt = bpy.context.object
        lt.name = f"LGT_{who}_torch"
        lt.data.energy, lt.data.color, lt.data.shadow_soft_size = 45.0, (1.0, 0.55, 0.2), 0.1
        lt.parent = f
        root["torch"] = 1
    return root, head, arms


def setup_render(scene, w, h, samples, denoise=False):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    try:
        scene.cycles.use_denoising = denoise
    except Exception:
        pass
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    try:
        scene.render.image_settings.media_type = "IMAGE"
    except Exception:
        pass
    scene.render.image_settings.file_format = "PNG"


# ---------- 実行 ----------

SAND = (0.86, 0.69, 0.40)


def build_creature(name, track):
    """生き物の体（頭から尾までの球）と、砂の下の浅い所に出る影と盛り上がり。"""
    col = COLORS.get(name, (0.6, 0.6, 0.5))
    m_body = make_mat(f"{name}_body", col)
    m_head = make_mat(f"{name}_head", tuple(c * 0.75 for c in col))
    m_shadow = make_mat(f"{name}_shadow", (0.35, 0.25, 0.12))
    m_mound = make_mat(f"{name}_mound", tuple(min(1.0, c * 1.12) for c in SAND))
    segs, shadows, mounds = [], [], []
    for i in range(track.segs):
        segs.append(prim("sphere", m_head if i == 0 else m_body, f"{name}_seg{i}", None, (0, 0, -50), radius=1.0))
        shadows.append(prim("cyl", m_shadow, f"{name}_shadow{i}", None, (0, 0, -50), radius=1.0, depth=0.02))
        mounds.append(prim("sphere", m_mound, f"{name}_mound{i}", None, (0, 0, -50), radius=1.0))
    return segs, shadows, mounds


def pose_creature(parts, track, t):
    segs, shadows, mounds = parts
    for (p, r), so, sh, mo in zip(track.body(t), segs, shadows, mounds):
        x, y, z = p
        so.location = bl((x, y), z)
        so.scale = (r, r, r)
        if -2.5 < z < 0.0:   # 砂の下の浅い所: 影（深いほど薄く小さく）と盛り上がり
            k = 1.0 + z / 2.5
            sh.location = bl((x, y), 0.03)
            sh.scale = (r * (1.3 + 0.4 * k), r * (1.3 + 0.4 * k), 1.0)
            mo.location = bl((x, y), -r * 0.8)
            mo.scale = (r * 1.4, r * 1.4, r * (0.3 + 0.8 * k))
        else:
            sh.location = mo.location = (0, 0, -50)


def build_fx(fx, i):
    kind = fx.get("kind", "plume")
    m = make_mat(f"fx{i}", tuple(min(1.0, c * 1.08) for c in SAND))
    if kind == "ring":   # 波紋は平たく細い、少し暗い輪
        m = make_mat(f"fx{i}_ring", tuple(c * 0.72 for c in SAND))
        objs = []
        for j in range(int(fx.get("n", 3))):
            bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.025, major_segments=48, minor_segments=6)
            o = bpy.context.object
            o.data.materials.append(m)
            o.location = (0, 0, -50)
            objs.append(o)
        return objs
    return [prim("sphere", m, f"fx{i}_{j}", None, (0, 0, -50), radius=1.0) for j in range(6)]


def pose_fx(fx, objs, t, pos, place):
    at = fx["at"]
    p = pos[at] if isinstance(at, str) and at in pos else place(at)
    amt = motion.fx_amount(fx, t)
    t0, t1 = fx["t"]
    if fx.get("kind") == "ring":   # 波紋: 輪が順に広がる
        n = len(objs)
        for j, o in enumerate(objs):
            u = ((t - t0) / max(1e-6, t1 - t0) * n - j) % n / n if t0 <= t <= t1 else -1
            if 0 <= u <= 1:
                rr = fx.get("r", 3.0) * (0.1 + u)
                o.location = bl(p, 0.0)
                o.scale = (rr, rr, 1.0)
            else:
                o.location = (0, 0, -50)
        return
    r, h = fx.get("r", 2.0), fx.get("h", 4.0)   # 噴き上げ: 球の柱が伸びて広がる
    for j, o in enumerate(objs):
        if amt <= 0.01:
            o.location = (0, 0, -50)
            continue
        f = j / max(1, len(objs) - 1)
        ang = j * 2.4
        rr = r * (0.35 + 0.65 * f) * amt
        o.location = bl((p[0] + math.cos(ang) * r * 0.3 * f, p[1] + math.sin(ang) * r * 0.3 * f), h * f * amt)
        o.scale = (rr, rr, rr * 0.8)


def apply_pose(root, head, arms, who, pose, b, x, y, z, t, lay):
    """姿勢を人形に当てる（走る上下動・前傾、しゃがむ、伏せる、手を振る、指さす、両手を上げる、体を振る）。"""
    hh = lay["people"][who]["h"]
    rx, rz, k = 0.0, -math.radians(b), 1.0
    for sx, piv in arms.items():
        piv.rotation_euler = (0, 0, 0)
    if pose == "lie":   # うつ伏せ: 体を前へ倒し、頭が向いている側に（体の中ほどを位置に）
        r = SHAPE.get(who, SHAPE_DEFAULT)["r"]
        root.location = bl((x - math.sin(math.radians(b)) * hh / 2, y + math.cos(math.radians(b)) * hh / 2), r + z)
        root.rotation_euler = (-math.pi / 2, 0, rz)
        root.scale = (1, 1, 1)
        return
    if pose in ("run", "walk"):
        f, amp, lean, swing = (2.6, 0.06, 14, 45) if pose == "run" else (1.8, 0.025, 4, 25)
        z += abs(math.sin(2 * math.pi * f * t)) * amp * hh
        rx = -math.radians(lean)
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(swing) * math.sin(2 * math.pi * f * t / 2 + (0 if sx > 0 else math.pi)), 0, 0)
    elif pose in ("crouch", "crouching"):
        k, rx = 0.65, -math.radians(20)
    elif pose in ("kneel", "kneeling"):
        k = 0.7
    elif pose in ("sit", "sitting"):
        k = 0.6
    elif pose == "bow":        # お辞儀: 上体を前へ倒す
        rx = -math.radians(35)
    elif pose == "look_up":    # 見上げる: 少し反る
        rx = math.radians(10)
    elif pose == "kneel_hold":  # ひざをついて、両手で相手の手を包む
        k = 0.7
    elif pose == "look_down":   # うつむく
        rx = -math.radians(12)
    elif pose == "proud":       # 胸を張る（両手を腰に）
        rx = math.radians(6)
    elif pose == "stroke":      # かがんで相手の頭をなでる
        k, rx = 0.8, -math.radians(15)
    elif pose == "jump":
        for sx, piv in arms.items():
            piv.rotation_euler = (0, sx * -math.radians(120), 0)
    elif pose == "shake":   # 犬のように体を振る
        rz += math.radians(18) * math.sin(2 * math.pi * 6 * t)
    if root.get("torch") and pose not in ("wave", "point", "arms_up", "lie"):   # 松明を掲げる
        arms[1].rotation_euler = (math.radians(20), -math.radians(130), 0)
    if pose in ("hold", "kneel_hold"):
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(70), 0, 0)
    elif pose == "proud":
        for sx, piv in arms.items():
            piv.rotation_euler = (0, sx * math.radians(35), 0)
    elif pose == "stroke":
        arms[1].rotation_euler = (math.radians(95 + 10 * math.sin(2 * math.pi * 1.2 * t)), 0, 0)
    elif pose == "horn":        # 角笛を口に当てて吹く（両腕を前に上げる）
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(115), 0, sx * -math.radians(15))
    elif pose == "gesture":    # 片手を横に広げて部屋を示す
        arms[1].rotation_euler = (math.radians(30), -math.radians(70 + 20 * math.sin(2 * math.pi * 0.6 * t)), 0)
    if pose == "wave":
        arms[1].rotation_euler = (0, -math.radians(150 + 20 * math.sin(2 * math.pi * 2.0 * t)), 0)
    elif pose == "point":
        arms[1].rotation_euler = (math.radians(90), 0, 0)
    elif pose == "arms_up":
        for sx, piv in arms.items():
            piv.rotation_euler = (0, sx * -math.radians(160), 0)
    root.location = bl((x, y), z)
    root.rotation_euler = (rx, 0, rz)
    root.scale = (1, 1, k)


def render_cut(st, lay, cut, sec, fps, size, samples, out_dir, work_root, denoise=False, preview=False, motion_step=None):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    c_ = st.bk["cuts"][cut]
    build_site(lay, c_.get("lighting") or st.bk.get("lighting"))
    plan = Plan(st, cut, sec)
    figs = {}
    for who in plan.people:
        figs[who] = build_person(who, lay["people"][who]["h"], lay["people"][who].get("torch", False))
        if who not in plan.shown:   # 画に映さない人物は描かない（静止画と同じ）
            for o in [figs[who][0]] + list(figs[who][0].children_recursive):
                o.hide_render = True
    creatures = {n: build_creature(n, tr) for n, tr in plan.creatures.items()}
    fxs = [(fx, build_fx(fx, i)) for i, fx in enumerate(plan.c.get("fx") or [])]
    bpy.ops.object.camera_add()
    cam = bpy.context.object
    cam.name = "CAM"
    cam.data.sensor_width = 36.0
    cam.data.sensor_fit = "HORIZONTAL"
    cam.data.clip_start, cam.data.clip_end = 0.05, 1000
    scene.camera = cam
    w, h = size
    setup_render(scene, w, h, samples, denoise)
    n = max(2, round(sec * fps))
    frames_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"previs_{cut}_", dir=work_root))
    checks = {}
    warnings = []
    cam_moves = bool(plan.c.get("cam_end"))   # staging.py はカメラ本体の移動を知らないので、終わりの比較はしない

    def pose(i):
        t = i / fps
        pos = plan.positions(t)
        for who, (root, head, arms) in figs.items():
            b = plan.face(who, t, pos)
            x, y = pos[who]
            apply_pose(root, head, arms, who, plan.pose_name(who, t), b, x, y, motion.ground_z(lay, x, y) + plan.height(who, t), t, lay)
            head.rotation_euler = (0, 0, -math.radians(plan.head_offset(who, t, pos, b)))
        for name, parts in creatures.items():
            pose_creature(parts, plan.creatures[name], t)
        for fx, objs in fxs:
            pose_fx(fx, objs, t, pos, st.place)
        (cx, cy, cz), yaw, pitch, lens = plan.camera(t, pos)
        cam.location = bl((cx, cy), cz)
        cam.rotation_euler = (math.pi / 2 + pitch, 0, -math.radians(yaw))
        cam.data.lens = lens
        bpy.context.view_layer.update()
        return pos

    if motion_step:   # 動きの確認: motion_step 秒ごとのコマだけ描く（動画は作らない）
        idx = sorted({min(n - 1, round(k * motion_step * fps)) for k in range(int(sec / motion_step) + 1)} | {n - 1})
    elif preview:     # 始め・中ほど・終わりの3枚だけ
        idx = [0, n // 2, n - 1]
    else:
        idx = range(n)
    for i in idx:
        pos = pose(i)
        out_of_frame = []
        for who in plan.shown:
            root = figs[who][0]
            p = Vector(root.matrix_world.translation) + Vector((0, 0, lay["people"][who]["h"] * 0.5))
            v = world_to_camera_view(scene, cam, p)
            if v.z > 0 and not (-0.02 <= v.x <= 1.02):
                out_of_frame.append(who)
        if out_of_frame:
            warnings.append((i, out_of_frame))
        if i in (0, n - 1) and not (cam_moves and i != 0):
            which = "start" if i == 0 else "end"
            fr = st.frame(cut, which)
            res = []
            for e in fr["people"]:
                root = figs[e["who"]][0]
                p = Vector(root.matrix_world.translation) + Vector((0, 0, lay["people"][e["who"]]["h"] * 0.5))
                v = world_to_camera_view(scene, cam, p)
                res.append({"who": e["who"], "staging_s": e["s"], "blender_s": round((v.x - 0.5) * 2, 2)})
            checks[which] = res
        scene.render.filepath = str(frames_dir / f"{i:04d}.png")
        bpy.ops.render.render(write_still=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    checks["warnings"] = warnings
    checks["frames"] = list(idx)
    if preview or motion_step:
        return None, frames_dir, n, checks
    mp4 = out_dir / f"{cut}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", str(frames_dir / "%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(mp4)], check=True)
    return mp4, frames_dir, n, checks


def motion_sheet(cut, shot, frames_dir, frames, fps, out_dir, cols=4):
    """動きの確認用: 決まった秒ごとのコマを左上から並べ、秒を書いた画像（D-060）。"""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 400, 225
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (W * cols, H * rows + 36), (25, 25, 25))
    d = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(staging.FONT, 18) if staging.FONT else None
    small = ImageFont.truetype(staging.FONT, 16) if staging.FONT else None
    d.text((10, 8), f"{cut}  {shot.get('sec')}秒  {shot.get('shot', '')}", fill=(230, 230, 230), font=font)
    for k, i in enumerate(frames):
        fr = Image.open(frames_dir / f"{i:04d}.png").convert("RGB").resize((W, H))
        x, y = (k % cols) * W, 36 + (k // cols) * H
        sheet.paste(fr, (x, y))
        d.rectangle([x, y, x + 64, y + 22], fill=(0, 0, 0))
        d.text((x + 6, y + 2), f"{i / fps:.1f}s", fill=(255, 255, 255), font=small)
    p = out_dir / f"{cut}_motion.jpg"
    sheet.save(p, quality=85)
    return p


def check_sheet(cut, shot, frames_dir, n, out_dir):
    """採用した静止画（上）と、下絵の始め・中ほど・終わり（下）を並べた確認用の画像。"""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 480, 270
    sheet = Image.new("RGB", (W * 3, H * 2), (30, 30, 30))
    img = shot.get("image")
    if img and (ROOT / img).exists():
        still = Image.open(ROOT / img).convert("RGB").resize((W, H))
        sheet.paste(still, (0, 0))
    d = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(staging.FONT, 18) if staging.FONT else None
    d.text((W + 12, 12), f"{cut}  上: 採用した静止画 / 下: 下絵（始め・中ほど・終わり）", fill=(220, 220, 220), font=font)
    for k, i in enumerate((0, n // 2, n - 1)):
        fr = Image.open(frames_dir / f"{i:04d}.png").convert("RGB").resize((W, H))
        sheet.paste(fr, (W * k, H))
    p = out_dir / f"{cut}_check.jpg"
    sheet.save(p, quality=88)
    return p


def rel(p):
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("cuts", nargs="*", help="カットID（省略で全カット）")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--size", default="854x480")   # Seedance は高さ 480 未満の参照動画で失敗する（2026-10-30）
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--out", help="出力先（既定 previs/<シーン>）")
    ap.add_argument("--denoise", action="store_true", help="ノイズ除去を入れる（1フレーム約1秒増える。既定は切る）")
    ap.add_argument("--preview", action="store_true", help="始め・中ほど・終わりの3枚だけ描いて確認画像を作る（動画は作らない）")
    ap.add_argument("--motion", type=float, nargs="?", const=0.5, help="動きの確認（D-060）: この秒ごとのコマだけ描いて <カット>_motion.jpg を作る（既定 0.5秒、動画は作らない）")
    a = ap.parse_args()
    st = staging.Stage(a.scene_dir)
    lay = st.lay
    scene_id = pathlib.Path(a.scene_dir).name
    out_dir = ROOT / (a.out or f"previs/{scene_id}")
    size = tuple(int(v) for v in a.size.split("x"))
    work = pathlib.Path(tempfile.mkdtemp(prefix="previs_"))
    cuts = a.cuts or st.order
    try:
        for cut in cuts:
            shot = yaml.safe_load((ROOT / a.scene_dir / "shots" / f"{cut}.yaml").read_text(encoding="utf-8"))
            mp4, fdir, n, checks = render_cut(st, lay, cut, float(shot["sec"]), a.fps, size, a.samples, out_dir, work,
                                              denoise=a.denoise, preview=a.preview, motion_step=a.motion)
            frames = checks.pop("frames")
            sheet = motion_sheet(cut, shot, fdir, frames, a.fps, out_dir) if a.motion else check_sheet(cut, shot, fdir, n, out_dir)
            print(f"{cut}: {rel(mp4) if mp4 else '（動画なし）'}（{n}フレーム、{shot['sec']}秒）、{rel(sheet)}")
            warn = checks.pop("warnings", [])
            if warn:
                who = sorted({w for _, ws in warn for w in ws})
                print(f"  警告: 画面の端から出る人物 {', '.join(who)}（フレーム {warn[0][0]}〜{warn[-1][0]}）。出入りを意図したカットでなければ、カメラか動きを直す")
            for which, res in checks.items():
                diff = max((abs(r["staging_s"] - r["blender_s"]) for r in res), default=0)
                print(f"  {which}: 画面の左右（staging.py と Blender の差の最大 {diff:.2f}）",
                      " ".join(f"{r['who']}:{r['staging_s']}/{r['blender_s']}" for r in res))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
