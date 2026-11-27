# Fly.io Free プラン向け Discord Voice Hub デプロイ手順

この手順で、**Discord Bot（タイ語チャット読み上げ＋音楽キュー）と管理Web画面を1つのFly Machine**で常時稼働させます。SQLiteの設定・プレイリスト・OAuthセッションは別の永続Volumeに保存します。Cloudflareがなくても、`https://<アプリ名>.fly.dev` で管理できます。

> **注意（2026-11-27確認）：** Fly.ioの新Freeプランは月 **$5相当の利用枠** があり、クレジットカード登録が必要です。$5を使い切るとPay As You Goへ移行するため、**月額0円を保証する構成ではありません**。CPU・RAM・Volume・外向き通信をすべて含めた利用量をダッシュボードで監視してください。URLを開かなくてもBotが動くように `auto_stop_machines="off"` にしています。その分、常時VM利用料が発生します。

## この構成のポイント

| 項目 | 設定 |
| --- | --- |
| Docker | `python:3.12-slim-bookworm` + FFmpeg + Deno + `discord.py` + FastAPI |
| Bot / Web | 1つのPythonプロセス（`uvicorn` 1 worker） |
| Fly Machine | `shared-cpu-1x` / 512MB RAM / **常時起動** |
| Region | `nrt`（東京。料金と利用可能性を確認して変更可） |
| Web | 公開HTTPS `https://<アプリ名>.fly.dev` |
| Database | `/data/voice_hub.sqlite3`（Fly Volume 1GB） |
| Secret | `fly secrets import`（`.env.fly` はGit・Dockerイメージに入れない） |
| 台数 | **1台のみ**。Botの二重起動とSQLiteの分離を防ぐ |

512MBで不足した場合はメモリ増量が必要ですが、無料$5枠を超える可能性が高くなります。まず少人数のサーバーで負荷を見てください。

## 1. Fly.ioアカウントとCLI

1. [Fly.io Free](https://fly.io/pricing/) に登録し、Freeプランの条件と従量課金への移行条件を確認します。
2. [公式のFly CLIインストール手順](https://fly.io/docs/flyctl/install/) で `fly` を入れ、ログインします。

```bash
fly auth login
fly platform regions
```

以下、アプリ名を例として `my-voice-hub-123` とします。Fly.io全体で一意の名前にしてください。**`<APP_NAME>` を文字通りコピペせず、実際の名前へ置換します。**

## 2. プロジェクトとアプリを作成

このZIPを展開し、プロジェクトに移動します。

```bash
unzip voice-hub-fly.zip
cd discord-voice-hub
```

エディターで `fly.toml` の `app` を実際の名前に書き換えます。

```toml
app = "my-voice-hub-123"
primary_region = "nrt"
```

アプリを作成します。**`fly launch` ではなく `fly apps create`** を使います（このZIPの `Dockerfile` と `fly.toml` を使うため）。

```bash
fly apps create my-voice-hub-123
fly ips list -a my-voice-hub-123
```

Public IPがまだない場合は、**無料の共有IPv4**とIPv6を割り当てます（専用IPv4は有料なので選びません）。すでに割り当て済みなら追加不要です。

```bash
fly ips allocate-v4 --shared -a my-voice-hub-123
fly ips allocate-v6 -a my-voice-hub-123
```

## 3. 永続Volumeを先に作成

```bash
fly volumes create voicehub_data --size 1 --region nrt -a my-voice-hub-123
fly volumes list -a my-voice-hub-123
```

`fly.toml` に既にある以下のマウント設定と名前を一致させます。

```toml
[mounts]
source = "voicehub_data"
destination = "/data"
```

Volumeは**1つだけ**作ります。別のMachineが同じVolumeを同時マウントすることはできません。起動時にエントリーポイントが `/data` の所有権を調整し、Bot本体は権限の少ない `voicehub` ユーザーで動きます。Volumeは自動スナップショットが既定で有効ですが、スナップショットも保存コストの対象です。

## 4. Discord Developer Portalの設定

1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリを新規作成します。
2. **Bot** で Tokenを取得し、**Message Content Intent** をONにします（テキスト読み上げに必要）。
3. **OAuth2 / General** で Client IDとClient Secretを取得します。
4. **OAuth2 / Redirects** に次を登録します。URLが1文字でも違うとログインに失敗します。

```text
https://my-voice-hub-123.fly.dev/auth/callback
```

5. OAuth2 URL Generatorで `bot` と `applications.commands` を選び、View Channels / Send Messages / Connect / Speak権限を付けてDiscordサーバーへ招待します。

Token・Client Secretは他人に送ったり、GitHubへコミットしたりしないでください。

## 5. SecretをFly.ioへ登録

```bash
cp .env.fly.example .env.fly
```

`.env.fly` をエディターで編集し、4つの項目を自分の値に変更します。

```dotenv
DISCORD_TOKEN=自分のDiscordBotToken
DISCORD_CLIENT_ID=自分のClientID
DISCORD_CLIENT_SECRET=自分のClientSecret
DISCORD_REDIRECT_URI=https://my-voice-hub-123.fly.dev/auth/callback
```

このファイルを端末に表示したり公開したりせず、標準入力で秘密情報として登録します。

```bash
fly secrets import --stage -a my-voice-hub-123 < .env.fly
fly secrets list -a my-voice-hub-123
```

`--stage` は初回デプロイまでMachineを起動しないための設定です。`COOKIE_SECURE=true` と `/data` のDBパスは `fly.toml` にあるため、Secret化しなくてかまいません。

## 6. デプロイ（必ず1台）

```bash
fly deploy --ha=false -a my-voice-hub-123
fly status -a my-voice-hub-123
fly scale show -a my-voice-hub-123
```

`--ha=false` が重要です。Fly.ioの既定では高可用性の追加Machineを用意する場合があり、**無料枠の消費とDiscord Bot重複起動**を避けるために1台だけにします。初回デプロイ後、Machineが1台であることを確認してください。自動停止は `fly.toml` で無効にしています。

ブラウザで以下を開きます。

```text
https://my-voice-hub-123.fly.dev
https://my-voice-hub-123.fly.dev/healthz
```

`/healthz` は `{"ok":true,"discord_connected":true}` を返せばBotもDiscordに接続済みです。`false` の場合はDiscord Tokenや接続状況を調べてください。ヘルスチェックはWebプロセスの稼働のみを保証し、Discord音声の完全な疎通までは保証しません。

## 7. Bot機能を確認

1. DiscordでVoice Channelに入るとリリンが自動参加し、全テキストチャンネルとVC内チャットを読み上げます。手動の `/join` も利用可能です。`/testtts` で音声を確認できます。
2. `/play 曲名` または `/play https://www.youtube.com/watch?v=...`
3. `/queue`, `/skip`, `/next 曲名`, `/playnow 曲名`, `/remove 1`, `/move`, `/clear`, `/pause`, `/resume`, `/repeat`
4. WebダッシュボードにDiscordでログインし、個人別のタイ語女性声・男性声／速度・高さを変更
5. Webで個人プレイリストを作成し、自分がBotと同じVCにいるときに再生

**YouTubeの注意:** サンプルは `yt-dlp` とFFmpegを使う技術検証用です。YouTubeの制約・仕様変更によって再生に失敗することがあります。公開運用前に利用規約と音源の再生許諾を確認してください。

## 8. ログ・メモリ・請求額を確認

```bash
fly logs -a my-voice-hub-123
fly status -a my-voice-hub-123
fly machines list -a my-voice-hub-123
fly volumes list -a my-voice-hub-123
```

Fly.ioのBillingダッシュボードで**毎月の残り無料枠と外向き通信量**をチェックしてください。512MBでOOMが起きる場合は増量できますが、無料枠超過の可能性があります。専用IPv4、2台目のMachine、大きなVolume、有料のDBは追加しないでください。

WebのレスポンスがなくてもDiscord Gateway接続は必要です。`auto_stop_machines="off"` を `"stop"` に変更しないでください。

## 9. SQLiteのバックアップ

Fly Volumeには通常デイリースナップショットがありますが、単一Volumeはレプリケーションされません。重要なデータは別の場所へダウンロードしてください。

```bash
# 稼働中のSQLiteを安全に複製（WAL中でも整合したバックアップを作成）
fly ssh console -a my-voice-hub-123 -C "python -m app.backup"
```

コマンドに表示されたパス（例: `/data/backups/voice_hub_20261127T120000Z.sqlite3`）を指定してダウンロードします。

```bash
fly ssh sftp get /data/backups/voice_hub_20261127T120000Z.sqlite3 ./voicehub-backup.sqlite3 -a my-voice-hub-123
```

バックアップファイルにもDiscord OAuthトークンが含まれるため、厳重に保管してください。**1GB Volumeでバックアップを貯め続けると容量不足になります。** 別の保存先に移したら容量を管理してください。

## 10. 更新方法と制限

- 変更後は `fly deploy --ha=false -a my-voice-hub-123` で更新します。SQLiteは `/data` のVolumeに残ります。
- SQLiteとBotの状態は1台・1リージョンのみです。Machine故障やリリース更新では一時停止することがあります。無停止・高可用性構成ではありません。
- HTTP WebのみがFly Proxyを通ります。Discord Gateway・VC音声はFly MachineからDiscordへ直接接続します。
- Cloudflareで独自ドメインを使う場合は、Fly.ioの証明書とDNSを追加し、**Discord OAuth Redirect** と `DISCORD_REDIRECT_URI` の両方を新ドメインへ変更します。`*.fly.dev` で使う場合、Cloudflare Tunnelは不要です。
- **本番接続未検証:** この環境ではFlyアカウント・Dockerデーモン・実Discord Tokenがないため、イメージビルドとVC再生の実機確認はデプロイ後に行ってください。

## 公式ドキュメント

- [Fly.io Freeプラン発表](https://fly.io/changelog/introducing-the-free-plan/)
- [fly.toml リファレンス](https://fly.io/docs/reference/configuration/)
- [Volumes](https://fly.io/volumes/)
- [Secrets import](https://fly.io/docs/flyctl/secrets-import/)
- [Deploy](https://fly.io/docs/flyctl/deploy/)
- [料金](https://fly.io/pricing/)
