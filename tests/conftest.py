"""テストの共通の準備（2026-11-08）。tools/ の道具を import できるようにする。Blender（bpy）は使わない。

  python -m pytest tests -q
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
FIX = ROOT / "tests" / "fixtures"


def load_line_fixture(scene):
    """tests/fixtures/line/<場面>.json（tests/make_line_fixtures.py が作る）を、tools/framing.line_issues の引数の形にする。"""
    d = json.loads((FIX / "line" / f"{scene}.json").read_text(encoding="utf-8"))
    data = {"cuts": d["cuts"]}
    bk = {"cuts": d["bk_cuts"], "places": d["places"]}
    lines = [tuple(l) for l in d["lines"]]
    feet_tab = d["feet"]

    def feet(cut, who, t):
        rows = (feet_tab.get(cut) or {}).get(who)
        if not rows:
            return None
        r = min(rows, key=lambda r: abs(r[0] - float(t)))
        return (r[1], r[2]) if len(r) >= 3 else None
    return data, d["shots"], bk, lines, feet
