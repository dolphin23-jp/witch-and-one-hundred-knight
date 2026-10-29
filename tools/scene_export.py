"""ショットファイルと voice.yaml から、組み立て用の共通の入力（scene.json と素材）を作る。

  python tools/scene_export.py episodes/A/scenes/A01            # animatic/build/A01/
  python tools/scene_export.py episodes/A/scenes/A01 --overlays  # 漫符の試験（overlays.yaml）も入れる

HyperFrames と Remotion の比較（制作パイプラインのマイルストーン3）のため、どちらの組み立ても同じ入力を読む。
tools/animatic.py（ffmpeg で直接書き出す仮の組み立て）と同じ規則で、尺・カメラの動き・台詞の置き場所を決める。
  画: 静止画を 1920x1080 の JPEG に縮めて img/ に置く（カメラの寄りで 1.2 倍まで拡大しても 1280x720 で粗くならない）
  声: audio/<シーン>/original/<台詞ID>_<人物>.wav。voice.yaml の split（息継ぎを広げる）は2本に分けて audio/ に置く
  BGM: voice.yaml の bgm（原作音源から声を分けた残り、D-045）。シーンより短ければループする
出力は Git の管理外（animatic/build/）。
"""
import argparse
import json
import pathlib
import shutil
import subprocess

import yaml
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAMES = {"metallica": "メタリカ", "bisco": "ビスコ", "arlecchino": "アルレッキーノ", "hundred-knight": "百騎兵"}
KIND = {"S": ("止め", "#2a2f36"), "K": ("止め＋カメラ", "#223040"), "M": ("小さな動き", "#1d3326"), "A": ("見せ場", "#5a3414")}


def dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


CODES = {"static", "push", "push_fast", "pull", "pan_l", "pan_r", "tilt_up", "tilt_down", "shake"}


def norm_move(m, kind):
    """camera.move を Remotion の動き（camera.ts）の名前に直す。言葉で書いたもの（A01b など）は tools/animatic.py の motion() と同じ読み方"""
    if not m:
        return "push" if kind == "K" else "static"
    if m in CODES:
        return m
    if "tilt up" in m:
        return "tilt_up"
    if "pan" in m:
        return "pan_l" if ("左へ" in m and "左から" not in m) else "pan_r"
    if "揺れ" in m:
        return "shake"
    if "push_fast" in m or "一気に寄る" in m:
        return "push_fast"
    if "pull" in m or "引く" in m:
        return "pull"
    if "push" in m or "寄る" in m or "寄り" in m:
        return "push"
    return "static"


def export(scene_dir, out_root, with_overlays=False):
    scene_dir = ROOT / scene_dir
    vspec = yaml.safe_load((scene_dir / "voice.yaml").read_text(encoding="utf-8"))
    scene = vspec["scene"]
    total = float(vspec["total_sec"])
    shots = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((scene_dir / "shots").glob("*.yaml"))]
    shots.sort(key=lambda s: s["tc"])
    src_dir = ROOT / "audio" / scene / "original"
    script = {l["id"]: l for l in yaml.safe_load((src_dir / "script.yaml").read_text(encoding="utf-8"))["lines"]}

    out = out_root / scene
    (out / "img").mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(parents=True, exist_ok=True)

    cuts = []
    for i, s in enumerate(shots):
        start = float(s["tc"])
        nxt = float(shots[i + 1]["tc"]) if i + 1 < len(shots) else total
        sec = round(nxt - start, 3)
        label, color = KIND.get(s.get("kind"), ("", "#1d2b24"))
        cam = s.get("camera") or {}
        head = f"{s['id']}   {s.get('kind', '')} {label}   {sec:.1f}秒" + (f"   カメラ {cam['setup']}" if cam.get("setup") else "")
        cut = {"id": s["id"], "kind": s.get("kind"), "start": start, "sec": sec, "label": head, "color": color,
               "move": norm_move(cam.get("move"), s.get("kind")), "image": None,
               "shot": s.get("shot", ""), "acting": s.get("acting", "")}
        img = ROOT / s["image"] if s.get("image") else None
        if img and img.exists():
            dst = out / "img" / f"{s['id']}.jpg"
            # 元の画が新しいときだけ作り直す（差し替えたカットだけ更新される）
            if not dst.exists() or dst.stat().st_mtime < img.stat().st_mtime:
                im = Image.open(img).convert("RGB")
                im = im.resize((1920, round(1920 * im.height / im.width)), Image.LANCZOS)
                if im.height != 1080:   # 16:9 に中央で切る（元は 2688x1520 など）
                    top = (im.height - 1080) // 2
                    im = im.crop((0, top, 1920, top + 1080))
                im.save(dst, quality=90)
            cut["image"] = f"img/{s['id']}.jpg"
            cut["source"] = s["image"]
        # 動画（段階2）: ショットファイルの video があれば静止画の代わりに使う。
        # video_fit: speed（全体をカットの長さに合わせて速める、最大1.6倍）/ head（頭から切る）/ tail（終わりに合わせて切る）
        vid = ROOT / s["video"] if s.get("video") else None
        if vid and vid.exists():
            (out / "vid").mkdir(exist_ok=True)
            dst = out / "vid" / f"{s['id']}{vid.suffix}"
            if not dst.exists() or dst.stat().st_mtime < vid.stat().st_mtime:
                shutil.copy2(vid, dst)
            L = dur(vid)
            fit = s.get("video_fit", "speed")
            rate = min(max(L / sec, 1.0), 1.6) if fit == "speed" else 1.0
            used = sec * rate
            begin = max(0.0, L - used) if fit == "tail" else (max(0.0, (L - used) / 2) if fit == "speed" else 0.0)
            cut.update({"video": f"vid/{dst.name}", "video_rate": round(rate, 4), "video_from": round(begin, 3)})
            cut["label"] += "   動画"
        cuts.append(cut)

    voices, subs = [], []
    for v in vspec["lines"]:
        wav = next(src_dir.glob(f"{v['id']}_*.wav"))
        L = dur(wav)
        sp = v.get("split")
        if sp:
            a, b = out / "audio" / f"{v['id']}_a.wav", out / "audio" / f"{v['id']}_b.wav"
            ff("-i", wav, "-t", sp["at"], a)
            ff("-ss", sp["at"], "-i", wav, b)
            voices += [{"src": f"audio/{a.name}", "at": v["at"], "sec": round(dur(a), 3)},
                       {"src": f"audio/{b.name}", "at": round(v["at"] + sp["at"] + sp["gap"], 3), "sec": round(dur(b), 3)}]
            end = v["at"] + L + sp["gap"]
        else:
            dst = out / "audio" / wav.name
            shutil.copy2(wav, dst)
            voices.append({"src": f"audio/{dst.name}", "at": v["at"], "sec": round(L, 3)})
            end = v["at"] + L
        who = NAMES.get(script[v["id"]]["who"], script[v["id"]]["who"])
        subs.append({"start": v["at"], "end": round(end + 0.15, 3), "text": f"{v['id']} {who}「{script[v['id']]['text']}」"})

    bgm = None
    if vspec.get("bgm"):
        b = vspec["bgm"]
        src = ROOT / b["src"]
        dst = out / "audio" / src.name
        shutil.copy2(src, dst)
        L = dur(src) - float(b.get("from", 0.0))
        bgm = {"src": f"audio/{dst.name}", "from": float(b.get("from", 0.0)), "volume": float(b.get("volume", 1.0)),
               "sec": round(L, 3), "loop": L < total}

    overlays = []
    if with_overlays:
        spec = yaml.safe_load((scene_dir / "overlays.yaml").read_text(encoding="utf-8")) if (scene_dir / "overlays.yaml").exists() else {"overlays": []}
        by_id = {c["id"]: c for c in cuts}
        (out / "overlays").mkdir(exist_ok=True)
        for o in spec["overlays"]:
            c = by_id[o["cut"]]
            src = ROOT / o["src"]
            shutil.copy2(src, out / "overlays" / src.name)
            overlays.append({**o, "src": f"overlays/{src.name}", "start": round(c["start"] + o.get("at", 0.0), 3),
                             "sec": round(o.get("sec", c["sec"] - o.get("at", 0.0)), 3)})

    data = {"scene": scene, "width": 1280, "height": 720, "fps": 24, "total": total,
            "cuts": cuts, "voices": voices, "subs": subs, "overlays": overlays, "bgm": bgm}
    (out / "scene.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{(out / 'scene.json').relative_to(ROOT)}（{len(cuts)}カット、声 {len(voices)} 本、漫符 {len(overlays)}、"
          f"BGM {'なし' if not bgm else str(bgm['sec']) + '秒' + ('（ループ）' if bgm['loop'] else '')}）")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scene_dir")
    p.add_argument("--out", default="animatic/build")
    p.add_argument("--overlays", action="store_true")
    a = p.parse_args()
    export(a.scene_dir, ROOT / a.out, a.overlays)


if __name__ == "__main__":
    main()
