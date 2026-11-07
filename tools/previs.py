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
import previs_face  # noqa: E402  顔と会話の芝居（2026-11-07）
FACES = {}   # 人物 → 顔の部品（previs_face.add_face）
import previs_council  # noqa: E402  議会室（6章後半 回3、A28〜A32）の形・人形・効果（並行作業と重ならないよう別のファイル、2026-11-05）
import previs_ch7  # noqa: E402  7章（A33〜）の人形（ミトン・アロマニ・ジェル・馬車）とチンクエの道具（2026-11-06）
import previs_rig  # noqa: E402  関節のある人形と姿勢の補間（2026-11-06 の下絵のアップグレード）
import previs_fx  # noqa: E402  戦いの効果（閃光・衝撃・破片・土煙・集中線・剣の軌跡）と、止め・スロー（2026-11-06）
FXCTX = {}   # 効果がその時刻の場面を知るための道具（render_cut が入れる）
RENDER = {"engine": "workbench", "rig": True, "face": True, "dof": True, "grade": True, "ambient": True}   # face・dof・grade・ambient は 2026-11-07
# 場所の空気（2026-11-07、previs_fx の motes）。(layout の id, 光の名前) か layout の id で選ぶ。None で出さない。カットの ambient: false で消す、{style: ...} で替える
AMBIENT = {
    "witch-house": "dust", "living-room": "dust", "st-dolce": "dust", "underground-cavern": "dust", "beast-village": "dust",
    "az-brocken": "dust", "council-room": "dust", "witch-house-front": "dust", "swamp-hill": "miasma",
    "marika-forest": "fireflies", "hekkahenne-forest": "fireflies", "cinque-forest": "leaves",
    "rift-void": "sparkles", "witch-city": "sparkles", "tettara-desert": "sand", "tettara-dunes": "sand",
    ("council-room", "council_ruin"): "miasma", ("cinque-forest", "marsh"): "miasma", ("az-brocken", "night_mtn"): None,
    ("witch-city", "void_gate"): "sparkles",
}
AMBIENT_BOX = {"dust": [7.0, 7.0, 3.0], "fireflies": [10.0, 10.0, 3.5], "leaves": [10.0, 10.0, 5.0], "sand": [12.0, 8.0, 2.5],
               "miasma": [10.0, 10.0, 4.0], "sparkles": [12.0, 12.0, 6.0], "snow": [10.0, 10.0, 5.0], "embers": [8.0, 8.0, 4.0]}   # 描き方（2026-11-06 の下絵のアップグレード）。workbench（既定、tools/previs_look.py）か cycles（前の描き方）
EXT = (previs_council, previs_ch7)   # 場面ごとの形の追加（build_extra・decorate を順に呼ぶ）

import bpy  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402


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
        _sp = st.dir / "shots" / f"{cut}.yaml"   # カットの頭の場面の時刻（台詞・口の動き・聞き手の視線に使う、2026-11-07）
        self.shot = (yaml.safe_load(_sp.read_text(encoding="utf-8")) or {}) if _sp.exists() else {}
        self.tc0 = float(self.shot.get("tc", 0.0))
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
        self.creatures = {n: motion.CreatureTrack(motion.creature_spec(st.bk["cuts"], cut, n), st.place)
                          for n in (self.c.get("creatures") or {})}

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

    def ride(self, who, t):
        """キーの on（生き物の体の上に乗る、2026-11-06）: その時刻の体の上の位置 (x, y, 高さ)。乗っていなければ None。
        on: {c: 生き物, s: 頭からの道のり m, up: 体の上の面からの高さの倍率（1 で背中の上）, dz: さらに上げる m}。
        on のあるキーとないキーの間は、地面の位置と体の上の位置を混ぜる（跳び乗る・跳び降りる。arc で弧）。"""
        tr = self.tracks.get(who)
        if tr is None or not self.creatures:
            return None
        a, b, u = motion.segment(tr.k, t)
        oa, ob = a.get("on"), b.get("on")
        if not oa and not ob:
            return None

        def body_pt(on):
            ct = self.creatures[on["c"]]
            s_ = float(on.get("s", 0.0))
            S = ct.s_of(t)
            pt = ct.point_at_s(S - s_)
            f = min(1.0, max(0.0, s_ / ct.len))
            r = ct.r * (1.0 - 0.55 * f) * (1.15 if s_ < ct.len / max(1, ct.segs - 1) else 1.0)
            if on.get("off") is not None:   # 体の向きに沿ったずらし [横, 前, 背]（その所の太さに対する割合）。目の横にしがみつくなど
                sp = ct.len / max(1, ct.segs - 1)   # 体の節の間隔（目の置き方 pose_creature と同じ向きにする）
                a_, b_ = ct.point_at_s(S - s_), ct.point_at_s(S - s_ - sp)
                # 向きは Blender の座標（y 北が正）で、pose_creature の目の置き方と同じ式で求め、layout の座標（y 南が正）へ戻す
                dB = Vector((a_[0] - b_[0], -(a_[1] - b_[1]), a_[2] - b_[2]))
                dB = dB.normalized() if dB.length > 1e-6 else Vector((0, 1, 0))
                sB = dB.cross(Vector((0, 0, 1)))
                sB = sB.normalized() if sB.length > 1e-3 else Vector((1, 0, 0))
                uB = sB.cross(dB).normalized()
                o = on["off"]
                v = sB * float(o[0]) + dB * float(o[1]) + uB * float(o[2])
                return (pt[0] + r * v.x, pt[1] - r * v.y, pt[2] + r * v.z + float(on.get("dz", 0.0)))
            return (pt[0], pt[1], pt[2] + r * float(on.get("up", 1.0)) + float(on.get("dz", 0.0)))
        if oa and ob and oa.get("c") == ob.get("c"):
            on = {**ob, "s": lerp(float(oa.get("s", 0.0)), float(ob.get("s", 0.0)), u),
                  "up": lerp(float(oa.get("up", 1.0)), float(ob.get("up", 1.0)), u), "dz": lerp(float(oa.get("dz", 0.0)), float(ob.get("dz", 0.0)), u)}
            if oa.get("off") is not None and ob.get("off") is not None:
                on["off"] = [lerp(float(x), float(y), u) for x, y in zip(oa["off"], ob["off"])]
            return body_pt(on)
        pa = body_pt(oa) if oa else (a["at"][0], a["at"][1], float(a.get("z", 0.0)))
        pb = body_pt(ob) if ob else (b["at"][0], b["at"][1], float(b.get("z", 0.0)))
        z = lerp(pa[2], pb[2], u) + (float(b["arc"]) * math.sin(math.pi * u) if b.get("arc") and a is not b else 0.0)
        return (lerp(pa[0], pb[0], u), lerp(pa[1], pb[1], u), z)

    def position(self, who, t):
        if who in self.tracks:
            rd = self.ride(who, t)
            if rd is not None:
                return rd[:2]
            return self.tracks[who].at(t)[0]
        if self.start_pos[who] == self.end_pos[who] and not (self.c.get("move") or {}).get(who, {}).get("via"):
            return tuple(self.start_pos[who])
        return catmull(self._path(who), self.ease(who, t))

    def positions(self, t):
        return {w: self.position(w, t) for w in self.people}

    def height(self, who, t):
        """地面からの高さ m（跳ぶ・埋まる・生き物に乗る）。キーのない人物は 0。"""
        if who in self.tracks:
            rd = self.ride(who, t)
            if rd is not None:
                return rd[2] - motion.ground_z(self.st.lay, rd[0], rd[1])
            return self.tracks[who].at(t)[1]
        return 0.0

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
                if isinstance(f, str) and f in self.creatures:   # 生き物の頭を見る（2026-11-06）
                    hx, hy, _ = self.creatures[f].head(t)
                    return staging.bearing_of((hx - pos[who][0], hy - pos[who][1]))
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
            return self._listen_offset(who, t, pos, body)
        g1 = (self.c.get("gaze_end") or {}).get(who, g0)
        b0 = self.st.face_of(g0, who, pos)
        b1 = self.st.face_of(g1, who, pos)
        b = lerp_bearing(b0, b1, smooth(t / max(self.sec, 1e-6)))
        return max(-80.0, min(80.0, staging.ang_diff(b, body)))

    def _listen_offset(self, who, t, pos, body):
        """gaze のない人は、ほかの人が話している間、話し手のほうへ頭を向ける（±70°まで。話し手が替わると約0.3秒で向き直る、2026-11-07）。"""
        if not RENDER.get("face", True):
            return 0.0
        acc = 0.0
        steps = (0.0, 0.12, 0.24, 0.36)
        for dt in steps:
            sp = previs_face.speaker_at(self.st.dir, self.tc0 + max(0.0, t - dt), self.people)
            if sp and sp != who and sp in pos and who in pos:
                b = staging.bearing_of((pos[sp][0] - pos[who][0], pos[sp][1] - pos[who][1]))
                acc += max(-70.0, min(70.0, staging.ang_diff(b, body)))
        return acc / len(steps)

    def roll(self, t):
        """画面の傾き（度）。cam_keys の roll を時刻で補間する（なければ 0）。"""
        if not self.camtrack:
            return 0.0
        a, b, u = motion.segment(self.camtrack.k, t)
        return lerp(float(a.get("roll", 0.0)), float(b.get("roll", 0.0)), u)

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
    "scarecocco": {"r": 0.17, "hr": 0.11, "arm": 0.035},   # ニワトリの魔獣（高さ約60cm）。丸い胴の小さな人形で表す
    # 夜会編 回2（A14〜A17）
    "purple-peel": {"r": 0.17, "hr": 0.13, "arm": 0.055},
    "lily": {"r": 0.14, "hr": 0.12, "arm": 0.04},
    "rabi": {"r": 0.14, "hr": 0.12, "arm": 0.04},
    "usamaru": {"r": 0.13, "hr": 0.14, "arm": 0.04},    # ウサギ人形（ラヴィのそばに浮かぶ）
    "teressa": {"r": 0.14, "hr": 0.12, "arm": 0.04},
    "berda": {"r": 0.15, "hr": 0.125, "arm": 0.045},
    "apprentice-a": {"r": 0.12, "hr": 0.11, "arm": 0.035},
    "apprentice-b": {"r": 0.12, "hr": 0.11, "arm": 0.035},
    "apprentice-c": {"r": 0.12, "hr": 0.11, "arm": 0.035},
    "apprentice-d": {"r": 0.12, "hr": 0.11, "arm": 0.035},
    "karakuri-doll": {"r": 0.5, "hr": 0.3, "arm": 0.07},   # 守護者のからくり人形（卵形の胴に骸骨の顔と笠。設定画 karakuri-doll_sheet_01）
    # 6章後半 回1（A18〜A22）
    "marny": {"r": 0.05, "hr": 0.07, "arm": 0.018},          # 沼の妖精マーニィ（小さな体と大きな緑の蝶の羽。浮いている。設定画 marny_sheet_01）
    "forest-servant": {"r": 0.23, "hr": 0.19, "arm": 0.085},  # 森の魔女のシモベ（設定画 forest-servant_sheet_01: ずんぐりした背 約1.25m、白い丸い頭、赤い上着、2本の銅の管の角）
    "marika-mouse": {"r": 0.04, "hr": 0.04, "arm": 0.01},    # マーリカのネズミ（白い小さなネズミ）
    # 6章後半 回2（A23〜A27）
    "night-train": {"r": 1.3, "hr": 0.5, "arm": 0.0},         # 夜光列車（設定画 night-train_sheet_01: 獅子の顔の機関車。両側に山羊の頭、後ろに蛇。長さ約8m）
    "blind-witch": {"r": 0.2, "hr": 0.13, "arm": 0.045},      # 盲いた魔女＝ヴェライエ（設定画 blind-witch_sheet_01: 腰の曲がった小柄な老婆、大きなつばの紺のとんがり帽子で目が隠れる、紺のローブ、ホウキの杖）
    "guide-witch": {"r": 0.14, "hr": 0.12, "arm": 0.04},      # 魔女都の案内魔女（設定画 guide-witch_sheet_01: 紺の長いコートと短いケープ、銀の縁、銀髪のまとめ髪、紺のとんがり帽子、ランタンの杖）
}
WITCHES = ("purple-peel", "lily", "rabi", "teressa", "berda", "apprentice-a", "apprentice-b", "apprentice-c", "apprentice-d", "blind-witch", "guide-witch")
SHAPE_DEFAULT = {"r": 0.16, "hr": 0.13, "arm": 0.05}
for _m in EXT:   # 回3 の人形（リューベンス・ネザリア・巨大メタリカ・沼を浴びたテレッサ）、7章の人形
    SHAPE.update(_m.SHAPE)


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


_PRIM_MESH = {}   # 形（種類と大きさ）ごとの元のメッシュ。同じ形は複製して作る（bpy.ops の追加は物が増えるほど遅い。森の 725 個で 26 秒、2026-11-06）


def _prim_mesh(kind, kw):
    key = (kind, tuple(sorted((k, v if not isinstance(v, list) else tuple(v)) for k, v in kw.items() if k not in ("location", "rotation"))))
    me = _PRIM_MESH.get(key)
    if me is None:
        args = {k: v for k, v in kw.items() if k not in ("location", "rotation")}
        if kind == "cyl":
            bpy.ops.mesh.primitive_cylinder_add(**{"vertices": 28, **args})
        elif kind == "cone":
            bpy.ops.mesh.primitive_cone_add(**{"vertices": 28, **args})
        elif kind == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(segments=28, ring_count=14, **args)
        elif kind == "cube":
            bpy.ops.mesh.primitive_cube_add(**args)
        tmp = bpy.context.object
        me = tmp.data
        smooth_shade(tmp)
        me.use_fake_user = True
        bpy.data.objects.remove(tmp, do_unlink=True)
        _PRIM_MESH[key] = me
    return me


def prim(kind, mat, name, parent=None, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1), **kw):
    me = _prim_mesh(kind, kw).copy()
    me.use_fake_user = False
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    vl = bpy.context.view_layer
    for ob in list(vl.objects.selected):   # bpy.ops の追加と同じく、新しい物だけを選んで、作業中の物にする
        if ob is not None:
            ob.select_set(False)
    o.select_set(True)
    vl.objects.active = o
    o.data.materials.append(mat)
    o.parent = parent
    o.location = loc
    o.rotation_euler = rot
    o.scale = scale
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
            "bed": (0.7, 0.3, 0.2), "hearth": (0.45, 0.42, 0.38), "straw": (0.85, 0.72, 0.38),
            "rockcliff": (0.46, 0.4, 0.36)}
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
    o.visible_camera = False   # 光の玉そのものは映さない（大きな補助光が円盤に見えて手前の物を隠した。A07-003）
    return o


def build_span(name, pr):
    """from→to に沿った壁・柵・梁（高さ h、厚さ t、下端 z）。"""
    (x0, y0), (x1, y1) = pr["from"], pr["to"]
    L = math.dist((x0, y0), (x1, y1))
    rot = -math.degrees(math.atan2(-(y1 - y0), x1 - x0))   # 東西に長い箱を、from→to の向きに回す（box の rot は上から見て時計回り）
    if name.startswith("rail"):   # 線路: 2本のレールと枕木（ヘッカヘンネの隠し森の駅、A20〜A23）。gauge はレールの間隔
        steel = make_mat(f"{name}_steel", (0.32, 0.3, 0.3), rough=0.4)
        g = pr.get("gauge", 1.2)
        nx, ny = -(y1 - y0) / L, (x1 - x0) / L   # from→to に直交する向き
        for sg in (-1, 1):
            box(steel, f"ENV_{name}_r{sg}", (x0 + x1) / 2 + nx * sg * g / 2, (y0 + y1) / 2 + ny * sg * g / 2, L, 0.08, 0.12, pr.get("z", 0.0) + 0.1, rot)
        n = max(2, int(L / 0.9))
        for k in range(n):
            u = (k + 0.5) / n
            box(mat_of("wood"), f"ENV_{name}_tie{k}", x0 + (x1 - x0) * u, y0 + (y1 - y0) * u, 0.25, g + 0.6, 0.1, pr.get("z", 0.0), rot + 90)
        return
    key = "fence" if name.startswith("fence") else ("wood" if name.startswith(("beam", "plank")) else "stonewall")
    t = pr.get("t", 0.08 if key == "fence" else 0.4)
    if key == "fence":   # 柵は横木2本と杭
        for k, zz in enumerate((0.45, 0.85)):
            box(mat_of(key), f"ENV_{name}_rail{k}", (x0 + x1) / 2, (y0 + y1) / 2, L, t, 0.08, zz * pr.get("h", 1.0), rot)
        n = max(2, int(L / 1.5) + 1)
        for k in range(n):
            u = k / (n - 1)
            box(mat_of(key), f"ENV_{name}_post{k}", x0 + (x1 - x0) * u, y0 + (y1 - y0) * u, 0.12, 0.12, pr.get("h", 1.0))
        return
    m = make_mat(f"{name}_m", tuple(v / 255 for v in pr["color"]), emit=pr.get("glow", 0.0)) if pr.get("color") else mat_of(key)   # 色と光（議事塔の金色の門、2026-11-05）
    box(m, f"ENV_{name}", (x0 + x1) / 2, (y0 + y1) / 2, L, t, pr.get("h", 1.5), pr.get("z", 0.0), rot)


def build_room(name, pr, door_closed=False):
    """中に入れる部屋: x・y の範囲の4つの壁（openings で扉・窓・出入口を開ける）、床、屋根。walls で作る壁を選べる。
    door_closed（カットの door_closed: true）のときは、ja が「扉」の開口に閉じた扉の板を入れる（A06 の見直し、2026-11-01）。"""
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
            if door_closed and o.get("ja") == "扉":   # 閉じた扉の板（濃い木の色）
                dm = make_mat("door_leaf", (0.42, 0.24, 0.12))
                if horiz:
                    box(dm, f"ENV_{name}_{side}_door", o["c"], fixed, o["w"] - 0.04, 0.08, o["h"], 0.0)
                else:
                    box(dm, f"ENV_{name}_{side}_door", fixed, o["c"], 0.08, o["w"] - 0.04, o["h"], 0.0)
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


def bool_diff(obj, cutter):
    """obj から cutter の形をくり抜く（cutter は消す）。"""
    mod = obj.modifiers.new("cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def beam_mat(name, rgb, strength=0.6, alpha=0.85):
    """光の柱: 半分透ける発光（下絵の目安）。"""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    mix = nt.nodes.new("ShaderNodeMixShader")
    em = nt.nodes.new("ShaderNodeEmission")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    em.inputs["Color"].default_value = (*linear(rgb), 1.0)
    em.inputs["Strength"].default_value = strength
    mix.inputs["Fac"].default_value = alpha
    nt.links.new(em.outputs[0], mix.inputs[1])
    nt.links.new(tr.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    return m


def build_cave(name, pr, x, y):
    """地下空洞・井戸の筒・地上の円い地面・井戸の縁・光の柱・岩の柱・横穴・苔・砂の滝・ピラー（A07・A08）。作ったら True。"""
    rockc = make_mat(f"{name}_rock", (0.42, 0.24, 0.15))
    if name.startswith("cave"):
        dome = prim("sphere", rockc, f"ENV_{name}", loc=bl((x, y), 0.0), radius=1.0, scale=(pr["rx"], pr["ry"], pr["h"]))
        if pr.get("hole"):
            # 球は中身の詰まった形として扱われ、くり抜くと穴の底（z h-3）にふたができて床が見えなかった（A07-003）。
            # 厚みのある殻にしてからくり抜く（厚みは物体の大きさで伸びるので小さめ。東西 約1.2m）
            sol = dome.modifiers.new("shell", "SOLIDIFY")
            sol.thickness, sol.offset = 0.06, 1.0
            bpy.context.view_layer.objects.active = dome
            bpy.ops.object.modifier_apply(modifier=sol.name)
            bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=pr["hole"], depth=6.0, location=bl((x, y), pr["h"]))
            bool_diff(dome, bpy.context.object)
        return True
    if name.startswith("shaft"):
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=pr.get("r", 0.65), depth=pr["z1"] - pr["z0"], end_fill_type="NOTHING",
                                            location=bl((x, y), (pr["z0"] + pr["z1"]) / 2))
        o = bpy.context.object
        o.name = f"ENV_{name}"
        o.data.materials.append(make_mat(f"{name}_stone", (0.55, 0.52, 0.46)))
        return True
    if name.startswith("plate"):
        disc = prim("cyl", make_mat(f"{name}_sand", (0.78, 0.62, 0.36)), f"ENV_{name}", loc=bl((x, y), pr["z"] - 0.1), radius=pr["r"], depth=0.2)
        if pr.get("hole"):
            bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=pr["hole"], depth=2.0, location=bl((x, y), pr["z"]))
            bool_diff(disc, bpy.context.object)
        return True
    if name.startswith("rim"):
        ring = prim("cyl", make_mat(f"{name}_stone", (0.62, 0.6, 0.54)), f"ENV_{name}", loc=bl((x, y), pr["z"] + pr.get("h", 0.8) / 2), radius=pr["r"], depth=pr.get("h", 0.8))
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=pr.get("inner", 0.65), depth=3.0, location=bl((x, y), pr["z"]))
        bool_diff(ring, bpy.context.object)
        return True
    if name.startswith("beam"):
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=pr.get("r", 0.7), depth=pr["z1"] - pr["z0"], end_fill_type="NOTHING",
                                            location=bl((x, y), (pr["z0"] + pr["z1"]) / 2))
        o = bpy.context.object
        o.name = f"ENV_{name}"
        o.data.materials.append(beam_mat(f"{name}_m", (0.75, 0.85, 1.0), strength=0.25, alpha=0.95))
        return True
    if name.startswith("column"):
        cm = make_mat(f"{name}_m", tuple(v / 255 for v in pr["color"])) if pr.get("color") else rockc
        prim("cyl", cm, f"ENV_{name}", loc=bl((x, y), pr["h"] / 2), radius=pr.get("r", 2.0), depth=pr["h"])
        return True
    if name.startswith("tunnel"):   # 横穴: 壁に貼った暗い円（向き face は穴が口を開ける方位）
        dark = make_mat(f"{name}_dark", (0.03, 0.02, 0.02))
        prim("cyl", dark, f"ENV_{name}", loc=bl((x, y), pr.get("z", 2.5)), rot=(math.pi / 2, 0, -math.radians(pr.get("face", 0))), radius=pr.get("r", 2.0), depth=0.6)
        return True
    if name.startswith("moss"):
        g = make_mat(f"{name}_m", (0.45, 1.0, 0.35), emit=2.0)
        prim("sphere", g, f"ENV_{name}", loc=bl((x, y), pr.get("z", 3.0)), radius=0.45, scale=(1, 1, 0.6))
        point_light(f"LGT_{name}", x, y, pr.get("z", 3.0), pr.get("energy", 15), (0.45, 1.0, 0.4), 0.5)
        return True
    if name.startswith("sandfall"):
        sm = make_mat(f"{name}_m", (0.85, 0.7, 0.45))
        prim("cyl", sm, f"ENV_{name}", loc=bl((x, y), pr.get("z1", 14.0) / 2), radius=0.06, depth=pr.get("z1", 14.0))
        return True
    if name.startswith("crystal"):   # ピラー: 縦長の結晶（glow で光る強さ）
        cm = make_mat(f"{name}_m", (0.55, 0.85, 1.0), emit=pr.get("glow", 0.0))
        prim("cyl", cm, f"ENV_{name}", loc=bl((x, y), pr.get("h", 4.0) / 2), radius=pr.get("r", 0.6), depth=pr.get("h", 4.0), vertices=6)
        prim("cone", cm, f"ENV_{name}_tip", loc=bl((x, y), pr.get("h", 4.0) + 0.5), radius1=pr.get("r", 0.6), radius2=0.0, depth=1.0, vertices=6)
        if pr.get("glow"):
            point_light(f"LGT_{name}", x, y, pr.get("h", 4.0) * 0.6, 60 * pr["glow"], (0.6, 0.85, 1.0), 0.5)
        return True
    return False


def swirl_mat(name, cols=None, strength=0.6, scale=0.22):
    """光る渦の地面（異空間、ユーザーの背景 otherspace_bg_01。2026-11-05）。歪ませた波の模様を色の帯に通して光らせる。"""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (scale, scale, scale)
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.inputs["Scale"].default_value = 1.0
    wave.inputs["Distortion"].default_value = 18.0
    wave.inputs["Detail"].default_value = 4.0
    wave.inputs["Detail Scale"].default_value = 1.6
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    cols = cols or [(0.0, (4, 3, 18)), (0.55, (16, 10, 52)), (0.75, (48, 22, 118)), (0.87, (60, 70, 200)), (0.96, (120, 160, 250))]
    els = ramp.color_ramp.elements
    while len(els) > 1:
        els.remove(els[-1])
    els[0].position, els[0].color = cols[0][0], (*linear(tuple(v / 255 for v in cols[0][1])), 1.0)
    for pos, rgb in cols[1:]:
        e = els.new(pos)
        e.color = (*linear(tuple(v / 255 for v in rgb)), 1.0)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = strength
    nt.links.new(geo.outputs["Position"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], wave.inputs["Vector"])
    nt.links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return m


def build_otherworld(name, pr, x, y, lamps):
    """ユーザーの背景画像に合わせて足した形（2026-11-05）。作ったら True。
    森の発着場（forest-station_bg_01）: sigilcircle 地面の魔法陣、sigil 光る紋の円盤、brickplat レンガのホーム、shelter 小屋、belllamp 鐘を吊るした街灯。
    異空間（otherspace_bg_01）・追憶の園（memory-garden の参考画）: gothicgate 透けたゴシックの門、deadtree 大木、hole 暗い穴、fruittree 光る実の木、
    bench ベンチ、stonering 石の輪、menhir 立石。魔女都（walpurgis_bg_01）: cathedral 尖塔の多い議事塔、spire 尖塔。"""
    rot = pr.get("rot", 0)
    z0 = pr.get("z", 0.0)
    if name.startswith("sigilcircle"):   # 地面に描いた魔法陣（石の縁、明るい石の地、赤い六芒星と中心の六角形）
        r = pr.get("r", 7.0)
        prim("cyl", make_mat(f"{name}_rim", (0.45, 0.43, 0.4)), f"ENV_{name}", loc=bl((x, y), z0 + 0.025), radius=r, depth=0.05, vertices=64)
        prim("cyl", make_mat(f"{name}_base", tuple(v / 255 for v in pr.get("color", [176, 150, 118]))), f"ENV_{name}_base",
             loc=bl((x, y), z0 + 0.03), radius=r * 0.93, depth=0.05, vertices=64)
        red = make_mat(f"{name}_red", tuple(v / 255 for v in pr.get("line", [150, 58, 40])))
        rr = r * 0.88
        for tri in (0, 1):
            for k in range(3):
                a0 = math.radians(90 + tri * 60 + k * 120)
                a1 = math.radians(90 + tri * 60 + (k + 1) * 120)
                p0 = (x + rr * math.cos(a0), y + rr * math.sin(a0))
                p1 = (x + rr * math.cos(a1), y + rr * math.sin(a1))
                L = math.dist(p0, p1)
                ang = -math.degrees(math.atan2(-(p1[1] - p0[1]), p1[0] - p0[0]))
                box(red, f"ENV_{name}_l{tri}{k}", (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, L, 0.32, 0.02, z0 + 0.055, ang)
        prim("cyl", red, f"ENV_{name}_core", loc=bl((x, y), z0 + 0.065), radius=r * 0.18, depth=0.03, vertices=6)
        return True
    if name.startswith("sigil"):   # 草の上の光る紋の円盤（橙）
        r = pr.get("r", 0.9)
        prim("cyl", make_mat(f"{name}_ring", (0.3, 0.26, 0.22)), f"ENV_{name}", loc=bl((x, y), z0 + 0.03), radius=r, depth=0.06)
        prim("cyl", make_mat(f"{name}_glow", tuple(v / 255 for v in pr.get("color", [255, 140, 40])), emit=pr.get("emit", 1.6)),
             f"ENV_{name}_in", loc=bl((x, y), z0 + 0.05), radius=r * 0.72, depth=0.04)
        return True
    if name.startswith("brickplat"):   # レンガのホーム（片方の端が丸い）。w は長さ、d は幅、rot は長い向き（上から時計回り、0 で東西）
        w, d, hh = pr.get("w", 7.0), pr.get("d", 2.6), pr.get("h", 0.25)
        brick = make_mat(f"{name}_m", tuple(v / 255 for v in pr.get("color", [128, 66, 50])))
        box(brick, f"ENV_{name}", x, y, w, d, hh, z0, rot)
        a = math.radians(rot)
        ex, ey = x - math.cos(a) * w / 2, y - math.sin(a) * w / 2   # 丸い端（rot 0 のとき西の端。長い向きは layout で (cos rot, sin rot)）
        prim("cyl", brick, f"ENV_{name}_end", loc=bl((ex, ey), z0 + hh / 2), radius=d / 2, depth=hh)
        return True
    if name.startswith("shelter"):   # 小さな小屋（木の柱4本と格子、緑の屋根）
        w, d, hh = pr.get("w", 2.0), pr.get("d", 1.6), pr.get("h", 2.4)
        wood = make_mat(f"{name}_wood", (0.36, 0.28, 0.24))
        a = math.radians(rot)
        for k, (sx, sy) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
            dx, dy = sx * (w / 2 - 0.08), sy * (d / 2 - 0.08)
            box(wood, f"ENV_{name}_p{k}", x + dx * math.cos(a) - dy * math.sin(a), y + dx * math.sin(a) + dy * math.cos(a), 0.12, 0.12, hh, z0)
        box(make_mat(f"{name}_roof", (0.2, 0.32, 0.26)), f"ENV_{name}_roof", x, y, w + 0.4, d + 0.4, 0.18, z0 + hh, rot)
        return True
    if name.startswith("belllamp"):   # 鐘を吊るした街灯（ホームの丸い端。A23 で百騎兵が鳴らす鐘）
        hh = pr.get("h", 3.2)
        iron = make_mat(f"{name}_iron", (0.16, 0.15, 0.14))
        prim("cyl", iron, f"ENV_{name}", loc=bl((x, y), z0 + hh / 2), radius=0.06, depth=hh)
        head = make_mat(f"{name}_head", (1.0, 0.85, 0.45), emit=3.0 * lamps)
        box(head, f"ENV_{name}_head", x, y, 0.26, 0.26, 0.36, z0 + hh)
        point_light(f"LGT_{name}", x, y, z0 + hh + 0.18, pr.get("energy", 30) * lamps)
        f = math.radians(pr.get("face", 90))   # 鐘を吊るす腕の向き（北 0・東 90）
        bx, by = x + math.sin(f) * 0.45, y - math.cos(f) * 0.45
        box(iron, f"ENV_{name}_arm", (x + bx) / 2, (y + by) / 2, 0.5, 0.06, 0.06, z0 + hh - 0.75, pr.get("face", 90) - 90)
        prim("cone", make_mat(f"{name}_brass", (0.62, 0.42, 0.2), rough=0.35), f"ENV_{name}_bell", loc=bl((bx, by), z0 + hh - 1.05),
             radius1=0.2, radius2=0.07, depth=0.36)
        return True
    if name.startswith("gothicgate"):   # 透けたゴシックの門（線路をふさぐ。両脇の尖塔、尖った頭、縦の格子、裾が外へ広がる）
        w, hh = pr.get("w", 19.0), pr.get("h", 17.0)
        glass = beam_mat(f"{name}_m", tuple(v / 255 for v in pr.get("color", [120, 150, 230])), strength=pr.get("glow", 0.35), alpha=pr.get("alpha", 0.55))
        line = make_mat(f"{name}_line", (0.5, 0.6, 0.95), emit=pr.get("line_glow", 0.5))
        f = pr.get("face", 270)   # 門の正面の向き（列車が来る方）
        a = math.radians(f)
        ux, uy = math.cos(a), math.sin(a)   # 門の幅の向き（正面に直交）
        for sg in (-1, 1):   # 両脇の尖塔
            sx, sy = x + ux * sg * w * 0.36, y + uy * sg * w * 0.36
            prim("cone", glass, f"ENV_{name}_sp{sg}", loc=bl((sx, sy), z0 + hh * 0.62), radius1=0.9, radius2=0.05, depth=hh * 1.24, vertices=4)
            box(glass, f"ENV_{name}_sk{sg}", x + ux * sg * w * 0.43, y + uy * sg * w * 0.43, w * 0.16, 0.3, hh * 0.45, z0, f)   # 裾
        box(glass, f"ENV_{name}", x, y, w * 0.62, 0.35, hh * 0.78, z0, f)   # 扉の板（幅は門の幅の向き）
        roof = prim("cone", glass, f"ENV_{name}_arch", loc=bl((x, y), z0 + hh * 0.78 + hh * 0.11), radius1=w * 0.31, radius2=0.0, depth=hh * 0.22, vertices=4)
        roof.rotation_euler = (0, 0, -math.radians(f) + math.pi / 2)   # 四角錐を正面の向きに薄くして、正面から三角に見せる
        roof.scale = (0.06, 1.0, 1.0)
        n = int(pr.get("bars", 13))
        for k in range(n):   # 縦の格子
            u = (k + 0.5) / n - 0.5
            box(line, f"ENV_{name}_bar{k}", x + ux * u * w * 0.6, y + uy * u * w * 0.6, 0.08, 0.08, hh * 0.76, z0 + 0.02, f)
        point_light(f"LGT_{name}", x - math.sin(a) * 3, y + math.cos(a) * 3, z0 + hh * 0.4, pr.get("energy", 900), (0.55, 0.65, 1.0), 3.0)
        return True
    if name.startswith("deadtree"):   # 葉のない大木（太い幹、ねじれた枝、地面に広がる根）
        r0, hh = pr.get("r", 1.6), pr.get("h", 9.0)
        bark = make_mat(f"{name}_bark", tuple(v / 255 for v in pr.get("color", [62, 48, 46])))
        prim("cone", bark, f"ENV_{name}", loc=bl((x, y), z0 + hh / 2), radius1=r0, radius2=r0 * 0.45, depth=hh)
        for k in range(7):   # 枝
            ang = k * 2 * math.pi / 7 + 0.4
            L = hh * (0.55 + 0.1 * (k % 3))
            br = prim("cone", bark, f"ENV_{name}_br{k}", loc=bl((x + math.cos(ang) * L * 0.32, y + math.sin(ang) * L * 0.32), z0 + hh * 0.85 + L * 0.25),
                      radius1=r0 * 0.3, radius2=0.04, depth=L)
            br.rotation_euler = (math.radians(55), 0, -ang + math.pi / 2)
        for k in range(9):   # 根
            ang = k * 2 * math.pi / 9
            L = r0 * (3.0 + (k % 3))
            rt = prim("cone", bark, f"ENV_{name}_rt{k}", loc=bl((x + math.cos(ang) * L * 0.45, y + math.sin(ang) * L * 0.45), z0 + 0.15),
                      radius1=r0 * 0.35, radius2=0.03, depth=L)
            rt.rotation_euler = (math.radians(86), 0, -ang + math.pi / 2)
            rt.scale = (1.0, 0.5, 1.0)
        return True
    if name.startswith("hole"):   # 地面の暗い穴（大木のまわり）
        prim("cyl", make_mat(f"{name}_m", (0.01, 0.01, 0.03)), f"ENV_{name}", loc=bl((x, y), z0 + 0.012), radius=pr.get("r", 1.5), depth=0.02)
        return True
    if name.startswith("fruittree"):   # 光る翠ノ実の生る木（濃い青緑の茂みと、光る緑の実）
        r0, hh, cr = pr.get("r", 0.6), pr.get("h", 6.0), pr.get("crown", 3.6)
        prim("cone", make_mat(f"{name}_bark", (0.3, 0.22, 0.24)), f"ENV_{name}", loc=bl((x, y), z0 + hh / 2), radius1=r0, radius2=r0 * 0.45, depth=hh)
        prim("sphere", make_mat(f"{name}_leaf", tuple(v / 255 for v in pr.get("leaf", [26, 40, 70]))), f"ENV_{name}_crown",
             loc=bl((x, y), z0 + hh), radius=1.0, scale=(cr, cr, cr * 0.55))
        fruit = make_mat(f"{name}_fruit", (0.55, 1.0, 0.8), emit=pr.get("glow", 2.4))
        n = int(pr.get("n", 12))
        for k in range(n):
            ang = k * 2.39996
            rr = cr * (0.55 + 0.4 * ((k * 7) % 5) / 5)
            prim("sphere", fruit, f"ENV_{name}_f{k}", loc=bl((x + math.cos(ang) * rr, y + math.sin(ang) * rr), z0 + hh - cr * 0.35 + (k % 3) * 0.3), radius=0.16)
        point_light(f"LGT_{name}", x, y, z0 + hh - 0.6, pr.get("energy", 60), (0.55, 1.0, 0.75), cr * 0.6)
        return True
    if name.startswith("bench"):   # 木のベンチ
        wood = make_mat(f"{name}_wood", (0.36, 0.26, 0.2))
        box(wood, f"ENV_{name}", x, y, pr.get("w", 1.8), 0.5, 0.08, z0 + 0.42, rot)
        box(wood, f"ENV_{name}_back", x, y, pr.get("w", 1.8), 0.08, 0.5, z0 + 0.5, rot)
        for k, s in enumerate((-1, 1)):
            a = math.radians(rot)
            box(wood, f"ENV_{name}_leg{k}", x + s * 0.8 * math.cos(a), y + s * 0.8 * math.sin(a), 0.08, 0.45, 0.42, z0, rot)
        return True
    if name.startswith("stonering"):   # 木の根元を囲む低い石の輪
        r = pr.get("r", 3.0)
        stone = make_mat(f"{name}_m", (0.34, 0.32, 0.36))
        n = int(pr.get("n", 10))
        for k in range(n):
            ang = 2 * math.pi * k / n
            box(stone, f"ENV_{name}_{k}", x + r * math.cos(ang), y + r * math.sin(ang), 1.2, 0.7, 0.55, z0, math.degrees(ang) + 90)
        return True
    if name.startswith("menhir"):   # 立石
        box(make_mat(f"{name}_m", (0.3, 0.28, 0.32)), f"ENV_{name}", x, y, 0.6, 0.45, pr.get("h", 1.6), z0, rot)
        return True
    if name.startswith("cathedral"):   # 議事塔（尖塔の多いゴシックの大聖堂。walpurgis_bg_01）。w・d・h は本体、spire は中央の尖塔の高さ
        w, d, hh, sp = pr.get("w", 22.0), pr.get("d", 22.0), pr.get("h", 40.0), pr.get("spire", 45.0)
        wall = make_mat(f"{name}_wall", tuple(v / 255 for v in pr.get("wall", [58, 54, 70])))
        roofm = make_mat(f"{name}_roof", tuple(v / 255 for v in pr.get("roof_color", [36, 34, 50])))
        win = make_mat(f"{name}_win", (1.0, 0.72, 0.32), emit=1.8 * lamps)
        box(wall, f"ENV_{name}", x, y, w, d, hh, z0)
        box(wall, f"ENV_{name}_core", x, y, w * 0.5, d * 0.5, hh * 0.3, z0 + hh)   # 中央の塔の胴
        prim("cone", roofm, f"ENV_{name}_spire", loc=bl((x, y), z0 + hh * 1.3 + sp / 2), radius1=w * 0.3, radius2=0.0, depth=sp, vertices=8)
        for k, (sx, sy) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):   # 四隅の小塔と尖り屋根
            cx, cy = x + sx * w / 2, y + sy * d / 2
            prim("cyl", wall, f"ENV_{name}_t{k}", loc=bl((cx, cy), z0 + hh * 0.55), radius=w * 0.09, depth=hh * 1.1, vertices=8)
            prim("cone", roofm, f"ENV_{name}_tr{k}", loc=bl((cx, cy), z0 + hh * 1.1 + sp * 0.2), radius1=w * 0.1, radius2=0.0, depth=sp * 0.4, vertices=8)
        for row in range(int(pr.get("rows", 5))):   # 南の面の金色の窓
            zz = z0 + 14.0 + row * (hh - 16.0) / max(1, pr.get("rows", 5) - 1)
            for k in range(5):
                u = (k + 0.5) / 5 - 0.5
                box(win, f"ENV_{name}_w{row}{k}", x + u * w * 0.8, y + d / 2 + 0.02, 1.0, 0.06, 2.4, zz)
        return True
    if name.startswith("spire"):   # 家の上や街の尖塔（細い石の塔と尖り屋根、窓の灯り）
        r, hh = pr.get("r", 1.6), pr.get("h", 18.0)
        wall = make_mat(f"{name}_wall", tuple(v / 255 for v in pr.get("wall", [60, 56, 70])))
        prim("cyl", wall, f"ENV_{name}", loc=bl((x, y), z0 + hh / 2), radius=r, depth=hh, vertices=8)
        prim("cone", make_mat(f"{name}_roof", tuple(v / 255 for v in pr.get("roof_color", [36, 34, 50]))), f"ENV_{name}_roof",
             loc=bl((x, y), z0 + hh + r * 2.2), radius1=r * 1.2, radius2=0.0, depth=r * 4.4, vertices=8)
        win = make_mat(f"{name}_win", (1.0, 0.72, 0.32), emit=1.6 * lamps)
        for k in range(int(pr.get("windows", 3))):
            box(win, f"ENV_{name}_w{k}", x, y + r + 0.01, 0.5, 0.06, 1.2, z0 + hh * (0.3 + 0.2 * k))
        return True
    return False


def build_extra(name, pr, x, y, lit):
    """家・柱・家具・ランプなど（村とティトの家、A05b）。作ったら True。"""
    rot = pr.get("rot", 0)
    lamps = lit.get("lamps", 1.0) if lit else 1.0
    if any(_m.build_extra(sys.modules[__name__], name, pr, x, y, lit) for _m in EXT):   # 議会室（A28〜A32）、チンクエ（7章）
        return True
    if build_otherworld(name, pr, x, y, lamps):
        return True
    if build_cave(name, pr, x, y):
        return True
    if name.startswith("house"):
        w, d, h = pr.get("w", 6), pr.get("d", 5), pr.get("h", 3)
        # 壁と屋根の色（wall・roof_color、0〜255）。なければ木の壁と赤い瓦（セント・ドルチェは白い漆喰と紫の屋根、A11）
        wm = make_mat(f"{name}_wall", tuple(v / 255 for v in pr["wall"])) if pr.get("wall") else mat_of("wood")
        rm = make_mat(f"{name}_rooftop", tuple(v / 255 for v in pr["roof_color"])) if pr.get("roof_color") else mat_of("roof")
        box(wm, f"ENV_{name}", x, y, w, d, h, 0.0, rot)
        roof_on(rm, f"ENV_{name}_roof", x, y, w + 0.6, d + 0.6, h, pr.get("roof", 1.8), rot)
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
    if name.startswith(("moon", "sun")):   # 空の月・沈む太陽（光る球。遠く高くに置く。白夜月、A13〜A17）
        m = make_mat(f"{name}_m", tuple(v / 255 for v in pr.get("color", [245, 245, 255])), emit=pr.get("emit", 2.5))
        prim("sphere", m, f"ENV_{name}", loc=bl((x, y), pr.get("z", 80.0)), radius=pr.get("r", 5.0))
        return True
    if name.startswith("peak"):   # 遠くの山（円錐。z0 は裾の高さ）
        m = make_mat(f"{name}_m", tuple(v / 255 for v in pr.get("color", [120, 95, 80])))
        hh, r = pr.get("h", 60.0), pr.get("r", 40.0)
        prim("cone", m, f"ENV_{name}", loc=bl((x, y), pr.get("z0", 0.0) + hh / 2), radius1=r, radius2=r * pr.get("top", 0.08), depth=hh)
        return True
    if name.startswith("ledge"):   # 岩の柱の上の丸い足場（上面の高さ h、下は z0 まで。アズ・ブロッケン山、A14〜A17）。
        # 人物の高さは motion.ground_z に入れない（足場の間を跳ぶときに高さが飛ぶため）。足場の上の人物はキーの z に上面の高さを書く
        hh, z0, r = pr.get("h", 5.0), pr.get("z0", -40.0), pr.get("r", 3.0)
        prim("cone", mat_of("rockcliff"), f"ENV_{name}", loc=bl((x, y), (hh + z0) / 2 - 0.08), radius1=r * pr.get("base", 1.35), radius2=r, depth=hh - z0)
        top = make_mat(f"{name}_top", tuple(v / 255 for v in pr.get("top", [150, 112, 72])))
        prim("cyl", top, f"ENV_{name}_top", loc=bl((x, y), hh - 0.04), radius=r, depth=0.08)
        return True
    if name.startswith("rune"):   # 古の風紋（地面の上の光る輪。A14）
        bpy.ops.mesh.primitive_torus_add(major_radius=pr.get("r", 0.8), minor_radius=pr.get("w", 0.05), location=bl((x, y), pr.get("z", 0.0) + 0.03))
        o = bpy.context.object
        o.name = f"ENV_{name}"
        o.data.materials.append(make_mat(f"{name}_m", tuple(v / 255 for v in pr.get("color", [140, 230, 255])), emit=pr.get("emit", 1.2)))
        if pr.get("inner", True):   # 内側にもう一つ小さな輪（渦巻きの目安）
            bpy.ops.mesh.primitive_torus_add(major_radius=pr.get("r", 0.8) * 0.5, minor_radius=pr.get("w", 0.05), location=bl((x, y), pr.get("z", 0.0) + 0.03))
            o2 = bpy.context.object
            o2.name = f"ENV_{name}_in"
            o2.data.materials.append(o.data.materials[0])
        return True
    if name.startswith("barrel"):   # カシの樽（A16）
        hh = pr.get("h", 0.9)
        prim("cyl", mat_of("furn"), f"ENV_{name}", loc=bl((x, y), pr.get("z", 0.0) + hh / 2), radius=pr.get("r", 0.35), depth=hh)
        return True
    if name.startswith("effigy"):   # 山頂の広場の中央の木の像（積んだ黒い丸太の塔と白い面。背景画 az-brocken-summit_bg_01）
        hh, r = pr.get("h", 4.0), pr.get("r", 1.2)
        z0 = pr.get("z", 0.0)
        dark = make_mat(f"{name}_m", (0.2, 0.15, 0.11))
        prim("cone", dark, f"ENV_{name}", loc=bl((x, y), z0 + hh / 2), radius1=r, radius2=0.12, depth=hh)
        mask = make_mat(f"{name}_mask", (0.92, 0.88, 0.8))
        for k, (zz, a) in enumerate(((0.88, 180), (0.55, 140), (0.4, 230))):
            rr = r * (1.0 - zz) + 0.12
            prim("sphere", mask, f"ENV_{name}_mask{k}", loc=bl((x + rr * math.sin(math.radians(a)), y - rr * math.cos(math.radians(a))), z0 + hh * zz), radius=0.15, scale=(1, 0.5, 1))
        return True
    if name.startswith("bud"):   # 咲く前のピラー・オブ・テンペランス（黒い柱と緑のつぼみ。咲くのは fx の bloom）
        hh = pr.get("h", 2.6)
        z0 = pr.get("z", 0.0)
        dark = make_mat(f"{name}_m", (0.08, 0.1, 0.08))
        green = make_mat(f"{name}_g", (0.3, 0.9, 0.35), emit=pr.get("glow", 0.8))
        prim("cyl", dark, f"ENV_{name}", loc=bl((x, y), z0 + hh * 0.35), radius=0.22, depth=hh * 0.7)
        prim("sphere", green, f"ENV_{name}_bud", loc=bl((x, y), z0 + hh * 0.8), radius=0.35, scale=(1, 1, 1.4))
        prim("cone", green, f"ENV_{name}_tip", loc=bl((x, y), z0 + hh), radius1=0.12, radius2=0.0, depth=0.4)
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
    if name.startswith("curtain"):   # color（0〜255）があればその色（バスルームの赤いカーテン、A21）
        cm = make_mat(f"{name}_m", tuple(v / 255 for v in pr["color"])) if pr.get("color") else mat_of("curtain")
        box(cm, f"ENV_{name}", x, y, pr.get("w", 1.0), 0.04, pr.get("h", 1.9), pr.get("z", 0.0), rot)
        return True
    if name.startswith("tub"):   # 大きな浴槽（縁と、沼の水の光る水面。メタリカの風呂は沼の水、A21）
        w, d, hh = pr.get("w", 2.2), pr.get("d", 1.2), pr.get("h", 0.7)
        rim = make_mat(f"{name}_rim", tuple(v / 255 for v in pr.get("color", [190, 150, 90])), rough=0.4)
        box(rim, f"ENV_{name}", x, y, w, d, hh, 0.0, rot)
        water = make_mat(f"{name}_water", tuple(v / 255 for v in pr.get("water", [40, 170, 120])), rough=0.2, emit=pr.get("glow", 0.6))
        box(water, f"ENV_{name}_water", x, y, w - 0.2, d - 0.2, 0.012, hh + 0.002, rot)   # 縁の箱の上面に乗せる（中に置くと箱に隠れて見えなかった、2026-11-02）
        return True
    if name.startswith("tray"):   # 床に置いたお盆と食器（閉じた扉の前の冷めた食事、A18）
        box(mat_of("furn"), f"ENV_{name}", x, y, pr.get("w", 0.5), pr.get("d", 0.35), 0.04, pr.get("z", 0.0), rot)
        dish = make_mat(f"{name}_dish", (0.92, 0.9, 0.85))
        for k, (dx, dy, rr) in enumerate(((-0.12, 0.0, 0.08), (0.1, -0.05, 0.06), (0.1, 0.08, 0.05))):
            prim("cyl", dish, f"ENV_{name}_dish{k}", loc=bl((x + dx, y + dy), pr.get("z", 0.0) + 0.06), radius=rr, depth=0.04)
        return True
    if name.startswith("dais"):   # 円形の石造りの台座（森の庭園、A20）・円形の駅のホーム
        prim("cyl", make_mat(f"{name}_m", tuple(v / 255 for v in pr.get("color", [140, 136, 122]))), f"ENV_{name}",
             loc=bl((x, y), pr.get("h", 0.3) / 2), radius=pr.get("r", 4.0), depth=pr.get("h", 0.3), vertices=48)
        return True
    if name.startswith("bell"):   # 鐘の柱（駅の鐘、A23 で鳴らす）
        hh = pr.get("h", 2.4)
        prim("cyl", make_mat(f"{name}_post", (0.25, 0.2, 0.16)), f"ENV_{name}", loc=bl((x, y), hh / 2), radius=0.06, depth=hh)
        box(make_mat(f"{name}_arm", (0.25, 0.2, 0.16)), f"ENV_{name}_arm", x + 0.25, y, 0.6, 0.08, 0.08, hh - 0.1)
        prim("cone", make_mat(f"{name}_brass", (0.85, 0.65, 0.25), rough=0.3), f"ENV_{name}_bell", loc=bl((x + 0.45, y), hh - 0.35),
             radius1=0.18, radius2=0.06, depth=0.3)
        return True
    if name.startswith("flowers"):   # 花の群れ（森の開けた場所、A20）
        cols = [(0.95, 0.85, 0.3), (0.9, 0.45, 0.6), (0.95, 0.95, 0.95)]
        for k in range(int(pr.get("n", 7))):
            a, rr = k * 2.4, pr.get("r", 0.6) * ((k * 37) % 10) / 10
            fm = make_mat(f"{name}_f{k}", cols[k % 3])
            prim("sphere", fm, f"ENV_{name}_{k}", loc=bl((x + math.cos(a) * rr, y + math.sin(a) * rr), 0.18), radius=0.07)
        return True
    for key, mk in (("table", "furn"), ("shelf", "furn"), ("bed", "bed"), ("crate", "straw")):
        if name.startswith(key):
            box(mat_of(mk), f"ENV_{name}", x, y, pr.get("w", 1.0), pr.get("d", 0.6), pr.get("h", 0.8), 0.0, rot)
            if key == "table":   # 赤い布
                box(mat_of("cloth"), f"ENV_{name}_cloth", x, y, pr.get("w", 1.0) * 0.6, pr.get("d", 0.6) + 0.06, 0.02, pr.get("h", 0.8))
            return True
    return False


def build_site(lay, lighting=None, door_closed=False):
    """場所（world/*-layout.yaml）を、平らな色分けと簡単な立体で作る。lighting は layout の lighting の名前（夕方・夜など）。"""
    MATS.clear()   # カットごとに場面を作り直すので、前のカットの材質は使えない
    g = lay.get("ground") or {}
    lit0 = (lay.get("lighting") or {}).get(lighting) or {}
    gcol = tuple(v / 255 for v in (lit0.get("ground") or g.get("color", [41, 158, 82])))   # 光に ground があれば地面の色も変える（layout_extra で足した場所）
    swamp = make_mat("swamp", gcol)
    glow = lit0.get("ground_glow", g.get("glow"))   # 光る渦の地面（異空間、otherspace_bg_01）。{strength, scale} か true
    if glow:
        gl = glow if isinstance(glow, dict) else {}
        swamp = swirl_mat("swamp_glow", strength=gl.get("strength", 0.7), scale=gl.get("scale", 0.22))
    # 地面の高さ（ground の z）。山の上の場面は足場を 0 に、下の砂漠を z に下げる（アズ・ブロッケン山、A14〜A17）
    poly_mesh("ENV_swamp_far", [(-600, -600), (600, -600), (600, 600), (-600, 600)], g.get("z", 0.0) - 0.03, swamp)
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
            build_room(name, pr, door_closed)
    for name, pr in (lay.get("props") or {}).items():
        if "at" not in pr or name == "door":
            continue
        if pr.get("only_lit") and lighting not in pr["only_lit"]:   # その光のときだけ描く（沈む前の太陽など）
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
        elif name.startswith("tree"):   # r: 幹の太さ（根元の半径）、crown: 葉の茂みの半径（森、A10）
            r0 = pr.get("r", 0.45)
            prim("cone", trunk, f"ENV_{name}", loc=bl((x, y), pr["h"] / 2), radius1=r0, radius2=r0 * (0.6 if r0 > 1.0 else 0.27), depth=pr["h"])
            if pr.get("crown"):
                leaf = make_mat(f"{name}_leaf", tuple(v / 255 for v in pr.get("leaf", [45, 85, 40])))
                prim("sphere", leaf, f"ENV_{name}_crown", loc=bl((x, y), pr["h"]), radius=1.0, scale=(pr["crown"], pr["crown"], pr["crown"] * 0.5))
        elif name.startswith("bush"):   # 茂み: 平たい緑の半球（h は高さ）
            bush = make_mat(f"{name}_m", tuple(v / 255 for v in pr.get("color", [55, 105, 45])))
            prim("sphere", bush, f"ENV_{name}", loc=bl((x, y), pr.get("z", 0.0)), radius=1.0, scale=(pr.get("r", 0.7), pr.get("r", 0.7), pr.get("h", 0.6)))
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
    if lit.get("fill"):   # 洞窟などの弱い補助光（大きな点光源）
        fl = lit["fill"]
        point_light("LGT_fill", fl["at"][0], fl["at"][1], fl.get("z", 8.0), fl.get("energy", 500), tuple(v / 255 for v in fl.get("color", [180, 190, 220])), fl.get("radius", 4.0))
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


def build_train(who, h):
    """夜光列車（設定画 night-train_sheet_01）。前（+Y）が獅子の顔、両側に山羊の頭（ゴードーラ・ゴードール）、後ろの上に蛇（スネーカー）。
    濃い紫がかった黒の車体、後ろのドームに琥珀色の窓、大きな車輪、煙突。長さ約8m・車体の高さ約2.6m（蛇の頭は約4.3m）。"""
    root = bpy.data.objects.new(f"FIG_{who}", None)
    bpy.context.collection.objects.link(root)
    body = make_mat("train_body", (0.26, 0.2, 0.33), rough=0.5)   # 下絵で形が読めるよう、設定画より少し明るく
    dark = make_mat("train_dark", (0.08, 0.06, 0.1), rough=0.6)
    rim = make_mat("train_rim", (0.75, 0.48, 0.2), rough=0.35)
    lion = make_mat("train_lion", (0.45, 0.28, 0.16))
    mane = beam_mat("train_mane", (1.0, 0.55, 0.12), strength=2.5, alpha=0.75)
    eye = make_mat("train_eye", (1.0, 0.75, 0.2), emit=4.0)
    goat = make_mat("train_goat", (0.48, 0.3, 0.62))
    horn = make_mat("train_horn", (0.55, 0.45, 0.32))
    snake = make_mat("train_snake", (0.18, 0.45, 0.32))
    window = make_mat("train_window", (1.0, 0.62, 0.2), emit=3.0)
    prim("cube", body, "train_hull", root, (0, -0.4, 1.55), scale=(1.2, 3.4, 1.0), size=2.0)      # 車体（前後 -3.8〜3.0、高さ 0.55〜2.55）
    prim("cyl", body, "train_boiler", root, (0, 0.6, 2.35), (math.pi / 2, 0, 0), radius=1.05, depth=4.6)   # 丸い屋根（ボイラー）
    for k, yy in enumerate((1.6, 0.6)):                                              # 煙突2本
        prim("cyl", dark, f"train_stack{k}", root, (0.0 if k == 0 else 0.35, yy, 3.55), radius=0.22, depth=0.7)
    prim("sphere", body, "train_dome", root, (0, -2.6, 2.55), radius=1.15, scale=(1.0, 1.0, 0.85))   # 後ろのドーム
    prim("cyl", window, "train_windows", root, (0, -2.6, 2.75), radius=1.17, depth=0.22)            # 琥珀色の窓の帯
    prim("sphere", lion, "train_lion", root, (0, 3.3, 1.75), radius=0.95, scale=(1.0, 0.85, 1.05))  # 獅子の顔（前）
    prim("cyl", mane, "train_mane", root, (0, 3.05, 1.8), (math.pi / 2, 0, 0), radius=1.45, depth=0.25)   # 燃えるたてがみ
    for sx in (-1, 1):
        prim("sphere", eye, f"train_eye{sx}", root, (sx * 0.33, 4.1, 2.0), radius=0.11)
        prim("cone", dark, f"train_lion_horn{sx}", root, (sx * 0.75, 3.3, 2.75), (0, sx * 0.5, 0), radius1=0.18, radius2=0.0, depth=0.7)
        g = prim("sphere", goat, f"train_goat{sx}", root, (sx * 1.45, 1.1, 1.9), radius=0.5, scale=(0.8, 1.15, 0.9))   # 山羊の頭（両側）
        prim("cone", horn, f"train_goat_horn{sx}", root, (sx * 1.55, 0.95, 2.55), (math.radians(-25), sx * 0.35, 0), radius1=0.1, radius2=0.0, depth=0.6)
        for k, yy in enumerate((2.0, 0.6, -0.8, -2.6)):                              # 車輪（後ろが大きい）
            rr = 0.85 if k == 3 else 0.55
            prim("cyl", dark, f"train_wheel{sx}{k}", root, (sx * 1.25, yy, rr), (0, math.pi / 2, 0), radius=rr, depth=0.18)
            prim("cyl", rim, f"train_rim{sx}{k}", root, (sx * 1.3, yy, rr), (0, math.pi / 2, 0), radius=rr * 1.02, depth=0.06)
    for k in range(5):                                                               # 蛇（後ろから立ち上がって前を向く）
        u = k / 4
        prim("sphere", snake, f"train_snake{k}", root, (0, -3.9 + 0.25 * u, 2.4 + 1.7 * u), radius=0.32 - 0.04 * u)
    prim("sphere", snake, "train_snake_head", root, (0, -3.45, 4.25), radius=0.3, scale=(0.8, 1.4, 0.7))
    head = bpy.data.objects.new(f"HEAD_{who}", None)
    bpy.context.collection.objects.link(head)
    head.parent = root
    head.location = (0, 3.3, 1.75)
    arms = {}
    for sx in (-1, 1):   # 腕は無いが、姿勢を当てる処理のために空の肩を置く
        piv = bpy.data.objects.new(f"SHOULDER_{who}{sx}", None)
        bpy.context.collection.objects.link(piv)
        piv.parent = root
        arms[sx] = piv
    root["arms"] = 1
    return root, head, arms


def build_person(who, h, torch=False, sword=False):
    if who == "night-train":
        return build_train(who, h)
    sh = SHAPE.get(who, SHAPE_DEFAULT)
    col = COLORS.get(who, (0.9, 0.9, 0.9))
    r, hr, arm = sh["r"], sh["hr"], sh["arm"]
    body_h = max(0.2, h - 2 * hr)
    root = bpy.data.objects.new(f"FIG_{who}", None)
    bpy.context.collection.objects.link(root)
    m_body = make_mat(f"{who}_body", col)
    m_dark = make_mat(f"{who}_dark", tuple(c * 0.45 for c in col))
    m_face = make_mat(f"{who}_nose", (1.0, 0.93, 0.8))
    al = body_h * 0.6
    rig = None
    if RENDER.get("rig", True) and who not in previs_rig.SKIP:   # 関節のある人形（2026-11-06、tools/previs_rig.py）
        bpy.data.objects.remove(root, do_unlink=True)
        root, head, arms, rig = previs_rig.build(sys.modules[__name__], who, h, sh, col, m_body, m_dark)
    else:
        if who == "karakuri-doll":   # 卵形の胴（球を縦に伸ばす）
            prim("sphere", m_body, f"{who}_body", root, (0, 0, body_h * 0.5), radius=1.0, scale=(r, r, body_h * 0.55))
        else:
            prim("cyl", m_body, f"{who}_body", root, (0, 0, body_h / 2), radius=r, depth=body_h)
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
    if who == "forest-servant":   # 白い丸い頭と、橙に光る四角い目（鼻の代わりに向きを示す）、ギザギザの口の白い帯
        prim("sphere", make_mat("servant_head", (0.93, 0.93, 0.96), rough=0.3), f"{who}_head", head, (0, 0, hr), radius=hr, scale=(1.05, 1, 1.05))
        eye = make_mat("servant_eye", (1.0, 0.6, 0.15), emit=1.5)
        for sx in (-1, 1):
            prim("cube", eye, f"{who}_eye{sx}", head, (sx * hr * 0.38, hr * 0.97, hr * 1.15), size=2.0, scale=(hr * 0.17, 0.015, hr * 0.1))
        prim("cube", make_mat("servant_mouth", (0.25, 0.25, 0.28)), f"{who}_mouth", head, (0, hr * 0.95, hr * 0.62), size=2.0, scale=(hr * 0.55, 0.012, 0.012))
    else:
        prim("sphere", m_body, f"{who}_head", head, (0, 0, hr), radius=hr, scale=(1, 1, 1.2 if who == "arlecchino" else 1))
        face = previs_face.add_face(sys.modules[__name__], who, head, hr) if rig is not None and RENDER.get("face", True) else None
        if face is not None:   # 顔（目・眉・口、2026-11-07 tools/previs_face.py）。鼻は小さく
            FACES[who] = face
            prim("cone", m_face, f"{who}_nose", head, (0, hr * 0.9, hr * 0.86), (-math.pi / 2, 0, 0), radius1=hr * 0.08, radius2=0.0, depth=hr * 0.2)
        else:
            prim("cone", m_face, f"{who}_nose", head, (0, hr * 1.0, hr), (-math.pi / 2, 0, 0), radius1=hr * 0.3, radius2=0.0, depth=hr * 0.7)
    if who == "metallica":
        hat = make_mat("hat", (0.35, 0.2, 0.5))
        prim("cyl", hat, "hat_brim", head, (0, 0, hr * 1.7), radius=0.38, depth=0.025)
        prim("cone", hat, "hat_cone", head, (0, -0.03, hr * 1.7 + 0.3), (math.radians(8), 0, 0), radius1=0.25, radius2=0.0, depth=0.6)
        if rig is not None:
            rig.brim = True   # うつむきの角度を抑える（previs_rig.apply）
        for sx in (-1, 1):   # 長い三つ編み（背中側を見分けるため）
            prim("cyl", m_dark, f"braid{sx}", head, (sx * 0.1, -hr * 0.9, -0.45), radius=0.04, depth=1.1)
    elif who in WITCHES:   # 夜会の魔女と見習い魔女のとんがり帽子（人物の色を濃くしたもの）
        hat = make_mat(f"{who}_hat", tuple(c * 0.55 for c in col))
        big = 1.0 if who.startswith("apprentice") else 1.15
        prim("cyl", hat, f"{who}_brim", head, (0, 0, hr * 1.7), radius=0.3 * big, depth=0.02)
        prim("cone", hat, f"{who}_cone", head, (0, 0, hr * 1.7 + 0.25 * big), radius1=0.18 * big, radius2=0.0, depth=0.5 * big)
        if rig is not None:
            rig.brim = True
    elif who == "karakuri-doll":
        cap = make_mat("doll_cap", (0.5, 0.3, 0.25))
        skull = make_mat("doll_skull", (0.9, 0.85, 0.7))
        prim("cyl", cap, "doll_cap", head, (0, -0.05, hr * 2.0), (math.radians(-12), 0, 0), radius=hr * 1.5, depth=0.08)
        prim("sphere", skull, "doll_skull", head, (0, hr * 0.75, hr), radius=hr * 0.55, scale=(1, 0.6, 1.1))
    elif who == "usamaru":   # ウサギの長い耳
        for sx in (-1, 1):
            prim("sphere", m_body, f"usa_ear{sx}", head, (sx * hr * 0.45, 0, hr * 2.4), radius=1.0, scale=(0.05, 0.03, 0.2))
    elif who == "hundred-knight":
        helmet = make_mat("hk_plume", (0.55, 0.85, 1.0), emit=0.6)
        prim("cone", helmet, "plume", head, (0, 0, hr * 2.0 + 0.1), radius1=0.07, radius2=0.0, depth=0.3)
    elif who == "bisco":
        prim("sphere", m_dark, "tail", root, (0, -r - 0.08, body_h * 0.4), radius=0.1, scale=(0.7, 1.4, 0.9))
    elif who == "marny":   # 大きな緑の蝶の羽（背中から左右に、少し後ろへ開く。黄色の斑紋は下絵では省く）と触角、白い襟
        wing = beam_mat("marny_wing", (0.35, 0.95, 0.3), strength=0.5, alpha=0.8)
        for sx in (-1, 1):
            for k, (zz, sz) in enumerate(((0.08, 0.17), (-0.06, 0.12))):   # 上の羽と下の羽
                w = prim("sphere", wing, f"marny_wing{sx}{k}", root, (sx * 0.16, -0.05, body_h * 0.7 + zz), radius=1.0, scale=(0.14, 0.01, sz))
                w.rotation_euler = (0, sx * math.radians(18 if k == 0 else -12), sx * math.radians(-22))
            prim("cyl", m_dark, f"marny_ant{sx}", head, (sx * hr * 0.4, 0.02, hr * 2.3), (0, sx * 0.3, 0), radius=0.006, depth=0.12)
        collar = make_mat("marny_collar", (0.95, 0.98, 0.9))
        prim("sphere", collar, "marny_collar", root, (0, 0, body_h * 0.95), radius=1.0, scale=(0.08, 0.08, 0.03))
    elif who == "marika-mouse":   # 丸い耳と細い尻尾
        for sx in (-1, 1):
            prim("sphere", m_body, f"mouse_ear{sx}", head, (sx * hr * 0.8, 0, hr * 1.8), radius=hr * 0.55, scale=(1, 0.4, 1))
        prim("cyl", m_dark, "mouse_tail", root, (0, -r - 0.06, 0.02), (math.radians(80), 0, 0), radius=0.006, depth=0.14)
    elif who == "forest-servant":   # 銅の管の角（外へ曲がり、先に笠。まわりに緑の光）、灰色の襟巻き、黒い長靴、濃い緑の腕
        copper = make_mat("servant_copper", (0.72, 0.42, 0.22), rough=0.35)
        glow = beam_mat("servant_glow", (0.55, 1.0, 0.3), strength=1.2, alpha=0.5)
        for sx in (-1, 1):
            prim("cyl", copper, f"servant_pipe{sx}", head, (sx * hr * 0.75, 0, hr * 2.05), (0, sx * 0.45, 0), radius=0.035, depth=0.2)
            prim("cone", copper, f"servant_cap{sx}", head, (sx * hr * 1.0, 0, hr * 2.55), (0, sx * 0.25, 0), radius1=0.06, radius2=0.0, depth=0.07)
            prim("sphere", glow, f"servant_glow{sx}", head, (sx * hr * 0.95, 0, hr * 2.35), radius=0.08)
        prim("sphere", make_mat("servant_scarf", (0.55, 0.55, 0.57)), "servant_scarf", root, (0, 0.02, body_h * 0.95), radius=1.0, scale=(r * 1.1, r * 1.05, 0.07))
        boot = make_mat("servant_boot", (0.1, 0.1, 0.11))
        for sx in (-1, 1):
            prim("cube", boot, f"servant_boot{sx}", root, (sx * r * 0.45, 0.04, 0.09), size=2.0, scale=(0.09, 0.14, 0.09))
        green = make_mat("servant_arm", (0.27, 0.29, 0.22))
        for sx in (-1, 1):
            for ob in arms[sx].children_recursive:
                if ob.type == "MESH":
                    ob.data.materials.clear(); ob.data.materials.append(green)
    elif who == "arlecchino":
        horn = make_mat("horn", (0.72, 0.45, 0.2))
        for sx in (-1, 1):
            prim("cyl", horn, f"horn{sx}", head, (sx * hr * 0.55, 0, hr * 2.4 + 0.05), radius=0.03, depth=0.25)
    elif who in ("luckiny", "tito") or who.startswith("villager"):   # 獣人の角
        horn = make_mat(f"{who}_horn", (0.85, 0.8, 0.7))
        for sx in (-1, 1):
            prim("cone", horn, f"horn{sx}", head, (sx * hr * 0.5, 0, hr * 2.2), (0, sx * 0.25, 0), radius1=0.035, radius2=0.0, depth=0.3)
    for _m in EXT:   # 回3 の人形（A28〜A32）、7章の人形
        _m.decorate(sys.modules[__name__], who, root, head, arms, sh, body_h, col)
    if rig is not None:   # 関節の人形: 飾りを関節へ付け替え、剣と松明は右手に
        previs_rig.reparent_decor(rig)
        if sword:
            previs_rig.attach_sword(sys.modules[__name__], rig, who)
        if torch:
            previs_rig.attach_torch(sys.modules[__name__], rig, who)
        return root, head, arms
    if sword:   # 剣: 右手の先から刃（百騎兵、A07）
        blade = make_mat(f"{who}_blade", (0.85, 0.88, 0.95))
        hilt = make_mat(f"{who}_hilt", (0.25, 0.2, 0.15))
        prim("cyl", hilt, f"{who}_hilt", arms[1], (0, 0, -al - 0.06), radius=0.03, depth=0.16)
        prim("cube", blade, f"{who}_blade", arms[1], (0, 0, -al - 0.14 - 0.45), scale=(0.05, 0.012, 0.45), size=2.0)
        root["sword"] = 1
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
    # 下絵なので光の跳ね返りを減らして速くする
    for k, v in (("max_bounces", 3), ("diffuse_bounces", 2), ("glossy_bounces", 0), ("transmission_bounces", 0),
                 ("volume_bounces", 0), ("transparent_max_bounces", 8)):
        try:
            setattr(scene.cycles, k, v)
        except Exception:
            pass
    try:
        scene.cycles.caustics_reflective = scene.cycles.caustics_refractive = False
    except Exception:
        pass
    try:
        scene.cycles.use_denoising = denoise
    except Exception:
        pass
    try:   # 動かない背景（森・街の何百もの形）をコマごとに作り直さない（2026-11-05。1コマの時間を短くする）
        scene.render.use_persistent_data = True
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
    m_shadow = make_mat(f"{name}_shadow", (0.5, 0.38, 0.22))
    m_mound = make_mat(f"{name}_mound", tuple(min(1.0, c * 1.12) for c in SAND))
    if getattr(track, "mound", "") == "soft":   # 広く低い半透明の盛り上がり（節が管のように並んで見えないように）
        m_mound = beam_mat(f"{name}_mound_soft", tuple(min(1.0, c * 1.2) for c in SAND), strength=0.05, alpha=0.55)
    segs, shadows, mounds = [], [], []
    m_band = make_mat(f"{name}_band", (0.86, 0.38, 0.16))
    for i in range(track.segs):
        segs.append(prim("sphere", m_head if i == 0 else (m_band if i in (2, 3) else m_body), f"{name}_seg{i}", None, (0, 0, -50), radius=1.0))
        shadows.append(prim("cyl", m_shadow, f"{name}_shadow{i}", None, (0, 0, -50), radius=1.0, depth=0.02))
        mounds.append(prim("sphere", m_mound, f"{name}_mound{i}", None, (0, 0, -50), radius=1.0))
    deco = {}
    if name == "sand-eater":   # 設定画（sand-eater_sheet_01「針菌幼虫」）: とがった頭、左右の黄色い花のような目、腹の橙のトゲ、尾の赤いトゲ（2026-11-06）
        spike = make_mat(f"{name}_spike", (0.92, 0.36, 0.14))
        eye = make_mat(f"{name}_eye", (1.0, 0.86, 0.35), emit=1.2)
        ring = make_mat(f"{name}_eyering", (0.95, 0.45, 0.12), emit=0.6)
        pupil = make_mat(f"{name}_pupil", (0.25, 0.12, 0.05))
        deco["nose"] = prim("cone", m_head, f"{name}_nose", None, (0, 0, -50), radius1=1.0, radius2=0.0, depth=1.0, vertices=20)
        deco["eyes"] = [(prim("cyl", ring, f"{name}_eyering{sx}", None, (0, 0, -50), radius=1.0, depth=1.0, vertices=24),
                         prim("cyl", eye, f"{name}_eye{sx}", None, (0, 0, -50), radius=1.0, depth=1.0, vertices=24),
                         prim("cyl", pupil, f"{name}_pupil{sx}", None, (0, 0, -50), radius=1.0, depth=1.0, vertices=12)) for sx in (-1, 1)]
        deco["tail"] = prim("cone", spike, f"{name}_tail", None, (0, 0, -50), radius1=1.0, radius2=0.0, depth=1.0, vertices=12)
        deco["mouth"] = prim("sphere", make_mat(f"{name}_mouth", (0.35, 0.05, 0.04)), f"{name}_mouth", None, (0, 0, -50), radius=1.0)   # 口（キーの open で開く）
        deco["legs"] = [prim("cone", spike, f"{name}_leg{i}{sx}", None, (0, 0, -50), radius1=1.0, radius2=0.0, depth=1.0, vertices=8)
                        for i in range(2, track.segs - 2) for sx in (-1, 1)]
    return segs, shadows, mounds, deco


def _aim(o, p, d, size):
    """円すい・円柱（+Z が軸）を、点 p に、向き d へ向けて置く。size は (半径, 半径, 長さ)。"""
    from mathutils import Vector as _V
    o.location = p
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = _V((0, 0, 1)).rotation_difference(d)
    o.scale = size


def pose_creature(parts, track, t):
    segs, shadows, mounds, deco = parts
    soft = getattr(track, "mound", "") == "soft"
    body = track.body(t)
    if deco:   # 頭・目・トゲ・尾（体の節の並びから向きを出す）
        pts = [Vector(bl((p[0], p[1]), p[2])) for p, r in body]
        rs = [r for p, r in body]
        up = Vector((0, 0, 1))

        def dir_at(i):
            a, b = (pts[0], pts[1]) if i == 0 else (pts[i - 1], pts[i])
            d = a - b
            return d.normalized() if d.length > 1e-6 else Vector((0, 1, 0))
        d0 = dir_at(0)
        side0 = d0.cross(up)
        side0 = side0.normalized() if side0.length > 1e-3 else Vector((1, 0, 0))
        up0 = side0.cross(d0).normalized()
        r0 = rs[0]
        _aim(deco["nose"], pts[0] + d0 * r0 * 0.55, d0, (r0 * 0.78, r0 * 0.78, r0 * 1.9))
        for (rg_, ey, pu), sx in zip(deco["eyes"], (-1, 1)):
            c = pts[0] + side0 * sx * r0 * 0.86 + up0 * r0 * 0.28 - d0 * r0 * 0.05
            n = (side0 * sx).normalized()
            _aim(rg_, c, n, (r0 * 0.46, r0 * 0.46, 0.05))
            _aim(ey, c + n * 0.03, n, (r0 * 0.34, r0 * 0.34, 0.05))
            _aim(pu, c + n * 0.06, n, (r0 * 0.1, r0 * 0.1, 0.05))
        op = track.open_at(t) if hasattr(track, "open_at") else 0.0
        mo_ = deco["mouth"]
        if op > 0.02:   # 口: 頭の前の下に、暗い赤の楕円（開くほど大きい）
            _aim(mo_, pts[0] + d0 * r0 * 0.62 - up0 * r0 * 0.42, d0, (r0 * 0.55 * op, r0 * 0.38 * op + 0.02, r0 * 0.5 * op + 0.02))
        else:
            mo_.location = (0, 0, -50)
        dl = dir_at(len(pts) - 1)
        rl = rs[-1]
        _aim(deco["tail"], pts[-1] - dl * rl * 1.6, -dl, (rl * 0.75, rl * 0.75, rl * 3.2))
        k = 0
        for i in range(2, len(pts) - 2):
            di = dir_at(i)
            si = di.cross(up)
            si = si.normalized() if si.length > 1e-3 else Vector((1, 0, 0))
            ui = si.cross(di).normalized()
            for sx in (-1, 1):
                o = deco["legs"][k]
                k += 1
                outv = (si * sx * 0.8 - ui * 0.6).normalized()
                _aim(o, pts[i] + outv * rs[i] * 0.95, (outv - ui * 0.4).normalized(), (rs[i] * 0.16, rs[i] * 0.16, rs[i] * 0.7))
    for (p, r), so, sh, mo in zip(body, segs, shadows, mounds):
        x, y, z = p
        so.location = bl((x, y), z)
        so.scale = (r, r, r)
        if -2.5 < z < 0.0:   # 砂の下の浅い所: 影（深いほど薄く小さく）と盛り上がり
            k = 1.0 + z / 2.5
            sh.location = bl((x, y), 0.02)
            sh.scale = (r * (1.1 + 0.3 * k), r * (1.1 + 0.3 * k), 1.0)
            mo.location = bl((x, y), 0.0)   # 盛り上がり: 地面から 0.2〜0.6m（浅いほど高い）
            mo.scale = (r * 2.6, r * 2.6, 0.12 + 0.22 * k) if soft else (r * 1.5, r * 1.5, 0.2 + 0.4 * k)
        else:
            sh.location = mo.location = (0, 0, -50)


def build_fx(fx, i):
    if fx.get("kind") in previs_fx.KINDS_2D:   # 画面の効果は描いた後に重ねる（物は作らない）
        return []
    ext = previs_fx.build(sys.modules[__name__], fx, i)   # 戦いの効果（2026-11-06）
    if ext is not None:
        return ext
    ext = previs_council.build_fx(sys.modules[__name__], fx, i)   # 霧・雷撃・閃光・沼の波（A28〜A32）
    if ext is not None:
        return ext
    kind = fx.get("kind", "plume")
    col = tuple(v / 255 for v in fx["color"]) if fx.get("color") and kind not in ("light",) else None
    m = make_mat(f"fx{i}", col or tuple(min(1.0, c * 1.08) for c in SAND))
    if kind == "pool":   # 広がる沼（床に平たい円。少し光る。毒沼だが色は碧、ユーザー指定 2026-10-31）
        pm = make_mat(f"fx{i}_pool", col or (0.12, 0.56, 0.45), rough=0.3, emit=fx.get("glow", 0.25))
        return [prim("cyl", pm, f"fx{i}_pool", None, (0, 0, -50), radius=1.0, depth=0.04, vertices=48)]
    if kind == "bloom":  # 花のように開く（柱のまわりの花びら。根元で外へ倒れる）
        bm = make_mat(f"fx{i}_petal", col or (0.55, 0.85, 1.0), emit=fx.get("glow", 0.6))
        objs = []
        for j in range(int(fx.get("n", 6))):
            o = prim("cube", bm, f"fx{i}_petal{j}", None, (0, 0, 0), size=1.0)
            o.scale = (fx.get("w", 0.55), 0.08, fx.get("len", 2.2))
            bpy.context.view_layer.objects.active = o
            o.select_set(True)
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            for v in o.data.vertices:   # 根元（下の端）を原点に
                v.co.z += fx.get("len", 2.2) / 2
            o.location = (0, 0, -50)
            objs.append(o)
        return objs
    if kind == "sprout":  # 芽吹いて伸び、恐怖で一瞬に枯れる草（緑の茎と、枯れた茶色の茎）
        g = make_mat(f"fx{i}_green", (0.35, 0.75, 0.3))
        d = make_mat(f"fx{i}_dead", (0.45, 0.33, 0.2))
        n = int(fx.get("n", 7))
        objs = []
        for mat, tag in ((g, "g"), (d, "d")):
            for j in range(n):
                o = prim("cone", mat, f"fx{i}_{tag}{j}", None, (0, 0, 0), radius1=0.04, radius2=0.0, depth=1.0, vertices=8)
                for v in o.data.vertices:
                    v.co.z += 0.5
                o.location = (0, 0, -50)
                objs.append(o)
        return objs
    if kind == "ring":   # 波紋は平たく細い、少し暗い輪
        m = make_mat(f"fx{i}_ring", col or tuple(c * 0.72 for c in SAND))
        objs = []
        for j in range(int(fx.get("n", 3))):
            bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.025, major_segments=48, minor_segments=6)
            o = bpy.context.object
            o.data.materials.append(m)
            o.location = (0, 0, -50)
            objs.append(o)
        return objs
    if kind == "eyes":    # 闇の中で光る目（2つ）
        em = make_mat(f"fx{i}_eye", (1.0, 0.75, 0.2), emit=6.0)
        return [prim("sphere", em, f"fx{i}_eye{j}", None, (0, 0, -50), radius=1.0) for j in range(2)]
    if kind == "spark":   # 火花（明るい小さな光）
        em = make_mat(f"fx{i}_spark", (1.0, 0.9, 0.5), emit=8.0)
        return [prim("sphere", em, f"fx{i}_sp{j}", None, (0, 0, -50), radius=1.0) for j in range(5)]
    if kind == "leak":   # 扉の下のすき間から漏れる光（床に平たい光る帯と、弱い点光源。A18 の閉じこもるメタリカの部屋）
        em = make_mat(f"fx{i}_leak", tuple(v / 255 for v in fx.get("color", [90, 255, 140])), emit=0.001)
        o = prim("cube", em, f"fx{i}_leak", None, (0, 0, -50), size=1.0)
        bpy.ops.object.light_add(type="POINT", location=(0, 0, -50))
        lt = bpy.context.object
        lt.name = f"LGT_fx{i}"
        lt.data.color = tuple(v / 255 for v in fx.get("color", [90, 255, 140]))
        lt.data.shadow_soft_size = 0.3
        return [o, lt]
    if kind == "shards":  # 飛び散るガラスの破片（窓破り、A18）
        gm = beam_mat(f"fx{i}_glass", tuple(v / 255 for v in fx.get("color", [200, 235, 255])), strength=0.8, alpha=0.4)   # alpha は透ける割合。Workbench で見えるよう 0.7 → 0.4（2026-11-06）
        return [prim("cube", gm, f"fx{i}_sh{j}", None, (0, 0, -50), size=1.0) for j in range(int(fx.get("n", 14)))]
    if kind == "rift":    # 何もない空間の裂け目（夜光列車が出入りする。縦長の光る楕円が開いて閉じる、A23）
        rm = beam_mat(f"fx{i}_rift", col or (0.7, 0.45, 1.0), strength=3.0, alpha=0.85)
        cm = make_mat(f"fx{i}_rift_core", (0.02, 0.0, 0.05), rough=1.0)
        return [prim("sphere", rm, f"fx{i}_rift", None, (0, 0, -50), radius=1.0), prim("sphere", cm, f"fx{i}_rift_core", None, (0, 0, -50), radius=1.0)]
    if kind == "breath":  # 火を吐く（獅子の口から前へ伸びる炎の円すい、A23 の戦い）
        fm = beam_mat(f"fx{i}_breath", col or (1.0, 0.5, 0.1), strength=4.0, alpha=0.45)   # 透ける割合。Workbench で炎が読めるよう 0.8 → 0.45（2026-11-06）
        return [prim("cone", fm, f"fx{i}_breath", None, (0, 0, -50), radius1=1.0, radius2=0.15, depth=1.0)]
    if kind == "paper":   # ひらひら舞い落ちる紙切れ（切符、A20）
        pm = make_mat(f"fx{i}_paper", (0.95, 0.92, 0.82))
        return [prim("cube", pm, f"fx{i}_paper", None, (0, 0, -50), size=1.0)]
    if kind == "light":   # だんだん強くなる光（ピラーが光り始める）
        bpy.ops.object.light_add(type="POINT", location=(0, 0, -50))
        lt = bpy.context.object
        lt.name = f"LGT_fx{i}"
        lt.data.color = tuple(v / 255 for v in fx.get("color", [150, 220, 255]))
        lt.data.shadow_soft_size = 0.6
        return [lt]
    return [prim("sphere", m, f"fx{i}_{j}", None, (0, 0, -50), radius=1.0) for j in range(6)]


def pose_fx(fx, objs, t, pos, place):
    if fx.get("kind") in previs_fx.KINDS_2D:
        return
    if previs_fx.pose(sys.modules[__name__], fx, objs, t, FXCTX):
        return
    if previs_council.pose_fx(sys.modules[__name__], fx, objs, t, pos, place):
        return
    at = fx["at"]
    p = pos[at] if isinstance(at, str) and at in pos else place(at)
    if fx.get("off"):   # 置き場所からのずれ [dx, dy]（人物が持つ物を、体の前に出すなど）
        p = (p[0] + fx["off"][0], p[1] + fx["off"][1])
    amt = motion.fx_amount(fx, t)
    t0, t1 = fx["t"]
    kind = fx.get("kind", "plume")
    z0 = fx.get("z", 0.0)
    if kind == "eyes":    # 目: t の間だけ、開くように縦に伸びる
        on = t0 <= t <= t1
        op = min(1.0, (t - t0) / 0.4) if on else 0.0
        for j, o in enumerate(objs):
            if not on:
                o.location = (0, 0, -50)
                continue
            dx = (j - 0.5) * fx.get("gap", 0.9)
            o.location = bl((p[0] + dx, p[1]), z0)
            rr = fx.get("r", 0.18)
            o.scale = (rr, rr, rr * max(0.08, op))
        return
    if kind == "spark":   # 火花: 一瞬だけ散る
        for j, o in enumerate(objs):
            if amt <= 0.01:
                o.location = (0, 0, -50)
                continue
            ang = j * 1.3
            d = 0.6 * amt
            o.location = bl((p[0] + math.cos(ang) * d, p[1] + math.sin(ang) * d), z0 + 0.3 * math.sin(j) * amt)
            rr = 0.08 + 0.06 * amt
            o.scale = (rr, rr, rr)
        return
    if kind == "pool":    # 沼: t の間に r0 から r まで広がり、その後はそのまま
        u = 0.0 if t < t0 else min(1.0, (t - t0) / max(1e-6, t1 - t0))
        u = 1 - (1 - u) * (1 - u)
        rr = fx.get("r0", 0.3) + (fx.get("r", 10.0) - fx.get("r0", 0.3)) * u
        o = objs[0]
        if t < t0:
            o.location = (0, 0, -50)
        else:
            o.location = bl(p, z0 + 0.02)
            o.scale = (rr, rr * fx.get("ry", 1.0), 1.0)
        return
    if kind == "bloom":   # 花びら: t の間に 0 度から open 度まで外へ倒れる
        u = 0.0 if t < t0 else min(1.0, (t - t0) / max(1e-6, t1 - t0))
        u = u * u * (3 - 2 * u)
        ang = math.radians(fx.get("open", 55)) * u
        n = len(objs)
        rad = fx.get("r", 0.75)
        for j, o in enumerate(objs):
            a = 2 * math.pi * j / n
            o.location = bl((p[0] + math.sin(a) * rad, p[1] - math.cos(a) * rad), z0)
            # 外向き（方位 a）へ倒す: 方位へ向けてから、x 軸のまわりに傾ける
            o.rotation_euler = (ang, 0, -a + math.pi)
        return
    if kind == "sprout":  # 草: t0 から grow 秒で伸び、wither の時刻から 0.5 秒で茶色になってしおれる
        n = len(objs) // 2
        grow = fx.get("grow", 3.0)
        tw = fx.get("wither", t1)
        hmax = fx.get("h", 0.5)
        g = 0.0 if t < t0 else min(1.0, (t - t0) / grow)
        w = 0.0 if t < tw else min(1.0, (t - tw) / 0.5)
        for j in range(n):
            a = 2 * math.pi * j / n + 0.4
            rr = 0.12 * (j % 3)
            x, y = p[0] + math.cos(a) * rr, p[1] + math.sin(a) * rr
            hh = hmax * (0.6 + 0.4 * ((j * 7) % 5) / 4)
            for k, o in ((0, objs[j]), (1, objs[n + j])):
                show = g > 0.01 and ((k == 0 and w < 0.5) or (k == 1 and w >= 0.5))
                if not show:
                    o.location = (0, 0, -50)
                    continue
                o.location = bl((x, y), z0)
                o.scale = (1.0, 1.0, max(0.02, hh * g * (1 - 0.45 * w)))
                o.rotation_euler = (math.radians(12 + 70 * w) * (1 if j % 2 else -1), 0, a)
        return
    if kind == "leak":    # 漏れる光: t の間、amount の形で明るくなり、pulse があればゆっくり明滅する
        u = 0.0 if t < t0 else min(1.0, (t - t0) / max(1e-6, fx.get("rise", 1.0)))
        if t > t1:
            u = max(0.0, 1.0 - (t - t1) / fx.get("fade", 0.5))
        if fx.get("pulse"):
            u *= 0.65 + 0.35 * math.sin(2 * math.pi * t / fx["pulse"])
        o, lt = objs
        w, d = fx.get("w", 0.9), fx.get("d", 0.35)
        o.location = bl(p, z0 + 0.006)
        o.scale = (w, d, 0.01)
        o.rotation_euler = (0, 0, -math.radians(fx.get("rot", 0)))
        o.data.materials[0].node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = fx.get("emit", 4.0) * u
        lt.location = bl(p, z0 + 0.08)
        lt.data.energy = fx.get("energy", 25) * u
        return
    if kind == "shards":  # 破片: t0 に dir の向きへ扇形に飛び出し、落ちながら回る
        n = len(objs)
        for j, o in enumerate(objs):
            if t < t0 or t > t1:
                o.location = (0, 0, -50)
                continue
            tt = t - t0
            a = math.radians(fx.get("dir", 180) + (j / max(1, n - 1) - 0.5) * fx.get("spread", 100))
            v = fx.get("speed", 4.0) * (0.6 + 0.4 * ((j * 7) % 5) / 4)
            vz = 1.2 + 1.5 * ((j * 3) % 4) / 3
            zz = max(0.02, z0 + vz * tt - 4.9 * tt * tt)
            o.location = bl((p[0] + math.sin(a) * v * tt, p[1] - math.cos(a) * v * tt), zz)
            sz = fx.get("size", 0.12) * (0.5 + 0.5 * ((j * 5) % 3) / 2)
            o.scale = (sz, sz * 0.7, 0.01)
            o.rotation_euler = (tt * 7 + j, tt * 5, j * 0.7)
        return
    if kind == "rift":    # 裂け目: t0 から open 秒で開き、t1 の close 秒前から閉じる。face（方位）の向きに面を向ける
        on = t0 <= t <= t1
        if not on:
            for o in objs:
                o.location = (0, 0, -50)
            return
        op, cl = fx.get("open", 0.8), fx.get("close", 0.8)
        u = min(1.0, (t - t0) / op, max(0.0, (t1 - t) / cl))
        u = u * u * (3 - 2 * u)
        w, hh = fx.get("w", 3.0), fx.get("h", 4.5)
        th = math.radians(fx.get("face", 90))
        for k, o in enumerate(objs):
            sc = 1.0 if k == 0 else 0.82
            o.location = bl(p, z0 + hh / 2)
            o.scale = (w / 2 * u * sc, 0.06 if k == 0 else 0.03, hh / 2 * (0.3 + 0.7 * u) * sc)
            o.rotation_euler = (0, 0, -th)
        return
    if kind == "breath":  # 炎: t の間、face の向きへ length まで伸びて揺らぐ（at は口の位置、z は高さ）
        o = objs[0]
        if not (t0 <= t <= t1):
            o.location = (0, 0, -50)
            return
        u = min(1.0, (t - t0) / 0.3, max(0.0, (t1 - t) / 0.3))
        L = fx.get("length", 5.0) * u
        th = math.radians(fx.get("face", 0))
        dx, dy = math.sin(th), -math.cos(th)
        o.location = bl((p[0] + dx * L / 2, p[1] + dy * L / 2), z0)
        rr = fx.get("r", 1.0) * (0.8 + 0.2 * math.sin(t * 23))
        o.scale = (rr, rr, max(0.01, L))
        o.rotation_euler = (math.pi / 2, 0, -th + math.pi)
        return
    if kind == "paper":   # 紙: t の間に z0 から地面（end_z）まで、左右に揺れながら落ちる。その後は地面に残る（keep）
        o = objs[0]
        if t < t0 or (t > t1 and not fx.get("keep", True)) or (fx.get("gone") is not None and t >= fx["gone"]):   # gone: 拾われて消える秒
            o.location = (0, 0, -50)
            return
        u = min(1.0, (t - t0) / max(1e-6, t1 - t0))
        zz = z0 + (fx.get("end_z", 0.02) - z0) * u
        sway = 0.0 if u >= 1.0 else math.sin(u * math.pi * fx.get("swings", 3)) * fx.get("sway", 0.35)
        dx, dy = fx.get("drift", [0.0, 0.0])
        o.location = bl((p[0] + dx * u + sway, p[1] + dy * u), zz)
        o.scale = (0.12, 0.07, 0.004)
        o.rotation_euler = (0.0 if u >= 1.0 else math.sin(u * 9) * 0.6, 0.0, u * 2.0)
        return
    if kind == "light":   # 光: 時刻とともに強くなる（shape rise）
        u = 0.0 if t < t0 else min(1.0, (t - t0) / max(1e-6, t1 - t0))
        objs[0].location = bl(p, z0)
        if fx.get("fade") and t > t1:
            u = max(0.0, 1.0 - (t - t1) / fx["fade"])
        objs[0].data.energy = fx.get("energy", 300) * u
        return
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
        o.location = bl((p[0] + math.cos(ang) * r * 0.3 * f, p[1] + math.sin(ang) * r * 0.3 * f), z0 + h * f * amt)   # z: 噴き上げの根元の高さ（天井の砂ぼこり、A07-015）
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
    elif pose in ("kneel", "kneeling"):   # ひざまずく（低く、少し前へ。0.7 では立っているように見えた、A08-006 の見直し 2026-11-01）
        k, rx = 0.58, -math.radians(10)
    elif pose in ("sit", "sitting"):
        k = 0.6
    elif pose == "bow":        # お辞儀: 上体を前へ倒す（人形は腰で曲がらないので、体ごと少し倒して低くする。35度では倒れて見えた）
        k, rx = 0.93, -math.radians(20)
    elif pose == "look_up":    # 見上げる: 少し反る
        rx = math.radians(10)
    elif pose == "kneel_hold":  # ひざをついて、両手で相手の手を包む
        k = 0.7
    elif pose == "ready":       # 剣を構える（前かがみ）
        rx = -math.radians(10)
    elif pose == "slash":       # 斬る（前へ踏み込む）
        rx = -math.radians(18)
    elif pose == "look_down":   # うつむく
        rx = -math.radians(12)
    elif pose == "proud":       # 胸を張る（両手を腰に）
        rx = math.radians(6)
    elif pose == "cover":       # 片手で口を押さえる（少し前かがみ）
        rx = -math.radians(10)
    elif pose == "cross":       # 腕を組んでふんぞり返る
        rx = math.radians(8)
    elif pose == "lean":        # 顔をずいと近づける（前へ傾く）
        rx = -math.radians(18)
    elif pose == "stroke":      # かがんで相手の頭をなでる
        k, rx = 0.8, -math.radians(15)
    elif pose == "jump":
        for sx, piv in arms.items():
            piv.rotation_euler = (0, sx * -math.radians(120), 0)
    elif pose == "shake":   # 犬のように体を振る
        rz += math.radians(18) * math.sin(2 * math.pi * 6 * t)
    elif pose == "dance":   # ワルギスのダンス（A16・A17）: 跳ねながら左右に体をひねり、腕を交互に振り上げる
        z += abs(math.sin(2 * math.pi * 1.6 * t)) * 0.06 * hh
        rz += math.radians(30) * math.sin(2 * math.pi * 0.8 * t)
        rx = math.radians(6) * math.sin(2 * math.pi * 1.6 * t)
    elif pose == "hop":     # 喜んで小さく跳ねる
        z += abs(math.sin(2 * math.pi * 2.2 * t)) * 0.12 * hh
    elif pose == "ride":    # ホウキに腰かける（A15）
        k, rx = 0.62, -math.radians(8)
    elif pose == "cough":   # むせる（前かがみで口を押さえ、小刻みに揺れる）
        rx = -math.radians(26) + math.radians(5) * math.sin(2 * math.pi * 4 * t)
    elif pose == "cry":     # ひざをついて泣く（顔を両手でおおう）
        k, rx = 0.68, -math.radians(16) + math.radians(3) * math.sin(2 * math.pi * 1.5 * t)
    elif pose == "fall":    # しりもち（後ろへ傾いて座る）
        k, rx = 0.55, math.radians(16)
    elif pose == "spin":    # 転がる・回る（からくり人形、A14）
        rz += 4 * math.pi * t
    elif pose == "nod":     # うなずく（体ごと前へ深く倒す。A07-004 の溜め、2026-11-01）
        k, rx = 0.95, -math.radians(24)
    elif pose == "tumble":  # 宙で前へ回る（とんぼ返り。体を丸めて x 軸のまわりに回す、A07-014・015）
        k, rx = 0.7, -2 * math.pi * 1.3 * t
    elif pose == "flip":    # 天井に逆さにしゃがむ（足を天井に付け、体は下へ。位置の z は足の高さ、A07-015）
        k, rx, rz = 0.65, math.pi + math.radians(20), rz + math.pi
    elif pose == "dive":    # 頭から真下へ突っ込む（剣を先に。位置の z は足の高さ、A07-015b・016）
        rx, rz = math.pi - math.radians(12), rz + math.pi
    if root.get("torch") and pose not in ("wave", "point", "arms_up", "lie"):   # 松明を頭の上に立てて持つ（前は横に掲げていて、棒が画面を斜めに横切った。A06 の見直し 2026-11-01）
        arms[1].rotation_euler = (math.radians(160), -math.radians(12), 0)
    if pose in ("hold", "kneel_hold"):
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(70), 0, 0)
    elif pose == "dive":        # 両腕を頭の先へ（剣が先になる）
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(175), 0, sx * -math.radians(8))
    elif pose in ("tumble", "flip"):   # 体を丸めて、腕を前に
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(60), 0, 0)
    elif pose == "ready":
        arms[1].rotation_euler = (math.radians(60), 0, 0)
    elif pose == "slash":       # 右腕を上から斜めに振り下ろす
        arms[1].rotation_euler = (math.radians(160 - 140 * min(1.0, (t * 3.0) % 1.0)), 0, -math.radians(30))
    elif pose == "proud":
        for sx, piv in arms.items():
            piv.rotation_euler = (0, sx * math.radians(35), 0)
    elif pose == "stroke":
        arms[1].rotation_euler = (math.radians(95 + 10 * math.sin(2 * math.pi * 1.2 * t)), 0, 0)
    elif pose == "cover":
        arms[1].rotation_euler = (math.radians(145), 0, math.radians(30))
    elif pose == "cross":       # 腕を前へ上げて内へ寄せる（胸の前で組む。符号を逆にすると横へ広がる）
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(80), 0, sx * math.radians(55))
    elif pose == "horn":        # 角笛を口に当てて吹く（両腕を前に上げる）
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(115), 0, sx * -math.radians(15))
    elif pose == "gesture":    # 片手を横に広げて部屋を示す
        arms[1].rotation_euler = (math.radians(30), -math.radians(70 + 20 * math.sin(2 * math.pi * 0.6 * t)), 0)
    elif pose == "dance":
        for sx, piv in arms.items():
            ph = 0 if sx > 0 else math.pi
            piv.rotation_euler = (math.radians(40), sx * -math.radians(95 + 55 * math.sin(2 * math.pi * 0.8 * t + ph)), 0)
    elif pose in ("hop", "ride"):
        for sx, piv in arms.items():
            piv.rotation_euler = (0, sx * -math.radians(130), 0) if pose == "hop" else (math.radians(55), 0, sx * math.radians(20))
    elif pose == "cough":
        arms[1].rotation_euler = (math.radians(140), 0, math.radians(30))
    elif pose == "cry":
        for sx, piv in arms.items():
            piv.rotation_euler = (math.radians(150), 0, sx * math.radians(28))
    elif pose == "fall":
        for sx, piv in arms.items():
            piv.rotation_euler = (-math.radians(35), 0, sx * -math.radians(20))
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


def render_cut(st, lay, cut, sec, fps, size, samples, out_dir, work_root, denoise=False, preview=False, motion_step=None, audit=None):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _PRIM_MESH.clear()   # 前のカットのメッシュは消えている
    scene = bpy.context.scene
    c_ = st.bk["cuts"][cut]
    build_site(lay, c_.get("lighting") or st.bk.get("lighting"), bool(c_.get("door_closed")))
    plan = Plan(st, cut, sec)
    figs = {}
    previs_rig.RIGS.clear()
    FACES.clear()
    for who in plan.people:
        figs[who] = build_person(who, lay["people"][who]["h"], lay["people"][who].get("torch", False), lay["people"][who].get("sword", False))
    rigtracks = {}   # 関節の人形の姿勢（キーの姿勢を補間、進んだ道のり、回り続けた角度）
    for who in plan.people:
        if who in previs_rig.RIGS:
            keys = plan.tracks[who].k if who in plan.tracks else None
            rigtracks[who] = previs_rig.PoseTrack(keys, (lambda tt, w=who: plan.position(w, tt)), sec,
                                                  fallback=(lambda tt, w=who: plan.pose_name(w, tt)), who=who)
        if who not in plan.shown:   # 画に映さない人物は描かない（静止画と同じ）
            for o in [figs[who][0]] + list(figs[who][0].children_recursive):
                o.hide_render = True
    for who in (c_.get("ghost") or []):   # 幻影（A11 のメタリカ）: 半分透ける、青白く光る人形
        if who in figs:
            gm = beam_mat(f"{who}_ghost", (0.7, 0.85, 1.0), strength=1.0, alpha=0.45)
            for o in figs[who][0].children_recursive:
                if o.type == "MESH":
                    o.data.materials.clear()
                    o.data.materials.append(gm)
    # 隠す物（hide_props）と、床の下からせり上がって現れる物（reveal）。ピラーは守護者を倒すまで見えない（D-070、2026-11-01）
    def prop_objs(name):
        return [o for o in bpy.data.objects if o.name in (f"ENV_{name}", f"ENV_{name}_tip", f"LGT_{name}")]
    for name in (c_.get("hide_props", st.bk.get("hide_props")) or []):   # カットに無ければシーンの頭の hide_props
        for o in prop_objs(name):
            o.hide_render = True
    reveals = []
    for rv in ([c_["reveal"]] if isinstance(c_.get("reveal"), dict) else (c_.get("reveal") or [])):
        objs = prop_objs(rv["prop"])
        pr = lay["props"][rv["prop"]]
        sink = float(pr.get("h", 4.0)) + 1.2   # 床の下へ沈める深さ（先の円錐まで隠れる）
        base = {o.name: (o.location.z, o.data.energy if o.type == "LIGHT" else None) for o in objs}
        reveals.append((rv, objs, sink, base))
    creatures = {n: build_creature(n, tr) for n, tr in plan.creatures.items()}
    fxs = [(fx, build_fx(fx, i)) for i, fx in enumerate(plan.c.get("fx") or [])]
    lit_name = c_.get("lighting") or st.bk.get("lighting")
    amb = c_.get("ambient", True)
    style = (amb.get("style") if isinstance(amb, dict) else None) or AMBIENT.get((lay.get("id"), lit_name), AMBIENT.get(lay.get("id")))
    if RENDER.get("ambient", True) and RENDER["engine"] == "workbench" and amb is not False and style and (lay.get("id"), lit_name) not in [k for k, v in AMBIENT.items() if v is None]:
        (cx0, cy0, cz0), yaw0, pitch0, _l0 = plan.camera(0.0, plan.positions(0.0))   # カメラの前 4.5m を真ん中に（世界に止まっているので、カメラが動くと粒が流れて見える）
        dd = 4.5 * math.cos(pitch0)
        fxa = {"kind": "motes", "style": style, "box": AMBIENT_BOX.get(style, [8.0, 8.0, 3.0]),
               "at": [cx0 + math.sin(math.radians(yaw0)) * dd, cy0 - math.cos(math.radians(yaw0)) * dd, cz0 + 4.5 * math.sin(pitch0)]}
        if isinstance(amb, dict):
            fxa.update({k: v for k, v in amb.items() if k != "style"})
        fxs.append((fxa, build_fx(fxa, len(fxs))))
    broom = None
    if c_.get("broom"):   # ホウキ（A15）: broom の人物が ride のときだけ、体の下に描く（後ろへ長く伸ばし、ほかの人物が後ろに乗れる）
        bm = make_mat("broom_stick", (0.45, 0.3, 0.15))
        bb = make_mat("broom_brush", (0.85, 0.7, 0.35))
        broom = bpy.data.objects.new("BROOM", None)
        bpy.context.collection.objects.link(broom)
        L = float(c_.get("broom_len", 3.2))
        prim("cyl", bm, "broom_stick", broom, (0, 0.8 - L / 2, 0), (math.pi / 2, 0, 0), radius=0.035, depth=L)
        prim("cone", bb, "broom_brush", broom, (0, 0.8 - L - 0.3, 0), (-math.pi / 2, 0, 0), radius1=0.22, radius2=0.04, depth=0.7)
    bpy.ops.object.camera_add()
    cam = bpy.context.object
    cam.name = "CAM"
    cam.data.sensor_width = 36.0
    cam.data.sensor_fit = "HORIZONTAL"
    cam.data.clip_start, cam.data.clip_end = 0.05, 1000
    scene.camera = cam
    w, h = size
    setup_render(scene, w, h, samples, denoise)
    if RENDER["engine"] == "workbench":   # アニメの絵コンテに近い見た目で速く描く（tools/previs_look.py）
        import previs_look
        previs_look.apply(scene, (lay.get("lighting") or {}).get(c_.get("lighting") or st.bk.get("lighting")) or {})
    n = max(2, round(sec * fps))
    tw = previs_fx.TimeWarp(c_.get("timewarp"))   # 止め・スロー（2026-11-06）。キーの時刻は場面の時刻

    def where(at, t, z=None):
        """効果の置き場所（Blender の座標）。人物（体の 0.6 の高さ）・生き物（頭）・場所・[x, y]・[x, y, z]。"""
        if isinstance(at, (list, tuple)) and len(at) == 3:
            return Vector(bl((at[0], at[1]), at[2]))
        if isinstance(at, str) and at in plan.people:
            x, y = plan.position(at, t)
            zz = motion.ground_z(lay, x, y) + plan.height(at, t)
            return Vector(bl((x, y), zz + (lay["people"][at]["h"] * 0.6 if z is None else float(z))))
        if isinstance(at, str) and at in plan.creatures:
            hx, hy, hz = plan.creatures[at].head(t)
            return Vector(bl((hx, hy), hz + (0.0 if z is None else float(z))))
        x, y = st.place(at)
        return Vector(bl((x, y), motion.ground_z(lay, x, y) + (0.0 if z is None else float(z))))

    def project(at, t, z=None):
        v = world_to_camera_view(scene, cam, where(at, t, z))
        return (v.x, v.y) if v.z > 0 else None

    names = {w: [str(lay["people"][w].get("ja", "")).split("（")[0]] for w in plan.people}
    acting_ex = previs_face.acting_expr(str(plan.shot.get("shot", "")) + "。" + str(plan.shot.get("acting", "")), names)   # 画の名前（寄り・泣くメタリカ）と芝居の文から（その人の名前と同じ区切りの言葉。2026-11-07 に画の名前も）
    texts = previs_face._texts(st.dir)
    key_ex = {w: sorted((float(k.get("t", 0.0)), k["expr"]) for k in ks if isinstance(k, dict) and k.get("expr"))
              for w, ks in (plan.c.get("keys") or {}).items()}

    def face_state(who, t, T):
        """(表情, 口の開き)。keys の expr → 話している台詞の言葉 → 芝居の文 → neutral。"""
        sp = previs_face.speaking(st.dir, who, plan.tc0 + T)
        ex = None
        for kt, kx in key_ex.get(who, []):
            if kt <= t + 1e-6:
                ex = kx
        if ex is None and sp:
            ex = previs_face.expr_from_speech(texts.get(sp[0], ""))
        if ex is None:
            ex = acting_ex.get(who)
        if ex in ("angry", "annoyed") and sp and sp[1] > 0.5 and "！！" in texts.get(sp[0], ""):
            ex = "shout"
        return (ex or "neutral"), (sp[1] if sp else 0.0)

    FXCTX.clear()
    FXCTX.update({"cam": cam, "where": where, "project": project, "fps": fps,
                  "ground": lambda p: motion.ground_z(lay, p.x, -p.y)})
    trails = {}
    if c_.get("trail", True):
        for who, rgx in previs_rig.RIGS.items():
            if getattr(rgx, "tip", None) is not None and who in plan.shown:
                trails[who] = previs_fx.Trail(sys.modules[__name__], who, rgx)
    frames_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"previs_{cut}_", dir=work_root))
    checks = {}
    warnings = []
    cam_moves = bool(plan.c.get("cam_end"))   # staging.py はカメラ本体の移動を知らないので、終わりの比較はしない

    def pose_one(who, t, pos=None):
        """1人だけ時刻 t の姿勢にする（剣の軌跡で、少し前の剣の位置を取る）。"""
        pos = pos or plan.positions(t)
        b = plan.face(who, t, pos)
        x, y = pos[who]
        zz = motion.ground_z(lay, x, y) + plan.height(who, t)
        prm, dist, spd, sxa, sza = rigtracks[who].at(t)
        previs_rig.apply(previs_rig.RIGS[who], prm, b, x, y, zz, t, dist, spd, sxa, sza, gaze=plan.head_offset(who, t, pos, b), bl=bl)

    def pose(i):
        T = i / fps
        t = tw.scene_t(T)
        pos = plan.positions(t)
        for who, (root, head, arms) in figs.items():
            b = plan.face(who, t, pos)
            x, y = pos[who]
            zz = motion.ground_z(lay, x, y) + plan.height(who, t)
            if who in rigtracks:
                prm, dist, spd, sxa, sza = rigtracks[who].at(t)
                ex, mo = face_state(who, t, T) if who in FACES else ("neutral", 0.0)
                if who in FACES:
                    prm = previs_face.gesture(prm, who, t, mo)   # 話す身ぶり・呼吸（2026-11-07）
                previs_rig.apply(previs_rig.RIGS[who], prm, b, x, y, zz, t, dist, spd, sxa, sza,
                                 gaze=plan.head_offset(who, t, pos, b), bl=bl)
                if who in FACES:
                    previs_face.pose(FACES[who], ex, mo, previs_face.blink(who, t))
            else:
                apply_pose(root, head, arms, who, plan.pose_name(who, t), b, x, y, zz, t, lay)
                head.rotation_euler = (0, 0, -math.radians(plan.head_offset(who, t, pos, b)))
            if broom is not None and who == c_["broom"]:
                riding = plan.pose_name(who, t) == "ride"
                for o in [broom] + list(broom.children_recursive):
                    o.hide_render = not riding
                broom.location = bl((x, y), zz + 0.32)
                broom.rotation_euler = (0, 0, -math.radians(b))
        for name, parts in creatures.items():
            pose_creature(parts, plan.creatures[name], t)
        for who, tr in trails.items():   # 剣の軌跡（少し前の剣の位置から帯を作り、最後に今の姿勢へ戻す）。走る・歩く間は描かない
            prm_now = rigtracks[who].params(t)
            tr.update(t, lambda tt, w=who: pose_one(w, tt), active=max(float(prm_now.get("run", 0.0)), float(prm_now.get("walk", 0.0))) < 0.5)
        for rv, objs, sink, base in reveals:   # t の間に床の下からせり上がる（光も強くなる）
            t0, t1 = rv["t"]
            u = 0.0 if t < t0 else min(1.0, (t - t0) / max(1e-6, t1 - t0))
            u = u * u * (3 - 2 * u)
            for o in objs:
                z0, en = base[o.name]
                o.location.z = z0 - sink * (1.0 - u)
                if en is not None:
                    o.data.energy = en * u
        (cx, cy, cz), yaw, pitch, lens = plan.camera(t, pos)
        hy, hp = tw.hold_shake(T)   # 止めている間の小さな揺れ
        yaw += hy
        pitch += math.radians(hp)
        cam.location = bl((cx, cy), cz)
        roll = plan.roll(t)   # 画面を傾ける（ダッチアングル、cam_keys の roll 度。A07 の戦い）
        if roll:
            m = (Matrix.Rotation(-math.radians(yaw), 4, "Z") @ Matrix.Rotation(math.pi / 2 + pitch, 4, "X")
                 @ Matrix.Rotation(math.radians(roll), 4, "Z"))
            cam.rotation_euler = m.to_euler("XYZ")
        else:
            cam.rotation_euler = (math.pi / 2 + pitch, 0, -math.radians(yaw))
        cam.data.lens = lens
        bpy.context.view_layer.update()
        if RENDER["engine"] == "workbench" and RENDER.get("dof", True):   # 寄り・胸上は顔に焦点を合わせて背景をぼかす（2026-11-07）
            import previs_audit
            lk, fr = previs_audit._cam_state(plan, t)
            frs = str(fr or "").split("→")[-1]
            if lk in previs_rig.RIGS and lk in plan.shown and ("寄り" in frs or "胸上" in frs or "アップ" in frs):
                rgk = previs_rig.RIGS[lk]
                hc = rgk.head.matrix_world @ Vector((0, 0, rgk.hr))
                cam.data.dof.use_dof = True
                cam.data.dof.focus_distance = max(0.2, (hc - cam.matrix_world.translation).length)
                cam.data.dof.aperture_fstop = 1.4 if ("寄り" in frs or "アップ" in frs) else 2.2
            else:
                cam.data.dof.use_dof = False
        for fx, objs in fxs:   # 効果はカメラを置いた後（カメラへ向ける閃光があるため）
            pose_fx(fx, objs, t, pos, st.place)
        bpy.context.view_layer.update()
        return pos

    if audit is not None:   # 構図の測定（--audit）: 0.75 秒ごとに動かして、描かずに顔の位置・大きさ・隠れを測る
        idx = sorted({min(n - 1, round(k * 0.75 * fps)) for k in range(int(sec / 0.75) + 1)} | {n - 1})
    elif motion_step:   # 動きの確認: motion_step 秒ごとのコマだけ描く（動画は作らない）
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
        if audit is not None:
            import previs_audit
            audit.append(previs_audit.measure(scene, cam, figs, plan, lay, tw.scene_t(i / fps), i / fps))
            continue
        scene.render.filepath = str(frames_dir / f"{i:04d}.png")
        if RENDER["engine"] == "workbench":
            previs_look.sync()   # コマごとに変わる霧・光の透け方
        bpy.ops.render.render(write_still=True)
        previs_fx.post(frames_dir / f"{i:04d}.png", plan.c.get("fx") or [], tw.scene_t(i / fps), i, FXCTX)   # 集中線・反転など
        if RENDER["engine"] == "workbench" and RENDER.get("grade", True):
            previs_look.grade(frames_dir / f"{i:04d}.png", (lay.get("lighting") or {}).get(lit_name) or {})   # 色調と周辺減光（2026-11-07）
    if audit is not None:
        return None, frames_dir, n, {"warnings": warnings, "frames": list(idx)}
    out_dir.mkdir(parents=True, exist_ok=True)
    checks["warnings"] = warnings
    checks["frames"] = list(idx)
    # 真っ黒なコマ（カメラが壁・岩・草むらなどの中に入った）。A14-007・008 で見落とした（2026-11-01）
    from PIL import Image, ImageStat
    checks["dark"] = [i for i in idx if ImageStat.Stat(Image.open(frames_dir / f"{i:04d}.png").convert("L").resize((64, 36))).mean[0] < 8]
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
    ap.add_argument("--engine", choices=("workbench", "cycles"), default="workbench",
                    help="描き方。workbench（既定、1コマ約0.3秒、輪郭線と影。EGL が要る）か cycles（前の描き方、1コマ約6秒）")
    ap.add_argument("--preview", action="store_true", help="始め・中ほど・終わりの3枚だけ描いて確認画像を作る（動画は作らない）")
    ap.add_argument("--motion", type=float, nargs="?", const=0.5, help="動きの確認（D-060）: この秒ごとのコマだけ描いて <カット>_motion.jpg を作る（既定 0.5秒、動画は作らない）")
    ap.add_argument("--audit", action="store_true", help="構図の測定（2026-11-07）: 描かずに、0.75秒ごとの各人物の顔の位置・大きさ・隠れを gen/framing.yaml に書く（tools/framing.py で読む）")
    a = ap.parse_args()
    RENDER["engine"] = a.engine
    st = staging.Stage(a.scene_dir)
    lay = st.lay
    scene_id = pathlib.Path(a.scene_dir).name
    out_dir = ROOT / (a.out or f"previs/{scene_id}")
    size = tuple(int(v) for v in a.size.split("x"))
    work = pathlib.Path(tempfile.mkdtemp(prefix="previs_"))
    cuts = a.cuts or st.order
    if a.audit:
        import previs_audit
        try:
            previs_audit.run(st, lay, a.scene_dir, cuts, a.fps, size, a.samples, out_dir, work, render_cut)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return
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
            dark = checks.pop("dark", [])
            if dark:
                print(f"  警告: 真っ黒なコマ {len(dark)}枚（フレーム {dark[0]}〜{dark[-1]}）。カメラが壁や物の中に入っていないか確かめる")
            for which, res in checks.items():
                diff = max((abs(r["staging_s"] - r["blender_s"]) for r in res), default=0)
                print(f"  {which}: 画面の左右（staging.py と Blender の差の最大 {diff:.2f}）",
                      " ".join(f"{r['who']}:{r['staging_s']}/{r['blender_s']}" for r in res))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
