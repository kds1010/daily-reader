# Daymeld for iPhone and Mac

DaymeldのSwiftUIネイティブクライアントです。iPhone版とmacOS版は共通の画面・モデルを使い、既存のMac mini APIへ接続します。Agent画面ではCodexに加えて、別ホストのTailscale Serveで公開されたtanomiをDaymeldの8787 BFF経由で利用できます。Daymeldとtanomiのタスクは更新時刻順の同じカード一覧へまとめ、ミント／紫で出所を区別します。使用状況にはCodexとtanomiの5時間・週次利用枠を表示します。クライアントからtanomiへ直接接続しません。セッションが残る完了・失敗・停止済みtanomiタスクには、カードから追加指示を送って同じ作業を継続できます。iPhoneのXcodeターゲットとBundle IDは更新互換性のため維持しています。tanomi本体の導入・常駐化はこのリポジトリの責務外です。

iPhone版の「今日」から現在地の一回保存、または移動の記録開始・停止を選べる。記録開始時にWhen In Use権限を要求し、Core Locationの標準位置更新（目標精度100 m、移動距離100 m）とバックグラウンド位置更新・表示インジケーターを有効にする。固定間隔での取得は保証しない。Always権限は要求せず、強制終了・再起動後の記録はユーザーが再開する。取得した座標・精度・取得時刻・概算位置フラグは端末のApplication Support内の保護された未同期キューへ先に保存し、最大500件ずつMac miniの`POST /api/locations/sync`へ同期する。失敗時は保持し、位置更新時・前景更新時・手動操作で再試行する。履歴は`data/conversations.sqlite3`の`location_events`へ保存する。iPhone・macOSの「今日」→「GPSの取得履歴・マップ」で端末のローカル日付を選択し、`GET /api/locations?start=...&end=...`から500件ずつ取得して、時刻順一覧、MapKitの地点、取得時刻と水平精度を表示する。未同期データはiPhoneのGPSカードに件数と直近20件を表示し、地図へは同期後に反映する。地図表示にはAppleの地図サービスを使用する。GPS履歴はCodexへ送らない。録音との位置照合は確認済み録音日時がある場合のみ行い、取り込み時刻で代用しない。

GPSカードは記録の稼働状態、最終取得日時と経過時間、最終同期成功日時、取得失敗、未同期件数を区別します。取得時刻が古いだけでは、静止・記録終了・取得失敗のどれかは断定しません。記録中も「現在地を再取得して保存」を押せます。一回取得専用のCLLocationManagerを使い、継続記録のマネージャーは停止しません。100 mの移動基準とWhen In Use権限は維持します。最終取得・同期成功の時刻だけを保護された`location-diagnostics.json`へ保存し、起動時に復元します。診断ファイルには座標を保存せず、記録自体は再開しません。一回取得と継続記録の分離は[AppleのrequestLocation仕様](https://developer.apple.com/documentation/corelocation/cllocationmanager/requestlocation())と[複数マネージャーの利用仕様](https://developer.apple.com/documentation/corelocation/cllocationmanager)に基づきます。旧未同期キューは引き続き読み込み・再送できます。同期エラーには接続・応答・保存処理などの安全な分類だけを表示します。

Soundcore WorkなどがMP3ファイルを共有・エクスポートするときは、共有先にDaymeldを
選択できます。Daymeldが開いて「会話」タブへ移動し、既存のファイル選択と同じ経路で
Mac miniへ原音を送信します。送信成功後はDaymeldの受信Inbox内に作られたコピーだけを
削除し、共有元のファイルを直接開いたり変更したりしません。MP3以外の音声形式には
対応していません。

文字起こし完了後は録音の詳細から、タスク、フォローアップ、決定事項、アイデア、困りごとの
Codex整理を開始できます。開始前にCodexへ渡す範囲を確認し、MP3原音、GPS、ファイル名、
ほかの録音は送信しません。抽出された候補は「音声インボックス」で根拠発言とともに確認し、
タイトル、詳細、担当者、期限を編集できます。「通常タスクに追加」または「Agentへ依頼」を
押すまで外部の作業は開始されず、実行しない候補は保存または破棄できます。Mac miniのCodex
CLIがChatGPTアカウントへログインしていない場合、Codex整理ボタンは利用できません。OpenAI
APIキーは不要です。

メールは対応操作を画面へ即時反映し、メール行を右へフルスワイプして「完了」にできます。スワイプ完了時はチェック演出、フェード・縮小、触覚フィードバックを行い、通信に失敗した場合は元の位置へアニメーション付きで復元します。メールカードの概要をタップすると、Gmailスレッドの本文を必要時だけMac mini API経由で取得し、外部Webを開かずアプリ内で表示します。既存の「既読」操作だけがGmailの未読ラベルを変更し、「完了」はDaymeld内の対応状態を更新します。

## 実機で試す

1. `ios/DailyReader/DailyReader.xcodeproj`をXcodeで開く。
2. TargetのSigning & Capabilitiesで自分のPersonal Teamを選ぶ。
3. Bundle Identifierが重複する場合は、自分専用の値へ変更する。
4. iPhoneを接続し、Developer Modeを有効にして実行する。
5. 初回起動時に通知とHealthKitの読み取りを許可する。GPSは「今日」で一回保存または記録開始を選んだ時だけ、位置情報の許可を確認する。
6. 設定画面へHealthKit同期トークンを入力する。

Agentが完了・判断待ち・失敗へ遷移すると、iPhoneのローカル通知を表示します。初回の
Agent一覧取得は基準作成のみで通知せず、アプリを閉じている間の遷移は次回の一覧更新時に
一度だけ通知します。これはAPNsではないため、アプリが強制終了された状態での即時通知は
保証しません。通知を拒否した場合はiOSの設定からDaymeldの通知を有効にしてください。
Agent画面では、インストール中の版をSideStoreで実際に配信中の最新版と比較し、
一致時は「アプリ最新版」、差がある場合は「SideStore更新あり」と両方の版を表示します。

開発用実機へCodexから初期設定する場合は、`health-sync-token.txt`をアプリの
Documents領域へ転送して再起動します。アプリはトークンをKeychainへ保存できた場合だけ、
平文の転送ファイルを直ちに削除します。トークンをリポジトリやアプリ本体へ含めないでください。
未設定時は、CoreDeviceが上書きできる空の受け口ファイルをアプリが作成します。

無料Personal TeamでHealthKitのプロビジョニングに失敗する場合は、まず
`DailyReader.entitlements`から`com.apple.developer.healthkit.background-delivery`だけを外し、
前景同期で実機検証してください。

## Macで使う

KeyHintsは専用のSwiftPM/Nixリポジトリ（`kds1010/keyhints`）で管理する独立したメニューバー常駐ユーティリティです。導入とログイン時自動起動はmac-miniのHome Manager設定が管理します。

`DaymeldMac`は専用のネイティブmacOSターゲットです。Agent、今日、メール、ニュース、
tanomiなどはiPhoneと同じMac mini上の状態を表示します。健康情報はiPhoneからサーバーへ
同期済みの集計を表示できますが、MacにはHealthKitデータストアがないため、Mac側には
HealthKit同期ボタンやトークン入力を表示しません。
Agent画面では、インストール中の版を最新のmacOS配布版と比較して更新状態を表示します。
タスク一覧はVim式の`j`/`k`、`h`/`l`、`Enter`/`Esc`、`Ctrl`+`u`/`Ctrl`+`d`、
`gg`/`G`、`zt`/`zz`/`zb`で操作できます。`dd`/`dj`は非表示後に次へ、`dk`は前へ移り、
テキスト入力中はショートカットを無効化します。

画面内容は「表示」メニューまたは`Command`+`=`（`Command`+`+`も可）／`Command`+`-`で
80%から140%まで10%刻みで拡大・縮小できます。選択した倍率は次回起動時も維持され、
`Command`+`0`で100%へ戻ります。
`Command`+`R`を押すと、表示に使うすべてのデータをMac mini APIから再読み込みします。

Xcodeでは`DaymeldMac` schemeと`My Mac`を選択して実行できます。このMac用のアドホック
署名済み成果物を生成する場合は、リポジトリ直下で次を実行します。

```bash
uv run --frozen python scripts/build_macos_release.py
```

`data/macos/Daymeld.app`と`data/macos/Daymeld-macOS.zip`が生成されます。アプリはApp Sandboxを
有効にし、Mac mini APIへ接続するための外向きネットワーク通信を許可します。HealthKit
entitlementは含みません。
この成果物は同じMacでの個人利用向けです。他のMacへ配布する場合はDeveloper ID署名と
notarizationを別途行ってください。

## SideStoreで更新する

自動デプロイの完了範囲は、検証済みIPAとソースを生成し、LANおよび外出先用Funnelから
正しい成果物を取得できることをMac側で確認するところまでです。SideStoreによる再署名と
iPhoneへのインストール、権限付与、画面・操作確認は配信後の独立した実機工程です。
iPhoneが未接続でも配信成功は失敗扱いにしません。

Mac miniで次を実行すると、HealthKit entitlementを保持したアドホック署名済みseed IPA、
LAN用と外出先用のSideStoreソース、アイコンを
`data/sidestore/`へ生成します。Daily Readerサーバーはこのディレクトリだけを
LAN専用ポート`8788`から配信します。Agent、Gmail、健康情報を扱うメインサーバーは
従来どおり`127.0.0.1:8787`だけで待ち受けます。配布ポートも接続元を自宅LANの
`192.168.10.0/24`、IPv4 link-localの`169.254.0.0/16`（現環境ではiPhoneの
USB直接リンク）、Mac自身に制限します。

```bash
uv run --frozen python scripts/build_sidestore_release.py
```

SideStoreへ次のソースURLを一度追加すると、以降はケーブルなしでDaily Readerを
自宅LANからインストール・更新できます。

```text
http://sk-mins-Mac-mini.local:8788/source.json
```

外出先でも更新する場合は、`127.0.0.1:8789`の専用配信サーバーをTailscale Funnelの
`8443`番へ中継します。初回ビルド時に作成する32-byteランダムトークン付きURLが
アクセス資格情報です。外部から到達できますが、配信対象はソースJSON、アイコン、
ソースに列挙した最新版1件のIPAだけです。旧版はMac内の`release-history.json`と
最大10版のIPAとして保持し、この履歴ファイルは配信しません。
秘密URLを会話、Issue、ログへ貼り付けたり、他者と共有したりしないでください。Codexから
実機へ登録する場合は、URLを表示しない次のスクリプトを使います。

```bash
uv run --frozen python scripts/open_sidestore_remote_source.py \
  --device '<iPhone名またはUDID>' \
  --bundle-id '<実機上のSideStore bundle identifier>'
```

既にLAN sourceからDaily Readerをインストールしている場合、remote sourceを追加しただけでは
Installed Appの更新元は切り替わりません。remote sourceの一覧からDaily Readerを一度
インストールし、更新成功後にLAN sourceを削除してください。以後はremote sourceが更新元に
なります。

seed IPAのアドホック署名はHealthKit entitlementをSideStoreへ引き渡すためのもので、
インストール時にはSideStoreが端末上のApple Accountで再署名します。無料Personal Teamでは署名の有効期間が
7日間のため、LocalDevVPNを有効にして定期更新を成功させてください。新版の取得時は、
自宅でも外出先でもiPhoneのTailscaleを切り、LocalDevVPNを有効にします。外出先用ソースは
通常のWi-Fiまたはモバイル回線から取得できます。更新後はLocalDevVPNを切り、Daily Readerの
通常利用に必要なTailscaleを再び有効にします。7日署名のRefreshにはLocalDevVPNと
インターネット接続が必要ですが、IPAを再取得しないため通常はMac miniへの接続を必要としません。
LANソースを使う場合、初回接続時にiOSがSideStoreのローカルネットワークアクセスを求めたら
許可してください。

### 更新版が上がらない場合の切り分け

更新はLocalDevVPNを接続し、SideStoreの`Sources → Daymeld Remote → UPDATE`から行います。
`Refresh`は既存IPAの7日署名を更新する操作です。完了後はDaymeldのAgent画面にある
「インストール済み」の版を配信版と比較してください。7 DAYSだけでは新版の導入成功と判定しません。
Daymeldに更新ありと出ているのにSource側がOPENのままなら、LocalDevVPN接続後に
SideStoreだけをアプリ切替画面で終了・再起動し、ソースを読み直します。
更新中はSideStoreを前面に保ち、完了前にほかのアプリへ切り替えないでください。
`Swift.CancellationError`が出た場合はSourceへ戻り、UPDATEを一度だけ押して再試行します。
更新対象のDaymeldでGPSを停止した場合は、更新後にTailscaleへ戻して記録を再開します。
アプリ削除・Deactivate・データベース初期化は不要です。

2026-09-07の実機では、複数版ソースの詳細に0.1.194が表示されていても、SourceのUPDATEが
0.1.186をインストールしました。配布IPAの内部版とファイル名は一致していました。
SideStore 0.6.3（4deda922）は詳細表示に`versions[0]`、Sourceからの更新には別の
`latestSupportedVersion`関係を使います。マージ後の関係補正が版順の変更時に限られ、
表示版と更新先が食い違う経路があります。公開ソースは最新版1件に限定し、旧版の候補を残しません。
`verify_sidestore_remote.py`も複数版の再公開を失敗として検出します。
修正後、同じiPhone・LocalDevVPN・Daymeld Remoteから0.1.195へ更新し、
Daymeld自身の「インストール済み 0.1.195 (195)」を2026-09-07 17:36 JSTに確認しました。
続く0.1.198への更新も18:19 JSTに確認し、GPSとHealthKitのMacへの実同期に成功しました。
0.1.202は18:41 JSTに実機導入を確認しました。Tailscaleへ戻した後、GPS同期はHTTP 200、
予定・移動情報は旧キューの回復と新規取得の両方がHTTP 200、HealthKitはHTTP 202でした。
18:42 JSTにGPSのバックグラウンド記録を再開しています。

また、このSideStoreでは`My Apps`の検索がCore Dataの保存済み`hasUpdate`列を使う一方、
SourceのボタンはSwiftの計算プロパティを使います。保存列は既定のNOから更新されておらず、
`My Apps`の「No Updates Available」は更新なしの根拠になりません。Source側から更新してください。
匿名のSQLite/Core Data再現でも、Swift側の更新判定trueに対しSQL検索0件となりました。
この上流UIの不整合と、VPN・IPA配信の成否は区別します。HealthKit対応の自己ビルド版は維持します。

実装の根拠はSideStore 4deda922の`AltStoreCore/Model/InstalledApp.swift`、
`Model/MergePolicies/MergePolicy.swift`、`Model/StoreApp.swift`、
`AltStore/Sources/SourceDetailContentViewController.swift`、`Managing Apps/AppManager.swift`です。

### 配信エラーの診断

Mac miniのサービス用リポジトリで`uv run --frozen python scripts/verify_sidestore_remote.py`を
実行すると、秘密URLを表示せず配布物と拒否パスを確認できます。Python 3.12以上が必要です。
`--request-origin http://127.0.0.1:8789 --skip-tailscale-config`を付けると、同じ配布物を
Funnel経由ではなく転送先へ直接要求し、障害の層を切り分けられます。

| 診断 | 次に確認すること |
| --- | --- |
| `connection failed` / `timed out` | DNS、接続経路、待受PIDと応答。証明書エラーとは断定しません。 |
| `TLS certificate verification failed` / `TLS connection failed` | HTTPS接続の証明書検証またはTLS接続。証明書検証を無効化しないでください。 |
| 成果物の`HTTP 404` | 登録ソース、トークン設定、公開中の版。旧版URLは配信対象外です。 |
| `HTTP 502 gateway failure` | Funnel転送先の`127.0.0.1:8789`とサービス実体。HTTPSでHTTP応答が返っている場合、TLS不成立と混同しません。 |
| `HTTP 200 but content did not match` | 配信先とローカル成果物の相違、または検証中の別リリース公開。 |
| `incomplete HTTP response` | 転送途中の切断。配信ログと通信経路を確認します。 |
| `rejection check: expected HTTP 404` | 拒否すべきパスの応答。502なら転送障害であり、情報公開の証拠ではありません。 |

検証コマンド自身も意図的な404をログへ残します。ユーザー操作の失敗と照合する際は、
実行時刻、ソース取得、要求IPA版、HTTP結果を区別してください。ソース取得200だけでは、
IPA取得や端末での再署名・インストール成功を意味しません。取得要求がない場合も、
キャッシュ・VPN・再署名のどれが原因かをサーバーログだけで断定できません。
診断は例外の固定分類だけを表示し、通信例外のURL・理由・本文・原因チェーンを出しません。
[PythonのURLError仕様](https://docs.python.org/3.12/library/urllib.error.html)では原因が
文字列または例外になり得るため、どちらの場合も秘密値を含む原文は表示しません。

### HealthKit対応SideStore

公式SideStore 0.6.3のAltSignは、元IPAにHealthKit entitlementがあってもApple Developer
PortalのHealthKit機能へ対応付けないため、再署名後のDaily ReaderからHealthKit entitlementが
失われます。Daily Readerのseed IPAだけを正しく署名しても解決しません。実機では
`Missing com.apple.developer.healthkit entitlement`として再現します。

この端末では、SideStore 0.6.3（commit `4deda922`）のAltSign submoduleへ
[`patches/sidestore-0.6.3-healthkit.patch`](patches/sidestore-0.6.3-healthkit.patch)を適用した
自己ビルド版を使用します。このパッチは`com.apple.developer.healthkit`とApple Developer
Portal feature ID `HK421J6T7P`を双方向に対応付けます。公式SideStoreへ更新するとパッチが
失われるため、HealthKit対応を取り込んだことを実装で確認するまで公式版へ置き換えないでください。

再ビルド時はSideStore 0.6.3をrecursive submodule付きで取得し、AltSign submodule内で
パッチを適用します。SideStoreの古いビルドスクリプトが`em_proxy`の`latest` releaseから
互換性のない成果物を取得する場合は、release tag `build`のiOS device/simulator静的ライブラリ、
ヘッダー、Swift bridgeを使用します。IPA作成前に全`._*`と`.DS_Store`を除外してください。
AppleDoubleファイルが残るとiOSが`._AltWidgetExtension.appex`をapp extensionとして解釈し、
インストールが失敗します。

配布前に、SideStore.appの`AltStoreCore.framework/AltStoreCore`へ`HK421J6T7P`が含まれること、
`codesign --verify --deep --strict`が成功すること、IPAと内包`AltBackup.ipa`に`._*`がないことを
確認します。USB接続したiPhoneへiLoaderの`Import IPA`で上書きした後、更新済みSideStoreで
Daily Readerを再インストールし、iOSのHealthKit許可画面と実際の同期成功まで確認します。
これはHealthKit対応SideStore自体を新規作成・更新した場合の手動適格性確認であり、通常の
Daily Reader IPA配信を自動デプロイする際の完了条件には含めません。

SideStore 0.6.3は取得失敗時やIPAダウンロード時に秘密URLを端末ログへ記録し得ます。
SideStoreまたはiPhoneの診断ログを共有した場合はトークン漏洩として扱います。トークンを
変更しただけでは保存済みsource URLは更新されないため、Funnelを停止し、新トークンで
再ビルド・サーバー再起動後、新しいremote sourceを再登録して一度インストールし、
旧sourceを削除してください。復旧までは外出先更新を利用できません。

## 検証

### UX fixture とプレビュー

fixture確認用のビルドでは `SWIFT_ACTIVE_COMPILATION_CONDITIONS=DEBUG` を明示してください。
これは[AppleのActive Compilation Conditions](https://developer.apple.com/documentation/xcode/build-settings-reference)で、
構成名がDebugだけでは、このプロジェクトの`#if DEBUG`は有効になりません。
起動引数 `-daymeld-fixture` に次のシナリオを指定して、実データへ接続せずに
画面状態を再現できます。

```text
standard         通常状態（Agent全状態、今日、体調、メール、ニュース）
empty            空状態とtanomi停止
partial-failure  一部API失敗と、前回データを残した再試行
stress           大量カード、長い本文、狭い画面での折返し
in-flight        Agent・tanomiが実行中、操作中表示
```

XcodeのSchemeのArgumentsへ `-daymeld-fixture standard` を追加するか、Previewの
`DaymeldRootPreview`を使って確認してください。fixtureは匿名の固定データだけで構成され、
Releaseビルドでは起動引数から選択できません。fixtureからのタスク・メール・記事操作は
ローカルの画面状態だけを変更し、Mac miniのDBやGmailへ書き込みません。

```bash
xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DailyReader -sdk iphonesimulator -configuration Debug \
  SWIFT_ACTIVE_COMPILATION_CONDITIONS=DEBUG \
  CODE_SIGNING_ALLOWED=NO build

xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DaymeldMac -sdk macosx -configuration Debug \
  -derivedDataPath /tmp/daily-reader-macos-derived \
  SWIFT_ACTIVE_COMPILATION_CONDITIONS=DEBUG \
  CODE_SIGNING_ALLOWED=NO build
```

### 応答性の回帰確認

Agentの初期表示はメール等の取得を待たず、tanomi一覧も補助情報の取得から独立しています。
前景のDaymeld一覧とtanomi一覧は別々に取得完了後5秒で更新するため、一方の接続不調は
他方の通知検出周期を延ばしません。repos/config/usageは手動更新時と約60秒ごとに取得します。
カレンダーの同期検索、端末情報のファイル処理、GPSキューの保存は画面処理から分離しています。

匿名の遅延・失敗応答を使い、実際のAppModelとURLSessionで初期表示、同値更新、重複取得、
非表示操作中の古い応答、キャンセル復帰を検証できます。実サーバー・Gmail・通知へは書き込みません。
画面を持たないこのテストではアニメーション・OS通知・生活データ取得を置き換え、更新処理と
Combine通知を実行します。描画の検証とは分けて扱ってください。

```bash
uv run --frozen pytest -q -s tests/test_ios_performance.py tests/test_ios_location_runtime.py
```

EventKit検索が同期処理である点はApple SDKの`EKEventStore.h`に記載されています。
[EKEventStoreの仕様](https://developer.apple.com/documentation/eventkit/ekeventstore)に従い、
検索・変換・保存で同じstoreを使用し、別storeのEKEventを混在させません。
位置情報の端末全体の有効状態も、権限拒否時に限って専用actorから確認します。
[Apple DTSの説明](https://developer.apple.com/forums/thread/732108)では、この同期確認が
メインスレッドの応答性を損なう可能性が示されています。
このテストはiPhone実機の停止時間や描画フレームを測定するものではありません。

2026-09-08の比較では、メールとtanomiのrepos応答をそれぞれ2秒遅らせ、
修正前後のAppModelを同じ匿名応答で各1回実行しました。

| 確認項目 | 修正前 | 修正後 |
| --- | ---: | ---: |
| Agentが最初に届くまで | 2.62秒 | 0.31秒 |
| Agent定期更新1回のHTTP要求数 | 6 | 2 |
| 同一応答でのAppModel更新通知 | 2 | 0 |

直列取得によるAgent表示待ちと、同一応答での不要な更新通知を再現・改善できました。
取得の一本化、古い応答の破棄、非表示・メール完了との競合、キャンセル後の再開も成功しています。
同期EventKit・ファイル処理はコード上のメインスレッド停止要因として分離しましたが、
実機で観測された個々のフリーズとの対応は未確定です。検証ホストの負荷が高かったため秒数は
この条件での参考値であり、iPhone実機の停止時間、描画フレーム、メモリ改善量は未測定です。

同日の回帰検証は既存397件、AppModel実行テスト1件、GPS実行テスト1件が成功しています。
署名なしのiPhone SDK・macOS・Simulatorビルドも成功しました。Simulatorのstress fixtureは
起動後約1分でも動作し、3秒間のスタック採取ではメインスレッドがイベント待機状態でした。
その時点のRSSは約77 MiBですが、操作中や修正前との比較値ではありません。
ruff、JavaScript構文、JSON、diffチェックも成功しました。SimulatorのGUI操作はComputer Useの
許可対象外だったため、タブ切替・入力・スクロールの操作確認は未実施です。
このタスクではコミットまでを行い、統合・配布成果物の再生成と配信は外部supervisorが担当します。
