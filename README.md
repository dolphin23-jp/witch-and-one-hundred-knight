# witch-and-one-hundred-knight

『魔女と百騎兵』1作目の個人鑑賞用アニメ化の制作リポジトリ。方針は Living Spec（Claude Doc）、作業の決まりは [CLAUDE.md](CLAUDE.md) を参照。

## いまの段階

ベイクオフ（画像・動画・声の候補を同じ条件で比べる）。詳しくは [bakeoff/README.md](bakeoff/README.md)。

## 初回の準備（ユーザーが行うこと）

1. **Higgsfield** の有料プランに加入し、claude.ai の 設定 → コネクタ → カスタムコネクタを追加 で `https://mcp.higgsfield.ai/mcp` を登録してログインする。
2. **Gemini API** のキーを Google AI Studio で発行し、claude.ai/code の環境設定 → API認証情報 に登録する。
   - 許可するサイト: `generativelanguage.googleapis.com`
   - ヘッダー名: `x-goog-api-key`、プレフィックス: なし、値: APIキー
3. **クラウド環境のセットアップスクリプト** に [setup/cloud-setup.sh](setup/cloud-setup.sh) の中身を貼る。
4. **参照画像** を `refs/` フォルダに置く。ファイル名は [refs/index.yaml](refs/index.yaml) の名前にする（例: `metallica_official.png`）。スマホからは、ブラウザで GitHub のリポジトリを開き、`refs` フォルダで「Add file → Upload files」を選ぶ。またはクラウドセッションのチャットに画像を添付して「refs に置いて」と頼む。
