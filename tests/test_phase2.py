"""フェーズ2（2026-11-08）のテスト: 向きを変える秒 turn、カメラの aim: eyes と face_y、顔の目印と横の角度、うつむきの抑えの合計化。
turn・aim・目印・角度は Blender なし。うつむきの抑えは tools/previs_rig.py が bpy を読むので、bpy のある環境だけで走る。"""
import math

import framing
import framing_fix
import motion
import pytest


def place(v):
    return tuple(v)


# ---------- turn ----------

def test_face_without_turn_is_unchanged():
    """turn を書かなければ今までどおり、キーの間ずっとかけて向きを変える（既定の動きは変えない）。"""
    tr = motion.PersonTrack([{"t": 0.0, "at": [0, 0], "face": 100}, {"t": 10.0, "face": 300}], place)
    kind, fa, fb, u = tr.face_key(5.0)
    assert kind == "lerp" and fa == 100 and fb == 300 and abs(u - 0.5) < 1e-9


def test_face_with_turn_holds_then_turns():
    """turn: 0.5 なら、キーの 0.5 秒前までは前の向きのまま、最後の 0.5 秒で向きを変える（A36-010 の 5.4 秒かけた振り向き）。"""
    tr = motion.PersonTrack([{"t": 0.0, "at": [0, 0], "face": 100}, {"t": 10.0, "face": 300, "turn": 0.5}], place)
    assert tr.face_key(5.0)[0] == "hold" and tr.face_key(5.0)[1] == 100
    assert tr.face_key(9.4)[0] == "hold"
    kind, fa, fb, u = tr.face_key(9.75)
    assert kind == "lerp" and abs(u - 0.5) < 1e-9
    assert tr.face_key(10.0)[3] == 1.0


# ---------- aim: eyes と face_y ----------

def _cam_track():
    return motion.CamTrack([{"t": 0.0, "cam": [0.0, -5.0, 1.5], "look": "a", "look_h": 1.0, "aim": "eyes", "face_y": 0.6},
                            {"t": 2.0, "cam": [0.0, -5.0, 1.5], "look": "a"}])


def _at(ct, t, aim):
    return ct.at(t, {"a": (0.0, 0.0)}, lambda v, p: tuple(p[v]), lambda v: 1.2, aim=aim)


def test_aim_eyes_only_on_written_key():
    ct = _cam_track()
    eyes = lambda who: (0.1, 0.2, 1.43)   # noqa: E731
    cam, look, lh, lens = _at(ct, 0.0, eyes)
    assert look == (0.1, 0.2) and abs(lh - 1.43) < 1e-9
    cam, look, lh, lens = _at(ct, 2.0, eyes)   # aim を書いていないキー: 引き継いだ look_h 1.0
    assert look == (0.0, 0.0) and abs(lh - 1.0) < 1e-9
    assert "aim" not in ct.k[1]


def test_aim_without_function_falls_back():
    """目の位置を知らない道具（略図・tools/camera_room_check.py）は look_h で計算する。"""
    cam, look, lh, lens = _at(_cam_track(), 0.0, None)
    assert look == (0.0, 0.0) and abs(lh - 1.0) < 1e-9


def test_face_y_interpolation():
    ct = _cam_track()
    assert abs(ct.face_y_at(0.0) - 0.6) < 1e-9
    assert abs(ct.face_y_at(2.0) - 0.5) < 1e-9
    assert motion.CamTrack([{"t": 0.0, "cam": [0, 0, 1], "look": "a"}]).face_y_at(0.0) is None


# ---------- 目印と横の角度 ----------

def test_landmark_occlusion():
    assert framing._lm_occ({"lm": {"le": "ok", "re": "tswamp_brim", "mo": "ok"}}) is None          # 片目が見える
    assert framing._lm_occ({"lm": {"le": "tswamp_brim", "re": "tswamp_brim", "mo": "ok"}}) == "tswamp_brim"   # 両目が隠れる（A30-013）
    assert framing._lm_occ({"lm": {"le": "tswamp_brim", "re": "tswamp_brim", "mo": "bisco_body"}}) == "tswamp_brim"
    assert framing._lm_occ({"lm": {"le": "back", "re": "back", "mo": "back"}}) is None          # 向こう向きは別の印
    assert framing._lm_occ({"lm": {"le": "ok", "re": "ok", "mo": "berda_hand1"}}) is None       # 目が見える
    assert framing._lm_occ({"lm": {"le": "berda_hand1", "re": "berda_fore1", "mo": "berda_hand1"}}) is None   # 手で顔を覆う芝居


def test_occ_of_uses_landmarks_when_measured():
    s = {"frame": "寄り", "people": {"t": {"occ": "tswamp_brim", "lm": {"le": "ok", "re": "ok", "mo": "ok"}}}}
    assert framing.occ_of(s, "t") is None              # 目印が見えれば、頭の真ん中への線が当たっても隠れていない
    s = {"frame": "寄り", "people": {"t": {"occ": "tswamp_brim"}}}
    assert framing.occ_of(s, "t") == "tswamp_brim"     # 古い測定（目印なし）は今までどおり


def test_face_angle_prefers_horizontal():
    assert framing.face_angle({"fa": 120, "fay": 60}) == 60
    assert framing.face_angle({"fa": 120}) == 120


def test_turn_threshold_matches_judgement():
    """framing_fix の回り込みは、判定（105 度）と同じ物差し。前は 100 度で回していた。"""
    def at(d):
        b = math.radians(d)
        return framing_fix._turn(math.sin(b), -math.cos(b), 0.0)
    assert at(103.0) is None
    assert at(110.0) is not None


# ---------- うつむきの抑え（bpy のある環境だけ） ----------

def test_brim_pitch_uses_total():
    previs_rig = pytest.importorskip("previs_rig")   # bpy を読む
    cry = previs_rig.POSES["cry"]
    lean, head = float(cry["lean"]), float(cry["head"][0])
    assert lean + previs_rig.brim_head_pitch(lean, head) < 25.0   # 前は頭だけ抑えて合計 40 度
    assert previs_rig.brim_head_pitch(8.0, 0.0) == 0.0            # 16 度以下は変えない
    assert previs_rig.brim_head_pitch(40.0, 0.0) >= previs_rig.BRIM_HEAD_MIN
