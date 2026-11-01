"""カメラから見た人物の画面上の位置（下絵を描く前の目安）。
使い方: python tools/frame_check.py <シーンのフォルダ> [<カットID>...] [--step 1.0] [--all]
下絵を描く前に、カメラの直しを数字で確かめる（2026-11-01、回2 の下絵の見直しで作った）。--all は only にない人物も × を付けて出す（映り込みの確認）。
頭の高さはメタリカだけ帽子の 0.8m を足す。姿勢（ひざをつく等）は見ないので、低い姿勢では頭の値が大きめに出る。
出力: 時刻ごとに、人物の 横位置 s（-1 左端 … +1 右端）、足 / 頭 の縦位置（-1 下端 … +1 上端）、距離。
画面の外（|s|>1、頭が上端より上、足が下端より下）には印を付ける。"""
import sys, math
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import staging, motion

args = [a for a in sys.argv[1:]]
step = 1.0
ALL = "--all" in args
if ALL: args.remove("--all")
if "--step" in args:
    i = args.index("--step"); step = float(args[i + 1]); del args[i:i + 2]
st = staging.Stage(args[0])
cuts = args[1:] or st.order


def proj(cam, yaw, pitch, lens, p, h):
    """yaw は staging の向き（北が 0、時計回り?）。staging.vec_of で前方ベクトルを作る。"""
    d = staging.vec_of(yaw)
    right = (-d[1], d[0])
    v = (p[0] - cam[0], p[1] - cam[1])
    zf = staging.dot(v, d)
    if zf <= 0.05:
        return None
    x = staging.dot(v, right)
    th = math.tan(staging.fov_half(lens))
    tv = th * 9 / 16
    # 縦: 前方 zf、高さの差 dh を pitch で回す
    dh = h - cam[2]
    zc = zf * math.cos(pitch) + dh * math.sin(pitch)
    yc = -zf * math.sin(pitch) + dh * math.cos(pitch)
    if zc <= 0.05:
        return None
    return x / zc / th, yc / zc / tv, zf


for cut in cuts:
    c = st.bk["cuts"][cut]
    sec = float(c.get("sec") or 0) or max(float(k["t"]) for k in c["cam_keys"])
    shown = c.get("only") or list(st.bk["start"])
    only = set(shown)
    if ALL:
        shown = list((c.get("keys") or {}).keys())
    tracks = {w: motion.PersonTrack(k, st.place) for w, k in (c.get("keys") or {}).items()}
    ct = motion.CamTrack([dict(k) for k in c["cam_keys"]])
    print(f"== {cut} {c.get('frame', '')}")
    t = 0.0
    while t <= sec + 1e-6:
        pos = {w: tr.at(t)[0] for w, tr in tracks.items()}
        zz = {w: tr.at(t)[1] for w, tr in tracks.items()}

        def look_pt(v, p):
            return tuple(p[v]) if isinstance(v, str) and v in p else tuple(st.place(v))

        def look_height(v):
            if isinstance(v, str) and v in st.lay["people"]:
                return st.lay["people"][v]["h"] * 0.75 + zz.get(v, 0.0)
            return 0.0
        cam, look, lh, lens = ct.at(t, pos, look_pt, look_height)
        pitch = math.atan2(lh - cam[2], max(0.3, math.dist(cam[:2], look)))
        yaw = staging.bearing_of((look[0] - cam[0], look[1] - cam[1]))
        row = []
        for w in shown:
            if w not in pos:
                continue
            hh = st.lay["people"][w]["h"] + (0.8 if w == "metallica" else 0.0)
            a = proj(cam, yaw, pitch, lens, pos[w], zz[w])
            b = proj(cam, yaw, pitch, lens, pos[w], zz[w] + hh)
            if a is None:
                row.append(f"{staging.SHORT.get(w, w)}:後ろ")
                continue
            s, fy, dist = a
            hy = b[1] if b else 9
            mark = ""
            if abs(s) > 1.0: mark += "外"
            if hy > 1.0: mark += "頭切"
            if fy < -1.0: mark += "足切"
            if w not in only and abs(s) > 1.05:
                continue
            row.append(f"{'' if w in only else '×'}{staging.SHORT.get(w, w)} s{s:+.2f} 足{fy:+.2f} 頭{hy:+.2f} {dist:.1f}m{('〔' + mark + '〕') if mark else ''}")
        print(f"  {t:4.1f}s L{lens:.0f} | " + " | ".join(row))
        t += step
