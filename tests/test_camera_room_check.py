"""tools/camera_room_check.py（描く前のカメラの衝突の確かめ）のテスト（2026-11-08）。"""
import math
import shutil

import camera_room_check as crc
import staging
import yaml
from conftest import ROOT

ROOM = {"name": "room_t", "ja": "部屋", "x": [0.0, 4.0], "y": [0.0, 3.0], "h": 3.0, "sides": ["n", "s", "e", "w"],
        "openings": [{"side": "s", "c": 2.0, "w": 1.0, "h": 2.0, "ja": "扉"}]}


def kinds(C, L, rooms=(ROOM,), walls=(), ceils=(), solids=()):
    return [k for k, _w, _d in crc.check_point(C, L, list(rooms), list(walls), list(ceils), list(solids))]


def test_rect_room():
    assert kinds((2.0, 1.5, 1.5), (1.0, 1.0, 1.2)) == []                 # 中から中
    assert kinds((2.0, 6.0, 1.5), (1.0, 1.0, 1.2)) == ["壁ごし"]          # 南の外から、扉でない所を通して中を見る
    assert kinds((2.0, 6.0, 1.2), (2.0, 1.5, 1.2)) == []                 # 扉を通して見る
    assert kinds((2.0, 0.02, 1.5), (2.0, 2.0, 1.2)) == ["壁の中"]          # 北の壁の厚みの中
    assert kinds((2.0, 0.12, 1.5), (2.0, 2.0, 1.2)) == []                # 壁のすぐ前で、壁に背を向ける（A39-007）
    assert kinds((2.0, 1.5, 3.5), (2.0, 1.5, 1.0)) == ["天井より上"]
    assert kinds((-3.0, -3.0, 12.0), (2.0, 1.5, 1.0)) == ["屋根ごし"]               # 高い所から屋根を通して中を見る
    assert kinds((-3.0, 1.5, 1.5), (9.0, 1.5, 1.0)) == []                # 両方とも外（建物を手前に入れる引き）は数えない


def test_round_wall_with_gap_and_door():
    wall = {"name": "cwall_t", "ja": "円い壁", "at": [0.0, 0.0], "r": 10.0, "h": 9.0, "gap": [[80, 100]], "door": {"at": 180, "w": 3.0, "h": 4.4}}
    assert kinds((0.0, -15.0, 2.0), (0.0, 0.0, 1.0), rooms=(), walls=(wall,)) == ["壁ごし"]   # 北の外から（方位 0）
    assert kinds((15.0, 0.0, 2.0), (0.0, 0.0, 1.0), rooms=(), walls=(wall,)) == []          # 東の破れた所（80〜100 度）
    assert kinds((0.0, 15.0, 2.0), (0.0, 0.0, 1.0), rooms=(), walls=(wall,)) == []          # 南の扉
    assert kinds((0.0, -10.2, 2.0), (0.0, 0.0, 1.0), rooms=(), walls=(wall,)) == ["壁の中"]


def test_A14_007_turned_camera_is_caught(tmp_path):
    """A14-007 を 2026-11-07 の自動の直しで顔の前へ回したカメラ（t 7.0 のキーを、メタリカの周りに -77 度）は、居間の壁の外に出た。
    測定（previs_audit）は壁を見逃し、描いた後の一色の確かめで見つかった。描く前のこの確かめで見つかる。今のカメラ（元に戻したもの）は問題なし。"""
    src = ROOT / "episodes/A/scenes/A14"
    dst = tmp_path / "A14"
    (dst / "shots").mkdir(parents=True)
    shutil.copy(src / "blocking.yaml", dst / "blocking.yaml")
    shutil.copy(src / "shots" / "A14-007.yaml", dst / "shots" / "A14-007.yaml")
    assert "A14-007" not in crc.check_scene(dst)
    bk = yaml.safe_load((dst / "blocking.yaml").read_text(encoding="utf-8"))
    st = staging.Stage(dst)
    keys = sorted(bk["cuts"]["A14-007"]["cam_keys"], key=lambda k: float(k["t"]))
    k = keys[-1]
    mx, my = st.place("m_pat")
    dx, dy = k["cam"][0] - mx, k["cam"][1] - my
    r, b = math.hypot(dx, dy), math.degrees(math.atan2(dx, -dy))
    nb = math.radians(b - 77)
    k["cam"] = [round(mx + r * math.sin(nb), 2), round(my - r * math.cos(nb), 2), k["cam"][2]]
    bk["cuts"]["A14-007"]["cam_keys"] = keys
    (dst / "blocking.yaml").write_text(yaml.safe_dump(bk, allow_unicode=True, sort_keys=False), encoding="utf-8")
    res = crc.check_scene(dst)
    assert "A14-007" in res
    assert {h["kind"] for h in res["A14-007"]} & {"壁ごし", "壁の中"}
