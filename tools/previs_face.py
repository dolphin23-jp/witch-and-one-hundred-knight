"""人形の顔と会話の芝居（2026-11-07、下絵のアップグレードの続き）。previs.py から使う。

顔: 目（ハイライト付き。まばたき）・眉・口（開け閉めと口角）・照れの頬を、頭の球の前（+Y）に付ける。
  百騎兵は兜の中の橙に光る目だけ（口はない）。森の使用人・からくり人形は前からの顔のまま。
表情（expr）: neutral・smile・grin（にやり、キヒヒ）・angry・shout・sad・surprised・tired・shy（照れ）・annoyed（呆れ・ちっ）。
  決め方（強い順）: blocking の keys の expr（その時刻より前の最後のキー）→ 話している台詞の文の感動詞と記号（キヒヒ・アハハ・！！・！？・ちっ・はあ……）→ カットの芝居（acting）の文で、
  その人の名前と同じ区切りにある言葉 → neutral。
口: 台詞を話している間、原作の声（audio/<場面>/original/<台詞ID>_<話し手>.wav）の大きさで開く。
視線: blocking の gaze がない人は、ほかの人が話している間、話し手のほうへ頭を向ける（previs.py の Plan.head_offset）。
話す身ぶり: 話している間、立つ・座る・構えの姿勢に、ひじと腕の小さな動きを足す。まばたきは 2.5〜5 秒ごと。呼吸は胸がわずかに上下する。
"""
import math
import pathlib
import re
import wave

import numpy as np
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent

EXPR = {   # brow: 眉の内側を上げる角度（度、－で怒り）、raise: 眉の高さ（頭の半径に対する割合）、eye: 目の開き、mw: 口の幅、corner: 口角（度、＋で上）、mo: 口の開きの基本
    "neutral": dict(brow=0, raise_=0.0, eye=1.0, mw=1.0, corner=0, mo=0.0),
    "smile": dict(brow=6, raise_=0.04, eye=0.5, mw=1.2, corner=24, mo=0.12),
    "grin": dict(brow=-8, raise_=0.0, eye=0.62, mw=1.35, corner=30, mo=0.18, asym=0.09),
    "angry": dict(brow=-26, raise_=-0.04, eye=0.82, mw=1.1, corner=-16, mo=0.0),
    "shout": dict(brow=-26, raise_=-0.02, eye=0.92, mw=1.3, corner=-6, mo=0.55),
    "sad": dict(brow=24, raise_=0.03, eye=0.72, mw=0.8, corner=-22, mo=0.0),
    "surprised": dict(brow=4, raise_=0.14, eye=1.35, mw=0.8, corner=0, mo=0.5),
    "tired": dict(brow=12, raise_=-0.03, eye=0.42, mw=0.9, corner=-10, mo=0.0),
    "shy": dict(brow=16, raise_=0.03, eye=0.68, mw=0.8, corner=10, mo=0.0, blush=1.0),
    "annoyed": dict(brow=-12, raise_=0.0, eye=0.58, mw=0.9, corner=-14, mo=0.0),
}
WORDS = [   # カットの芝居（acting）の文の言葉 → 表情（上ほど強い）。台詞の文には使わない（「お疲れ様」「泣き虫」「怒ってたなぁ」を取り違える）
    ("grin", ("にやり", "ニヤ", "不敵", "高笑い", "ほくそ笑", "得意げ", "勝ち誇", "キヒヒ", "含み笑", "したり顔", "悪い顔")),
    ("shout", ("怒鳴", "叫ぶ", "叫び", "叫ん", "わめ")),
    ("angry", ("怒る", "怒り", "怒っ", "激怒", "睨", "にらむ", "にらみ", "にらま", "いら立", "苛立", "噛みつ", "かみつ", "食ってかか", "悔し", "歯ぎしり")),
    ("surprised", ("驚", "びっくり", "ぎょっ", "目を丸", "跳び上が", "飛び上が", "凍りつ", "青ざめ", "固まる", "固まっ", "怯え", "おびえ", "息をのむ", "息を呑")),
    ("sad", ("泣く", "泣い", "泣き出", "涙", "悲し", "しょんぼり", "落ち込", "うなだれ", "寂し", "さびし", "うつむ")),
    ("shy", ("照れ", "赤面", "頬を染", "もじもじ", "恥ずかし")),
    ("tired", ("疲れ", "ため息", "溜息", "ぐったり", "眠", "へたり込", "息を切ら")),
    ("annoyed", ("呆れ", "あきれ", "うんざり", "舌打ち", "困っ", "困り", "むっと", "ふくれ", "むくれ", "すねる", "すねて", "ジト目")),
    ("smile", ("笑う", "笑っ", "笑顔", "微笑", "ほほえ", "嬉し", "うれし", "喜", "はしゃ", "にこ", "ニコ", "手を振", "目を輝か", "吹き出", "ばんざい", "ほっと", "うっとり")),
]
SPEECH = [   # 台詞の文の感動詞と記号 → 表情（話している間）
    ("grin", ("キヒヒ", "キーッヒッヒ", "ヒッヒッヒ", "フフフ", "フフッ", "クックッ", "ククク")),
    ("smile", ("アハハ", "あはは", "ウフフ", "うふふ", "えへへ", "エヘヘ", "ふふっ")),
    ("surprised", ("！？", "!?", "？！", "?!")),
    ("shout", ("！！", "!!")),
]
SPEECH_HEAD = [   # 台詞の頭の言葉 → 表情
    ("surprised", ("え？", "えっ", "ええっ", "なっ……", "なっ！", "うわ", "ひぃ", "まさか")),
    ("annoyed", ("ちっ", "チッ", "はぁ？", "はあ？", "ったく", "まったく")),
    ("tired", ("はあ……", "はぁ……", "はぁ、はぁ", "やれやれ", "ふう……")),
    ("sad", ("……すまない", "ごめん")),
]


def expr_from_speech(text):
    """台詞の文から、話している間の表情（感動詞と記号だけで決める）。"""
    s = str(text or "").strip()
    for name, words in SPEECH_HEAD:
        if any(s.startswith(w) for w in words):
            return name
    for name, words in SPEECH:
        if any(w in s for w in words):
            return name
    return None


NO_FACE = {"forest-servant", "karakuri-doll", "night-train", "sand-eater", "scarecocco", "usamaru", "marika-mouse", "aromani-gel",
           "heinz-carriage"}
HK = "hundred-knight"


def _mat(P, name, rgb, emit=0.0):
    return P.make_mat(name, rgb, emit=emit)


SKIN = (1.0, 0.88, 0.78)
FACE_STYLE = {   # 人物ごとの顔の違い
    "arlecchino": {"plate": None, "head": (0.95, 0.94, 0.9), "eye": (0.85, 0.08, 0.06), "eye_glow": 1.5},   # 白い卵形の頭（頭の球がそのまま顔）、赤い目
    HK: {"plate": (0.03, 0.03, 0.04), "eye": (1.0, 0.55, 0.12), "eye_glow": 3.0, "mouth": False, "brows": False},   # 兜の中の黒い顔、橙に光る目
}
# 顔の面（肌色の楕円体。頭の球の前に少し出す）: 中心と半径（頭の半径 hr に対する割合。頭の目印から）
PLATE_C, PLATE_R = (0.0, 0.3, 0.97), (0.88, 0.75, 0.9)   # 前の面が頭の球（半径 hr）より前に出るように


def add_face(P, who, head, hr):
    """頭（HEAD_<人物>、首の位置の空の目印。頭の球は (0, 0, hr) を中心に半径 hr）に顔を付ける。顔の部品の dict を返す（顔がなければ None）。"""
    if who in NO_FACE:
        return None
    st = FACE_STYLE.get(who, {})
    plate = st.get("plate", SKIN)
    cx, cy, cz = (v * hr for v in PLATE_C)
    rx, ry, rz = (v * hr for v in PLATE_R)
    if st.get("head"):   # 頭の球の色を替える（アルレッキーノの白い卵形の頭）
        hm = _mat(P, f"{who}_fhead", st["head"])
        for o in head.children:
            if o.name.startswith(f"{who}_head") and o.type == "MESH":
                o.data.materials.clear()
                o.data.materials.append(hm)
    if plate is None:   # 頭の球が顔（アルレッキーノ）
        cx, cy, cz, rx, ry, rz = 0.0, 0.0, hr, hr, hr, hr * (1.2 if who == "arlecchino" else 1.0)
    else:
        P.prim("sphere", _mat(P, f"{who}_fplate", plate), f"{who}_fplate", head, (cx, cy, cz), radius=1.0, scale=(rx, ry, rz))

    def on(x, z, lift=0.015):   # 顔の面の上の点（x: 左右、z: 頭の目印からの高さ。頭の半径に対する割合）
        X, Z = x * hr, z * hr
        u = 1.0 - (X / rx) ** 2 - ((Z - cz) / rz) ** 2
        Y = cy + ry * math.sqrt(max(0.0, u)) + lift * hr
        return (X, Y, Z)

    f = {"hr": hr, "who": who}
    eye_rgb = st.get("eye", (0.05, 0.04, 0.07))
    eye_m = _mat(P, f"{who}_feye", eye_rgb, emit=st.get("eye_glow", 0.0))
    R = hr * 0.15
    f["eyes"], f["glints"], f["brows"], f["blush"] = [], [], [], []
    white = _mat(P, f"{who}_fglint", (1.0, 1.0, 1.0), emit=0.8)
    brow_m = _mat(P, f"{who}_fbrow", (0.12, 0.08, 0.06))
    pink = _mat(P, f"{who}_fblush", (1.0, 0.45, 0.5), emit=0.4)
    for sx in (-1, 1):
        e = P.prim("sphere", eye_m, f"{who}_feye{sx}", head, on(sx * 0.34, 1.12), radius=R, scale=(0.9, 0.45, 1.2))
        f["eyes"].append(e)
        if who != HK:
            f["glints"].append(P.prim("sphere", white, f"{who}_fglint{sx}", e, (sx * 0.32 * R, 0.85 * R, 0.4 * R), radius=0.3 * R))
        if st.get("brows", True):
            f["brows"].append(P.prim("cube", brow_m, f"{who}_fbrow{sx}", head, on(sx * 0.35, 1.47), size=2.0, scale=(hr * 0.17, hr * 0.03, hr * 0.035)))
        if plate is not None and who != HK:
            bl = P.prim("sphere", pink, f"{who}_fblush{sx}", head, on(sx * 0.52, 0.82, 0.0), radius=hr * 0.14, scale=(1.0, 0.3, 0.55))
            bl.hide_render = True
            f["blush"].append(bl)
    if st.get("mouth", True):
        lip = _mat(P, f"{who}_fmouth", (0.38, 0.08, 0.1))
        f["mouth"] = P.prim("sphere", lip, f"{who}_fmouth", head, on(0.0, 0.58), radius=1.0, scale=(hr * 0.2, hr * 0.06, hr * 0.03))
        f["corners"] = [P.prim("cube", lip, f"{who}_fcorner{sx}", head, on(sx * 0.2, 0.6), size=2.0, scale=(hr * 0.075, hr * 0.03, hr * 0.022)) for sx in (-1, 1)]
    else:
        f["mouth"], f["corners"] = None, []
    objs = f["eyes"] + f["brows"] + ([f["mouth"]] if f["mouth"] else []) + f["corners"]
    f["base"] = {o.name: (tuple(o.location), tuple(o.scale)) for o in objs}
    return f


def pose(f, expr, mouth_open, blink):
    """表情・口の開き（0〜1）・まばたき（0〜1、1 で閉じる）を当てる。"""
    if f is None:
        return
    hr = f["hr"]
    e = EXPR.get(expr) or EXPR["neutral"]
    eye_k = e["eye"] * (1.0 - 0.92 * blink)
    B = f["base"]
    for o in f["eyes"]:
        (lx, ly, lz), (sx, sy, sz) = B[o.name]
        o.scale = (sx, sy, sz * max(0.07, eye_k))
    for k, o in enumerate(f["brows"]):
        side = -1 if k == 0 else 1
        (lx, ly, lz), sc = B[o.name]
        dz = hr * (e["raise_"] + (e.get("asym", 0.0) if side < 0 else 0.0))
        o.location = (lx, ly, lz + dz)
        # 内側（顔の真ん中の側）を上げる角度: 正面から見て、左右で回す向きが逆
        o.rotation_euler = (0, math.radians(e["brow"]) * side, 0)
    if f["who"] == HK:   # 兜の目: 怒ると内側が下がる
        for k, o in enumerate(f["eyes"]):
            o.rotation_euler = (0, math.radians(e["brow"] * 0.6) * (-1 if k == 0 else 1), 0)
    if f["mouth"] is None:
        return
    (lx, ly, lz), (sx, sy, sz) = B[f["mouth"].name]
    mo = max(e["mo"], min(1.0, mouth_open))
    f["mouth"].scale = (sx * e["mw"] * (1.0 - 0.25 * mo), sy, sz * (1.0 + 4.0 * mo))
    f["mouth"].location = (lx, ly, lz - hr * 0.06 * mo)
    for k, o in enumerate(f["corners"]):
        side = -1 if k == 0 else 1
        (cx, cy, cz), sc = B[o.name]
        ang = math.radians(e["corner"])
        o.location = (cx * e["mw"], cy, cz + hr * 0.05 * math.sin(ang))
        o.rotation_euler = (0, -ang * side, 0)
        o.hide_render = mo > 0.35   # 大きく開けている間は口角を出さない
    for o in f["blush"]:
        o.hide_render = not e.get("blush")


# ---------- 台詞（話し手・口の開き・表情の言葉） ----------
_SPEECH = {}
_ENV = {}
_TEXT = {}


def _texts(scene_dir):
    """台詞ID → 文（audio/<場面>/original/script.yaml）。"""
    key = str(scene_dir)
    if key not in _TEXT:
        out = {}
        sp = ROOT / "audio" / pathlib.Path(scene_dir).name / "original" / "script.yaml"
        if sp.exists():
            try:
                for l in (yaml.safe_load(sp.read_text(encoding="utf-8")) or {}).get("lines") or []:
                    out[l["id"]] = str(l.get("text", ""))
            except Exception:
                pass
        _TEXT[key] = out
    return _TEXT[key]


def speech(scene_dir):
    """[(始め, 終わり, 話し手, 台詞ID, wav)]（場面の頭からの秒）。"""
    key = str(scene_dir)
    if key in _SPEECH:
        return _SPEECH[key]
    out = []
    vy = pathlib.Path(scene_dir) / "voice.yaml"
    src = ROOT / "audio" / pathlib.Path(scene_dir).name / "original"
    if vy.exists() and src.exists():
        for l in (yaml.safe_load(vy.read_text(encoding="utf-8")) or {}).get("lines") or []:
            wav = next(iter(sorted(src.glob(f"{l['id']}_*.wav"))), None)
            if wav is None:
                continue
            try:
                with wave.open(str(wav)) as w:
                    dur = w.getnframes() / float(w.getframerate())
            except Exception:
                continue
            out.append((float(l["at"]), float(l["at"]) + dur, wav.stem.split("_", 1)[1], l["id"], wav))
    _SPEECH[key] = out
    return out


def envelope(wav):
    """声の大きさ（100 コマ/秒、0〜1）。"""
    key = str(wav)
    if key in _ENV:
        return _ENV[key]
    with wave.open(str(wav)) as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    dt = {1: np.int8, 2: np.int16, 4: np.int32}.get(sw, np.int16)
    a = np.frombuffer(raw, dtype=dt).astype(np.float32)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    hop = max(1, sr // 100)
    m = len(a) // hop
    rms = np.sqrt((a[: m * hop].reshape(m, hop) ** 2).mean(axis=1)) if m else np.zeros(1)
    ref = float(np.percentile(rms, 92)) if m else 1.0
    env = np.clip(rms / max(ref, 1e-6), 0.0, 1.0)
    k = 5   # 50ms でならす
    if len(env) > k:
        env = np.convolve(env, np.ones(k) / k, mode="same")
    _ENV[key] = env
    return env


def speaking(scene_dir, who, T):
    """T（場面の頭からの秒）に who が話している台詞 (台詞ID, 口の開き 0〜1) か None。"""
    for a, b, w, lid, wav in speech(scene_dir):
        if w == who and a <= T <= b:
            env = envelope(wav)
            i = int((T - a) * 100)
            v = float(env[min(len(env) - 1, max(0, i))])
            return lid, max(0.0, min(1.0, (v - 0.12) * 1.5))
    return None


def speaker_at(scene_dir, T, present):
    """T に話している人（present の中で）。"""
    for a, b, w, lid, wav in speech(scene_dir):
        if a <= T <= b and w in present:
            return w
    return None


NOT_IF = {"手を振": ("振りかざ", "振り回", "振り払", "振り上げ")}   # 「雷の手を振りかざす」は手を振る（笑み）ではない


def expr_from_text(text):
    for name, words in WORDS:
        if any(wd in text and not any(x in text for x in NOT_IF.get(wd, ())) for wd in words):
            return name
    return None


_JA_ALL = None


def _subject_first(clause, hit, names):
    """区切りの主語を先頭にする: 「〜が」「〜は」の付いた名前 → 区切りの終わりの名前（「アルレッキーノをにらむルッキーニィ」はルッキーニィ）→ 前に出る名前。"""
    if len(hit) < 2:
        return hit
    def ja_of(w):
        return [j for j in names.get(w) or [] if j] or ([ja_names()[w]] if w in ja_names() else [])
    for w in hit:
        if any(f"{j}が" in clause or f"{j}は" in clause for j in ja_of(w)):
            return [w] + [x for x in hit if x != w]
    tail = clause.rstrip("　 。、！？…）」")
    for w in hit:
        if any(j and tail.endswith(j) for j in ja_of(w)):
            return [w] + [x for x in hit if x != w]
    return hit


def ja_names():
    """全部の配置（world/*-layout.yaml の people と world/ch7-people-extra.yaml）から集めた、人物の日本語の名前（「（」より前）。
    配置によっては ja を書いていない人物がいる（居間のルッキーニィ・マーニィ）ので、その補いに使う。"""
    global _JA_ALL
    if _JA_ALL is None:
        _JA_ALL = {}
        for f in sorted((ROOT / "world").glob("*.yaml")):
            try:
                d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            for w, pz in ((d.get("people") if isinstance(d, dict) else None) or {}).items():
                ja = str((pz or {}).get("ja") or "").split("（")[0] if isinstance(pz, dict) else ""
                if ja and w not in _JA_ALL:
                    _JA_ALL[w] = ja
    return _JA_ALL


def acting_expr(acting, names):
    """カットの芝居の文から、人物ごとの表情（その人の名前と同じ区切り「。」「、」にある言葉）。names: {人物: 日本語の名前}。"""
    out = {}
    names = {w: ([j for j in ja if j] or ([ja_names()[w]] if w in ja_names() else [])) for w, ja in names.items()}
    for clause in re.split(r"[。、（）「」・]", acting or ""):   # 「・」は画の名前（寄り・泣くメタリカ）の区切り
        ex = expr_from_text(clause)
        if not ex:
            continue
        hit = sorted((min(clause.find(j) for j in ja if j and j in clause), w) for w, ja in names.items() if ja and any(j and j in clause for j in ja))
        hit = [w for _p, w in hit]   # 区切りの中で前に出てくる順（「ビスコが百騎兵をにらむ」はビスコ。2026-11-07 までは人物の並び順で取り違えた）
        hit = _subject_first(clause, hit, names)
        for w in hit[:1]:   # 区切りの主語だけ
            out.setdefault(w, ex)
    return out


def blink(who, t):
    """まばたき（0〜1）。人ごとに決まった間隔（2.5〜5 秒）。"""
    seed = sum(ord(ch) for ch in who)
    period = 2.5 + (seed % 25) / 10.0
    ph = (t + (seed % 7) * 0.37) % period
    d = 0.13
    return max(0.0, 1.0 - abs(ph - d / 2) / (d / 2)) if ph < d else 0.0


def gesture(prm, who, t, mo):
    """話す身ぶり（右のひじと腕が声の大きさで少し上がる・小さなうなずき）と呼吸（前かがみのわずかな上下）を、落ち着いた姿勢に足す。"""
    calm = 1.0 - max(float(prm.get("run", 0.0)), float(prm.get("walk", 0.0)), float(prm.get("air", 0.0)))
    if calm <= 0.05:
        return prm
    q = dict(prm)
    seed = sum(ord(ch) for ch in who)
    q["lean"] = float(q.get("lean", 0.0)) + 1.0 * calm * math.sin(2 * math.pi * (0.22 + (seed % 5) * 0.01) * t + seed)
    if mo > 0.02:
        w = calm * min(1.0, mo * 1.4)
        ra = list(q.get("r_arm") or (0.0, 0.0, 0.0))
        ra[0] = float(ra[0]) + 9.0 * w
        q["r_arm"] = ra
        q["r_el"] = float(q.get("r_el", 0.0)) + 17.0 * w
        hd = list(q.get("head") or (0.0, 0.0, 0.0))
        hd[0] = float(hd[0]) + 4.0 * w * math.sin(2 * math.pi * 1.6 * t + seed)
        q["head"] = hd
    return q
