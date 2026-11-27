# Voice Hub — Discord読み上げ＆音楽Bot

> **Fly.io無料枠向けの新しい構成：** [Fly.ioデプロイ手順（日本語）](docs/FLY_IO_JA.md)。`Dockerfile`・`fly.toml`・`.env.fly.example`・SQLiteバックアップ機能を追加しました。Oracle Cloud版の手順も引き続き使えます。

> **Oracle Cloud Always Free + Cloudflare Tunnelで24時間稼働したい場合：** [タイ語のセットアップ手順](docs/ORACLE_CLOUDFLARE_TH.md) を参照してください。`deploy/oracle-install.sh` はUbuntu 24.04 ARM64用のインストーラー、`deploy/voicehub.service` は自動起動用のsystemdユニット、`.env.oracle.example` は本番用設定テンプレートです。Discord・Oracle・Cloudflareのアカウントでの実運用テストは未実施です。

Discordで `/join` すると、BotがVCに入り、そのコマンドを実行したテキストチャンネルの読み上げを自動開始します。`/play` は曲名・YouTube動画URL・YouTubeプレイリストURL（最大30件）に対応し、Web画面から個人別ボイスと保存プレイリストを管理できます。

## できること

| 操作 | 内容 |
| --- | --- |
| `/join` | VCに参加／指定したテキストチャンネルを自動読み上げ |
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
| `/voice` | 自分の読み上げ音声をDiscordから変更 |
| `/leave` | Bot退出・読み上げ終了 |
| Webダッシュボード | Discordログイン・タイ語ボイス変更・音楽キューの追加／優先再生／指定削除／並び替え／操作、プレイリスト作成／曲追加／削除／再生・管理者による読み上げチャンネル設定 |

読み上げ中は音楽の音量を自動で約28%に下げ、終わると元に戻します。キューを読み上げ用と音楽用に分けてあるので、読み上げと音楽は共存します。**読み上げ音声はタイ語（th-TH）のみ**。個人ごとに女性声／男性声、話す速さ（−40〜＋40%）、声の高さ（−30〜＋30 Hz）を保存できます。日本語・英語のボイス選択はありません。文章の自動翻訳は行いません。

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

9. `http://127.0.0.1:8000` を開いてDiscordログイン。Discordのテキストチャンネルで `/join` を実行し、読み上げを確認。

> Windowsは仮想環境の有効化に `.venv\Scripts\activate` を使用。FFmpegとDenoはPATHから実行できる必要があります。

## 設計

```text
app/
  server.py       Discord OAuth2 / Web API / Bot起動
  bot.py          Discord Slash Commands / 新規チャット検知
  player.py       音楽検索 / TTSキュー / 音声合成・ミキサー
  music_queue.py  順番変更・指定削除・優先キュー管理
  audio_mix.py    48kHz PCM音声の混合
  db.py           SQLite / ユーザー設定 / プレイリスト保存
  static/index.html   ダッシュボードUI
tests/test_core.py      音声混合・保存ロジック検証
```

データは `voice_hub.sqlite3` に保存。OAuthセッションは推測困難なCookie識別子を持ち、トークンはサーバー側で管理します。ログインユーザーが所属するサーバーだけを一覧に表示し、他人のプレイリストを操作できないようにしています。サーバー全体の読み上げチャンネル変更は管理権限が必要です。

## 公開するとき

- **BotとWeb APIは同じ常時起動のPythonサーバー**で実行してください。静的Webホスティング単体ではDiscordのGateway接続が維持できません。
- HTTPSドメインを用意し、Discord OAuth Redirectを公開URLの `/auth/callback` に変更、`.env` の `DISCORD_REDIRECT_URI` と `COOKIE_SECURE=true` を設定してください。
- SQLiteデータは永続ディスクへ保存し、バックアップを取ってください。OAuthトークンを保存するDBは管理者以外が読み取れないようにしてください。
- 複数プロセスで起動しないこと（同一トークンのBotが重複起動します）。
- サーバー規模が大きくなる場合はDiscordのMessage Content Intent審査・レート制限を確認してください。

## YouTube機能の重要な注意

このMVPは `yt-dlp` による曲名検索・動画URL解析とFFmpegの音声ストリーミングで動く **技術検証用の実装** です。YouTubeのAPI Servicesポリシーは、動画から音声を分離することやバックグラウンド再生を制限しています。またYouTube側の変更で再生できなくなる可能性があります。正式運用前に利用規約と音源の利用許可を確認し、必要なら再生元を許諾済みの音楽ソースへ置き換えてください。YouTubeの公式な共同視聴はDiscordの **Watch Together** を使えます。

## 制約・確認状況

- 音声ファイルは一時的にTTS用として生成し、再生後に削除します。
- 1サーバーあたりBotが接続するVCは1つ。読み上げ対象チャンネルも1つです。
- TTSは1人あたり1.2秒の簡易クールダウン、180文字上限、TTSキュー40件、音楽キュー150件です。
- Webから保存リストを再生するには本人がBotと同じVCにいる必要があります。
- 外部サービスの本番アカウントでの音声疎通・OAuth往復・YouTube再生は未検証です。実アカウント接続後に確認が必要です。

## テスト

```bash
python -m unittest discover -s tests -v
```
