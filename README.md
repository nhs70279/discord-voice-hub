# リリン（ลิลิน）— タイ語読み上げ＆音楽Bot

**キャラクター：リリン**。長い黒髪・紫の瞳・近未来の女王をモチーフにしたボットです。WebダッシュボードとDiscordコマンドの表示文はタイ語で統一。アバター画像は [`app/static/lilin-avatar.jpg`](app/static/lilin-avatar.jpg) にあり、Discord Developer PortalのBotアイコンとして設定できます。

読み上げはリリン固有のタイ語女性ニューラル音声（`th-TH-PremwadeeNeural`）に統一しました。ユーザーごとの話速・音程設定は残し、初期値は声を不自然に変えない `0%` / `0 Hz` です。既存の男性声・他言語音声設定はDB起動時にリリンの声へ移行します（個人の話速、音程、プレイリストは保持）。「最も自然な声」の絶対評価はできず、実際の音質はEdge TTSサービスと文章に依存します。

> **Wispbyte Freeへの移行（第1候補）：** [Wispbyte無料Pythonサーバー・Webサブドメイン・Discord OAuth導入手順（日本語）](docs/WISPBYTE_JA.md)。`start_wispbyte.py`と`requirements.wispbyte.txt`でBot・管理画面を1プロセスで起動。メモリ512MBと共有IPの制約により、音声/YouTubeの正常動作は実環境テストが必要です。無料枠の維持には14日ごとのパネルログインが必要です。

> **Google Cloud e2-microへの移行（費用に注意）：** [Ubuntu 24.04・Tailscale Funnel・systemdによる導入手順（日本語）](docs/GOOGLE_CLOUD_E2_MICRO_JA.md)。`deploy/gcp-install.sh` を使い、Botと管理画面を同じ1GiB RAMのVMで実行します。**e2-micro本体は無料枠がありますが、通常の外部IPv4が約 $0.005/時、無料アウトバウンドは月1GBまでなので完全無料は保証できません。** 課金条件を確認してからVMを作成してください。

> **Northflankへの移行（未実施）：** [Northflank導入・Fly.ioからのSQLite移行手順（日本語）](docs/NORTHFLANK_JA.md)。現在のDockerfileを再利用して、単一の常時稼働サービスでDiscord Botとタイ語Webダッシュボードを動かします。サービス作成時は無料プランでもカードによる本人確認が要求される場合があります。

> **旧Fly.io構成：** [Fly.ioデプロイ手順（日本語）](docs/FLY_IO_JA.md)。SQLiteバックアップとFly Volumeを引き継ぐまでは旧アプリを削除しないでください。

> **Oracle Cloud Always Free + Cloudflare Tunnelで24時間稼働したい場合：** [タイ語のセットアップ手順](docs/ORACLE_CLOUDFLARE_TH.md) を参照してください。`deploy/oracle-install.sh` はUbuntu 24.04 ARM64用のインストーラー、`deploy/voicehub.service` は自動起動用のsystemdユニット、`.env.oracle.example` は本番用設定テンプレートです。Discord・Oracle・Cloudflareのアカウントでの実運用テストは未実施です。

リリンは人がVCに入ると自動参加し、サーバー内の全テキストチャンネルとVC内チャットを読み上げます（初期設定ON）。複数VCに人がいる場合は現在の利用者がいる部屋を維持し、別のVCにいるユーザーがチャットを投稿するとそのVCへ移動します。手動の `/join` も可能です。`/play` は曲名・YouTube動画URL・YouTubeプレイリストURL（最大30件）に対応し、Web画面（タイ語UI）から話速・声の高さと保存プレイリストを管理できます。

## できること

| 操作 | 内容 |
| --- | --- |
| `/join` | 手動でVCに参加／全チャットの読み上げを開始 |
| `/autojoin enabled` | サーバーのVC自動参加をON/OFF（サーバー管理者のみ） |
| `/testtts` | タイ語TTS生成・再生の確認 |
| `/diagnose` | チャット受信・VC・音声合成の診断 |
| `/play query` | 曲名・URL・プレイリストURLをキューの最後に追加 |
| `/next query` | 現在の曲は止めず、既存キューを飛ばして次に再生 |
| `/playnow query` | 現在の曲をスキップし、指定した曲を最優先で再生 |
| `/queue` | 現在再生中の曲と、番号付き再生待ち一覧（最大20件） |
| `/remove number` | 再生待ちの指定番号の曲だけを削除 |
| `/move from_number to_number` | 再生待ちの曲を並び替え |
| `/clear` | 再生中の曲は止めず、再生待ちだけ全削除 |
| `/skip` | 現在の曲をスキップし次の曲へ |
| `/stop` | 音楽を停止して再生待ちも全削除（TTSは継続） |
| `/pause`, `/resume` | 音楽の一時停止と再開（TTSは継続） |
| `/shuffle` | 再生待ちの曲順をランダムに変更 |
| `/repeat off/one/all` | リピートなし／現在の曲／キュー全体 |
| `/nowplaying`, `/volume` | 現在の曲の確認・音楽音量変更 |
| `/voice` | リリンの固定タイ語女性ボイスを確認 |
| `/leave` | Bot退出・読み上げ終了 |
| Webダッシュボード | タイ語表示・Discordログイン・話速／音程設定・音楽キューの追加／優先再生／指定削除／並び替え／操作、プレイリスト作成／曲追加／削除／再生・管理者によるVC自動参加ON/OFF |

読み上げ中は音楽の音量を自動で約28%に下げ、終わると元に戻します。キューを読み上げ用と音楽用に分けてあるので、読み上げと音楽は共存します。**通常はリリンのタイ語女性ニューラル音声**を優先します。Edge TTSが `NoAudioReceived` を返す場合は3回まで試し、それでも生成できないときだけタイ語の予備音声（gTTS）に切り替えます。予備音声では声質や速度・音程が異なる場合があります。日本語・英語・男性ボイスの選択はありません。文章の自動翻訳は行いません。

**キューの番号について：** `/queue` には「再生中」と「再生待ち」が別々に表示されます。`/remove 2` は再生待ちリストの2番だけを削除します（再生中の曲は消しません）。`/next` は再生中の曲が終わった直後に割り込み、`/playnow` は現在の曲を途中で切り替えます。プレイリストURLを指定した場合、登録順を維持します。

## 動かすまで

1. [Discord Developer Portal](https://discord.com/developers/applications) で **New Application** を作成。
2. **Bot** からTokenを取得し、**Privileged Gateway Intents → Message Content Intent** を有効化。
3. **OAuth2 → Redirects** に `http://127.0.0.1:8000/auth/callback` を追加。Client ID・Client Secretを控える。
4. **OAuth2 → URL Generator** で `bot` と `applications.commands` を選択。Bot権限は `View Channels`、`Send Messages`、`Connect`、`Speak` を設定し、作成したURLから自分のサーバーへ招待。
5. Python 3.11以上、[FFmpeg](https://ffmpeg.org/)、[Deno](https://deno.com/)（YouTube抽出用JS実行環境）をインストール。
6. このフォルダで仮想環境を作り、依存関係をインストール。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

7. `.env.example` を `.env` にコピーし、`DISCORD_TOKEN`、`DISCORD_CLIENT_ID`、`DISCORD_CLIENT_SECRET` を入力。**このファイルは公開GitHubへコミットしないこと。**
8. 起動。

```bash
python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
```

9. `http://127.0.0.1:8000` を開いてDiscordログイン。VCに参加すればリリンが自動参加します。`/testtts` で読み上げ音声を確認できます。

> Windowsは仮想環境の有効化に `.venv\Scripts\activate` を使用。FFmpegとDenoはPATHから実行できる必要があります。

## 設計

```text
app/
  server.py       Discord OAuth2 / Web API / Bot起動
  bot.py          Discord Slash Commands / 新規チャット検知
  player.py       音楽検索 / TTSキュー / 音声合成・ミキサー
  tts_fallback.py Edge Neuralの再試行・タイ語予備音声
  voice_policy.py VC自動参加先とチャット閲覧権限の判定
  tts_text.py     タイ語読み上げ向けDiscordチャット整形
  music_queue.py  順番変更・指定削除・優先キュー管理
  audio_mix.py    48kHz PCM音声の混合
  db.py           SQLite / ユーザー設定 / プレイリスト保存
  static/index.html   タイ語ダッシュボードUI
  static/lilin-avatar.jpg   リリンのキャラクターアイコン
tests/test_core.py      音声混合・保存ロジック検証
```

データは `voice_hub.sqlite3` に保存。OAuthセッションは推測困難なCookie識別子を持ち、トークンはサーバー側で管理します。ログインユーザーが所属するサーバーだけを一覧に表示し、他人のプレイリストを操作できないようにしています。サーバー全体のVC自動参加設定は管理権限が必要です。

## 公開するとき

- **BotとWeb APIは同じ常時起動のPythonサーバー**で実行してください。静的Webホスティング単体ではDiscordのGateway接続が維持できません。
- HTTPSドメインを用意し、Discord OAuth Redirectを公開URLの `/auth/callback` に変更、`.env` の `DISCORD_REDIRECT_URI` と `COOKIE_SECURE=true` を設定してください。
- SQLiteデータは永続ディスクへ保存し、バックアップを取ってください。OAuthトークンを保存するDBは管理者以外が読み取れないようにしてください。
- 複数プロセスで起動しないこと（同一トークンのBotが重複起動します）。
- サーバー規模が大きくなる場合はDiscordのMessage Content Intent審査・レート制限を確認してください。

## YouTube機能の重要な注意

### Wispbyte PremiumのYouTube再生障害

リリンは新しい `yt-dlp` と EJS / Deno を使い、通常のYouTube抽出に失敗した場合に `web_safari`（HLS優先）、`android_vr`、`web_embedded` を順番に試します。FFmpegが取得済みのURLから一切音声を読み込めなければ、`yt-dlp` 自身で最大80MiBまで一時的に音声ファイルをダウンロードして再試行します。ファイルは再生終了・スキップ時に削除します。`/diagnose` でエラーを確認してください。

WispbyteなどのデータセンターIP全体がYouTubeにブロックされていると、この再試行だけでは再生できません。必要であれば、**使い捨ての検証用アカウント**から取得したYouTube Cookieを、Wispbyteサーバー内の非公開ファイル（所有者のみアクセスできる`chmod 600`）に保存し、`YOUTUBE_COOKIES_PATH=data/youtube-cookies.txt` を `.env` に追加できます。認証Cookieはアカウントへのアクセス権を持つ機密情報です。**GitHub、Discord、サポートチケットには絶対に載せないでください。** Cookie利用によるアカウント制限・停止リスクがあるため、普段使いのYouTubeアカウントでは行わないでください。

CookieやPO Tokenを導入しても、YouTubeがサーバーIPからの通信を拒否する場合、成功を保証できません。YouTubeの公式共同視聴が目的ならDiscordのWatch Togetherを利用してください。

このMVPは `yt-dlp` による曲名検索・動画URL解析とFFmpegの音声ストリーミングで動く **技術検証用の実装** です。YouTubeのAPI Servicesポリシーは、動画から音声を分離することやバックグラウンド再生を制限しています。またYouTube側の変更で再生できなくなる可能性があります。正式運用前に利用規約と音源の利用許可を確認し、必要なら再生元を許諾済みの音楽ソースへ置き換えてください。YouTubeの公式な共同視聴はDiscordの **Watch Together** を使えます。

## 制約・確認状況

- 音声ファイルは一時的にTTS用として生成し、再生後に削除します。
- 1サーバーあたりBotが接続するVCは1つ。テキスト読み上げ対象はBotが閲覧できる全チャット（VC内チャットを含む）です。ただしVC内の他の利用者に閲覧権限がないチャンネルの内容は、情報漏洩を防ぐため読み上げません。
- TTSは1人あたり1.2秒の簡易クールダウン、180文字上限、TTSキュー40件、音楽キュー150件です。
- Webから保存リストを再生するには本人がBotと同じVCにいる必要があります。
- 外部サービスの本番アカウントでの音声疎通・OAuth往復・YouTube再生は未検証です。実アカウント接続後に確認が必要です。

## テスト

```bash
python -m unittest discover -s tests -v
```
