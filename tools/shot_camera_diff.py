#!/usr/bin/env python3
"""ショットファイルの camera と、blocking.yaml の最初のカメラのキーの食い違いの一覧（2026-11-08、previs の根本改善のフェーズ0）。報告だけで、どちらも直さない。

カメラの設計は2か所にある: ショットファイルの camera（pos・look・lens・move）と、blocking.yaml の cam_keys（下絵 tools/previs.py が使う）。
tools/framing_fix.py は blocking.yaml だけを直すので、直したカットではショットファイルが古いままになる。
比べるのは、最初のカメラのキー（t がいちばん小さい）の cam（[x, y, 高さ]）・lens と、ショットファイルの camera.pos・camera.lens。
位置が 0.05m より、レンズが 0.5mm より違えば食い違いとする。cam が follow（人物を追う）のキーと、pos が数でないショットファイルは「比べられない」。

  python tools/shot_camera_diff.py                                   # 数を表示
  python tools/shot_camera_diff.py --md docs/previs_review/shot_camera_diff.md   # 一覧を書く
"""
import argparse
import math
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
POS_TOL = 0.05
LENS_TOL = 0.5


def compare():
    rows, skipped, total = [], [], 0
    for bkp in sorted(ROOT.glob("episodes/*/scenes/*/blocking.yaml")):
        sd = bkp.parent
        bk = yaml.safe_load(bkp.read_text(encoding="utf-8")) or {}
        for cut, c in (bk.get("cuts") or {}).items():
            ck = (c or {}).get("cam_keys")
            sp = sd / "shots" / f"{cut}.yaml"
            if not ck or not sp.exists():
                continue
            sh = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
            cam = sh.get("camera")
            if not isinstance(cam, dict) or "pos" not in cam:
                continue
            k0 = sorted(ck, key=lambda k: float(k.get("t", 0.0)))[0]
            if not isinstance(k0.get("cam"), list):
                skipped.append((sd.name, cut, "blocking の最初のキーが follow"))
                continue
            try:
                p = [float(v) for v in cam["pos"]]
            except (TypeError, ValueError):
                skipped.append((sd.name, cut, f"ショットファイルの pos が数でない（{cam['pos']}）"))
                continue
            total += 1
            n = min(len(p), 3, len(k0["cam"]))
            b = [float(v) for v in k0["cam"][:n]]
            d = math.dist(b, p[:n])
            try:
                lb, ls = float(k0.get("lens", 35)), float(cam.get("lens", 35))
            except (TypeError, ValueError):
                lb, ls = None, None
            lens_diff = lb is not None and abs(lb - ls) > LENS_TOL
            if d > POS_TOL or lens_diff:
                rows.append({"scene": sd.name, "cut": cut, "dist": round(d, 2), "shot_pos": p, "bk_cam": k0["cam"][:3],
                             "shot_lens": cam.get("lens"), "bk_lens": k0.get("lens"), "shot_look": cam.get("look"), "bk_look": k0.get("look")})
    return rows, skipped, total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--md", help="一覧を書く（Markdown）")
    a = ap.parse_args()
    rows, skipped, total = compare()
    head = f"比べたカット {total}、食い違い {len(rows)}（位置 {sum(1 for r in rows if r['dist'] > POS_TOL)}、レンズだけ {sum(1 for r in rows if r['dist'] <= POS_TOL)}）、比べられない {len(skipped)}"
    print(head)
    if a.md:
        md = ["# ショットファイルの camera と blocking.yaml の最初のカメラのキーの食い違い（tools/shot_camera_diff.py、報告だけ）", "",
              "どちらも直していない。下絵（tools/previs.py）と構図の直し（tools/framing_fix.py）が使うのは blocking.yaml の cam_keys。"
              "ショットファイルの camera は、直した後に書き直されていない。どちらを正本にするか（ショットファイルを blocking から作り直すか、camera を消すか）は、まだ決めていない。", "",
              head, "", "| 場面 | カット | 位置の差 m | ショットの pos | blocking の cam | レンズ（ショット / blocking） | 見る先（ショット / blocking） |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
        for r in rows:
            md.append(f"| {r['scene']} | {r['cut']} | {r['dist']} | {r['shot_pos']} | {r['bk_cam']} | {r['shot_lens']} / {r['bk_lens']} | {r['shot_look']} / {r['bk_look']} |")
        if skipped:
            md += ["", "## 比べられないカット", ""] + [f"- {s} {c}: {why}" for s, c, why in skipped]
        p = ROOT / a.md
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
