#!/usr/bin/env python3
"""Gemini 3.8 Flash TTS で声を設計し、台詞を読ませる。

APIキー:
  クラウド環境の「API認証情報」に登録しておけば、送信時に自動で付く（キーはこのセッションから見えない）。
  手元で動かすときだけ、環境変数 GEMINI_API_KEY を使う。

使い方:
  python tools/gemini_tts.py design bakeoff/voices/metallica.yaml          # 声を作る（voice_id を YAML に書き込む）
  python tools/gemini_tts.py speak  bakeoff/voices/metallica.yaml          # tests の台詞をすべて読ませる
  python tools/gemini_tts.py lines  episodes/A/scenes/A01/shots           # ショットファイルの台詞を読ませる（--only A01-006）
  python tools/gemini_tts.py speak  bakeoff/voices/metallica.yaml --only gag
  python tools/gemini_tts.py replicate bakeoff/voices/metallica.yaml       # 権利のあるサンプル音声から声を作る
  python tools/gemini_tts.py list                                           # 作成済みの声の一覧
  どのコマンドも --dry-run で、送信内容だけを表示する。

声の複製（replicate）は、声の持ち主本人が同意し、権利のある音声に限って使う（D-037）。
実在の声優の演技（原作ゲームのボイスなど）は使わない。YAML の gemini.replicate に、
サンプル（10〜30秒）、本人が同意文を読み上げた音声、権利の根拠を書いておく。
"""
import argparse
import base64
import json
import os
import pathlib
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-3.8-flash-tts"
OUT = ROOT / "bakeoff" / "results" / "voice"


def ssl_context():
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if not ca and pathlib.Path("/root/.ccr/ca-bundle.crt").exists():
        ca = "/root/.ccr/ca-bundle.crt"
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def call(method, path, body=None, dry_run=False):
    url = f"{BASE}{path}"
    if dry_run:
        print(f"[dry-run] {method} {url}")
        if body is not None:
            print(json.dumps(body, ensure_ascii=False, indent=2))
        return None
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        headers["x-goog-api-key"] = key
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ssl_context(), timeout=300) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"Gemini API エラー {e.code}: {detail[:1000]}")


def find_audio(obj):
    """応答の中から base64 の音声データを探す（steps[].content[].data など）。"""
    if isinstance(obj, dict):
        mime = str(obj.get("mime_type") or obj.get("mimeType") or "")
        if "data" in obj and isinstance(obj["data"], str) and (mime.startswith("audio") or obj.get("type") == "audio"):
            return obj["data"]
        for v in obj.values():
            hit = find_audio(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find_audio(v)
            if hit:
                return hit
    return None


def wav_seconds(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return round(float(out), 2)
    except Exception:
        return None


def record(target, model, prompt, output, gen_sec, note):
    cmd = [sys.executable, str(ROOT / "tools" / "record.py"), "add",
           "--target", target, "--role", "voice", "--model", model, "--via", "gemini-api",
           "--prompt", prompt, "--output", str(output), "--gen-sec", f"{gen_sec:.1f}"]
    if note:
        cmd += ["--note", note]
    subprocess.run(cmd, check=True)


def show(path):
    path = pathlib.Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load(spec_path):
    p = pathlib.Path(spec_path)
    return p, yaml.safe_load(p.read_text(encoding="utf-8"))


def write_voice_id(spec_path, voice_id):
    text = spec_path.read_text(encoding="utf-8")
    new, n = re.subn(r"(voice_id:\s*)(null|voice_\S+)", rf"\g<1>{voice_id}", text, count=1)
    if n != 1:
        sys.exit(f"{spec_path} に voice_id の行が見つかりません。")
    spec_path.write_text(new, encoding="utf-8")


def cmd_design(a):
    spec_path, spec = load(a.spec)
    g = spec["gemini"]
    if g.get("voice_id") and not a.force:
        sys.exit(f"すでに voice_id={g['voice_id']} があります。作り直すときは --force を付けてください。")
    body = {
        "store": True,
        "voice": {
            "model": MODEL,
            "type": "prompted",
            "display_name": g["display_name"],
            "gender": g["gender"],
            "language_code": g["language_code"],
            "prompted": {"input": g["description"].strip()},
        },
    }
    t0 = time.time()
    res = call("POST", "/voices", body, a.dry_run)
    if res is None:
        return
    voice = res.get("voice", res)
    voice_id = voice.get("id") or res.get("id")
    if not voice_id:
        sys.exit("応答に voice の id がありません: " + json.dumps(res)[:800])
    out_dir = OUT / spec["character"]
    out_dir.mkdir(parents=True, exist_ok=True)
    sample = find_audio(voice.get("sample_audio") or res)
    out = out_dir / "design_sample.wav"
    if sample:
        out.write_bytes(base64.b64decode(sample))
    write_voice_id(spec_path, voice_id)
    record(f"voice:{spec['character']}", f"{MODEL}/voice-design", g["description"].strip(),
           out if sample else "", time.time() - t0, f"voice_id={voice_id}")
    print(f"声を作りました: {voice_id}")
    if sample:
        print(f"試聴: {show(out)}（{wav_seconds(out)}秒）")


CONSENT_JA = "私はこの音声の所有者であり、Googleがこの音声を使用して音声合成モデルを作成することを承認します。"


def to_wav24k(src, dst):
    """24kHz・モノラル・16bit の WAV に変換する（推奨形式）。"""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                    "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", str(dst)], check=True)
    return dst


def cmd_replicate(a):
    spec_path, spec = load(a.spec)
    g = spec["gemini"]
    r = g.get("replicate") or {}
    missing = [k for k in ("source", "consent", "rights") if not r.get(k)]
    if missing:
        sys.exit(f"gemini.replicate に {', '.join(missing)} がありません。"
                 "サンプル音声、同意文の音声、権利の根拠（誰の声で、どう同意を得たか）を書いてください。")
    if g.get("voice_id") and not a.force:
        sys.exit(f"すでに voice_id={g['voice_id']} があります。作り直すときは --force を付けてください。")
    src, consent = ROOT / r["source"], ROOT / r["consent"]
    for p in (src, consent):
        if not p.exists():
            sys.exit(f"ファイルがありません: {p}")
    work = ROOT / "tmp" / "replicate"
    work.mkdir(parents=True, exist_ok=True)
    src_wav = to_wav24k(src, work / "source.wav")
    con_wav = to_wav24k(consent, work / "consent.wav")
    dur = wav_seconds(src_wav)
    if dur is not None and not (10 <= dur <= 30):
        sys.exit(f"サンプル音声は10〜30秒にしてください（いまは{dur}秒）。")
    print(f"同意文（本人がこの通りに読み上げていること）: {CONSENT_JA}")
    body = {
        "store": True,
        "voice": {
            "model": MODEL,
            "type": "replicated",
            "display_name": g["display_name"],
            "replicated": {
                "source_audio": {"mime_type": "audio/wav",
                                 "data": "<base64省略>" if a.dry_run else base64.b64encode(src_wav.read_bytes()).decode()},
                "consent_audio": {"mime_type": "audio/wav",
                                  "data": "<base64省略>" if a.dry_run else base64.b64encode(con_wav.read_bytes()).decode()},
            },
        },
    }
    t0 = time.time()
    res = call("POST", "/voices", body, a.dry_run)
    if res is None:
        return
    v = res.get("replicated_voice") or res.get("voice") or res
    voice_id = v.get("id") or v.get("key")
    if not voice_id:
        sys.exit("応答に voice の id がありません: " + json.dumps(res)[:800])
    write_voice_id(spec_path, voice_id)
    record(f"voice:{spec['character']}", f"{MODEL}/voice-replicate", f"source={r['source']} rights={r['rights']}",
           r["source"], time.time() - t0, f"voice_id={voice_id}")
    print(f"声を作りました: {voice_id}（サンプル{dur}秒）")


def cmd_speak(a):
    spec_path, spec = load(a.spec)
    voice_id = spec["gemini"].get("voice_id")
    if not voice_id and not a.dry_run:
        sys.exit("voice_id がありません。先に design を実行してください。")
    out_dir = OUT / spec["character"]
    out_dir.mkdir(parents=True, exist_ok=True)
    tests = [t for t in spec["tests"] if not a.only or t["id"] in a.only]
    for t in tests:
        body = {
            "model": MODEL,
            "input": [{
                "type": "user_input",
                "content": [{
                    "type": "text",
                    "text": t["text"],
                    "annotations": [{"type": "speech_metadata", "style": t["style"]}],
                }],
            }],
            "response_format": {"type": "audio", "mime_type": "audio/wav", "sample_rate": 24000},
            "generation_config": {"speech_config": [{"voice": voice_id or "voice_(未作成)"}]},
        }
        t0 = time.time()
        res = call("POST", "/interactions", body, a.dry_run)
        if res is None:
            continue
        audio = find_audio(res)
        if not audio:
            sys.exit("応答に音声がありません: " + json.dumps(res)[:800])
        out = out_dir / f"{t['id']}.wav"
        if out.exists() and not a.overwrite:
            n = 2
            while (out_dir / f"{t['id']}_{n:02d}.wav").exists():
                n += 1
            out = out_dir / f"{t['id']}_{n:02d}.wav"
        out.write_bytes(base64.b64decode(audio))
        dur = wav_seconds(out)
        record(f"voice:{spec['character']}", MODEL, f"{t['text']} || style: {t['style']}",
               out, time.time() - t0, f"test={t['id']} 音声{dur}秒")
        print(f"{t['id']}: {show(out)}（{dur}秒）")


def synth(voice_id, text, style, dry_run):
    body = {
        "model": MODEL,
        "input": [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": text,
                "annotations": [{"type": "speech_metadata", "style": style}],
            }],
        }],
        "response_format": {"type": "audio", "mime_type": "audio/wav", "sample_rate": 24000},
        "generation_config": {"speech_config": [{"voice": voice_id or "voice_(未作成)"}]},
    }
    res = call("POST", "/interactions", body, dry_run)
    if res is None:
        return None
    audio = find_audio(res)
    if not audio:
        sys.exit("応答に音声がありません: " + json.dumps(res)[:800])
    return base64.b64decode(audio)


def next_free(out):
    if not out.exists():
        return out
    n = 2
    while out.with_name(f"{out.stem}_{n:02d}{out.suffix}").exists():
        n += 1
    return out.with_name(f"{out.stem}_{n:02d}{out.suffix}")


def cmd_lines(a):
    """ショットファイルの dialogue を読ませる。出力は audio/<シーン>/<カットID>_<番号>_<人物>.wav"""
    voices = {}
    for f in sorted(pathlib.Path(a.shots).glob("*.yaml")):
        shot = yaml.safe_load(f.read_text(encoding="utf-8"))
        if a.only and shot["id"] not in a.only:
            continue
        for i, d in enumerate(shot.get("dialogue") or [], 1):
            who = d["who"]
            if who not in voices:
                _, spec = load(str(ROOT / "bakeoff" / "voices" / f"{who}.yaml"))
                voices[who] = spec["gemini"].get("voice_id")
                if not voices[who] and not a.dry_run:
                    sys.exit(f"{who} の voice_id がありません。")
            out_dir = ROOT / "audio" / shot["scene"]
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{shot['id']}_{i}_{who}.wav"
            if not a.overwrite:
                out = next_free(out)
            if not a.dry_run:
                time.sleep(7)   # 1分10本の制限（Tier 1）に合わせる
            t0 = time.time()
            wav = synth(voices[who], d["line"], d["delivery"], a.dry_run)
            if wav is None:
                print(f"{shot['id']}_{i} {who}: {d['line']} || {d['delivery']}")
                continue
            out.write_bytes(wav)
            dur = wav_seconds(out)
            record(shot["id"], MODEL, f"{d['line']} || style: {d['delivery']}",
                   out, time.time() - t0, f"voice:{who} 台詞{i} 音声{dur}秒")
            print(f"{shot['id']}_{i} {who}: {show(out)}（{dur}秒）")


def cmd_list(a):
    res = call("GET", "/voices", None, a.dry_run)
    if res is None:
        return
    for v in res.get("voices", []):
        print(f"{v.get('id')}  {v.get('display_name')}  {v.get('type')}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("design"); s.add_argument("spec"); s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_design)
    s = sub.add_parser("speak"); s.add_argument("spec"); s.add_argument("--only", nargs="*")
    s.add_argument("--overwrite", action="store_true"); s.set_defaults(func=cmd_speak)
    s = sub.add_parser("replicate"); s.add_argument("spec"); s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_replicate)
    s = sub.add_parser("lines"); s.add_argument("shots", help="ショットファイルのフォルダ")
    s.add_argument("--only", nargs="*"); s.add_argument("--overwrite", action="store_true")
    s.set_defaults(func=cmd_lines)
    s = sub.add_parser("list"); s.set_defaults(func=cmd_list)
    for s in sub.choices.values():
        s.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
