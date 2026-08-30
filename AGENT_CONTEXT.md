# Daymeld（内部プロジェクト名: Daily Reader）— AI引き継ぎコンテキスト

この文書は、コンテキストを持たない生成AIが本リポジトリの作業を再開するための要点です。
最初に本ファイルと `README.md` を読み、実装を推測せず、現在のコードと生成データを確認してください。
表示ブランドはDaymeldです。`daily-reader`／`daily_reader`などの技術識別子は更新互換性のため維持します。

## 目的と運用形態

- 個人向けCodexタスク／生活／情報ダッシュボード。起動時はAgentタブを表示し、iPhoneから自律タスクを投入する。
- 同じ基盤で生活支援機能を拡張する。最初の追加機能はGmailの未対応メール管理。
- Mac miniの `127.0.0.1:8787` だけでホストし、Tailscale Serve経由でtailnet内に限定公開する。
- 公開URL: `https://sk-mins-mac-mini.tailc193b2.ts.net/`
- 外部サーバー、DB、有料ホスティングは使わない。SideStore更新成果物だけは、Mac mini上の専用loopbackサーバーをTailscale Funnelの`8443`番へ中継する。
- 起動時とローカル時刻の8時、10時、12時、17時、20時、22時に更新する。
- Codex CLIを1更新につき最大1回呼び、低コストモデルで全ハイライトをまとめて生成する。

## 主要ファイル

- `config/feeds.toml`: RSS、Atom、公式ページ、Google News検索などの収集元。公式・一次情報は`priority`で明示する。
- `config/keywords.toml`: 記事スコアの加点・減点キーワード。
- `config/highlight-schema.json`: Codexの構造化出力スキーマ。
- `src/daily_reader/core.py`: 収集、各種パーサー、正規化、重複排除、画像抽出、新店日付検証。
- `src/daily_reader/highlights.py`: 候補選定、Codexプロンプト、出力検証、OG画像補完。
- `src/daily_reader/local_server.py`: ローカルHTTPサーバー、更新スケジューラー、閲覧・不要フィードバック・メールAPI。
- `src/daily_reader/email_assistant.py`: Gmail読み取り・既読反映OAuth、重要度判定、SQLite状態管理。
- `src/daily_reader/daily_planner.py`: タスク、繰り返しルーティン、健康チェックインのSQLite状態管理。
- `src/daily_reader/agent_jobs.py`: Agentタスクキュー、イベント、状態のSQLite永続化。
- `src/daily_reader/agent_worker.py`: 専用worktreeでCodexを反復実行し、検証済み変更をmainへ統合する常駐ワーカー。
- `src/daily_reader/tanomi_client.py`: tanomi（既定Tailscale Serve URL `https://xh23040023-l.tailc193b2.ts.net`）の許可パスだけを8787から中継するBFFクライアント。
- Agentワーカーはpush後にローカルのデフォルトブランチを同期し、対象リポジトリの`AGENTS.md`に従ったデプロイと実環境確認が成功してからタスクを完了する。
- iPhoneクライアント変更のデプロイ境界は、検証済みSideStore IPA・ソースを生成し、LAN配信とFunnel配信をMac側から検証するところまでとする。SideStoreでの再署名、iPhoneへのインストール、権限付与、外部回線・画面・操作確認は独立した実機工程であり、未実施でもAgentタスクを失敗させない。
- ローカルのデフォルトブランチがリモートと分岐してfast-forwardできない場合は、リモートへrebaseし、競合をCodexで解消・再検証してからローカルコミットもpushする。
- ローカルのデフォルトブランチに未コミット変更がある場合は、その変更を保持して同期をスキップし、すでに成功したタスクのpushや後続処理を失敗扱いにしない。
- Agentワーカーは既定で最大10件を並列実行し、`--max-workers`で並列数を変更できる。
- 通常のAgentタスクは既定モデルで読み取り専用の実装計画を作成し、依頼時に選択した実装モデル／Effort（既定は`gpt-5.6-luna`／low）で同じスレッドを再開して実装・検証・コミットする。要件深掘りと完了後の質問は既定モデルを維持する。
- AgentキューのSQLite接続は各操作後に明示的にcloseし、10並列のポーリングでファイル記述子を蓄積しない。
- Agentワーカー起動時は、前プロセスの再起動で`running`のまま残ったタスクを自動で再キュー化し、保持した作業環境から再試行する。
- デプロイ確認は実装セッションとは別の自動承認付きCodexセッションで実行し、`launchctl`による再起動と実環境確認に必要な権限を自動審査へ回す。
- 実装・継続・デプロイ中の必須コマンドが`Operation not permitted`、CoreSimulatorService接続拒否、Simulator runtimeなしで失敗した場合は、ホスト環境不足と決めつけず承認付きで同じコマンドを再実行する。同じ隔離環境の失敗を複数ターン繰り返さない。
- デプロイ確認でAgentワーカー自身を再起動する場合は、現在のPID、起動時刻、デプロイ済みコミット、サービスログを先に確認する。復旧後のセッションで対象コミットへの再起動済みと確認できた場合は再度kickstartせず、自己再起動の反復と同一worktreeへのCodex多重実行を防ぐ。
- Codexの構造化出力スキーマはワーカー起動時にDaily Reader基準の絶対パスへ解決し、tonoiなど別リポジトリのworktreeでも同じスキーマを使用する。
- tonoiとconfigは`config/agent-repositories.toml`の`deploy = false`により、検証済み変更を`main`へ統合してpushした時点で完了とし、実環境デプロイは行わない。
- `config/agent-repositories.toml`: Agentが操作できるGitリポジトリの許可リスト。Daily Reader、soan、宿直（tonoi）、configを登録し、ホーム相対パスにも対応する。
- `site/app.js`, `site/style.css`: iPhone向け1ページUI。ニュース／メールを上部タブで切り替える。
- `ios/DailyReader/`: SwiftUIで全面実装したiPhone・macOSネイティブクライアント。`DailyReader` iPhoneターゲットと`DaymeldMac` macOSターゲットがAgent、今日、メール、ニュース、設定の画面とAPIモデルを共有する。iPhoneのBundle IDとHealthKit entitlementは更新互換性のためmacOSターゲットから分離する。
- iPhoneネイティブクライアントはHealthKit日次集計、Agentの完了・判断待ち・失敗遷移に対するローカル通知、App Intents、Keychainでの同期トークン保存に対応する。初回一覧取得は通知せず、停止中の遷移は次回の成功した一覧更新時に一度だけ通知する。APNsではないため、強制終了中の即時通知は保証しない。無料Personal TeamのApp ID消費を抑えるため、iPhone版は単一アプリターゲットを維持し、ウィジェットや通知Extensionは実機署名検証後に追加する。
- macOS版はMac mini API上のAgent、Planner、Gmail、ニュース、tanomi、Codex利用状況と、iPhoneから同期済みの健康集計を共有する。MacにはHealthKitデータストアがないため、HealthKit同期とトークン入力はiPhone版だけに表示する。`Command`+`R`で全データを再読み込みする。画面内容は`Command`+`=`（`Command`+`+`も可）／`Command`+`-`で80%から140%まで10%刻みで拡大・縮小し、`Command`+`0`で100%へ戻せる。倍率は次回起動時も維持する。拡大時は画面レイヤーを後段変形せず、各テキストスタイルを倍率に応じたポイントサイズでレイアウト・描画して文字の鮮明さを保つ。macOS版はApp Sandboxと外向きネットワークだけを許可し、HealthKit entitlementを含めない。
- macOS版のAgent一覧はVim式のキーボード操作に対応する。`j`/`k`で選択、`l`/`Enter`で展開、`h`/`Esc`で折り畳み、`Ctrl`+`d`/`Ctrl`+`u`でページ移動、`gg`/`G`で先頭・末尾、`zt`/`zz`/`zb`で選択カードを上・中央・下へ配置する。`dd`/`dj`は選択タスクを非表示にして次へ、`dk`は非表示にして前へ移る。テキスト入力中はこれらを無効化する。実行中または待機中のtanomiタスクは非表示にしない。
- iPhone・macOS版のAgent画面は、インストール中の`CFBundleShortVersionString`を、Mac mini APIが実際の配布成果物から返すOS別の最新版と比較する。一致時は緑の「アプリ最新版」、不一致時はiPhoneで黄色の「SideStore更新あり」、macOSで黄色の「アプリ更新あり」と両方の版を表示する。iOS最新版は`data/sidestore/source.json`と対応IPA、macOS最新版は`data/macos/Daymeld.app`からリクエストごとに検証して返し、Gitコミット数だけでは更新ありと判定しない。
- `scripts/build_macos_release.py`: 現在のコミット数を版にしたarm64 macOS Releaseをビルドし、アドホック署名、Sandbox・network client entitlement、HealthKit entitlement非混入、Bundle ID・版・アーキテクチャを検証して、Git管理外の`data/macos/Daymeld.app`とZIPを生成する。個人Mac以外へ配布する場合はDeveloper ID署名とnotarizationを別途行う。
- SideStore配布物は`scripts/build_sidestore_release.py`でHealthKit entitlementを含むアドホック署名済みseed IPAとして、メイン静的ルート外の`data/sidestore/`へ生成する。SideStoreはseed署名からentitlementを読み、端末上のApple Accountで再署名する。LAN用`0.0.0.0:8788`は`source.json`、`DailyReader.ipa`、`icon.png`だけを配信し、接続元をMac自身、自宅LANの`192.168.10.0/24`、IPv4 link-localの`169.254.0.0/16`（現環境ではiPhoneのUSB直接リンク）へ制限する。外出先用`127.0.0.1:8789`はスクリプト生成の32-byte random path tokenをconstant-timeで照合し、`remote-source.json`を`source.json`として、アイコンとソースに列挙した最大10版のIPAだけを配信する。サーバーログへ秘密URLを出さず、tokenファイルと`remote-source.json`は`0600`で保存する。SideStore自身は失敗時等にURLをiPhone診断ログへ記録し得るため、そのログも共有しない。Tailscale Funnelは`8443`番だけを8789へ中継する。配布ファイル不足や補助ポートの起動失敗時もメインサーバーは継続する。Agent、Gmail、健康情報のAPIは引き続き`127.0.0.1:8787`とTailscale Serveの`443`番に限定する。更新時はiPhoneのTailscaleを切り、LocalDevVPNを使用する。LAN用ソースは同一Wi-Fi、外出先用ソースは通常のWi-Fiまたはモバイル回線で取得する。LAN版からの移行時はremote source追加後、その一覧から一度installして更新元を関連付け、LAN sourceを削除する。
- 公式SideStore 0.6.3のAltSignはHealthKit entitlementをApple Developer Portal featureへ対応付けず、Daily Readerの再署名時にHealthKitを除去する。この端末ではSideStore 0.6.3（commit `4deda922`）へ`ios/patches/sidestore-0.6.3-healthkit.patch`を適用した自己ビルド版を使用する。パッチは`com.apple.developer.healthkit`とfeature ID `HK421J6T7P`を双方向に変換する。公式SideStoreへ更新せず、更新する場合は同等修正の有無を実装で確認する。再ビルド・IPA梱包・iLoader導入・AppleDouble除外・実機検証の詳細は`ios/README.md`に従う。HealthKit対応SideStore自体を新規作成・更新した際の手動適格性確認では、HealthKit許可画面の表示だけでなく、Tailscale接続下で実データ同期が成功することまで確認する。この手動確認は通常のDaily Reader IPA配信の完了条件には含めない。
- `site/data/articles.json`, `site/data/highlights.json`: 公開中の生成済みスナップショット。起動時・定期更新時に再生成するGit管理対象外の実行時データ。
- `data/read-events.jsonl`: 実際に開いた記事のローカル履歴。Git管理対象外。
- `data/feedback-events.jsonl`: 「表示したくない」と指定した記事のローカル履歴。Git管理対象外。
- `data/selection-history.jsonl`: 分野別ハイライトの連続掲載履歴。Git管理対象外。
- `data/update-stats.jsonl`: 更新ごとの新規記事数、ハイライト採用数、継続数。Git管理対象外。
- `data/assistant.sqlite3`: メール判定と対応状態。Mac mini内だけに保持しGit管理対象外。
- `data/planner.sqlite3`: タスク、日別ルーティン完了、健康日次集計。Mac mini内だけに保持しGit管理対象外。
- `data/agent.sqlite3`: Codexタスク、実行状態、イベント。Mac mini内だけに保持しGit管理対象外。
- HTTPサーバーを先に起動可能な状態にし、ニュース更新とGmail同期は別々のバックグラウンドスレッドで実行する。
- Gmailは起動直後と15分ごとに同期し、メールタブには迷惑メール・ゴミ箱を除く全未読メールを表示し、「今日」には重要な未読メールを表示する。アプリの「既読」はGmailのスレッドから`UNREAD`ラベルを外し、Gmailで既読にしたメールも次回同期後に一覧から外れる。読み取り同期は`gmail.readonly`でも継続し、Gmailへ既読を反映する操作だけ`gmail.modify`の再認証を必要とする。ニュース・Gmailの同期中もHTTP応答をブロックしない。
- ネイティブメール操作は完了・保留・対応不要などのローカル状態を楽観的に画面反映し、API処理を非同期で継続する。失敗時はカードを復元する。メール行は右スワイプでも完了でき、本文は`/api/email-content/{thread_id}`からオンデマンド表示する。
- 初期画面は「Agent」。通常タスク、日別ルーティン、健康チェックインは「今日」に表示し、HealthKit集計は専用トークン付きAPIで受け取る。
- Agentタブでは、依頼フォームを最上部に置き、その下にCodex app-serverとtanomiの利用枠ごとの使用率、残量、リセット日時（月日・時刻）を表示する。tanomiは`/api/tanomi/usage`の`five_hour`を「5時間」、`seven_day`を「週次」として表示し、利用量取得の失敗でタスク一覧を利用不可にはしない。
- Agentタブではtanomiも同じ画面で扱う。iPhone・ブラウザは8765へ直接接続せず、8787の同一オリジンBFFを使用する。tanomi停止時は既存Agentを表示し続ける。
- tanomi本体はDaily Readerの構成物ではなく、別ホストのTailscale Serve（既定 `https://xh23040023-l.tailc193b2.ts.net`）経由で起動する外部サービスである。導入元・実行ファイル・常駐設定はDaily Readerでは管理せず、tonoi/config側で別途管理する対象とし、実環境では`/api/health`、`/api/repos`、`/api/tasks`のJSON応答を確認してから利用可能と判断する。
- ヘッダーには、稼働中のパッケージ版とGitコミットを組み合わせたデプロイバージョン、サーバー起動日時、および初期表示・手動再読み込み時の画面更新日時を表示する。画面更新日時は経過5分未満を緑、5分以上10分未満を黄緑、10分以上を黄色で示す。
- 各タブは、画面上端から下へ引っ張る操作でもヘッダーの再読み込みボタンと同じ内容を更新する。
- Daymeld Agentタスクとtanomiチケットは更新時刻順の同じカード一覧へ統合し、Daymeldはミント、tanomiは紫の出所バッジ・左端アクセントで区別する。どちらも既定で折り畳み、状態アイコン、リポジトリ、更新時刻を一覧表示する。展開中は5秒ごとの更新後も同じ順序と展開状態を維持する。
- Daymeldカードを開くと「現在の進捗」と「やりとり」を分け、ユーザー・Agent・システム進捗を話者ごとに表示する。待機中・実行中・判断待ちの直近3件を表示し、カードから全履歴も展開できる。待機中・実行中・判断待ち、および作業環境を保持した失敗状態へ追加指示を送れる。tanomiカードは展開時だけ依頼内容と結果またはエラーを全文表示する。
- Agentタスクはカード全体の左右スワイプまたは一覧に常時表示する「非表示」ボタンでアーカイブでき、7日間アーカイブ一覧から確認できる。期限後は関連履歴、保持中の専用worktree、タスク用branchを自動削除し、アーカイブ後に進捗や履歴が更新されると通常一覧へ自動で戻す。
- 完了タスクは完了サマリーを表示し、同じカードから内容を質問できる。回答時は最新のリポジトリを読み取り専用で確認し、既存のやりとりへ回答を追加する。新たな変更は実装せず、別タスクとして案内する。
- Agentタブ最上部の依頼フォームは、省スペースなプロンプト入力とリポジトリ選択を表示し、実装モデル／Effortは折り畳み詳細から選択できる。画面遷移なしで即時実行する。

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

- 既定の実装モデルは `gpt-5.6-luna`、推論はlow。依頼ごとに対応するモデル／Effortを選択できる。
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

- 全フィードを上限付きで並列取得し、遅い・失敗した1フィードが他の新着取得を妨げない。外部APIや有料サービスは使わない。
- RSS/Atomに公開・更新日時がない記事は取得時刻を仮置きするが、`published_at_verified=false`として候補順位を減点し、新着枠から除外する。
- 通常記事の2日を超える将来日時は異常値として除外する。イベント・書籍は開催日・発売日が将来になり得るため除外しない。
- 公式リリース、自治体、一次研究などはフィードの`priority`を候補順位へ加算し、転載・まとめより一次情報を優先する。
- 公開24時間以内を最も強く、3日以内、7日以内の順に候補順位を加点し、14日超と前回掲載記事は減点する。
- Codexへ渡す候補は、全10分野の上位候補を先に最大8件ずつ確保してから全体新着・公式リリース・総合上位を加える。特定分野や公式記事が候補枠を先に使い切らないようにする。
- 同一タイトルの再配信記事を候補段階で重複排除し、1情報源は分野枠あたり最大3件に制限する。Google Newsの同じ記事や単一フィードが候補枠を埋めないようにする。
- 長いタイトルが酷似する同一シリーズ・続編も候補段階でまとめ、同じ検証やデスクツアーの連作より異なる発見を優先する。
- 選定では新しい事実、具体的な検証結果、失敗からの学び、実務への影響、意外性を重視し、14日超の記事は同じ分野に7日以内の適格候補がある場合は選ばない。
- モデルの出力は実際に渡した候補IDだけを許可し、書籍・今後の地域情報・新店を除いて、7日以内の代替候補がある場合は14日超の記事を事後検証でも除外する。
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

iPhoneネイティブクライアントを変更した場合は、署名なしのデバイスSDKビルドも実行する。

```bash
xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DailyReader -sdk iphoneos -configuration Debug \
  -derivedDataPath /tmp/daily-reader-ios-derived \
  CODE_SIGNING_ALLOWED=NO build
```

共有SwiftUIまたはmacOSネイティブクライアントを変更した場合は、署名なしのmacOSビルドも実行する。

```bash
xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DaymeldMac -sdk macosx -configuration Debug \
  -derivedDataPath /tmp/daily-reader-macos-derived \
  CODE_SIGNING_ALLOWED=NO build
```

HealthKit capabilityとSideStore再署名の可否はSimulatorでは確定できないため、XcodeでPersonal Teamを選択した実機ビルドを別途行う。Apple Accountへのログイン、Developer Mode、HealthKit・通知権限の許可はユーザー本人が操作する。

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

- 再起動後は、PID の更新、LaunchAgent の稼働、`http://127.0.0.1:8787/` の成功応答、`https://sk-mins-mac-mini.tailc193b2.ts.net/` の成功応答、および変更機能の代表的な動作を確認する。iPhoneクライアント変更の代表確認は次項の配信検証を指し、物理端末上の操作確認は含めない。
- SideStore配信を変更した場合は、生成IPAのバージョンとコード署名にHealthKitの2 entitlementが含まれること、`0.0.0.0:8788`の待ち受けと、MacのLAN IPおよび`sk-mins-Mac-mini.local`から`source.json`とIPAを取得できることを確認する。さらに`127.0.0.1:8789`だけで外出先用サーバーが待ち受け、`scripts/verify_sidestore_remote.py`でFunnelの現行3成果物がローカルと一致し、tokenなし、誤token、traversal、APIパス、非GETメソッドが404になることを確認する。同スクリプトでServe/Funnel JSONが`443 -> 8787`のtailnet限定と`8443 -> 8789`のFunnelだけであることも検証する。ここまで成功すれば配信デプロイは完了とし、検証出力へ秘密URLを含めない。
- iPhoneへのインストール、SideStoreによる再署名、Tailscaleを切った外部回線からの8443取得と443拒否、HealthKit・通知権限、実データ同期、画面・操作確認は配信後にユーザーが実施する独立工程とする。iPhone未接続やユーザー操作待ちをAgentタスクの失敗にせず、完了報告へ配信済みバージョンと実機導入の実施状況を分けて記載する。
- macOSクライアントを変更した場合は、main統合後に`scripts/build_macos_release.py`で最新アプリとZIPを再生成し、Bundle ID、版、arm64、アドホック署名、Sandbox・外向きネットワーク entitlement、HealthKit entitlement非混入を確認する。アプリを起動し、同じMac mini APIから代表データを取得できれば、このMac向け配布は完了とする。`/Applications`へのコピーは必須ではない。
- デプロイまたは実環境確認に失敗した状態を完了として扱わない。実行できない場合は未デプロイと阻害要因を明示する。
- 文書、コメント、テストだけの変更で実行時成果物が変わらない場合は再起動不要だが、その判断を完了報告へ明記する。

## Gitと環境上の注意

- GitHubリポジトリは `kds1010/daily-reader`、ブランチは `main`。
- 画像対応までの基準コミットは `57ab455 feat: add images to highlight cards`。現在の先端は `git log -1` で確認する。
- 端末設定は `~/.config/nix/` のNix/Home Manager管理。シェル、PATH、環境変数、Codex設定を直接変更しない。
- Mac mini固有のシステム適用コマンドは、ユーザー自身に実行してもらう。
- Tailscale FunnelはSideStore配布専用の`8443 -> 127.0.0.1:8789`に限り使う。`443`のServe、`8787`のメインサービス、Agent、Gmail、HealthKit、PlannerはFunnelへ載せない。FunnelのURL path tokenはbearer credentialである。漏洩時は有効化時と同じ引数へ`off`を付けた`tailscale funnel --bg --yes --https=8443 http://127.0.0.1:8789 off`で8443だけを止め、新tokenでreleaseを再生成してサービスを再起動し、新URLをSideStoreへ再登録・installして旧sourceを削除する。`funnel reset`は443 Serveも消すため使わない。0.6.3は保存済みsource URLを自動更新しないため、復旧まで外出先更新が停止する点を明示する。
- 既存のユーザー変更を破棄しない。`git reset --hard` 等を使わない。

## 再開時の最初の確認

1. `git status --short` で未コミット変更を確認する。
2. `lsof -nP -iTCP:8787 -sTCP:LISTEN` でサーバー稼働を確認する。
3. `site/data/articles.json` の `errors` と記事数を確認する。
4. `site/data/highlights.json` の10分野、記事リンク、画像数を確認する。
5. 公開URLはTailscale接続端末から確認する。

この文書と実装が食い違う場合は、実装と実データを正とし、この文書を更新すること。

## Sitesデプロイの判断

- 現時点ではSitesへの全面移行を行わない。Sitesで増えるのは、Mac mini停止中でも利用できること、Tailscaleなしの外部アクセス、管理されたクラウド永続化、認証付き共有などであり、ニュース収集やハイライト生成そのものの能力ではない。
- 現行のCodex CLI・Git worktree・LaunchAgent、ローカルGmail OAuth、HealthKit同期、Planner／AgentのSQLiteはSitesへそのまま移せない。Sites URLからtailnet内Mac APIへ接続する前提も成立しないため、移行には認証・公開API・クラウド実行基盤の再設計が必要になる。
- 将来スパイクする場合は、既存の`site/`とPythonサーバーを維持したまま、機密性の低いニュース一覧・ハイライトだけを独立したSitesフロントエンドとして作る。初期データは匿名化fixtureに限定し、Gmail、健康、Agent履歴はアップロードしない。
- フィードバックや閲覧履歴をクラウド保存する必要が生じた場合だけD1移行を別途設計する。Gmail、HealthKit、Planner、Agentのクラウド移行は、認証・秘密情報・データ保持方針を確定した別タスクとする。
- Sitesを試す場合も現行のTailscale版と並行稼働し、非公開アクセスで検証する。公開範囲、Mac停止時の期待動作、外部サービス利用を確認するまで現行URLを切り替えない。
