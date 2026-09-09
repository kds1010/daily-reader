# 音声取り込みの認証通知

SoundcoreからGoogle Drive、DriveからDaymeldへ音声を取り込む途中で本人の認証や接続確認が
必要になった場合、Daymeldに対応内容を残し、iPhone・Macのローカル通知へ渡します。
原音、録音名、メールアドレス、トークン、秘密鍵は通知へ含めません。

## いつ通知されるか

- Soundcoreの接続監視がログイン画面、Appleの本人確認、Drive連携の再認証要求を確認した場合。
- MacのDrive取得処理が、認証情報・鍵・共有権限に関する接続失敗を確認した場合。
- 同じ障害が続く間は同じ通知IDを使い、各端末で一度だけ通知します。接続が正常に戻った後の
  再発は新しい通知IDになり、再度通知します。

通信失敗、取得件数が変わらないこと、新しい録音がないことだけでは認証切れと判断しません。
Soundcoreの一時的な確認失敗では、未解決の認証通知を消しません。
初めてアプリが取得した時点で認証が必要なら、その通知も対象です。

通知が未許可、通知登録に失敗、更新が中断された場合は通知済みにせず、次回の正常な更新で
再試行します。通知をタップするとDaymeldの対応内容を開けます。本人確認は通知本文へ
パスワードやコードを入力する方式ではなく、表示された案内に従って公式の認証画面で行います。

## iPhoneに届く条件と限界

Daymeldの通知許可、Mac miniへの接続、最新版アプリの起動が必要です。iPhoneからMacへ
接続するには既存のTailscale接続を使います。アプリ内の認証通知はOS通知の許可と独立して表示します。

この実装はAPNsのリモートプッシュではありません。アプリが前景で更新した時、またはiOSが
バックグラウンド更新を実行した時に、取得した認証状態をローカル通知へ登録します。
前景では30秒ごとに確認し、会話タブの「接続と通知」から手動でも確認できます。
バックグラウンド更新の15分指定は最短の希望時刻であり、15分ごとの到着を保証しません。
強制終了、通信不能、Mac停止中などは即時通知されません。閉じている間にも即時に知らせる
別の通知経路は、この実装には含めません。

Soundcoreの接続監視は既存のCodexタスクが通常15分ごとに実行します。MacとCodexアプリの
稼働が必要です。Drive取得はDaymeldサーバー内のworkerが独立して実行します。
Soundcoreの画面が表示できること、Drive連携の再認証が不要なこと、原音の保存完了は別々に判断します。

## 保存とAPI

現在の障害は`data/connection-alerts.sqlite3`へ0600で保存します。
`provider`は`soundcore`と`google_drive`だけです。通知文は固定の文面から作り、
取得したページの自由文、URL、認証情報を保存・配信しません。

`GET /api/connection-alerts`は次の形で現在有効な通知だけを返します。

```json
{"alerts": [{"id": "障害ごとのID", "provider": "soundcore", "title": "固定タイトル", "message": "固定の対応案内", "occurred_at": "発生日時"}]}
```

既存の`GET /api/agent-notifications`にも`connection_alerts`配列を追加します。
既存のAgentタスク通知とは独立した状態管理を使い、認証通知のために架空のAgentタスクを作りません。
接続状態だけが取得できない場合は、この配列を`null`としてAgentタスク通知を継続します。
単独の接続状態APIは503を返します。どちらの場合も、アプリは最後に取得した未解決の通知を保持します。

SoundcoreのUI監視からの報告は、Mac自身のloopback接続だけに許可した
`POST /api/connection-health/soundcore`を使います。既存のHost・Origin制限も適用します。

| 確認できた状態 | 送る内容 |
| --- | --- |
| 録音一覧が表示され、認証要求も解消した | `{"state":"connected"}` |
| ログインが必要 | `{"state":"authentication_required","reason":"sign_in_required"}` |
| Appleの本人確認が必要 | `{"state":"authentication_required","reason":"verification_required"}` |
| Soundcore内のDrive連携が再認証を要求 | `{"state":"authentication_required","reason":"drive_authorization_required"}` |
| 一時的な通信・画面取得失敗 | `{"state":"unavailable"}` |

認証状態を報告するためにSoundcoreをログアウトさせたり、Drive連携を解除したりしません。
Drive側の認証状態はMacの取得workerが記録し、このSoundcore報告APIでは変更しません。

## 検証と配信

匿名のサーバー・Swiftテストで、初回、同じ障害の継続、復旧後の再発、通知登録失敗、
古い応答、後方互換デコードと接続元制限を確認します。実アカウントを意図的に失効させたり、
本番へ架空の認証エラーを登録したりして通知テストは行いません。

共有Swiftとサーバーを変更するため、main統合後はWeb LaunchAgentを再起動し、
両OSの配布成果物を生成・検証します。iPhoneへのSideStoreインストールと実機通知許可は
配信後の独立した工程です。通知のOS受付と、ユーザーが端末で読んだことを区別します。

公式根拠:

- [Apple: バックグラウンド更新の開始時刻](https://developer.apple.com/documentation/backgroundtasks/bgtaskrequest/earliestbegindate)
- [Apple: APNsへのアプリ登録](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns)
- [OpenAI: ローカルの定期実行](https://learn.chatgpt.com/docs/automations?surface=app)
