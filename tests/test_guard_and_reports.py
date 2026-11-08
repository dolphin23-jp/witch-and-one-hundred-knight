"""見張り（tools/guard_audio.py）と、食い違いの一覧（tools/shot_camera_diff.py）の動きの確かめ（2026-11-08）。"""
import guard_audio
import shot_camera_diff


def test_guard_audio_against_head():
    """作業ツリーで音源・voice.yaml・ショットファイルの tc・sec・dialogue を変えていない（基準は HEAD）。"""
    assert guard_audio.check("HEAD") == []


def test_shot_camera_diff_rows_have_fields():
    rows, skipped, total = shot_camera_diff.compare()
    assert total > 0
    for r in rows[:5]:
        assert {"scene", "cut", "dist", "shot_pos", "bk_cam", "shot_lens", "bk_lens"} <= set(r)
