# Daily Reader for iPhone

Daily ReaderのSwiftUIネイティブクライアントです。既存のMac mini APIへTailscale経由で接続します。

## 実機で試す

1. `ios/DailyReader/DailyReader.xcodeproj`をXcodeで開く。
2. TargetのSigning & Capabilitiesで自分のPersonal Teamを選ぶ。
3. Bundle Identifierが重複する場合は、自分専用の値へ変更する。
4. iPhoneを接続し、Developer Modeを有効にして実行する。
5. 初回起動時に通知とHealthKitの読み取りを許可する。
6. 設定画面へHealthKit同期トークンを入力する。

開発用実機へCodexから初期設定する場合は、`health-sync-token.txt`をアプリの
Documents領域へ転送して再起動します。アプリはトークンをKeychainへ保存できた場合だけ、
平文の転送ファイルを直ちに削除します。トークンをリポジトリやアプリ本体へ含めないでください。
未設定時は、CoreDeviceが上書きできる空の受け口ファイルをアプリが作成します。

無料Personal TeamでHealthKitのプロビジョニングに失敗する場合は、まず
`DailyReader.entitlements`から`com.apple.developer.healthkit.background-delivery`だけを外し、
前景同期で実機検証してください。

## SideStoreで更新する

Mac miniで次を実行すると、未署名IPA、LAN用と外出先用のSideStoreソース、アイコンを
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

IPAはSideStoreが端末上のApple Accountで署名します。無料Personal Teamでは署名の有効期間が
7日間のため、LocalDevVPNを有効にして定期更新を成功させてください。新版の取得時は、
自宅でも外出先でもiPhoneのTailscaleを切り、LocalDevVPNを有効にします。外出先用ソースは
通常のWi-Fiまたはモバイル回線から取得できます。更新後はLocalDevVPNを切り、Daily Readerの
通常利用に必要なTailscaleを再び有効にします。7日署名のRefreshにはLocalDevVPNと
インターネット接続が必要ですが、IPAを再取得しないため通常はMac miniへの接続を必要としません。
LANソースを使う場合、初回接続時にiOSがSideStoreのローカルネットワークアクセスを求めたら
許可してください。

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
```
