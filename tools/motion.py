#!/usr/bin/env python3
"""動きのキー（D-060）。blocking.yaml のカットに書いた、時刻ごとの人物・カメラ・生き物・効果の動きを読む。

tools/staging.py（略図・静止画の指示）は始めと終わりだけを使い、tools/previs.py（下絵動画）は時刻ごとに使う。
bpy を使わないので staging.py からも読める。

書き方（t はカットの頭からの秒。キーの間は ease で補う）:

  keys:                                   # 人物
    luckiny:
      - {t: 0.0, at: lk_a, pose: run}                      # at: 場所名か [x, y]
      - {t: 1.2, at: lk_b, face: 240, pose: wave, ease: out}
      - {t: 2.6, at: lk_c, z: 0.4, pose: jump}             # z: 地面からの高さ m（埋まるのは負）
    face を省くと、動いている間は進む向き、止まっていれば前の向き。"@人物" はその人物を見続ける。
    at を省くと前の位置のまま（姿勢や向きだけ変える）。

  cam_keys:                               # カメラ
    - {t: 0.0, cam: [x, y, 高さ], look: luckiny, look_h: 1.0, lens: 35, frame: 全身}
    - {t: 1.5, cam: {follow: luckiny, offset: [-2, 3], z: 1.2}, look: luckiny, lens: 28, ease: linear}
    cam の follow は、その時刻の人物の位置から offset（東, 南 m）ずらした所。look は人物名・場所名・[x, y]。
    look_h は見る点の高さ m（省くと人物は背の 0.75、場所は 0）。

  shake: [{t: [2.4, 3.0], amp: 1.5}]       # カメラの揺れ（度）。手持ちの揺れは amp 0.4 くらい

  creatures:                              # 生き物（砂食みなど。体の節が頭の通った道をたどる）
    sand-eater:
      len: 8.0                            # 体長 m
      r: 0.7                              # 太さ（半径）m
      keys: [{t: 0.0, at: [x, y], z: -3}, {t: 1.5, at: [x, y], z: 4, ease: linear}, {t: 3.0, at: [x, y], z: -3}]
    z が負の所は砂の下（地面に隠れる）。浅い所（0〜-2.5m）は砂の上に影と盛り上がりを描く。
      after: A07-007                      # 前のカットの続き（前のカットの通り道を体の形に使う。最初のキーは前のカットの終わりと同じ所に）

  fx:                                     # 効果
    - {kind: plume, t: [0.8, 2.5], at: [x, y], r: 2.0, h: 5.0}   # 砂の噴き上げ（at は人物名でも）
    - {kind: ring, t: [0.0, 4.0], at: [x, y], r: 3.0, n: 3}      # 砂の波紋
    shape: fall を付けた plume は、始めが大きく、だんだん消える（舞った砂が落ちる）

  pose: stand（既定）・walk・run・crouch・kneel・sit・lie・wave・point・arms_up・shake・jump
  ease: inout（既定。滑らかに始まり止まる）・linear（一定の速さ）・in（だんだん速く）・out（だんだん遅く）・step（次のキーで切り替わる）
"""
import math

POSE_POSTURE = {"crouch": "crouching", "crouching": "crouching", "kneel": "kneeling", "kneeling": "kneeling",
                "sit": "sitting", "sitting": "sitting", "lie": "lying", "lying": "lying"}


def ease(name, u):
    u = min(1.0, max(0.0, u))
    if name == "linear":
        return u
    if name == "in":
        return u * u
    if name == "out":
        return 1 - (1 - u) * (1 - u)
    if name == "step":
        return 0.0 if u < 1.0 else 1.0
    return u * u * (3 - 2 * u)


def bearing_of(v):
    return math.degrees(math.atan2(v[0], -v[1])) % 360


def ang_diff(a, b):
    return (a - b + 180) % 360 - 180


def lerp(a, b, u):
    return a + (b - a) * u


def segment(keys, t):
    """t を挟む2つのキーと、その間の進み具合（ease を掛けたもの）。"""
    if t <= keys[0]["t"]:
        return keys[0], keys[0], 0.0
    for a, b in zip(keys, keys[1:]):
        if t <= b["t"]:
            u = (t - a["t"]) / max(1e-6, b["t"] - a["t"])
            return a, b, ease(b.get("ease", "inout"), u)
    return keys[-1], keys[-1], 1.0


def sorted_keys(ks):
    return sorted(ks or [], key=lambda k: float(k["t"]))


def filled(keys, place):
    """at・z・pose を省いたキーに、前のキーの値を入れる（位置は座標にする）。"""
    out, prev = [], {}
    for k in sorted_keys(keys):
        k2 = dict(k)
        if "at" in k2:
            k2["at"] = tuple(place(k2["at"]))
        elif "at" in prev:
            k2["at"] = prev["at"]
        k2.setdefault("z", prev.get("z", 0.0))
        k2.setdefault("pose", prev.get("pose", "stand"))
        out.append(k2)
        prev = k2
    return out


# ---------- staging.py が使う（始めと終わりだけ） ----------

def expand_keys(c):
    """keys から set（始めの位置と向き）・move（終わりと途中の位置）・posture・posture_end を作る。元の項目は残す。"""
    if not c or not c.get("keys"):
        return c
    c = dict(c)
    st, mv = dict(c.get("set") or {}), dict(c.get("move") or {})
    p0, p1 = dict(c.get("posture") or {}), dict(c.get("posture_end") or {})
    for who, ks in c["keys"].items():
        ks = sorted_keys(ks)
        a, b = ks[0], ks[-1]
        s = {k: a[k2] for k, k2 in (("at", "at"), ("face", "face")) if k2 in a}
        if s:
            st[who] = s
        if len(ks) > 1:
            last_at = next((k["at"] for k in reversed(ks) if "at" in k), None)
            m = {}
            if last_at is not None:
                m["to"] = last_at
            last_face = next((k["face"] for k in reversed(ks) if "face" in k), None)
            if last_face is not None and last_face != a.get("face"):
                m["face"] = last_face
            vias = [k["at"] for k in ks[1:-1] if "at" in k]
            if vias and last_at is not None:
                m["via"] = vias
            m["t"] = [float(a["t"]), float(b["t"])]
            mv[who] = m
        pa = POSE_POSTURE.get(a.get("pose"))
        last_pose = next((k["pose"] for k in reversed(ks) if "pose" in k), a.get("pose"))
        pb = POSE_POSTURE.get(last_pose)
        if pa:
            p0[who] = pa
        if pa or pb:
            p1[who] = pb
    c["set"], c["move"] = st, mv
    if p0:
        c["posture"] = p0
    if p1:
        c["posture_end"] = p1
    return c


def ground_z(lay, x, y):
    """地面の高さ m（layout の砂丘 dune* の上なら、その高さ）。下絵で人物とカメラを砂丘の上に乗せる。"""
    z = 0.0
    for name, pr in (lay.get("props") or {}).items():
        if not name.startswith("dune") or "at" not in pr:
            continue
        r = float(pr.get("r", 10))
        ry = r * float(pr.get("ry", 0.6))
        u = ((x - pr["at"][0]) / r) ** 2 + ((y - pr["at"][1]) / ry) ** 2
        if u < 1.0:
            z = max(z, float(pr.get("h", 4)) * math.sqrt(1.0 - u))
    return z


def resolve_cam(cam, pos, base_z=None):
    """cam_keys の cam（[x, y, 高さ] か follow）を座標にする。follow は人物（pos）か生き物の頭。
    follow の z は、人物ならその足元の地面から、生き物なら頭の高さからの高さ。base_z(名前, x, y) が土台の高さを返す。"""
    if isinstance(cam, dict):
        p = pos[cam["follow"]]
        off = cam.get("offset", [0.0, 0.0])
        x, y = p[0] + off[0], p[1] + off[1]
        z0 = base_z(cam["follow"], x, y) if base_z else 0.0
        return [x, y, z0 + float(cam.get("z", 1.2))]
    return [float(v) for v in cam]


def creature_spec(cuts, cut, name, depth=0):
    """生き物の creatures の設定。after: <前のカット> があれば、前のカットの頭の通り道を負の時刻で前に足す
    （体は頭の通った道をたどるので、カットの始めの体の形が前のカットの終わりの続きになる）。
    前のカットの最後のキーは、このカットの最初のキーと同じ所とみなして捨てる。"""
    spec = dict(cuts[cut]["creatures"][name])
    prev = spec.get("after")
    if not prev or depth > 20 or name not in (cuts.get(prev, {}).get("creatures") or {}):
        return spec
    ps = creature_spec(cuts, prev, name, depth + 1)
    pk = sorted_keys(ps["keys"])
    own = sorted_keys(spec["keys"])
    shift = float(own[0]["t"]) - float(pk[-1]["t"])
    spec["keys"] = [{**k, "t": float(k["t"]) + shift} for k in pk[:-1]] + own
    return spec


def creature_head(c, name, t, place):
    """カットの creatures の頭の位置（x, y, z）。"""
    ks = sorted_keys(c["creatures"][name]["keys"])
    ks = [{**k, "at": tuple(place(k["at"])), "z": float(k.get("z", 0.0))} for k in ks]
    a, b, u = segment(ks, t)
    return (lerp(a["at"][0], b["at"][0], u), lerp(a["at"][1], b["at"][1], u), lerp(a["z"], b["z"], u))


def expand_cam(c, pos_start, pos_end, place=None):
    """cam_keys から、静止画と略図が読む cam・cam_end・look・look_end・look_h・lens・lens_end・frame・frame_end・cam_t を作る。
    follow が生き物のときは、その時刻の頭の位置から（z は頭の高さから）。"""
    ck = sorted_keys(c.get("cam_keys"))
    if not ck:
        return c
    c = dict(c)
    a, b = ck[0], ck[-1]
    creatures = c.get("creatures") or {}

    def heads(t):
        return {n: creature_head(c, n, t, place) for n in creatures}

    def resolve(cam, pos, t):
        hd = heads(t)
        pos2 = {**pos, **{n: h[:2] for n, h in hd.items()}}
        return resolve_cam(cam, pos2, (lambda n, x, y: hd[n][2] if n in hd else 0.0))
    c["cam"] = resolve(a["cam"], pos_start, float(a["t"]))
    c["look"] = a["look"]
    c["lens"] = a.get("lens", c.get("lens", 35))
    if a.get("look_h") is not None:
        c["look_h"] = a["look_h"]
    if a.get("frame"):
        c["frame"] = a["frame"]
    c.setdefault("frame", "全身")
    if len(ck) > 1:
        last = {k: next((x[k] for x in reversed(ck) if k in x), None) for k in ("cam", "look", "lens", "look_h", "frame")}
        if last["cam"] is not None:
            ce = resolve(last["cam"], pos_end, float(b["t"]))
            if ce != c["cam"]:
                c["cam_end"] = ce
        if last["look"] is not None and last["look"] != c["look"]:
            c["look_end"] = last["look"]
        if last["lens"] is not None and last["lens"] != c["lens"]:
            c["lens_end"] = last["lens"]
        if last["look_h"] is not None and last["look_h"] != c.get("look_h"):
            c["look_h_end"] = last["look_h"]
        if last["frame"] and last["frame"] != c["frame"]:
            c["frame_end"] = last["frame"]
        c["cam_t"] = [float(a["t"]), float(b["t"])]
    return c


# ---------- previs.py が使う（時刻ごと） ----------

class PersonTrack:
    """1人の時刻ごとの位置・高さ・向き・姿勢。"""

    def __init__(self, keys, place):
        self.k = filled(keys, place)

    def at(self, t):
        a, b, u = segment(self.k, t)
        p = (lerp(a["at"][0], b["at"][0], u), lerp(a["at"][1], b["at"][1], u))
        z = lerp(a["z"], b["z"], u)
        if b.get("arc") and a is not b:   # 跳ぶ: キーの間で上に弧を描く（arc は弧の高さ m）
            z += b["arc"] * math.sin(math.pi * u)
        pose = b["pose"] if (a is not b and u > 0.5) else a["pose"]
        if b.get("ease") == "step":
            pose = a["pose"] if u < 1.0 else b["pose"]
        return p, z, pose

    def moving_dir(self, t):
        a, b, _ = segment(self.k, t)
        if a is b or math.dist(a["at"], b["at"]) < 0.3:
            return None
        return bearing_of((b["at"][0] - a["at"][0], b["at"][1] - a["at"][1]))

    def face_key(self, t):
        """その時刻に効いている向きの指定（キーの face）。動いていて face がなければ進む向き。"""
        a, b, u = segment(self.k, t)
        fa = next((k["face"] for k in reversed(self.k) if k["t"] <= a["t"] and "face" in k), None)
        fb = b.get("face")
        md = self.moving_dir(t)
        if md is not None and "face" not in b:
            return ("dir", md, None, 0.0)
        if fb is not None and a is not b:
            return ("lerp", fa if fa is not None else md, fb, u)
        return ("hold", fa if fa is not None else md, None, 0.0)


class CamTrack:
    """カメラの時刻ごとの位置・見る点・レンズ。"""

    def __init__(self, keys):
        self.k = sorted_keys(keys)
        # 省いた項目は前のキーの値
        prev = {}
        for k in self.k:
            for name in ("cam", "look", "lens", "look_h"):
                if name not in k and name in prev:
                    k[name] = prev[name]
            prev = k

    def at(self, t, pos, look_pt, look_height, base_z=None):
        a, b, u = segment(self.k, t)
        ca, cb = resolve_cam(a["cam"], pos, base_z), resolve_cam(b["cam"], pos, base_z)
        cam = [lerp(x, y, u) for x, y in zip(ca, cb)]
        la, lb = look_pt(a["look"], pos), look_pt(b["look"], pos)
        look = (lerp(la[0], lb[0], u), lerp(la[1], lb[1], u))
        ha = a.get("look_h") if a.get("look_h") is not None else look_height(a["look"])
        hb = b.get("look_h") if b.get("look_h") is not None else look_height(b["look"])
        lens = lerp(float(a.get("lens", 35)), float(b.get("lens", a.get("lens", 35))), u)
        return cam, look, lerp(ha, hb, u), lens


def shake_at(shakes, t):
    """揺れ（度）。いくつかの正弦の和で、決まった形の揺れにする。"""
    yaw = pitch = 0.0
    for s in shakes or []:
        t0, t1 = s["t"]
        if t0 <= t <= t1:
            env = math.sin(math.pi * (t - t0) / max(1e-6, t1 - t0))
            amp = float(s.get("amp", 1.0)) * env
            yaw += amp * (0.6 * math.sin(23.0 * t) + 0.4 * math.sin(37.0 * t + 1.3))
            pitch += amp * (0.6 * math.sin(29.0 * t + 0.7) + 0.4 * math.sin(17.0 * t + 2.1))
    return yaw, pitch


class CreatureTrack:
    """生き物の頭の通り道と、そこをたどる体の節の位置（3次元。z は地面からの高さ）。"""

    def __init__(self, spec, place, step=0.02):
        self.len = float(spec.get("len", 8.0))
        self.r = float(spec.get("r", 0.7))
        self.segs = int(spec.get("segs", 16))
        ks = sorted_keys(spec["keys"])
        self.k = [{**k, "at": tuple(place(k["at"])), "z": float(k.get("z", 0.0))} for k in ks]
        self.t0, self.t1 = self.k[0]["t"], self.k[-1]["t"]
        # 頭の通り道を細かくたどり、通った長さで引けるようにする
        self.path, self.s_at = [], []
        n = max(2, int((self.t1 - self.t0) / step) + 1)
        s = 0.0
        prev = None
        for i in range(n + 1):
            t = self.t0 + (self.t1 - self.t0) * i / n
            p = self.head(t)
            if prev is not None:
                s += math.dist(p, prev)
            self.path.append((t, s, p))
            prev = p
        # 始めの向きを後ろへのばす（始めから体が全部あるように）
        p0, p1 = self.path[0][2], self.path[min(5, len(self.path) - 1)][2]
        d = [p1[i] - p0[i] for i in range(3)]
        L = math.sqrt(sum(v * v for v in d)) or 1.0
        self.back = tuple(v / L for v in d)

    def head(self, t):
        a, b, u = segment(self.k, t)
        return (lerp(a["at"][0], b["at"][0], u), lerp(a["at"][1], b["at"][1], u), lerp(a["z"], b["z"], u))

    def s_of(self, t):
        if t <= self.t0:
            return 0.0
        for (ta, sa, _), (tb, sb, _) in zip(self.path, self.path[1:]):
            if t <= tb:
                return lerp(sa, sb, (t - ta) / max(1e-9, tb - ta))
        return self.path[-1][1]

    def point_at_s(self, s):
        if s <= 0:
            p0 = self.path[0][2]
            return tuple(p0[i] + self.back[i] * s for i in range(3))
        for (_, sa, pa), (_, sb, pb) in zip(self.path, self.path[1:]):
            if s <= sb:
                u = (s - sa) / max(1e-9, sb - sa)
                return tuple(lerp(pa[i], pb[i], u) for i in range(3))
        # 終わりの先は、最後の向きにのばす
        pa, pb = self.path[-2][2], self.path[-1][2]
        d = [pb[i] - pa[i] for i in range(3)]
        L = math.sqrt(sum(v * v for v in d)) or 1.0
        return tuple(pb[i] + d[i] / L * (s - self.path[-1][1]) for i in range(3))

    def body(self, t):
        """頭から尾までの節の位置と半径のリスト。"""
        s = self.s_of(t)
        out = []
        for i in range(self.segs):
            f = i / max(1, self.segs - 1)
            r = self.r * (1.0 - 0.55 * f) * (1.15 if i == 0 else 1.0)
            out.append((self.point_at_s(s - self.len * f), r))
        return out


def fx_amount(fx, t):
    """効果の強さ 0〜1（始めに大きくなり、終わりに小さくなる）。"""
    t0, t1 = fx["t"]
    if t < t0 or t > t1:
        return 0.0
    u = (t - t0) / max(1e-6, t1 - t0)
    if fx.get("shape") == "fall":   # 舞った砂が落ちていく: 始めが大きく、だんだん消える
        return 1.0 - u
    return math.sin(math.pi * min(1.0, u * 1.6)) if u < 0.5 else math.sin(math.pi * u)
