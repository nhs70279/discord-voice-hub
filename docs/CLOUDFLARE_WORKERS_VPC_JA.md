# リリンの独立HTTPS管理画面 — Cloudflare Workers VPC

`olimpos.jp`を含む既存ドメインのDNSを一切変更せずに、リリンのWispbyteサーバーへHTTPS接続する。URLは **https://lilin-voice-hub.jp777.workers.dev**。

## 構成

1. ブラウザーはCloudflare Workersの無料`workers.dev`ドメインへHTTPSで接続する。
2. Worker（`deploy/cloudflare-worker/worker.mjs`）は`LILIN_VPC`というWorkers VPC Service bindingで転送する。
3. VPC ServiceはCloudflare Tunnelを介してWispbyteサーバーの`127.0.0.1:13225`へアクセスする。WorkerからWispbyteまでCloudflare Tunnelによって暗号化される。
4. Wispbyteでは`start_wispbyte.py`が`CLOUDFLARE_TUNNEL_TOKEN`を読み込み、公式の`cloudflared`をSHA-256確認後に`.runtime/`へ配置し、Bot/Webと同時に起動する。

Cloudflare側の作成名:

| 種類 | 名前 |
| --- | --- |
| Cloudflare Tunnel | `lilin-voice-hub-wispbyte` |
| Workers VPC Service | `lilin-wispbyte-local-dashboard` |
| Worker | `lilin-voice-hub` |
| Worker binding | `LILIN_VPC` |

## 安全に設定する

- Tunnel Tokenは**秘密**。GitHub・Discordメッセージ・サポートチケットには貼らない。
- WispbyteのEnvironmentファイル（`/.env`）に`CLOUDFLARE_TUNNEL_TOKEN`を追加して保存する。トークンをコマンド引数に含めず`TUNNEL_TOKEN`環境変数で子プロセスに渡す。
- 同じ`.env`内の`DISCORD_REDIRECT_URI`を`https://lilin-voice-hub.jp777.workers.dev/auth/callback`に設定する。`COOKIE_SECURE=true`も維持する。
- [Discord Developer Portal](https://discord.com/developers/applications)のリリンのOAuth2 Redirectsにも同じURLを登録して保存する。**正確に一致**させる。
- Wispbyteを再起動し、コンソールにCloudflare Tunnelの接続ログが出ることを確認する。
- ブラウザーで`https://lilin-voice-hub.jp777.workers.dev/healthz`を開き、`ok:true`と`discord_connected:true`を確認する。OAuthログイン後にユーザー設定を変更して確認する。

## 接続できない場合

Workers VPCは現在ベータで、**cloudflaredからCloudflareへのUDP 7844（QUIC）が必要**。Wispbyteの無料ノードでこのアウトバウンド通信が禁止されている場合、WorkerはHTTP 502を返す。この場合はWispbyteのサポートにUDP 7844の利用可否を確認する。Workerの内部エラーを利用者にそのまま表示しない。

Cloudflare Tunnel/Workers VPCは現時点で無料プランでも利用できるが、無料枠・ベータの条件は今後変わり得る。Wispbyte Freeの14日ごとのログイン条件も残る。

- [Cloudflare Workers VPC](https://developers.cloudflare.com/workers-vpc/)
- [Cloudflare TunnelとQUIC](https://developers.cloudflare.com/workers-vpc/configuration/tunnel/)
- [VPC Services](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/)
- [Workers VPC Binding API](https://developers.cloudflare.com/workers-vpc/api/)
