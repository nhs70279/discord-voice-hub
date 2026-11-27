"""Discord slash commands. /join immediately enables channel TTS."""

import asyncio
import importlib.util
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
            raise ValueError("ใช้คำสั่งนี้ในเซิร์ฟเวอร์ Discord นะคะ")
        user_voice = interaction.user.voice
        if not user_voice or not user_voice.channel:
            raise ValueError("กรุณาเข้าห้องเสียงก่อนค่ะ")
        guild_id = interaction.guild.id
        existing = self.guild_players.get(guild_id)
        if existing:
            if existing.vc.channel != user_voice.channel:
                if any(not member.bot for member in existing.vc.channel.members):
                    raise ValueError("ลิลินกำลังใช้งานในห้องเสียงอื่นอยู่ค่ะ กรุณาเข้าห้องนั้นก่อนนะคะ")
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
            raise ValueError("พิมพ์ /join เพื่อให้ลิลินเข้าห้องเสียงก่อนค่ะ")
        if not valid_query(query):
            raise ValueError("กรุณาใส่ชื่อเพลงหรือลิงก์ YouTube แบบ HTTPS")
        tracks = await asyncio.to_thread(discover_tracks, query)
        if not tracks:
            raise ValueError("ลิลินหาเพลงนี้ไม่เจอค่ะ")
        items = [Track(track["title"], track["url"], requester) for track in tracks]
        player.add_tracks(items, front=front or now, now=now)
        return len(tracks)

    def player_for_command(self, interaction: discord.Interaction) -> GuildPlayer:
        player = self.guild_players.get(interaction.guild_id)
        if not player:
            raise ValueError("ลิลินยังไม่ได้เข้าห้องเสียงค่ะ")
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or interaction.user.voice.channel != player.vc.channel:
            raise ValueError("กรุณาเข้าห้องเสียงเดียวกับลิลินค่ะ")
        return player


class VoiceCommands(commands.Cog):
    def __init__(self, bot: VoiceHubBot):
        self.bot = bot

    @app_commands.command(name="join", description="ให้ลิลินเข้าห้องเสียงและเริ่มอ่านแชตภาษาไทย")
    @app_commands.describe(channel="ช่องข้อความที่จะอ่าน (เว้นว่างเพื่อใช้ช่องปัจจุบัน)")
    async def join(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await interaction.response.defer(ephemeral=True)
        try:
            player = await self.bot.ensure_player(interaction)
            selected = channel or interaction.channel
            if not selected or not isinstance(selected, (discord.TextChannel, discord.Thread)):
                raise ValueError("กรุณาใช้ /join ในช่องข้อความค่ะ")
            db.set_read_channel(interaction.guild_id, selected.id)
            await interaction.followup.send(f"💜 ลิลินมาแล้วค่ะ! อยู่ที่ {player.vc.channel.mention} และจะอ่านแชตจาก {selected.mention} ให้อัตโนมัตินะคะ", ephemeral=True)
        except Exception as error:
            await interaction.followup.send(str(error), ephemeral=True)

    @app_commands.command(name="testaudio", description="ทดสอบเสียงของลิลินโดยไม่ใช้ YouTube หรือบริการเสียงอ่าน")
    async def test_audio(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            player = await self.bot.ensure_player(interaction)
            player.play_test_tone()
            await interaction.followup.send(
                "🔊 ลิลินกำลังเล่นเสียงทดสอบสั้น ๆ ค่ะ หากไม่ได้ยิน ให้ตรวจสอบเสียงใน Discord หรือใช้ /diagnose",
                ephemeral=True,
            )
        except Exception as error:
            log.warning("Voice test failed: %s", error)
            await interaction.followup.send(f"ทดสอบเสียงไม่สำเร็จ: {error}", ephemeral=True)

    @app_commands.command(name="diagnose", description="ตรวจสอบการเชื่อมต่อเสียงและสถานะเพลงของลิลิน")
    async def diagnose(self, interaction: discord.Interaction):
        player = self.bot.guild_players.get(interaction.guild_id)
        if player is None:
            return await interaction.response.send_message("ลิลินยังไม่ได้เข้าห้องเสียงค่ะ กรุณาใช้ /join ก่อน", ephemeral=True)
        dave = "พร้อม" if importlib.util.find_spec("davey") else "ไม่พบ"
        lines = [
            f"🔎 discord.py: {discord.__version__} / DAVE: {dave}",
            f"🔊 เชื่อมต่อเสียง: {'ใช่' if player.vc.is_connected() else 'ไม่'}",
            f"📡 ส่งเสียง: {'ใช่' if player.vc.is_playing() else 'ไม่'}",
            f"🎵 เพลง: {player.playback_status}",
            f"⚠️ เพลงผิดพลาด: {player.last_music_error or 'ไม่มี'}",
            f"⚠️ เสียงอ่านผิดพลาด: {player.last_tts_error or 'ไม่มี'}",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="play", description="เล่นเพลงด้วยชื่อหรือลิงก์ YouTube หรือเพลย์ลิสต์")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.ensure_player(interaction)
            count = await self.bot.enqueue_query(interaction.guild_id, query, str(interaction.user.id))
            await interaction.followup.send(f"🎵 ลิลินเพิ่มเพลงให้แล้ว {count} เพลงค่ะ", ephemeral=True)
        except Exception as error:
            log.warning("Play failed: %s", error)
            await interaction.followup.send(f"ขอโทษค่ะ เล่นเพลงไม่ได้: {error}", ephemeral=True)

    @app_commands.command(name="next", description="เพิ่มเพลงไว้เล่นถัดจากเพลงปัจจุบัน")
    async def play_next(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            player = self.bot.player_for_command(interaction)
            count = await self.bot.enqueue_query(interaction.guild_id, query, str(interaction.user.id), front=True)
            await interaction.followup.send(f"⏭️ ลิลินจัด {count} เพลงไว้เป็นเพลงถัดไปแล้วค่ะ", ephemeral=True)
        except Exception as error:
            await interaction.followup.send(str(error), ephemeral=True)

    @app_commands.command(name="playnow", description="ข้ามเพลงปัจจุบันแล้วเล่นเพลงใหม่ทันที")
    async def play_now(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            self.bot.player_for_command(interaction)
            count = await self.bot.enqueue_query(interaction.guild_id, query, str(interaction.user.id), now=True)
            await interaction.followup.send(f"▶️ ลิลินจะข้ามเพลงเดิมแล้วเล่น {count} เพลงนี้ให้ทันทีค่ะ", ephemeral=True)
        except Exception as error:
            await interaction.followup.send(str(error), ephemeral=True)

    @app_commands.command(name="skip", description="ข้ามเพลงที่กำลังเล่น")
    async def skip(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.skip()
        await interaction.response.send_message("⏭️ ข้ามเพลงแล้วค่ะ", ephemeral=True)

    @app_commands.command(name="pause", description="พักเพลงชั่วคราว แต่ยังอ่านแชตต่อ")
    async def pause(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.mixer.paused = True
        await interaction.response.send_message("⏸️ ลิลินพักเพลงไว้ก่อนนะคะ", ephemeral=True)

    @app_commands.command(name="resume", description="เล่นเพลงต่อ")
    async def resume(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.mixer.paused = False
        await interaction.response.send_message("▶️ เล่นเพลงต่อแล้วค่ะ", ephemeral=True)

    @app_commands.command(name="queue", description="ดูเพลงที่กำลังเล่นและคิวเพลงถัดไป")
    async def queue(self, interaction: discord.Interaction):
        player = self.bot.guild_players.get(interaction.guild_id)
        if not player:
            return await interaction.response.send_message("ลิลินยังไม่ได้เข้าห้องเสียงค่ะ", ephemeral=True)
        pending = player.music_queue.snapshot()
        names = [f"🎵 กำลังเล่น: {player.current.title if player.current else 'ไม่มี'}", f"📋 รอเล่น: {len(pending)} เพลง"]
        names += [f"{idx}. {t.title}" for idx, t in enumerate(pending[:20], start=1)]
        if len(pending) > 20:
            names.append(f"…เหลืออีก {len(pending) - 20} เพลง")
        if not pending:
            names.append("คิวเพลงว่างอยู่ค่ะ")
        await interaction.response.send_message("\n".join(names), ephemeral=True)

    @app_commands.command(name="remove", description="ลบเพลงตามหมายเลขออกจากคิว")
    @app_commands.describe(number="หมายเลขเพลงจาก /queue ไม่รวมเพลงที่กำลังเล่น")
    async def remove(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 150]):
        try:
            player = self.bot.player_for_command(interaction)
            track = player.music_queue.remove(number)
            await interaction.response.send_message(f"🗑️ ลบเพลงหมายเลข {number}: {track.title} แล้วค่ะ", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="move", description="เปลี่ยนลำดับเพลงในคิว")
    async def move(self, interaction: discord.Interaction, from_number: app_commands.Range[int, 1, 150], to_number: app_commands.Range[int, 1, 150]):
        try:
            player = self.bot.player_for_command(interaction)
            player.music_queue.move(from_number, to_number)
            await interaction.response.send_message(f"🔀 ย้ายเพลงจากลำดับ {from_number} ไป {to_number} แล้วค่ะ", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="clear", description="ล้างคิวเพลงรอโดยไม่หยุดเพลงปัจจุบัน")
    async def clear(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
            count = len(player.music_queue)
            player.music_queue.clear()
            await interaction.response.send_message(f"🧹 ลบเพลงรอเล่น {count} เพลงแล้วค่ะ เพลงปัจจุบันยังเล่นต่อ", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="shuffle", description="สุ่มลำดับเพลงในคิว")
    async def shuffle(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
            player.music_queue.shuffle()
            await interaction.response.send_message("🔀 สุ่มลำดับเพลงเรียบร้อยค่ะ", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="repeat", description="ตั้งค่าการเล่นเพลงซ้ำ")
    @app_commands.choices(mode=[
        app_commands.Choice(name="ปิด", value="off"),
        app_commands.Choice(name="เล่นซ้ำเพลงเดียว", value="one"),
        app_commands.Choice(name="เล่นซ้ำทั้งคิว", value="all"),
    ])
    async def repeat(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        try:
            player = self.bot.player_for_command(interaction)
            player.repeat_mode = mode.value
            await interaction.response.send_message(f"🔁 ตั้งค่าเล่นซ้ำ: {mode.name} แล้วค่ะ", ephemeral=True)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)

    @app_commands.command(name="nowplaying", description="ดูเพลงที่กำลังเล่น")
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.bot.guild_players.get(interaction.guild_id)
        if not player or not player.current:
            return await interaction.response.send_message("ตอนนี้ยังไม่มีเพลงที่กำลังเล่นค่ะ", ephemeral=True)
        await interaction.response.send_message(f"🎶 {player.current.title}\nขอเพลงโดย: <@{player.current.requester}>", ephemeral=True)

    @app_commands.command(name="stop", description="หยุดเพลงและล้างคิว แต่ยังอ่านแชตต่อ")
    async def stop(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.stop_music()
        await interaction.response.send_message("⏹️ ลิลินหยุดเพลงแล้วค่ะ", ephemeral=True)

    @app_commands.command(name="volume", description="ปรับระดับเสียงเพลงระหว่าง 0 ถึง 100")
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 100]):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.mixer.music_volume = percent / 100
        await interaction.response.send_message(f"ระดับเสียงเพลง: {percent}%", ephemeral=True)

    @app_commands.command(name="leave", description="ให้ลิลินออกจากห้องเสียง")
    async def leave(self, interaction: discord.Interaction):
        try:
            self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        await self.bot.leave_guild(interaction.guild_id)
        await interaction.response.send_message("👋 ลิลินไปก่อนนะคะ หยุดอ่านแชตแล้วค่ะ 💜", ephemeral=True)

    @app_commands.command(name="voice", description="เลือกเสียงอ่านประจำตัวของลิลิน")
    @app_commands.choices(voice=[app_commands.Choice(name=label, value=key) for key, label in db.VOICE_CHOICES.items()])
    async def voice(self, interaction: discord.Interaction, voice: app_commands.Choice[str]):
        db.set_voice(interaction.user.id, voice.value)
        await interaction.response.send_message(f"เสียงอ่านของลิลิน: {voice.name} 💜", ephemeral=True)
