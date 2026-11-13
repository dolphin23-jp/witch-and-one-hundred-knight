"""立ち位置の文（tools/staging.py の staging_en。静止画の指示文になる）の直しの確かめ（2026-11-13、A30-018〜023 の組み直し）。Blender は使わない。"""
import math

import staging
from conftest import ROOT

A30 = ROOT / "episodes" / "A" / "scenes" / "A30"


def _text(st, cut, which):
    return st.staging_en(cut, st.frame(cut, which), which)


def test_axis_declared_per_cut_does_not_crash_checks():
    """カットの axis: で宣言した軸（cuts の範囲がない書き方）で checks() が KeyError: 'cuts' にならない。"""
    st = staging.Stage(str(A30))
    res = st.checks()
    assert res["axis"] == []


def test_posture_from_keys_is_written():
    """keys の姿勢（lie・kneel）が文に出る（前は pose を書かないと全員 standing）。"""
    st = staging.Stage(str(A30))
    t = _text(st, "A30-022", "end")
    assert t.count("lying on the ground") >= 2   # 倒れた百騎兵と沼のテレッサ
    assert "leaning back and laughing" in t      # カットの pose_end の言葉が先


def test_only_start_end_and_small_uses_lens():
    """only_start・only_end でカットの中の切り替えの始めと終わりの画の人物を決める。「small in the background」はレンズを見る。"""
    st = staging.Stage(str(A30))
    s = _text(st, "A30-021", "start")
    assert s.startswith("In frame (exactly one person): Teressa") and "kneeling in the swamp" in s
    assert _text(st, "A30-021", "end").startswith("In frame (exactly one person): Berda")
    end20 = _text(st, "A30-020", "end")
    teressa = next(p for p in end20.split("; ") if p.startswith("Teressa"))
    assert "small in the background" not in teressa   # 62mm の肩なめの真ん中の主役


def test_eye_look_h_for_aim_eyes():
    """aim: eyes のカメラのキーは、見る人物の目の高さ（姿勢で低くなる）を look_h にする。"""
    st = staging.Stage(str(A30))
    c = staging.with_eye_look_h(st, "A30-021", "start", staging.cam_at(st.bk["cuts"]["A30-021"], "start"))
    eye = st.lay["people"]["teressa-swamp"].get("eye", 1.4)
    assert math.isclose(c["look_h"], round(eye * staging.POSTURE_H["kneeling"], 2))
