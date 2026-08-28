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
`site/sidestore/`へ生成します。ローカルHTTPサーバーとTailscale Serveがそのまま配信します。

```bash
uv run --frozen python scripts/build_sidestore_release.py
```

SideStoreへ次のソースURLを一度追加すると、以降はケーブルなしでDaily Readerを
インストール・更新できます。

```text
https://sk-mins-mac-mini.tailc193b2.ts.net/sidestore/source.json
```

IPAはSideStoreが端末上のApple Accountで署名します。無料Personal Teamでは署名の有効期間が
7日間のため、SideStoreのVPNを有効にして定期更新を成功させてください。配布URLはtailnet内限定です。

## 検証

```bash
xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DailyReader -sdk iphonesimulator -configuration Debug \
  CODE_SIGNING_ALLOWED=NO build
```
