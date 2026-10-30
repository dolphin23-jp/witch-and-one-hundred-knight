# A02 の生成計画（gen/A02_plan.yaml）を作る。生成はしない。python3 tools/a02_plan.py
# 場面ごとの芝居（動画の指示文）をここに書いているので、別の場面ではこのファイルを写して直す
import sys, math, pathlib, yaml
sys.path.insert(0, "tools")
import staging, stills_plan as sp
st = staging.Stage("episodes/A/scenes/A02")
D = st.dir; R = "episodes/A/scenes/A02"
CELS = ["A02-013", "A02-016"]           # 3人が映るカット（CLAUDE.md: 3人以上は1人ずつ作って重ねる）
NO_END = {"A02-004": "百騎兵が画面の外へ走り出て、終わりは人物がいない。背景だけの画は作らない（背景は生成しない）。開始の静止画と下絵だけで作る",
          "A02-007": "位置も向きも変わらない（頷くだけ）。開始と同じ画を終わりにも使う"}
VP = {  # 動画の指示文（芝居とカメラだけ。見た目は書かない）
"001": "Extreme wide shot, static camera. The Hundred Knight stands still in the foreground with its back to us. Bisco bursts out of the front door, waves one hand and runs down the bridge steps across the clearing, slowing down and stopping in front of the Hundred Knight at about 4.6 seconds, slightly out of breath, one hand on her hip, and keeps talking to it.",
"002": "Medium close-up of Bisco, static low camera looking up at her from the Hundred Knight's eye level. She stands with her hands on her hips, talking down to the Hundred Knight; on 'mattaku' she jerks her thumb over her shoulder toward the house and rolls her eyes in exasperation, ending with a small sigh.",
"003": "Full shot from the side, static camera. Bisco pulls a small folded paper out of the pouch at her belt, bends down a little and hands it to the Hundred Knight; the Hundred Knight takes it with its big gauntlets and looks down at it. Bisco straightens up.",
"004": "Medium-long shot looking down at the Hundred Knight, static camera. It peers at the small paper in its big gauntlets, nods once, grips the paper, turns toward the right side of the picture and at about 2.2 seconds starts running, leaving the frame on the right by about 3.6 seconds. The shot ends on the empty clearing.",
"005": "Wide shot, static low camera. The Hundred Knight runs from the left toward the right of the picture; in the background on the left Bisco takes half a step forward and stretches one arm out, calling to it. The Hundred Knight stops short at about 2 seconds and turns around to look back at Bisco.",
"006": "Medium-long shot over the Hundred Knight's shoulder, static camera at its eye level. Bisco walks briskly toward the Hundred Knight, pointing at her own chest with her thumb, then gestures toward the distance with one hand; at about 6 seconds she bends down to the Hundred Knight's eye level, smiles a little shyly and holds out a small fist.",
"007": "Close-up looking down at the Hundred Knight, static camera. It looks up at Bisco (off screen) and gives one big, slow nod, then keeps looking up.",
"008": "Medium close-up of Bisco, static camera slightly below her. Still bent forward, she blinks in surprise; after a short pause she straightens up, her eyes wander away, she scratches her cheek, then clenches a small fist in front of her chest and nods to herself. Her tail sways restlessly.",
"009": "Full shot of both characters from the south, static camera. Bisco, flustered, waves both hands in front of her chest and takes half a step back, turns halfway toward the house and points over her shoulder at it with her thumb, then glances sideways at the Hundred Knight to see its reaction. Her tail swishes busily. The Hundred Knight stands still, looking up at her.",
"010": "Medium close-up of Bisco in profile, low camera, slow push in. She looks up at the second-floor window of the house; her voice softens, her eyes narrow gently and a faint smile appears.",
"011": "Close-up of Bisco, static camera. She snaps out of it and turns back toward the Hundred Knight (off screen), raises her index finger, then presses one hand to her chest and talks quickly with her eyes darting away, cheeks slightly flushed. One ear twitches.",
"012": "Medium close-up of Bisco, slow push in. She lowers the hand from her chest and clenches a fist tightly in front of it; her eyes drop to her feet and her brows draw together into a troubled, brooding look. Her lips start to move and stop. Her tail goes still. Hold on her silent face at the end.",
"013": "Wide shot from the south, static camera. Bisco stands with her head bowed; the Hundred Knight stands in the foreground on the right, looking up at her. Arlecchino comes down the bridge steps; when he starts to speak, Bisco jumps, her shoulders flinch up, and she turns around toward him; the Hundred Knight turns its head toward him too. Arlecchino stops at the foot of the bridge, one hand on his chest.",
"014": "Medium close-up of Arlecchino, static camera. He stands upright with one hand on his chest and speaks politely; on 'nonbiri' he freezes for an instant, then continues as if nothing happened and bows.",
"015": "Medium-long shot from the south-west, static camera, Arlecchino in the background. Bisco snaps upright, looks from Arlecchino to the Hundred Knight and back, then raises a fist energetically; at about 3.8 seconds she turns toward the right side of the picture and starts to stride off.",
"016": "Extreme wide high-angle shot from the south, static camera. Bisco strides away east along the boardwalk, Arlecchino follows a little behind, both growing smaller in the morning mist. The Hundred Knight watches them go, then at about 4.6 seconds turns around and looks up at the second-floor window of the house.",
}
TAIL = ("2D TV anime style, clean line art, cel shading, rich but soft colors. Keep every character's design, colors and proportions identical to the first frame and the character sheets. "
        "Early morning light, a thin mist over the swamp. No text, no captions, no speech bubbles, no manga symbols (no anger marks, sweat drops or question marks).")
SPEAKER_VISIBLE = {"001": "bisco", "002": "bisco", "003": "bisco", "005": "bisco", "006": "bisco", "008": "bisco", "009": "bisco", "010": "bisco", "011": "bisco", "012": "bisco", "015": "bisco"}
stills, cels, comps, videos = [], [], [], []
for cut in st.order:
    shot = yaml.safe_load((D / "shots" / f"{cut}.yaml").read_text())
    k = cut[-3:]
    out = lambda w, n=1: f"{R}/stills/{w}/{cut}_{w}_gpt-image-2.5_0{n}.png"
    if cut in CELS:
        for w in ("start", "end"):
            for e in st.frame(cut, w)["people"]:
                c = sp.build_cel(st, cut, e["who"], w)
                c["output"] = f"{R}/stills/cels/{cut}_{w}_{e['who']}.png"; cels.append(c)
            comps.append({"cut": cut, "which": w, "method": f"python tools/composite.py {R} {cut} --which {w} --cels {R}/stills/cels --plate refs/witch-house-front_bg_01.png（生成なし）→ 重ねた画を1枚編集して朝の光にそろえる（1クレジット）",
                          "prompt": "Image 1 is a rough composite: separately drawn character cels pasted onto a background. Redraw it as one finished anime frame: keep every character's position, size, pose, facing and design exactly as in image 1, keep the house and bridge layout, but unify everything into soft early-morning light (pale clear sky, low sun from the east, a thin mist over the swamp, the swamp still glowing green, lamps unlit), add natural contact shadows on the ground and fix the edges. " + sp.STYLE,
                          "output": out(w)})
        continue
    it = sp.build(st, cut, "start"); it["output"] = out("start"); stills.append(it)
    if cut in NO_END:
        it2 = {"cut": cut, "which": "end", "skip": NO_END[cut]}
    else:
        it2 = sp.build(st, cut, "end")
        it2["refs"].append(out("start"))
        it2["prompt"] = it2["prompt"].replace(" Keep each character", f" Image {len(it2['refs'])} is the first frame of the same shot (made first): keep its background rendering, morning light and the way the characters are drawn. Keep each character")
        if cut == "A02-012":
            it2["prompt"] = it2["prompt"].replace(", looking toward the camera", "")
        it2["output"] = out("end")
    stills.append(it2)
for cut in st.order:
    shot = yaml.safe_load((D / "shots" / f"{cut}.yaml").read_text()); k = cut[-3:]
    dur = max(4, math.ceil(shot["sec"] - 0.05))
    lines = [d.split("「")[0] for d in (shot.get("dialogue") or [])]
    sv = SPEAKER_VISIBLE.get(k)
    end = None if cut == "A02-004" else (f"{R}/stills/start/{cut}_start_gpt-image-2.5_01.png" if cut == "A02-007" else f"{R}/stills/end/{cut}_end_gpt-image-2.5_01.png")
    who = sorted({e["who"] for w in ("start", "end") for e in st.frame(cut, w)["people"]})
    v = {"cut": cut, "sec": shot["sec"], "duration": dur, "credits": dur * 3,
         "start_image": f"{R}/stills/start/{cut}_start_gpt-image-2.5_01.png", "end_image": end,
         "image_references": [f"refs/{w}_sheet_01.png" for w in who],
         "previs": f"previs/A02/{cut}.mp4",
         "audio_reference": (f"audio/A02/original/{lines[0]}_{sv}.wav（mp3 に変えて渡す）" if lines and sv else None),
         "prompt": VP[k] + (" Sync the speaking character's mouth to the line in audio 1 whenever the face is visible." if lines and sv else "") + " The shot ends on the given last frame."*(end is not None) + " " + TAIL}
    videos.append(v)
cs = len([s for s in stills if not s.get("skip")]) + len(cels) + len(comps)
cv = sum(v["credits"] for v in videos)
plan = {"status": "承認待ち（2026-10-30 作成。生成なし・クレジットなし）",
        "scene": "A02 魔女邸の前・別れ（朝）。16カット 103.0秒。全カットを動画にする（新しい作り方の試験）",
        "decision": "D-055（開始＋終わりの静止画＋設定画＋下絵＋口パク参照）、D-057（順に作る）",
        "credits_estimate": {"stills": cs, "videos": cv, "total": cs + cv},
        "order": "区間ごと（知らせ 001〜004 → 呼び止め 005〜008 → 言い訳 009〜012 → 割り込みと別れ 013〜016）に、静止画（開始→終わり）→ Claude が点検 → 動画。点検で崩れていた静止画の動画は作らずに報告。失敗しても自動では作り直さない",
        "stills": {"model": "gpt_image_2_5", "quality": "medium", "resolution": "2k", "aspect_ratio": "16:9", "note": "1枚1クレジット。終わりは開始の画を添えて作る（同じ背景・光・描き方にそろえる）", "items": stills},
        "cels": {"note": "013・016 は3人が映るので、人物を1人ずつ透明な背景で作り（1枚1クレジット）、tools/composite.py で重ね、1枚編集して朝の光にそろえる", "items": cels, "composite": comps},
        "videos": {"model": "seedance_2_0", "mode": "std", "resolution": "480p", "generate_audio": False, "note": "1秒3クレジット。最短4秒・整数秒。カットより長い分は組み立てで速度を少し上げて収める。口パクの参照は、喋る人物の口が見えるカットだけ", "items": videos}}
p = D / "gen" / "A02_plan.yaml"
p.write_text("# A02 の生成計画（承認を取るための表。tools/stills_plan.py の指示文に、終わりの画・重ねる画・動画を足したもの）\n" + yaml.safe_dump(plan, allow_unicode=True, sort_keys=False, width=200))
for v in videos: print(v["cut"], v["sec"], v["duration"], v["credits"], v["audio_reference"])
print("stills", cs, "videos", cv, "total", cs + cv, "cels", len(cels))
