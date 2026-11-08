"""退避の一覧（tools/archive.py）で、Git の外の下絵の mp4（2026-11-08 オーナー判断 (b)）を扱うテスト。Drive にはつながない。"""
import argparse
import subprocess

import pytest

import archive


def _setup(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    monkeypatch.setattr(archive, "MANIFEST", tmp_path / "archive" / "drive_manifest.yaml")
    mp4 = tmp_path / "previs" / "A99" / "A99-001.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.write_bytes(b"first")
    lst = tmp_path / "list.txt"
    lst.write_text("previs/A99/A99-001.mp4\n")
    return mp4, lst


def test_plan_refreshes_pending_after_rerender(tmp_path, monkeypatch):
    """まだ上げていないものを描き直したら、plan をもう一度で sha256 を測り直す（upload が止まらない）。上げたものは変えない。"""
    mp4, lst = _setup(tmp_path, monkeypatch)
    archive.cmd_plan(argparse.Namespace(list=str(lst)))
    first = archive.load()["files"][0]["sha256"]
    mp4.write_bytes(b"second render")
    archive.cmd_plan(argparse.Namespace(list=str(lst)))
    f = archive.load()["files"][0]
    assert f["sha256"] != first and f["bytes"] == len(b"second render") and f["state"] == "pending"
    m = archive.load()
    m["files"][0]["state"] = "uploaded"
    archive.save(m)
    mp4.write_bytes(b"third")
    archive.cmd_plan(argparse.Namespace(list=str(lst)))
    assert archive.load()["files"][0]["bytes"] == len(b"second render")


def test_purge_skips_files_outside_git(tmp_path, monkeypatch):
    """初めから Git の外の mp4 は git rm しない（手元のファイルも消さない）。"""
    mp4, lst = _setup(tmp_path, monkeypatch)
    archive.cmd_plan(argparse.Namespace(list=str(lst)))
    m = archive.load()
    m["files"][0]["state"] = "uploaded"
    archive.save(m)
    archive.cmd_purge(argparse.Namespace())
    assert mp4.exists()


def test_purge_not_blocked_by_pending_outside_git(tmp_path, monkeypatch):
    """Git の外の mp4 がまだ上げていなくても、Git にある上げ済みのものの purge は止めない。Git にある未送信は止める。"""
    mp4, lst = _setup(tmp_path, monkeypatch)
    archive.cmd_plan(argparse.Namespace(list=str(lst)))
    png = tmp_path / "episodes" / "x.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"png")
    subprocess.run(["git", "add", "episodes/x.png"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x"], cwd=tmp_path, check=True)
    (tmp_path / "list2.txt").write_text("episodes/x.png\n")
    archive.cmd_plan(argparse.Namespace(list=str(tmp_path / "list2.txt")))
    m = archive.load()
    for f in m["files"]:
        if f["path"] == "episodes/x.png":
            f["state"] = "uploaded"
    archive.save(m)
    archive.cmd_purge(argparse.Namespace())
    assert not png.exists() and mp4.exists()
    png.parent.mkdir(parents=True, exist_ok=True)   # git rm で空のフォルダも消える
    png.write_bytes(b"png2")
    subprocess.run(["git", "add", "episodes/x.png"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "y"], cwd=tmp_path, check=True)
    (tmp_path / "list3.txt").write_text("episodes/x.png\n")
    m = archive.load()
    m["files"] = [f for f in m["files"] if f["path"] != "episodes/x.png"]
    archive.save(m)
    archive.cmd_plan(argparse.Namespace(list=str(tmp_path / "list3.txt")))
    with pytest.raises(SystemExit):
        archive.cmd_purge(argparse.Namespace())
