"""Lyria（Gemini API）で BGM を作る。

  python tools/lyria.py audio/music/a01_morning.yaml             # 生成して記録
  python tools/lyria.py audio/music/a01_morning.yaml --dry-run   # 送る内容だけ表示

設計ファイル（YAML）: model, target（カットIDかシーン）, prompt, out（出力のパス。拡張子 .wav）
APIキーはクラウド環境の「API認証情報」で自動付与される。キーをファイルやコマンドに書かない。
実在の作曲家・演奏者・曲名を指示文に入れない（原曲の再現はしない、D-023）。
"""
import argparse
import base64
import json
import pathlib
import subprocess
import sys
import time

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gemini_tts import ROOT, call, find_audio, show, wav_seconds  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("spec")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    spec = yaml.safe_load(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    body = {
        "model": spec["model"],
        "input": spec["prompt"].strip(),
        "response_format": {"type": "audio"},   # 既定の MP3（lyria-3.5 は WAV 指定を受け付けない）
    }
    t0 = time.time()
    res = call("POST", "/interactions", body, a.dry_run)
    if res is None:
        return
    audio = find_audio(res)
    if not audio:
        sys.exit("応答に音声がありません: " + json.dumps(res, ensure_ascii=False)[:800])
    out = ROOT / spec["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 2
    base = out
    while out.exists():
        out = base.with_name(f"{base.stem}_{n:02d}{base.suffix}")
        n += 1
    out.write_bytes(base64.b64decode(audio))
    sec = wav_seconds(out)
    subprocess.run([sys.executable, str(ROOT / "tools" / "record.py"), "add",
                    "--target", spec["target"], "--role", "music", "--model", spec["model"], "--via", "gemini-api",
                    "--prompt", spec["prompt"].strip(), "--prompt-file", a.spec, "--output", str(out.relative_to(ROOT)),
                    "--gen-sec", f"{time.time() - t0:.1f}", "--note", f"音声{sec}秒"]
                   + (["--cost-usd", str(spec["cost_usd"])] if spec.get("cost_usd") else []), check=True)
    print(f"{show(out)}（{sec}秒）")


if __name__ == "__main__":
    main()
