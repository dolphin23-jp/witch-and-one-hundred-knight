# 組み立ての実装の比較（HyperFrames と Remotion）

制作パイプラインのマイルストーン3「組み立ての実装（HyperFrames か Remotion）は A01 で両方を試して決める」の記録。2026-09-27。

## 条件

- 入力は共通: `python tools/scene_export.py episodes/A/scenes/A01 --overlays` が作る `animatic/build/A01/`（scene.json、1920x1080 に縮めた静止画56枚、原作の台詞29本、漫符1つ）。
- 中身は Animatic v08（tools/animatic.py、ffmpeg で直接書き出し）と同じにした: 静止画、カメラの動き（寄る・引く・パン・ティルト・揺れ）、左上のカット情報、右上のタイムコード、下の字幕、原作の台詞。
- 漫符の試験として、A01-023 に怒りマーク（overlays/anger_mark_01.svg）を別の層で重ねた（ぽんと出て小さく脈打つ）。
- 1280x720、24fps、201.3秒（4831フレーム）。クラウドのセッション（2コア、メモリ7GB）で、Chromium は入っているもの（/opt/pw-browsers）を使う。

## 結果

| 項目 | Remotion 4.0.529 | HyperFrames 0.8.79 |
| --- | --- | --- |
| 書き方 | React（TypeScript）。フレームごとに式で計算する | HTML と GSAP。Python で index.html を生成する |
| 導入 | npm install 31秒、271MB | npm install 12秒、143MB |
| 書き出しの時間 | 6分13秒 | 7分37秒（画の取り込み4分12秒＋書き出し2分56秒） |
| 画 | v08 と見分けがつかない | v08 と見分けがつかない |
| 声の時刻 | 約40ms（1フレーム）遅れる | ずれなし |
| 声の大きさ | v08 と同じ | v08 より3dB大きい（割れはない。data-volume で直せる） |
| 漫符の層 | 問題なし（spring で出す） | 問題なし（GSAP で出す） |
| カメラの動き | 式をそのまま書ける | 始めと終わりを GSAP でつなぐ。揺れは1フレームずつ置く |
| 動かせるもの | CSS で書けるものは何でも | GSAP で動かせる値に限られる（透明度、位置、拡大、回転、幅と高さ） |
| 決まりへの合い方 | 警告なし | lint の警告85（カットごとに別ファイルにする作法。書き出しには影響しない） |
| ライセンス | 個人は無料（会社で使う場合は有料の条件あり） | Apache-2.0 |
| スマホでの確認 | 書き出した動画を見るだけ | `hyperframes publish` で HeyGen に上げるとブラウザで見られる（素材を外に上げることになる） |
| 成熟度 | 1.0 以降、長く使われている | 0.x（変化が速い。版を固定して使う） |

1カットの差し替えは、どちらも組み直しは全体の書き出し（6〜8分）になる。静止画の縮小は差し替えたカットだけやり直す（scene_export.py）。

## 使い方

```sh
python tools/scene_export.py episodes/A/scenes/A01 --overlays
sh animatic/remotion/render.sh A01 A01_remotion            # animatic/out/A01_remotion.mp4
sh animatic/hyperframes/render.sh A01 A01_hyperframes      # animatic/out/A01_hyperframes.mp4
```

初回は `animatic/remotion` と `animatic/hyperframes` で `npm install` が要る。
