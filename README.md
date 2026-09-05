# Daymeld (内部プロジェクト名: Daily Reader)

> SwiftUIネイティブiPhone・macOS版は[`ios/README.md`](ios/README.md)を参照してください。

Codexへの自律タスク投入、今日やること、重要メール、関心のあるニュースをiPhoneとMacで扱える個人用ダッシュボードです。Mac miniのlocalhostで動かし、Tailscale Serveを通して自分のtailnet内だけに公開します。外部サーバーやデータベースは必要ありません。SideStoreの更新成果物だけは、秘密URLで保護したTailscale Funnelの専用ポートから配信できます。起動時はAgentタブを最初に表示します。

## 会話データの解析

iPhone・macOS版の「会話」タブでは、Soundcore Workが書き出したMP3に加え、文字起こし済みの
UTF-8 TXTを取り込めます。MP3原音は`data/conversations/audio/`へ再圧縮せず保存し、TXT原文は
`data/conversations.sqlite3`へ保存します。同一種別・同一内容はSHA-256で重複排除します。
TXTは10 MiBまでです。MP3保存後の空き容量が5 GiB未満になるアップロードは拒否し、取り込んだ
原音・原文は自動削除しません。

Mac側ではfaster-whisperで日本語を文字起こしし、pyannoteの
`speaker-diarization-community-1`で話者区間を分離します。Hugging Faceでモデルの利用条件に
同意したうえで、アクセストークンを改行付きの
`secrets/huggingface-token.txt`へ保存してください。音声と解析処理はMac内で完結します。
TXTは音声解析を行わないため、Hugging Faceトークンは不要です。非空行を順番に発話として扱い、
話者を推測せず「話者1」として分類します。解析結果、話者、話題、発話、確認待ちタスクは
`data/conversations.sqlite3`へ保存されます。
候補タスクは追加指示を編集してから「Agentへ依頼」または「通常タスクに追加」を選ぶまで
実行されません。

## 主な機能

- AgentタブからCodexへタスクを投入し、専用worktreeで実装・検証・main反映まで継続
- Agentタブからtanomiへもタスクを投入し、同じ画面で状態と結果を確認
- Agentタブ最上部からプロンプト、リポジトリ選択、依頼ボタンで直接タスクを投入し、その下にCodexとtanomiの使用率、残量、リセット日時（月日・時刻）を利用枠ごとに表示
- Daymeldとtanomiのタスクを更新時刻順の同じカード一覧に表示し、ミント／紫の出所色で区別
- Agentタスクの待機、実行、判断待ち、完了、失敗状態をiPhoneから確認
- Agentタスクが完了・判断待ち・失敗へ遷移した際の通知（初回取得後のローカル通知）
- 完了タスクのサマリーを確認し、その内容について同じカードからAgentへ質問
- 作成済みAgentタスクのやりとりを確認し、実行中でも同じCodexスレッドへ追加指示を送信
- tanomiの完了・失敗・停止済みタスクへ同じセッションを継続する追加指示を送信
- Agentタスクをカード全体の左右スワイプまたは一覧に常時表示する「非表示」ボタンでアーカイブし、表示は操作直後に滑らかに更新して実アーカイブを裏で継続する（失敗時は復元）。7日間閲覧可能にした後に自動削除。進捗更新時は通常一覧へ戻す
- ヘッダーに稼働中のパッケージ版、Gitコミット、デプロイ日時、画面更新日時を表示（経過5分未満は緑、5分以上10分未満は黄緑、10分以上は黄色）
- iPhone・macOSのインストール版を実際の配布成果物と比較し、「アプリ最新版」または「アプリ更新あり」を表示
- 画面上端から下へ引っ張り、表示中のタブを手動更新
- RSS/Atomと公式ページの並列定期取得（外部API・有料サービス不要）
- URL正規化による重複除去
- キーワードによる注目度スコア
- カテゴリ、検索、新着順／注目順の切り替え
- 「あとで読む」の端末内保存と、記事を開いた既読イベントの記録
- 各記事の「表示したくない」フィードバックを次回のハイライト選定へ反映
- 24時間以内の新着優先、前回掲載の減点、連続掲載の抑制と「新着／継続」表示
- フィード日時の信頼性を検証し、日時不明の記事を新着扱いしない一次情報優先ランキング
- 更新ごとの新規記事数、ハイライト採用数、新選・継続数の表示と履歴保存
- ダークモード、ホーム画面起動、オフラインキャッシュ
- 一部フィードが失敗しても、取得できた記事で更新を継続
- 起動時と毎日8時、10時、12時、17時、20時、22時の自動更新
- Codex CLIによる「今日のハイライト」と、公式リリースの製品別日本語まとめ（記事構成が変わった場合のみ生成）
- 自動車・製造業におけるML基盤、データマネジメント、データガバナンスの実践事例
- 業務改善やQOL向上につながるガジェット、家電、仕事効率化ツールの分野別ハイライト
- 記事を開いた履歴をMac mini内の`data/read-events.jsonl`へ記録し、`/api/analytics`で閲覧傾向を集計
- 不要とした記事をMac mini内の`data/feedback-events.jsonl`へ記録し、同一記事の除外と類似傾向の減点に利用
- localhostへの限定バインド
- Gmailの全未読メール（迷惑メール・ゴミ箱を除く）表示、返信状況の追跡、日次・週次リマインド
- Gmailスレッドへのリンク、Gmailへ反映される既読操作、対応済み・保留・対応不要の記録
- ネイティブアプリでGmailスレッド本文をオンデマンド表示、メール対応は即時反映・完了スワイプに対応
- 「今日」画面でのタスク、期限、優先度、毎日・平日・毎週のルーティン管理
- 疲労度・気分・体調メモと、iPhoneショートカットから同期したHealthKit日次集計の表示
- iPhoneと同じAgent・今日・メール・ニュースを表示するネイティブmacOSアプリ
- Mac mini上のSoan文書を装飾・画像込みで読み、段落ブロックへのコメントからLLM改訂案を作ってローカル保存する「資料」タブ（全文入力は副編集モード）

## 今日のタスクと体調

「今日」タブでは、期限当日・期限超過・期限なしのタスクと、その日に該当する
ルーティンをまとめて確認できます。通常タスクは完了すると一覧から外れ、ルーティンは
日ごとに完了履歴を保持して翌日に再表示されます。データはMac mini内の
`data/planner.sqlite3`だけに保存され、Gitには含まれません。

体調チェックインは画面から疲労度、気分、メモを入力できます。HealthKitの日次集計は
iPhoneのショートカットから次のAPIへ送信します。

```text
POST /api/health/sync
Authorization: Bearer <専用トークン>
Content-Type: application/json
```

専用トークンは改行なしで`secrets/health-sync-token.txt`へ保存します。送信例:

```json
{
  "date": "2026-08-24",
  "sleep_minutes": 412,
  "steps": 8432,
  "resting_heart_rate": 58,
  "hrv_ms": 44.2,
  "respiratory_rate": 14.5
}
```

## Gmailアシスタント

Google Cloudでデスクトップアプリ用OAuthクライアントを作成し、JSONを
`secrets/gmail-client.json`へ配置します。権限はGmailの読み取り・ラベル変更に必要な
`gmail.modify`を使用します。

```bash
uv run --frozen daily-reader-gmail auth
uv run --frozen daily-reader-gmail sync
```

認証トークンとSQLiteデータベースはMac mini内だけに保存され、Git管理されません。
初回認証後、常駐サーバーが15分ごとにGmailを自動同期します。Daymeldの先頭に
メールタブには重要度にかかわらず全未読メールが表示され、「今日」には重要な未読メールが表示されます。アプリの「既読」はGmailのスレッドを既読にして即座に一覧から外し、
Gmailで既読にした場合も次回同期後に一覧から外れます。以前の読み取り専用権限で認証済みの場合は、
`uv run --frozen daily-reader-gmail auth`を再実行して権限を更新してください。返信したスレッドは
「返信待ち」になり、Web操作や電話で対応した項目は画面の「対応済み」で完了できます。対応済み・完了の操作は、Gmailスレッドも既読にして一覧から外します。
日次表示は直近1日、週次表示は直近7日を基本とし、期限付きと返信待ちは期間外でも残ります。

## Mac miniで起動する

[uv](https://docs.astral.sh/uv/)が必要です。

```bash
uv sync --frozen
uv run daily-reader-local
```

別のターミナルまたはLaunchAgentでAgentワーカーを起動します。

```bash
uv run --frozen daily-reader-agent-worker
```

Agentワーカーは既定で最大10件のタスクを並列実行します。端末の負荷やCodexの利用枠に
合わせて変更する場合は、`--max-workers 4`のように指定します。各タスクは専用worktree
で実行され、同じキュー項目が複数ワーカーに取得されることはありません。

Agentが操作できるリポジトリは`config/agent-repositories.toml`の明示的な許可リストに限定されます。複数の`[[repositories]]`を登録でき、`path`には設定ファイルからの相対パス、絶対パス、または`~/`から始まるホーム相対パスを指定できます。現在はDaily Reader、soan、宿直（tonoi）、configを選択できます。ワーカーはタスクごとに専用branchとworktreeを作り、最初のターンで既定モデルが実装計画を作成した後、同じCodexスレッドを`gpt-5.6-luna`（low）で再開して実装・検証・コミットを行います。構造化出力により工程を制御し、計画の品質を保ちながら実装部分の利用量を抑えます。検証済みの変更は各リポジトリの最新デフォルトブランチへrebaseしてpushし、ローカルのデフォルトブランチも同期します。その後、各リポジトリの`AGENTS.md`に従ったデプロイと実環境確認が成功してからタスクを完了し、作業環境を削除します。競合は同じCodexセッションへ戻して解決と再検証を行います。configとtonoiは自動デプロイの対象外です。

Daymeldとtanomiのタスクカードは更新時刻順の同じ一覧へ表示され、Daymeldはミント、tanomiは紫の出所バッジと左端アクセントで区別できます。どちらも既定では折り畳まれ、状態アイコン、リポジトリ、更新時刻を一覧で確認できます。Daymeldカードを開くと「現在の進捗」と「やりとり」を分けて表示し、tanomiカードは開いたときだけ依頼内容と結果（またはエラー）を全文表示します。セッションが残る完了・失敗・停止済みtanomiタスクには、同じセッションを継続する追加指示を送れます。
使用状況カードにはCodexに加えて、tanomiの5時間枠と週次枠の使用率、残量、リセット日時も表示します。
tanomiの使用状況は短時間キャッシュし、上流の一時的なレート制限時は直近の取得値を
「前回取得」として表示します。
Daymeldタスクカードには、依頼時に選択した実装モデルとEffortを表示します。これは計画ターンに使う既定モデルではなく、実装・検証ターンに使う設定です。カードを開くと、待機中・実行中・判断待ちの直近のやりとりが5秒ごとに自動更新されます。
作成したタスクの「やりとりを表示」から全履歴も確認でき、表示中は
「やりとりを非表示」または表示中のやりとりをタップして閉じられます。待機中・実行中の
タスクへ送った追加指示は、次のCodexターンで同じスレッドに渡されます。アーカイブした
タスクは7日間アーカイブ一覧から確認でき、その後に履歴、保持中の専用worktree、タスク用branchが
自動削除されます。アーカイブ後に
進捗が更新されたタスクは通常一覧へ自動で戻ります。判断待ち、
または作業環境が保持された失敗タスクへ送信すると、その環境から処理を再開します。
完了したタスクには「完了サマリー」と確認欄が残ります。サマリーについて質問すると、
Agentが現在の実装も参照しながら読み取り専用で回答し、その回答は同じ「やりとり」へ
追加されるとともにカードの最新サマリーへ反映されます。元の完了内容は次回の確認にも
引き継がれます。新たなコード変更が必要な依頼は、別タスクとして投入してください。

ブラウザで <http://127.0.0.1:8787> を開きます。起動時に記事を取得し、その後は毎日8時、10時、12時、17時、20時、22時に更新します。サーバーは`127.0.0.1`だけで待ち受けるため、LANへ直接公開されません。

SideStore配布を生成している場合だけ、`data/sidestore/`内のIPA、ソースJSON、アイコンを
専用ポート`8788`から自宅LANの`192.168.10.0/24`へ配信します。メイン画面とAPIは
このポートから配信しません。IPv4 link-localの`169.254.0.0/16`も許可します
（現環境ではMacとiPhoneのUSB直接リンク）。それ以外のネットワークからの要求は拒否します。
LAN側では`source.json`、`DailyReader.ipa`、`icon.png`の3ファイルだけを配信し、
外出先用の秘密URLを含む`remote-source.json`は配信しません。

ビルド時には、外出先更新用のバージョン付きIPAと`remote-source.json`も生成します。
初回だけ32-byteのランダムなURLトークンを`secrets/sidestore-remote-token.txt`へ`0600`で
作成し、標準出力へ秘密URLを表示しません。秘密URLを含む`remote-source.json`も`0600`で
保存します。`127.0.0.1:8789`の専用サーバーは秘密URL配下のソースJSON、アイコン、
ソースに列挙した現在版と直前版を含む最大10版のIPAだけを配信し、Tailscale Funnelの
`8443`番からのみ中継します。URLを知る人は配布物を取得できるため、URLやトークンを
共有しないでください。SideStoreは取得失敗時などにURLを端末診断ログへ記録し得るため、
SideStoreやiPhoneの診断ログも共有しないでください。

Codex CLIがログイン済みの場合は、更新時に注目記事から今日のハイライトを生成します。Snowflake、Databricks、dbt、Apache Icebergなどの公式リリースは製品別に束ね、英語の記事も日本語で要約して原文リンクを添えます。同じ候補記事の組み合わせでは再生成しません。実行にはCodexの利用量を消費しますが、`gpt-5.6-luna`を低推論設定で1回だけ呼び出し、ハイライトと公式リリースまとめを同時に生成します。Codexは`--ephemeral`、`--sandbox read-only`、構造化出力で呼び出され、記事本文中の命令を無視するよう指示されます。

### tanomi連携

Agent画面のtanomi欄は、Daymeldの`127.0.0.1:8787`を経由する同一オリジンBFFから、既定のTailscale Serve URL `https://xh23040023-l.tailc193b2.ts.net`へ接続します。ブラウザやiPhoneからtanomiへ直接接続せず、CORSも追加しません。別ホストのtanomiを使う場合は`--tanomi-base-url`でURLを変更できます。tanomiの停止中もDaymeldの既存Agent・ニュース・メールは利用できます。

tanomi本体はDaymeldに含まれず、別ホストのTailscale Serveで別途起動しておく必要があります。利用可能と判断するには、tanomiホストの`/api/health`、`/api/repos`、`/api/tasks`がJSONを返すことを確認してください。8765番を別のHTTPサーバーで代用してもBFFは利用できません。tanomiの実行ファイルと常駐設定はこのリポジトリでは管理せず、tonoi/config側で別途管理する対象です。

`bypassPermissions`、deploy、完全削除は強い実行権限または破壊的操作を伴うため、画面で明示確認を行います。8765のtanomi APIをDaymeld以外の公開ポートやSideStore Funnelへ転送しないでください。

## TailscaleでiPhoneだけに公開する

Mac miniとiPhoneを同じtailnetへ参加させ、Mac miniで次を実行します。

```bash
tailscale serve --bg --yes http://127.0.0.1:8787
tailscale serve status
```

`tailscale serve status`に表示された`https://<Mac mini名>.<tailnet名>.ts.net`をiPhoneのSafariで開きます。Serveにはtailnetのアクセス制御が適用されるため、iPhoneだけに限定する場合はTailscaleのポリシーでも対象端末またはユーザーを制限してください。

SideStoreの外出先更新だけは、メインサービスとは別のFunnelポートを使用します。

```bash
tailscale funnel --bg --yes --https=8443 http://127.0.0.1:8789
uv run --frozen python scripts/verify_sidestore_remote.py
```

`443`番のServeと`127.0.0.1:8787`は変更せず、Agent、Gmail、HealthKit、Plannerを
Funnelへ載せません。検証コマンドは秘密URLを表示せず、現行3成果物の内容一致、拒否パス、
`443`/`8443`のTailscale設定境界を確認します。外部配信を停止する場合は、共有Serve設定を
全消去する`tailscale funnel reset`を使わず、次のように有効化時と同じ引数へ`off`を付けます。

```bash
tailscale funnel --bg --yes --https=8443 http://127.0.0.1:8789 off
tailscale serve status --json
tailscale funnel status --json
```

`AllowFunnel`と8443のhandlerが消え、`443 -> 127.0.0.1:8787`が残ったことを確認します。

トークンを変更しても、SideStore 0.6.3の保存済みソースURLは自動更新されません。
漏洩時はFunnelを停止して新しいトークンで再ビルド・再起動し、実機へ新URLを再登録して
remote一覧からDaily Readerを一度インストールします。復旧まで外出先更新は停止します。

## 購読先とキーワード

購読先は[`config/feeds.toml`](config/feeds.toml)、評価キーワードは[`config/keywords.toml`](config/keywords.toml)で管理します。

```toml
[[feeds]]
name = "サイト名"
url = "https://example.com/feed.xml"
category = "カテゴリ"
priority = 6 # 公式・一次情報だけを優先する場合は1〜10
enabled = true
```

```toml
[positive]
Python = 4

[negative]
広告 = -4
```

タイトルまたは概要にキーワードが含まれると、指定した重みが記事のスコアへ加算されます。同じキーワードが複数回現れても、記事ごとに一度だけ加算します。
`priority`は情報源の品質を表し、公式リリース、自治体、一次研究などに設定します。
未指定は`0`です。フィードに公開・更新日時がない記事は取得時刻を保存しますが、候補選定では
未検証日時として減点されるため、古い記事が「たった今の新着」に見えることを防ぎます。

## 常駐化

`daily-reader-local`をmacOSのLaunchAgentから起動すると、ログイン後に常駐できます。Mac mini固有のHome Manager設定から、リポジトリ内の`.venv/bin/daily-reader-local`を作業ディレクトリ付きで起動する構成を推奨します。Tailscale Serveの設定はバックグラウンド設定としてTailscale側に保存されます。

## Sitesへの移行について

現状はSitesへ全面移行せず、Mac miniのローカルサーバーをTailscale Serveで使います。Sitesにすると、Mac miniが停止中でもニュース閲覧ができること、Tailscaleに接続していない場所からの利用、認証付き共有、クラウド上の永続化が可能になります。一方、ニュース収集・Codexによるハイライト生成の能力が増えるわけではありません。

Codex CLI、Git worktree、LaunchAgent、ローカルGmail OAuth、HealthKit同期、Planner／AgentのSQLiteはSites上ではそのまま動かせません。Sitesからtailnet内のMac APIへ接続することもできないため、全面移行には公開API、認証、クラウド実行基盤、データ移行の再設計が必要です。これは現行の「tailnet内限定」「外部サーバー・DBを使わない」という方針とも合いません。

将来、Mac停止中の閲覧や他者との共有が必要になった場合は、既存版を残したまま、ニュース一覧・ハイライトだけを独立したSites版として段階導入します。最初は匿名化した固定データで検証し、Gmail、健康、Agent履歴はSitesへ送信しません。閲覧履歴やフィードバックをクラウド保存する場合はD1移行を別タスクで設計し、Sites版と現行Tailscale版を並行稼働させてから切り替えます。

## iPhoneへの追加

1. 公開されたページをSafariで開きます。
2. 共有ボタンから「ホーム画面に追加」を選びます。
3. 「Web Appとして開く」を有効にして追加します。

「あとで読む」と既読状態はiPhoneのブラウザ内にだけ保存されます。ブラウザデータを消去した場合や別の端末には引き継がれません。
「表示したくない」はMac miniにも保存されるため、別端末での表示と次回更新時のCodexによる選定にも反映されます。少数の指定だけでカテゴリ全体を除外せず、タイトル・情報元・カテゴリに繰り返し現れる傾向を減点材料として扱います。

## ネイティブmacOSアプリ

macOS版はiPhone版と同じMac mini APIへ接続し、Agent、今日のタスク、メール、ニュース、
tanomi、Codex利用状況を共有します。iPhoneから同期済みの健康集計も表示しますが、Macには
HealthKitデータストアがないため、macOS版からHealthKitを新規同期する操作は表示しません。
Agent画面には、インストール中の版と最新のmacOS配布版を比較した更新状態を表示します。
Agent一覧では`j`/`k`、`h`/`l`、`Enter`/`Esc`、`Ctrl`+`u`/`Ctrl`+`d`、`gg`/`G`、
`zt`/`zz`/`zb`で選択・展開・移動でき、`dd`/`dj`/`dk`で選択タスクを非表示にできます。
テキスト入力欄を編集中は、これらのキーを通常の文字入力として扱います。
`Command`+`R`で、表示に使うすべてのデータをMac mini APIから再読み込みできます。
画面内容は`Command`+`=`（`Command`+`+`も可）／`Command`+`-`で80%から140%まで
拡大・縮小でき、`Command`+`0`で100%へ戻せます。選択した倍率は次回起動時も維持されます。

このMacで使うアドホック署名済みアプリとZIPは次のコマンドで生成します。

```bash
uv run --frozen python scripts/build_macos_release.py
```

成果物はGit管理外の`data/macos/Daymeld.app`と`data/macos/Daymeld-macOS.zip`です。
初回は`Daymeld.app`を開き、通知を許可してください。他のMacへ配布する場合は、この個人用
アドホック署名とは別にDeveloper ID署名とnotarizationが必要です。

Web版のニュース「既読」「あとで読む」はブラウザ内に保存されます。ネイティブ版の
「あとで読む」は端末内に保存し、記事を開いた既読イベントは`/api/read`へ送信します。
保存済み記事そのものはネイティブアプリ間で共有しません。

### ネイティブUXの確認用データ

Debugビルドでは、実データへ接続せずにネイティブ画面の状態を確認できます。Xcode Schemeの
Argumentsへ `-daymeld-fixture standard`、`empty`、`partial-failure`、`stress`、または
`in-flight`を指定してください。通常、空、部分失敗、大量・長文、処理中の5シナリオで、
Agent・今日・体調・メール・ニュースの表示と再試行を確認できます。fixtureは匿名固定データで、
Releaseビルドでは無効です。詳細は[`ios/README.md`](ios/README.md)を参照してください。

## 開発時の検証

```bash
uv run ruff check .
uv run pytest
uv run daily-reader
uv run daily-reader-local
```
