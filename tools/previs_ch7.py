"""7章（A33〜、魔女と虫姫）の下絵の形: 新しい人形と、チンクエのゴミ山・森・沼地のピラーの道具。tools/previs.py から呼ぶ。

previs_council.py と同じ呼ばれ方（previs.py の EXT の並び）:
  SHAPE                 人形の太さ（previs.SHAPE に足す）
  build_extra(P, ...)   道具の頭の名前で形を作る（作ったら True）
  decorate(P, ...)      人形の飾り（ミトンの髪・触角・羽根、アロマニ・ジェルの角と耳、馬車は人形の体を隠して箱馬車と馬を作る）
  build_fx / pose_fx    今は無し（霧・瘴気・沼の波は previs_council の fx を使う）

人形（world/ch7-people-extra.yaml）:
  mitten          ミトン（refs/mitten_sheet_01）。青い殻、桃色の髪、黒いリボン、赤紫の触角、黄色の毛の襟と腹、腕4本、透けた青い羽根
  aromani-gel     アロマニ・ジェル（refs/aromani-gel_sheet_01）。金色のスライム、角、紫の耳、緑の手、足元のゲル
  heinz-carriage  ハインツの馬車（refs/heinz-carriage_sheet_01）。紺と金の箱馬車、白い馬2頭、御者台の兵士。向きは馬の向き（前 = 人形の正面）
  heinz           ハインツ（金髪、白と紺の上着）
  heavy-fruit     ずっしりの実、perfume 香水の小瓶（人形の体を隠して小物だけ）
  jade-fruit      翠ノ実（refs/yakai-props_sheet_01。りんご形の光る緑の実、茶色の茎と葉 1 枚。A25 で盲いた魔女がビスコの手に渡す。2026-11-13）

道具（world/cinque-forest-layout.yaml）:
  junk    ゴミの山（苔の盛り土に樽・木箱・車輪・骨・壺・布）。r・h
  wheel   荷車の車輪（rot 向き、lean 傾き）
  crate   木箱。w・d・h
  reeds   かなきり穂の茂み（細い茎と金色の穂）。r・n・h
  pond    沼の水面（暗い碧、縁の泥）。r
  log     倒木。len・r・rot・lean
"""
import math

import bpy

SHAPE = {
    "mitten": {"r": 0.18, "hr": 0.24, "arm": 0.05},
    "aromani-gel": {"r": 0.75, "hr": 0.42, "arm": 0.16},
    "heinz-carriage": {"r": 1.2, "hr": 0.3, "arm": 0.05},
    "heinz": {"r": 0.16, "hr": 0.13, "arm": 0.05},
    "heavy-fruit": {"r": 0.16, "hr": 0.02, "arm": 0.01},
    "perfume": {"r": 0.04, "hr": 0.02, "arm": 0.005},
    "jade-fruit": {"r": 0.05, "hr": 0.02, "arm": 0.005},
}
PREFIXES = ("junk", "wheel", "crate", "reeds", "pond", "log", "mpillar")


def rnd(k, salt=0):
    return ((k * 7919 + salt * 104729) % 1000) / 1000.0


def _torus(P, mat, name, parent, loc, rot, R, r):
    bpy.ops.mesh.primitive_torus_add(major_radius=R, minor_radius=r, location=(0, 0, 0))
    o = bpy.context.object
    o.name = name
    o.data.materials.append(mat)
    o.parent = parent
    o.location = loc
    o.rotation_euler = rot
    return o


# ---------- 道具 ----------

def build_extra(P, name, pr, x, y, lit):
    if not name.startswith(PREFIXES):
        return False
    if name.startswith("junk"):
        build_junk(P, name, pr, x, y)
    elif name.startswith("wheel"):
        build_wheel(P, name, x, y, pr.get("r", 0.6), pr.get("rot", 0), pr.get("lean", 70), pr.get("z", 0.0))
    elif name.startswith("crate"):
        wood = P.make_mat(f"{name}_m", (0.42, 0.28, 0.16))
        w, d, h = pr.get("w", 0.7), pr.get("d", 0.6), pr.get("h", 0.55)
        P.prim("cube", wood, f"ENV_{name}", loc=P.bl((x, y), pr.get("z", 0.0) + h / 2), rot=(0, 0, -math.radians(pr.get("rot", 0))), scale=(w / 2, d / 2, h / 2), size=2.0)
    elif name.startswith("reeds"):   # かなきり穂: 細い茎と金色の穂（茂みの中で百騎兵が揺すると実が落ちる）
        stem = P.make_mat(f"{name}_stem", (0.55, 0.5, 0.22))
        ear = P.make_mat(f"{name}_ear", tuple(v / 255 for v in pr.get("ear", [235, 184, 72])))   # がま（沼の岸）は ear: [90, 55, 30]
        r, n, hh = pr.get("r", 1.2), int(pr.get("n", 26)), pr.get("h", 1.5)
        for k in range(n):
            a = rnd(k, 3) * 2 * math.pi
            rr = r * math.sqrt(rnd(k, 4))
            sx, sy = x + math.cos(a) * rr, y + math.sin(a) * rr
            h = hh * (0.7 + 0.5 * rnd(k, 5))
            tilt = (rnd(k, 6) - 0.5) * 0.35
            P.prim("cyl", stem, f"ENV_{name}_s{k}", loc=P.bl((sx, sy), h / 2), rot=(tilt, (rnd(k, 7) - 0.5) * 0.35, 0), radius=0.018, depth=h, vertices=6)
            P.prim("sphere", ear, f"ENV_{name}_e{k}", loc=P.bl((sx + tilt * h * 0.5, sy), h), radius=0.06, scale=(1, 1, 2.6))
    elif name.startswith("pond"):   # 沼の水面（暗い碧）と縁の泥
        r = pr.get("r", 6.0)
        mud = P.make_mat(f"{name}_mud", (0.3, 0.24, 0.14))
        water = P.make_mat(f"{name}_w", tuple(v / 255 for v in pr.get("color", [36, 84, 72])), rough=0.35)
        P.prim("cyl", mud, f"ENV_{name}_mud", loc=P.bl((x, y), 0.015), radius=r + 0.8, depth=0.03, vertices=64)
        P.prim("cyl", water, f"ENV_{name}", loc=P.bl((x, y), 0.035), radius=r, depth=0.03, vertices=64)
    elif name.startswith("mpillar"):   # 沼地のピラー（咲く前）: 黒い尖塔に緑に光るガラス、ふくらんだ頭と尖った先（world/marsh-pillar の参考画）
        h, r = pr.get("h", 4.4), pr.get("r", 0.42)
        dark = P.make_mat(f"{name}_dark", (0.08, 0.09, 0.08), rough=0.4)
        glass = P.make_mat(f"{name}_glass", (0.35, 0.95, 0.55), emit=pr.get("glow", 1.2))
        P.prim("cone", dark, f"ENV_{name}_foot", loc=P.bl((x, y), 0.35), radius1=r * 1.6, radius2=r * 0.9, depth=0.7)
        P.prim("cyl", glass, f"ENV_{name}", loc=P.bl((x, y), h * 0.42), radius=r * 0.9, depth=h * 0.62)
        for k in range(4):   # 黒い枠（縦の4本）
            a = k * math.pi / 2 + math.pi / 4
            P.prim("cyl", dark, f"ENV_{name}_rib{k}", loc=P.bl((x + math.cos(a) * r * 0.92, y + math.sin(a) * r * 0.92), h * 0.42), radius=0.04, depth=h * 0.62)
        P.prim("sphere", dark, f"ENV_{name}_bulb", loc=P.bl((x, y), h * 0.8), radius=r * 1.25, scale=(1, 1, 1.25))
        P.prim("cone", glass, f"ENV_{name}_tip", loc=P.bl((x, y), h * 0.97), radius1=r * 0.35, radius2=0.0, depth=h * 0.15)
        P.point_light(f"LGT_{name}", x, y, h * 0.5, 60, (0.4, 1.0, 0.55), 0.6)
    elif name.startswith("log"):   # 倒木（苔むした横倒しの幹。rot は長い向きの方位、lean は片方の端を上げる角度）
        m = P.make_mat(f"{name}_m", (0.3, 0.26, 0.14))
        L, r = pr.get("len", 4.0), pr.get("r", 0.35)
        P.prim("cyl", m, f"ENV_{name}", loc=P.bl((x, y), r + L / 2 * math.sin(math.radians(pr.get("lean", 0)))),
               rot=(math.pi / 2 - math.radians(pr.get("lean", 0)), 0, -math.radians(pr.get("rot", 0))), radius=r, depth=L, vertices=10)
    return True


def build_wheel(P, name, x, y, r, rot, lean, z0, parent=None, mats=None):
    """荷車の車輪（輪・輻・こしき）。rot は車軸の向き（方位）、lean は地面からの傾き（90 で立つ、0 で寝る）。"""
    wood, iron = mats or (P.make_mat(f"{name}_wood", (0.45, 0.3, 0.17)), P.make_mat(f"{name}_iron", (0.25, 0.23, 0.22)))
    root = bpy.data.objects.new(f"ENV_{name}", None)
    bpy.context.collection.objects.link(root)
    root.parent = parent
    root.location = P.bl((x, y), z0 + r * math.sin(math.radians(lean)) + 0.03) if parent is None else (x, y, z0)
    root.rotation_euler = (0, math.radians(90 - lean), -math.radians(rot))
    _torus(P, iron, f"ENV_{name}_rim", root, (0, 0, 0), (0, math.pi / 2, 0), r, r * 0.07)
    P.prim("cyl", wood, f"ENV_{name}_hub", root, (0, 0, 0), (0, math.pi / 2, 0), radius=r * 0.18, depth=r * 0.3)
    for k in range(10):
        a = k * math.pi / 5
        P.prim("cyl", wood, f"ENV_{name}_sp{k}", root, (0, math.cos(a) * r / 2, math.sin(a) * r / 2), (a, 0, 0), radius=r * 0.035, depth=r, vertices=6)
    return root


def build_junk(P, name, pr, x, y):
    """ゴミの山: 苔の盛り土に、樽・木箱・車輪・骨・壺・ぼろ布（refs の背景 cinque-dump の5枚の見た目）。"""
    r, h = pr.get("r", 3.0), pr.get("h", 1.4)
    moss = P.make_mat(f"{name}_moss", (0.34, 0.33, 0.14))
    dirt = P.make_mat(f"{name}_dirt", (0.36, 0.27, 0.17))
    wood = P.make_mat(f"{name}_wood", (0.42, 0.28, 0.15))
    iron = P.make_mat(f"{name}_iron", (0.24, 0.22, 0.2))
    bone = P.make_mat(f"{name}_bone", (0.85, 0.8, 0.68))
    pot = P.make_mat(f"{name}_pot", (0.5, 0.36, 0.25))
    cloth = P.make_mat(f"{name}_cloth", (0.55, 0.22, 0.2))
    P.prim("sphere", moss, f"ENV_{name}", loc=P.bl((x, y), 0.0), radius=1.0, scale=(r, r * 0.85, h))
    P.prim("sphere", dirt, f"ENV_{name}_b", loc=P.bl((x + r * 0.3, y + r * 0.25), 0.0), radius=1.0, scale=(r * 0.6, r * 0.55, h * 0.7))
    k0 = int(pr.get("seed", 0))
    for k in range(int(pr.get("n", 9))):
        kk = k + k0 * 31
        a = rnd(kk, 11) * 2 * math.pi
        rr = r * (0.35 + 0.65 * rnd(kk, 12))
        px, py = x + math.cos(a) * rr, y + math.sin(a) * rr * 0.85
        zz = max(0.0, h * (1 - (rr / r) ** 2)) * 0.9
        kind = kk % 6
        if kind == 0:   # 樽（寝かせる）
            P.prim("cyl", wood, f"ENV_{name}_barrel{k}", loc=P.bl((px, py), zz + 0.3), rot=(math.pi / 2, 0, a), radius=0.3, depth=0.8)
        elif kind == 1:   # 木箱
            P.prim("cube", wood, f"ENV_{name}_crate{k}", loc=P.bl((px, py), zz + 0.25), rot=(0.2 * rnd(kk, 13), 0.15, a), scale=(0.35, 0.3, 0.25), size=2.0)
        elif kind == 2:   # 車輪（立てかける）
            build_wheel(P, f"{name}_wh{k}", px, py, 0.55, math.degrees(a), 55 + 30 * rnd(kk, 14), zz, mats=(wood, iron))
        elif kind == 3:   # 骨
            for j in range(3):
                P.prim("cyl", bone, f"ENV_{name}_bone{k}{j}", loc=P.bl((px + 0.2 * j, py + 0.1 * j), zz + 0.06), rot=(math.pi / 2, 0, a + j), radius=0.04, depth=0.6)
        elif kind == 4:   # 壺
            P.prim("sphere", pot, f"ENV_{name}_pot{k}", loc=P.bl((px, py), zz + 0.22), radius=0.25, scale=(1, 1, 1.15))
        else:   # ぼろ布
            P.prim("cube", cloth, f"ENV_{name}_cloth{k}", loc=P.bl((px, py), zz + 0.05), rot=(0.1, 0.2, a), scale=(0.45, 0.35, 0.03), size=2.0)


# ---------- 人形の飾り ----------

def _hide_default(root):
    for o in root.children_recursive:
        o.hide_render = True
        o.hide_viewport = True


def decorate(P, who, root, head, arms, sh, body_h, col):
    hr, r = sh["hr"], sh["r"]
    if who == "mitten":
        pink = P.make_mat("mitten_hair", (0.93, 0.55, 0.78))
        dark = P.make_mat("mitten_bow", (0.1, 0.12, 0.22), rough=0.35)
        mag = P.make_mat("mitten_ant", (0.82, 0.12, 0.5))
        fur = P.make_mat("mitten_fur", (0.85, 0.72, 0.4))
        belly = P.make_mat("mitten_belly", (0.82, 0.66, 0.36))
        shell = P.make_mat("mitten_shell", (0.27, 0.58, 0.72), rough=0.3)
        wing = P.beam_mat("mitten_wing", (0.6, 0.85, 1.0), strength=0.35, alpha=0.55)
        P.prim("sphere", shell, "mitten_helm", head, (0, -0.02, hr * 1.05), radius=hr * 1.18, scale=(1, 1.05, 1.0))   # 頭の殻
        P.prim("sphere", pink, "mitten_hair", head, (0, 0.04, hr * 0.85), radius=hr * 1.12, scale=(1.05, 0.95, 0.8))   # 前髪
        for sx in (-1, 1):
            P.prim("sphere", dark, f"mitten_bow{sx}", head, (sx * hr * 0.55, 0.0, hr * 2.05), radius=hr * 0.5, scale=(1.2, 0.6, 0.7))   # 黒いリボン
            P.prim("cyl", mag, f"mitten_ant{sx}", head, (sx * hr * 0.6, -0.02, hr * 2.6), (0, sx * 0.35, 0), radius=0.012, depth=hr * 1.4, vertices=6)   # 触角
            P.prim("sphere", mag, f"mitten_anttip{sx}", head, (sx * hr * 0.85, -0.02, hr * 3.25), radius=0.03)
            P.prim("sphere", wing, f"mitten_wing{sx}", root, (sx * 0.28, -r - 0.05, body_h * 0.62), (0.25, sx * 0.55, 0), radius=1.0, scale=(0.14, 0.03, 0.42))   # 透けた羽根
            mid = bpy.data.objects.new(f"MIDARM_mitten{sx}", None)   # 中の腕（腕は4本）
            bpy.context.collection.objects.link(mid)
            mid.parent = root
            mid.location = (sx * (r + 0.03), 0.02, body_h * 0.55)
            mid.rotation_euler = (0, sx * 0.7, 0)
            P.prim("cyl", shell, f"mitten_midarm{sx}", mid, (0, 0, -0.14), radius=0.035, depth=0.28)
            P.prim("sphere", fur, f"mitten_midhand{sx}", mid, (0, 0, -0.3), radius=0.05)
        P.prim("cyl", fur, "mitten_collar", root, (0, 0, body_h * 0.98), radius=r * 1.35, depth=0.08)   # 黄色の毛の襟
        P.prim("sphere", belly, "mitten_belly", root, (0, r * 0.62, body_h * 0.45), radius=1.0, scale=(r * 0.75, r * 0.45, body_h * 0.4))   # 腹
    elif who == "aromani-gel":
        horn = P.make_mat("gel_horn", (0.72, 0.55, 0.18), rough=0.3)
        ear = P.make_mat("gel_ear", (0.45, 0.15, 0.55))
        green = P.make_mat("gel_green", (0.45, 0.62, 0.15), rough=0.2, emit=0.15)
        eye = P.make_mat("gel_eye", (0.6, 0.06, 0.04), emit=0.8)
        dark = P.make_mat("gel_mouth", (0.08, 0.05, 0.02))
        P.prim("sphere", P.make_mat("gel_body", col, rough=0.2), "gel_belly", root, (0, 0, body_h * 0.45), radius=1.0, scale=(r * 1.15, r * 1.05, body_h * 0.55))   # 洋なし形の胴
        for sx in (-1, 1):
            P.prim("cone", horn, f"gel_horn{sx}", head, (sx * hr * 0.5, 0, hr * 1.9), (0, sx * 0.3, 0), radius1=0.08, radius2=0.0, depth=0.35)
            P.prim("sphere", ear, f"gel_ear{sx}", head, (sx * hr * 1.15, -0.05, hr * 1.2), (0, sx * 0.9, 0), radius=1.0, scale=(0.08, 0.06, 0.26))
            P.prim("sphere", eye, f"gel_eye{sx}", head, (sx * hr * 0.35, hr * 0.92, hr * 1.25), radius=0.05)
            P.prim("sphere", green, f"gel_hand{sx}", arms[sx], (0, 0, -body_h * 0.6), radius=0.22, scale=(1, 1, 1.4))
        P.prim("sphere", dark, "gel_mouth", root, (0, r * 1.05, body_h * 0.62), radius=1.0, scale=(r * 0.55, 0.08, 0.2))
        P.prim("sphere", green, "gel_puddle", root, (0, 0, 0.04), radius=1.0, scale=(r * 1.75, r * 1.6, 0.12))   # 足元のゲル
    elif who == "heinz":
        P.prim("sphere", P.make_mat("heinz_hair", (0.95, 0.82, 0.45)), "heinz_hair", head, (0, -0.02, hr * 1.25), radius=hr * 1.08, scale=(1, 1.05, 0.75))
        P.prim("cyl", P.make_mat("heinz_gold", (0.85, 0.66, 0.25)), "heinz_trim", root, (0, 0, body_h * 0.97), radius=r * 1.05, depth=0.04)
    elif who == "heinz-carriage":
        _hide_default(root)
        build_carriage(P, root)
    elif who == "heavy-fruit":
        _hide_default(root)
        P.prim("sphere", P.make_mat("fruit_m", (0.78, 0.45, 0.18)), "fruit", root, (0, 0, 0.16), radius=0.17, scale=(1, 1, 0.9))
        P.prim("cyl", P.make_mat("fruit_stem", (0.3, 0.42, 0.15)), "fruit_stem", root, (0, 0, 0.32), radius=0.02, depth=0.08)
    elif who == "jade-fruit":   # 翠ノ実: 手のひらに乗る大きさ（直径約 10cm）の、内から光る緑の実
        _hide_default(root)
        P.prim("sphere", P.make_mat("jade_fruit", (0.42, 0.95, 0.5), rough=0.35, emit=1.8), "jade", root, (0, 0, 0.05), radius=0.05, scale=(1, 1, 0.92))
        P.prim("cyl", P.make_mat("jade_stem", (0.32, 0.22, 0.1)), "jade_stem", root, (0, 0, 0.105), radius=0.005, depth=0.03)
        P.prim("sphere", P.make_mat("jade_leaf", (0.18, 0.48, 0.2)), "jade_leaf", root, (0.022, 0, 0.112), radius=1.0, scale=(0.028, 0.012, 0.005))
    elif who == "perfume":
        _hide_default(root)
        glass = P.beam_mat("perfume_glass", (0.85, 0.6, 0.95), strength=0.6, alpha=0.4)
        P.prim("sphere", glass, "perfume", root, (0, 0, 0.06), radius=0.05, scale=(1, 1, 1.2))
        P.prim("cyl", P.make_mat("perfume_cap", (0.85, 0.66, 0.25)), "perfume_cap", root, (0, 0, 0.13), radius=0.015, depth=0.04)


def build_carriage(P, root):
    """紺と金の箱馬車（長さ約 4.4m）と白い馬2頭（前、+Y）。人形の正面（+Y）が馬の向き。"""
    navy = P.make_mat("coach_navy", (0.1, 0.17, 0.42), rough=0.35)
    gold = P.make_mat("coach_gold", (0.88, 0.68, 0.25), rough=0.3)
    white = P.make_mat("coach_white", (0.95, 0.95, 0.96))
    curtain = P.make_mat("coach_curtain", (0.92, 0.92, 0.95))
    lamp = P.make_mat("coach_lamp", (1.0, 0.85, 0.5), emit=1.5)
    guard = P.make_mat("coach_guard", (0.62, 0.64, 0.7), rough=0.3)
    cab_y = -1.4   # 箱の中心（馬の後ろ）
    P.prim("cube", navy, "coach_box", root, (0, cab_y, 1.55), size=2.0, scale=(0.85, 1.1, 0.75))   # 箱（座面の高さ 1.0）
    P.prim("cube", navy, "coach_roof", root, (0, cab_y, 2.38), size=2.0, scale=(0.9, 1.18, 0.08))
    P.prim("cube", gold, "coach_trim", root, (0, cab_y, 2.3), size=2.0, scale=(0.92, 1.2, 0.03))
    P.prim("cone", gold, "coach_crown", root, (0, cab_y, 2.62), radius1=0.18, radius2=0.08, depth=0.35)
    for sx in (-1, 1):
        P.prim("cube", curtain, f"coach_win{sx}", root, (sx * 0.86, cab_y, 1.75), size=2.0, scale=(0.02, 0.7, 0.32))   # 窓（白いカーテン）
        P.prim("sphere", gold, f"coach_emblem{sx}", root, (sx * 0.87, cab_y, 1.18), radius=0.14, scale=(0.2, 1, 1))
        P.prim("sphere", lamp, f"coach_lamp{sx}", root, (sx * 0.95, cab_y + 1.15, 2.2), radius=0.09, scale=(1, 1, 1.4))
        for wy, wr in ((cab_y - 0.95, 0.75), (cab_y + 1.0, 0.55)):   # 後ろの大きな車輪と前の小さな車輪
            w = build_wheel(P, f"coach_wheel{sx}{wy:.1f}", sx * 1.0, wy, wr, 90, 90, wr, parent=root, mats=(navy, gold))
            w.rotation_euler = (0, 0, 0)
            w.location = (sx * 1.0, wy, wr)
    P.prim("cube", navy, "coach_seat", root, (0, cab_y + 1.45, 1.35), size=2.0, scale=(0.6, 0.3, 0.12))   # 御者台
    P.prim("cyl", guard, "coach_guard_body", root, (0, cab_y + 1.45, 1.75), radius=0.16, depth=0.7)   # 御者の兵士
    P.prim("sphere", guard, "coach_guard_head", root, (0, cab_y + 1.45, 2.22), radius=0.13)
    for hx in (-0.45, 0.45):   # 白い馬2頭
        P.prim("sphere", white, f"horse_body{hx}", root, (hx, 1.6, 1.25), radius=1.0, scale=(0.3, 0.85, 0.38))
        P.prim("cyl", white, f"horse_neck{hx}", root, (hx, 2.35, 1.65), (-0.6, 0, 0), radius=0.13, depth=0.7)
        P.prim("sphere", white, f"horse_head{hx}", root, (hx, 2.65, 1.95), radius=1.0, scale=(0.12, 0.3, 0.14))
        P.prim("cube", navy, f"horse_cloth{hx}", root, (hx, 1.6, 1.5), size=2.0, scale=(0.32, 0.35, 0.12))
        for lx, ly in ((-0.15, 1.05), (0.15, 1.05), (-0.15, 2.15), (0.15, 2.15)):
            P.prim("cyl", white, f"horse_leg{hx}{lx}{ly}", root, (hx + lx, ly, 0.5), radius=0.05, depth=1.0)
    P.prim("cyl", gold, "coach_pole", root, (0, 0.6, 1.0), (math.pi / 2, 0, 0), radius=0.04, depth=2.6)
