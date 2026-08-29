# Daily Reader agent instructions

作業を開始する前に、リポジトリ直下の `AGENT_CONTEXT.md` を最後まで読んでください。

- `AGENT_CONTEXT.md` に記載された目的、運用構成、ユーザーの関心、地域制約、検証手順を守ってください。
- 文書と実装・生成データが食い違う場合は、実装と実データを正として原因を確認し、必要に応じて文書も更新してください。
- 作業完了前に、変更内容に応じて `AGENT_CONTEXT.md` の更新が必要か確認してください。

## Deployment completion requirement

- Daily Reader の実行時の挙動に影響する変更は、検証、コミット、`main` への統合、`origin/main` への push だけでは完了ではありません。push 後に、変更の影響を受ける LaunchAgent を再起動して稼働中サービスへ反映し、実環境を検証してから完了としてください。
- Web サーバー、UI、ニュース、メール、プランナー、設定、依存関係を変更した場合は `org.nix-community.home.daily-reader` を再起動します。Agent のキュー、ワーカー、リポジトリ操作を変更した場合は `org.nix-community.home.daily-reader-agent-worker` も再起動します。
- 再起動前に `lsof -nP -iTCP:8787 -sTCP:LISTEN` で現在の正確な PID を記録してください。再起動には `launchctl kickstart -k gui/$(id -u)/<label>` を使い、プロセスを手作業で広範囲に停止しないでください。
- 再起動後は、PID が更新されたこと、該当 LaunchAgent が実行中であること、`http://127.0.0.1:8787/` が成功応答を返すこと、Tailscale Serve の公開 URL `https://sk-mins-mac-mini.tailc193b2.ts.net/` が成功応答を返すことを確認してください。変更した機能についても実環境で代表的な確認を行ってください。iPhoneクライアント変更の代表確認は次項の配信検証を指し、物理端末上の操作確認は含めません。
- iPhoneクライアントまたはSideStore配信を変更した場合は、mainへの統合後に`scripts/build_sidestore_release.py`で最新IPAとソースを再生成してください。生成IPAのバージョン、HealthKit entitlement、LANの`8788`、loopbackの`8789`を確認し、外出先配信が有効な場合は`scripts/verify_sidestore_remote.py`でFunnel成果物の一致、拒否パス、`443`/`8443`のTailscale境界を検証してください。ここまで成功すれば配信デプロイは完了です。秘密URLやtokenをコマンド出力、ログ、完了報告へ含めないでください。
- SideStoreによるApple Accountでの再署名、iPhoneへのインストール、外部回線での取得、権限付与、目視・操作確認は配信後の独立した実機工程です。iPhoneの未接続、ロック、VPN状態、ユーザー操作待ちをAgentタスクの失敗または未デプロイ理由にしないでください。完了報告では配信済みバージョンと、実機インストールが未実施かどうかを分けて示してください。
- 再起動または実環境検証に失敗した場合は完了と報告せず、ログを確認して修正と再検証を続けてください。権限、ネットワーク、外部状態などにより実行できない場合は、未デプロイであることと阻害要因を明示してください。
- 文書、コメント、テストだけの変更で実行時成果物が変わらない場合は再起動を省略できますが、完了報告に「実行時変更なしのためデプロイ不要」と明記してください。
