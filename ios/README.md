# Daily Reader for iPhone

Daily ReaderのSwiftUIネイティブクライアントです。既存のMac mini APIへTailscale経由で接続します。

## 実機で試す

1. `ios/DailyReader/DailyReader.xcodeproj`をXcodeで開く。
2. TargetのSigning & Capabilitiesで自分のPersonal Teamを選ぶ。
3. Bundle Identifierが重複する場合は、自分専用の値へ変更する。
4. iPhoneを接続し、Developer Modeを有効にして実行する。
5. 初回起動時に通知とHealthKitの読み取りを許可する。
6. 設定画面へHealthKit同期トークンを入力する。

無料Personal TeamでHealthKitのプロビジョニングに失敗する場合は、まず
`DailyReader.entitlements`から`com.apple.developer.healthkit.background-delivery`だけを外し、
前景同期で実機検証してください。

## 検証

```bash
xcodebuild -project ios/DailyReader/DailyReader.xcodeproj \
  -scheme DailyReader -sdk iphonesimulator -configuration Debug \
  CODE_SIGNING_ALLOWED=NO build
```
