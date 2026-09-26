#!/usr/bin/env python3
"""生成とQCの記録（D-034: ショットごとに、使ったサービスと費用を記録する）。

記録は logs/generations.jsonl に1行1件で追記する（ショットファイルのコメントを壊さないため）。

使い方:
  # 生成を記録
  python tools/record.py add --target A01-001 --role image --model nano-banana-pro --via higgsfield \
      --prompt-file bakeoff/shots/A01-001.yaml --output bakeoff/results/image/A01-001/nano-banana-pro_01.png \
      --credits 2 --gen-sec 35 --note "1回目"

  # QCを記録
  python tools/record.py qc --target A01-001 --output bakeoff/results/image/A01-001/nano-banana-pro_01.png \
      --result pass --note "沼の緑は良い。塔の段数が少ない"

  # 集計（モデル別、対象別）
  python tools/record.py summary
  python tools/record.py summary --by target

  # ある対象の記録を一覧
  python tools/record.py show --target A01-001
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "generations.jsonl"
JST = dt.timezone(dt.timedelta(hours=9))


def now():
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def append(entry):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load():
    if not LOG.exists():
        return []
    rows = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def rel(p):
    if not p:
        return None
    path = pathlib.Path(p)
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def cmd_add(a):
    prompt = a.prompt
    if a.prompt_file:
        prompt = {"file": rel(a.prompt_file), "text": prompt} if prompt else {"file": rel(a.prompt_file)}
    entry = {
        "kind": "generation",
        "at": now(),
        "target": a.target,
        "role": a.role,
        "model": a.model,
        "via": a.via,
        "prompt": prompt,
        "refs": a.refs or [],
        "output": rel(a.output),
        "credits": a.credits,
        "cost_usd": a.cost_usd,
        "gen_sec": a.gen_sec,
        "note": a.note,
    }
    append(entry)
    print(f"記録しました: {a.target} / {a.model} -> {entry['output']}")


def cmd_qc(a):
    entry = {
        "kind": "qc",
        "at": now(),
        "target": a.target,
        "output": rel(a.output),
        "result": a.result,
        "note": a.note,
    }
    append(entry)
    print(f"QCを記録しました: {a.target} {a.result}")


def cmd_summary(a):
    rows = [r for r in load() if r.get("kind") == "generation"]
    if not rows:
        print("記録はまだありません。")
        return
    key = "model" if a.by == "model" else "target"
    agg = defaultdict(lambda: {"n": 0, "credits": 0.0, "usd": 0.0, "sec": 0.0, "unknown_cost": 0})
    for r in rows:
        g = agg[r.get(key) or "?"]
        g["n"] += 1
        if r.get("credits") is not None:
            g["credits"] += r["credits"]
        if r.get("cost_usd") is not None:
            g["usd"] += r["cost_usd"]
        if r.get("credits") is None and r.get("cost_usd") is None:
            g["unknown_cost"] += 1
        if r.get("gen_sec") is not None:
            g["sec"] += r["gen_sec"]
    label = "モデル" if key == "model" else "対象"
    print(f"| {label} | 件数 | クレジット | USD | 生成時間(秒) | 費用未記入 |")
    print("| --- | --- | --- | --- | --- | --- |")
    tot = {"n": 0, "credits": 0.0, "usd": 0.0, "sec": 0.0, "unknown_cost": 0}
    for k in sorted(agg):
        g = agg[k]
        print(f"| {k} | {g['n']} | {g['credits']:g} | {g['usd']:.2f} | {g['sec']:g} | {g['unknown_cost']} |")
        for f in tot:
            tot[f] += g[f]
    print(f"| 合計 | {tot['n']} | {tot['credits']:g} | {tot['usd']:.2f} | {tot['sec']:g} | {tot['unknown_cost']} |")


def cmd_show(a):
    rows = [r for r in load() if r.get("target") == a.target]
    if not rows:
        print(f"{a.target} の記録はありません。")
        return
    for r in rows:
        if r["kind"] == "generation":
            cost = r.get("credits") if r.get("credits") is not None else r.get("cost_usd")
            print(f"{r['at']}  生成  {r['role']}/{r['model']}  費用={cost}  {r.get('output')}  {r.get('note') or ''}")
        else:
            print(f"{r['at']}  QC    {r['result']}  {r.get('output')}  {r.get('note') or ''}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="生成を記録")
    s.add_argument("--target", required=True, help="カットID（A01-001）または声の対象（voice:metallica）")
    s.add_argument("--role", required=True, choices=["image", "video", "voice", "music", "sfx", "other"])
    s.add_argument("--model", required=True)
    s.add_argument("--via", default=None, help="higgsfield / gemini-api など")
    s.add_argument("--prompt", default=None, help="実際に送った指示文")
    s.add_argument("--prompt-file", default=None, help="指示文の出典ファイル")
    s.add_argument("--refs", nargs="*", help="添付した参照画像")
    s.add_argument("--output", default=None)
    s.add_argument("--credits", type=float, default=None, help="サービスのクレジット消費")
    s.add_argument("--cost-usd", type=float, default=None)
    s.add_argument("--gen-sec", type=float, default=None, help="生成にかかった秒数")
    s.add_argument("--note", default=None)
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("qc", help="QCを記録")
    s.add_argument("--target", required=True)
    s.add_argument("--output", default=None)
    s.add_argument("--result", required=True, choices=["pass", "fail", "hold"])
    s.add_argument("--note", default=None)
    s.set_defaults(func=cmd_qc)

    s = sub.add_parser("summary", help="費用と件数を集計")
    s.add_argument("--by", choices=["model", "target"], default="model")
    s.set_defaults(func=cmd_summary)

    s = sub.add_parser("show", help="対象の記録を一覧")
    s.add_argument("--target", required=True)
    s.set_defaults(func=cmd_show)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    sys.exit(main())
