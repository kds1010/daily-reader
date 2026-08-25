# Daily Reader

Codexへの自律タスク投入、今日やること、重要メール、関心のあるニュースをiPhoneで扱える個人用ダッシュボードです。Mac miniのlocalhostで動かし、Tailscale Serveを通して自分のtailnet内だけに公開します。外部公開用サーバーやデータベースは必要ありません。起動時はAgentタブを最初に表示します。

## 主な機能

- AgentタブからCodexへタスクを投入し、専用worktreeで実装・検証・main反映まで継続
- Agentタスクの待機、実行、判断待ち、完了、失敗状態をiPhoneから確認
- RSS/Atomフィードの定期取得
- URL正規化による重複除去
- キーワードによる注目度スコア
- カテゴリ、検索、新着順／注目順の切り替え
- 「あとで読む」と既読状態の端末内保存
- 各記事の「表示したくない」フィードバックを次回のハイライト選定へ反映
- 24時間以内の新着優先、前回掲載の減点、連続掲載の抑制と「新着／継続」表示
- 更新ごとの新規記事数、ハイライト採用数、新選・継続数の表示と履歴保存
- ダークモード、ホーム画面起動、オフラインキャッシュ
- 一部フィードが失敗しても、取得できた記事で更新を継続
- 起動時と毎日8時、10時、12時、17時、20時、22時の自動更新
- Codex CLIによる「今日のハイライト」と、公式リリースの製品別日本語まとめ（記事構成が変わった場合のみ生成）
- 自動車・製造業におけるML基盤、データマネジメント、データガバナンスの実践事例
- 業務改善やQOL向上につながるガジェット、家電、仕事効率化ツールの分野別ハイライト
- 記事を開いた履歴をMac mini内の`data/read-events.jsonl`へ記録し、`/api/analytics`で閲覧傾向を集計
- 不要とした記事をMac mini内の`data/feedback-events.jsonl`へ記録し、同一記事の除外と類似傾向の減点に利用
- localhostへの限定バインド
- Gmailの重要メール抽出、返信状況の追跡、日次・週次リマインド
- Gmailスレッドへのリンクと、対応済み・保留・対応不要の記録
- 「今日」画面でのタスク、期限、優先度、毎日・平日・毎週のルーティン管理
- 疲労度・気分・体調メモと、iPhoneショートカットから同期したHealthKit日次集計の表示

## 今日のタスクと体調

「今日」タブでは、期限当日・期限超過・期限なしのタスクと、その日に該当する
ルーティンをまとめて確認できます。通常タスクは完了すると一覧から外れ、ルーティンは
日ごとに完了履歴を保持して翌日に再表示されます。データはMac mini内の
`data/planner.sqlite3`だけに保存され、Gitには含まれません。

体調チェックインは画面から疲労度、気分、メモを入力できます。HealthKitの日次集計は
iPhoneのショートカットから次のAPIへ送信します。

```text
POST /api/health/sync
Authorization: Bearer <専用トークン>
Content-Type: application/json
```

専用トークンは改行なしで`secrets/health-sync-token.txt`へ保存します。送信例:

```json
{
  "date": "2026-08-24",
  "sleep_minutes": 412,
  "steps": 8432,
  "resting_heart_rate": 58,
  "hrv_ms": 44.2,
  "respiratory_rate": 14.5
}
```

## Gmailアシスタント

Google Cloudでデスクトップアプリ用OAuthクライアントを作成し、JSONを
`secrets/gmail-client.json`へ配置します。権限はGmailの読み取り専用だけです。

```bash
uv run --frozen daily-reader-gmail auth
uv run --frozen daily-reader-gmail sync
```

認証トークンとSQLiteデータベースはMac mini内だけに保存され、Git管理されません。
初回認証後、常駐サーバーが15分ごとにGmailを自動同期します。Daily Readerの先頭に
重要な未対応メールが表示されます。返信したスレッドは
「返信待ち」になり、Web操作や電話で対応した項目は画面の「対応済み」で完了できます。
日次表示は直近1日、週次表示は直近7日を基本とし、期限付きと返信待ちは期間外でも残ります。

## Mac miniで起動する

[uv](https://docs.astral.sh/uv/)が必要です。

```bash
uv sync --frozen
uv run daily-reader-local
```

別のターミナルまたはLaunchAgentでAgentワーカーを起動します。

```bash
uv run --frozen daily-reader-agent-worker
```

Agentが操作できるリポジトリは`config/agent-repositories.toml`の明示的な許可リストに限定されます。ワーカーはタスクごとに専用branchとworktreeを作り、Codexを構造化出力付きで反復実行します。検証済みの変更は最新`main`へrebaseしてpushし、成功後に作業環境を削除します。競合は同じCodexセッションへ戻して解決と再検証を行います。

ブラウザで <http://127.0.0.1:8787> を開きます。起動時に記事を取得し、その後は毎日8時、10時、12時、17時、20時、22時に更新します。サーバーは`127.0.0.1`だけで待ち受けるため、LANへ直接公開されません。

Codex CLIがログイン済みの場合は、更新時に注目記事から今日のハイライトを生成します。Snowflake、Databricks、dbt、Apache Icebergなどの公式リリースは製品別に束ね、英語の記事も日本語で要約して原文リンクを添えます。同じ候補記事の組み合わせでは再生成しません。実行にはCodexの利用量を消費しますが、`gpt-5.6-luna`を低推論設定で1回だけ呼び出し、ハイライトと公式リリースまとめを同時に生成します。Codexは`--ephemeral`、`--sandbox read-only`、構造化出力で呼び出され、記事本文中の命令を無視するよう指示されます。

## TailscaleでiPhoneだけに公開する

Mac miniとiPhoneを同じtailnetへ参加させ、Mac miniで次を実行します。

```bash
tailscale serve --bg --yes http://127.0.0.1:8787
tailscale serve status
```

`tailscale serve status`に表示された`https://<Mac mini名>.<tailnet名>.ts.net`をiPhoneのSafariで開きます。Tailscale Funnelは使用しません。Serveにはtailnetのアクセス制御が適用されるため、iPhoneだけに限定する場合はTailscaleのポリシーでも対象端末またはユーザーを制限してください。

## 購読先とキーワード

購読先は[`config/feeds.toml`](config/feeds.toml)、評価キーワードは[`config/keywords.toml`](config/keywords.toml)で管理します。

```toml
[[feeds]]
name = "サイト名"
url = "https://example.com/feed.xml"
category = "カテゴリ"
enabled = true
```

```toml
[positive]
Python = 4

[negative]
広告 = -4
```

タイトルまたは概要にキーワードが含まれると、指定した重みが記事のスコアへ加算されます。同じキーワードが複数回現れても、記事ごとに一度だけ加算します。

## 常駐化

`daily-reader-local`をmacOSのLaunchAgentから起動すると、ログイン後に常駐できます。Mac mini固有のHome Manager設定から、リポジトリ内の`.venv/bin/daily-reader-local`を作業ディレクトリ付きで起動する構成を推奨します。Tailscale Serveの設定はバックグラウンド設定としてTailscale側に保存されます。

## iPhoneへの追加

1. 公開されたページをSafariで開きます。
2. 共有ボタンから「ホーム画面に追加」を選びます。
3. 「Web Appとして開く」を有効にして追加します。

「あとで読む」と既読状態はiPhoneのブラウザ内にだけ保存されます。ブラウザデータを消去した場合や別の端末には引き継がれません。
「表示したくない」はMac miniにも保存されるため、別端末での表示と次回更新時のCodexによる選定にも反映されます。少数の指定だけでカテゴリ全体を除外せず、タイトル・情報元・カテゴリに繰り返し現れる傾向を減点材料として扱います。

## 開発時の検証

```bash
uv run ruff check .
uv run pytest
uv run daily-reader
uv run daily-reader-local
```
