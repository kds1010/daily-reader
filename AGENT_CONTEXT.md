# Daily Reader — AI引き継ぎコンテキスト

この文書は、コンテキストを持たない生成AIが本リポジトリの作業を再開するための要点です。
最初に本ファイルと `README.md` を読み、実装を推測せず、現在のコードと生成データを確認してください。

## 目的と運用形態

- 個人向けニュース／情報ダッシュボード。iPhoneで1ページのハイライトを確認する。
- Mac miniの `127.0.0.1:8787` だけでホストし、Tailscale Serve経由でtailnet内に限定公開する。
- 公開URL: `https://sk-mins-mac-mini.tailc193b2.ts.net/`
- 外部サーバー、DB、有料ホスティングは使わない。
- 起動時とローカル時刻の8時、12時、17時、20時に更新する。
- Codex CLIを1更新につき最大1回呼び、低コストモデルで全ハイライトをまとめて生成する。

## 主要ファイル

- `config/feeds.toml`: RSS、Atom、公式ページ、Google News検索などの収集元。
- `config/keywords.toml`: 記事スコアの加点・減点キーワード。
- `config/highlight-schema.json`: Codexの構造化出力スキーマ。
- `src/daily_reader/core.py`: 収集、各種パーサー、正規化、重複排除、画像抽出、新店日付検証。
- `src/daily_reader/highlights.py`: 候補選定、Codexプロンプト、出力検証、OG画像補完。
- `src/daily_reader/local_server.py`: ローカルHTTPサーバー、更新スケジューラー、閲覧・不要フィードバックAPI。
- `site/app.js`, `site/style.css`: iPhone向け1ページUI。
- `site/data/articles.json`, `site/data/highlights.json`: 公開中の生成済みスナップショット。Git管理対象。
- `data/read-events.jsonl`: 実際に開いた記事のローカル履歴。Git管理対象外。
- `data/feedback-events.jsonl`: 「表示したくない」と指定した記事のローカル履歴。Git管理対象外。
- `data/selection-history.jsonl`: 分野別ハイライトの連続掲載履歴。Git管理対象外。

## 現在のハイライト分野

表示順は次の10分野で固定している。

1. データ・AI
2. データマネジメント・エンジニアリング書籍
3. 生成AI活用・テクニック
4. CLI・ターミナル生産性
5. 業務改善・QOL
6. 子育て
7. 横浜イベント
8. 街の新店
9. 睡眠
10. 筋トレ

その下に、公式リリースまとめ、ガジェットまとめ、厳選技術ブログ、折りたたみ式の全記事一覧がある。

## ユーザーの重要な関心

- 最優先: データマネジメント、ガバナンス、品質、メタデータ、カタログ、リネージ。
- Snowflake、dbt、Apache Iceberg、Databricksの公式更新を早く把握したい。
- データエンジニアリング、ML基盤、MLOps、モデル／学習データガバナンス。
- 自動車会社におけるデータ・ML管理。なければ製造業など類似産業の一次事例。
- データ関連書籍の新刊・近刊。
- 生成AIの実践的な設計・評価・RAG・エージェント・コスト改善・Codex活用。
- eza、fzf、Yazi、cmuxのようなCLI／TUI生産性ツール。Nightlyや用途不明の記事は不要。
- マウス、キーボード、ディスプレイ、デスク環境などの実用的なガジェット。
- 睡眠製品、睡眠研究、根拠と安全性が分かるサプリ情報。筋トレ情報。
- 一般的な社会、政治、芸能、災害、交通ニュースは不要。

## 地域情報の厳格なルール

- 居住基準は桜木町駅周辺。
- 子育ては徒歩圏のみ。桜木町、野毛、花咲町、紅葉坂、宮崎町、北仲、馬車道、高島町を対象とする。
- 子育ては電車・バス移動が必要な候補を出さない。該当なしの日は空でよい。
- 横浜イベントは桜木町、みなとみらい、馬車道、関内、野毛、高島町、新高島、横浜駅、西区、中区を対象とする。
- 戸塚区、金沢区、青葉区など遠方の情報で不足を補完しない。
- 街の新店は子育てと同じ徒歩圏のみ。開店・移転・リニューアル日を本文から確認できる記事だけを採用する。
- 新店日は過去60日以内または今後に限定する。RSS公開日だけが新しい古記事を信用しない。
- 例: 7年前の「すみれ 横浜店」記事がGoogle Newsで再掲されたため、この検証を追加した。

## ハイライト画像

- `Article.image_url` にRSSの `media:content`、`media:thumbnail`、画像enclosure、本文内画像を優先保存する。
- 選定済みハイライトだけ、記事ページの `og:image` を並列取得して補完する。全記事へはアクセスしない。
- OG取得はプライベート／ループバックIPを拒否し、リダイレクト先も検査する。
- 各分野の先頭画像は大きな横長、2件目以降は小さなサムネイル。画像なしは文字カードのまま。
- iPhoneでは `loading="lazy"` と `decoding="async"` を使用し、失敗画像はUIから除去する。

## Codexハイライト生成

- モデルは `gpt-5.6-luna`、推論はlow。
- `--ephemeral`、`--sandbox read-only`、JSON Schemaによる構造化出力を使う。
- 記事候補のハッシュが変わらない限り再生成しない。
- `PROMPT_VERSION` を変えると再生成され、Codex利用量を消費する。選定方針を変えた場合だけ更新する。
- 公式英語リリースは製品ごとに日本語で統合し、元記事のタイトル・情報元・公開日を表示する。
- すべての関連記事リンクは「関連記事」だけではなく、記事タイトル・情報元・日付を表示する。

## 閲覧ログ

- 全リンクのクリックを `POST /api/read` へ送り、`data/read-events.jsonl` に追記する。
- `GET /api/analytics` でカテゴリ、情報元、表示面別の集計を返す。
- ログはMac mini内だけに保持し、Gitへ含めない。

## 不要記事フィードバック

- 各表示面の「表示したくない」から `POST /api/feedback` へ送り、`data/feedback-events.jsonl` に追記する。
- `GET /api/feedback` は指定済み記事IDを返し、画面上で同一記事を非表示にする。
- 次回ハイライト生成では指定済み記事を候補から除外し、直近100件のタイトル、情報元、カテゴリをCodexへ不要例として渡す。
- 不要例は繰り返し現れる傾向の減点にだけ使い、少数例によるカテゴリ全体の除外や既存の優先・地域ルールの上書きはしない。
- フィードバック内容も入力ハッシュへ含めるため、新しい指定後の定期更新では記事候補が同じでも再生成される。

## ハイライトの鮮度

- 公開24時間以内の記事は候補順位を加点し、前回掲載記事は減点する。
- 分野別の選定履歴を `data/selection-history.jsonl` に保存し、2回連続掲載済みの記事は代替候補がある限り次回掲載しない。
- 適格な代替候補がない分野では継続掲載を許可し、空欄を低品質な新着で埋めない。
- UIでは初掲載を「新着」、前回からの掲載を「継続」と表示する。

## 開発・検証

依存関係はuvで固定されている。完了前に最低限、以下を実行する。

```bash
uv run --frozen ruff check .
uv run --frozen pytest
node --check site/app.js
node --check site/sw.js
jq empty config/highlight-schema.json
git diff --check
```

サーバー起動:

```bash
uv run --frozen daily-reader-local
```

サーバー再起動前は、必ず次で `127.0.0.1:8787` の正確なPIDを確認し、そのPIDだけを停止する。

```bash
lsof -nP -iTCP:8787 -sTCP:LISTEN
```

起動時には全フィード取得と、候補が変わった場合のCodex生成が走る。不要な再起動は避ける。

## Gitと環境上の注意

- GitHubリポジトリは `kds1010/daily-reader`、ブランチは `main`。
- 画像対応までの基準コミットは `57ab455 feat: add images to highlight cards`。現在の先端は `git log -1` で確認する。
- 端末設定は `~/.config/nix/` のNix/Home Manager管理。シェル、PATH、環境変数、Codex設定を直接変更しない。
- Mac mini固有のシステム適用コマンドは、ユーザー自身に実行してもらう。
- Tailscale Funnelは使わない。Serveのみを使う。
- 既存のユーザー変更を破棄しない。`git reset --hard` 等を使わない。

## 再開時の最初の確認

1. `git status --short` で未コミット変更を確認する。
2. `lsof -nP -iTCP:8787 -sTCP:LISTEN` でサーバー稼働を確認する。
3. `site/data/articles.json` の `errors` と記事数を確認する。
4. `site/data/highlights.json` の10分野、記事リンク、画像数を確認する。
5. 公開URLはTailscale接続端末から確認する。

この文書と実装が食い違う場合は、実装と実データを正とし、この文書を更新すること。
