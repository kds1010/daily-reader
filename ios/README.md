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

Mac miniで次を実行すると、未署名IPA、SideStoreソース、アイコンを
`data/sidestore/`へ生成します。Daily Readerサーバーはこのディレクトリだけを
LAN専用ポート`8788`から配信します。Agent、Gmail、健康情報を扱うメインサーバーは
従来どおり`127.0.0.1:8787`だけで待ち受けます。配布ポートも接続元を自宅LANの
`192.168.10.0/24`、IPv4 link-localの`169.254.0.0/16`（現環境ではiPhoneの
USB直接リンク）、Mac自身に制限します。

```bash
uv run --frozen python scripts/build_sidestore_release.py
```

SideStoreへ次のソースURLを一度追加すると、以降はケーブルなしでDaily Readerを
インストール・更新できます。

```text
http://sk-mins-Mac-mini.local:8788/source.json
```

IPAはSideStoreが端末上のApple Accountで署名します。無料Personal Teamでは署名の有効期間が
7日間のため、LocalDevVPNを有効にして定期更新を成功させてください。新版の取得時は
iPhoneとMac miniを同じWi-Fiへ接続し、Tailscaleを切ってLocalDevVPNを有効にします。
配布物は同じLAN内だけから取得でき、インターネットへ公開しません。初回接続時にiOSが
SideStoreのローカルネットワークアクセスを求めた場合は許可してください。

## 検証

```bash
xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DailyReader -sdk iphonesimulator -configuration Debug \
  CODE_SIGNING_ALLOWED=NO build
```
