# SoundcoreのGoogle Drive同期から取り込む

SoundcoreがGoogle Driveへ保存したOGG/MP3原音をMac miniへ取得し、Daymeldの会話解析へ渡します。
共有リンクの入力や、iPhoneで毎回ファイルを選択する操作を省くための経路です。

## 処理の流れ

1. 録音機からiPhoneのSoundcoreへ転送し、Soundcore自身のクラウド同期を行います。
2. Soundcore Online HubのGoogle Drive連携が、音声をDriveのSoundCoreフォルダーへ保存します。
3. Macの取得処理が設定したフォルダー配下を確認し、DriveファイルIDで取り込み済みか照合します。
4. 未保存の原音だけを取得し、容量とチェックサムを検証してからDaymeldへ登録・送信します。
5. Macの永続キューが音声解析を受け付け、既存の文字起こし・GPS照合・Codex整理へ進めます。
6. タスク・調査・予定・関心などの候補は、既存の自動整理設定と根拠の確認条件に従います。

| 工程 | 成功の判断 | 失敗した場合 |
| --- | --- | --- |
| Soundcore → Drive | フォルダーだけでなく音声ファイルが存在する | Soundcoreの接続・同期状態を確認 |
| Drive → Mac | サイズ・チェックサムが一致した原音を保存する | 原本を保持し、次回取得で再試行 |
| Daymeldへの取り込み | 原音保存と解析受付が完了する | 永続状態から再開 |
| 文字起こし・整理 | 録音側の解析状態が完了する | 会話の詳細で失敗理由を確認 |

取り込みの`completed`は原音保存と解析受付までを指します。文字起こし、Codex整理、
Calendarへの反映の完了を意味しません。Driveから消えたファイルを理由にMacの録音を削除しません。

## 録音日時と取得元

SoundcoreがDriveへ書く音声は`録音.ogg`のような共通名です。直上の録音フォルダー名が
`2026-09-09 17:21:48`のような秒までの日時である場合だけ、日本時間として解釈します。
出典は`soundcore_drive_folder_name`です。Driveへの保存日時やファイル更新日時を録音日時に
代用しません。日時形式でない名前、不正な日時、日付だけの名前は日時不明として保持します。
別の有効な日時へ改名された場合は、その名前の日時として扱うため確認が必要です。
日本以外で録音した場合は、日本時間という前提と異なるため日時の確認が必要です。

DriveファイルID、親フォルダーIDと名前、元ファイル名、サイズ、更新時刻、取得できた版と
チェックサムを取得元情報として保存します。アプリの録音名は親フォルダー名を使い、
同名の`録音.ogg`を日時や題名で区別します。原音は変換せずMac内へ保存します。

同じDriveファイルの再取得と同じ音声SHAは重複として扱います。既存の文字起こし、日時、
確認済み候補を上書きしません。同じDrive IDの内容が変更された場合は競合として止め、
別の内容を既存録音へ自動で置き換えません。音声が別形式へ再圧縮された場合は別内容であり、
題名や日時だけでは同一録音と決めません。

確認できた録音開始日時に発話の経過秒を足し、既存のGPS履歴へ照合します。
GPSや原音をCodexへ送る範囲は増やしません。

## 接続と権限

SoundcoreとGoogle DriveとDaymeldの接続は、それぞれ別です。Soundcore側の認証を復旧しても、
Mac側のDrive取得が成功したことにはなりません。

Mac用の取得処理は、SoundCoreフォルダーだけを閲覧者として共有した専用サービスアカウントを
使用できます。Google Cloudのプロジェクト管理ロールやWorkspaceのドメイン全体の委任は不要です。
スコープは`drive.readonly`で、Driveの共有権限と組み合わせて読み取れる対象を限定します。
巡回・取得処理も設定したSoundCore配下だけを扱います。共有設定、作成・変更・削除は行いません。

専用鍵から短期アクセストークンを自動取得するため、外部OAuthアプリのTestingに伴う
更新用トークンの7日制限は適用されません。鍵の無効化・削除、ポリシーによる失効、
フォルダー共有の解除では取得できなくなるため、永久接続を保証するものではありません。
認証情報、取得元情報、同期状態はMac内で保護し、URLやトークンをログへ出しません。

従来のデスクトップOAuthも選べます。既存クライアント定義を再利用し、Drive用の認証情報を
Gmailとは別に保存します。この方式の`drive.readonly`は本人のDrive全体の読み取り許可です。
初回同意が必要で、外部アプリがTestingの場合は更新用トークンが7日で失効します。
サービスアカウントへの切り替えでは既存OAuthトークンを読み書きしません。
個人Gmailはこのフォルダー共有方式の対象外で、既存のGmail認証を維持します。

SoundcoreやDrive取得側で認証が必要になった場合は、Daymeldのアプリ内表示と端末の
ローカル通知で対応を知らせます。同じ障害の繰り返し通知を抑えます。
iPhoneを閉じている間の即時通知は保証しません。条件と手順は[認証通知](connection-alerts.md)を参照してください。

## Macでの設定と確認

サービス用リポジトリの`secrets/`に専用サービスアカウントのJSON鍵を保存し、
ファイルを本人所有の0600にします。シンボリックリンク、別所有者、広い権限の鍵は拒否します。
キーを置くだけでは認証方式は変わりません。Google Driveで対象のSoundCoreフォルダーを
そのサービスアカウントへ閲覧者として共有してから、明示的に切り替えます。
サービスアカウントにはメール受信箱がないため、共有時の「通知」は外します。

```bash
uv run --frozen python -m daily_reader.drive_sync sync \
  --service-account-key secrets/drive-service-account.json \
  --folder-id '<SoundCoreフォルダーのID>'
uv run --frozen python -m daily_reader.drive_sync status
```

既に同じフォルダーを設定済みなら`--folder-id`は省略できます。鍵の読込と実際のDrive上の
対象フォルダーの読み取りに成功した後だけ、認証方式と鍵の参照を保護された設定へ保存します。
確認前の失敗・設定保存の失敗では、既存設定とOAuthトークンを保持します。
通常の`sync`と常駐workerは保存済み認証方式を再利用します。
認証先はGoogleの正規トークンURLだけに固定し、別ユーザーへの委任は受け付けません。

デスクトップOAuthを使う場合は次の手順です。作業用worktreeで実運用の認証情報を作成しません。

```bash
uv run --frozen python -m daily_reader.drive_sync auth
```

ブラウザーでGoogleの読み取り許可を確認します。認証情報は`secrets/drive-token.json`へ
0600で保存し、Gmail用トークンは変更しません。認証の取消・権限不足・保存失敗時は、
既存のDrive認証情報を上書きしません。認証し直す場合だけ`auth --force`を使います。

次に、本人が選んだSoundCoreフォルダーのIDを指定して初回同期します。

```bash
uv run --frozen python -m daily_reader.drive_sync sync --folder-id '<SoundCoreフォルダーのID>'
uv run --frozen python -m daily_reader.drive_sync status
```

サービスアカウントから既存OAuthへ戻す場合は`sync --user-oauth`を明示します。
OAuthで対象フォルダーを読み取れることを確認してから設定を切り替えます。
`auth`はOAuthの認証情報を更新するだけで、選択中の認証方式を変更しません。

初回にDrive上の対象フォルダーを確認してから、`data/drive-sync.json`へ設定を保存します。
Webサーバー内の`DriveSyncWorker`が起動時と通常15分ごとに確認し、未設定時は取得しません。
Macのサーバーが稼働していればDriveからの取得は続きます。SoundcoreからDriveへの転送は
引き続きSoundcore側の接続・動作条件に依存します。

巡回中のフォルダー・ページとファイル別の状態は`data/drive-sync.sqlite3`へ保持します。
一度の実行は最大10ファイル・50ページで、15分を目安に区切ります。通信中はその終了を待つため、
厳密な15分の強制終了ではありません。上限に達した続きは次回に処理します。
上限に達しただけでは全件取得済みと判断しません。元データが多い初回は複数回に分かれます。
接続などの全体的な失敗が続く場合は実行間隔を広げ、既定設定では最大4時間まで待ちます。
一件の形式不正や内容競合で、正常な録音の取得を永久に止めないようにします。
一時コピーは`data/drive-sync-staging/`で保護し、取得・送信後に回収します。
取得中は一時コピーとDaymeldへの保存コピーを考慮して5 GiBの空きを残します。

`status`は設定・認証情報の存在と直近実行結果を読む診断です。`auth_type`が認証方式、
`credential_private`が認証ファイルの保護状態です。サービスアカウントでは鍵のファイル属性
だけを調べ、鍵本文の読み取りやGoogle認証を行いません。`authorized`がtrueでも
Googleとの通信成功や全録音の解析完了とは判断しません。失敗した取得を明示的に再試行する場合は
`sync --retry-failed`を使います。内容が変更された元ファイルの競合は、自動上書きで解消しません。

## Daymeldの受け取りAPI

`POST /api/conversations/drive-imports`へ音声のメタデータを渡すと、受付状態と
`needs_audio`を返します。必要な場合だけ`POST /api/conversations/drive-imports/{id}/audio`へ
Content-Length付きで原音を送ります。OGG/MP3の内容、容量、存在するMD5を検証します。
メタデータに取得URLや認証情報は受け付けません。
メタデータだけの未受信受付は、原音処理の待ち件数に含めません。音声受信中・保存後の
解析受付待ちを合わせて最大10件とし、受信開始時に確認します。取得側が止まって残った
未受信受付は同じIDで再開でき、後続の録音を塞ぎません。

`GET /api/conversations/drive-imports`はページ指定付き一覧、`GET /{id}`は受付状態を返します。
保存後は専用workerが1件ずつ解析を受け付けます。再起動時は中断された転送・解析受付を復旧し、
成功済みの文字起こしを繰り返しません。転送または解析受付が失敗上限に達した受付は
`POST /{id}/retry`で再試行できます。APIは既存の接続元・Host・Origin制限内にあり、Funnelへ公開しません。

## 実環境で確認したこと

2026年9月9日の比較では、Soundcore Online Hubを閉じた間、11秒の新しい録音は約6分以上
Driveへ届きませんでした。Online Hubを開いた直後、同期設定を変更せず音声が保存されました。
この環境ではSoundcoreの画面を開くと転送が進みましたが、すべての環境で閉じると必ず停止すると
一般化はしません。Soundcore側は別の接続復旧処理で確認します。

既存のGoogle Driveコネクターは音声のメタデータと内部ファイル参照を返しましたが、
このMacへ実ファイルを展開する機能は提供されていませんでした。アプリ内ブラウザーとChromeの
通常ダウンロードも失敗したため、Mac用のDrive API取得処理を用意しています。
コネクターの取得成功だけを、Macへの原音保存成功とは扱いません。

初回OAuth取り込みでは実音声8件を保存し、8件ともサイズ・MD5・SHA256・録音日時・取得元情報が
一致しました。再同期は新規0件・登録済み8件・失敗0件で、録音IDと原音ハッシュも同じでした。
この時点の取り込み完了は解析受付までであり、全8件の文字起こし完了を意味しません。
専用サービスアカウントでも対象フォルダーの読み取りと編集不可を実通信で確認し、
代表音声1件のサイズ・MD5・SHA256が既存原音と一致しました。

回帰検証には匿名の音声転送・再開・重複排除・日時の引き継ぎ、および専用認証への切り替え・
失敗時の既存認証保持・OAuthへの復帰・常駐workerによる認証方式の再利用を含みます。

## 公式仕様

- [Driveの原本ダウンロード](https://developers.google.com/workspace/drive/api/guides/manage-downloads):
  通常ファイルは`files.get`のメディア取得を使用します。Google文書の変換用exportとは別です。
- [Driveのファイルとフォルダー検索](https://developers.google.com/workspace/drive/api/guides/search-files):
  親フォルダー条件を指定し、ページトークンを追って列挙します。
- [デスクトップアプリのOAuth](https://developers.google.com/identity/protocols/oauth2/native-app):
  ブラウザーの同意とloopbackでの応答受信を使用します。
- [Google認証情報の失効条件](https://developers.google.com/identity/protocols/oauth2#expiration):
  更新用の認証情報にも失効条件があります。
- [サービスアカウントへのDrive共有](https://developers.google.com/workspace/guides/create-credentials):
  特定フォルダーを直接共有する場合、管理ロールやドメイン全体の委任は不要です。
- [サービスアカウント認証](https://developers.google.com/identity/protocols/oauth2/service-account):
  専用鍵から短期アクセストークンを取得します。
- [サービスアカウント鍵の管理](https://docs.cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys):
  鍵を保護し、失効・無効化と運用時の交換を別に管理します。
