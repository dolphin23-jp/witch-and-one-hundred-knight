#!/usr/bin/env python3
"""tests/fixtures/line/<場面>.json を作る（2026-11-08 の測定から、線の判定に要る所だけ）。

測定（gen/framing.yaml）は描き直すと変わるので、テストは今の測定の写しを使う。
写すのは、カットの範囲のコマの look・frame・cam・T・t と、人物の wx・wy・fh・in・h・frac・top・fy、
blocking の cam_keys・act・camera、ショットファイルの tc・shot・acting・camera・act、台詞の時刻、blocking から求めた足元の位置。

  python tests/make_line_fixtures.py   # 測り直した後、テストの期待を見直すときだけ
"""
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import framing  # noqa: E402

RANGES = {"A30": ("A30-018", "A30-033"), "A32": ("A32-012", "A32-026"), "A33": ("A33-019", "A33-036"),
          "A35": ("A35-001", "A35-006"), "A36": ("A36-008", "A36-015"), "A13": ("A13-001", "A13-007"), "A17": ("A17-026", "A17-028")}
PKEYS = ("wx", "wy", "fh", "in", "h")
SIZE_KEYS = ("frac", "top", "fy")   # 画の大きさの引き継ぎ（A17）だけ


def build(scene, lo, hi):
    sd = ROOT / "episodes/A/scenes" / scene
    data = yaml.safe_load((sd / "gen/framing.yaml").read_text(encoding="utf-8"))
    order = list(data["cuts"])
    cuts = order[order.index(lo):order.index(hi) + 1]
    shots = {}
    for f in sorted((sd / "shots").glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        if d["id"] in cuts:
            shots[d["id"]] = {k: d.get(k) for k in ("tc", "shot", "acting", "camera", "act") if d.get(k) is not None}
    bk = yaml.safe_load((sd / "blocking.yaml").read_text(encoding="utf-8"))
    lines = framing.speakers(sd, shots)
    feet_fn = framing.feet_index(sd, data)
    out = {"scene": scene, "cuts": {}, "shots": shots, "lines": [list(l) for l in lines], "places": bk.get("places") or {},
           "bk_cuts": {}, "feet": {}}
    keys = PKEYS + (SIZE_KEYS if scene == "A17" else ())
    measured = {w for c in cuts for s in data["cuts"][c]["samples"] for w in s["people"]}
    for c in cuts:
        v = data["cuts"][c]
        out["cuts"][c] = {"sec": v["sec"], "shot": v.get("shot"), "samples": [
            {"T": s["T"], "t": s["t"], "look": s.get("look"), "frame": s.get("frame"), "cam": s["cam"],
             "people": {w: {k: m[k] for k in keys if k in m} for w, m in s["people"].items()}} for s in v["samples"]]}
        bc = bk["cuts"][c] or {}
        out["bk_cuts"][c] = {k: bc[k] for k in ("cam_keys", "act", "camera") if k in bc}
        people = sorted(measured)
        out["feet"][c] = {w: [[s["t"]] + [round(x, 3) for x in (feet_fn(c, w, s["t"]) or [])] for s in v["samples"]] for w in people}
    return out


def main():
    dst = ROOT / "tests/fixtures/line"
    dst.mkdir(parents=True, exist_ok=True)
    for scene, (lo, hi) in RANGES.items():
        d = build(scene, lo, hi)
        p = dst / f"{scene}.json"
        p.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(p.relative_to(ROOT), round(p.stat().st_size / 1024), "KB")


if __name__ == "__main__":
    main()
