#!/usr/bin/env python3
"""原作の音源と台詞の順の見張り（2026-11-08、previs の根本改善のフェーズ0）。

下絵の道具を直す作業で、原作の音源（audio/ の下）、台詞の置き場所（各場面の voice.yaml）、
ショットファイルの tc・sec・dialogue（台詞の順と秒）を変えていないことを、Git の基準（既定 origin/main）と比べて確かめる。
変わっていれば一覧を出して終わりの番号 1 で止まる（テストと PR の前に走らせる）。ショットファイルのほかの項目（camera など）の変化は数えない。

  python tools/guard_audio.py                 # origin/main と比べる
  python tools/guard_audio.py --base HEAD~3   # 基準を変える
"""
import argparse
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOT_KEYS = ("tc", "sec", "dialogue")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def changed(base):
    """基準から、作業ツリー（コミットしていない変更と、まだ Git に入れていないファイルも）までに変わったファイル。"""
    out = set(git("diff", "--name-only", base, "--", "audio", "episodes").stdout.split())
    out |= set(git("ls-files", "--others", "--exclude-standard", "--", "audio", "episodes").stdout.split())
    return sorted(out)


def check(base):
    bad = []
    if git("rev-parse", "--verify", "--quiet", base).returncode != 0:
        return [f"基準 {base} が見つからない（git fetch origin main の後で）"]
    for f in changed(base):
        p = ROOT / f
        if f.startswith("audio/"):
            bad.append(f"音源が変わった: {f}")
        elif f.endswith("/voice.yaml"):
            bad.append(f"台詞の置き場所が変わった: {f}")
        elif "/shots/" in f and f.endswith(".yaml"):
            old = git("show", f"{base}:{f}")
            a = (yaml.safe_load(old.stdout) or {}) if old.returncode == 0 else None
            b = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else None
            if a is None or b is None:
                bad.append(f"ショットファイルが{'増えた' if a is None else '消えた'}: {f}")
                continue
            for k in SHOT_KEYS:
                if a.get(k) != b.get(k):
                    bad.append(f"ショットファイルの {k} が変わった: {f}（{a.get(k)!r} → {b.get(k)!r}）")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main")
    a = ap.parse_args()
    bad = check(a.base)
    if bad:
        print("\n".join(bad))
        print(f"見張り: {len(bad)} 件（{a.base} と比べて）")
        sys.exit(1)
    print(f"見張り: 音源・voice.yaml・ショットファイルの tc・sec・dialogue は {a.base} と同じ")


if __name__ == "__main__":
    main()
