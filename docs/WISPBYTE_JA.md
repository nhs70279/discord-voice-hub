# Wispbyte Freeでリリンを24時間運用する

対象: [リリン（ลิลิน）](https://github.com/nhs70279/discord-voice-hub) のDiscord Gateway・タイ語TTS・音楽再生・タイ語Web管理画面を**無料のPythonサーバー1台**で起動する。

> **状態:** Wispbyte用の起動ファイル・依存関係・手順を準備済み。実際のWispbyteサーバー作成、音声疎通、YouTubeの再生、OAuthログインは未実施。無料ホストではCPU/メモリや共有IPの制約があるため、音声を含めた正常動作を保証するものではない。

## 無料プランの条件

Wispbyteの案内ではメモリ512MB、通常時CPUの約35%、ディスク1GB、1サーバー/アカウント。カード登録不要・月額0円で、**14日以内に管理画面へ1度ログイン**する限りアイドル時も止まらない。14日間ログインがないとサービスは停止する。

- [Wispbyte公式サイト](https://wispbyte.com/)
- [公式: Getting Started](https://wispbyte.com/kb/getting-started)
- [公式: Startup Settings](https://wispbyte.com/kb/startup-settings)
- [公式: Subdomains（ベータ）](https://wispbyte.com/kb/subdomains)

## 1. Wispbyteで無料Pythonサーバーを作る

1. [Wispbyte Client Panel](https://wispbyte.com/client) に登録してメール認証する。クレジットカード不要。
2. **Create Server → Free Plan** を選択。サーバー名 `lilin-voice-hub`、ランタイム **Python**（通常はPython 3.11系）を選択し、1台だけ作成する。有料プランやMinecraft用サーバーを選ばない。
3. **Files** からこのGitHubリポジトリのファイルをアップロードする。GitHub連携・Cloneがパネルに表示されている場合は `nhs70279/discord-voice-hub` の `main` ブランチを使用。インストール時にトップ階層へ `requirements.wispbyte.txt` と `start_wispbyte.py`、`app/` ディレクトリがあることを確認する。
4. **Startup** で、次の項目を設定する。項目名は選択したPythonイメージによって異なることがある。

   | 項目 | 設定値 |
   | --- | --- |
   | `PY_FILE` | `start_wispbyte.py` |
   | `REQUIREMENTS_FILE` | `requirements.wispbyte.txt` |
   | `PY_PACKAGES` | 空欄（requirementsから読み込む） |
   | `AUTO_UPDATE` | GitHub連携を使う場合のみ必要に応じて有効 |

   起動コマンドを直接編集するUIであれば `python3 start_wispbyte.py` とする。**プロセスを二重起動しない。** WispbyteのPython Eggが`requirements.wispbyte.txt`をインストールしない場合は、StartupのAdditional Python Packages、またはコンソールから`pip install -r requirements.wispbyte.txt`で依存パッケージをインストールする。

## 2. Web管理画面のHTTPSアドレス

1. Wispbyteのサーバーの **Subdomains（Beta）** から無料のサブドメインを有効にする。HTTPS証明書の準備が完了し、公開URLが開けることを確認する。
2. サブドメインは、Wispbyteがサーバーに割り当てた**Primary Port**へ接続される。このプロジェクトの`start_wispbyte.py`は`SERVER_PORT`（なければ`PORT`）を読み、FastAPIを`0.0.0.0`で起動する。URLに追加の`:ポート番号`を付けない。
3. `/healthz` がHTTP 200を返すことを確認する。Discord Token未設定なら`discord_connected:false`になるのは正常。

サブドメインがBetaであることに注意。SSLエラーや502/503/526が発生したらWispbyte側のSubdomainとPrimary Portの対応を確認する。パスワードやBot TokenをURLへ含めない。

## 3. Discord認証を設定

1. [Discord Developer Portal](https://discord.com/developers/applications) → リリンのアプリケーション → **OAuth2 → Redirects** に `https://実際のWispbyteサブドメイン/auth/callback` を登録する。
2. **Startup → Environment Variables** から以下を設定する。パネルが任意の環境変数に対応しない場合は、プロジェクトのトップ階層に**自分だけが閲覧できる**`.env`を作り、同名の値を記入する。**GitHubには絶対にコミットしない。**

   | Key | Value |
   | --- | --- |
   | `DISCORD_TOKEN` | 既存のBot Token（秘密） |
   | `DISCORD_CLIENT_ID` | リリンのDiscord Application ID |
   | `DISCORD_CLIENT_SECRET` | OAuth Client Secret（秘密） |
   | `DISCORD_REDIRECT_URI` | `https://実際のドメイン/auth/callback` |
   | `COOKIE_SECURE` | `true` |
   | `DATABASE_PATH` | 省略可。既定で`/home/container/data/voice_hub.sqlite3`相当 |

3. 旧Fly.ioの同じBot Tokenを使用しているプロセスが停止していることを確認してから、Wispbyteの**Start**を押す。
4. `/healthz` の`discord_connected`が`true`になり、Discordでリリンがオンラインになるのを確認。WebのDiscord OAuthログインとプレイリスト表示を確認する。

## 4. 音声再生用のFFmpegとDeno

Wispbyteの無料Pythonイメージはroot権限がないので`apt install ffmpeg`を前提にできない。このリポジトリでは、Wispbyte専用`requirements.wispbyte.txt`に`imageio-ffmpeg`を追加してFFmpeg実行ファイルを提供する。

`start_wispbyte.py`は次を行う。

- システムに`ffmpeg`がなければ、`imageio-ffmpeg`の実行ファイルに`.runtime/ffmpeg`というリンクを作る。
- `deno`がなければ公式リリースのx86_64版をダウンロードし、公開SHA-256を照合して`.runtime/deno`に保存する。次回以降は再利用する。ダウンロードに失敗してもDiscord Bot自体は起動を試みるが、YouTube検索・再生が失敗する可能性がある。
- SQLiteを`data/voice_hub.sqlite3`に保存し、起動コマンド1つでBotとWeb管理画面を動かす。

**512MB RAMで確実に動くかは未検証。** FFmpegとDeno、Discord DAVE暗号化・Opus共有ライブラリを無料コンテナが動かせるか、以下の順番で音声を確認する。

1. `/diagnose` でDAVE、ボイス接続、音声状態を確認。
2. `/testaudio` で音声経路のテストトーンを確認。
3. `/testtts` でタイ語読み上げを確認。
4. `/play` で音楽を流して3分以上再生できるか確認。
5. WebでOAuthログインと設定変更・プレイリスト作成を確認。

Wispbyteの無料ノードではDiscordの共有IPが原因のHTTP 429も報告されている。リリンのOAuthが429になる場合、ログを見て再試行まで待つ必要がある。無料ノードのIP変更や完全な429回避は保証できない。

## 5. Fly.ioからSQLiteを移す

Fly.io側のアプリとVolumeを**まだ削除しない**。OAuthトークンも含むDBのバックアップは公開・GitHubへコミット・チャットへ貼り付けない。

1. Fly側を停止し、既存の`python -m app.backup`でSQLiteバックアップを取得して自分の端末にダウンロードする。
2. Wispbyteの**Files**で`data/`フォルダに`voice_hub_import.sqlite3`としてバックアップをアップロードする（通常の1GBディスク枠に注意）。
3. Botを停止した状態で、既存の`data/voice_hub.sqlite3`を別名に保管する。インポートファイルのSQLite整合性確認が終わってから`voice_hub.sqlite3`へ名称を変更する。
4. Wispbyteを再起動してプレイリストとユーザー個別設定を確認する。成功後もしばらく旧Fly Volumeは残しておく。

## 6. 継続運用

- Wispbyte Client Panelへ**少なくとも14日ごとにログイン**する。無料枠内ではBotの稼働時間自体による追加料金はない。
- Discord Botの再起動やログイン時の429が出たら、連続再起動を避け、時間をおいて再試行する。
- CPU 35%とRAM 512MBが上限に近い場合、同時再生やTTSの処理を減らす。有料プランには勝手に切り替わらない。
- `data/`と`.runtime/`はGit管理しない。重要なDBは定期的に**管理者の端末へ**保管する。無料ホスト上のファイルを唯一のバックアップにしない。
