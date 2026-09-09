# AI利用量の計測

Daymeldが起動するCodexリクエストを、継立の日次集計と共通のJSONL台帳へ追記します。
既定の保存先は`$XDG_STATE_HOME/tsugitate/ai-usage.jsonl`です。XDGの指定がなければ
`~/.local/state/tsugitate/ai-usage.jsonl`となり、`TSUGITATE_USAGE_LOG`で保存先を指定できます。
継立のインストールや追加のPython依存は必要ありません。

記録対象は次の工程です。設定中のモデルや推論量は変更しません。

| task_type | 対象 | task_id |
|---|---|---|
| `daymeld-news` | ニュースのハイライトと公式リリースまとめ | 候補入力のハッシュ |
| `daymeld-conversation-insights` | 会話からの候補抽出 | 録音ID |
| `daymeld-conversation-overview` | 会話の要約 | 録音ID |
| `daymeld-conversation-correction` | 文字起こしの文脈補正案 | 録音ID |
| `daymeld-conversation-verification` | 補正案の独立した意味照合 | 録音ID |
| `daymeld-life-research` | 暮らしの調べもの | 暮らしの項目ID |
| `daymeld-agent-planning` / `requirements` / `implementation` / `follow-up` | Agentの各工程 | AgentジョブID |
| `daymeld-agent-integration-conflict` / `default-branch-conflict` / `deployment` | Agentの競合解消・デプロイ | AgentジョブID |

各リクエストの終了時にschema version 1の1行を記録します。モデル指定がないリクエストは
`requested_model`と`requested_effort`を`null`にし、実モデルはCodexの明示的な実行時
モデル情報が得られた場合だけ`models`へ保存します。指定名から実モデルを推定しません。
`turn.completed.usage`から入力・キャッシュ入力・出力トークンを集計し、
`input_tokens`に含まれる`cached_input_tokens`を二重加算しません。
モデル情報を返さないCLIでは`models`は空のままでも、トークン数は記録できます。
費用は現在のCodexイベントで取得できないため`cost_usd=null`、
`cost_kind=unavailable`です。定額契約の請求額や推定単価から補完しません。

ホスト、工程、ID、指定モデルと推論量、時刻、経過秒数、終了状態、終了コード、
利用量だけを保存します。プロンプト、記事・会話本文、音声、ツール結果、エラー本文、
認証情報は台帳へ保存しません。台帳は0600で開き、flockの排他制御で並列追記します。
書き込めない場合は固定の短い警告だけを出し、主処理の結果を変更しません。

失敗・タイムアウト・通常の取消でも、その時点で取得できた完了イベントを記録します。
完了イベントより前の中断ではトークン数が不明になります。ワーカー自体のSIGKILLや
電源断では終了処理が実行されず、行が残らない場合があります。台帳は提供元の請求台帳や
端末内すべてのAI利用量を保証するものではありません。文字起こしのローカルWhisper、
モデルを呼ばない日記・集計、外部のtanomiやSoanへの中継はこのCodex計測に含みません。
