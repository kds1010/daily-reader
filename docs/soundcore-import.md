# Soundcoreクラウドからの取り込み

Soundcoreで音声を含む共有リンクを作り、Daymeldの「会話」→「Soundcoreの共有リンクを取り込む」へ
貼り付けると、Mac miniがクラウドから原音を直接取得します。iPhoneでMP3を書き出す必要はありません。
Soundcore側で文字起こしをしていない録音も、音声が共有されていれば取り込めます。

## 共有リンクからの流れ

1. Soundcoreの共有操作で、音声を含むリンクを作成します。
2. Daymeldの「会話」でリンクを貼り付けて取り込みます。ショートカットの
   「SoundcoreリンクをDaymeldに取り込む」からもURLを渡せます。
3. Macが受付を保存し、1件ずつ取得します。Macでの受付後は、iPhoneを開いたままにする必要はありません。
4. OGGまたはMP3の原本を変換せず保存し、既存のMac内の文字起こし・話者分離へ渡します。
5. 確認できた録音開始日時と発言の経過秒をGPS履歴へ照合し、自動整理の設定に従い
   タスク・調査・予定・関心などの候補を作ります。

「取り込み完了」は原音の保存と音声解析の受付を指します。文字起こし・Codex整理の完了は録音側の状態で
確認してください。取得失敗は取り込み一覧から再試行できます。解析失敗は録音詳細から再解析します。
Macへの受付前に接続できなかった場合はリンク入力を残します。TailscaleとMac側サービスが必要です。

共有済み録音を取得する機能であり、Soundcoreアカウント全体の新規録音を自動発見する機能ではありません。
Soundcoreのログイン資格情報の保存や、クラウド同期設定の自動変更は行いません。
同じ共有リンクは同じ受付を返し、元の録音が編集されても自動で再取り込み・上書きしません。

## 取得データと日時

- 対応URLは、取得方式を確認したEUサーバーの
  `https://speaker-eu.eufylife.com/knowledge/sharelink/<共有コード>`です。
  言語指定とフラグメントを正規化し、別ホスト・別パス・資格情報付きURLは受け付けません。
- 共有ページ自身の読み取りAPIから音声の配信先を取得し、確認済みのCloudFrontホストだけから取得します。
  HTTPSの証明書を検証し、DNSで得たIPv4の全応答を公開IPに限定して接続先を固定します。リダイレクトは追跡しません。
  稼働環境でIPv6の名前解決だけが遅延するため、確認済みの両ホストにはIPv4で接続します。OSのDNS設定は変更しません。
- 共有コードや音声URLをログ・応答・Codexへ渡しません。再送用の共有URLはMacの保護されたDB内だけに保存します。
  原音は`data/conversations/audio/`へ保持し、取得元のタイトル・時刻・録音時間と、存在する場合の
  クラウド文字起こし・要約は非公開の`cloud_metadata`へ分離して保存します。
- 今回の解析入力は原音からMacで生成した文字起こしです。クラウド要約を発言扱いせず、
  クラウド文字起こしから時刻・話者を推測して取り込みません。
- クラウドのepoch日時と未変更の日時タイトル（日本時間）が秒単位で一致する場合だけ、
  `recorded_at_source=soundcore_cloud_timestamp`として録音日時を確定します。
  改名済み・日時不一致・不正値では元情報を保持して日時不明にします。共有作成時刻・取得時刻で代用しません。
  日本以外で作ったタイトルなどは、この一致条件では確定できない場合があります。
- 同じ原音のSHA-256が既存録音と一致した場合は、その録音を返し、本文・日時・候補・人物対応を変更しません。
  同じ会話でもSoundcore側で再圧縮した別形式は別ファイルのため、タイトルや日時だけでは統合しません。

## 再試行と制限

取り込み待ちは最大10件、取得は1件ずつです。通信の一時失敗は間隔を空けて初回を含め最大3回試し、
サーバー再起動後も保存済み受付から再開します。期限切れ・音声共有なし・未対応形式は失敗理由を表示します。
音声は2 GiB以下、メタデータは10 MiB以下、転送は最大10分、通信の待機は25秒です。
保存時に5 GiBを残し、取得中も一時コピーと本保存に必要な空き容量を確認します。
通常の取得失敗時は途中ファイルを取り込み用の一時領域から回収し、既存の原音や外部原本は削除しません。

2026-09-09に、本人指定の未文字起こし共有リンクから、約40分・22,451,708 bytesのOGG原音を
最後まで取得し、タイトルとepoch日時の一致、0600での保存を確認しました。
共有リンクの情報は[公開共有ページの実装](https://speaker-eu.eufylife.com/acc_h5_resource/static/js/async/knowledge_sharelink/%5Bshare_code%5D/page.4dab74c5.js)と
[読み取りAPIを呼ぶ実装](https://speaker-eu.eufylife.com/acc_h5_resource/static/js/knowledge.55cc78cd.js)で確認しています。
これは一般向けAPIの互換性保証ではないため、仕様変更時は安全なエラーとして停止します。
Online Hubのアカウント連携・クラウド同期については[公式FAQ](https://www.ankerjapan.com/blogs/faq/soundcore-work)を参照してください。

## ファイルから取り込む場合

Soundcoreで録音をMP3として書き出し、共有先にDaymeldを選ぶと、Mac miniへの送信が始まります。
送信後は従来どおり、Mac内で文字起こし・話者分離を行い、自動整理の設定に従って処理します。

## ショートカットで操作を短縮する

iPhone・Macのショートカットアプリで、Daymeldの「MP3をDaymeldに取り込む」アクションを使えます。

1. 新しいショートカットに「MP3をDaymeldに取り込む」を追加します。
2. 「MP3ファイル」に、共有から受け取る「ショートカットの入力」、または書き出し済みのMP3を指定します。
3. 共有から使う場合はショートカットの詳細で共有シートへの表示を有効にします。
   固定ファイルを取り込む場合はホーム画面などへショートカットを追加すると、そのボタンで開始できます。
4. 実行するとDaymeldが開き、ファイルを端末へ保存してから受付結果を返します。
   「会話」の送信待ちが消えて取り込み履歴に表示されるまで、Daymeldを開いておいてください。

固定ファイル指定は毎回そのファイルを送ります。Soundcore内の最新録音を自動選択するものではありません。
同じ原音の再送はMac側のSHA-256で重複排除し、既存の解析結果や録音日時を変更しません。
ショートカットの「受付」は端末への保存であり、Macへの送信・文字起こしの完了ではありません。

## MP3書き出しとアカウント全体の自動化

2026年9月8日に確認した公式公開資料では、Soundcoreの録音一覧から未取り込みMP3を取得する
公開API・App Intents・Shortcutsの仕様を確認できませんでした。Soundcore側でのMP3書き出し・共有操作は残ります。
アカウント全体から未共有の録音を取り出す常時自動転送は実装していません。
共有リンクを渡した録音については、上記のクラウド取り込みでMP3書き出しを省けます。

- [Soundcore公式製品説明](https://www.soundcore.com/products/d3200-soundcore-work-ai-voice-recorder)では、USBによる録音のエクスポートは非対応です。
- [日本公式FAQ](https://www.ankerjapan.com/blogs/faq/soundcore-work)のOnline HubはSoundcoreアカウントとクラウド同期を使うWeb版です。Web版の存在だけでは任意アプリ向けAPIの利用可否は判断できません。
- [日本公式リリースノート](https://www.ankerjapan.com/blogs/magazine/ai_voice_recorder_release_notes)と[公式Work FAQ](https://service.soundcore.com/article-description/soundcore-Work-FAQ)も確認しました。録音機からSoundcoreアプリへの自動同期と、Daymeldへの書き出しは別工程です。

この機能はSoundcoreの資格情報を保存せず、クラウド同期を有効にしません。
APIの接続先はDaymeldに設定済みのMac miniです。Tailscale接続とMac側サービスの稼働が必要です。

## MP3ファイル送信の通信失敗・中断からの復旧

MP3の共有・ファイル選択・ショートカット入力は、端末のApplication Support内の
`Daymeld/ConversationImports/`へ原音と元のファイル名を保ったコピーとして保存します。
コピーと索引はアプリ内だけで扱い、バックアップ対象外にします。iPhoneでは初回ロック解除後に
読み書きできるファイル保護を設定します。受付済みの同一内容は1件にまとめ、1件ずつ送信します。

- 通信失敗時は「会話」の送信待ちに残し、「失敗したファイルを再送」で再試行できます。
- アプリを再起動すると受付済みの送信待ちを復元し、一度ずつ送信を試みます。
- アプリの停止・強制終了中の送信継続は保証しません。失敗が続く場合は接続・空き容量を確認してください。
- 読み取れないキュー項目は保持して警告し、正常な項目の送信は継続します。
  コピー中に停止して受付が完了しなかった一時ファイルは、次の起動時に回収します。
- Mac側の成功応答後に端末内のキューコピーを削除します。iPhoneの共有受信Inbox内のコピーは、
  送信した内容と一致する場合だけ削除します。共有元やファイル選択元の原本は変更しません。
- MP3は2 GiB以下、TXTの通常取り込みは10 MiB以下です。MP3保存後にMacの空き容量を5 GiB残す条件は従来どおりです。
- Mac mini上の原音は削除しません。送信先変更後の再送は、その時点の設定済みサーバーへ送信します。

## 実装・検証

クラウド取得は`soundcore_cloud.py`、永続キューは`soundcore_imports.py`で実装しています。
`POST /api/conversations/soundcore-imports`へJSONの`url`を渡すと受付を返します。
GETで状態を読み、`POST /api/conversations/soundcore-imports/{id}/retry`で失敗分を再試行します。
URLはクエリ文字列へ載せません。一般録音APIへクラウドの原文・秘密URLは含めません。
匿名テストでダウンロード制限、DNS/TLS境界、日時、重複・並行受付・再起動回復、
SwiftのURLSessionと状態表示を検証し、両OSのビルドと配信確認を行います。

`ImportRecordingIntent`はAppleの[IntentFile](https://developer.apple.com/documentation/appintents/intentfile)を受け取り、
URL入力はファイルとしてコピーし、URLのない入力だけ`data`を使います。元の`filename`を保持します。
[openAppWhenRun](https://developer.apple.com/documentation/appintents/appintent/openappwhenrun-475kn)は
iOS 17・macOS 14との互換性のため使用し、長いネットワーク送信をIntentの実行完了条件にしません。
新しいExtension、権限、外部サービスは追加しません。

`tests/test_conversation_import_native.py`は実際のIntentファイル受け渡し、永続キュー、APIClient、
URLSessionを匿名HTTPサーバーへ接続して、原音・ファイル名の保持、重複、失敗、再起動、再送、
共有元の保護、破損項目の分離と未完了コピー回収を検証します。
既存の受信宣言は`tests/test_ios_file_sharing.py`、両OSの組み込みはXcodeビルドで確認します。
Soundcoreの共有シート、ショートカットのホーム画面登録、実機上の操作確認は配信後の独立工程です。

2026-09-08の検証では両OSの署名なしビルドとアクションメタデータ生成が成功しました。
iOSではSSU YAML生成後に`Could not archive SSU artifacts`が出るため、Siriの言い換え認識は未確認です。
[Appleの説明](https://developer.apple.com/videos/play/wwdc2023/10102/)は言い換え認識を説明していますが、
このエラーの影響自体は確認できていません。メタデータ生成だけで実機操作の成功とは判定しません。
