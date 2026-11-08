#!/usr/bin/env python3
"""下絵の成果物の版の記録と照合（2026-11-08、previs の根本改善のフェーズ1）。Blender を使わない。

下絵の動画（<カット>.mp4）・確認画像（_check.jpg）・動きの確認（_motion.jpg）・構図の測定（gen/framing.yaml）が、
どの入力（設計）とどの道具で作られたかを記録し、今の入力・道具と比べる。2026-11-07 までは記録がなく、
11-01 の旧人形の motion シートと 11-07 の動画が並んでいても気づけなかった。

カットの入力のハッシュ（input_hash）に入れるもの:
  blocking.yaml のそのカット（アンカーを開いたもの）と、カット以外の頭の項目（layout・layout_extra・lighting・places・start など）、
  前のカットまでの動きで決まるカットの頭の立ち位置と向き（tools/staging.py の状態）、生き物の after でつながる前のカットの通り道、
  配置ファイル（layout と layout_extra）の中身、ショットファイル、そのカットの時間（前後 1 秒）にかかる voice.yaml の台詞と、その wav と、
  台詞の文（audio/<場面>/original/script.yaml の行。表情を決める）。
道具のハッシュ（tool_hash）: 下絵を描く道具（RENDER_TOOLS）、測定ではそれに tools/previs_audit.py を足したもの（AUDIT_TOOLS）。

記録は場面ごとに1つ: previs/<場面>/manifest.yaml（cuts → カット → video・check・motion → 記録）。
測定は gen/framing.yaml のカットごとに input_hash・tool_hash を書く（tools/previs_audit.py）。
照合: tools/previs_status.py（一覧）、tools/framing.py（古い測定を報告に出す）、tools/framing_fix.py（古い測定では直さない）。
"""
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

RENDER_TOOLS = ("previs.py", "previs_rig.py", "previs_face.py", "previs_fx.py", "previs_look.py", "previs_council.py", "previs_ch7.py",
                "motion.py", "staging.py")
AUDIT_TOOLS = RENDER_TOOLS + ("previs_audit.py",)
SHORT = 10   # 見出しに描く、ハッシュの頭の字数


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def file_sha(p):
    p = pathlib.Path(p)
    return _sha(p.read_bytes()) if p.exists() else None


def tool_hash(files=RENDER_TOOLS):
    h = hashlib.sha256()
    for f in sorted(files):
        p = ROOT / "tools" / f
        h.update(f.encode())
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()


def git_commit():
    """(コミット, 作業ツリーに変更があるか)。"""
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    d = subprocess.run(["git", "status", "--porcelain", "--", "tools", "episodes", "world", "audio"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() or None, bool(d.stdout.strip())


def _canon(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


class SceneInputs:
    """1場面の、カットごとの入力のハッシュを求める（staging.Stage を1回だけ作る）。"""

    def __init__(self, scene_dir, stage=None):
        import staging   # noqa: PLC0415
        import motion    # noqa: PLC0415
        self.motion = motion
        sd = pathlib.Path(scene_dir)
        self.dir = sd if sd.is_absolute() else ROOT / sd
        self.raw = yaml.safe_load((self.dir / "blocking.yaml").read_text(encoding="utf-8")) or {}
        self.st = stage or staging.Stage(self.dir)
        self.top = {k: v for k, v in self.raw.items() if k != "cuts"}
        files = [self.raw.get("layout")] + [ex.get("file") for ex in self.raw.get("layout_extra") or []]
        self.layout_sha = {f: file_sha(ROOT / f) for f in files if f}
        vy = self.dir / "voice.yaml"
        self.voice = (yaml.safe_load(vy.read_text(encoding="utf-8")) or {}).get("lines") or [] if vy.exists() else []
        src = ROOT / "audio" / self.dir.name / "original"
        self.src = src
        sp = src / "script.yaml"
        self.script = {}
        if sp.exists():
            for l in (yaml.safe_load(sp.read_text(encoding="utf-8")) or {}).get("lines") or []:
                self.script[l.get("id")] = l
        self._wav = {}

    def _wav_info(self, lid):
        """(wav の名前, sha256, 秒)。wav がなければ None。"""
        if lid not in self._wav:
            wav = next(iter(sorted(self.src.glob(f"{lid}_*.wav"))), None) if self.src.exists() else None
            info = None
            if wav:
                dur = 0.0
                try:
                    import wave   # noqa: PLC0415
                    with wave.open(str(wav)) as w:
                        dur = w.getnframes() / float(w.getframerate())
                except Exception:
                    dur = 0.0
                info = [wav.name, file_sha(wav), round(dur, 3)]
            self._wav[lid] = info
        return self._wav[lid]

    def shot(self, cut):
        p = self.dir / "shots" / f"{cut}.yaml"
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}

    def inputs(self, cut):
        """カットの入力（ハッシュの元）。"""
        c = (self.raw.get("cuts") or {}).get(cut)
        sh = self.shot(cut)
        tc, sec = float(sh.get("tc", 0.0) or 0.0), float(sh.get("sec", 0.0) or 0.0)
        s = self.st.states.get(cut) or {}
        start = s.get("start")
        creatures = {}
        for n in ((c or {}).get("creatures") or {}):
            try:
                creatures[n] = self.motion.creature_spec(self.st.bk["cuts"], cut, n)
            except Exception:
                creatures[n] = None
        lines = []
        for l in self.voice:   # 台詞の時間（wav の長さ）が、カットの前後 1 秒にかかるもの
            at = float(l.get("at", 0.0))
            lid = l.get("id")
            info = self._wav_info(lid)
            end = at + (info[2] if info else 2.0)
            if end >= tc - 1.0 and at <= tc + sec + 1.0:
                lines.append({"line": l, "wav": info, "script": self.script.get(lid)})
        return {"cut": c, "top": self.top, "start_state": start, "track_start": s.get("track_start"),
                "creatures": creatures, "layout": self.layout_sha, "shot": sh, "voice": lines}

    def input_hash(self, cut):
        return _sha(_canon(self.inputs(cut)).encode())


# ---------- 記録 ----------

def manifest_path(scene, out_dir=None):
    """記録の置き場所。下絵と同じフォルダ（既定 previs/<場面>/、--out を使ったときはそのフォルダ）。"""
    return (pathlib.Path(out_dir) if out_dir else ROOT / "previs" / scene) / "manifest.yaml"


def load(scene, out_dir=None):
    p = manifest_path(scene, out_dir)
    if not p.exists():
        return {}
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("cuts") or {}


def record(scene, cut, kind, entry, out_dir=None):
    """previs/<場面>/manifest.yaml（out_dir を渡せばそのフォルダ）の cuts.<カット>.<kind> を書き換える（video・check・motion）。"""
    p = manifest_path(scene, out_dir)
    cuts = load(scene, out_dir)
    cuts.setdefault(cut, {})[kind] = entry
    data = {"note": "tools/previs.py が書く（tools/manifest.py）。照合は tools/previs_status.py",
            "cuts": {k: cuts[k] for k in sorted(cuts)}}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")


def entry(input_hash, tool_h, files, **extra):
    commit, dirty = git_commit()
    e = {"input_hash": input_hash, "tool_hash": tool_h, "commit": commit, "dirty": dirty,
         "made": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    e.update(extra)
    e["files"] = {}
    for f in files:
        f = pathlib.Path(f)
        f = f if f.is_absolute() else ROOT / f
        try:
            name = str(f.relative_to(ROOT))
        except ValueError:
            name = str(f)
        e["files"][name] = file_sha(f)
    return e


def stamp(input_hash, tool_h):
    """確認画像の見出しに描く短い文（スマホで古さがわかるように）。"""
    return f"入力 {input_hash[:SHORT]}・道具 {tool_h[:6]}・{datetime.date.today().isoformat()}"


def compare(rec, cur_input, cur_tool):
    """記録と今の入力・道具を比べる: 最新 / 入力が古い / 道具が古い / 記録なし。"""
    if not rec or not rec.get("input_hash"):
        return "記録なし"
    if rec["input_hash"] != cur_input:
        return "入力が古い"
    if rec.get("tool_hash") and rec["tool_hash"] != cur_tool:
        return "道具が古い"
    return "最新"
