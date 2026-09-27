#!/usr/bin/env python3
"""Drive の「魔女と百騎兵 原作音源」フォルダと、受け口（setup/drive_bridge.gs）を通してファイルをやり取りする。

Drive コネクタは 10MB を超えるファイルを読み書きできないので、大きい音声はこれを使う。
読み書きできるのは「魔女と百騎兵 原作音源」フォルダの中だけ。パスはそのフォルダからの道筋で書く。

  python tools/drive.py ls                                  # フォルダの中身
  python tools/drive.py ls 声とBGMの分離/パート17
  python tools/drive.py get "魔女と百騎兵Revival パート17.mp3" tmp/src/part17.mp3
  python tools/drive.py put tmp/out/パート17_声.mp3 声とBGMの分離/パート17 --replace
  python tools/drive.py mkdir 声とBGMの分離/パート17

受け口の URL は pipeline/config.yaml の drive_bridge.url。
鍵は環境変数 DRIVE_BRIDGE_KEY（クラウド環境の設定で入れる。ファイルやコマンドに書かない）。
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDER = "application/vnd.google-apps.folder"
CHUNK = 16 * 1024 * 1024        # 送るときの一切れ（256KiB の倍数）
DL_CHUNK = 8 * 1024 * 1024      # 受け口が一度に返せる上限


def bridge_url():
    cfg = yaml.safe_load((ROOT / "pipeline" / "config.yaml").read_text(encoding="utf-8"))
    return cfg["drive_bridge"]["url"]


def call(action, **params):
    key = os.environ.get("DRIVE_BRIDGE_KEY")
    if not key:
        sys.exit("DRIVE_BRIDGE_KEY がありません。クラウド環境の設定で環境変数に入れてください（setup/drive_bridge.md）。")
    body = json.dumps(dict(params, action=action, key=key)).encode()
    # 鍵をコマンドラインに出さないよう、本文は標準入力で渡す
    r = subprocess.run(["curl", "-sS", "-L", "--max-time", "600", "-H", "Content-Type: application/json",
                        "--data-binary", "@-", bridge_url()], input=body, capture_output=True, check=True)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        sys.exit(f"受け口の返事が読めません: {r.stdout[:300]!r}")
    if not out.get("ok"):
        sys.exit(f"受け口のエラー（{action}）: {out.get('error')}")
    return out


# ---------- 道筋 ----------

def children(folder_id=None):
    return call("list", **({"folder": folder_id} if folder_id else {}))["files"]


def resolve(path):
    """道筋からファイル（またはフォルダ）を探す。見つからなければ None。"""
    cur = None
    parts = [p for p in str(path).split("/") if p]
    item = None
    for i, name in enumerate(parts):
        found = [f for f in children(cur) if f["name"] == name]
        if not found:
            return None
        item = found[0]
        cur = item["id"]
    return item


# ---------- コマンド ----------

def cmd_ls(args):
    folder = None
    if args.path:
        item = resolve(args.path)
        if not item:
            sys.exit(f"見つかりません: {args.path}")
        folder = item["id"]
    for f in sorted(children(folder), key=lambda f: (f["mimeType"] != FOLDER, f["name"])):
        size = f"{int(f['size']) / 1e6:8.1f}MB" if f.get("size") else "  フォルダ" if f["mimeType"] == FOLDER else ""
        print(f"{size}  {f['name']}  ({f['id']})")


def cmd_mkdir(args):
    print(call("mkdir", path=args.path)["id"])


def cmd_get(args):
    if args.path.startswith("id:"):
        fid = args.path[3:]
    else:
        item = resolve(args.path)
        if not item:
            sys.exit(f"見つかりません: {args.path}")
        fid = item["id"]
    info = call("info", id=fid)["file"]
    size = int(info["size"])
    dst = pathlib.Path(args.dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    md5 = hashlib.md5()
    with open(dst, "wb") as fh:
        start = 0
        while start < size:
            r = call("download", id=fid, start=start, length=DL_CHUNK)
            data = base64.b64decode(r["data"])
            if not data:
                sys.exit(f"途中で読めなくなりました（{start}/{size}）")
            fh.write(data)
            md5.update(data)
            start += len(data)
            print(f"  {start / 1e6:.1f}/{size / 1e6:.1f}MB", flush=True)
    if info.get("md5Checksum") and md5.hexdigest() != info["md5Checksum"]:
        sys.exit("md5 が合いません。もう一度読み込んでください。")
    print(f"{dst}（{size / 1e6:.1f}MB、md5 一致）")


def _put_direct(url, src, size):
    """窓口 URL へ一切れずつ送る。成功したら Drive のファイル情報を返す。"""
    with open(src, "rb") as fh, tempfile.TemporaryDirectory() as td:
        start = 0
        while True:
            fh.seek(start)
            data = fh.read(CHUNK)
            end = start + len(data) - 1
            hdr = pathlib.Path(td) / "h"
            rng = f"bytes {start}-{end}/{size}" if data else f"bytes */{size}"
            r = subprocess.run(["curl", "-sS", "--max-time", "600", "-X", "PUT", "-D", str(hdr),
                                "-H", f"Content-Range: {rng}", "--data-binary", "@-", "-w", "\n%{http_code}", url],
                               input=data, capture_output=True, check=True)
            body, _, code = r.stdout.decode(errors="replace").rpartition("\n")
            code = int(code)
            if code in (200, 201):
                return json.loads(body)
            if code != 308:
                return {"_error": f"{code} {body[:300]}"}
            # 308: 受け取った範囲の次から
            got = [l for l in hdr.read_text(errors="replace").splitlines() if l.lower().startswith("range:")]
            start = int(got[-1].split("-")[-1]) + 1 if got else 0
            print(f"  {start / 1e6:.1f}/{size / 1e6:.1f}MB", flush=True)


def _put_via_bridge(url, src, size):
    """予備: 受け口を経由して送る（1回 8MB）。"""
    with open(src, "rb") as fh:
        start = 0
        while start < size:
            data = fh.read(DL_CHUNK)
            r = call("put", url=url, start=start, total=size, data=base64.b64encode(data).decode())
            start += len(data)
            print(f"  {start / 1e6:.1f}/{size / 1e6:.1f}MB（受け口経由）", flush=True)
            if r.get("done"):
                return r["file"]
    sys.exit("送り終えたのに完了の返事がありません")


def mime_of(path):
    return {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".flac": "audio/flac",
            ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json",
            ".srt": "text/plain", ".tsv": "text/tab-separated-values"}.get(pathlib.Path(path).suffix.lower(),
                                                                          "application/octet-stream")


def put(src, dir_path, name=None, replace=False):
    src = pathlib.Path(src)
    size = src.stat().st_size
    name = name or src.name
    folder = call("mkdir", path=dir_path)["id"]
    url = call("upload", folder=folder, name=name, mime=mime_of(src), replace="1" if replace else "0")["url"]
    if not url:
        sys.exit("受け口が窓口 URL を返しませんでした")
    f = _put_direct(url, src, size)
    if "_error" in f:
        print(f"  直接送れませんでした（{f['_error']}）。受け口経由で送り直します。", flush=True)
        url = call("upload", folder=folder, name=name, mime=mime_of(src), replace="1" if replace else "0")["url"]
        f = _put_via_bridge(url, src, size)
    local = hashlib.md5(src.read_bytes()).hexdigest()
    if f.get("md5Checksum") and f["md5Checksum"] != local:
        sys.exit(f"md5 が合いません: {name}")
    print(f"{dir_path}/{name}（{size / 1e6:.1f}MB、id {f.get('id')}）")
    return f


def cmd_put(args):
    put(args.src, args.dir, args.name, args.replace)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ls"); p.add_argument("path", nargs="?"); p.set_defaults(fn=cmd_ls)
    p = sub.add_parser("mkdir"); p.add_argument("path"); p.set_defaults(fn=cmd_mkdir)
    p = sub.add_parser("get", help="道筋か id:<ファイルID>"); p.add_argument("path"); p.add_argument("dst")
    p.set_defaults(fn=cmd_get)
    p = sub.add_parser("put"); p.add_argument("src"); p.add_argument("dir"); p.add_argument("--name")
    p.add_argument("--replace", action="store_true", help="同じ名前のファイルをゴミ箱に入れてから置く")
    p.set_defaults(fn=cmd_put)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
