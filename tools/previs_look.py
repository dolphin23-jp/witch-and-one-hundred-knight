#!/usr/bin/env python3
"""下絵の絵作り（2026-11-06 の下絵のアップグレード）。Workbench で、アニメの絵コンテに近い見た目にする。

これまでの下絵は Cycles（CPU）で、1コマ約6秒（854x480）かかり、ざらついて暗かった。
EGL（Mesa のソフトウェアの OpenGL）を入れると Workbench が動き、1コマ約0.3秒で描ける（2026-11-06 に確かめた。EEVEE は 1コマ20秒超で使えない）。
  apt-get install -y libegl1 libegl-mesa0      # セッションごとに（setup/ の環境のスクリプトにも書く）

見た目:
  - 色は材質の色そのもの（Workbench の MATERIAL）。光る物（発光の材質）は明るいまま、ほかは場面の明るさで暗くする（夜・洞窟の雰囲気）
  - 太陽の向きから落ちる影（show_shadows）。アニメの影色のように、はっきりした影のかたまり
  - 輪郭線（object outline）と、くぼみの線（cavity）。人形の形が背景から浮く
  - 半透明（光の柱・霧・瘴気・羽）は材質の alpha
previs.py の --engine workbench（既定）で使う。--engine cycles で前の描き方に戻せる。
"""
import math

import bpy
from mathutils import Vector


def _socket(node, *names):
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    return None


def material_look(m):
    """材質から (色 linear RGB, alpha, 光るか) を読む。make_mat・beam_mat・swirl_mat の形に合わせる。"""
    col, alpha, glow = (0.6, 0.6, 0.6), 1.0, 0.0
    if not m.use_nodes or not m.node_tree:
        c = tuple(m.diffuse_color)
        return c[:3], c[3], 0.0
    nt = m.node_tree
    pr = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    em = next((n for n in nt.nodes if n.type == "EMISSION"), None)
    mix = next((n for n in nt.nodes if n.type == "MIX_SHADER"), None)
    ramp = next((n for n in nt.nodes if n.type == "VALTORGB"), None)
    if pr is not None:
        col = tuple(pr.inputs["Base Color"].default_value)[:3]
        es = _socket(pr, "Emission Strength")
        glow = float(es.default_value) if es is not None else 0.0
        if glow > 0:
            ec = _socket(pr, "Emission Color", "Emission")
            if ec is not None:
                col = tuple(ec.default_value)[:3]
        a = _socket(pr, "Alpha")
        if a is not None:
            alpha = float(a.default_value)
    elif em is not None:
        col = tuple(em.inputs["Color"].default_value)[:3]
        glow = max(0.5, float(em.inputs["Strength"].default_value))
        if ramp is not None:   # 渦の地面: 色の帯の明るい側の色
            els = ramp.color_ramp.elements
            col = tuple(els[max(0, len(els) - 2)].color)[:3]
        if mix is not None:
            alpha = float(mix.inputs["Fac"].default_value)
    return col, alpha, glow


def scene_level(lit):
    """場面の明るさ（0.35〜1.0）と色み。光の設定（layout の lighting）から。"""
    se = float(lit.get("sun_energy", 3.0))
    ss = float(lit.get("sky_strength", 1.0))
    level = max(0.38, min(1.0, 0.42 + 0.16 * min(se, 3.5) + 0.08 * (ss - 0.6)))
    if "level" in lit:   # layout の lighting に wb_level を書けば、それを使う
        level = float(lit["level"])
    sc = lit.get("sun_color") or [255, 240, 220]
    tint = tuple(0.72 + 0.28 * (v / 255) for v in sc)
    return level, tint


def apply(scene, lit, sun_obj=None, outline=True, aa="FXAA"):
    """Workbench の設定と、材質の色の書き換え。場面を作り終えた後、描く前に1回呼ぶ。"""
    scene.render.engine = "BLENDER_WORKBENCH"
    disp = scene.display
    sh = disp.shading
    sh.light = "STUDIO"
    try:
        sh.studio_light = "outdoor.sl"
    except Exception:
        pass
    sh.color_type = "MATERIAL"
    sh.show_shadows = True
    sh.shadow_intensity = 0.42
    sh.show_object_outline = bool(outline)
    sh.object_outline_color = (0.03, 0.025, 0.04)
    sh.show_cavity = True
    sh.cavity_type = "WORLD"
    sh.cavity_ridge_factor = 0.6
    sh.cavity_valley_factor = 1.0
    sh.show_specular_highlight = False
    sh.background_type = "WORLD"
    try:
        disp.render_aa = aa
    except Exception:
        pass
    level, tint = scene_level(lit or {})
    # 影の向き: 太陽（LGT_sun）の向き。Workbench の light_direction は光の来る向き
    sun = sun_obj or bpy.data.objects.get("LGT_sun")
    if sun is not None:
        d = sun.matrix_world.to_quaternion() @ Vector((0.0, 0.0, 1.0))
        if d.z < 0.25:   # 低すぎる太陽は影が長すぎるので、少し持ち上げる
            d.z = 0.25
        d.normalize()
        disp.light_direction = (d.x, d.y, d.z)
    disp.shadow_shift = 0.08
    disp.shadow_focus = 0.9
    for m in bpy.data.materials:
        col, alpha, glow = material_look(m)
        if glow > 0.05:   # 光る物は暗くしない（夜・洞窟で目立つ）
            k = 1.0 + min(0.6, 0.15 * glow)
            c = tuple(min(1.0, v * k) for v in col)
        else:
            c = tuple(v * level * t for v, t in zip(col, tint))
        m.diffuse_color = (*c, alpha)
        if alpha < 0.999:
            try:
                m.blend_method = "BLEND"
            except Exception:
                pass
    w = scene.world
    if w is not None:
        sky = (0.4, 0.5, 0.7)
        if w.use_nodes and "Background" in w.node_tree.nodes:
            bg = w.node_tree.nodes["Background"]
            sky = tuple(bg.inputs["Color"].default_value)[:3]
        w.color = tuple(min(1.0, v * (0.75 + 0.35 * level)) for v in sky)
