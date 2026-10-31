#!/usr/bin/env python3
"""採用した大きい生成物（静止画・動画・人物セル）を Drive に一時退避し、必要なときに元の場所へ戻す（D-065）。

退避先は、Drive の受け口（tools/drive.py）で書ける「魔女と百騎兵 原作音源」フォルダの中の
「一時退避（生成物）」。元のパスのまま置く。ユーザーが後で別の場所へ移してよい（その場合は manifest の drive 欄を直す）。

  python tools/archive.py plan LIST.txt            # 退避するファイルの一覧（1行1パス）から archive/drive_manifest.yaml を作る
  python tools/archive.py upload [--limit N] [--shard K/N]   # manifest の未送信分を上げる（md5 を確かめる。途中から再開できる）
  python tools/archive.py verify                   # manifest の全件が Drive にあり、サイズが合うかを確かめる
  python tools/archive.py purge                    # 送信と確認が済んだものだけ git rm（作業ツリーから外す）
  python tools/archive.py restore --scene A04      # 場面ごとに Drive から元の場所へ戻す（sha256 を確かめる）
  python tools/archive.py restore PATH…            # パスを指定して戻す

manifest（archive/drive_manifest.yaml）の state は pending → uploaded。
Git の履歴には退避前のファイルが残るので、容量を実際に減らすには履歴の書き換えが別に要る（ユーザーの確認が先）。
"""
import argparse
import fcntl
import hashlib
import pathlib
import subprocess
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import drive  # noqa: E402

ROOT = drive.ROOT
MANIFEST = ROOT / "archive" / "drive_manifest.yaml"
FOLDER = "一時退避（生成物）"


def load():
    return yaml.safe_load(MANIFEST.read_text()) if MANIFEST.exists() else {"folder": FOLDER, "files": []}


def save(m):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        "# 採用した大きい生成物の Drive 退避（tools/archive.py、D-065）。手で編集しない。\n"
        "# drive は「魔女と百騎兵 原作音源」フォルダからの道筋。id は Drive のファイルID。\n"
        + yaml.safe_dump(m, allow_unicode=True, sort_keys=False, default_flow_style=None, width=200)
    )


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cmd_plan(args):
    m = load()
    have = {f["path"] for f in m["files"]}
    n = 0
    for line in pathlib.Path(args.list).read_text().splitlines():
        p = line.strip()
        if not p or p in have:
            continue
        src = ROOT / p
        m["files"].append({"path": p, "bytes": src.stat().st_size, "sha256": sha256(src),
                           "drive": f"{FOLDER}/{p}", "id": None, "state": "pending"})
        n += 1
    m["files"].sort(key=lambda f: f["path"])
    save(m)
    tot = sum(f["bytes"] for f in m["files"])
    print(f"追加 {n} 件。manifest 合計 {len(m['files'])} 件 {tot / 1e6:.0f}MB")


def mark_uploaded(path, fid):
    """複数のプロセスが同時に上げても manifest が壊れないよう、鍵をかけて1件ずつ書き戻す。"""
    with open(MANIFEST.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        m = load()
        for f in m["files"]:
            if f["path"] == path:
                f["id"], f["state"] = fid, "uploaded"
        save(m)


def cmd_upload(args):
    m = load()
    todo = [f for f in m["files"] if f["state"] != "uploaded"]
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        todo = [f for i, f in enumerate(todo) if i % n == k]
    if args.limit:
        todo = todo[: args.limit]
    failed = []
    for i, f in enumerate(todo, 1):
        src = ROOT / f["path"]
        if not src.exists():
            print(f"スキップ（ファイルなし）: {f['path']}")
            continue
        if sha256(src) != f["sha256"]:
            sys.exit(f"manifest の作成後にファイルが変わっています: {f['path']}")
        d = pathlib.PurePosixPath(f["drive"])
        for attempt in range(1, 4):
            try:
                r = drive.put(src, str(d.parent), d.name, replace=True)
                mark_uploaded(f["path"], r["id"])
                break
            except SystemExit as e:
                print(f"  失敗 {attempt}/3: {e}", flush=True)
            except Exception as e:  # ネットワークの一時的な失敗
                print(f"  失敗 {attempt}/3: {e!r}", flush=True)
        else:
            failed.append(f["path"])
        print(f"[{i}/{len(todo)}]", flush=True)
    left = sum(f["state"] != "uploaded" for f in load()["files"])
    print(f"このプロセスの失敗 {len(failed)} 件 / manifest 全体の未送信 {left} 件")
    for p in failed:
        print("  失敗:", p)


def drive_listing(dir_path):
    item = drive.resolve(dir_path)
    return {c["name"]: c for c in drive.children(item["id"])} if item else {}


def cmd_verify(args):
    m = load()
    bad = 0
    by_dir = {}
    for f in m["files"]:
        by_dir.setdefault(str(pathlib.PurePosixPath(f["drive"]).parent), []).append(f)
    for d, fs in sorted(by_dir.items()):
        listing = drive_listing(d)
        for f in fs:
            c = listing.get(pathlib.PurePosixPath(f["drive"]).name)
            if not c or int(c.get("size", -1)) != f["bytes"] or f["state"] != "uploaded":
                bad += 1
                print(f"不一致: {f['path']}（{'Driveに無い' if not c else 'サイズ違い' if int(c.get('size', -1)) != f['bytes'] else '未送信'}）")
    print(f"確認 {len(m['files'])} 件 / 不一致 {bad} 件")
    sys.exit(1 if bad else 0)


def cmd_purge(args):
    m = load()
    ok = [f for f in m["files"] if f["state"] == "uploaded" and (ROOT / f["path"]).exists()]
    if any(f["state"] != "uploaded" for f in m["files"]):
        sys.exit("未送信のファイルがあります。upload と verify を先に終えてください。")
    for f in ok:
        if sha256(ROOT / f["path"]) != f["sha256"]:
            sys.exit(f"ファイルが manifest と違います: {f['path']}")
    for i in range(0, len(ok), 100):
        subprocess.run(["git", "rm", "-q", "--"] + [f["path"] for f in ok[i:i + 100]], cwd=ROOT, check=True)
    print(f"git rm {len(ok)} 件（{sum(f['bytes'] for f in ok) / 1e6:.0f}MB）。コミットは別に行う")


def exclude_locally(path):
    """戻したファイルを誤ってコミットしないよう、このクローンだけの除外（.git/info/exclude）に登録する。"""
    ex = ROOT / ".git" / "info" / "exclude"
    ex.parent.mkdir(parents=True, exist_ok=True)
    lines = ex.read_text().splitlines() if ex.exists() else []
    if "/" + path not in lines:
        ex.write_text("\n".join(lines + ["/" + path]) + "\n")


def cmd_restore(args):
    m = load()
    sel = [f for f in m["files"]
           if (args.scene and f"/scenes/{args.scene}/" in f["path"]) or f["path"] in set(args.paths)]
    if not sel:
        sys.exit("対象がありません（--scene か PATH を指定）")
    for i, f in enumerate(sel, 1):
        dst = ROOT / f["path"]
        if dst.exists() and sha256(dst) == f["sha256"]:
            continue
        ns = argparse.Namespace(path=f"id:{f['id']}", dst=str(dst))
        drive.cmd_get(ns)
        if sha256(dst) != f["sha256"]:
            sys.exit(f"sha256 が合いません: {f['path']}")
        exclude_locally(f["path"])
        print(f"[{i}/{len(sel)}] 戻した: {f['path']}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("list"); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("upload"); p.add_argument("--limit", type=int)
    p.add_argument("--shard", help="K/N（N 個のプロセスで分けて上げるときの K 番目。0 始まり）")
    p.set_defaults(fn=cmd_upload)
    p = sub.add_parser("verify"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("purge"); p.set_defaults(fn=cmd_purge)
    p = sub.add_parser("restore"); p.add_argument("paths", nargs="*"); p.add_argument("--scene")
    p.set_defaults(fn=cmd_restore)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
