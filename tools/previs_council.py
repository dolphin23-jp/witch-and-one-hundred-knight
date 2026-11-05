"""議事塔の議会室（6章後半 回3、A28〜A32）の下絵の形・人形・効果。tools/previs.py から呼ぶ。

回2（A23〜A27）の下絵の直しと並行して作ったので、previs.py には呼び出しだけを足し、中身はこのファイルに分けた（2026-11-05）。
どの関数も、最初の引数 P に previs.py のモジュールを受け取り、その道具（prim・box・make_mat・beam_mat・bl・point_light）を使う。

形（world/council-room-layout.yaml の props の名前の頭。部品は1つの物にまとめるので、hide_props・reveal・only_lit がそのまま効く）:
  cwall    円い壁。r 半径、h 高さ、windows 窓の数、door {at: 方位, w, h}、gap [[方位, 方位], …] 破れて無い範囲（北 0・東 90、時計回り）
  cceil    円すいの天井。r 半径、z 縁の高さ、rise 頂上までの高さ、r_in 真ん中の抜け（壊れた後）、gap 抜けた範囲
  ctable   円卓（赤い布、金色の魔法陣）。r、h
  cchair   ゴシックの背の高い椅子。face 座る人の向き、tall 背の高さ、fallen: true で倒れた椅子
  correry  天井から下がる天球儀（透けた球、金の輪、光る惑星、真鍮の軸）。z 球の中心の高さ、r、energy 光
  cdoor    両開きの扉。bearing 扉のある壁の方位、w、h、open 開いた角度
  crubble  瓦礫。r 散らばる半径、n 数
  cswamp   床を覆う毒沼（碧、D-063）。r
  cplanet  床に落ちた天球儀の惑星（光る球）。r、color
人形: rubens（リューベンス）、nezaria（ネザリアと話す杖、帽子の上のネズミ）、metallica-giant（巨大メタリカ）、teressa-swamp（毒沼を浴びたテレッサ）。
効果（fx の kind）:
  fog    霧。at、r（広がり）、h（厚さ）、alpha、t: [始め, 終わり]、rise（濃くなる秒）、fade（薄れる秒）
  bolt   雷撃。at（手の位置。人物名か場所）、z（手の高さ）、to（落ちる先の場所か人物の並び）、t、rate（明滅の速さ）
  flash  画面いっぱいの光（白）や暗転（color: [0,0,0]）。カメラに付けた小さな球の内側で、手前の物も覆う。t、peak（いちばん濃い秒）、alpha（at は使わない）
  surge  流れ込む沼の波。at（出どころ）、to（行き先）、t、r（太さ）、h（高さ）、alpha（書くと半透明の霞）
  mist   半透明の噴き上げ（沼の瘴気）。plume と同じ書き方（r、h、z、color）で、中の人物が透けて見える。alpha（濃さ、既定 0.45）
"""
import math

import bpy

SHAPE = {
    "rubens": {"r": 0.15, "hr": 0.125, "arm": 0.045},
    "nezaria": {"r": 0.17, "hr": 0.125, "arm": 0.045},
    "metallica-giant": {"r": 1.30, "hr": 1.05, "arm": 0.36},   # メタリカの人形（r 0.16・hr 0.13）を約 8.1 倍（背 13m）
    "teressa-swamp": {"r": 0.14, "hr": 0.12, "arm": 0.04},
}
GIANT_K = 8.1
PREFIXES = ("cwall", "cceil", "ctable", "cchair", "correry", "cdoor", "crubble", "cswamp", "cplanet")


# ---------- 小さな道具 ----------

def rnd(k, salt=0):
    """決まった並びの 0〜1（毎回同じ形にする）。"""
    return ((k * 7919 + salt * 104729) % 1000) / 1000.0


def join(objs, name):
    objs = [o for o in objs if o is not None]
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    if len(objs) > 1:
        bpy.ops.object.join()
    o = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)   # 原点を世界の原点に（後で動かす・回すため）
    o.name = name
    return o


def in_ranges(b, ranges):
    b %= 360
    for a0, a1 in ranges or []:
        a0, a1 = a0 % 360, a1 % 360
        if (a0 <= a1 and a0 <= b <= a1) or (a0 > a1 and (b >= a0 or b <= a1)):
            return True
    return False


def dist_to_ranges(b, ranges):
    """方位 b から、範囲の端までのいちばん近い角度（範囲の外のとき）。"""
    best = 999.0
    for a0, a1 in ranges or []:
        for e in (a0, a1):
            best = min(best, abs((b - e + 180) % 360 - 180))
    return best


def radial(x, y, b, r):
    a = math.radians(b)
    return x + r * math.sin(a), y - r * math.cos(a)


def mesh_obj(name, verts, faces, mat):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(mat)
    return o


def seg_between(P, mat, name, a, b, r):
    """点 a から b（Blender の座標）への細い円柱。"""
    from mathutils import Vector
    va, vb = Vector(a), Vector(b)
    d = vb - va
    L = max(1e-4, d.length)
    o = P.prim("cyl", mat, name, None, tuple((va + vb) / 2), radius=r, depth=1.0, vertices=6)
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d.normalized())
    o.scale = (1.0, 1.0, L)
    return o


# ---------- 形 ----------

def build_extra(P, name, pr, x, y, lit):
    if not name.startswith(PREFIXES):
        return False
    lit = lit or {}
    lamps = lit.get("lamps", 1.0)
    if name.startswith("cwall"):
        build_wall(P, name, pr, x, y, lit)
    elif name.startswith("cceil"):
        build_ceiling(P, name, pr, x, y)
    elif name.startswith("ctable"):
        build_table(P, name, pr, x, y)
    elif name.startswith("cchair"):
        build_chair(P, name, pr, x, y)
    elif name.startswith("correry"):
        build_orrery(P, name, pr, x, y, lamps)
    elif name.startswith("cdoor"):
        build_door(P, name, pr, x, y)
    elif name.startswith("crubble"):
        build_rubble(P, name, pr, x, y)
    elif name.startswith("cswamp"):
        m = P.make_mat(f"{name}_m", tuple(v / 255 for v in pr.get("color", [32, 150, 118])), rough=0.25, emit=pr.get("glow", 0.35))
        parts = [P.prim("cyl", m, f"ENV_{name}", loc=P.bl((x, y), 0.05), radius=pr.get("r", 12.0), depth=0.06, vertices=64)]
        bub = P.make_mat(f"{name}_bub", (0.35, 0.85, 0.65), rough=0.2, emit=0.6)
        for k in range(int(pr.get("bubbles", 14))):   # 泡（沼の泡、E42）
            bx, by = radial(x, y, rnd(k, 1) * 360, pr.get("r", 12.0) * (0.15 + 0.8 * rnd(k, 2)))
            parts.append(P.prim("sphere", bub, f"ENV_{name}_b{k}", loc=P.bl((bx, by), 0.08), radius=0.08 + 0.12 * rnd(k, 3), scale=(1, 1, 0.5)))
        join(parts, f"ENV_{name}")
    elif name.startswith("cplanet"):
        col = tuple(v / 255 for v in pr.get("color", [255, 200, 70]))
        r = pr.get("r", 0.5)
        parts = [P.prim("sphere", P.make_mat(f"{name}_m", col, emit=2.2 * lamps), f"ENV_{name}", loc=P.bl((x, y), r * 0.9), radius=r)]
        gold = P.make_mat(f"{name}_gold", (0.85, 0.62, 0.25), rough=0.35)
        bpy.ops.mesh.primitive_torus_add(major_radius=r * 1.5, minor_radius=0.04, location=P.bl((x, y), r * 0.9), rotation=(math.radians(70), 0, 0.6))
        o = bpy.context.object
        o.data.materials.append(gold)
        parts.append(o)
        join(parts, f"ENV_{name}")
        P.point_light(f"LGT_{name}", x, y, r * 2.0, 40 * lamps, col, 0.3)
    return True


def build_wall(P, name, pr, x, y, lit):
    r, h = pr.get("r", 12.5), pr.get("h", 9.0)
    gaps = pr.get("gap") or []
    door = pr.get("door")
    wood = P.make_mat(f"{name}_wood", (0.30, 0.18, 0.12))
    dark = P.make_mat(f"{name}_dark", (0.16, 0.10, 0.08))
    curtain = P.make_mat(f"{name}_curtain", (0.48, 0.12, 0.24))
    win = P.make_mat(f"{name}_win", tuple(v / 255 for v in lit.get("window", [40, 52, 120])), emit=0.9)
    star = P.make_mat(f"{name}_star", (1.0, 0.95, 0.8), emit=3.0)
    parts = []
    n = 72
    seg_w = 2 * math.pi * (r + 0.2) / n * 1.06
    for k in range(n):
        b = (k + 0.5) * 360 / n
        cx, cy = radial(x, y, b, r + 0.2)
        if in_ranges(b, gaps):   # 破れた所: 低い壁の根元だけが、ところどころ残る
            if rnd(k, 5) < 0.45:
                parts.append(P.box(wood, f"ENV_{name}_stub{k}", cx, cy, seg_w, 0.4, 0.3 + 1.6 * rnd(k, 6), 0.0, b))
            continue
        hh = h
        d = dist_to_ranges(b, gaps)
        if gaps and d < 14:   # 破れた所の縁はぎざぎざに低い
            hh = h * (0.35 + 0.6 * rnd(k, 7)) if d < 8 else h * (0.75 + 0.25 * rnd(k, 8))
        if door and abs((b - door["at"] + 180) % 360 - 180) < math.degrees(door.get("w", 3.0) / 2 / r):
            parts.append(P.box(wood, f"ENV_{name}_lintel{k}", cx, cy, seg_w, 0.4, hh - door.get("h", 4.4), door.get("h", 4.4), b))
            continue
        parts.append(P.box(wood, f"ENV_{name}_s{k}", cx, cy, seg_w, 0.4, hh, 0.0, b))
        parts.append(P.box(dark, f"ENV_{name}_pan{k}", *radial(x, y, b, r - 0.02), seg_w, 0.04, 1.2, 0.0, b))   # 腰の羽目板
    nw = int(pr.get("windows", 14))
    for j in range(nw):
        b = (j + 0.5) * 360 / nw
        if in_ranges(b, gaps) or dist_to_ranges(b, gaps) < 8:
            continue
        if door and abs((b - door["at"] + 180) % 360 - 180) < 14:
            continue
        wx, wy = radial(x, y, b, r - 0.03)
        parts.append(P.box(win, f"ENV_{name}_win{j}", wx, wy, 1.5, 0.04, 5.0, 1.8, b))
        for s in range(5):   # 窓の星
            sx = (rnd(j * 5 + s, 9) - 0.5) * 1.2
            ux, uy = math.cos(math.radians(b)), math.sin(math.radians(b))
            parts.append(P.box(star, f"ENV_{name}_st{j}{s}", wx + ux * sx - math.sin(math.radians(b)) * 0.03, wy + uy * sx + math.cos(math.radians(b)) * 0.03,
                               0.05, 0.02, 0.05, 2.2 + 4.0 * rnd(j * 5 + s, 10), b))
        for sg in (-1, 1):   # カーテン（窓の両脇）
            off = sg * 1.15
            ux, uy = math.cos(math.radians(b)), math.sin(math.radians(b))
            cx, cy = radial(x, y, b, r - 0.18)
            parts.append(P.box(curtain, f"ENV_{name}_cu{j}{sg}", cx + ux * off, cy + uy * off, 0.75, 0.16, 6.8, 0.3, b))
        cb = b + 180 / nw   # 窓と窓の間の柱
        if not in_ranges(cb, gaps) and dist_to_ranges(cb, gaps) > 6 and not (door and abs((cb - door["at"] + 180) % 360 - 180) < 9):
            px, py = radial(x, y, cb, r - 0.3)
            parts.append(P.prim("cyl", dark, f"ENV_{name}_col{j}", loc=P.bl((px, py), h / 2), radius=0.3, depth=h, vertices=12))
    join(parts, f"ENV_{name}")


def build_ceiling(P, name, pr, x, y):
    r, z, rise = pr.get("r", 12.8), pr.get("z", 9.0), pr.get("rise", 5.0)
    r_in = pr.get("r_in", 0.0)
    gaps = pr.get("gap") or []
    m = P.make_mat(f"{name}_m", (0.17, 0.10, 0.07))
    rib = P.make_mat(f"{name}_rib", (0.42, 0.28, 0.14), rough=0.4)
    n = 48
    verts, faces = [], []

    def zz(rr):
        return z + rise * (1.0 - rr / r)
    for k in range(n):
        b0, b1 = k * 360 / n, (k + 1) * 360 / n
        if in_ranges((b0 + b1) / 2, gaps):
            continue
        ri = r_in
        if r_in > 0:   # 抜けた縁はぎざぎざ
            ri = r_in + 2.5 * rnd(k, 11) + (3.0 if dist_to_ranges((b0 + b1) / 2, gaps) < 10 else 0.0)
        pts = []
        for b, rr in ((b0, r), (b1, r), (b1, ri), (b0, ri)):
            px, py = radial(x, y, b, rr)
            pts.append((px, -py, zz(rr)))
        i0 = len(verts)
        verts.extend(pts)
        faces.append([i0, i0 + 1, i0 + 2, i0 + 3] if ri > 0 else [i0, i0 + 1, i0 + 2])
    parts = [mesh_obj(f"ENV_{name}", verts, faces, m)]
    for k in range(12):   # 放射状の梁
        b = k * 30 + 15
        if in_ranges(b, gaps):
            continue
        ri = max(r_in + 1.5, 0.6) if r_in > 0 else 0.6
        a = radial(x, y, b, r)
        c = radial(x, y, b, ri)
        parts.append(seg_between(P, rib, f"ENV_{name}_rib{k}", (a[0], -a[1], zz(r) - 0.15), (c[0], -c[1], zz(ri) - 0.15), 0.12))
    join(parts, f"ENV_{name}")


def build_table(P, name, pr, x, y):
    r, h = pr.get("r", 3.0), pr.get("h", 0.8)
    wood = P.make_mat(f"{name}_wood", (0.22, 0.13, 0.08))
    cloth = P.make_mat(f"{name}_cloth", (0.72, 0.12, 0.08), rough=0.7)
    gold = P.make_mat(f"{name}_gold", (1.0, 0.75, 0.3), emit=1.6)
    parts = [P.prim("cyl", wood, f"ENV_{name}_base", loc=P.bl((x, y), h * 0.4), radius=r * 0.55, depth=h * 0.8, vertices=40),
             P.prim("cyl", cloth, f"ENV_{name}", loc=P.bl((x, y), h - 0.2), radius=r + 0.05, depth=0.42, vertices=64)]
    for k, rr in enumerate((0.92, 0.66, 0.3)):   # 金色の魔法陣の輪
        bpy.ops.mesh.primitive_torus_add(major_radius=r * rr, minor_radius=0.025, major_segments=64, minor_segments=6, location=P.bl((x, y), h + 0.02))
        o = bpy.context.object
        o.data.materials.append(gold)
        parts.append(o)
    rr = r * 0.66
    for tri in (0, 1):   # 六芒星
        for k in range(3):
            a0, a1 = 90 + tri * 60 + k * 120, 90 + tri * 60 + (k + 1) * 120
            p0, p1 = radial(x, y, a0, rr), radial(x, y, a1, rr)
            parts.append(seg_between(P, gold, f"ENV_{name}_l{tri}{k}", (p0[0], -p0[1], h + 0.02), (p1[0], -p1[1], h + 0.02), 0.02))
    bpy.ops.mesh.primitive_torus_add(major_radius=r + 0.05, minor_radius=0.03, major_segments=64, minor_segments=6, location=P.bl((x, y), h - 0.41))   # 金の房
    o = bpy.context.object
    o.data.materials.append(P.make_mat(f"{name}_fringe", (0.85, 0.62, 0.25), rough=0.4))
    parts.append(o)
    join(parts, f"ENV_{name}")


def build_chair(P, name, pr, x, y):
    tall = pr.get("tall", 1.0)
    red = P.make_mat(f"{name}_red", (0.42, 0.13, 0.12), rough=0.6)
    dark = P.make_mat(f"{name}_dark", (0.2, 0.08, 0.07))
    parts = [P.prim("cube", red, f"ENV_{name}_seat", loc=(0, 0, 0.47), scale=(0.3, 0.3, 0.05), size=2.0)]
    for k, (sx, sy) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
        parts.append(P.prim("cube", dark, f"ENV_{name}_leg{k}", loc=(sx * 0.26, sy * 0.26, 0.21), scale=(0.035, 0.035, 0.21), size=2.0))
    bh = 1.35 * tall   # 背もたれ（座る人の後ろ = 南、-Y）
    parts.append(P.prim("cube", red, f"ENV_{name}_back", loc=(0, -0.3, 0.52 + bh / 2), scale=(0.3, 0.04, bh / 2), size=2.0))
    tip = P.prim("cone", red, f"ENV_{name}_tip", loc=(0, -0.3, 0.52 + bh + 0.22), radius1=0.42, radius2=0.0, depth=0.45, vertices=4)
    tip.rotation_euler = (0, 0, math.pi / 4)
    tip.scale = (1.0, 0.13, 1.0)
    parts.append(tip)
    for sx in (-1, 1):
        parts.append(P.prim("cube", dark, f"ENV_{name}_post{sx}", loc=(sx * 0.3, -0.3, 0.52 + bh / 2), scale=(0.045, 0.045, bh / 2 + 0.05), size=2.0))
        parts.append(P.prim("sphere", dark, f"ENV_{name}_fin{sx}", loc=(sx * 0.3, -0.3, 0.6 + bh), radius=0.07))
        parts.append(P.prim("cube", dark, f"ENV_{name}_arm{sx}", loc=(sx * 0.3, -0.02, 0.72), scale=(0.04, 0.28, 0.03), size=2.0))
    o = join(parts, f"ENV_{name}")
    f = math.radians(pr.get("face", 0))
    if pr.get("fallen"):   # 背中から倒れた椅子
        o.rotation_euler = (math.radians(-82), 0, -f)
        o.location = P.bl((x, y), 0.32)
    else:
        o.rotation_euler = (0, 0, -f)
        o.location = P.bl((x, y), 0.0)


def build_orrery(P, name, pr, x, y, lamps):
    z, r = pr.get("z", 6.2), pr.get("r", 2.8)
    globe = P.beam_mat(f"{name}_globe", (1.0, 0.8, 0.72), strength=0.35, alpha=0.85)   # beam_mat の alpha は透ける割合
    line = P.make_mat(f"{name}_line", (1.0, 0.82, 0.4), emit=2.2 * lamps)
    gold = P.make_mat(f"{name}_gold", (0.85, 0.62, 0.25), rough=0.35)
    parts = [P.prim("sphere", globe, f"ENV_{name}", loc=P.bl((x, y), z), radius=r)]
    for k in range(10):   # 星座の線（球の表面の短い光る線）
        a0 = rnd(k, 21) * 2 * math.pi
        e0 = (rnd(k, 22) - 0.3) * 1.2
        pts = []
        for s in range(3):
            a, e = a0 + s * 0.25, e0 + (rnd(k * 3 + s, 23) - 0.5) * 0.4
            pts.append((x + r * math.cos(e) * math.cos(a), -(y + r * math.cos(e) * math.sin(a)), z + r * math.sin(e)))
        for s in range(2):
            parts.append(seg_between(P, line, f"ENV_{name}_c{k}{s}", pts[s], pts[s + 1], 0.02))
    for k, (maj, tilt, zo) in enumerate(((r * 1.4, 14, 0.0), (r * 0.95, 0, -r * 0.85))):   # 金の輪
        bpy.ops.mesh.primitive_torus_add(major_radius=maj, minor_radius=0.06, major_segments=64, minor_segments=8,
                                         location=P.bl((x, y), z + zo), rotation=(math.radians(tilt), math.radians(tilt * 0.4), 0))
        o = bpy.context.object
        o.data.materials.append(gold)
        parts.append(o)
    h_top = pr.get("table_h", 0.8)
    parts.append(P.prim("cyl", gold, f"ENV_{name}_axis", loc=P.bl((x, y), (h_top + z) / 2), radius=0.1, depth=z - h_top, vertices=12))   # 円卓の上の真鍮の軸
    parts.append(P.prim("cone", gold, f"ENV_{name}_foot", loc=P.bl((x, y), h_top + 0.25), radius1=0.45, radius2=0.1, depth=0.5, vertices=16))
    parts.append(P.prim("cyl", gold, f"ENV_{name}_hang", loc=P.bl((x, y), z + r + (14.0 - z - r) / 2), radius=0.05, depth=max(0.2, 14.0 - z - r), vertices=8))
    cols = [(1.0, 0.8, 0.3), (1.0, 0.55, 0.62), (0.96, 0.94, 1.0), (1.0, 0.8, 0.3), (1.0, 0.55, 0.62), (0.96, 0.94, 1.0)]
    for k in range(6):   # 光る惑星（金の輪の上）
        a = math.radians(k * 60 + 20)
        px, py = x + r * 1.4 * math.cos(a), y + r * 1.4 * math.sin(a) * math.cos(math.radians(14))
        pz = z + r * 1.4 * math.sin(a) * math.sin(math.radians(14)) + (rnd(k, 24) - 0.5) * 0.6
        pm = P.make_mat(f"{name}_pl{k}", cols[k], emit=2.6 * lamps)
        parts.append(P.prim("sphere", pm, f"ENV_{name}_pl{k}", loc=P.bl((px, py), pz), radius=0.32 + 0.16 * rnd(k, 25)))
        parts.append(seg_between(P, gold, f"ENV_{name}_arm{k}", (x, -y, z), (px, -py, pz), 0.03))
    join(parts, f"ENV_{name}")
    P.point_light(f"LGT_{name}", x, y, z - r * 0.6, pr.get("energy", 900) * lamps, (1.0, 0.75, 0.45), r * 0.8)


def build_door(P, name, pr, x, y):
    b = pr.get("bearing", 180)
    w, h, a = pr.get("w", 3.0), pr.get("h", 4.4), math.radians(pr.get("open", 70))
    leaf = P.make_mat(f"{name}_leaf", (0.24, 0.14, 0.09))
    brass = P.make_mat(f"{name}_brass", (0.85, 0.65, 0.3), rough=0.3)
    bb = math.radians(b)
    u = (math.cos(bb), math.sin(bb))           # 壁に沿う向き
    inw = (-math.sin(bb), math.cos(bb))        # 部屋の中へ
    parts = []
    for sg in (1, -1):
        hx, hy = x + u[0] * sg * w / 2, y + u[1] * sg * w / 2
        v = (math.cos(a) * -u[0] * sg + math.sin(a) * inw[0], math.cos(a) * -u[1] * sg + math.sin(a) * inw[1])
        cx, cy = hx + v[0] * w / 4, hy + v[1] * w / 4
        rot = math.degrees(math.atan2(v[0], -v[1])) - 90
        parts.append(P.box(leaf, f"ENV_{name}_{sg}", cx, cy, w / 2, 0.1, h, 0.0, rot))
        kx, ky = hx + v[0] * w * 0.42, hy + v[1] * w * 0.42
        parts.append(P.prim("sphere", brass, f"ENV_{name}_k{sg}", loc=P.bl((kx, ky), 1.2), radius=0.07))
    join(parts, f"ENV_{name}")


def build_rubble(P, name, pr, x, y):
    stone = P.make_mat(f"{name}_m", (0.27, 0.22, 0.2))
    wood = P.make_mat(f"{name}_w", (0.3, 0.18, 0.12))
    parts = []
    for k in range(int(pr.get("n", 10))):
        rx, ry = radial(x, y, rnd(k, 31) * 360, pr.get("r", 2.5) * rnd(k, 32))
        s = 0.25 + 0.9 * rnd(k, 33)
        parts.append(P.box(stone if k % 3 else wood, f"ENV_{name}_{k}", rx, ry, s * (1.0 + rnd(k, 34)), s * 0.8, s * (0.5 + rnd(k, 35)), 0.0, rnd(k, 36) * 180))
    join(parts, f"ENV_{name}")


# ---------- 人形 ----------

def decorate(P, who, root, head, arms, sh, body_h, col):
    hr, r = sh["hr"], sh["r"]
    al = body_h * 0.6
    if who == "rubens":   # 橙のフリルの頭飾り、長い黄色の髪、背中の茶色の鞄、左手の大きな緑の本
        P.prim("cyl", P.make_mat("rubens_bonnet", (0.95, 0.55, 0.15)), "rubens_bonnet", head, (0, -0.02, hr * 1.75), (math.radians(-20), 0, 0), radius=hr * 1.05, depth=0.06)
        P.prim("cyl", P.make_mat("rubens_hair", (0.92, 0.85, 0.35)), "rubens_hair", head, (0, -hr * 0.85, -0.15), radius=hr * 0.85, depth=0.45)
        P.prim("cube", P.make_mat("rubens_bag", (0.5, 0.3, 0.15)), "rubens_bag", root, (0, -r - 0.07, body_h * 0.62), size=2.0, scale=(0.13, 0.06, 0.12))
        P.prim("cube", P.make_mat("rubens_book", (0.25, 0.45, 0.25)), "rubens_book", arms[-1], (0, 0.1, -al + 0.05), size=2.0, scale=(0.03, 0.12, 0.15))
    elif who == "nezaria":   # とても大きな白いとんがり帽子（裏は薄紫）と金の輪、帽子のつばの白ネズミ、長い薄紫の髪、右に立てた金の頭の杖
        white = P.make_mat("nez_hat", (0.96, 0.96, 0.98))
        lav = P.make_mat("nez_lav", (0.72, 0.62, 0.9))
        gold = P.make_mat("nez_gold", (0.9, 0.68, 0.25), rough=0.3)
        P.prim("cyl", white, "nez_brim", head, (0, 0, hr * 1.7), radius=0.5, depth=0.025)
        P.prim("cyl", lav, "nez_brim_under", head, (0, 0, hr * 1.7 - 0.018), radius=0.49, depth=0.012)
        P.prim("cone", white, "nez_cone", head, (0, -0.04, hr * 1.7 + 0.3), (math.radians(-10), 0, 0), radius1=0.24, radius2=0.0, depth=0.6)
        bpy.ops.mesh.primitive_torus_add(major_radius=0.36, minor_radius=0.02, location=(0, 0, 0))
        ring = bpy.context.object
        ring.name = "nez_ring"
        ring.data.materials.append(gold)
        ring.parent = head
        ring.location = (0, 0, hr * 1.7 + 0.12)
        ring.rotation_euler = (math.radians(12), 0, 0)
        P.prim("cyl", lav, "nez_hair", head, (0, -hr * 0.95, -0.45), radius=hr * 0.85, depth=1.0)
        mouse = P.make_mat("nez_mouse", (0.97, 0.97, 0.97))
        P.prim("sphere", mouse, "nez_mouse", head, (-0.28, 0.18, hr * 1.7 + 0.05), radius=0.045, scale=(1.0, 1.3, 0.85))
        for sx in (-1, 1):
            P.prim("sphere", mouse, f"nez_mouse_ear{sx}", head, (-0.28 + sx * 0.03, 0.22, hr * 1.7 + 0.1), radius=0.02, scale=(1, 0.4, 1))
        silver = P.make_mat("nez_staff", (0.78, 0.8, 0.85), rough=0.3)
        P.prim("cyl", silver, "nez_staff", root, (r + 0.14, 0.1, 0.9), radius=0.018, depth=1.8)
        P.prim("sphere", gold, "nez_staff_head", root, (r + 0.14, 0.1, 1.9), radius=0.1, scale=(1, 1, 1.3))
        bpy.ops.mesh.primitive_torus_add(major_radius=0.1, minor_radius=0.025, location=(0, 0, 0))
        sr = bpy.context.object
        sr.name = "nez_staff_ring"
        sr.data.materials.append(gold)
        sr.parent = root
        sr.location = (r + 0.14, 0.1, 1.82)
    elif who == "metallica-giant":   # メタリカの帽子と三つ編みを大きくしたもの、足元の碧の瘴気
        k = GIANT_K
        hat = P.make_mat("giant_hat", (0.35, 0.2, 0.5))
        P.prim("cyl", hat, "giant_brim", head, (0, 0, hr * 1.7), radius=0.38 * k, depth=0.025 * k)
        P.prim("cone", hat, "giant_cone", head, (0, -0.03 * k, hr * 1.7 + 0.3 * k), (math.radians(8), 0, 0), radius1=0.25 * k, radius2=0.0, depth=0.6 * k)
        braid = P.make_mat("giant_braid", tuple(c * 0.45 for c in col))
        for sx in (-1, 1):
            P.prim("cyl", braid, f"giant_braid{sx}", head, (sx * 0.1 * k, -hr * 0.9, -0.45 * k), radius=0.04 * k, depth=1.1 * k)
        mist = P.beam_mat("giant_mist", (0.3, 0.95, 0.65), strength=0.9, alpha=0.6)
        for j in range(7):
            a = j * 2 * math.pi / 7
            P.prim("sphere", mist, f"giant_mist{j}", root, (math.cos(a) * r * 1.6, math.sin(a) * r * 1.6, 0.6 + 0.8 * rnd(j, 41)),
                   radius=1.0, scale=(r * 0.9, r * 0.9, 0.9 + 0.8 * rnd(j, 42)))
    elif who == "teressa-swamp":   # 沼を浴びて垂れたとんがり帽子（碧に濡れた色）
        hat = P.make_mat("tswamp_hat", (0.18, 0.42, 0.34))
        P.prim("cyl", hat, "tswamp_brim", head, (0, 0, hr * 1.6), (math.radians(-18), 0, 0), radius=0.3, depth=0.02)
        P.prim("cone", hat, "tswamp_cone", head, (0, -0.12, hr * 1.6 + 0.18), (math.radians(-55), 0, 0), radius1=0.17, radius2=0.0, depth=0.45)
        drip = P.beam_mat("tswamp_drip", (0.25, 0.9, 0.6), strength=0.6, alpha=0.8)
        P.prim("sphere", drip, "tswamp_drip", root, (0, 0, body_h * 0.6), radius=1.0, scale=(r * 1.25, r * 1.25, body_h * 0.55))


# ---------- 効果 ----------

def _mix(m):
    return m.node_tree.nodes.get("Mix Shader")


def build_fx(P, fx, i):
    kind = fx.get("kind")
    if kind == "fog":   # 霧: 平たい半透明の白い球をいくつも重ねる。濃さは pose で変える
        objs = []
        col = tuple(v / 255 for v in fx.get("color", [235, 238, 250]))
        for j in range(int(fx.get("n", 12))):
            m = P.beam_mat(f"fx{i}_fog{j}", col, strength=fx.get("glow", 0.5), alpha=1.0)
            objs.append(P.prim("sphere", m, f"fx{i}_fog{j}", None, (0, 0, -50), radius=1.0))
        return objs
    if kind == "bolt":   # 雷撃: 折れ線の光る細い円柱（落ちる先ごとに8本）と、落ちる先の閃光
        m = P.make_mat(f"fx{i}_bolt", tuple(v / 255 for v in fx.get("color", [255, 245, 150])), emit=12.0)
        objs = []
        for k in range(len(fx.get("to") or [])):
            for s in range(8):
                objs.append(P.prim("cyl", m, f"fx{i}_b{k}{s}", None, (0, 0, -50), radius=0.07, depth=1.0, vertices=6))
        bpy.ops.object.light_add(type="POINT", location=(0, 0, -50))
        lt = bpy.context.object
        lt.name = f"LGT_fx{i}"
        lt.data.color = (1.0, 0.95, 0.6)
        lt.data.shadow_soft_size = 1.0
        objs.append(lt)
        return objs
    if kind == "flash":   # 画面いっぱいの光か暗転: カメラに付けた小さな球の内側（半透明の発光）。手前の物も覆う
        col = tuple(v / 255 for v in fx.get("color", [255, 255, 255]))
        m = P.beam_mat(f"fx{i}_flash", col, strength=fx.get("glow", 3.0) if sum(col) > 0 else 0.0, alpha=1.0)
        o = P.prim("sphere", m, f"fx{i}_flash", None, (0, 0, -500), radius=0.3)
        o.visible_shadow = False
        o.visible_diffuse = False
        o.visible_glossy = False
        return [o]
    if kind == "mist":    # 半透明の噴き上げ（沼の瘴気）
        col = tuple(v / 255 for v in fx.get("color", [40, 190, 140]))
        objs = []
        for j in range(6):
            m = P.beam_mat(f"fx{i}_mist{j}", col, strength=fx.get("glow", 0.9), alpha=1.0 - fx.get("alpha", 0.45))
            objs.append(P.prim("sphere", m, f"fx{i}_mist{j}", None, (0, 0, -50), radius=1.0))
        return objs
    if kind == "surge":   # 沼の波: 出どころから行き先へ、波の先が進む球の列（alpha を書くと半透明の霞）
        col = tuple(v / 255 for v in fx.get("color", [32, 160, 120]))
        if fx.get("alpha"):
            m = P.beam_mat(f"fx{i}_surge", col, strength=fx.get("glow", 0.8), alpha=1.0 - fx["alpha"])
        else:
            m = P.make_mat(f"fx{i}_surge", col, rough=0.25, emit=fx.get("glow", 0.5))
        return [P.prim("sphere", m, f"fx{i}_s{j}", None, (0, 0, -50), radius=1.0) for j in range(int(fx.get("n", 16)))]
    return None


def pose_fx(P, fx, objs, t, pos, place):
    kind = fx.get("kind")
    if kind not in ("fog", "bolt", "flash", "surge", "mist"):
        return False
    at = fx.get("at", [0.0, 0.0])
    p = pos[at] if isinstance(at, str) and at in pos else place(at)
    t0, t1 = fx["t"]
    z0 = fx.get("z", 0.0)
    if kind == "mist":    # plume と同じ形（球の柱が伸びて広がる）
        import motion
        amt = motion.fx_amount(fx, t)
        r, h = fx.get("r", 2.0), fx.get("h", 4.0)
        for j, o in enumerate(objs):
            if amt <= 0.01:
                o.location = (0, 0, -50)
                continue
            f = j / max(1, len(objs) - 1)
            ang = j * 2.4
            rr = r * (0.35 + 0.65 * f) * amt
            o.location = P.bl((p[0] + math.cos(ang) * r * 0.3 * f, p[1] + math.sin(ang) * r * 0.3 * f), z0 + h * f * amt)
            o.scale = (rr, rr, rr * 0.8)
        return True
    if kind == "fog":
        rise, fade = fx.get("rise", 2.0), fx.get("fade", 2.0)
        u = 0.0 if t < t0 else min(1.0, (t - t0) / max(1e-6, rise))
        if t > t1:
            u = max(0.0, 1.0 - (t - t1) / max(1e-6, fade))
        R, H = fx.get("r", 10.0), fx.get("h", 2.5)
        for j, o in enumerate(objs):
            if u <= 0.001:
                o.location = (0, 0, -50)
                continue
            a = rnd(j, 51) * 2 * math.pi + t * 0.05 * (1 if j % 2 else -1)   # ゆっくり漂う
            rr = R * (0.15 + 0.7 * rnd(j, 52))
            o.location = P.bl((p[0] + math.cos(a) * rr, p[1] + math.sin(a) * rr), z0 + H * (0.2 + 0.6 * rnd(j, 53)))
            s = R * (0.35 + 0.3 * rnd(j, 54))
            o.scale = (s, s * (0.7 + 0.3 * rnd(j, 55)), H * 0.6)
            mx = _mix(o.data.materials[0])
            if mx:
                mx.inputs["Fac"].default_value = 1.0 - fx.get("alpha", 0.22) * u   # Fac は透ける割合
        return True
    if kind == "flash":
        o = objs[0]
        cam = bpy.data.objects.get("CAM")
        if cam is not None and o.parent is not cam:   # カメラに付ける（カメラと一緒に動く）
            o.parent = cam
        if not (t0 <= t <= t1) or cam is None:
            o.location = (0, 0, -500)
            return True
        pk = fx.get("peak", t0 + 0.1)
        u = (t - t0) / max(1e-6, pk - t0) if t < pk else 1.0 - (t - pk) / max(1e-6, t1 - pk)
        o.location = (0.0, 0.0, 0.0)
        mx = _mix(o.data.materials[0])
        if mx:
            mx.inputs["Fac"].default_value = 1.0 - max(0.0, min(1.0, u)) * fx.get("alpha", 0.95)
        return True
    if kind == "surge":
        a = p
        b = pos[fx["to"]] if isinstance(fx["to"], str) and fx["to"] in pos else place(fx["to"])
        u = 0.0 if t < t0 else min(1.0, (t - t0) / max(1e-6, t1 - t0))
        n = len(objs)
        R, H = fx.get("r", 1.6), fx.get("h", 1.2)
        for j, o in enumerate(objs):
            f = j / max(1, n - 1)
            if u <= 0.001 or f > u:
                o.location = (0, 0, -50)
                continue
            x = a[0] + (b[0] - a[0]) * f + (rnd(j, 61) - 0.5) * R
            y = a[1] + (b[1] - a[1]) * f + (rnd(j, 62) - 0.5) * R
            front = max(0.0, 1.0 - (u - f) * 3.0)   # 波の先が高い
            o.location = P.bl((x, y), z0 + H * (0.2 + 0.8 * front) * (1.0 - f * 0.3))
            s = R * (0.6 + 0.4 * rnd(j, 63))
            o.scale = (s, s, H * (0.5 + 0.8 * front))
        return True
    # bolt
    targets = fx.get("to") or []
    lt = objs[-1]
    on = t0 <= t <= t1 and (int(t * fx.get("rate", 14)) % 3 != 2)
    if not on:
        for o in objs:
            o.location = (0, 0, -50)
        lt.data.energy = 0.0
        return True
    seed = int(t * fx.get("rate", 14))
    for k, tg in enumerate(targets):
        q = pos[tg] if isinstance(tg, str) and tg in pos else place(tg)
        zq = fx.get("z_to", 0.3)
        pts = []
        for s in range(9):
            f = s / 8
            jx = 0.0 if s in (0, 8) else (rnd(seed * 13 + k * 7 + s, 71) - 0.5) * 1.6
            jy = 0.0 if s in (0, 8) else (rnd(seed * 13 + k * 7 + s, 72) - 0.5) * 1.6
            pts.append((p[0] + (q[0] - p[0]) * f + jx, -(p[1] + (q[1] - p[1]) * f + jy), z0 + (zq - z0) * f))
        for s in range(8):
            from mathutils import Vector
            o = objs[k * 8 + s]
            va, vb = Vector(pts[s]), Vector(pts[s + 1])
            d = vb - va
            o.location = tuple((va + vb) / 2)
            o.rotation_mode = "QUATERNION"
            o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d.normalized())
            o.scale = (1.0, 1.0, max(0.05, d.length))
    q0 = targets[0] if targets else at
    q = pos[q0] if isinstance(q0, str) and q0 in pos else place(q0)
    lt.location = P.bl(q, 2.0)
    lt.data.energy = fx.get("energy", 6000)
    return True
