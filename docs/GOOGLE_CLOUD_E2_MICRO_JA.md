# Google Cloud e2-microでリリンを24時間稼働する（日本語）

対象: Discord音楽・タイ語読み上げ・Web管理画面を同じUbuntu 24.04 LTS x86_64 VMで動かす。Fly.ioのBotやVolumeは、復元が成功するまで削除しない。

> **重要: 「完全無料」は保証できません。** Google CloudのCompute Engine Free Tierは米国3リージョンのe2-microを月1台分、Standard Persistent Disk 30 GB-month、北米発の対外データ転送1 GB/月まで対象としています。しかし、**外部IPv4（静的・一時的）は通常 $0.005/時間（720時間で約 $3.60）**で、無料枠の外部IPは月1時間分だけです。BotがDiscordの音声サーバーに接続するにはインターネット接続が必要で、通常の外部IPv4付きVMではこの料金が発生します。音声データの転送も1 GB/月を超える可能性があります。Cloud Billing登録・支払い方法が必要です。

公式: [Compute Engine無料枠](https://cloud.google.com/free/docs/free-cloud-features)、[外部IP料金](https://cloud.google.com/vpc/network-pricing)、[予算・アラート](https://cloud.google.com/billing/docs/how-to/budgets)。**通常の予算アラートは請求額のハード上限ではありません。**

## 1. VMを作成する

1. [Google Cloud Console](https://console.cloud.google.com/) にログインし、プロジェクトを作る。Cloud Billingを有効にする。カード情報をチャットやGitHubに書かない。
2. **Compute Engine → VMインスタンス → インスタンスを作成**を選ぶ。
3. 名前 `lilin-voice-hub`、リージョンを **`us-central1` / `us-west1` / `us-east1` のいずれか**にする（東京はe2-micro無料枠の対象外）。ゾーンはリージョン内の通常のゾーンを選ぶ。
4. シリーズ **E2**、マシンタイプ **`e2-micro`**。Spot／Preemptibleにはしない。
5. ブートディスクを **Ubuntu 24.04 LTS（x86_64）・Standard Persistent Disk（pd-standard）・20～25GB** に変更する。既定のBalanced SSD（pd-balanced）は無料枠と異なるので確認する。追加ディスクやスナップショットは不要。
6. ネットワーク: **HTTP/HTTPSファイアウォールの許可はオフ**。ダッシュボードはTailscale Funnelで公開し、ポート8000やDiscord音声用ポートを外部へ直接公開しない。
7. インターネットへの発信に必要なため、この手順では通常の**一時的な外部IPv4**を有効にする。前述のとおり**毎月のIP料金が発生する**。外部IPを「なし」にすると追加の有料ネットワーク構成がない限りDiscordとYouTubeへの接続が困難になる。
8. 作成後、Cloud Billingで予算・通知を設定する。予算アラートだけでは自動停止せず、0円を保証しない。

## 2. SSHでインストール（Docker不要）

Google Cloud ConsoleのVM一覧から **SSH** を開き、以下を実行する。

```bash
git clone https://github.com/nhs70279/discord-voice-hub.git
cd discord-voice-hub
sudo bash deploy/gcp-install.sh
```

このインストーラーはFFmpeg・Python・Deno（公式SHA-256確認済み）を入れ、systemd自動起動ユニットを登録する。e2-microのメモリが1GiBと少ないため**2GiBのswap**を作る（ディスク容量を消費）。Botはまだ起動しない。リリンのソースは `/opt/voice-hub`、SQLiteデータは `/var/lib/voice-hub` に分離される。秘密情報の入った`.env`は後からインストーラーを再実行しても上書きしない。

## 3. 管理画面の無料HTTPSアドレスを用意する

Tailscale Personal Freeの **Funnel** を利用すると、独自ドメインや公開ポートなしで `https://ホスト名.テールネット名.ts.net` の固定HTTPSアドレスを利用できる。Funnelは全Tailscaleプランで利用可能だが、ベータ機能かつ帯域制限がある。

1. [Tailscaleに登録](https://login.tailscale.com/start) する。既存の個人アカウントで構わない。
2. VMのSSHでTailscaleをインストールし、出力される認証URLを自分のブラウザで承認する。

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```

3. 管理画面用ローカルポート8000をHTTPSで公開する。

   ```bash
   sudo tailscale funnel --bg 8000
   sudo tailscale funnel status
   ```

4. 初回はTailscaleの画面でFunnel許可・HTTPS設定を求められる場合がある。表示された**実際のHTTPS URL**を控える。インターネット上の誰でもそのURLに到達できるため、リリンのDiscordログイン保護を外さない。

参考: [Funnel公式ガイド](https://tailscale.com/docs/features/tailscale-funnel)、[Funnel CLI](https://tailscale.com/docs/reference/tailscale-cli/funnel)。

## 4. Discordの認証を設定して起動

1. [Discord Developer Portal](https://discord.com/developers/applications) のリリンアプリで **OAuth2 → Redirects** に `https://実際のホスト名.ts.net/auth/callback` を追加し、保存する。URLの大文字小文字・末尾のパスも一致させる。
2. VMのSSHから設定ファイルを開く。

   ```bash
   sudoedit /opt/voice-hub/.env
   ```

3. `DISCORD_TOKEN`、`DISCORD_CLIENT_ID`、`DISCORD_CLIENT_SECRET`、`DISCORD_REDIRECT_URI` の実値を入れる。Tokenをコマンドライン履歴、GitHub、公開ログに貼らない。`COOKIE_SECURE=true`、`DATABASE_PATH=/var/lib/voice-hub/voice_hub.sqlite3`、`PORT=8000` はそのまま。
4. **Fly.io上の同じBotが動いていないことを確認**してから起動する。

   ```bash
   sudo systemctl start voicehub
   sudo systemctl status voicehub --no-pager
   curl -fsS http://127.0.0.1:8000/healthz
   ```

5. 表示されたHTTPS URLへアクセスし、Discord OAuthログイン・プレイリスト画面・`/testtts`・`/play` を確認。

診断:

```bash
sudo journalctl -u voicehub -n 100 --no-pager
sudo journalctl -u voicehub -f
free -h
df -h /
```

## 5. Fly.ioからユーザーデータを安全に移す

`/var/lib/voice-hub/voice_hub.sqlite3` が新しい保存先。WebユーザーのOAuthトークンやプレイリストを含むため、DBは公開しない。Fly側が停止・バックアップ成功したことを確認するまでVolumeを消去しない。

1. 旧Fly Volumeで `python -m app.backup` を実行し、SQLite整合性のあるバックアップを取得する。旧サービスは停止して二重起動を避ける。
2. 自分の管理するPCにバックアップを安全にダウンロードし、**SSH/SCP経由**でGoogle VMへ転送する（GoogleのSSHコンソールのアップロード機能でも可）。たとえば一時的に `~/voice_hub_backup.sqlite3` として転送する。DBをGitHubやチャットにアップロードしない。
3. VMで**サービスを停止**し、新規DBがあれば別名で退避してから、バックアップを本番データ領域に配置する。既存ファイルは上書き前に保管する。SSHでの例:

   ```bash
   sudo systemctl stop voicehub
   sudo install -o voicehub -g voicehub -m 0600 ~/voice_hub_backup.sqlite3 /var/lib/voice-hub/import.sqlite3
   sudo -u voicehub sqlite3 /var/lib/voice-hub/import.sqlite3 'PRAGMA integrity_check;'
   ```

4. `ok` が返ること、元のバックアップを保持していることを確認する。**既存のDBを消す前に**、必要なら復元時点の状態を確認してから、`import.sqlite3` を正式DBへ切り替える。WAL/SHMが残っている場合は、サービス停止中にチェックポイントと整合性を確認する。DB操作に自信がなければ、その時点で実際のファイル状態を提示して相談する。
5. VMのリリンを起動して、プレイリスト、ユーザー別音声設定、Webログインをテスト。成功後も旧Fly Volumeはしばらく保持する。

## 6. 24時間稼働と費用のチェック

- `systemctl enable voicehub` 済みなので**VMを再起動してもBotが自動起動**する。通常のe2-micro VMは無操作スリープしないが、メンテナンス、障害、性能不足で中断する可能性はある。
- e2-microは共有CPU・メモリ1GiB。複数のDiscordサーバーで同時にTTSと音楽を流すと速度低下やメモリ不足になる場合がある。swapは性能を上げる仕組みではない。
- **月1GBのアウトバウンド通信は非常に少ない**。例えば128kbpsの音声を連続送信すると、音声部分だけで約58MB/時（約17時間で1GB）。実際はプロトコルの追加通信もあるので超過は早くなる。常時音楽・複数VC再生では無料枠超過が現実的。
- Billingで日々のコストとデータ転送量、外部IP課金を確認する。通常の予算アラートだけで課金は止まらない。
- 無料枠を重視するなら、使用量が予想より多いことが分かった段階で稼働を停止する。`sudo systemctl stop voicehub` はBotを止めるが、**外部IPv4付きVM自体が稼働中ならIP料金は継続**する。

**状態:** これはGitHubからインストール可能にするための設定・手順で、Google Cloudアカウント内のVM作成、Discord認証情報の登録、音声の実環境テストは未実施。
