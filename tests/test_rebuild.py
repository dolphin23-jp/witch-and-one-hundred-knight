"""作り直し（2026-11-08）で足した判定のテスト: 宣言した軸（D-077 案）・例外 waive・長いカットの止まり。Blender は使わない。"""
import framing


def _cut(cams, a=(0.0, 0.0), b=(2.0, 0.0)):
    rows = [{"T": i * 0.75, "t": i * 0.75, "look": "a", "frame": "胸上", "cam": list(cm) + [1.5],
             "people": {"a": {"wx": a[0], "wy": a[1], "fh": 90, "in": True, "h": 1.6}, "b": {"wx": b[0], "wy": b[1], "fh": 270, "in": True, "h": 1.6}}}
            for i, cm in enumerate(cams)]
    return {"sec": 0.75 * len(cams), "samples": rows}


def test_declared_axis_side():
    """宣言した軸の反対の側のコマがあれば「線を越える」。推定（相手の向きなど）は使わない。"""
    data = {"cuts": {"X-001": _cut([(1.0, -3.0)] * 3), "X-002": _cut([(1.0, 3.0)] * 3)}}
    bk = {"cuts": {"X-001": {}, "X-002": {}}, "places": {},
          "axes": [{"name": "二人", "a": "a", "b": "b", "side": [1.0, -5.0], "cuts": ["X-001", "X-002"]}]}
    shots = {"X-001": {"tc": 0.0}, "X-002": {"tc": 10.0}}
    out = framing.line_issues(data, shots, bk, [])
    assert "X-001" not in out
    assert out["X-002"][0]["kind"] == "線を越える" and out["X-002"][0]["axis"] == "二人"


def test_axis_free_and_by_name():
    bk = {"cuts": {"X-001": {"axis": "free"}, "X-002": {"axis": "二人"}, "X-003": {}},
          "axes": [{"name": "二人", "a": "a", "b": "b", "side": [0, 0]}]}
    order = ["X-001", "X-002", "X-003"]
    assert framing.declared_axis(bk, "X-001", order) == "free"
    assert framing.declared_axis(bk, "X-002", order)["name"] == "二人"
    assert framing.declared_axis(bk, "X-003", order) is None


def test_stall_note():
    """長いカットで、人物とカメラのキーのない間が 6 秒を超えると知らせる。ゆっくりすぎる寄りは動きに数えない。"""
    still = {"only": ["a"], "keys": {"a": [{"t": 0.0, "at": [0, 0]}, {"t": 0.4, "pose": "cry"}, {"t": 21.0, "pose": "cry"}]},
             "cam_keys": [{"t": 0.0, "cam": [3, 0, 1], "look": "a", "lens": 60}, {"t": 21.0, "cam": [2.3, 0, 1], "look": "a", "lens": 45}]}
    n = framing.stall_note(still, 21.6)
    assert n and n["gap"] > 20
    cut = {"only": ["a"], "keys": {"a": [{"t": 0.0, "at": [0, 0]}, {"t": 4.0, "pose": "cry"}, {"t": 8.0, "face": 10}, {"t": 12.0, "pose": "kneel"}]},
           "cam_keys": [{"t": 0.0, "cam": [3, 0, 1], "look": "a", "lens": 50}, {"t": 6.0, "cam": [2, 1, 1], "look": "a", "lens": 50, "ease": "step"},
                        {"t": 15.0, "cam": [2, 1, 1], "look": "a", "lens": 50}]}
    assert framing.stall_note(cut, 15.0) is None
    assert framing.stall_note(still, 8.0) is None   # 短いカットは見ない
