"""Discord slash commands. /join immediately enables channel TTS."""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from . import db
from .player import GuildPlayer, Track, discover_tracks, speech_text, valid_query

log = logging.getLogger(__name__)


class VoiceHubBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.guild_players: dict[int, GuildPlayer] = {}
        self.last_message: dict[tuple[int, int], float] = {}

    async def setup_hook(self):
        await self.add_cog(VoiceCommands(self))
        await self.tree.sync()

    async def on_ready(self):
        log.info("Logged into Discord as %s", self.user)

    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not message.content:
            return
        player = self.guild_players.get(message.guild.id)
        if not player or not player.vc.is_connected():
            return
        if db.get_read_channel(message.guild.id) != message.channel.id:
            return
        # Per-author, per-guild cooldown: spam should not flood TTS.
        key = (message.guild.id, message.author.id)
        now = asyncio.get_running_loop().time()
        if now - self.last_message.get(key, 0) < 1.2:
            return
        self.last_message[key] = now
        spoken = speech_text(message.author.display_name[:32], message.content)
        if spoken:
            await player.add_speech(spoken, db.get_voice_config(message.author.id))

    async def ensure_player(self, interaction: discord.Interaction) -> GuildPlayer:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise ValueError("サーバー内で使用してください。")
        user_voice = interaction.user.voice
        if not user_voice or not user_voice.channel:
            raise ValueError("先にボイスチャンネルへ参加してください。")
        guild_id = interaction.guild.id
        existing = self.guild_players.get(guild_id)
        if existing:
            if existing.vc.channel != user_voice.channel:
                if any(not member.bot for member in existing.vc.channel.members):
                    raise ValueError("Botは別のVCで使用中です。現在のVCに参加するか、Botを退出させてください。")
                await existing.vc.move_to(user_voice.channel)
            return existing
        vc = interaction.guild.voice_client
        if vc and vc.channel != user_voice.channel:
            await vc.move_to(user_voice.channel)
        elif vc is None:
            vc = await user_voice.channel.connect()
        try:
            player = GuildPlayer(vc, asyncio.get_running_loop())
        except Exception:
            await vc.disconnect(force=True)
            raise
        self.guild_players[guild_id] = player
        return player

    async def leave_guild(self, guild_id: int):
        player = self.guild_players.pop(guild_id, None)
        if player:
            await player.shutdown()

    async def close(self):
        for guild_id in list(self.guild_players):
            try:
                await self.leave_guild(guild_id)
            except Exception:
                log.exception("Disconnecting from guild %s failed", guild_id)
        await super().close()

    async def enqueue_query(self, guild_id: int, query: str, requester: str, *, front: bool = False, now: bool = False) -> int:
        player = self.guild_players.get(guild_id)
        if not player:
            raise ValueError("先に /join でBotをVCに参加させてください。")
        if not valid_query(query):
            raise ValueError("曲名またはYouTubeのHTTPS URLを指定してください。")
        tracks = await asyncio.to_thread(discover_tracks, query)
        if not tracks:
            raise ValueError("曲が見つかりませんでした。")
        items = [Track(track["title"], track["url"], requester) for track in tracks]
        player.add_tracks(items, front=front or now, now=now)
        return len(tracks)

    def player_for_command(self, interaction: discord.Interaction) -> GuildPlayer:
        player = self.guild_players.get(interaction.guild_id)
        if not player:
            raise ValueError("BotはVCにいません。")
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or interaction.user.voice.channel != player.vc.channel:
            raise ValueError("Botと同じVCに参加してください。")
        return player


class VoiceCommands(commands.Cog):
    def __init__(self, bot: VoiceHubBot):
        self.bot = bot

    @app_commands.command(name="join", description="VCに参加し、チャット読み上げを自動開始")
    @app_commands.describe(channel="読み上げるテキストチャンネル（省略すると現在のチャンネル）")
    async def join(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await interaction.response.defer(ephemeral=True)
        try:
            player = await self.bot.ensure_player(interaction)
            selected = channel or interaction.channel
            if not selected or not isinstance(selected, (discord.TextChannel, discord.Thread)):
                raise ValueError("テキストチャンネルで /join を実行してください。")
            db.set_read_channel(interaction.guild_id, selected.id)
            await interaction.followup.send(f"🔊 {player.vc.channel.mention} に参加！ {selected.mention} のチャットを自動で読み上げます。", ephemeral=True)
        except Exception as error:
            await interaction.followup.send(str(error), ephemeral=True)

    @app_commands.command(name="play", description="曲名・YouTube URL・プレイリストURLで再生予約")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.ensure_player(interaction)
            count = await self.bot.enqueue_query(interaction.guild_id, query, str(interaction.user.id))
            await interaction.followup.send(f"🎵 {count}曲を再生キューに追加しました。", ephemeral=True)
        except Exception as error:
            log.warning("Play failed: %s", error)
            await interaction.followup.send(f"再生できませんでした：{error}", ephemeral=True)

    @app_commands.command(name="next", description="現在の曲はそのまま、指定した曲を次に再生")
    async def play_next(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            player = self.bot.player_for_command(interaction)
            count = await self.bot.enqueue_query(interaction.guild_id, query, str(interaction.user.id), front=True)
            await interaction.followup.send(f"⏭️ {count}曲をキューの先頭に追加しました。現在の曲が終わると再生します。", ephemeral=True)
        except Exception as error:
            await interaction.followup.send(str(error), ephemeral=True)

    @app_commands.command(name="playnow", description="現在の曲を切り替え、指定した曲をすぐ再生")
    async def play_now(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            self.bot.player_for_command(interaction)
            count = await self.bot.enqueue_query(interaction.guild_id, query, str(interaction.user.id), now=True)
            await interaction.followup.send(f"▶️ {count}曲を最優先にしました。現在の曲をスキップして再生します。", ephemeral=True)
        except Exception as error:
            await interaction.followup.send(str(error), ephemeral=True)

    @app_commands.command(name="skip", description="現在の曲をスキップ")
    async def skip(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.skip()
        await interaction.response.send_message("⏭️ スキップしました。", ephemeral=True)

    @app_commands.command(name="pause", description="音楽だけ一時停止（読み上げは継続）")
    async def pause(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.mixer.paused = True
        await interaction.response.send_message("⏸️ 音楽を一時停止しました。", ephemeral=True)

    @app_commands.command(name="resume", description="音楽を再開")
    async def resume(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.mixer.paused = False
        await interaction.response.send_message("▶️ 音楽を再開しました。", ephemeral=True)

    @app_commands.command(name="queue", description="現在の曲と再生待ちを確認")
    async def queue(self, interaction: discord.Interaction):
        player = self.bot.guild_players.get(interaction.guild_id)
        if not player:
            return await interaction.response.send_message("BotはVCにいません。", ephemeral=True)
        pending = player.music_queue.snapshot()
        names = [f"🎵 再生中：{player.current.title if player.current else 'なし'}", f"📋 再生待ち：{len(pending)}曲"]
        names += [f"{idx}. {t.title}" for idx, t in enumerate(pending[:20], start=1)]
        if len(pending) > 20:
            names.append(f"…残り{len(pending) - 20}曲")
        if not pending:
            names.append("キューは空です。")
        await interaction.response.send_message("\n".join(names), ephemeral=True)

    @app_commands.command(name="remove", description="再生待ちキューから指定番号の曲を削除")
    @app_commands.describe(number="/queue に表示された番号（現在再生中の曲は含まない）")
    async def remove(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 150]):
        try:
            player = self.bot.player_for_command(interaction)
            track = player.music_queue.remove(number)
            await interaction.response.send_message(f"🗑️ {number}番の「{track.title}」を削除しました。", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="move", description="再生待ちキュー内の曲を並び替え")
    async def move(self, interaction: discord.Interaction, from_number: app_commands.Range[int, 1, 150], to_number: app_commands.Range[int, 1, 150]):
        try:
            player = self.bot.player_for_command(interaction)
            player.music_queue.move(from_number, to_number)
            await interaction.response.send_message(f"🔀 {from_number}番を{to_number}番へ移動しました。", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="clear", description="現在の曲は止めず、再生待ちキューだけを空にする")
    async def clear(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
            count = len(player.music_queue)
            player.music_queue.clear()
            await interaction.response.send_message(f"🧹 再生待ち{count}曲を削除しました。現在の曲は継続します。", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="shuffle", description="再生待ちの曲順をランダムに変更")
    async def shuffle(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
            player.music_queue.shuffle()
            await interaction.response.send_message("🔀 再生待ちの曲順をシャッフルしました。", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="repeat", description="リピート方法を設定")
    @app_commands.choices(mode=[
        app_commands.Choice(name="オフ", value="off"),
        app_commands.Choice(name="現在の曲を繰り返す", value="one"),
        app_commands.Choice(name="キュー全体を繰り返す", value="all"),
    ])
    async def repeat(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        try:
            player = self.bot.player_for_command(interaction)
            player.repeat_mode = mode.value
            await interaction.response.send_message(f"🔁 リピート：{mode.name}", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="nowplaying", description="現在再生中の曲を確認")
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.bot.guild_players.get(interaction.guild_id)
        if not player or not player.current:
            return await interaction.response.send_message("現在再生している曲はありません。", ephemeral=True)
        await interaction.response.send_message(f"🎶 {player.current.title}\nリクエスト：<@{player.current.requester}>", ephemeral=True)

    @app_commands.command(name="stop", description="音楽を停止してキューを消去（読み上げは継続）")
    async def stop(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.stop_music()
        await interaction.response.send_message("⏹️ 音楽を停止しました。", ephemeral=True)

    @app_commands.command(name="volume", description="音楽の音量を0〜100で設定")
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 100]):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.mixer.music_volume = percent / 100
        await interaction.response.send_message(f"音楽音量：{percent}%", ephemeral=True)

    @app_commands.command(name="leave", description="BotをVCから退出させる")
    async def leave(self, interaction: discord.Interaction):
        try:
            self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        await self.bot.leave_guild(interaction.guild_id)
        await interaction.response.send_message("👋 退出しました。読み上げも停止しました。", ephemeral=True)

    @app_commands.command(name="voice", description="自分のタイ語読み上げボイスを変更")
    @app_commands.choices(voice=[app_commands.Choice(name=label, value=key) for key, label in db.VOICE_CHOICES.items()])
    async def voice(self, interaction: discord.Interaction, voice: app_commands.Choice[str]):
        db.set_voice(interaction.user.id, voice.value)
        await interaction.response.send_message(f"読み上げボイス：{voice.name}", ephemeral=True)
