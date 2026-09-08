# 会話の文字起こし

音声認識はMac内のfaster-whisperで行います。原音・正解文・認識本文を評価用の外部APIへ
送信せず、MP3原音は再圧縮・上書きしません。公開モデルの初回取得には通信が必要です。

## 認識設定

`conversation_transcription.py`が日本語、CPU int8、beam size 5、Silero VADで認識します。
設定版は`local-ja-v2`です。以前のsmallからlarge-v3-turboへ既定を変更しました。
既存の`DAYMELD_WHISPER_MODEL`指定は優先し、端末の環境設定は変更しません。
必要な環境設定は従来どおりNix/Home Managerで管理します。

前の認識文を次の窓へ引き継がない設定にし、誤認識が反復し続ける経路を抑えます。
窓をまたぐ文脈や表記の一貫性は弱くなる可能性があります。VADの閾値は既定を維持します。
beam size 5は以前もライブラリの既定だったため、これ自体を精度改善とは扱いません。
音量補正や推測による文章補完、低スコア発話の一括削除は行いません。

引数の根拠は導入済みfaster-whisper 1.2.1の
[transcribe実装](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py)と
[モデル名対応](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/utils.py)です。
`condition_on_previous_text=False`は反復ループを減らす一方で窓間の不一致を生み得ることが
実装のdocstringに明記されています。

## 結果と診断

会話詳細に使用モデルと診断を表示します。使用モデル・設定・録音時間・VAD通過時間・
認識所要時間・低logprob区間数・連続同文数・変換後のクリップ率をDBの
`recordings.transcription_metadata`へ保存します。`avg_logprob`は正解率ではありません。

認識区間の合計が録音時間の10%未満、連続同文、変換後PCMの1%以上が最大振幅付近の場合は
原音確認を案内します。これらは診断の目安であり、無音・誤認識・音割れの確定判定では
ありません。診断だけを理由に発話を削除しません。時刻は音声長の範囲に制限します。

2026-09-08の読み取り診断では、約28秒の録音のVAD通過は約0.5秒で、VAD閾値を0.5から
0.35へ緩めても変わりませんでした。現行PCM変換後の約3.45%がクリップしていましたが、
原音破損とは確定していません。約54分・58分の録音を含め、左右チャンネル平均による
大きな音量低下はなく、位相打消しを主因とする根拠もありませんでした。

## 再解析

完了済み・失敗したMP3の会話詳細から「文字起こしを再実行」を選べます。
Macの現在の設定で処理するため、既存モデル指定がある場合もそれを尊重します。
長い録音と初回モデル取得は時間がかかります。TXTは再解析しません。
同じ録音の二重投入とCodex整理中の投入は拒否します（競合はHTTP 409）。

認識失敗・空結果では旧本文を保持します。話者分離だけの失敗では認識本文を
「話者未判定」で保存します。Hugging Faceトークンは話者分離に必要ですが、
トークン不備で認識本文まで失うことはありません。秘密値を含み得る例外本文は返しません。

成功時に旧発話・話者名・認識設定を`conversation_transcription_history`へ保存し、
同じトランザクションで現在の結果を置き換えます。履歴はMac内のDBに保持し、
今回の画面には履歴復元操作を追加していません。話者番号は再解析で変わり得るため
名前は自動継承せず、この録音の人物対応を解除して再確認します。

確認済み候補の引用と位置根拠、追加済みの暮らしの項目は保持します。古い未確認候補と
暮らしの下書きは更新済み扱いにし、古い候補からの新規追加を拒否します。
再解析後は`transcription_needs_review=1`として自動Codex整理を止めます。
手動でCodex整理した新しい候補も自動採用せず、既存の用事との重複確認へ送ります。
旧録音全件の自動再解析は行いません。

## 品質の比較

`scripts/evaluate_conversation_transcription.py`はアプリDBを開かず、1回最大600秒の
音声区間を処理し、本文やファイル名を表示せず集計JSONだけを出します。
モデルごとに別プロセスで実行し、時刻・VAD・条件を揃えて比較してください。
認識所要時間はモデル読み込みを含み、peak RSSはそのプロセス全体の値です。
同時に動く処理の影響を受けるため、長時間録音の処理時間保証には使えません。

```bash
uv run --frozen python scripts/evaluate_conversation_transcription.py /path/to/audio.mp3 \
  --model small --previous-text --start 0 --seconds 60 --reference /path/to/verified.txt
uv run --frozen python scripts/evaluate_conversation_transcription.py /path/to/audio.mp3 \
  --model large-v3-turbo --start 0 --seconds 60 --reference /path/to/verified.txt
```

`--model-cache`で比較専用のモデル取得先を指定できます。`--no-vad`は局所診断用です。
正解文がない場合は`--reference`を省略し、文字誤り率を算出しません。他モデルの出力を
正解文にしないでください。正解文が空なら無音中の誤生成文字数だけを数えます。
文字誤り率はNFKC・小文字化・空白と句読点除外後の編集距離／正解文字数です。
音声・正解文・比較出力はGit管理外の一時領域へ置いてください。

2026-09-08にmacOSのKyoko音声（190語/分指定）で作った24.301秒・正規化後127文字の
匿名音声を比較しました。正解文は合成に使用した次の文章です。

> 明日の会議は午前十時からです。資料の更新と予約の確認をお願いします。データ品質の問題を調べて、原因と対応方法をまとめてください。来週の予定はまだ決まっていません。子供と図書館に行く前に、開館時間を確認します。今日は新しい機能を実装しましたが、テストはまだ終わっていません。

| モデル | 誤り文字数 / CER | 所要時間（読み込み含む） | peak RSS |
|---|---:|---:|---:|
| small・旧設定 | 5 / 3.94% | 65.542秒 | 1026 MiB |
| large-v3 | 未測定（10分で打ち切り） | 600秒超 | 未確定 |
| large-v3-turbo・新設定 | 4 / 3.15% | 262.381秒 | 814 MiB |

同じMac（8GB RAM・8 CPU）でモデルを順番に実行しましたが、Xcodeなど別の処理も
動いていたため、時間・RSSは負荷やスワップの影響を含みます。速度倍率やメモリ節約率は
この比較から断定しません。turboはこのサンプルで1文字改善し、large-v3の時間上限内で
完了したため採用しました。認識時間はsmallより増える可能性があり、長時間録音では
特に待ち時間が長くなります。モデル指定の上書きは従来どおり利用できます。

この短い合成音声だけでは、実録音・小声・固有名詞・話者交代・30秒窓をまたぐ会話の
精度改善を保証できません。実録音には手作業の正解文がないためCERは算出していません。
原音からの精度向上幅と、回帰テスト・配信の成功は区別して報告してください。

## 検証と配信

回帰テストは`test_conversation_transcription.py`、`test_conversations.py`、
`test_life_automation.py`、`test_life_assistant.py`、`test_local_server.py`、
`test_conversation_transcription_native.py`です。全pytest・ruffに加えて、共有Swiftのため
両OSのXcodeビルドが必要です。統合後はWeb LaunchAgent再起動、両OSの成果物再生成と
配信確認が必要です。Agent workerの実装は変更していません。
