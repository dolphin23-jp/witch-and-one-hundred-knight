"""scene.json（tools/scene_export.py）から HyperFrames の index.html を作る。

  python animatic/hyperframes/build_html.py A01      # animatic/build/A01/index.html と gsap.min.js
  sh animatic/hyperframes/render.sh A01 A01_hyperframes

カメラの動きは tools/animatic.py の zoompan、animatic/remotion/src/camera.ts と同じ式。
GSAP のタイムラインは一時停止で作り、window.__timelines に登録する（HyperFrames の決まり）。
"""
import html
import json
import math
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FONT = '"MJ100 Serif", serif'


def cam(move, n, N, W, H):
    """camera.ts と同じ。{z, x, y}（x, y は画面のピクセル）"""
    p = min(1, n / N) if N > 1 else 0
    if move == "push":
        return 1 + 0.1 * p, 0, 0
    if move == "push_fast":
        return min(1 + 0.2 * n / max(1, math.floor(N * 0.35)), 1.2), 0, 0
    if move == "pull":
        return 1.12 - 0.12 * p, 0, 0
    if move in ("pan_r", "pan_l"):
        q = p if move == "pan_r" else 1 - p
        return 1.15, -W * 0.15 * (q - 0.5), 0
    if move in ("tilt_up", "tilt_down"):
        q = p if move == "tilt_down" else 1 - p
        return 1.15, 0, -H * 0.15 * (q - 0.5)
    if move == "shake":
        z, d = 1.08, max(0, 1 - n / 14)
        return z, -28 * math.sin(n * 2.3) * d * z / 2, -20 * math.cos(n * 2.9) * d * z / 2
    return 1, 0, 0


def build(scene):
    out = ROOT / "animatic/build" / scene
    d = json.loads((out / "scene.json").read_text(encoding="utf-8"))
    W, H, fps, total = d["width"], d["height"], d["fps"], d["total"]
    shutil.copy2(HERE / "node_modules/gsap/dist/gsap.min.js", out / "gsap.min.js")
    fr = lambda s: round(s * fps) / fps   # フレームの境目にそろえる（Remotion と同じ切り替え位置）

    els, js = [], []
    for c in d["cuts"]:
        a, b = fr(c["start"]), fr(c["start"] + c["sec"])
        cid = "c_" + c["id"].replace("-", "_")
        body = (f'<img id="{cid}_img" src="{c["image"]}" alt="">' if c["image"] else
                f'<div class="note">画: {html.escape(c["shot"])}<br><br>動き: {html.escape(c["acting"])}</div>')
        els.append(f'<div id="{cid}" class="clip cut" style="background:{c["color"]}" data-start="{a:.4f}" data-duration="{b - a:.4f}" data-track-index="0">'
                   f'{body}<div class="tag" style="background:{c["color"]}">{html.escape(c["label"])}</div></div>')
        if not c["image"] or c["move"] == "static":
            continue
        N = max(1, round(c["sec"] * fps))
        sel = f"#{cid}_img"
        if c["move"] == "shake":   # 揺れは最初の15フレームだけ。1フレームずつ置く
            for n in range(0, 16):
                z, x, y = cam("shake", n, N, W, H)
                js.append(f'tl.set("{sel}",{{scale:{z:.4f},x:{x:.2f},y:{y:.2f}}},{a + n / fps:.4f});')
        elif c["move"] == "push_fast":   # 35% で 1.2 倍まで寄って止まる
            k = max(1, math.floor(N * 0.35))
            js.append(f'tl.fromTo("{sel}",{{scale:1,x:0,y:0}},{{scale:1.2,duration:{k / fps:.4f},ease:"none"}},{a:.4f});')
        else:
            z0, x0, y0 = cam(c["move"], 0, N, W, H)
            z1, x1, y1 = cam(c["move"], N, N, W, H)
            js.append(f'tl.fromTo("{sel}",{{scale:{z0:.4f},x:{x0:.2f},y:{y0:.2f}}},'
                      f'{{scale:{z1:.4f},x:{x1:.2f},y:{y1:.2f},duration:{b - a:.4f},ease:"none"}},{a:.4f});')

    for i, o in enumerate(d["overlays"]):   # 漫符（D-027）: ぽんと出て、小さく脈打つ
        a = fr(o["start"])
        size = W * o["size"]
        els.append(f'<img id="m{i}" class="clip mark" src="{o["src"]}" alt="" data-start="{a:.4f}" data-duration="{fr(o["sec"]):.4f}" data-track-index="1" '
                   f'style="width:{size:.1f}px;height:{size:.1f}px;left:{W * o["x"] - size / 2:.1f}px;top:{H * o["y"] - size / 2:.1f}px">')
        js.append(f'tl.fromTo("#m{i}",{{scale:0}},{{scale:1,duration:0.35,ease:"back.out(3)"}},{a:.4f});')
        if o.get("anim") == "pop_pulse":
            t, k = a + 0.35, 0
            while t < a + o["sec"] - 0.25:
                js.append(f'tl.to("#m{i}",{{scale:{1.06 if k % 2 == 0 else 0.97},duration:0.125,ease:"sine.inOut"}},{t:.4f});')
                t += 0.125; k += 1

    for i, s in enumerate(d["subs"]):
        a, b = fr(s["start"]), fr(s["end"])
        els.append(f'<div class="clip sub" data-start="{a:.4f}" data-duration="{b - a:.4f}" data-track-index="2"><span>{html.escape(s["text"])}</span></div>')

    for i, v in enumerate(d["voices"]):
        els.append(f'<audio id="v{i}" src="{v["src"]}" data-start="{fr(v["at"]):.4f}" data-duration="{v["sec"] + 0.05:.4f}" data-volume="1" data-track-index="3"></audio>')

    page = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width={W}, height={H}">
<!-- 自動生成（animatic/hyperframes/build_html.py）。手で直さない -->
<script src="gsap.min.js"></script>
<style>
@font-face {{ font-family:"MJ100 Serif"; src: local("Noto Serif CJK JP Bold"), local("NotoSerifCJKjp-Bold"); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{W}px; height:{H}px; overflow:hidden; background:#000; }}
#root {{ position:relative; width:{W}px; height:{H}px; overflow:hidden; font-family:{FONT}; }}
.clip {{ position:absolute; }}
.cut {{ inset:0; overflow:hidden; }}
.cut img {{ width:100%; height:100%; object-fit:cover; transform-origin:center; }}
.note {{ color:#e8e8e8; font-size:29px; line-height:1.5; padding:100px 60px; }}
.tag {{ position:absolute; left:20px; top:18px; font-size:22px; color:#fff; opacity:0.92; padding:2px 8px; white-space:pre; }}
.mark {{ transform-origin:center; }}
.sub {{ left:0; right:0; bottom:36px; display:flex; justify-content:center; }}
.sub span {{ max-width:1100px; text-align:center; font-size:30px; line-height:1.35; color:#fff;
  -webkit-text-stroke:6px #000; paint-order:stroke fill; line-break:anywhere; }}
#tc {{ position:absolute; right:30px; top:26px; font-size:26px; color:rgba(255,255,255,0.8); z-index:10; }}
</style>
</head>
<body>
<div id="root" data-composition-id="{scene}" data-start="0" data-duration="{total}" data-width="{W}" data-height="{H}">
{chr(10).join(els)}
<div id="tc"></div>
</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
{chr(10).join(js)}
// タイムコード（右上）
const tc = document.getElementById("tc");
const fmt = (t) => String(Math.floor(t / 60)).padStart(2, "0") + ":" + (t % 60).toFixed(2).padStart(5, "0");
tl.eventCallback("onUpdate", () => {{ tc.textContent = fmt(tl.time()); }});
tl.set({{}}, {{}}, {total});
window.__timelines["{scene}"] = tl;
tl.seek(0);
</script>
</body>
</html>
"""
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"{(out / 'index.html').relative_to(ROOT)}（要素 {len(els)}、アニメーション {len(js)}）")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "A01")
