"""Pick a voice channel for Lilin without depending on Discord internals."""


def human_count(channel) -> int:
    return sum(not member.bot for member in getattr(channel, "members", ()))


def occupied_channel(guild, current=None):
    """Keep an occupied current channel; otherwise choose the busiest room."""
    if current is not None and human_count(current):
        return current
    candidates = [channel for channel in guild.voice_channels if human_count(channel)]
    return max(candidates, key=lambda channel: human_count(channel), default=None)


def visible_to_voice_listeners(text_channel, voice_channel) -> bool:
    """Do not broadcast private-channel messages to unauthorized VC members."""
    listeners = [m for m in getattr(voice_channel, "members", ()) if not m.bot]
    return all(text_channel.permissions_for(member).view_channel for member in listeners)
