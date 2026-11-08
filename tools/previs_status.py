#!/usr/bin/env python3
"""下絵の成果物の版の一覧（2026-11-08、previs の根本改善のフェーズ1）。Blender を使わない。

場面のカットごとに、下絵の動画（<カット>.mp4）・確認画像（_check.jpg）・動きの確認（_motion.jpg）・構図の測定（gen/framing.yaml）が、
今の入力（設計）と道具で作られたものかを出す（tools/manifest.py）:

  最新         記録があり、入力も道具も今と同じ
  入力が古い   作った後で、そのカットの設計（blocking・ショットファイル・台詞など）が変わった
  道具が古い   作った後で、下絵（か測定）の道具が変わった
  食い違い     動画と確認画像の記録の入力が違う（別々の時に作った）
  記録がないもの（2026-11-08 より前に作ったもの）は Git から推定する:
    古い（推定）      そのファイルの最後のコミットの後で、blocking のそのカット（と start）か、下絵の道具（tools/manifest.py の RENDER_TOOLS）が変わった。
                      道具はファイル単位で見るので、ほかの場面の人形だけを変えたコミットでも「道具」に数える（粗い）
    同じらしい（推定）  どちらも変わっていない。ショットファイル・台詞・配置の変化は見ていない
  なし         ファイルがない

  python tools/previs_status.py episodes/A/scenes/A13                       # 場面のカットすべて
  python tools/previs_status.py episodes/A/scenes/A13 --cuts A13-005        # そのカットだけ
  python tools/previs_status.py --all --md docs/previs_review/previs_status.md   # 全場面の一覧を書く
"""
import argparse
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import manifest  # noqa: E402

KINDS = (("video", "{cut}.mp4"), ("check", "{cut}_check.jpg"), ("motion", "{cut}_motion.jpg"))
_SHOW = {}


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout


def _blocking_at(rev, rel):
    key = (rev, rel)
    if key not in _SHOW:
        txt = git("show", f"{rev}:{rel}")
        try:
            _SHOW[key] = yaml.safe_load(txt) or {} if txt else None
        except Exception:
            _SHOW[key] = None
    return _SHOW[key]


def _cut_sig(bk, cut):
    if not bk:
        return None
    return json.dumps([(bk.get("cuts") or {}).get(cut), bk.get("start")], sort_keys=True, default=str)


def guess(path, scene_rel, cut, now_bk):
    """記録のないファイルの版を Git から推定する。"""
    rel = str(path.relative_to(ROOT))
    rev = git("log", "-1", "--format=%H", "--", rel).strip()
    if not rev:
        return "記録なし（Git にない）"
    old = _blocking_at(rev, f"{scene_rel}/blocking.yaml")
    same = _cut_sig(old, cut) == _cut_sig(now_bk, cut)
    tools = [f"tools/{f}" for f in manifest.RENDER_TOOLS]
    later = git("rev-list", "--count", f"{rev}..HEAD", "--", *tools).strip()
    date = git("log", "-1", "--format=%cs", rev).strip()
    tools_later = bool(later and later != "0")
    why = "・".join(w for w, ok in (("設計", not same), ("道具", tools_later)) if ok)
    if why:
        return f"古い（推定。{date} の後で{why}が変わった）"
    return f"同じらしい（推定。{date} から設計も道具も変わっていない）"


def scene_status(scene_dir, cuts=None):
    sd = pathlib.Path(scene_dir)
    sd = sd if sd.is_absolute() else ROOT / sd
    scene_rel = str(sd.relative_to(ROOT))
    scene = sd.name
    si = manifest.SceneInputs(sd)
    now_bk = si.raw
    rt, at = manifest.tool_hash(manifest.RENDER_TOOLS), manifest.tool_hash(manifest.AUDIT_TOOLS)
    recs = manifest.load(scene)
    fr = sd / "gen" / "framing.yaml"
    audit = ((yaml.safe_load(fr.read_text(encoding="utf-8")) or {}).get("cuts") or {}) if fr.exists() else {}
    out = {}
    for cut in si.st.order:
        if cuts and cut not in cuts:
            continue
        cur = si.input_hash(cut)
        row = {}
        for kind, pat in KINDS:
            p = ROOT / "previs" / scene / pat.format(cut=cut)
            rec = (recs.get(cut) or {}).get(kind)
            if rec:
                row[kind] = manifest.compare(rec, cur, rt)
            elif p.exists():
                row[kind] = guess(p, scene_rel, cut, now_bk)
            else:
                row[kind] = "なし"
        v, c = (recs.get(cut) or {}).get("video"), (recs.get(cut) or {}).get("check")
        if v and c and v.get("input_hash") != c.get("input_hash"):
            row["video"] += "・確認画像と食い違い"
        a = audit.get(cut)
        row["audit"] = manifest.compare(a, cur, at) if a else "なし"
        out[cut] = row
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dirs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--cuts", help="カット（, で区切る）")
    ap.add_argument("--md", help="一覧を書く（Markdown）")
    a = ap.parse_args()
    dirs = sorted(str(p.parent.relative_to(ROOT)) for p in (ROOT / "episodes").glob("*/scenes/*/blocking.yaml")) if a.all else a.scene_dirs
    cuts = set(c for c in (a.cuts or "").split(",") if c) or None
    md = ["# 下絵の成果物の版（tools/previs_status.py）", "", "見方は tools/previs_status.py の頭のコメント。", "",
          "| カット | 動画 | 確認画像 | 動きの確認 | 構図の測定 |", "| --- | --- | --- | --- | --- |"]
    count = {}
    for sd in dirs:
        try:
            res = scene_status(sd, cuts)
        except Exception as e:
            print(f"{sd}: 読めない（{type(e).__name__}: {e}）")
            continue
        for cut, r in res.items():
            line = f"| {cut} | {r['video']} | {r['check']} | {r['motion']} | {r['audit']} |"
            md.append(line)
            print(f"{cut}: 動画 {r['video']}／確認画像 {r['check']}／動きの確認 {r['motion']}／測定 {r['audit']}")
            for k, v in r.items():
                head = v.split("（")[0].split("・")[0]   # 最新・入力が古い・古い・同じらしい・なし など
                count[(k, head)] = count.get((k, head), 0) + 1
    names = {"video": "動画", "check": "確認画像", "motion": "動きの確認", "audit": "測定"}
    summ = "、".join(f"{names[k]} {h} {n}" for (k, h), n in sorted(count.items()))
    print(summ)
    if a.md:
        p = ROOT / a.md
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(md[:4] + [summ, ""] + md[4:]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
