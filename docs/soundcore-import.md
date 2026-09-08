# SoundcoreのMP3取り込み

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

## 自動化できる範囲

2026年9月8日に確認した公式公開資料では、Soundcoreの録音一覧から未取り込みMP3を取得する
公開API・App Intents・Shortcutsの仕様を確認できませんでした。Soundcore側でのMP3書き出し・共有操作は残ります。
DaymeldのボタンだけでSoundcore内の未出力録音を取り出す機能や、常時自動転送は実装していません。

- [Soundcore公式製品説明](https://www.soundcore.com/products/d3200-soundcore-work-ai-voice-recorder)では、USBによる録音のエクスポートは非対応です。
- [日本公式FAQ](https://www.ankerjapan.com/blogs/faq/soundcore-work)のOnline HubはSoundcoreアカウントとクラウド同期を使うWeb版です。Web版の存在だけでは任意アプリ向けAPIの利用可否は判断できません。
- [日本公式リリースノート](https://www.ankerjapan.com/blogs/magazine/ai_voice_recorder_release_notes)と[公式Work FAQ](https://service.soundcore.com/article-description/soundcore-Work-FAQ)も確認しました。録音機からSoundcoreアプリへの自動同期と、Daymeldへの書き出しは別工程です。

この機能はSoundcoreの資格情報を保存せず、クラウド同期を有効にしません。
APIの接続先はDaymeldに設定済みのMac miniです。Tailscale接続とMac側サービスの稼働が必要です。

## 通信失敗・中断からの復旧

共有・ファイル選択・ショートカットの入力は、端末のApplication Support内の
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
