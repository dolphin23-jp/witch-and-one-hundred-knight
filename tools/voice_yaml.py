"""ショットファイルの台詞から voice.yaml（台詞の置き場所）を作る。Remotion の組み立て（D-050）で使う。

  python tools/voice_yaml.py episodes/A/scenes/A01b            # episodes/A/scenes/A01b/voice.yaml を書く
  python tools/voice_yaml.py episodes/A/scenes/A01b --dry-run  # 書かずに表示だけ

置き場所の規則は tools/animatic.py の build()（ffmpeg の仮の組み立て）と同じ:
  - カットの頭はショットファイルの tc。台詞はカットの頭から順に置く
  - 声の前の間は timing.lead があればそれ、なければ min(0.3秒, カットの秒 − 声の合計)
  - 1カットに台詞が複数あるときは 0.25秒あけて続ける（timing.at: {L215: 11.6} で、その台詞をカットの頭から何秒に置くか決められる。前の台詞と重なるときは後ろへずらす）
  - 1本の台詞を2カットに分けた所（L49a・L49b）は、a のカットに1本の声として置く（b のカットまで続けて鳴る）
  - 声のない台詞（「……」、lines.csv の flags に「無言」）は置かない（Remotion の組み立ては声のファイルを読むため）
BGM はシーンの original/<シーン>_bgm.ogg（原作音源から声を分けた残り、D-045・D-050）があれば使う。
"""
import argparse
import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
LINE = re.compile(r"^(L\d+)([ab]?)(?:（[^）]*）)?「(.*)」$")
LEAD, GAP = 0.3, 0.25


def dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def make(scene_dir):
    scene_dir = ROOT / scene_dir
    shots = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((scene_dir / "shots").glob("*.yaml"))]
    shots.sort(key=lambda s: float(s["tc"]))
    scene = shots[0]["scene"]
    src = ROOT / "audio" / scene / "original"
    lines, skipped, placed = [], [], set()
    for s in shots:
        items = []
        for d in s.get("dialogue") or []:
            if not isinstance(d, str):
                continue
            m = LINE.match(d.strip())
            if not m:
                sys.exit(f"台詞の形が読めません: {s['id']} {d}")
            lid, part = m.group(1), m.group(2)
            if part == "b" or lid in placed:   # 分けた台詞の後半は a のカットで置いた
                continue
            wavs = sorted(src.glob(f"{lid}_*.wav"))
            if not wavs:
                skipped.append(f"{lid}（{s['id']}）")
                continue
            items.append((lid, dur(wavs[0])))
            placed.add(lid)
        if not items:
            continue
        total_voice = sum(L for _, L in items) + GAP * (len(items) - 1)
        lead = (s.get("timing") or {}).get("lead", min(LEAD, max(0.0, float(s["sec"]) - total_voice)))
        cursor = float(s["tc"]) + lead
        fixed = (s.get("timing") or {}).get("at") or {}   # 台詞ごとの置き場所（カットの頭からの秒）。登場の後に言わせるなど（A07-007）
        for lid, L in items:
            if lid in fixed:
                cursor = max(cursor, float(s["tc"]) + float(fixed[lid]))
            lines.append({"id": lid, "at": round(cursor, 2)})
            cursor += L + GAP
    last = shots[-1]
    total = round(float(last["tc"]) + float(last["sec"]), 2)
    data = {"scene": scene,
            "source": f"audio/{scene}/original（ショットファイルの台詞から tools/voice_yaml.py で作成）",
            "note": "台詞の置き場所（シーンの頭からの秒）。カットの頭はショットファイルの tc",
            "total_sec": total}
    bgm = src / f"{scene}_bgm.ogg"
    if bgm.exists():
        data["bgm"] = {"src": str(bgm.relative_to(ROOT)), "from": 0.0, "volume": 1.0}
    data["lines"] = lines
    return data, skipped


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scene_dir")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    data, skipped = make(a.scene_dir)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200)
    if a.dry_run:
        print(text)
    else:
        out = ROOT / a.scene_dir / "voice.yaml"
        out.write_text("# tools/voice_yaml.py が書く。台詞の秒を変えたらショットファイルを直して作り直す\n" + text, encoding="utf-8")
        print(f"{out.relative_to(ROOT)}（台詞 {len(data['lines'])} 本、{data['total_sec']}秒）")
    if skipped:
        print("声のない台詞（置かない）: " + "、".join(skipped))


if __name__ == "__main__":
    main()
