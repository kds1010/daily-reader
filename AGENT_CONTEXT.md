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
- Soundcore Workから取り込む会話録音はMac miniの`data/conversations/audio/`へ原音のMP3を保持する。文字起こし済みのUTF-8 TXTも10 MiBまで取り込み、原文と分類結果を`data/conversations.sqlite3`へ保存する。TXTは非空行を順番に発話として扱い、音声解析・話者推測をせず「話者1」とする。文字起こしと話者分離はMac内で完結する。2026-09-07のユーザーの自動化指示に基づき、導入後の新規録音は自動整理が有効なら、録音日時・話者・発話時刻・文字起こしをChatGPTログイン済みのCodex CLIへ渡し、タスク、フォローアップ、決定事項、アイデア、困りごと、調べもの、予定、関心、好みの候補を構造化抽出する。Codexは`--ephemeral`、`--sandbox read-only`、ユーザー設定・ルール無効、JSON Schema出力で一時ディレクトリから呼び出す。APIキー環境変数を子プロセスから除外し、MP3原音、GPS、ファイル名、ほかの録音は渡さない。候補は根拠発言とともに音声インボックスへ保存し、明確な用事・調査・日時が揃った参加予定を暮らしの項目へ自動追加する。曖昧な内容は確認候補にする。コードAgentへの依頼は引き続き明示操作が必要。原音・TXT原文は自動削除せず、MP3保存時は5 GiBの空き容量を必ず残す。
- 起動時とローカル時刻の8時、10時、12時、17時、20時、22時に更新する。
- Codex CLIを1更新につき最大1回呼び、低コストモデルで全ハイライトをまとめて生成する。

## 主要ファイル

- `config/feeds.toml`: RSS、Atom、公式ページ、Google News検索などの収集元。公式・一次情報は`priority`で明示する。
- `config/keywords.toml`: 記事スコアの加点・減点キーワード。
- `config/highlight-schema.json`: Codexの構造化出力スキーマ。
- `config/conversation-insight-schema.json`: 会話から抽出する確認待ち候補のCodex構造化出力スキーマ。
- `src/daily_reader/core.py`: 収集、各種パーサー、正規化、重複排除、画像抽出、新店日付検証。
- `src/daily_reader/highlights.py`: 候補選定、Codexプロンプト、出力検証、OG画像補完。
- `src/daily_reader/local_server.py`: ローカルHTTPサーバー、更新スケジューラー、閲覧・不要フィードバック・メールAPI。
- `src/daily_reader/conversations.py`: 録音、文字起こし、候補、根拠、レビュー状態をSQLiteへ保存し、Planner・Agentへ渡す前の検証を行う。
- `src/daily_reader/conversation_insights.py`: ChatGPTログイン済みのCodex CLIを一時・読み取り専用・構造化出力で呼び、文字起こしを候補へ変換する。
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
- 通常のAgentタスクは既定モデルで読み取り専用の実装計画を作成し、依頼時に選択した実装モデル／Effort（既定は`gpt-6-astra`／low）で同じスレッドを再開して実装・検証・コミットする。要件深掘りと完了後の質問は既定モデルを維持する。
- AgentキューのSQLite接続は各操作後に明示的にcloseし、10並列のポーリングでファイル記述子を蓄積しない。
- Agentアーカイブの期限切れ掃除は、短いDBトランザクションで`cleanup_pending`を保存し、接続を閉じてからGit削除し、成功後に履歴を削除する。DB横の`agent.sqlite3-archive-cleanup.lock`のflockで別プロセスも含め掃除を直列化する。ロックファイルは削除しない。削除開始後は再開・追加指示・状態変更を拒否し、HTTP APIは409を返す。この変更の適用時はWebサーバーを先に再起動して変更拒否を有効にし、その後Agentワーカーを再起動する。途中失敗・再起動でも削除予約と履歴を保持し、次の掃除で部分削除の続きから再試行する。登録解除済みworktreeに残存ファイルがある場合は保存したまま理由をログへ記録し、自動で再帰削除しない。
- アーカイブ掃除はワーカー全体で最短60秒間隔とし、別スレッドの掃除中もキュー取得を続ける。同じタスクの掃除失敗は60秒から最大1時間まで間隔を広げる。AgentのDB操作は各接続5秒待機、SQLiteのBUSY/LOCKED系だけをロールバック・close後に最大3回試行する。Codex進捗の一時競合はイベントを保持して後から保存し、結果・状態の保存は必須とする。失敗状態の保存も競合した場合は、タスクの外部処理を再実行せず保存だけを次のポーリングで再試行する。Codexイベント処理が異常終了した場合は専用プロセスグループを停止して回収する。ワーカーのSIGTERM時も全Codexグループを回収し、新規取得・起動を止める。停止による中断は失敗保存せずrunningを維持し、次回起動時に再キュー化する。
- 2026-09-08のタスク失敗は、アーカイブ掃除がSQLite書き込みロックを保持したままGitを実行し、同時実行タスクの進捗INSERTがロック待機を超過する経路で発生した。WALは既に有効だった。並行書き込み・削除と再開の競合・プロセス中断からの回復・10並列取得は`tests/test_agent_jobs.py`、掃除のバックオフ・進捗の後続保存・失敗保存の再試行・子プロセス停止は`tests/test_agent_worker.py`で検証する。SQLiteの書き込みが同時に1接続であることとBEGIN IMMEDIATEの意味は[公式トランザクション仕様](https://www.sqlite.org/lang_transaction.html)、接続の待機・rollback/closeは[Python sqlite3仕様](https://docs.python.org/3.12/library/sqlite3.html)を参照する。
- Agentワーカー起動時は、前プロセスの再起動で`running`のまま残ったタスクを自動で再キュー化し、保持した作業環境から再試行する。
- デプロイ確認は実装セッションとは別の自動承認付きCodexセッションで実行し、`launchctl`による再起動と実環境確認に必要な権限を自動審査へ回す。
- 実装・継続・デプロイ中の必須コマンドが`Operation not permitted`、CoreSimulatorService接続拒否、Simulator runtimeなしで失敗した場合は、ホスト環境不足と決めつけず承認付きで同じコマンドを再実行する。同じ隔離環境の失敗を複数ターン繰り返さない。
- デプロイ確認でAgentワーカー自身を再起動する場合は、現在のPID、起動時刻、デプロイ済みコミット、サービスログを先に確認する。復旧後のセッションで対象コミットへの再起動済みと確認できた場合は再度kickstartせず、自己再起動の反復と同一worktreeへのCodex多重実行を防ぐ。
- Codexの構造化出力スキーマはワーカー起動時にDaily Reader基準の絶対パスへ解決し、tonoiなど別リポジトリのworktreeでも同じスキーマを使用する。
- tonoi、config、tsugitateは`config/agent-repositories.toml`の`deploy = false`により、検証済み変更を`main`へ統合してpushした時点で完了とし、実環境デプロイは行わない。tsugitateの実環境への適用はNix/Home Managerで管理し、ユーザーが別途行う。
- `config/agent-repositories.toml`: Agentが操作できるGitリポジトリの許可リスト。Daily Reader、soan、宿直（tonoi）、config、継立（tsugitate）を登録し、ホーム相対パスにも対応する。
- `site/app.js`, `site/style.css`: iPhone向け1ページUI。ニュース／メールを上部タブで切り替える。
- `ios/DailyReader/`: SwiftUIで全面実装したiPhone・macOSネイティブクライアント。`DailyReader` iPhoneターゲットと`DaymeldMac` macOSターゲットがAgent、今日、メール、ニュース、会話、音声インボックス、設定の画面とAPIモデルを共有する。会話のCodex整理は送信範囲と停止設定を表示し、新規録音は自動開始、旧録音は手動開始とする。候補の編集、根拠確認、保存、破棄、Planner・Agentへの明示的な振り分けに対応する。iPhoneのBundle IDとHealthKit entitlementは更新互換性のためmacOSターゲットから分離する。
- iPhoneネイティブクライアントはHealthKit日次集計、Agentの完了・判断待ち・失敗遷移に対するローカル通知、App Intents、Keychainでの同期トークン保存に対応する。`public.mp3`のViewerとして登録し、Soundcore Workなどの共有・エクスポート先から受け取ったMP3を「会話」へ送信する。送信成功後はアプリの`Documents/Inbox`内にある受信コピーだけを削除し、共有元の原本は変更しない。初回一覧取得は通知せず、停止中の遷移は次回の成功した一覧更新時に一度だけ通知する。APNsではないため、強制終了中の即時通知は保証しない。無料Personal TeamのApp ID消費を抑えるため、iPhone版は単一アプリターゲットを維持し、ウィジェットや通知Extensionは実機署名検証後に追加する。
- iPhone版の「今日」から現在地の一回保存、または移動の記録開始・停止を選べる。記録開始時にWhen In Use権限を要求し、Core Locationの標準位置更新（目標精度100 m、移動距離100 m）とバックグラウンド位置更新・表示インジケーターを有効にする。固定間隔での取得は保証しない。Always権限は要求せず、強制終了・再起動後の記録はユーザーが再開する。取得した座標・精度・取得時刻・概算位置フラグは端末のApplication Support内の保護された未同期キューへ先に保存し、最大500件ずつMac miniの`POST /api/locations/sync`へ同期する。失敗時は保持し、位置更新時・前景更新時・手動操作で再試行する。履歴は`data/conversations.sqlite3`の`location_events`へ保存する。iPhone・macOSの「今日」→「GPSの取得履歴・マップ」で端末のローカル日付を選択し、`GET /api/locations?start=...&end=...`から500件ずつ取得して、時刻順一覧、MapKitの地点、取得時刻と水平精度を表示する。未同期データはiPhoneのGPSカードに件数と直近20件を表示し、地図へは同期後に反映する。地図表示にはAppleの地図サービスを使用する。GPS履歴はCodexへ送らない。録音との位置照合は確認済み録音日時がある場合のみ行い、取り込み時刻で代用しない。

GPSカードは記録の稼働状態、最終取得日時と経過時間、最終同期成功日時、取得失敗、未同期件数を区別します。取得時刻が古いだけでは、静止・記録終了・取得失敗のどれかは断定しません。記録中も「現在地を再取得して保存」を押せます。一回取得専用のCLLocationManagerを使い、継続記録のマネージャーは停止しません。100 mの移動基準とWhen In Use権限は維持します。最終取得・同期成功の時刻だけを保護された`location-diagnostics.json`へ保存し、起動時に復元します。診断ファイルには座標を保存せず、記録自体は再開しません。旧未同期キューは引き続き読み込み・再送できます。同期エラーには接続・応答・保存処理などの安全な分類だけを表示します。

- macOS版はMac mini API上のAgent、Planner、Gmail、ニュース、tanomi、Codex利用状況と、iPhoneから同期済みの健康集計を共有する。MacにはHealthKitデータストアがないため、HealthKit同期とトークン入力はiPhone版だけに表示する。`Command`+`R`で全データを再読み込みする。画面内容は`Command`+`=`（`Command`+`+`も可）／`Command`+`-`で80%から140%まで10%刻みで拡大・縮小し、`Command`+`0`で100%へ戻せる。倍率は次回起動時も維持する。拡大時は画面レイヤーを後段変形せず、各テキストスタイルを倍率に応じたポイントサイズでレイアウト・描画して文字の鮮明さを保つ。macOS版はApp Sandbox、外向きネットワーク、Calendarアクセスを許可し、HealthKit entitlementを含めない。
- KeyHintsは専用リポジトリ `kds1010/keyhints` で管理する軽量SwiftネイティブmacOSユーティリティ。Accessibility APIが公開する前面アプリの可視操作要素へキーボードラベルを表示し、`AXPress`または限定的な座標クリックを実行する。macOS 14+、Accessibility許可が必要で、OCR・Screen Recording・Input Monitoringは使用しない。Nix/Home Managerがarm64ビルド、導入、ログイン時自動起動を管理する。
- macOS版のAgent一覧はVim式のキーボード操作に対応する。`j`/`k`で選択、`l`/`Enter`で展開、`h`/`Esc`で折り畳み、`Ctrl`+`d`/`Ctrl`+`u`でページ移動、`gg`/`G`で先頭・末尾、`zt`/`zz`/`zb`で選択カードを上・中央・下へ配置する。`dd`/`dj`は選択タスクを非表示にして次へ、`dk`は非表示にして前へ移る。テキスト入力中はこれらを無効化する。実行中または待機中のtanomiタスクは非表示にしない。
- iPhone・macOS版のAgent画面は、インストール中の`CFBundleShortVersionString`を、Mac mini APIが実際の配布成果物から返すOS別の最新版と比較する。一致時は緑の「アプリ最新版」、不一致時はiPhoneで黄色の「SideStore更新あり」、macOSで黄色の「アプリ更新あり」と両方の版を表示する。iOS最新版は`data/sidestore/source.json`と対応IPA、macOS最新版は`data/macos/Daymeld.app`からリクエストごとに検証して返し、Gitコミット数だけでは更新ありと判定しない。
- iPhone・macOS版の「資料」タブは8787の`/api/soan/*` BFFだけを使用し、loopbackのSoan mobile API（既定`127.0.0.1:7337`）へ接続する。SoanのGoogle資格情報、LLM資格情報、workspace path、pairing tokenはクライアントへ保存しない。表示・選択単位にはSoan APIが返すネイティブブロック（region IDsを束ねたparagraph、table、image、figure、segment、TOC、section break）を使用し、クライアント側で行から独自ブロックを推測しない。本文はブロックのspanメタデータから装飾を再現する。ブロックコメントによるLLM改訂では選択ブロックのregionだけをLLMへ送り、Soan側で返答のregion構造を検証して元文書へ局所合成する。全文や他ブロックをLLMへ送らない。全文の文字入力はメニュー内の副次的な編集モードとする。LLM改訂は提案取得だけで、ユーザーが「Mac miniへ保存」を押すまでworkspaceを書き換えない。保存時は取得時本文をbaseとして送り、Mac側の変更後に古いiPhone下書きで上書きしない。Google Docsへのpushは引き続きSoan側の明示操作とする。
- ネイティブUXのDebug検証には匿名の`DaymeldFixtures.swift`を使う。`-daymeld-fixture standard|empty|partial-failure|stress|in-flight`で、Agent全状態、空・部分失敗、大量長文、今日・体調・メール・ニュースを実データなしで再現する。fixtureはRelease起動から選択できず、fixture操作はMac miniのDB・Gmailへ書き込まない。
- `scripts/build_macos_release.py`: 現在のコミット数を版にしたarm64 macOS Releaseをビルドし、アドホック署名、Sandbox・network client entitlement、HealthKit entitlement非混入、Bundle ID・版・アーキテクチャを検証して、Git管理外の`data/macos/Daymeld.app`とZIPを生成する。個人Mac以外へ配布する場合はDeveloper ID署名とnotarizationを別途行う。
- SideStore配布物は`scripts/build_sidestore_release.py`でHealthKit entitlementを含むアドホック署名済みseed IPAとして、メイン静的ルート外の`data/sidestore/`へ生成する。SideStoreはseed署名からentitlementを読み、端末上のApple Accountで再署名する。LAN用`0.0.0.0:8788`は`source.json`、`DailyReader.ipa`、`icon.png`だけを配信し、接続元をMac自身、自宅LANの`192.168.10.0/24`、IPv4 link-localの`169.254.0.0/16`（現環境ではiPhoneのUSB直接リンク）へ制限する。外出先用`127.0.0.1:8789`はスクリプト生成の32-byte random path tokenをconstant-timeで照合し、`remote-source.json`を`source.json`として、アイコンとソースに列挙した最新版1件のIPAだけを配信する。旧版は配信しない`release-history.json`と最大10版のIPAとしてMac内に保持する。サーバーログへ秘密URLを出さず、tokenファイルと`remote-source.json`は`0600`で保存する。SideStore自身は失敗時等にURLをiPhone診断ログへ記録し得るため、そのログも共有しない。Tailscale Funnelは`8443`番だけを8789へ中継する。配布ファイル不足や補助ポートの起動失敗時もメインサーバーは継続する。Agent、Gmail、健康情報のAPIは引き続き`127.0.0.1:8787`とTailscale Serveの`443`番に限定する。更新時はiPhoneのTailscaleを切り、LocalDevVPNを使用し、`Sources → Daymeld Remote → UPDATE`から更新する。SideStore 0.6.3のMy Appsは保存済みhasUpdate列と計算プロパティの不一致により更新なしと誤表示し得る。複数版ソースでは詳細表示0.1.194に対して旧版0.1.186が実機へ入ったため、公開更新候補は1件に固定する。完了は7 DAYS表示でなくDaymeldのインストール済み版で確認する。詳細な切り分けは`ios/README.md`を参照する。LAN用ソースは同一Wi-Fi、外出先用ソースは通常のWi-Fiまたはモバイル回線で取得する。LAN版からの移行時はremote source追加後、その一覧から一度installして更新元を関連付け、LAN sourceを削除する。
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
- Gmailは起動直後と15分ごとに同期し、メールタブには迷惑メール・ゴミ箱を除く全未読メールを表示し、「今日」には重要な未読メールを表示する。アプリの「既読」および「対応済み・完了」はGmailのスレッドから`UNREAD`ラベルを外し、Gmailで既読にしたメールも次回同期後に一覧から外れる。読み取り同期は`gmail.readonly`でも継続し、Gmailへ既読を反映する操作だけ`gmail.modify`の再認証を必要とする。ニュース・Gmailの同期中もHTTP応答をブロックしない。
- ネイティブメール操作は完了・保留・対応不要などのローカル状態を楽観的に画面反映し、API処理を非同期で継続する。失敗時はカードを復元する。メール行は右スワイプでも完了でき、メール概要のタップで外部Webを開かず本文を`/api/email-content/{thread_id}`からオンデマンド表示する。
- 初期画面は「Agent」。通常タスク、日別ルーティン、健康チェックインは「今日」に表示し、HealthKit集計は専用トークン付きAPIで受け取る。
- iPhone・macOS共通のタブ順は「Agent → 今日 → 会話 → 資料 → メール → ニュース → 設定」。表示順と選択用のtagは独立し、通知・ファイル共有からの遷移に使う既存tagを維持する。
- Agentタブでは、依頼フォームを最上部に置き、その下にCodex app-serverとtanomiの利用枠ごとの使用率、残量、リセット日時（月日・時刻）を表示する。tanomiは`/api/tanomi/usage`の`five_hour`を「5時間」、`seven_day`を「週次」として表示し、利用量取得の失敗でタスク一覧を利用不可にはしない。usageは短時間キャッシュし、上流の一時的なレート制限時は直近の取得値を前回取得として表示する。
- Agentタブではtanomiも同じ画面で扱う。iPhone・ブラウザは8765へ直接接続せず、8787の同一オリジンBFFを使用する。tanomi停止時は既存Agentを表示し続ける。
- tanomi本体はDaily Readerの構成物ではなく、別ホストのTailscale Serve（既定 `https://xh23040023-l.tailc193b2.ts.net`）経由で起動する外部サービスである。導入元・実行ファイル・常駐設定はDaily Readerでは管理せず、tonoi/config側で別途管理する対象とし、実環境では`/api/health`、`/api/repos`、`/api/tasks`のJSON応答を確認してから利用可能と判断する。
- ヘッダーには、稼働中のパッケージ版とGitコミットを組み合わせたデプロイバージョン、サーバー起動日時、および初期表示・手動再読み込み時の画面更新日時を表示する。画面更新日時は経過5分未満を緑、5分以上10分未満を黄緑、10分以上を黄色で示す。
- 各タブは、画面上端から下へ引っ張る操作でもヘッダーの再読み込みボタンと同じ内容を更新する。
- Daymeld Agentタスクとtanomiチケットは更新時刻順の同じカード一覧へ統合し、Daymeldはミント、tanomiは紫の出所バッジ・左端アクセントで区別する。どちらも既定で折り畳み、状態アイコン、リポジトリ、更新時刻を一覧表示する。展開中は5秒ごとの更新後も同じ順序と展開状態を維持する。
- Daymeldカードは折り畳み時から、依頼時に選択した実装モデルとEffortを表示する。これは計画ターンの既定モデルではなく、実装・検証ターンに使う設定である。カードを開くと「現在の進捗」と「やりとり」を分け、ユーザー・Agent・システム進捗を話者ごとに表示する。待機中・実行中・判断待ちの直近3件を表示し、カードから全履歴も展開できる。待機中・実行中・判断待ち、および作業環境を保持した失敗状態へ追加指示を送れる。tanomiカードは展開時だけ依頼内容と結果またはエラーを表示し、セッションが残る完了・失敗・停止済みタスクへ追加指示を送れる。
- Agentタスクはカード全体の左右スワイプまたは一覧に常時表示する「非表示」ボタンでアーカイブでき、7日間アーカイブ一覧から確認できる。期限後は関連履歴、保持中の専用worktree、タスク用branchを自動削除し、アーカイブ後に進捗や履歴が更新されると通常一覧へ自動で戻す。
- tanomiの依頼・結果はWebとiPhone/macOSでMarkdown表示する。見出し、段落、強調、HTTP/HTTPSリンク、入れ子リスト、引用、コード、表、本文改行に対応する。コードと表は横スクロール可能。ネイティブの結果プレビューは最大3ブロック・600文字を目安に完全なトップレベルブロックだけを表示し、省略時は必ず全文表示ボタンを出す。単独の長いブロックは切らず全文を表示する。コンテキストメニューからMarkdown原文をコピーできる。エラーは原文表示し、APIの送受信・保存形式は変更しない。
- MarkdownのWeb解析はローカル同梱のmarked 15.0.12のlexer、描画は `site/markdown.js` の許可DOM生成を使い、HTMLを挿入しない。ネイティブは `MarkdownDocument.swift` でFoundationのpresentationIntentをツリーへ復元し、`MarkdownContentView.swift` で描画する。HTMLは文字、画像は代替文字にし、資格情報付き・HTTP/HTTPS以外のURLを無効化する。Foundationが省略する完全に空のブロック・全空の表行はネイティブ表示でも省略され得るが原文コピーは保持する。変更時は両Xcodeターゲット、`tests/test_tanomi_markdown.py`、匿名standard/stress fixtureで確認する。
- アーカイブ操作はWeb・iPhone・macOSとも一覧から即時に楽観的除去し、実アーカイブAPIをバックグラウンドで継続する。API失敗時は元の位置へ復元し、連続操作中の定期スナップショットは保留中IDを除外する。
- 完了タスクは完了サマリーを表示し、同じカードから内容を質問できる。回答時は最新のリポジトリを読み取り専用で確認し、既存のやりとりへ回答を追加するとともにカードの最新サマリーへ反映する。元の完了内容は次回の確認へ引き継ぎ、新たな変更は実装せず別タスクとして案内する。
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

- ハイライト生成モデルは `gpt-5.6-luna`、推論はlow。Agentタスクの既定（`gpt-6-astra`／low）とは別に管理する。
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
jq empty config/conversation-insight-schema.json
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
- Web サーバー、Web UI、ニュース、メール、プランナー、設定、サーバー依存関係の変更では `org.nix-community.home.daily-reader` を再起動する。Agent キュー、Agent ワーカー、リポジトリ操作の変更では `org.nix-community.home.daily-reader-agent-worker` も再起動する。iPhone/macOSクライアントと配布成果物だけの変更では、サーバーが正常稼働中なら再起動せずに成果物生成と配信検証を行う。
- デプロイ開始時は再起動の要否にかかわらず、LaunchAgentの状態、プロセスの実行コマンド、8787/8788/8789の待受PIDを確認する。`launchctl`の`state = running`だけでは正常と判断せず、実体が`daily-reader-local`であること、`xpcproxy`のまま停滞していないこと、3ポートを同じPIDが想定アドレスで待ち受けていることを確認する。
- 再起動前に `lsof -nP -iTCP:8787 -sTCP:LISTEN` で PID を記録する。再起動は次の形式で行う。

```bash
launchctl kickstart -k gui/$(id -u)/org.nix-community.home.daily-reader
launchctl kickstart -k gui/$(id -u)/org.nix-community.home.daily-reader-agent-worker
```

- 再起動後は30秒以内に新PIDが`daily-reader-local`として8787/8788/8789を待ち受けることを確認する。`xpcproxy`のまま、プロセスだけ存在して待受なし、またはHTTP応答なしの場合は起動失敗であり、完了とせずログとプロセス状態を調査する。再起動を省略した場合も同じサーバー状態確認を行う。
- `http://127.0.0.1:8787/` の成功応答、`https://sk-mins-mac-mini.tailc193b2.ts.net/` の成功応答、および変更機能の代表的な動作を確認する。iPhoneクライアント変更の代表確認は次項の配信検証を指し、物理端末上の操作確認は含めない。
- SideStore配信を変更した場合は、生成IPAのバージョンとコード署名にHealthKitの2 entitlementが含まれること、`0.0.0.0:8788`の待ち受けと、MacのLAN IPおよび`sk-mins-Mac-mini.local`から`source.json`とIPAを取得できることを確認する。さらに`127.0.0.1:8789`だけで外出先用サーバーが待ち受け、`scripts/verify_sidestore_remote.py`でFunnelの現行3成果物がローカルと一致し、tokenなし、誤token、traversal、APIパス、非GETメソッドが404になることを確認する。同スクリプトでServe/Funnel JSONが`443 -> 8787`のtailnet限定と`8443 -> 8789`のFunnelだけであることも検証する。ここまで成功すれば配信デプロイは完了とし、検証出力へ秘密URLを含めない。
- SideStoreがTLSエラーを表示した場合は、HTTPSのTLSハンドシェイク、HTTPステータス、`127.0.0.1:8789`の順に切り分ける。TLS成立後に443/8443が502なら証明書ではなくFunnel転送先の停止として扱い、8789とサービスPIDを確認する。
- SideStore配信診断は`uv run --frozen python scripts/verify_sidestore_remote.py`（Python 3.12以上）を使う。接続・TLS・タイムアウト・応答途中切断、成果物404/502と200の内容不一致、拒否パスの異常応答を区別し、秘密URLを含み得る通信例外の原因チェーンを表示しない。`--request-origin http://127.0.0.1:8789 --skip-tailscale-config`で転送先を直接検証できる。検証由来の404と実機失敗を混同せず、ソース200だけでIPA取得・再署名成功を判定しない。診断表は`ios/README.md`、回帰検証は`tests/test_verify_sidestore_remote.py`を参照する。
- 2026-09-08のSideStore更新失敗は、ユーザー提示の文言から`OperationError 1006`（端末UDID取得不可）と特定した。0.6.3の`fetchUDID()`はMinimuxer未開始・VPN経由の端末探索失敗でもnilになり、SideStore自身の再署名時にはペアリング情報欠落でも同じエラーになる。文言だけで再ペアリング必須と判断しない。既存ペアリングを維持し、Tailscaleを切ってLocalDevVPN接続、実際のTunnel IPとSideStoreの`Settings → VPN Configuration → User Device IP`の一致、`Confirm`による再スキャン、必要ならSideStoreの完全終了・再起動、Daymeld UPDATEの順で確認する。4deda922のiOS 26.4以降の既定値は`192.168.1.50`であり、LocalDevVPNの実設定と異なる場合がある。固定値を無条件に適用しない。同日20:02 JSTはLocalDevVPNがConnected、Local IP `10.7.0.0`／Tunnel IP `10.7.0.1`に対し、SideStoreがUser Device IP `192.168.1.50`、Discovered Device IP `N/A`、Active `No`だった。実接続先へ合わせてConfirm後、20:06にDiscovered Device IP `10.7.0.1`、Active `Yes`を確認した。この時点で更新完了は未確認、再ペアリングは未実施であり、接続先検出の回復を更新成功と混同しない。その後20:39〜20:40にSideStoreを完全終了後の新規起動から開き、Sources → Daymeld Remote → UPDATEを実行した。My Appsの更新なし・Daymeldの7 DAYS表示に加え、20:41にDaymeld本体の「インストール済み 0.1.214 (214)」を確認し、再ペアリングを一切行わず実機更新に成功した。これは同日の確認結果であり、今後の1006が同じ操作で必ず解消するとは保証しない。同日20:42にTailscale Connectedへの復帰、20:44:47にGPS記録再開と新規取得、20:45:16にGPS最終同期成功を実機画面で確認した。なお1006が続く場合だけ、接続可能なiPhoneを先に確認してSideStoreの`Reset Pairing File`、iLoaderの`Delete Stored Pairing → Refresh`と再ペアリング、`Manage Pairing File`の既存SideStore行への`Place`、端末でのRefresh・Daymeld UPDATEを確認する。HealthKit自己ビルド版は維持し、公式SideStoreの再インストールやアプリ削除を行わない。端末未接続時は既存ペアリングを削除しない。公式資料、4deda922の実装根拠、確認手順と成功判定は`ios/README.md`の1006診断を参照する。配信検証の成功だけでは実機エラーの解消を判定しない。
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

## 会話解析の復旧と録音日時

- サーバー起動時に中断された音声解析・Codex整理を失敗状態へ戻し、画面から再試行可能にする。自動整理の導入後に取り込んだ録音は、設定が有効なら文字起こし完了後にCodex整理を自動実行する。失敗は間隔を空けて最大3回試し、旧録音は本人の開始操作を維持する。
- 同一MP3の再取り込みでは既存の解析結果と確認待ち候補を保持し、新規保存時だけ音声解析を開始する。
- 会話詳細は表示中の音声解析・Codex整理が終わるまで状態を更新する。画面を開き直した場合も処理中なら更新を再開する。
- 新規MP3・TXT取り込み時は、Soundcoreの未変更タイトルを含むファイル名から秒までの録音日時を取得する。例: `2026-09-05_2026-09-05 15:26:25.mp3` → `2026-09-05T15:26:25+09:00`。先頭の書き出し日ではなく後半の日時を採用する。日付接頭辞なし、TXTの`_文字起こし`接尾辞にも対応する。Soundcore側の文字起こしは不要。ファイル名にタイムゾーンがないため日本時間として扱う。
- 日付のみ・改名済み・不正な日時のファイル名は録音日時不明とし、ファイル更新日時や取り込み日時を代用しない。既存レコードと重複取り込み時の日時・解析結果は変更しない。未確認の旧日時はCodexへnullを渡し、日時不明時は相対期限を日付へ変換せず原文を保持する。
- APIで明示するタイムゾーン付き`recorded_at`（`recorded_at_confirmed=true`が必要）はファイル名より優先する。日本以外で録音した日時はこの明示指定で補正する。

## 会話とGPSの自動紐付け

- 録音取り込み、音声解析完了、GPS同期、サーバー起動時に、確認済み録音日時とGPS履歴を自動照合する。前後5分以内・水平精度200 m以内・概算位置ではない観測を対象とし、取り込み時刻は使わない。
- `conversation_location_links`に録音・発言・GPSのID、対象時刻、日時の出典、算出根拠、時刻差、照合状態、方式版を保存する。音声発言は開始日時＋音声内秒からの推定とし、TXTの行番号は発言時刻に使わない。
- iPhone・macOSの会話詳細から録音・発言の推定場所、日時の出典、時刻差、精度、未照合理由、地図を確認できる。録音の停止・編集・時計ずれによる誤差を表示する。
- Planner・Agentへの承認時と発言の再解析前に、候補の根拠へ位置コンテキストのスナップショットを保存する。後から届くGPSは現在の位置リンクだけを更新し、過去の判断根拠は維持する。GPSはCodexへ送らない。
- 保存する関連、推定の限界、予定・人物履歴へ発展させる方針は[会話コンテキスト設計](docs/conversation-context.md)を参照する。
- [生活改善の設計](docs/conversation-value-design.md)に、約束の抜け・管理時間を評価する条件付き試算と6週間の試用方法を記載している。数値は実測ではなく設計仮説であり、計測・予定通知・試用はまだ開始していない。
- 2026-09-07にユーザーは、ネット巡回時間の削減、イベントの忘れ防止、必要な調べもののバックグラウンド実行を重視すると指定した。同設計書第10節へ反映している。コードAgentとは別に、暮らしの調査・予定・タスク・プロフィールを実装した。仕様と制約は[暮らしのアシスタント](docs/life-assistant.md)を参照する。

## 暮らしのアシスタント

- iPhone・macOSの「今日」から`LifeAssistantView`を開き、新規録音・一文メモからタスク・調査・予定を自動作成し、曖昧な候補だけ入力済みの確認画面へ残す。`data/conversations.sqlite3`の`life_entries`と`life_people`に保持する。元候補・調査結果・予定の関連を根拠に固定し、冪等キーと改訂番号で再送と古い端末の上書きを防ぐ。
- `life_research.ResearchWorker`はWebサーバー内で1件ずつ調査し、待機含め10件、1回600秒。ChatGPTログイン済みCodexのlive Web検索・読み取り専用・シェル無効を使う。手動調査は確認した依頼本文・条件・期限・URLを送り、自動調査は抽出時に個人情報・非公開情報を除いた公開質問文だけを送る。自動開始は日本時間で1日3件まで。コードAgentや人物一覧、GPS、他の会話を自動で含めない。停止・再試行・再起動時の失敗復旧に対応し、定期自動調査はまだない。変更時はWebサーバーのLaunchAgentを再起動する。
- 予定の開始・終了・タイムゾーン、申込期限、準備・出発・通知を保存する。予定変更は個別上書きのない関連タスク日時へ伝播し、中止時は未完了の関連タスクも中止する。申込済み時は関連する申込タスクを完了し、同一段階・同時刻の重複通知と完了後の段階通知を抑制する。変更前の予定・タスクは`life_revisions`へ保持し、詳細で直近20版を表示する。
- Calendarは初回権限許可後、端末更新時に追加・変更・中止を自動反映する。設定で停止できる。外部編集・削除・既存予定との連携開始は確認対象とし、逆同期はしない。macOSは`com.apple.security.personal-information.calendars`を持つ。iPhone・Macともfull access説明を付け、端末で本人が許可する。
- 生活通知は端末で将来の直近40件を予約し、更新で中止・変更分を取り除く。調査結果通知は前景更新・iPhone BGAppRefresh時に状態変化を通知する。APNsでなく終了中の即時通知は保証しない。通知を押すと「今日」へ移動する。
- プロフィールは録音内の対象者を一度選択し、同じ録音・対象者の明示的な情報へ引き継ぐ。人物の訂正は個別確認されていない引き継ぎ先にも反映する。別の録音へ同名だけで引き継がない。自分の関心・目標の有効な短いタイトルを既存ハイライトに文字列照合し、最大3記事を推薦する。第三者の情報・期限切れ・事実は推薦対象外。修正・保管・削除に対応し、敏感な特性や同名人物を自動推定・統合しない。
- 調査の有用性と任意の本人見積もり節約分数を保存する。生活改善率・閲覧削減時間は自動測定していない。検証は`tests/test_life_assistant.py`、`tests/test_life_automation.py`、実API JSONとSwiftモデル往復・権限宣言は`tests/test_life_native.py`、両プラットフォームのXcodeビルドを使う。

- `life_automation.AutomationWorker`はWebサーバー内で5秒ごとに新規録音、確認候補、予定の準備・申込タスク、調査結果の次の行動候補を処理する。導入日時・停止設定・試行回数・候補採否・録音内対象者の対応はSQLiteへ永続化する。文字数6万超の録音は手動開始へ戻す。停止は新規の自動処理を止め、開始済み調査は個別停止する。新しいメモには入力時刻を使い、過去の録音の取り込み日時と混同しない。

## iPhoneの自動コンテキスト

- `PhoneContextSync`は許可済みカレンダーの直近7日〜今後14日、Core Motionの最大7日分の移動区間と端末タイムゾーンを前景/BGAppRefreshで最短15分ごとに取得し、自分のMacの`POST /api/device-context/sync`へ同期する。モーションには初回権限が必要。取得失敗・拒否・停止・空の成功を区別し、未同期は保護ファイルへ保持する。旧送信は新しいスナップショットを上書きしない。
- `device_context.py`は90日までの観測を同じ会話DBへ保存し、録音/発言時刻と半開区間で照合する。カレンダーの時刻一致を参加や人物同定の証拠にしない。新鮮なカレンダーで予定の重なりと当日の20分着手枠を最大3件表示する。タスクの自動完了や着手枠の自動予約はしない。iPhone以外でも同期済み情報を表示する。
- GPSは速度・速度精度・シミュレーションフラグを取得し、`nearest-gps-v2`で移動による時刻差の影響を照合条件へ加える。場所の品質を保証するものではなく、速度不明の旧データは従来条件を使う。
- HealthKitは認証操作後の更新機会に最短30分間隔で既存5種を自動同期する。未取得を0にせず、当日正午までの24時間内の睡眠区間だけを重複除去して集計する。覚醒・inBedは除外する。日付は端末のローカル日付を使う。
- カレンダー・移動・健康データはCodexへ送信しない。端末で取得を個別停止できる。OSによるバックグラウンド制限と取得期間上限は維持する。仕様・保存境界・公式根拠・検証は[自動取得コンテキスト](docs/iphone-context.md)を参照する。

- 2026-09-07の実機設定で、GPSと端末コンテキストの小数秒付き日時がUTC設定でも末尾Zを含まず、APIがHTTP 400で拒否する問題を確認した。`preciseUTCTimestamp`でタイムゾーンを明示し、旧版がGMTで作った端末内キューだけ`repairLegacyQueuedUTCTimestamp`で復元して再送する。ユーザー入力やAPIのタイムゾーン必須条件は緩めない。同期拒否時はGPS・予定の本文をログへ出さず理由と転送形式だけ記録する。`tests/test_ios_sync_timestamps.py`はSwiftの実出力をPython保存処理へ渡し、再送の重複排除も検証する。

- 同日の実機同期で、Core Motionの1秒未満の区間を秒単位へ丸めると開始・終了が同値になり、予定を含む同期全体が拒否されることも確認した。移動区間・取得窓は小数秒とUTCを保持し、シリアライズ後も正の長さを持つ区間だけ作る。旧キュー内の同時刻の移動区間は再送から除外し、続くCore Motion再取得で補う。小数秒の日時解析にも対応し、旧キューを解析不能として読み飛ばさない。Swiftの実出力による0.7秒区間と旧キュー回復をPython保存まで検証する。

## PayPayの支払い明細

- iPhone・macOSの「今日」→「PayPayの支払い明細」で、本人がPayPayから出力したUTF-8の個人向け13列CSVを手動取り込みする。自動取得や資格情報の保存は行わない。
- `payment_history.py`が全行検証後、`data/payments.sqlite3`へ原文の列・正規化日時・整数の円金額・取引番号・取り込み履歴を保存する。DBは0600、Git管理外。日時は日本時間と明示して解釈し、元の日時も保持する。
- `POST /api/payments/import`と期間・ページ指定付き`GET /api/payments`を8787で提供する。同一ファイルと同内容・同番号の行を重複除外し、内容競合は既存行を保持して報告する。番号なし行は別ファイル間で自動統合せず注意件数を表示する。支出の合計や残高は算出しない。
- 明細APIはloopback/既存Tailscale ServeのHostと同一Originを検査し、no-storeで応答する。金融明細をURL・ログ・Codexへ送らず、Funnelへ公開しない。CSV原本は変更しない。Debug fixtureは実APIへ接続しない。
- macOSは選択したCSVを読むため`com.apple.security.files.user-selected.read-only`を持つ。配布スクリプトも必須entitlementとして検証し、選択ファイルへの書き込みは許可しない。
- 操作・公式根拠・形式の制限・重複条件は[支払い明細](docs/payment-history.md)を参照する。検証は`tests/test_payment_history.py`、`tests/test_local_server.py`、`tests/test_payment_native.py`と両OSのビルド。配信時はWeb再起動と両クライアントの成果物再生成・配信確認が必要。

## Gmail認証の診断と継続利用

アクセストークンの期限は自動更新します。`invalid_grant`などでrefresh token自体が
失効した場合は本人の再同意が必要です。通信障害とは区別して案内し、保存済みメールは保持します。
まずMac miniのサービス用リポジトリで、ネットワーク接続・トークン更新・DB初期化を行わない
診断を実行してください（作業用worktreeでは実運用の資格情報やDBを参照しません）。

```bash
uv run --frozen daily-reader-gmail doctor
```

診断にはファイルの有無、refresh tokenの有無、Gmail権限、アクセストークン期限、保存権限、
最終同期成功・試行日時、認証要求状態だけを表示します。秘密値・メール本文は表示しません。
アクセストークン期限からrefresh tokenの期限やOAuthアプリの公開状態は判定できず、
これらは不明と表示します。ファイルが読めてもGoogleとの接続成功を意味しません。

[Google公式資料](https://support.google.com/cloud/answer/15549945?hl=en)によると、
外部向けOAuthアプリがTestingの場合、Gmail権限のrefresh tokenは同意から7日で失効します。
繰り返す場合は次の順で確認してください。

1. Google Cloudで`secrets/gmail-client.json`を発行したプロジェクトを選び、
   Google Auth Platform → Audienceの公開状態を確認します。診断コマンドでは取得しません。
2. Testingなら、[個人利用の審査例外](https://support.google.com/cloud/answer/13464323?hl=en)
   と対象ユーザーを確認し、本人が公開状態をIn productionへ変更します。個人利用でも未確認アプリの
   警告やユーザー数上限は残ります。これはOAuthアプリの公開設定であり、DaymeldのWebサーバーを
   インターネットへ公開する操作ではありません。本環境の状態は未確認で、自動変更しません。
3. 変更後は、古いトークンが有効でも新しく同意するため、次を実行します。

```bash
uv run --frozen daily-reader-gmail auth --force
```

`--force`は`auth`専用です。ブラウザでGoogleのログイン・Gmail権限への同意を完了してください。
通常の初回認証・失効からの復旧は従来の`auth`も使えます。`auth`は成功後にメールを同期します。
有効なrefresh tokenと必要権限を取得できた場合だけ、0600の一時ファイルから認証情報を置き換えます。
キャンセル、権限不足、保存失敗時は既存トークンを保持します。定期更新とCLIは同じロックを使い、
同意待ち中はロックを保持しません。その間に別処理がトークンを更新した場合は上書きを避け、
再実行を案内します（通常のアクセストークン更新との競合でも再実行が必要になる場合があります）。
有効なトークンを読み取っただけでは書き直しません。

再認証後は`doctor`とメール画面で最終同期成功日時の更新・認証要求の解除を確認してください。
In productionでもユーザーの取り消し、Gmail権限を含む場合のパスワード変更などで失効し得ます。
[失効条件](https://developers.google.com/identity/protocols/oauth2#expiration)を確認し、永久接続や
7日超の継続を即時の検証だけで保証しないでください。Google側の設定変更・本人の同意・実同期復旧は、
コードの検証・配信とは分けて報告します。

## ネイティブクライアントの応答性

- 初期表示・手動更新はAgent、画面データ、補助情報を独立した更新経路で取得する。メールや端末コンテキストの完了をAgent表示の前提にしない。tanomiの一覧も設定・利用状況の取得を待たない。
- 前景のDaymeld一覧とtanomi一覧はそれぞれ取得完了後5秒で更新し、一方の遅延で他方の周期を延ばさない。設定・利用状況は60秒間隔、暮らしは30秒間隔で更新する。同じAgent一覧・読み込み状態ではPublished通知を出さず、通知状態も変更があった場合だけ保存する。
- `ResourceRefreshes`がリソースごとの取得を一本化する。書き込み後は古い世代を無効化し、新しい取得を開始する。非表示・メール完了の楽観表示を、操作前に発行したGETで戻さない。
- EventKitの同期検索・保存は専用actor内で完結し、EKEventをUIへ渡さない。端末コンテキストの保護ファイルとCore Motionの並べ替えもMainActor外で処理する。停止・権限変更は送信直前と自動カレンダー反映前に再確認する。
- GPSの`LocationJournal`が未同期キューを所有し、追加・成功IDの削除を保存成功後に確定する。復元完了前の取得は保留し、保存待ち中の停止・権限拒否を古い完了通知で上書きしない。端末全体の位置情報OFFの確認は権限拒否時に専用actorから行う。旧キュー・日時修復・ファイル保護・500件単位の送信は維持する。
- 回帰検証は`tests/test_ios_performance.py`と`tests/swift/AppRefreshHarness.swift`で実AppModel／URLSessionへ匿名の遅延・失敗応答を注入する。GPSの追加・送信中追加・保存失敗・状態競合は`tests/test_ios_location_runtime.py`で実サービス／journalを実行する。既存の静的fixtureは通信を省略するため、fixture表示だけで通信待ちや性能改善を判定しない。

## 日記の自動下書き

- iPhone・macOSの「今日」→「日記」から、Asia/Tokyoの日付ごとに記録から作る下書きを閲覧・編集・保存・削除できる。`diary.DiaryWorker`がWebサーバー内で起動時と5分ごとに当日＋過去7日、導入日以降を自動更新する。過去日は手動生成、自動生成は停止可能。
- 完了タスク・日別ルーティン・健康チェックイン・確認済み録音日時の会話・登録予定を、Mac内のテンプレートだけでまとめる。Codex・外部APIを呼ばず、GPS・メール・Agent履歴・人物プロフィールを材料にしない。予定の参加、会話の本人同定、感情を推測しない。材料なし・取得失敗を区別する。
- `data/planner.sqlite3`の`diary_entries/settings/revisions`へ自動本文・本人編集・根拠・改訂を保存する。本人編集と保存時の根拠を自動更新で消さず、旧版は日付ごとに最大20版を保持する。競合は409で入力を保持する。日記削除は本文・根拠コピー・履歴を消し、削除済みの印で自動再作成を防ぐ。元の生活記録は保持する。
- 日記の再取得はネイティブの補助情報レーンで並行実行し、Agent一覧や今日・メールの取得を待たせない。AppModelと同じAPIClientを使い、遅延した日記APIがAgent初期表示を妨げないことを性能テストでも確認する。
- 実装は`src/daily_reader/diary.py`と共有`Diary.swift`。検証は`tests/test_diary.py`、`tests/test_diary_native.py`、`tests/test_local_server.py`と両OSビルド。仕様・データ境界・制限は[日記](docs/diary.md)を参照する。配信にはWebサーバー再起動と両OS成果物の生成・配信検証が必要。

## 暮らしの秘書

- `secretary.py`は`GET /api/life`へ後方互換の`secretary`を追加するローカル集約処理。暮らしの用事、通常Planner、取得期間内の新鮮なCalendar、重要未読Gmail、確認候補、調査結果、既存関心記事を根拠・日時確実性付きで並べる。追加のLLM、OAuth操作、Gmail本文取得は行わない。元IDと明示的な親子関連で重複を除き、通常3件と全件・緊急件数を表示する。
- `secretary_cards`は内容版別の確認・保留、`secretary_days`は日付・タイムゾーン別の任意自己記録、`secretary_requests`は再送応答を同じ会話DBに保存する。確認は元タスク完了・Gmail既読と独立し、内容変更・期限当日の再提示、日跨ぎ保留、翌日のルーティンに対応する。
- Gmailの鮮度目安は1時間、ニュースとCalendarは24時間。取得成功時刻を集約時刻で代用せず、未取得・失敗・認証必要・停止・古さを区別する。既読・非表示記事は推薦前に除外する。カレンダー・GPS・健康をCodexへ送る範囲は増やさない。
- iPhone/macOSは`LifeAssistant.swift`の秘書カードから元項目、全件、取得状況、20分着手候補、週次評価へ移動する。自己記録の空欄と0を区別し、本人見積もり節約分数を別集計する。無駄な時間や忘れ、改善率は自動推定しない。定期調査・イベント変更監視は対象・周期・終了条件を要する後続拡張。
- 検証は`tests/test_secretary.py`、`tests/test_local_server.py`、`tests/test_life_native.py`と両OSのビルド。匿名fixtureの秘書導線は実APIへ書き込まない。サーバーと共有Swift変更のため、統合後はWeb LaunchAgent再起動、両OS成果物の再生成と配信検証が必要。
