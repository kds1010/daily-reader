# iPhone・MacからのGmail再接続

メール画面または設定の「Gmailを再接続」からGoogleの認証を開始します。
ブラウザで本人がログイン・Gmail権限への同意を行い、Daymeldへ戻ると状態を確認できます。
URL・認証コードの貼り付けやMacでのCLI操作は通常不要です。
ブラウザを開いている間もTailscaleを接続してください。

## 初回設定

既存の `secrets/gmail-client.json` はデスクトップ用なので変更せず維持します。
Google Cloudの同じプロジェクトで、Google Auth Platform → Clientsから
**Web application** のクライアントを作成してください。Gmail APIを有効にし、
既存と同じ `https://www.googleapis.com/auth/gmail.modify` の同意設定を使います。
Authorized redirect URIsには次のURLを完全一致で登録します。

```
https://sk-mins-mac-mini.tailc193b2.ts.net/api/gmail-auth/callback
```

取得したWebクライアントJSONを稼働リポジトリの `secrets/gmail-web-client.json` に
本人所有・0600の通常ファイルとして配置します。シンボリックリンクは使用しません。
サーバーは開始時に読み直すので配置後の再起動は不要です。アプリの「設定を再確認」を押します。
JSON・クライアントシークレットはチャット、Git、ログへ貼り付けないでください。
別の配置を使う場合はサーバーの `--gmail-web-client-secret PATH` で指定できます。
既定は `--gmail-client-secret` と同じディレクトリの `gmail-web-client.json` です。

GoogleのURL登録条件を満たすことと、実際のCloudプロジェクトで登録できることは区別します。
未登録なら `redirect_uri_mismatch` となり、Cloud設定の修正が必要です。
この設定のためにDaymeldの443をFunnelで公開する必要はありません。
Googleからの応答は本人のブラウザによるリダイレクトなので、tailnet内だけで受信します。
公開状態がTestingの場合の7日失効はこの導線で解消しません。公開設定・個人利用の扱いは
READMEの「Gmail認証の診断と継続利用」を参照してください。

## 保存・保護

- サーバーに一度に1件、10分有効の認証要求をメモリで保持します。開始にはJSONのPOSTを
  要求し、Host・Origin・Sec-Fetch-Siteを検査します。Funnelの8443は許可しません。
- アプリに返すブラウザ開始URLは一度だけ使えます。ブラウザをGoogleへ移す前に
  Secure・HttpOnly・SameSite=Lax・`__Host-` Cookieを設定します。
- Googleへの要求はランダムstate、PKCE S256、offline access、明示同意を使います。
  戻り先は上記固定URLです。コールバックではstateと同じブラウザのCookieを照合します。
- 同意を受け取った要求は一度だけトークン交換できます。期限切れ、別ブラウザ、再送は拒否します。
  認証URL・code・state・Cookie・トークン・プロバイダー例外をアクセスログに出しません。
  応答はno-store・no-referrerで、コールバックに外部リソースを置きません。
- 有効なアクセストークン、refresh token、Gmail権限が揃った後だけ、既存のトークンロックと
  原子的0600保存を使って `secrets/gmail-token.json` を更新します。
  同意中にCLIや定期更新が既存トークンを更新した場合は上書きせず、再試行になります。
- Webクライアントで作成したトークンにはそのclient ID/secretが含まれ、既存の非対話同期は
  保存トークンを使って更新できます。デスクトップ用CLIの認証も従来通り残ります。
- 保存後にメール同期を実行し、認証待ち・結果確認中・同期中・同期完了・同期失敗を分離します。
  同期失敗時は新しい認証を保持し、既存の15分定期同期が再試行します。
- アプリの接続画面を閉じても、同じアプリ起動中は再度開いて状態確認できます。
  アプリ強制終了で待機IDは消えます。未完了の同意は10分後に開始し直せます。
  サーバー再起動では要求を失効させます。未完了のOAuthをディスクに保存しません。
- 認証対象はGmailです。Driveサービスアカウント、Soundcore認証、Codexログインは変更しません。

## 検証と運用境界

`tests/test_gmail_oauth.py` は実ライブラリの認証URLとPKCE、匿名トークン交換、
ブラウザCookie、HTTP応答、Origin/Host拒否、期限、キャンセル、二重実行、
権限不足、保存失敗、同意中の競合、同期失敗を検証します。
Googleの実アカウントの同意を自動テストで実行したり、既存認証を失効させたりしません。
Swiftの応答デコード・遷移判定と両OSビルドも確認します。

Webサーバーの再起動と両OS成果物の再生成・配信検証が必要です。
コード配信、Cloudの初回設定、実アカウントの同意とメール同期、iPhoneへの実機導入は
それぞれ区別して報告します。初回設定前はアプリに設定案内が表示されます。

公式根拠:
- [Google Web server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)
- [google-auth-oauthlib Flow](https://google-auth-oauthlib.readthedocs.io/en/latest/reference/google_auth_oauthlib.flow.html)
- [Google OOB廃止](https://developers.google.com/identity/protocols/oauth2/resources/oob-migration)
