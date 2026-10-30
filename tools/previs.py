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
        if self.start_pos[who] == self.end_pos[who] and not (self.c.get("move") or {}).get(who, {}).get("via"):
            return tuple(self.start_pos[who])
        return catmull(self._path(who), self.ease(who, t))

    def positions(self, t):
        return {w: self.position(w, t) for w in self.people}

    def face(self, who, t, pos):
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
        pitch = staging.tilt_of(c, tuple(cam[:2]), look)
        yaw = staging.bearing_of((look[0] - cam[0], look[1] - cam[1]))
        return cam, yaw, pitch, lens


# ---------- Blender の場面 ----------

COLORS = {"metallica": (1.0, 0.59, 0.16), "bisco": (0.35, 0.59, 1.0), "hundred-knight": (0.63, 0.86, 1.0), "arlecchino": (0.86, 0.47, 0.86)}
# 人形の形。r: 体の半径、hr: 頭の半径、arm: 腕の太さ。背の高さは layout の people.<人物>.h
SHAPE = {
    "metallica": {"r": 0.16, "hr": 0.13, "arm": 0.045},
    "bisco": {"r": 0.15, "hr": 0.125, "arm": 0.045},
    "hundred-knight": {"r": 0.17, "hr": 0.20, "arm": 0.07},
    "arlecchino": {"r": 0.27, "hr": 0.21, "arm": 0.09},
}


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
        bpy.ops.mesh.primitive_cone_add(vertices=28, **kw)
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


def build_site(lay):
    """屋敷の前の広場（world/witch-house-front-layout.yaml）を、平らな色分けと簡単な立体で作る。"""
    swamp = make_mat("swamp", (0.16, 0.62, 0.32))
    poly_mesh("ENV_swamp_far", [(-300, -300), (300, -300), (300, 300), (-300, 300)], -0.03, swamp)
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
    h = lay["house"]
    wall = make_mat("wall", (0.55, 0.32, 0.18))
    roof = make_mat("roof", (0.95, 0.55, 0.2))
    cx, cy = h["center"]
    prim("cyl", wall, "ENV_house_wall", loc=bl((cx, cy), h["h"] / 2), radius=h["r"], depth=h["h"])
    prim("cone", roof, "ENV_house_roof", loc=bl((cx, cy), h["h"] + h["roof"] / 2), radius1=h["r"] + 0.5, radius2=0.0, depth=h["roof"])
    lantern = make_mat("lantern", (0.4, 1.0, 0.6), emit=2.0)
    prim("sphere", lantern, "ENV_house_top", loc=bl((cx, cy), h["h"] + h["roof"] + 0.3), radius=0.45)
    door = make_mat("door", (0.18, 0.1, 0.06))
    d = lay["props"]["door"]["at"]
    prim("cube", door, "ENV_door", loc=bl((d[0], d[1] - 0.05), 1.1), scale=(0.6, 0.12, 1.1), size=2.0)
    pole = make_mat("pole", (0.2, 0.2, 0.22))
    lamp = make_mat("lamp", (1.0, 0.85, 0.5), emit=1.5)
    trunk = make_mat("trunk", (0.38, 0.28, 0.2))
    red = make_mat("mush", (0.9, 0.2, 0.18))
    stump = make_mat("stump", (0.45, 0.33, 0.22))
    for name, pr in lay["props"].items():
        if "at" not in pr or name == "door":
            continue
        x, y = pr["at"]
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
    bg.inputs["Color"].default_value = (*linear((0.62, 0.78, 0.95)), 1.0)
    bg.inputs["Strength"].default_value = 1.4
    bpy.context.scene.world = world
    # 朝の光: 東の低い所から
    bpy.ops.object.light_add(type="SUN")
    sun = bpy.context.object
    sun.name = "LGT_sun"
    sun.data.energy = 4.0
    sun.rotation_euler = (math.radians(65), 0, math.radians(-100))   # 東（+X）から西へ、高さ 25°


def build_person(who, h):
    sh = SHAPE[who]
    col = COLORS[who]
    r, hr, arm = sh["r"], sh["hr"], sh["arm"]
    body_h = max(0.2, h - 2 * hr)
    root = bpy.data.objects.new(f"FIG_{who}", None)
    bpy.context.collection.objects.link(root)
    m_body = make_mat(f"{who}_body", col)
    m_dark = make_mat(f"{who}_dark", tuple(c * 0.45 for c in col))
    m_face = make_mat(f"{who}_nose", (1.0, 0.93, 0.8))
    prim("cyl", m_body, f"{who}_body", root, (0, 0, body_h / 2), radius=r, depth=body_h)
    al = body_h * 0.6
    for sx in (-1, 1):
        prim("cyl", m_dark, f"{who}_arm{sx}", root, (sx * (r + arm), 0, body_h * 0.92 - al / 2), radius=arm, depth=al)
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
    return root, head


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

def render_cut(st, lay, cut, sec, fps, size, samples, out_dir, work_root, denoise=False, preview=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    build_site(lay)
    plan = Plan(st, cut, sec)
    figs = {}
    for who in plan.people:
        figs[who] = build_person(who, lay["people"][who]["h"])
        if who not in plan.shown:   # 画に映さない人物は描かない（静止画と同じ）
            for o in [figs[who][0]] + list(figs[who][0].children_recursive):
                o.hide_render = True
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
        for who, (root, head) in figs.items():
            b = plan.face(who, t, pos)
            x, y = pos[who]
            root.location = bl((x, y))
            root.rotation_euler = (0, 0, -math.radians(b))
            head.rotation_euler = (0, 0, -math.radians(plan.head_offset(who, t, pos, b)))
        (cx, cy, cz), yaw, pitch, lens = plan.camera(t, pos)
        cam.location = bl((cx, cy), cz)
        cam.rotation_euler = (math.pi / 2 + pitch, 0, -math.radians(yaw))
        cam.data.lens = lens
        bpy.context.view_layer.update()
        return pos

    for i in ([0, n // 2, n - 1] if preview else range(n)):   # preview: 始め・中ほど・終わりの3枚だけ
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
                root, head = figs[e["who"]]
                p = Vector(root.matrix_world.translation) + Vector((0, 0, lay["people"][e["who"]]["h"] * 0.5))
                v = world_to_camera_view(scene, cam, p)
                res.append({"who": e["who"], "staging_s": e["s"], "blender_s": round((v.x - 0.5) * 2, 2)})
            checks[which] = res
        scene.render.filepath = str(frames_dir / f"{i:04d}.png")
        bpy.ops.render.render(write_still=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{cut}.mp4"
    if preview:
        checks["warnings"] = warnings
        return None, frames_dir, n, checks
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", str(frames_dir / "%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(mp4)], check=True)
    checks["warnings"] = warnings
    return mp4, frames_dir, n, checks


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
                                              denoise=a.denoise, preview=a.preview)
            sheet = check_sheet(cut, shot, fdir, n, out_dir)
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
