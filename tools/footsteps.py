#!/usr/bin/env python3
"""足音を、blocking.yaml の動き（walk・run のキー）に合わせて自動で置く（D-068、2026-11-01）。

  python tools/footsteps.py episodes/A/scenes/A06          # episodes/…/A06/footsteps.yaml を書く

- 人物が walk か run で動いている間（ほかの姿勢でも秒速 0.35m 以上で移動していれば。跳ぶ・座るなどは除く）、歩いた距離が歩幅に達するごとに1歩。歩幅は人物の背で変える
  （大人 0.7m、百騎兵など小さい人物 0.45m、走ると約 1.6倍）。
- 床の材質: 部屋（layout の room_*）の中は石の床（stone）、外は砂（sand）。左右の足と3種の音を順に使う。
- 音量: 歩く 0.16・走る 0.22（台詞を 1.0 とした大きさ。previs_reel.py が素材の山を台詞の山に合わせてから掛ける。2026-11-01 ユーザー「足音がまだ大きい」）、カメラからの距離で下げる（2.5m で 1.0、遠いほど小さく。0.035 未満は置かない）。
  only に入っていない人物（画に映らない人物）は置かない。
- 出力の footsteps.yaml は tools/previs_reel.py が sound.yaml と一緒に重ねる。手で直すときは sound.yaml の方に書く
  （footsteps.yaml は作り直すと上書きされる）。カットの footsteps: false でそのカットの足音を止められる。
"""
import math
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import staging  # noqa: E402
import motion  # noqa: E402

STEPS = ROOT / "audio/sfx/steps"
DT = 0.02


def inside_room(lay, x, y):
    for name, pr in (lay.get("props") or {}).items():
        if name.startswith("room") and "x" in pr:
            (xa, xb), (ya, yb) = pr["x"], pr["y"]
            if xa <= x <= xb and ya <= y <= yb:
                return True
    return False


def main():
    scene_dir = ROOT / sys.argv[1]
    st = staging.Stage(sys.argv[1])
    shots = {}
    for p in (scene_dir / "shots").glob("*.yaml"):
        s = yaml.safe_load(p.read_text(encoding="utf-8"))
        shots[s["id"]] = s
    people = st.lay["people"]
    items = []
    k_sample = 0
    for cut in st.order:
        c = st.bk["cuts"][cut] or {}
        if cut not in shots or c.get("footsteps") is False:
            continue
        sec = float(shots[cut]["sec"])
        only = c.get("only")
        tracks = {w: motion.PersonTrack(ks, st.place) for w, ks in (c.get("keys") or {}).items()}
        cam = motion.CamTrack(c["cam_keys"]) if c.get("cam_keys") else None
        for who, tr in tracks.items():
            if only is not None and who not in only:
                continue
            h = people.get(who, {}).get("h", 1.6)
            base = 0.7 if h >= 1.3 else 0.45
            acc, prev = 0.0, tr.at(0.0)[0]
            prev_z = tr.at(0.0)[1]
            t = DT
            while t <= sec:
                p, z, pose = tr.at(t)
                dz, prev_z = abs(z - prev_z), z
                d = math.dist(p, prev)
                prev = p
                speed = d / DT
                still = pose in ("jump", "lie", "sit", "kneel", "kneel_hold", "ride", "spin", "fall", "hop", "tumble", "flip", "dive") or dz / DT > 0.4   # 宙にいる間（上下に動いている間）は足音なし
                if (pose in ("walk", "run") and speed > 0.15) or (not still and speed > 0.35):
                    acc += d
                    running = pose == "run" or speed > 2.6
                    stride = base * (1.6 if running else 1.0)
                    if acc >= stride:
                        acc = 0.0
                        dist = 3.0
                        if cam:
                            pos = {w: trk.at(t)[0] for w, trk in tracks.items()}
                            look_pt = lambda v, pp: tuple(pp[v]) if isinstance(v, str) and v in pp else tuple(st.place(v))  # noqa: E731
                            try:
                                cp, _, _, _ = cam.at(t, pos, look_pt, lambda v: 1.0, lambda n, x, y: motion.ground_z(st.lay, x, y))
                                dist = math.dist(cp[:2], p)
                            except Exception:
                                pass
                        vol = (0.22 if running else 0.16) * min(1.0, 2.5 / max(0.5, dist))
                        if h < 1.3:
                            vol *= 0.75
                        if vol >= 0.035:
                            mat = "stone" if inside_room(st.lay, *p) else "sand"
                            k_sample += 1
                            side = "l" if k_sample % 2 else "r"
                            items.append({"src": f"audio/sfx/steps/{mat}_{side}{1 + (k_sample // 2) % 3}.ogg",
                                          "cut": cut, "t": round(t, 2), "volume": round(vol, 2), "who": who})
                else:
                    acc = min(acc, 0.35)   # 止まっていたら、次の一歩は早めに
                t += DT
    out = scene_dir / "footsteps.yaml"
    head = ("# tools/footsteps.py が blocking.yaml の walk・run の動きから書く足音（手で編集しない。直すなら sound.yaml に書くか、"
            "カットに footsteps: false）\n")
    out.write_text(head + yaml.safe_dump({"scene": st.bk["scene"], "sfx": items}, allow_unicode=True, sort_keys=False, width=200),
                   encoding="utf-8")
    print(f"{out.relative_to(ROOT)}（足音 {len(items)} 歩）")


if __name__ == "__main__":
    main()
