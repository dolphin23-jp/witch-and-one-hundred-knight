#!/usr/bin/env python3
"""関節のある人形と、姿勢の補間（2026-11-06 の下絵のアップグレード）。tools/previs.py から呼ぶ。

これまでの人形は「円柱の胴・球の頭・棒の腕」で脚がなく、しゃがむのは体を縦に縮め、姿勢はキーの中ほどで急に切り替わっていた。
アクション（斬る・跳ぶ・着地・宙返り・吹き飛ぶ）が読み取れないので、次のように作り直した。

人形: 腰（pelvis）→ 背骨（spine）→ 胸 → 首 → 頭、肩 → 上腕 → ひじ → 前腕 → 手（剣は右手に）、股 → もも → ひざ → すね → 足首 → 足。
  形は円柱と球だけ（顔と衣装は写さない。D-055）。魔女たちはスカートの円すい、百騎兵は短い脚と大きな手（籠手）。
  脚は地面に着いている姿勢では足の位置から逆算する（IK）。足が地面に埋まったり浮いたりしない。跳んでいる間は角度で決める。
  歩く・走るは、進んだ道のりから足の運びを決める（足が地面の上を滑らない）。止まっていれば立ち姿になる（その場で足踏みしない）。

姿勢（blocking.yaml の keys の pose）:
  名前（POSES の一覧）か、名前に上書きを足した辞書 {base: ready, r_arm: [120, 10, 0], lean: 20}。
  キーとキーの間は、前のキーの姿勢を保ち、次のキーの時刻の手前 blend 秒（既定 0.2秒、キーに blend: 0.06 のように書く）で次の姿勢へ移る。
  斬る・突くは「振りかぶり → 振り下ろし → 振り抜き」のように姿勢のキーを並べ、振り下ろしのキーを短い blend にすると鋭く見える。
  pease: in（だんだん速く。打ち込み）・out（だんだん遅く。着地の沈み）・linear・inout（既定）で、姿勢の移り方を変えられる。

姿勢の数（POSES の各項目。省くと 0）:
  drop      腰を落とす（股の高さに対する割合。0.3 で構え、0.5 でひざをつく）
  lean      上体を前へ倒す（度）。twist 上体を左へひねる（度）、tilt 上体を右へ傾ける（度）
  body      体ごと前へ回す（度。90 でうつ伏せ、-90 で仰向け、宙返りの途中など）。roll 体ごと横へ、spin 体ごと左へ回す（度）
  head      [うなずく（下が正）, 左を向く, 傾ける]（度）
  r_arm / l_arm  [前へ上げる, 外へ上げる, 内へ振る]（度。腕を下ろした形から）。r_el / l_el ひじを曲げる（度）
  r_ft / l_ft    足の位置のずらし [外, 前, 上]（股の高さに対する割合）。地面に着いた脚はこの位置へ IK で伸びる
  air       0〜1。1 で脚を角度（r_leg・l_leg [ももを前へ, 外へ, ひざを曲げる]）で決める（跳んでいる・宙で回る）
  grip      剣の向き（度。0 で前腕に直角で前、-90 で前腕の延長＝突き）
  walk / run     歩く・走るの重み（進んだ道のりから足を運ぶ）
  osc       揺れの種類と重み {wave: 1}（手を振る・踊る・体を振る・跳ねる・むせる・泣く・なでる・身ぶり）
  spin_x / spin_z  回り続ける速さ（回転/秒）。宙返り（x）とこま回り（z）
"""
import math

import bpy
from mathutils import Euler, Matrix, Vector

# 関節の人形にしない（人の形でないもの）。前の人形のまま
SKIP = {"night-train", "karakuri-doll", "marny", "marika-mouse", "usamaru", "scarecocco", "aromani-gel",
        "heinz-carriage", "heavy-fruit", "perfume", "sand-eater"}

# 人物ごとの体つき。hip: 股の高さ（首までの高さに対する割合）、hand: 手の大きさ、leg: 脚の太さ、
# skirt: スカートの丈（股の高さに対する割合。0 でなし）、flare: スカートの裾の広がり
STYLE = {
    "hundred-knight": {"hip": 0.40, "hand": 1.15, "leg": 1.35, "foot": 1.4, "arm_len": 0.85},
    "metallica": {"skirt": 0.62, "flare": 1.7},
    "metallica-giant": {"skirt": 0.62, "flare": 1.7},
    "bisco": {"hip": 0.5},
    "arlecchino": {"hip": 0.48, "hand": 1.3},
    "luckiny": {"skirt": 0.45, "flare": 1.5},
    "tito": {"skirt": 0.75, "flare": 1.5},
    "purple-peel": {"skirt": 0.95, "flare": 1.9},
    "lily": {"skirt": 0.7, "flare": 1.7},
    "rabi": {"skirt": 0.5, "flare": 1.6},
    "teressa": {"skirt": 0.8, "flare": 1.6},
    "teressa-swamp": {"skirt": 0.8, "flare": 1.6},
    "berda": {"skirt": 0.95, "flare": 1.7},
    "blind-witch": {"skirt": 1.0, "flare": 1.5},
    "guide-witch": {"skirt": 1.0, "flare": 1.5},
    "nezaria": {"skirt": 1.0, "flare": 1.6},
    "rubens": {"skirt": 0.7, "flare": 1.5},
    "mitten": {"hip": 0.42, "leg": 0.9},
    "apprentice-a": {"skirt": 0.6, "flare": 1.6},
    "apprentice-b": {"skirt": 0.6, "flare": 1.6},
    "apprentice-c": {"skirt": 0.6, "flare": 1.6},
    "apprentice-d": {"skirt": 0.6, "flare": 1.6},
    "forest-servant": {"hip": 0.36, "leg": 1.5},
    "water-merchant": {"hip": 0.45},
}

BLEND = 0.2   # 姿勢が移る時間の既定（秒）

# 人物ごとの姿勢の上書き。剣を持つ百騎兵は、立つ・歩くで剣先を前へ低く、走るで剣を後ろへ流す
OVERRIDE = {
    "hundred-knight": {
        "stand": {"r_arm": [20, 10, 0], "r_el": 55, "grip": -82},
        "idle": {"r_arm": [20, 10, 0], "r_el": 55, "grip": -82},
        "walk": {"r_arm": [20, 10, 0], "r_el": 55, "grip": -82},
        "run": {"r_arm": [-40, 18, 0], "r_el": 15, "grip": -150, "lean": 22},
        "look_up": {"r_arm": [20, 10, 0], "r_el": 55, "grip": -82},
        "look_down": {"r_arm": [20, 10, 0], "r_el": 55, "grip": -82},
        "nod": {"r_arm": [25, 10, 0], "r_el": 55, "grip": -95},
    },
}


def smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def ease(name, u):
    u = min(1.0, max(0.0, u))
    if name == "linear":
        return u
    if name == "in":
        return u * u * u
    if name == "out":
        return 1 - (1 - u) ** 3
    return u * u * (3 - 2 * u)


# ---------- 姿勢の一覧 ----------
# 角度は度、足のずらしは股の高さに対する割合。r_ = 右（剣の側）、l_ = 左
POSES = {
    "stand": {"r_arm": [4, 6, 0], "l_arm": [4, 6, 0], "r_el": 8, "l_el": 8},
    "idle": {"r_arm": [4, 6, 0], "l_arm": [4, 6, 0], "r_el": 10, "l_el": 10},
    "walk": {"walk": 1.0, "r_arm": [0, 5, 0], "l_arm": [0, 5, 0], "r_el": 15, "l_el": 15, "lean": 3},
    "run": {"run": 1.0, "r_arm": [0, 8, 0], "l_arm": [0, 8, 0], "r_el": 80, "l_el": 80, "lean": 14},
    "sprint": {"run": 1.25, "r_arm": [-10, 8, 0], "l_arm": [-10, 8, 0], "r_el": 70, "l_el": 70, "lean": 24},
    # 剣を後ろへ流して走る（百騎兵の突進）
    "dash": {"run": 1.3, "r_arm": [-45, 20, 0], "r_el": 20, "l_arm": [-30, 15, 0], "l_el": 30, "lean": 30, "grip": -140},
    "crouch": {"drop": 0.42, "lean": 28, "r_ft": [0.06, 0.12, 0], "l_ft": [0.06, -0.05, 0], "r_arm": [40, 12, 0], "l_arm": [40, 12, 0], "r_el": 40, "l_el": 40},
    "kneel": {"drop": 0.5, "lean": 8, "r_ft": [0.05, 0.45, 0], "l_ft": [0, -0.48, 0.04], "r_arm": [25, 8, 0], "l_arm": [10, 8, 0], "r_el": 30, "l_el": 20},
    "kneel_hold": {"drop": 0.5, "lean": 15, "r_ft": [0.05, 0.45, 0], "l_ft": [0, -0.48, 0.04], "r_arm": [70, 0, 10], "l_arm": [70, 0, 10], "r_el": 40, "l_el": 40},
    "sit": {"drop": 0.42, "lean": -6, "r_ft": [0.04, 0.48, 0], "l_ft": [0.04, 0.48, 0], "r_arm": [30, 10, 10], "l_arm": [30, 10, 10], "r_el": 40, "l_el": 40},
    "fall": {"drop": 0.82, "lean": -22, "air": 1.0, "r_leg": [80, 10, -25], "l_leg": [75, 10, -40], "r_arm": [-35, 25, 0], "l_arm": [-35, 25, 0], "r_el": 10, "l_el": 10},
    "lie": {"body": 90, "air": 1.0, "r_leg": [0, 4, -5], "l_leg": [0, 4, -5], "r_arm": [160, 18, 0], "l_arm": [150, 18, 0], "r_el": 15, "l_el": 15, "head": [-35, 0, 0]},
    "lie_back": {"body": -90, "air": 1.0, "r_leg": [0, 6, -5], "l_leg": [0, 6, -5], "r_arm": [10, 40, 0], "l_arm": [10, 40, 0], "r_el": 10, "l_el": 10},
    "bow": {"lean": 38, "drop": 0.03, "r_arm": [10, 0, 15], "l_arm": [10, 0, 15], "r_el": 20, "l_el": 20, "head": [15, 0, 0]},
    "nod": {"lean": 22, "head": [30, 0, 0], "r_arm": [8, 6, 0], "l_arm": [8, 6, 0], "r_el": 15, "l_el": 15},
    "look_up": {"lean": -10, "head": [-35, 0, 0], "r_arm": [4, 8, 0], "l_arm": [4, 8, 0], "r_el": 10, "l_el": 10},
    "look_down": {"lean": 8, "head": [32, 0, 0], "r_arm": [6, 6, 0], "l_arm": [6, 6, 0], "r_el": 15, "l_el": 15},
    "proud": {"lean": -8, "head": [-10, 0, 0], "r_arm": [-15, 38, 0], "l_arm": [-15, 38, 0], "r_el": 105, "l_el": 105, "r_ft": [0.08, 0, 0], "l_ft": [0.08, 0, 0]},
    "cross": {"lean": -6, "head": [-5, 0, 0], "r_arm": [55, 8, 55], "l_arm": [50, 8, 55], "r_el": 95, "l_el": 95},
    "cover": {"lean": 10, "head": [10, 0, 0], "r_arm": [70, 5, 35], "r_el": 120, "l_arm": [10, 6, 0], "l_el": 20},
    "lean": {"lean": 24, "head": [-12, 0, 0], "r_arm": [-10, 10, 0], "l_arm": [-10, 10, 0], "r_el": 20, "l_el": 20, "r_ft": [0, 0.15, 0]},
    "point": {"r_arm": [88, 5, 0], "r_el": 0, "l_arm": [6, 8, 0], "l_el": 15, "lean": 4, "twist": -8},
    "gesture": {"r_arm": [35, 55, 0], "r_el": 25, "l_arm": [8, 6, 0], "l_el": 15, "osc": {"gesture": 1.0}},
    "wave": {"r_arm": [0, 150, 0], "r_el": 25, "l_arm": [4, 6, 0], "l_el": 10, "osc": {"wave": 1.0}},
    "arms_up": {"r_arm": [10, 160, 0], "l_arm": [10, 160, 0], "r_el": 15, "l_el": 15, "head": [-15, 0, 0], "lean": -5},
    "hold": {"lean": 8, "r_arm": [70, 0, 15], "l_arm": [70, 0, 15], "r_el": 30, "l_el": 30},
    "stroke": {"lean": 22, "drop": 0.08, "r_arm": [95, 10, 0], "r_el": 30, "osc": {"stroke": 1.0}},
    "horn": {"head": [-10, 0, 0], "r_arm": [110, 0, 30], "l_arm": [110, 0, 30], "r_el": 100, "l_el": 100},
    "cough": {"lean": 28, "r_arm": [70, 5, 35], "r_el": 125, "l_arm": [20, 6, 10], "l_el": 60, "osc": {"cough": 1.0}},
    "cry": {"drop": 0.5, "lean": 22, "head": [25, 0, 0], "r_ft": [0.05, 0.45, 0], "l_ft": [0, -0.48, 0.04], "r_arm": [120, 0, 30], "l_arm": [120, 0, 30], "r_el": 130, "l_el": 130, "osc": {"cry": 1.0}},
    "shake": {"osc": {"shake": 1.0}, "r_arm": [0, 25, 0], "l_arm": [0, 25, 0], "r_el": 10, "l_el": 10},
    "hop": {"osc": {"hop": 1.0}, "r_arm": [10, 130, 0], "l_arm": [10, 130, 0], "r_el": 20, "l_el": 20},
    "dance": {"osc": {"dance": 1.0}, "r_arm": [40, 90, 0], "l_arm": [40, 90, 0], "r_el": 30, "l_el": 30},
    "ride": {"drop": 0.5, "lean": 8, "air": 1.0, "r_leg": [70, 18, -60], "l_leg": [70, 18, -60], "r_arm": [55, 0, 20], "l_arm": [55, 0, 20], "r_el": 30, "l_el": 30},
    # ---- 戦い ----
    "ready": {"drop": 0.22, "lean": 12, "twist": -18, "r_ft": [0.12, 0.38, 0], "l_ft": [-0.05, -0.32, 0],
              "r_arm": [45, 15, 10], "r_el": 70, "l_arm": [35, 25, 0], "l_el": 45, "grip": -50, "head": [-8, 18, 0]},
    # 低く構えて剣を後ろへ流す（突進の前、力をためる）
    "low_ready": {"drop": 0.34, "lean": 22, "twist": -12, "r_ft": [0.12, 0.42, 0], "l_ft": [-0.05, -0.4, 0],
                  "r_arm": [10, 30, -15], "r_el": 25, "l_arm": [45, 30, 0], "l_el": 50, "grip": -150, "head": [-18, 12, 0]},
    # 剣を地面に突き立てて踏みとどまる
    "brace": {"drop": 0.45, "lean": 26, "r_ft": [0.12, 0.3, 0], "l_ft": [-0.02, -0.38, 0], "r_arm": [70, 6, 6], "r_el": 25,
              "l_arm": [65, 18, 12], "l_el": 40, "grip": -170, "head": [-12, 0, 0]},
    "guard": {"drop": 0.25, "lean": 14, "twist": -5, "r_ft": [0.12, 0.3, 0], "l_ft": [-0.05, -0.3, 0],
              "r_arm": [85, -5, 35], "r_el": 70, "l_arm": [60, 10, 30], "l_el": 80, "grip": -60, "head": [-6, 5, 0]},
    # 縦に斬る: 振りかぶり → 振り下ろし → 振り抜き
    "slash_up": {"drop": 0.12, "lean": -12, "twist": 15, "r_ft": [0.1, 0.25, 0], "l_ft": [-0.05, -0.3, 0],
                 "r_arm": [168, 18, 0], "r_el": 35, "l_arm": [110, 25, 0], "l_el": 45, "grip": -75, "head": [-15, 0, 0]},
    "slash_down": {"drop": 0.38, "lean": 30, "twist": -12, "r_ft": [0.12, 0.6, 0], "l_ft": [-0.05, -0.45, 0],
                   "r_arm": [72, 8, 5], "r_el": 0, "l_arm": [-20, 30, 0], "l_el": 20, "grip": -75, "head": [5, 0, 0]},
    "slash_follow": {"drop": 0.32, "lean": 26, "twist": -28, "r_ft": [0.12, 0.55, 0], "l_ft": [-0.05, -0.45, 0],
                     "r_arm": [10, 28, 20], "r_el": 10, "l_arm": [-30, 35, 0], "l_el": 20, "grip": -75},
    # 横に斬る: 右へ引き絞る → 左へ振り抜く
    "swing_back": {"drop": 0.22, "lean": 10, "twist": 45, "r_ft": [0.15, 0.25, 0], "l_ft": [-0.08, -0.25, 0],
                   "r_arm": [85, 75, -20], "r_el": 30, "l_arm": [50, 10, 40], "l_el": 60, "grip": -60, "head": [-5, -30, 0]},
    "swing_through": {"drop": 0.3, "lean": 18, "twist": -55, "r_ft": [0.15, 0.45, 0], "l_ft": [-0.08, -0.3, 0],
                      "r_arm": [85, 10, 85], "r_el": 5, "l_arm": [30, 50, 0], "l_el": 30, "grip": -60, "head": [0, 20, 0]},
    # 突く: 引く → 突き出す
    "thrust_back": {"drop": 0.25, "lean": 6, "twist": 30, "r_ft": [0.1, 0.3, 0], "l_ft": [-0.05, -0.3, 0],
                    "r_arm": [40, 25, -10], "r_el": 110, "l_arm": [70, 10, 30], "l_el": 30, "grip": -90},
    "thrust": {"drop": 0.4, "lean": 28, "twist": -20, "r_ft": [0.12, 0.75, 0], "l_ft": [-0.05, -0.55, 0],
               "r_arm": [112, 0, 8], "r_el": 0, "l_arm": [-30, 35, 0], "l_el": 15, "grip": -90},
    # 下から斬り上げる
    "upper_low": {"drop": 0.4, "lean": 30, "twist": 25, "r_ft": [0.12, 0.35, 0], "l_ft": [-0.05, -0.35, 0],
                  "r_arm": [-25, 25, 0], "r_el": 10, "l_arm": [40, 20, 0], "l_el": 40, "grip": -40},
    "upper_high": {"drop": 0.05, "lean": -15, "twist": -15, "r_ft": [0.12, 0.3, 0.1], "l_ft": [-0.05, -0.2, 0],
                   "r_arm": [175, 10, 0], "r_el": 0, "l_arm": [-30, 30, 0], "l_el": 20, "grip": -60, "head": [-25, 0, 0]},
    # 回転斬り（spin_z と合わせる）
    "spin_slash": {"drop": 0.3, "lean": 12, "r_arm": [85, 80, 0], "r_el": 0, "l_arm": [70, 70, 0], "l_el": 10, "grip": -80,
                   "r_ft": [0.2, 0.1, 0], "l_ft": [0.2, -0.1, 0]},
    # 跳ぶ: 溜め → 伸び上がり → 抱え込み → 落ちる → 着地
    "jump_crouch": {"drop": 0.48, "lean": 35, "r_ft": [0.08, 0.1, 0], "l_ft": [0.08, -0.05, 0],
                    "r_arm": [-45, 15, 0], "l_arm": [-45, 15, 0], "r_el": 20, "l_el": 20, "head": [-10, 0, 0]},
    "jump_up": {"air": 1.0, "lean": -6, "r_leg": [-8, 6, -15], "l_leg": [10, 6, -30], "r_arm": [160, 20, 0], "l_arm": [150, 20, 0],
                "r_el": 15, "l_el": 15, "head": [-20, 0, 0]},
    "jump_tuck": {"air": 1.0, "lean": 25, "r_leg": [100, 10, -120], "l_leg": [95, 10, -125], "r_arm": [70, 25, 20], "l_arm": [70, 25, 20],
                  "r_el": 70, "l_el": 70},
    "jump_fall": {"air": 1.0, "lean": 5, "r_leg": [25, 8, -20], "l_leg": [5, 8, -35], "r_arm": [100, 60, 0], "l_arm": [100, 60, 0],
                  "r_el": 20, "l_el": 20, "head": [15, 0, 0]},
    "land": {"drop": 0.5, "lean": 32, "r_ft": [0.15, 0.15, 0], "l_ft": [0.15, -0.1, 0], "r_arm": [40, 45, 0], "l_arm": [30, 50, 0],
             "r_el": 20, "l_el": 20, "head": [-15, 0, 0]},
    # ヒーロー着地: 片ひざ・片手を地面に・剣を横へ
    "hero_land": {"drop": 0.62, "lean": 40, "r_ft": [0.18, 0.4, 0], "l_ft": [0.0, -0.5, 0.03], "r_arm": [30, 70, -20], "r_el": 10,
                  "l_arm": [75, 10, 10], "l_el": 10, "grip": -70, "head": [-30, 0, 0]},
    "jump": {"air": 1.0, "lean": 10, "r_leg": [60, 10, -80], "l_leg": [50, 10, -90], "r_arm": [140, 30, 0], "l_arm": [140, 30, 0],
             "r_el": 20, "l_el": 20},
    # 宙で回る（とんぼ返り）。spin_x で回り続ける
    "tumble": {"air": 1.0, "lean": 30, "spin_x": 1.3, "r_leg": [110, 10, -130], "l_leg": [110, 10, -130], "r_arm": [70, 20, 25], "l_arm": [70, 20, 25],
               "r_el": 80, "l_el": 80},
    "flip": {"air": 1.0, "body": 180, "spin": 180, "drop": -0.1, "lean": 20, "r_leg": [80, 15, -100], "l_leg": [80, 15, -100],
             "r_arm": [60, 30, 0], "l_arm": [60, 30, 0], "r_el": 50, "l_el": 50, "head": [-30, 0, 0]},
    "dive": {"air": 1.0, "body": 168, "spin": 180, "r_leg": [-10, 5, -20], "l_leg": [-15, 5, -30], "r_arm": [178, 4, 0], "l_arm": [175, 8, 0],
             "r_el": 0, "l_el": 0, "grip": -90},
    "spin": {"spin_z": 2.0},
    # 後ろへの宙返り
    "backflip": {"air": 1.0, "lean": 15, "spin_x": -1.7, "r_leg": [100, 10, -120], "l_leg": [100, 10, -120], "r_arm": [60, 25, 15], "l_arm": [60, 25, 15],
                 "r_el": 70, "l_el": 70, "grip": -100},
    # 飛びつく（体を水平に伸ばして両手を前へ。スケアコッコに飛びかかる、A10）
    "tackle": {"air": 1.0, "body": 75, "r_leg": [-15, 8, -20], "l_leg": [-25, 8, -30], "r_arm": [170, 15, 0], "l_arm": [170, 15, 0], "r_el": 5, "l_el": 5,
               "head": [-40, 0, 0], "grip": -120},
    # 剣を突き立てたまましがみつく（頭の横で、剣は前へ）
    "cling": {"air": 1.0, "lean": 10, "r_leg": [60, 20, -90], "l_leg": [30, 20, -60], "r_arm": [95, 5, 5], "r_el": 15, "l_arm": [90, 10, 15], "l_el": 25,
              "grip": -90, "head": [-10, 0, 0]},
    # 宙で回り抜けて、上から斬り下ろす（空中の振りかぶり）
    "air_slash_up": {"air": 1.0, "lean": -15, "r_leg": [40, 10, -90], "l_leg": [20, 10, -60], "r_arm": [180, 15, 0], "r_el": 60,
                     "l_arm": [120, 30, 0], "l_el": 40, "grip": -20, "head": [-10, 0, 0]},
    "air_slash_down": {"air": 1.0, "lean": 40, "r_leg": [70, 10, -100], "l_leg": [10, 10, -40], "r_arm": [20, 10, 0], "r_el": 0,
                       "l_arm": [-20, 40, 0], "l_el": 20, "grip": -20},
    # 吹き飛ばされる・よろける・倒れる・起き上がる
    "hit": {"air": 1.0, "lean": -35, "body": -20, "r_leg": [45, 15, -60], "l_leg": [30, 15, -40], "r_arm": [60, 70, 0], "l_arm": [60, 70, 0],
            "r_el": 30, "l_el": 30, "head": [-30, 0, 0]},
    "stagger": {"drop": 0.15, "lean": -18, "r_ft": [0.15, -0.3, 0], "l_ft": [0.0, 0.25, 0], "r_arm": [20, 55, 0], "l_arm": [30, 50, 0],
                "r_el": 30, "l_el": 30, "head": [-15, 10, 0]},
    "getup": {"drop": 0.55, "lean": 45, "r_ft": [0.12, 0.25, 0], "l_ft": [0.05, -0.3, 0.02], "r_arm": [20, 10, 0], "l_arm": [70, 10, 0],
              "r_el": 10, "l_el": 10},
    # 魔法: 片手を前へ・両手を掲げる
    "cast": {"lean": 8, "twist": -10, "r_ft": [0.1, 0.3, 0], "l_ft": [-0.05, -0.25, 0], "drop": 0.12, "r_arm": [95, 10, 0], "r_el": 5,
             "l_arm": [20, 20, 0], "l_el": 30, "head": [-5, 0, 0]},
    "cast_up": {"lean": -12, "r_arm": [175, 15, 0], "r_el": 5, "l_arm": [30, 30, 0], "l_el": 30, "head": [-30, 0, 0]},
    "stomp": {"drop": 0.15, "lean": 8, "r_ft": [0.1, 0.25, 0.5], "l_ft": [0, -0.1, 0], "r_arm": [40, 50, 0], "l_arm": [40, 50, 0],
              "r_el": 60, "l_el": 60},
    # 這う（ミトン、A33）
    "crawl": {"body": 70, "drop": 0.55, "air": 1.0, "r_leg": [60, 15, -80], "l_leg": [60, 15, -80], "r_arm": [140, 15, 0], "l_arm": [140, 15, 0],
              "r_el": 50, "l_el": 50, "head": [-40, 0, 0]},
}

# 前の人形の姿勢名（姿勢の一覧にないもの）→ 新しい姿勢
ALIAS = {"crouching": "crouch", "kneeling": "kneel", "sitting": "sit", "lying": "lie", "standing": "stand", "slash": "slash_down"}

ARM_KEYS = ("r_arm", "l_arm", "r_ft", "l_ft", "r_leg", "l_leg", "head")


def params_of(p, who=None):
    """姿勢の指定（名前か辞書）→ 数の辞書。who があれば人物ごとの上書き（OVERRIDE）も当てる。"""
    if p is None:
        p = "stand"
    if isinstance(p, str):
        name = ALIAS.get(p, p)
        out = dict(POSES.get(name, POSES["stand"]))
        out.update((OVERRIDE.get(who) or {}).get(name, {}))
        return out
    base = params_of(p.get("base", "stand"), who)
    for k, v in p.items():
        if k == "base":
            continue
        if k == "osc":
            base["osc"] = dict(v)
        else:
            base[k] = v
    return base


ZERO3 = [0.0, 0.0, 0.0]


def blend(p, q, u):
    """2つの姿勢の間（u 0〜1）。"""
    if u <= 0.0:
        return p
    if u >= 1.0:
        return q
    out = {}
    for k in set(p) | set(q):
        a, b = p.get(k), q.get(k)
        if k == "osc":
            oa, ob = a or {}, b or {}
            out[k] = {n: (oa.get(n, 0.0) * (1 - u) + ob.get(n, 0.0) * u) for n in set(oa) | set(ob)}
            continue
        if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
            a = list(a) if a is not None else ZERO3
            b = list(b) if b is not None else ZERO3
            out[k] = [x + (y - x) * u for x, y in zip(a, b)]
        else:
            a = float(a or 0.0)
            b = float(b or 0.0)
            out[k] = a + (b - a) * u
    return out


class PoseTrack:
    """1人の、時刻ごとの姿勢の数（キーの姿勢を、次のキーの手前 blend 秒で移す）と、進んだ道のり・回り続けた角度。"""

    def __init__(self, keys, pos_fn, sec, fallback=None, who=None):
        self.who = who
        self.k = [dict(k) for k in keys] if keys else []
        for k in self.k:
            k["_p"] = params_of(k.get("pose"), who)
        self.fallback = fallback   # キーのない人物: t → 姿勢名
        # 進んだ道のりと速さ（足の運び）、回り続けた角度を、細かい刻みで先に数えておく
        self.dt = 1.0 / 96
        n = int(max(sec, 0.1) / self.dt) + 3
        self.ts = [i * self.dt - self.dt for i in range(n)]
        self.dist, self.speed, self.sx, self.sz = [0.0], [0.0], [0.0], [0.0]
        prev = pos_fn(self.ts[0])
        for i in range(1, n):
            t = self.ts[i]
            p = pos_fn(t)
            d = math.dist(p, prev)
            self.dist.append(self.dist[-1] + d)
            self.speed.append(d / self.dt)
            q = self.params(t, raw=True)
            for arr, key in ((self.sx, "spin_x"), (self.sz, "spin_z")):
                rate = float(q.get(key, 0.0))
                v = arr[-1] + rate * 360.0 * self.dt
                if abs(rate) < 0.05:   # 回り終えたら、近い一回りの倍数へ（宙返りの後に体が傾いたままにならないように）
                    v += (round(v / 360.0) * 360.0 - v) * min(1.0, self.dt / 0.08)
                arr.append(v)
            prev = p
        # 速さはならす（キーの切り替わりでがたつかないように）
        sm = []
        w = 6
        for i in range(n):
            a, b = max(0, i - w), min(n, i + w + 1)
            sm.append(sum(self.speed[a:b]) / (b - a))
        self.speed = sm

    def _lookup(self, arr, t):
        x = (t - self.ts[0]) / self.dt
        i = int(max(0, min(len(arr) - 2, math.floor(x))))
        f = min(1.0, max(0.0, x - i))
        return arr[i] + (arr[i + 1] - arr[i]) * f

    def params(self, t, raw=False):
        if not self.k:
            return params_of(self.fallback(t) if self.fallback else "stand", self.who)
        ks = self.k
        if t <= ks[0]["t"]:
            return ks[0]["_p"]
        for a, b in zip(ks, ks[1:]):
            if t <= b["t"]:
                span = max(1e-6, b["t"] - a["t"])
                w = min(float(b.get("blend", BLEND)), span)
                t0 = b["t"] - w
                if t <= t0:
                    return a["_p"]
                u = ease(b.get("pease", "inout"), (t - t0) / max(1e-6, w))
                return blend(a["_p"], b["_p"], u)
        return ks[-1]["_p"]

    def at(self, t):
        """(姿勢の数, 進んだ道のり m, 速さ m/秒, 宙返りの角度, こま回りの角度)"""
        return (self.params(t), self._lookup(self.dist, t), self._lookup(self.speed, t),
                self._lookup(self.sx, t), self._lookup(self.sz, t))


# ---------- 人形を作る ----------

class Rig:
    pass


def _empty(name, parent, loc=(0, 0, 0)):
    o = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(o)
    o.parent = parent
    o.location = loc
    o.rotation_mode = "XYZ"
    return o


def build(P, who, h, sh, col, m_body, m_dark):
    """関節の人形の体・腕・脚。(root, head, arms, rig) を返す。頭の飾り（帽子など）は previs.py の build_person が head に付ける。"""
    st = STYLE.get(who, {})
    giant = 8.1 if who == "metallica-giant" else 1.0
    r, hr, arm = sh["r"], sh["hr"], sh["arm"]
    body_h = max(0.2, h - 2 * hr)   # 首（頭の付け根）の高さ
    hip_h = body_h * st.get("hip", 0.47)
    foot_h = 0.035 * h if who != "hundred-knight" else 0.05
    L1 = L2 = max(0.05, (hip_h - foot_h) * 0.5)
    torso = body_h - hip_h
    al = body_h * 0.6 * st.get("arm_len", 1.0)
    ua, fa = al * 0.48, al * 0.44
    leg_r = arm * 1.25 * st.get("leg", 1.0)
    hand_r = arm * 1.35 * st.get("hand", 1.0)
    hip_w = r * 0.5
    rg = Rig()
    rg.who, rg.h, rg.r, rg.hr, rg.body_h, rg.hip_h, rg.L1, rg.L2, rg.foot_h = who, h, r, hr, body_h, hip_h, L1, L2, foot_h
    rg.hip_w, rg.torso, rg.ua, rg.fa = hip_w, torso, ua, fa
    root = bpy.data.objects.new(f"FIG_{who}", None)
    bpy.context.collection.objects.link(root)
    root["arms"] = 1
    root["rig2"] = 1
    pelvis = _empty(f"PELVIS_{who}", root, (0, 0, hip_h))
    pelvis.rotation_mode = "QUATERNION"
    # 腰
    P.prim("cyl", m_dark, f"{who}_hips", pelvis, (0, 0, torso * 0.06), radius=r * 0.95, depth=torso * 0.3)
    spine = _empty(f"SPINE_{who}", pelvis, (0, 0, torso * 0.18))
    # 胸（上がやや細い円すい台）
    P.prim("cone", m_body, f"{who}_body", spine, (0, 0, (body_h - hip_h - torso * 0.18) / 2 - torso * 0.02),
           radius1=r, radius2=r * 0.86, depth=body_h - hip_h - torso * 0.14)
    neck = _empty(f"NECK_{who}", spine, (0, 0, body_h - hip_h - torso * 0.18))
    head = _empty(f"HEAD_{who}", neck, (0, 0, 0))
    if st.get("skirt"):
        sk_len = hip_h * st["skirt"]
        P.prim("cone", m_body, f"{who}_skirt", pelvis, (0, 0, torso * 0.12 - sk_len / 2 - torso * 0.05),
               radius1=r * st.get("flare", 1.6), radius2=r * 1.02, depth=sk_len + torso * 0.1, vertices=20)
    arms, elbows, hands = {}, {}, {}
    sh_z = body_h * 0.92 - hip_h - torso * 0.18
    for sx in (-1, 1):
        shoulder = _empty(f"SHOULDER_{who}{sx}", spine, (sx * (r + arm * 0.9), 0, sh_z))
        P.prim("sphere", m_dark, f"{who}_shoulderball{sx}", shoulder, (0, 0, 0), radius=arm * 1.25)
        P.prim("cyl", m_dark, f"{who}_arm{sx}", shoulder, (0, 0, -ua / 2), radius=arm, depth=ua)
        elbow = _empty(f"ELBOW_{who}{sx}", shoulder, (0, 0, -ua))
        P.prim("cyl", m_dark, f"{who}_fore{sx}", elbow, (0, 0, -fa / 2), radius=arm * 0.9, depth=fa)
        hand = _empty(f"HAND_{who}{sx}", elbow, (0, 0, -fa))
        P.prim("sphere", m_dark, f"{who}_hand{sx}", hand, (0, 0, -hand_r * 0.55), radius=hand_r, scale=(1.0, 1.1, 1.0))
        arms[sx], elbows[sx], hands[sx] = shoulder, elbow, hand
    hips, knees, feet = {}, {}, {}
    m_leg = m_dark
    shoe = P.make_mat(f"{who}_shoe", tuple(c * 0.3 for c in col))
    for sx in (-1, 1):
        hip = _empty(f"HIP_{who}{sx}", pelvis, (sx * hip_w, 0, 0))
        P.prim("cyl", m_leg, f"{who}_thigh{sx}", hip, (0, 0, -L1 / 2), radius=leg_r, depth=L1)
        knee = _empty(f"KNEE_{who}{sx}", hip, (0, 0, -L1))
        P.prim("sphere", m_leg, f"{who}_kneeball{sx}", knee, (0, 0, 0), radius=leg_r * 0.95)
        P.prim("cyl", m_leg, f"{who}_shin{sx}", knee, (0, 0, -L2 / 2), radius=leg_r * 0.85, depth=L2)
        ankle = _empty(f"ANKLE_{who}{sx}", knee, (0, 0, -L2))
        fl = max(0.06, h * 0.075) * st.get("foot", 1.0)
        P.prim("cube", shoe, f"{who}_foot{sx}", ankle, (0, fl * 0.3, -foot_h * 0.5), size=2.0,
               scale=(leg_r * 1.05, fl * 0.62, foot_h * 0.55))
        hips[sx], knees[sx], feet[sx] = hip, knee, ankle
    rg.root, rg.pelvis, rg.spine, rg.neck, rg.head = root, pelvis, spine, neck, head
    rg.arms, rg.elbows, rg.hands, rg.hips, rg.knees, rg.feet = arms, elbows, hands, hips, knees, feet
    rg.giant = giant
    root["_rig_id"] = who
    RIGS[who] = rg
    return root, head, arms, rg


RIGS = {}


def attach_sword(P, rg, who):
    """右手に剣（百騎兵）。刃は手の GRIP の +Y の向き。"""
    blade = P.make_mat(f"{who}_blade", (0.86, 0.9, 0.97), rough=0.3)
    hilt = P.make_mat(f"{who}_hilt", (0.25, 0.2, 0.15))
    gold = P.make_mat(f"{who}_guard", (0.85, 0.68, 0.3), rough=0.35)
    grip = _empty(f"GRIP_{who}", rg.hands[1], (0, 0, -0.06))
    P.prim("cyl", hilt, f"{who}_hilt", grip, (0, -0.04, 0), (math.pi / 2, 0, 0), radius=0.028, depth=0.2)
    P.prim("cube", gold, f"{who}_guard", grip, (0, 0.07, 0), size=2.0, scale=(0.13, 0.025, 0.035))
    L = 0.82
    P.prim("cube", blade, f"{who}_blade", grip, (0, 0.09 + L / 2, 0), size=2.0, scale=(0.055, L / 2, 0.012))
    tip = _empty(f"TIP_{who}", grip, (0, 0.09 + L, 0))
    mid = _empty(f"BLADEMID_{who}", grip, (0, 0.09 + L * 0.45, 0))
    rg.grip, rg.tip, rg.blade_mid = grip, tip, mid
    rg.root["sword"] = 1


def attach_torch(P, rg, who):
    fire = P.make_mat(f"{who}_torch", (1.0, 0.55, 0.15), emit=4.0)
    stick = P.make_mat(f"{who}_stick", (0.3, 0.2, 0.12))
    g = _empty(f"TORCH_{who}", rg.hands[1], (0, 0, -0.04))
    P.prim("cyl", stick, f"{who}_torch_stick", g, (0, 0, 0.12), radius=0.025, depth=0.45)
    f = P.prim("sphere", fire, f"{who}_torch_fire", g, (0, 0, 0.38), radius=0.08, scale=(1, 1, 1.5))
    bpy.ops.object.light_add(type="POINT", location=(0, 0, 0))
    lt = bpy.context.object
    lt.name = f"LGT_{who}_torch"
    lt.data.energy, lt.data.color, lt.data.shadow_soft_size = 45.0, (1.0, 0.55, 0.2), 0.1
    lt.parent = f
    rg.torch = g
    rg.root["torch"] = 1


def reparent_decor(rg):
    """飾り（帽子・鞄・尻尾・襟・羽・杖・ブーツなど）を、前の人形の付け方（root・腕）から、新しい関節へ付け替える。
    見た目の位置は変えない（作った直後の、立ってまっすぐな姿勢で付け替える）。"""
    bpy.context.view_layer.update()
    keep = set()
    for o in [rg.pelvis] + list(rg.pelvis.children_recursive):
        keep.add(o.name)

    def move(o, parent):
        mw = o.matrix_world.copy()
        o.parent = parent
        o.matrix_parent_inverse = parent.matrix_world.inverted()
        o.matrix_world = mw

    for o in list(rg.root.children):
        if o.name in keep or o is rg.pelvis:
            continue
        if any(k in o.name for k in ("mist", "fog", "puddle")):   # 足元の瘴気・霧・溜まりは根元のまま（previs_council の霧の隠し方が根元からの位置を使う）
            continue
        z = o.matrix_world.translation.z - rg.root.matrix_world.translation.z
        x = (rg.root.matrix_world.inverted() @ o.matrix_world.translation).x
        if z < rg.hip_h * 0.3 and abs(x) > rg.hip_w * 0.4:
            move(o, rg.feet[1 if x > 0 else -1])
        elif z < rg.hip_h + rg.torso * 0.2:
            move(o, rg.pelvis)
        else:
            move(o, rg.spine)
    # 髪（三つ編み・長い髪）は上の端を支点にして、いつも下へ垂れる（お辞儀で棒のように突き出ないように）
    rg.dangle = []
    for o in list(rg.head.children):
        if o.type == "MESH" and any(k in o.name for k in ("braid", "_hair")) and o.dimensions.z > rg.hr * 1.5:
            top = o.matrix_world @ Vector((0.0, 0.0, max(v.co.z for v in o.data.vertices)))
            piv = _empty(f"DANGLE_{o.name}", rg.head)
            piv.matrix_world = Matrix.Translation(top)
            move(o, piv)
            piv.rotation_mode = "QUATERNION"
            rg.dangle.append(piv)
    # 腕に付いていた物（本・杖・手の飾り）は手へ
    for sx in (-1, 1):
        sh = rg.arms[sx]
        for o in list(sh.children):
            if o.name.startswith((f"{rg.who}_arm", f"{rg.who}_shoulderball")) or o is rg.elbows[sx]:
                continue
            move(o, rg.hands[sx])


# ---------- 姿勢を当てる ----------

def _leg_ik(rg, sx, target):
    """股から足首の目標（骨盤の中の座標）へ、もも・すねの角度。ひざは前へ曲がる。"""
    hip = Vector((sx * rg.hip_w, 0.0, 0.0))
    v = Vector(target) - hip
    L1, L2 = rg.L1, rg.L2
    d = max(abs(L1 - L2) + 1e-3, min(L1 + L2 - 1e-4, v.length))
    a = math.atan2(-v.x, -v.z) if (abs(v.x) + abs(v.z)) > 1e-6 else 0.0
    side = math.sqrt(v.x * v.x + v.z * v.z)
    phi = math.atan2(v.y, side)
    alpha = math.acos(max(-1.0, min(1.0, (L1 * L1 + d * d - L2 * L2) / (2 * L1 * d))))
    beta = math.acos(max(-1.0, min(1.0, (L1 * L1 + L2 * L2 - d * d) / (2 * L1 * L2))))
    return phi + alpha, a, -(math.pi - beta)


def apply(rg, prm, bearing, x, y, z, t, dist=0.0, speed=0.0, spin_x=0.0, spin_z=0.0, gaze=0.0, bl=None, ground=None):
    """姿勢の数を人形に当てる。bearing は体の向き（北 0・東 90）、(x, y, z) は足元の位置（layout の座標と高さ）。"""
    g = rg.giant
    hip_h, r = rg.hip_h, rg.r
    walk_w = float(prm.get("walk", 0.0))
    run_w = float(prm.get("run", 0.0))
    osc = prm.get("osc") or {}
    # 歩く・走る: 進んだ道のりから足を運ぶ。止まっていれば立ち姿へ
    loco = max(walk_w, run_w)
    moving = 0.0
    if loco > 0:
        nominal = (0.9 if run_w < walk_w else 3.2) * (rg.h / 1.6) ** 0.5
        moving = smooth((speed / max(0.05, nominal) - 0.08) / 0.35) * min(1.0, loco)
    stride = hip_h * (1.7 + 1.4 * min(1.0, run_w))   # 2歩の長さ
    ph = 2 * math.pi * dist / max(0.05, stride)
    rf = list(prm.get("r_ft") or ZERO3)
    lf = list(prm.get("l_ft") or ZERO3)
    drop = float(prm.get("drop", 0.0))
    lean = float(prm.get("lean", 0.0))
    twist = float(prm.get("twist", 0.0))
    tilt = float(prm.get("tilt", 0.0))
    ra = list(prm.get("r_arm") or ZERO3)
    la = list(prm.get("l_arm") or ZERO3)
    re_, le_ = float(prm.get("r_el", 0.0)), float(prm.get("l_el", 0.0))
    head = list(prm.get("head") or ZERO3)
    if getattr(rg, "torch", None) is not None and not prm.get("torch_free"):   # 松明は頭の上に掲げて持つ
        ra, re_ = [150.0, 8.0, 0.0], 25.0
    lift_z = 0.0
    if moving > 0:
        rw = min(1.0, run_w)
        amp = (0.32 + 0.3 * rw) * moving       # 足の前後（股の高さに対する割合）
        lift = (0.12 + 0.22 * rw) * moving
        for ft, phase in ((rf, ph), (lf, ph + math.pi)):
            ft[1] += amp * math.sin(phase)
            ft[2] += lift * max(0.0, math.cos(phase))
        swing = (22 + 30 * rw) * moving
        ra[0] += swing * math.sin(ph + math.pi) * (0.25 if getattr(rg, "tip", None) is not None else 1.0)   # 剣を持つ腕は小さく振る
        la[0] += swing * math.sin(ph)
        drop += (0.04 + 0.05 * rw) * moving * (1 - abs(math.cos(ph)))
        lift_z = (0.05 * rw) * moving * hip_h * abs(math.sin(ph))   # 走りの浮き
        twist += (6 + 6 * rw) * moving * math.sin(ph)
    # 揺れ
    if osc:
        w = osc.get("wave", 0.0)
        if w:
            ra[1] += w * 18 * math.sin(2 * math.pi * 2.0 * t)
            re_ += w * 20 * math.sin(2 * math.pi * 2.0 * t + 1.0)
        w = osc.get("gesture", 0.0)
        if w:
            ra[1] += w * 18 * math.sin(2 * math.pi * 0.6 * t)
            twist += w * 6 * math.sin(2 * math.pi * 0.6 * t)
        w = osc.get("stroke", 0.0)
        if w:
            ra[0] += w * 10 * math.sin(2 * math.pi * 1.2 * t)
        w = osc.get("cough", 0.0)
        if w:
            lean += w * 6 * math.sin(2 * math.pi * 4 * t)
        w = osc.get("cry", 0.0)
        if w:
            lean += w * 3 * math.sin(2 * math.pi * 1.5 * t)
            head[0] += w * 4 * math.sin(2 * math.pi * 3 * t)
        w = osc.get("shake", 0.0)
        if w:
            twist += w * 22 * math.sin(2 * math.pi * 6 * t)
            tilt += w * 10 * math.sin(2 * math.pi * 6 * t + 0.6)
        w = osc.get("hop", 0.0)
        if w:
            lift_z += w * abs(math.sin(2 * math.pi * 2.2 * t)) * 0.12 * rg.h
            drop += w * 0.12 * (1 - abs(math.sin(2 * math.pi * 2.2 * t)))
        w = osc.get("dance", 0.0)
        if w:
            s1 = math.sin(2 * math.pi * 1.6 * t)
            lift_z += w * abs(s1) * 0.06 * rg.h
            twist += w * 30 * math.sin(2 * math.pi * 0.8 * t)
            lean += w * 6 * s1
            ra[1] += w * 55 * math.sin(2 * math.pi * 0.8 * t)
            la[1] += w * 55 * math.sin(2 * math.pi * 0.8 * t + math.pi)
            rf[2] += w * 0.25 * max(0.0, math.sin(2 * math.pi * 0.8 * t))
            lf[2] += w * 0.25 * max(0.0, -math.sin(2 * math.pi * 0.8 * t))
    else:
        # 立っているときの息づかい（ごく小さく）
        lean += 0.8 * math.sin(2 * math.pi * 0.25 * t)
    air = max(0.0, min(1.0, float(prm.get("air", 0.0))))
    body = float(prm.get("body", 0.0)) + spin_x
    roll = float(prm.get("roll", 0.0))
    spin = float(prm.get("spin", 0.0)) + spin_z
    # 根元（足元の位置と向き）
    loc = bl((x, y), z) if bl else (x, -y, z)
    rg.root.location = loc
    rg.root.rotation_euler = (0.0, 0.0, -math.radians(bearing))
    rg.root.scale = (1.0, 1.0, 1.0)
    # 骨盤: 高さ（腰を落とす）と、体ごとの回転（宙返り・うつ伏せ）
    pz = hip_h * (1.0 - drop) + lift_z
    br = math.radians(body)
    lie_k = smooth((abs(math.sin(br)) - 0.85) / 0.15) * air if z <= 0.05 else 0.0
    off_y = 0.0
    if lie_k > 0:   # 横たわる: 骨盤を体の厚みの高さに下ろし、体の真ん中を足元の位置に
        pz = pz + (r * 1.05 - pz) * lie_k
        off_y = -0.5 * (rg.h - 2 * hip_h) * math.sin(br) * lie_k
    rot = Matrix.Rotation(math.radians(spin), 4, "Z") @ Matrix.Rotation(-br, 4, "X") @ Matrix.Rotation(math.radians(roll), 4, "Y")
    rg.pelvis.rotation_quaternion = rot.to_quaternion()
    rg.pelvis.location = (0.0, off_y, pz)
    # 背骨・首・頭
    rg.spine.rotation_euler = (-math.radians(lean), math.radians(tilt), math.radians(twist))
    rg.neck.rotation_euler = (0.0, 0.0, 0.0)
    rg.head.rotation_euler = (-math.radians(head[0]), math.radians(head[2] if len(head) > 2 else 0.0),
                              math.radians(head[1]) - math.radians(gaze))
    # 腕
    for sx, a, el in ((1, ra, re_), (-1, la, le_)):
        fwd, out, tw = math.radians(a[0]), math.radians(a[1]), math.radians(a[2] if len(a) > 2 else 0.0)
        rg.arms[sx].rotation_euler = (fwd, -sx * out, sx * tw)
        rg.elbows[sx].rotation_euler = (math.radians(max(0.0, el)), 0.0, 0.0)
        rg.hands[sx].rotation_euler = (0.0, 0.0, 0.0)
    if getattr(rg, "dangle", None):   # 垂れる髪: 支点の向きを、世界の上下に合わせる（体の向きだけ残す）
        yaw = Matrix.Rotation(-math.radians(bearing), 4, "Z").to_quaternion()
        for piv in rg.dangle:
            pw = (fk(piv.parent) @ piv.matrix_parent_inverse).to_quaternion()
            piv.rotation_quaternion = pw.inverted() @ yaw
    if getattr(rg, "grip", None) is not None:
        rg.grip.rotation_euler = (math.radians(float(prm.get("grip", -40.0))), 0.0, 0.0)
    # 脚: 地面に着いている形（IK）と、角度で決める形（宙）を air で混ぜる
    for sx, ft, legk in ((1, rf, "r_leg"), (-1, lf, "l_leg")):
        tx = sx * (rg.hip_w + ft[0] * hip_h)
        ty = ft[1] * hip_h
        tz = ft[2] * hip_h + rg.foot_h - pz   # 骨盤から見た足首の高さ
        f_ik, a_ik, k_ik = _leg_ik(rg, sx, (tx, ty, tz)) if air < 1.0 else (0.0, 0.0, 0.0)
        legv = prm.get(legk) or [0.0, 4.0, -8.0]
        f_fk, a_fk, k_fk = math.radians(legv[0]), -sx * math.radians(legv[1]), math.radians(legv[2] if len(legv) > 2 else 0.0)
        f = f_ik * (1 - air) + f_fk * air
        ab = a_ik * (1 - air) + a_fk * air
        kn = k_ik * (1 - air) + k_fk * air
        rg.hips[sx].rotation_euler = (f, ab, 0.0)
        rg.knees[sx].rotation_euler = (kn, 0.0, 0.0)
        # 足は地面と平ら（宙ではすねに沿ってつま先を伸ばす）
        rg.feet[sx].rotation_euler = ((-(f + kn)) * (1 - air) + (-0.3) * air, 0.0, 0.0)


def pelvis_world(rg):
    return rg.pelvis.matrix_world.translation.copy()


def fk(o):
    """物の世界の行列を、親から順に掛けて求める（view_layer.update を待たない。剣の軌跡・垂れる髪で使う）。"""
    m = o.matrix_parent_inverse @ o.matrix_basis
    p = o.parent
    while p is not None:
        m = p.matrix_parent_inverse @ p.matrix_basis @ m if p.parent is None else p.matrix_parent_inverse @ p.matrix_basis @ m
        p = p.parent
    return m
