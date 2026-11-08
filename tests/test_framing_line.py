"""tools/framing.py のイマジナリーラインの判定のテスト（2026-11-08、previs の根本改善のフェーズ0）。

測定の写し（tests/fixtures/line/）で、2026-11-08 のレビューで見つけた判定の不具合が直っていること、
本当に線を越えているカットは今も見つかることを確かめる。"""
import framing
from conftest import load_line_fixture


def run(scene):
    data, shots, bk, lines, feet = load_line_fixture(scene)
    tr, blocks = [], {}
    out = framing.line_issues(data, shots, bk, lines, trace=tr, blocks=blocks, feet=feet)
    return out, {r["cut"]: r for r in tr}, blocks


def kinds(out, cut):
    return [i["kind"] for i in out.get(cut, [])]


# ---------- 合成のデータ ----------

def test_side_of():
    A, B = (0.0, 0.0), (2.0, 0.0)
    assert framing._side_of(A, B, (1.0, 3.0)) == 1
    assert framing._side_of(A, B, (1.0, -3.0)) == -1
    assert framing._side_of(A, B, (1.0, 0.3)) == 0          # 線から 0.5m 以内
    assert framing._side_of(A, B, (8.0, 1.0)) == 0          # 線の延長（20 度以内）


def _sample(t, cam, people, look="a", frame="胸上"):
    return {"T": t, "t": t, "look": look, "frame": frame, "cam": cam, "people": people}


def _pp(ax, ay, fh_a, bx, by, fh_b):
    return {"a": {"wx": ax, "wy": ay, "fh": fh_a, "in": True, "h": 1.6}, "b": {"wx": bx, "wy": by, "fh": fh_b, "in": True, "h": 1.6}}


def _cuts(spec):
    """spec: [(カット, カメラの x, y, 見る人, 枠)]。a は (0, 0) で東（90）を、b は (2, 0) で西（270）を向く。"""
    data, shots, bk = {"cuts": {}}, {}, {"cuts": {}, "places": {}}
    for k, (c, cams, look, frame) in enumerate(spec):
        rows = [_sample(i * 0.75, list(cm) + [1.5], _pp(0, 0, 90, 2, 0, 270), look, frame) for i, cm in enumerate(cams)]
        data["cuts"][c] = {"sec": 0.75 * len(cams), "samples": rows}
        shots[c] = {"tc": float(k * 10)}
        bk["cuts"][c] = {}
    return data, shots, bk


def test_crossing_found_and_stale():
    """越えたら印。基準との間に LINE_STALE より多くのカットがあれば「要確認」。"""
    north, south = (1.0, -3.0), (1.0, 3.0)
    spec = [("X-001", [north] * 3, "a", "胸上"), ("X-002", [south] * 3, "b", "胸上")]
    out = framing.line_issues(*_cuts(spec), lines=[])
    assert kinds(out, "X-002") == ["線を越える"]
    spec = [("X-001", [north] * 3, "a", "胸上")] + [(f"X-1{k:02d}", [(50.0, 50.0)] * 2, "z", "胸上") for k in range(6)] + [("X-200", [south] * 3, "b", "胸上")]
    data, shots, bk = _cuts(spec)
    out = framing.line_issues(data, shots, bk, [])
    assert kinds(out, "X-200") == ["線を越える（要確認）"]
    assert out["X-200"][0]["gap"] == 6


def test_wide_reestablishes():
    """大引きで二人がともに画面に入れば、そこで側を決め直す（前は飛ばすだけで、次のカットが印になった）。"""
    north, south = (1.0, -3.0), (1.0, 3.0)
    spec = [("X-001", [north] * 3, "a", "胸上"), ("X-002", [south] * 3, "a", "大引き"), ("X-003", [south] * 3, "b", "胸上")]
    out = framing.line_issues(*_cuts(spec), lines=[])
    assert "X-003" not in out


def test_crossing_inside_cut_is_not_a_flag():
    """カットの中でカメラが線を越える（回り込む）のは印にせず、終わりの側で続ける。"""
    north, south = (1.0, -3.0), (1.0, 3.0)
    spec = [("X-001", [north] * 3, "a", "胸上"), ("X-002", [north, north, south, south], "a", "胸上"), ("X-003", [south] * 3, "b", "胸上")]
    out = framing.line_issues(*_cuts(spec), lines=[])
    assert out == {}


# ---------- 測定の写し（2026-11-07 の測定） ----------

def test_A35_004_partner_left_is_not_a_flag():
    """A35-004: メタリカはこの場面にいない（声だけ）。前は、退場の途中（A35-003 の真ん中のコマ）のアルレッキーノを相手にして印が付いた。
    退場し終えた位置は 6m より遠く、見ている相手はいない。"""
    out, tr, _ = run("A35")
    assert "A35-004" not in out
    assert tr["A35-004"].get("skip") == "見ている相手がいない"


def test_A30_032_not_compared_with_10_cuts_before():
    """A30-032: 前は、10 カット前の A30-021 を基準に印が付いた。今は始めが線の上なので、そこで決め直す（印なし）。"""
    out, tr, _ = run("A30")
    assert "A30-032" not in out
    assert tr["A30-032"]["ref"] == "A30-021" and tr["A30-032"]["gap"] == 10


def test_A30_flip_flop_is_one_block():
    """A30-022（＋）→ A30-023（－）→ A30-025（＋）: 印は真ん中の A30-023 だけで、3 カットは1つの区間（どちらにそろえるかは1つの判断）。"""
    out, _, blocks = run("A30")
    assert kinds(out, "A30-023") == ["線を越える"]
    assert "A30-025" not in out
    seg = next(seg for seg in blocks[("metallica", "teressa-swamp")] if any(r[0] == "A30-023" for r in seg))
    assert [(r[0], r[1]) for r in seg] == [("A30-022", 1), ("A30-023", -1), ("A30-025", 1)]


def test_A32_giant_partner_and_block():
    """A32-017 の巨大メタリカは、不問を決めたネザリアに向かって叫ぶ（相手はコマの多数決で nezaria）。
    ビスコ↔巨大メタリカは A32-015・A32-020 が同じ側（－）で、A32-025 だけが反対（＋）。巨大メタリカの頭の動きで線が回らない（足元で測る）。"""
    out, tr, blocks = run("A32")
    assert tr["A32-017"]["P"] == "nezaria"
    assert "A32-017" not in out and "A32-020" not in out
    assert kinds(out, "A32-025") == ["線を越える"]
    seg = next(seg for seg in blocks[("bisco", "metallica-giant")] if any(r[0] == "A32-025" for r in seg))
    assert [(r[0], r[1]) for r in seg] == [("A32-015", -1), ("A32-020", -1), ("A32-025", 1)]


def test_A33_035_stale_reference():
    """A33-035: 基準の A33-022 との間に 12 カットあるので「要確認」。"""
    out, _, _ = run("A33")
    assert kinds(out, "A33-035") == ["線を越える（要確認）"]
    assert out["A33-035"][0]["gap"] == 12


def test_true_crossings_still_found():
    """本当に越えているカット（2026-11-07 に鏡写しを戻したもの）は、今も見つかる。"""
    out, _, _ = run("A36")
    assert kinds(out, "A36-014") == ["線を越える"]
    out, _, _ = run("A13")
    assert kinds(out, "A13-005") == ["線を越える"]


def test_A17_028_frame_inheritance():
    """A17-028: 最初のキーの「寄り」が、frame のないクレーンのキー（6 秒・18 秒）に引き継がれている。"""
    data, _shots, bk, _lines, _feet = load_line_fixture("A17")
    inh = framing.inherited_frame(data["cuts"]["A17-028"]["samples"], bk["cuts"]["A17-028"]["cam_keys"])
    assert inh and inh["kind"] == "画の大きさの引き継ぎ" and inh["keys"] == [6.0, 18.0] and inh["frame"] == "寄り"
    assert framing.inherited_frame(data["cuts"]["A17-026"]["samples"], bk["cuts"]["A17-026"]["cam_keys"]) is None
