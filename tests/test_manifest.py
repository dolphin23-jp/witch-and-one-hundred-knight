"""版の記録と照合（tools/manifest.py・tools/previs_status.py）のテスト（2026-11-08、previs の根本改善のフェーズ1）。Blender は使わない。"""
import shutil

import manifest
import yaml
from conftest import ROOT


def copy_scene(tmp_path, scene="A36"):
    src = ROOT / "episodes/A/scenes" / scene
    dst = tmp_path / scene
    (dst / "shots").mkdir(parents=True)
    shutil.copy(src / "blocking.yaml", dst / "blocking.yaml")
    shutil.copy(src / "voice.yaml", dst / "voice.yaml")
    for f in (src / "shots").glob("*.yaml"):
        shutil.copy(f, dst / "shots" / f.name)
    return dst


def hashes(sd):
    si = manifest.SceneInputs(sd)
    return {c: si.input_hash(c) for c in si.st.order}


def test_same_input_same_hash(tmp_path):
    sd = copy_scene(tmp_path)
    assert hashes(sd) == hashes(sd)
    assert hashes(sd) == hashes(ROOT / "episodes/A/scenes/A36")   # 置き場所ではなく中身で決まる（台詞の wav は audio/A36 を見る）


def test_camera_change_marks_only_that_cut(tmp_path):
    """カメラだけを変えると、そのカットだけが「入力が古い」になる。"""
    sd = copy_scene(tmp_path)
    before = hashes(sd)
    bk = yaml.safe_load((sd / "blocking.yaml").read_text(encoding="utf-8"))
    bk["cuts"]["A36-014"]["cam_keys"][0]["lens"] = 50
    (sd / "blocking.yaml").write_text(yaml.safe_dump(bk, allow_unicode=True, sort_keys=False), encoding="utf-8")
    after = hashes(sd)
    assert [c for c in before if before[c] != after[c]] == ["A36-014"]


def test_shot_and_voice_change(tmp_path):
    """ショットファイルの変化はそのカットだけ。台詞の時刻の変化は、その台詞がかかるカットだけ。"""
    sd = copy_scene(tmp_path)
    before = hashes(sd)
    p = sd / "shots" / "A36-010.yaml"
    sh = yaml.safe_load(p.read_text(encoding="utf-8"))
    sh["acting"] = str(sh.get("acting", "")) + "（テスト）"
    p.write_text(yaml.safe_dump(sh, allow_unicode=True, sort_keys=False), encoding="utf-8")
    after = hashes(sd)
    assert [c for c in before if before[c] != after[c]] == ["A36-010"]


def test_person_key_change_propagates_forward(tmp_path):
    """人物の動きを変えると、そのカットと、その立ち位置を引き継ぐ後のカットが古くなる（前のカットは変わらない）。"""
    sd = copy_scene(tmp_path)
    before = hashes(sd)
    bk = yaml.safe_load((sd / "blocking.yaml").read_text(encoding="utf-8"))
    ks = bk["cuts"]["A36-010"]["keys"]["luckiny"]
    ks[-1]["face"] = 10
    (sd / "blocking.yaml").write_text(yaml.safe_dump(bk, allow_unicode=True, sort_keys=False), encoding="utf-8")
    after = hashes(sd)
    changed = [c for c in before if before[c] != after[c]]
    order = list(before)
    assert changed and changed[0] == "A36-010"
    assert all(order.index(c) >= order.index("A36-010") for c in changed)


def test_compare_and_record(tmp_path):
    assert manifest.compare(None, "a", "t") == "記録なし"
    assert manifest.compare({"input_hash": "a", "tool_hash": "t"}, "a", "t") == "最新"
    assert manifest.compare({"input_hash": "a", "tool_hash": "t"}, "b", "t") == "入力が古い"
    assert manifest.compare({"input_hash": "a", "tool_hash": "t"}, "a", "u") == "道具が古い"
    f = tmp_path / "x.jpg"
    f.write_bytes(b"abc")
    manifest.record("A99", "A99-001", "check", manifest.entry("a", "t", [f], fps=12), out_dir=tmp_path)
    rec = manifest.load("A99", out_dir=tmp_path)["A99-001"]["check"]
    assert rec["input_hash"] == "a" and rec["fps"] == 12 and list(rec["files"].values()) == [manifest.file_sha(f)]


def test_status_finds_old_motion_sheets():
    """記録のない motion シート（2026-11-01 の旧人形のもの）は、Git から「古い（推定）」と出る。
    2026-11-08 に描き直した A13-005・A14-007・A15-014・A16-009・A17-026・A17-028 は記録ができたので、推定ではなく記録で照合する。"""
    import manifest
    import previs_status
    for scene, cut in (("A13", "A13-005"), ("A14", "A14-007"), ("A15", "A15-014"), ("A16", "A16-009"), ("A17", "A17-026"), ("A17", "A17-028")):
        res = previs_status.scene_status(ROOT / "episodes/A/scenes" / scene, {cut})
        assert res[cut]["motion"] in ("最新", "入力が古い", "道具が古い"), (cut, res[cut]["motion"])
    for scene, cut in (("A13", "A13-004"), ("A14", "A14-006"), ("A16", "A16-008"), ("A17", "A17-025")):
        if (manifest.load(scene).get(cut) or {}).get("motion"):
            continue   # 後で描き直して記録ができたもの
        res = previs_status.scene_status(ROOT / "episodes/A/scenes" / scene, {cut})
        assert res[cut]["motion"].startswith("古い"), (cut, res[cut]["motion"])
