# Daily Reader — AI引き継ぎコンテキスト

この文書は、コンテキストを持たない生成AIが本リポジトリの作業を再開するための要点です。
最初に本ファイルと `README.md` を読み、実装を推測せず、現在のコードと生成データを確認してください。

## 目的と運用形態

- 個人向けCodexタスク／生活／情報ダッシュボード。起動時はAgentタブを表示し、iPhoneから自律タスクを投入する。
- 同じ基盤で生活支援機能を拡張する。最初の追加機能はGmailの未対応メール管理。
- Mac miniの `127.0.0.1:8787` だけでホストし、Tailscale Serve経由でtailnet内に限定公開する。
- 公開URL: `https://sk-mins-mac-mini.tailc193b2.ts.net/`
- 外部サーバー、DB、有料ホスティングは使わない。
- 起動時とローカル時刻の8時、10時、12時、17時、20時、22時に更新する。
- Codex CLIを1更新につき最大1回呼び、低コストモデルで全ハイライトをまとめて生成する。

## 主要ファイル

- `config/feeds.toml`: RSS、Atom、公式ページ、Google News検索などの収集元。
- `config/keywords.toml`: 記事スコアの加点・減点キーワード。
- `config/highlight-schema.json`: Codexの構造化出力スキーマ。
- `src/daily_reader/core.py`: 収集、各種パーサー、正規化、重複排除、画像抽出、新店日付検証。
- `src/daily_reader/highlights.py`: 候補選定、Codexプロンプト、出力検証、OG画像補完。
- `src/daily_reader/local_server.py`: ローカルHTTPサーバー、更新スケジューラー、閲覧・不要フィードバック・メールAPI。
- `src/daily_reader/email_assistant.py`: Gmail読み取り・既読反映OAuth、重要度判定、SQLite状態管理。
- `src/daily_reader/daily_planner.py`: タスク、繰り返しルーティン、健康チェックインのSQLite状態管理。
- `src/daily_reader/agent_jobs.py`: Agentタスクキュー、イベント、状態のSQLite永続化。
- `src/daily_reader/agent_worker.py`: 専用worktreeでCodexを反復実行し、検証済み変更をmainへ統合する常駐ワーカー。
- Agentワーカーはpush後にローカルのデフォルトブランチを同期し、対象リポジトリの`AGENTS.md`に従ったデプロイと実環境確認が成功してからタスクを完了する。
- Agentワーカーは既定で最大10件を並列実行し、`--max-workers`で並列数を変更できる。
- デプロイ確認は実装セッションとは別の自動承認付きCodexセッションで実行し、`launchctl`による再起動と実環境確認に必要な権限を自動審査へ回す。
- Codexの構造化出力スキーマはワーカー起動時にDaily Reader基準の絶対パスへ解決し、tonoiなど別リポジトリのworktreeでも同じスキーマを使用する。
- tonoiは`config/agent-repositories.toml`の`deploy = false`により、検証済み変更を`main`へ統合してpushした時点で完了とし、実環境デプロイは行わない。
- `config/agent-repositories.toml`: Agentが操作できるGitリポジトリの許可リスト。Daily Reader、soan、宿直（tonoi）を登録し、ホーム相対パスにも対応する。
- `site/app.js`, `site/style.css`: iPhone向け1ページUI。ニュース／メールを上部タブで切り替える。
- `site/data/articles.json`, `site/data/highlights.json`: 公開中の生成済みスナップショット。Git管理対象。
- `data/read-events.jsonl`: 実際に開いた記事のローカル履歴。Git管理対象外。
- `data/feedback-events.jsonl`: 「表示したくない」と指定した記事のローカル履歴。Git管理対象外。
- `data/selection-history.jsonl`: 分野別ハイライトの連続掲載履歴。Git管理対象外。
- `data/update-stats.jsonl`: 更新ごとの新規記事数、ハイライト採用数、継続数。Git管理対象外。
- `data/assistant.sqlite3`: メール判定と対応状態。Mac mini内だけに保持しGit管理対象外。
- `data/planner.sqlite3`: タスク、日別ルーティン完了、健康日次集計。Mac mini内だけに保持しGit管理対象外。
- `data/agent.sqlite3`: Codexタスク、実行状態、イベント。Mac mini内だけに保持しGit管理対象外。
- HTTPサーバーを先に起動可能な状態にし、ニュース更新とGmail同期は別々のバックグラウンドスレッドで実行する。
- Gmailは起動直後と15分ごとに同期し、重要な未読メールだけを表示する。アプリの「既読」はGmailのスレッドから`UNREAD`ラベルを外し、Gmailで既読にしたメールも次回同期後に一覧から外れる。ニュース・Gmailの同期中もHTTP応答をブロックしない。
- 初期画面は「Agent」。通常タスク、日別ルーティン、健康チェックインは「今日」に表示し、HealthKit集計は専用トークン付きAPIで受け取る。
- 画面下部には、稼働中のパッケージ版とGitコミットを組み合わせたデプロイバージョン、およびサーバー起動日時を表示する。
- Agentタスクはカードを既定で折り畳み、状態アイコン、現在フェーズ、更新時刻を一覧表示する。カードを開くと待機中・実行中・判断待ちの直近3件のイベントを表示し、5秒ごとに自動更新する。カードから全履歴も展開でき、待機中・実行中・判断待ち、および作業環境を保持した失敗状態へ追加指示を送れる。実行中の指示は次のCodexターンで同じスレッドへ渡す。
- Agentタスクは一覧から非表示にでき、その後に進捗や履歴が更新されると自動で再表示する。
- Agentタスクは即時実行と要件深掘りの2モードを持つ。要件深掘りでは、最初のターンは実装せずリポジトリ調査と質問に限定し、回答後に同じスレッドで要件を固めてから実装する。

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

- 公開24時間以内を最も強く、3日以内、7日以内の順に候補順位を加点し、14日超と前回掲載記事は減点する。
- Codexへ渡す候補は、全10分野の上位候補を先に最大8件ずつ確保してから全体新着・公式リリース・総合上位を加える。特定分野や公式記事が候補枠を先に使い切らないようにする。
- 選定では新しい事実、具体的な検証結果、失敗からの学び、実務への影響、意外性を重視し、14日超の記事は同じ分野に7日以内の適格候補がある場合は選ばない。
- 分野別の選定履歴を `data/selection-history.jsonl` に保存し、2回連続掲載済みの記事は代替候補がある限り次回掲載しない。
- 適格な代替候補がない分野では継続掲載を許可し、空欄を低品質な新着で埋めない。
- UIでは初掲載を「新着」、前回からの掲載を「継続」と表示する。
- 更新時は前回スナップショットと比較し、新規記事数、新規記事からのハイライト採用数、ハイライトの新選・継続数を画面と`data/update-stats.jsonl`へ記録する。

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

## デプロイ完了条件

- 実行時の挙動に影響する変更は、検証済みコードを `main` へ統合して `origin/main` へ push した後、影響を受ける LaunchAgent を再起動し、実環境を確認して初めて完了とする。
- Web サーバー、UI、ニュース、メール、プランナー、設定、依存関係の変更では `org.nix-community.home.daily-reader` を再起動する。Agent キュー、Agent ワーカー、リポジトリ操作の変更では `org.nix-community.home.daily-reader-agent-worker` も再起動する。
- 再起動前に `lsof -nP -iTCP:8787 -sTCP:LISTEN` で PID を記録する。再起動は次の形式で行う。

```bash
launchctl kickstart -k gui/$(id -u)/org.nix-community.home.daily-reader
launchctl kickstart -k gui/$(id -u)/org.nix-community.home.daily-reader-agent-worker
```

- 再起動後は、PID の更新、LaunchAgent の稼働、`http://127.0.0.1:8787/` の成功応答、`https://sk-mins-mac-mini.tailc193b2.ts.net/` の成功応答、および変更機能の代表的な動作を確認する。
- デプロイまたは実環境確認に失敗した状態を完了として扱わない。実行できない場合は未デプロイと阻害要因を明示する。
- 文書、コメント、テストだけの変更で実行時成果物が変わらない場合は再起動不要だが、その判断を完了報告へ明記する。

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
