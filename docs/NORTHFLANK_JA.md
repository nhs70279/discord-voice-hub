# リリンをNorthflankの無料Sandboxへ移す手順

この構成はDiscord Gateway・ボイス（DAVE/UDP）・Thai TTS・YouTube音楽・タイ語Web管理画面を**同一のDockerサービス1台**で動かします。Vercelのようなリクエスト単位のFunctionではありません。

## 移行先の準備状況（2026年11月27日）

- Northflank Team: `karamel's team`（Free）
- 既存の空のProject: `Default` / Project ID `default` / US Central
- GitHub連携: `nhs70279/discord-voice-hub` を選択可能
- Combined Service: `discord-voice-hub`、ブランチ `main`、`/Dockerfile`、CIを有効化
- Runtime: `nf-compute-20`（0.2 shared vCPU・512MB）、インスタンス1台
- Public HTTP: コンテナの `8080` を公開。実際のドメインはサービス作成後に確認する

**重要：** 管理画面の実際のサービス作成フォームでは、Freeプランでもクレジットカードによる本人確認が必須でした。カードを登録するまでサービス作成・デプロイはできません。カード情報をチャットやGitHubに貼らず、NorthflankのStripe画面で本人が入力してください。料金ページのFree Sandboxは常時稼働・無料枠を案内していますが、適用条件・利用超過の請求はBillingで確認してください。

## 1. NorthflankでCombined Serviceを作る

1. [NorthflankのDefault Project](https://app.northflank.com/t/karamels-team/project/default) を開く。
2. **Deploy repository** から `nhs70279/discord-voice-hub` を選ぶ。
3. 名前 `discord-voice-hub`、Git branch `main`、Build type **Dockerfile**、Build context `/`、Dockerfile `/Dockerfile`。
4. **Enable CI** をONにする。新しいコミットでビルドされる設定を使用。
5. Runtime Plan `nf-compute-20`（512MB）、Instances `1`。SQLiteとDiscord Botの重複起動を防ぐため複数にしない。
6. HTTPポート `8080` / Public / HTTPを1つだけ設定する。NorthflankがDockerfileから8080を検出した場合は追加不要。
7. 可能ならHTTP health checkを `/healthz` に設定する。起動に数十秒かかる場合があるのでGrace periodを十分に取る。
8. カード認証を本人が完了させ、サービスを作成する。

DockerイメージにはFFmpeg・Deno・Python依存関係が含まれ、起動コマンドは `python -m uvicorn app.server:app --host 0.0.0.0 --port 8080` です。既存の `deploy/fly-entrypoint.sh` はFly専用APIを呼んでおらず、`/data` の所有者を設定して権限の低い `voicehub` ユーザーで起動するためNorthflankでも使用します。

## 2. `/data` を永続化する（Botを本格起動する前に必須）

NorthflankのService → **Volumes** → **Add volume** で、Single Read/Write・1GB以上を作り、Container mount pathを **`/data`** に設定します。1サービス1インスタンスの構成にしてください。

次の環境変数をService → Environment variables → Runtime variablesに登録します。

| Key | Value |
| --- | --- |
| `DATABASE_PATH` | `/data/voice_hub.sqlite3` |
| `COOKIE_SECURE` | `true` |
| `DISCORD_TOKEN` | Discord Bot Token（秘密） |
| `DISCORD_CLIENT_ID` | Discord Application ID |
| `DISCORD_CLIENT_SECRET` | Discord OAuth Client Secret（秘密） |
| `DISCORD_REDIRECT_URI` | `https://実際のcode.runホスト/auth/callback` |

値の書式は [`.env.northflank.example`](../.env.northflank.example) にあります。**TokenやClient SecretをGitHubのファイル・コミット・ログに書かないこと。**

Webにログインするには [Discord Developer Portal](https://discord.com/developers/applications) の対象アプリ → OAuth2 → Redirectsにも、NorthflankのURLと**完全に一致する** `https://.../auth/callback` を登録します。旧Fly.ioのRedirectは移行が完了するまで残して構いません。

## 3. 既存Fly.ioのプレイリスト・個別設定を引き継ぐ

旧DBにはOAuthトークンも含まれます。バックアップを公開・GitHubへコミット・チャットに添付しないでください。

1. Fly.io側のDiscord Botを停止して二重接続を防ぎ、最後のSQLiteバックアップを取る。旧Machineはトライアルにより5分で停止するため、短時間の起動中に実施します。
2. 旧Machineのコンテナ内で `python -m app.backup` を実行する。`/data/backups/voice_hub_日時.sqlite3` が作られます。
3. `fly ssh sftp get -a discord-voice-hub /data/backups/voice_hub_日時.sqlite3 ./voice_hub_backup.sqlite3` のように**自分のPCへ**安全にダウンロードする（手元の`flyctl`のヘルプで書式を確認）。
4. Northflankで `/data` ボリュームをマウントしたサービスが動くようになったら、Northflank CLIでバックアップを**別名** `/data/import.sqlite3` にアップロードする。

   ```bash
   northflank upload service file --projectId default --service discord-voice-hub --localPath ./voice_hub_backup.sqlite3 --remotePath /data/import.sqlite3
   ```

5. **稼働中のSQLiteファイルを上書きしないでください。** インポートしたファイルの整合性と所有者を確認し、Web/Botプロセスを停止したメンテナンス状態で `/data/voice_hub.sqlite3` に復元してください。復元時は旧ファイルを別名で保管し、バックアップからのリカバリーを可能にしてください。
6. NorthflankのBotを起動し、個人の音声設定・プレイリスト・Webログインを確認する。旧Fly.ioアプリと同一のBot Tokenで**同時に起動しない**こと。

復元手順はデータ損失につながる可能性があるため、ユーザーのバックアップ取得とサービス作成が終わった段階で、実際のNorthflankコンテナ状態を確認しながら実行することを推奨します。**既存Fly Volumeを削除しないでください。**

## 4. 稼働確認

1. 公開URLの `/healthz` がHTTP 200を返す。
2. コンテナログにDiscord Gateway接続成功が出る。
3. Discordのボイスに人が入るとリリンが自動参加する。
4. `/testtts` → `/diagnose` で音声フレームが増えることを確認（Edge Neural失敗時はGoogle Thaiへ自動切替）。
5. `/play` でYouTube音楽、Webからログインとプレイリスト操作を確認。
6. 5分以上、30分以上経ってもサービスが継続稼働することを確認。

Northflank Free Sandboxは長期の稼働保証を提供する本番プランとは異なります。無料枠、CPU 0.2 shared、512MB RAMで音声処理が遅い・OOMになる場合、プラン変更前に使用量と料金を確認してください。

## 参考（Northflank公式）

- [GitHubからのCombined Service](https://northflank.com/docs/v1/application/getting-started/build-and-deploy-your-code)
- [永続Volumeの追加](https://northflank.com/docs/v1/application/databases-and-persistence/add-a-volume)
- [環境変数・秘密情報](https://northflank.com/docs/v1/application/secure/inject-secrets)
- [CLIファイル転送](https://northflank.com/docs/v1/api/copy-files)
- [Freeプラン・Billing](https://northflank.com/pricing)
