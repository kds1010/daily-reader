# Daymeld for iPhone and Mac

DaymeldのSwiftUIネイティブクライアントです。iPhone版とmacOS版は共通の画面・モデルを使い、既存のMac mini APIへ接続します。Agent画面ではCodexに加えて、別ホストのTailscale Serveで公開されたtanomiをDaymeldの8787 BFF経由で利用できます。Daymeldとtanomiのタスクは更新時刻順の同じカード一覧へまとめ、ミント／紫で出所を区別します。使用状況にはCodexとtanomiの5時間・週次利用枠を表示します。クライアントからtanomiへ直接接続しません。iPhoneのXcodeターゲットとBundle IDは更新互換性のため維持しています。tanomi本体の導入・常駐化はこのリポジトリの責務外です。

メールは対応操作を画面へ即時反映し、メール行を右へフルスワイプして「完了」にできます。スワイプ完了時はチェック演出、フェード・縮小、触覚フィードバックを行い、通信に失敗した場合は元の位置へアニメーション付きで復元します。メールカードの概要をタップすると、Gmailスレッドの本文を必要時だけMac mini API経由で取得し、外部Webを開かずアプリ内で表示します。既存の「既読」操作だけがGmailの未読ラベルを変更し、「完了」はDaymeld内の対応状態を更新します。

## 実機で試す

1. `ios/DailyReader/DailyReader.xcodeproj`をXcodeで開く。
2. TargetのSigning & Capabilitiesで自分のPersonal Teamを選ぶ。
3. Bundle Identifierが重複する場合は、自分専用の値へ変更する。
4. iPhoneを接続し、Developer Modeを有効にして実行する。
5. 初回起動時に通知とHealthKitの読み取りを許可する。
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
ソースに列挙した最大10版のIPAだけです。
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

```bash
xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DailyReader -sdk iphonesimulator -configuration Debug \
  CODE_SIGNING_ALLOWED=NO build

xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DaymeldMac -sdk macosx -configuration Debug \
  -derivedDataPath /tmp/daily-reader-macos-derived \
  CODE_SIGNING_ALLOWED=NO build
```
