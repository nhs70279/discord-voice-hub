"""Discord slash commands. /join immediately enables channel TTS."""

import asyncio
import importlib.metadata
import logging
import os
import shutil
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from . import db
from .player import GuildPlayer, Track, discover_tracks, speech_text, valid_query
from .voice_policy import occupied_channel, visible_to_voice_listeners

log = logging.getLogger(__name__)


class VoiceHubBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.guild_players: dict[int, GuildPlayer] = {}
        self.last_message: dict[tuple[int, int], float] = {}
        self._voice_locks: dict[int, asyncio.Lock] = {}
        self._auto_join_tasks: dict[int, asyncio.Task] = {}

    async def setup_hook(self):
        await self.add_cog(VoiceCommands(self))
        await self.tree.sync()

    async def on_ready(self):
        log.info("Logged into Discord as %s", self.user)
        for guild in self.guilds:
            self.schedule_auto_join(guild)

    def schedule_auto_join(self, guild: discord.Guild):
        existing = self._auto_join_tasks.get(guild.id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(self._auto_join_guild(guild), name=f"autojoin-{guild.id}")
        self._auto_join_tasks[guild.id] = task
        task.add_done_callback(lambda finished: self._auto_join_finished(guild.id, finished))

    def _auto_join_finished(self, guild_id: int, task: asyncio.Task):
        if self._auto_join_tasks.get(guild_id) is task:
            self._auto_join_tasks.pop(guild_id, None)
        self._log_auto_join_result(task)

    @staticmethod
    def _log_auto_join_result(task: asyncio.Task):
        if not task.cancelled() and task.exception():
            log.error("Automatic voice join failed: %r", task.exception())

    async def _connect_to_voice(self, channel: discord.VoiceChannel, *, prioritize: bool = False) -> GuildPlayer:
        guild = channel.guild
        lock = self._voice_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            player = self.guild_players.get(guild.id)
            if player and player.vc.is_connected():
                if player.vc.channel != channel:
                    if not prioritize and occupied_channel(guild, player.vc.channel) == player.vc.channel:
                        return player
                    await player.vc.move_to(channel)
                return player
            if player:
                self.guild_players.pop(guild.id, None)
                await player.shutdown()
            vc = guild.voice_client
            if vc and not vc.is_connected():
                await vc.disconnect(force=True)
                vc = None
            if vc:
                if vc.channel != channel:
                    await vc.move_to(channel)
            else:
                vc = await channel.connect()
            try:
                player = GuildPlayer(vc, asyncio.get_running_loop())
            except Exception:
                await vc.disconnect(force=True)
                raise
            self.guild_players[guild.id] = player
            return player

    async def _auto_join_guild(self, guild: discord.Guild):
        if not db.get_auto_join(guild.id):
            return
        player = self.guild_players.get(guild.id)
        current = player.vc.channel if player and player.vc.is_connected() else None
        target = occupied_channel(guild, current)
        if target is not None:
            await self._connect_to_voice(target)
        elif player:
            await self.leave_guild(guild.id)

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or before.channel == after.channel:
            return
        try:
            await self._auto_join_guild(member.guild)
        except Exception:
            log.exception("Could not auto join/move in guild %s", member.guild.id)

    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        player = self.guild_players.get(message.guild.id)
        if db.get_auto_join(message.guild.id):
            author_voice = getattr(getattr(message.author, "voice", None), "channel", None)
            if isinstance(author_voice, discord.VoiceChannel) and (not player or not player.vc.is_connected() or player.vc.channel != author_voice):
                try:
                    player = await self._connect_to_voice(author_voice, prioritize=True)
                except Exception:
                    log.exception("Could not follow chat author into voice in guild %s", message.guild.id)
        if not player or not player.vc.is_connected():
            return
        player.chat_events_seen += 1
        if not message.content:
            player.chat_events_without_content += 1
            return
        player.chat_events_in_read_channel += 1
        if not visible_to_voice_listeners(message.channel, player.vc.channel):
            player.chat_messages_private_skipped += 1
            return
        # Per-author, per-guild cooldown: spam should not flood TTS.
        key = (message.guild.id, message.author.id)
        now = asyncio.get_running_loop().time()
        if now - self.last_message.get(key, 0) < 1.2:
            return
        self.last_message[key] = now
        spoken = speech_text(message.author.display_name[:32], message.content)
        if spoken:
            if await player.add_speech(spoken, db.get_voice_config(message.author.id)):
                player.chat_messages_enqueued += 1

    async def ensure_player(self, interaction: discord.Interaction) -> GuildPlayer:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise ValueError("ใช้คำสั่งนี้ในเซิร์ฟเวอร์ Discord นะคะ")
        user_voice = interaction.user.voice
        if not user_voice or not user_voice.channel:
            raise ValueError("กรุณาเข้าห้องเสียงก่อนค่ะ")
        if not isinstance(user_voice.channel, discord.VoiceChannel):
            raise ValueError("กรุณาเข้าห้องเสียงปกติก่อนค่ะ")
        return await self._connect_to_voice(user_voice.channel, prioritize=True)

    async def leave_guild(self, guild_id: int):
        lock = self._voice_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            player = self.guild_players.pop(guild_id, None)
            if player:
                await player.shutdown()

    async def close(self):
        tasks = list(self._auto_join_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
            raise ValueError("กรุณาใส่ชื่อเพลงหรือลิงก์ YouTube หรือ SoundCloud แบบ HTTPS")
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

    @app_commands.command(name="join", description="ให้ลิลินเข้าห้องเสียงและอ่านแชตภาษาไทยจากทุกช่อง")
    async def join(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            player = await self.bot.ensure_player(interaction)
            await interaction.followup.send(f"💜 ลิลินมาแล้วค่ะ! อยู่ที่ {player.vc.channel.mention} และจะอ่านแชตจากทุกช่อง รวมถึงแชตในห้องเสียงค่ะ", ephemeral=True)
        except Exception as error:
            await interaction.followup.send(str(error), ephemeral=True)

    @app_commands.command(name="autojoin", description="เปิดหรือปิดการเข้าห้องเสียงอัตโนมัติของลิลิน")
    @app_commands.default_permissions(manage_guild=True)
    async def autojoin(self, interaction: discord.Interaction, enabled: bool):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("ต้องมีสิทธิ์จัดการเซิร์ฟเวอร์ค่ะ", ephemeral=True)
        db.set_auto_join(interaction.guild.id, enabled)
        if enabled:
            self.bot.schedule_auto_join(interaction.guild)
        await interaction.response.send_message(
            "💜 เปิดการเข้าห้องเสียงอัตโนมัติแล้วค่ะ" if enabled else "ปิดการเข้าห้องเสียงอัตโนมัติแล้วค่ะ",
            ephemeral=True,
        )

    @app_commands.command(name="testaudio", description="ทดสอบเสียงของลิลินโดยไม่ใช้ YouTube หรือบริการเสียงอ่าน")
    async def test_audio(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            player = await self.bot.ensure_player(interaction)
            await interaction.followup.send(
                "🔊 ลิลินจะเล่นเสียงทดสอบ 4 วินาทีตอนนี้ค่ะ หากไม่ได้ยิน ให้ตรวจสอบเสียงใน Discord หรือใช้ /diagnose",
                ephemeral=True,
            )
            player.play_test_tone()
        except Exception as error:
            log.warning("Voice test failed: %s", error)
            await interaction.followup.send(f"ทดสอบเสียงไม่สำเร็จ: {error}", ephemeral=True)

    @app_commands.command(name="testtts", description="ทดสอบเสียงพูดภาษาไทยของลิลินโดยไม่ใช้ YouTube")
    async def test_tts(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            player = await self.bot.ensure_player(interaction)
            queued = await player.add_speech(
                "สวัสดีค่ะ ฉันชื่อลิลิน ตอนนี้กำลังทดสอบเสียงอ่านภาษาไทยค่ะ",
                db.get_voice_config(interaction.user.id),
            )
            if not queued:
                raise ValueError("คิวเสียงอ่านเต็ม กรุณารอสักครู่ค่ะ")
            await interaction.followup.send(
                "🎙️ กำลังสร้างเสียงอ่านภาษาไทยค่ะ หากไม่ได้ยิน กรุณารอประมาณ 10–20 วินาทีแล้วใช้ /diagnose ค่ะ",
                ephemeral=True,
            )
        except Exception as error:
            log.warning("TTS test failed: %s", error)
            await interaction.followup.send(f"ทดสอบเสียงอ่านไม่สำเร็จ: {error}", ephemeral=True)

    @app_commands.command(name="diagnose", description="ตรวจสอบการเชื่อมต่อเสียงและสถานะเพลงของลิลิน")
    async def diagnose(self, interaction: discord.Interaction):
        player = self.bot.guild_players.get(interaction.guild_id)
        if player is None:
            return await interaction.response.send_message("ลิลินยังไม่ได้เข้าห้องเสียงค่ะ กรุณาใช้ /join ก่อน", ephemeral=True)
        try:
            dave_version = importlib.metadata.version("davey")
        except importlib.metadata.PackageNotFoundError:
            dave_version = "ไม่พบ"
        try:
            edge_version = importlib.metadata.version("edge-tts")
        except importlib.metadata.PackageNotFoundError:
            edge_version = "ไม่พบ"
        try:
            youtube_version = importlib.metadata.version("yt-dlp")
        except importlib.metadata.PackageNotFoundError:
            youtube_version = "ไม่พบ"
        try:
            ejs_version = importlib.metadata.version("yt-dlp-ejs")
        except importlib.metadata.PackageNotFoundError:
            ejs_version = "ไม่พบ"
        voice_state = getattr(player.vc, "_connection", None)
        dave_session = getattr(voice_state, "dave_session", None)
        dave_ready = bool(dave_session and getattr(dave_session, "ready", False))
        dave_protocol = getattr(voice_state, "dave_protocol_version", 0)
        lines = [
            f"🔎 discord.py: {discord.__version__} / davey: {dave_version}",
            f"🎙️ edge-tts: {edge_version}",
            f"🎵 YouTube extractor: {youtube_version} / EJS: {ejs_version} / Deno: {'พร้อม' if shutil.which('deno') else 'ไม่พบ'}",
            f"🔑 YouTube Cookies: {'ตั้งค่าพาธแล้ว' if os.getenv('YOUTUBE_COOKIES_PATH') else 'ไม่ได้ตั้งค่า'}",
            "📝 ช่องแชตที่อ่าน: ทุกช่อง รวมถึงแชตในห้องเสียง",
            f"🚪 เข้าห้องเสียงอัตโนมัติ: {'เปิด' if db.get_auto_join(interaction.guild_id) else 'ปิด'}",
            f"📨 แชตที่ได้รับ: {player.chat_events_seen} / มีข้อความ: {player.chat_events_in_read_channel} / เข้าคิวเสียง: {player.chat_messages_enqueued}",
            f"⚠️ แชตไม่มีเนื้อหา: {player.chat_events_without_content}",
            f"🔒 แชตส่วนตัวที่ไม่อ่านเพื่อความปลอดภัย: {player.chat_messages_private_skipped}",
            f"🎙️ คิวเสียงอ่าน: {player.tts_queue.qsize()} / ตัวสร้างเสียงทำงาน: {'ใช่' if not player.tts_task.done() else 'ไม่'}",
            f"⏭️ ข้อความเสียงที่ข้ามเพราะคิวล้นหรือรอนาน: {player.chat_messages_dropped}",
            f"🎙️ เครื่องสร้างเสียงล่าสุด: {player.last_tts_engine or 'ยังไม่ได้สร้างเสียง'}",
            f"🔐 DAVE เข้ารหัสพร้อม: {'ใช่' if dave_ready else 'ไม่'} (protocol {dave_protocol})",
            f"🔊 เชื่อมต่อเสียง: {'ใช่' if player.vc.is_connected() else 'ไม่'}",
            f"📡 ส่งเสียง: {'กำลังส่ง' if player.vc.is_playing() else 'พักเพื่อลด CPU' if player.vc.is_paused() else 'ไม่ได้ส่ง'}",
            f"📦 เฟรมเสียงที่สร้าง: เพลง {player.mixer.music_frames_sent} / เสียงอ่านและทดสอบ {player.mixer.speech_frames_sent}",
            f"⚠️ ส่งเสียงผิดพลาด: {player.last_transport_error or 'ไม่มี'}",
            f"🎵 เพลง: {player.playback_status}",
            f"🔁 เพลงจาก SoundCloud สำรอง: {player.last_music_fallback or 'ไม่ได้ใช้'}",
            f"⚠️ เพลงผิดพลาด: {player.last_music_error or 'ไม่มี'}",
            f"⚠️ เสียงอ่านผิดพลาด: {player.last_tts_error or 'ไม่มี'}",
        ]
        if discord.__version__ == "2.7.1" and dave_version.startswith("0.2."):
            lines.append("⚠️ davey 0.2.x ยังไม่รองรับ discord.py 2.7.1 กรุณาใช้ davey 0.1.6")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="play", description="เล่นเพลงด้วยชื่อ ลิงก์ YouTube หรือ SoundCloud หรือเพลย์ลิสต์")
    async def play(self, interaction: discord.Interaction, query: str):
        # A slash interaction must be acknowledged within three seconds. If the
        # host/Discord network already missed that window, no followup is valid.
        age = (datetime.now(timezone.utc) - interaction.created_at).total_seconds()
        if age > 1.5:
            log.warning("/play arrived %.2fs after Discord created the interaction", age)
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)
        except discord.NotFound:
            log.warning("/play interaction expired before acknowledgement (age %.2fs)", age)
            return
        try:
            await self.bot.ensure_player(interaction)
            count = await self.bot.enqueue_query(interaction.guild_id, query, str(interaction.user.id))
            await interaction.followup.send(f"🎵 รับเพลงเข้าคิวแล้ว {count} เพลงค่ะ กำลังตรวจสอบแหล่งเสียงก่อนเล่นนะคะ", ephemeral=True)
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
        player.pause_music()
        await interaction.response.send_message("⏸️ ลิลินพักเพลงไว้ก่อนนะคะ", ephemeral=True)

    @app_commands.command(name="resume", description="เล่นเพลงต่อ")
    async def resume(self, interaction: discord.Interaction):
        try:
            player = self.bot.player_for_command(interaction)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)
        player.resume_music()
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
        if player.last_music_fallback and player.current:
            names.append(f"🔁 ใช้เสียงสำรองจาก SoundCloud: {player.last_music_fallback}")
        if player.last_music_error:
            names.append(f"⚠️ เพลงล่าสุดมีปัญหา: {player.last_music_error}")
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
