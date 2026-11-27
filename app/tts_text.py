"""Clean Discord chat into natural, Thai-only speech for Lilin."""

import re


def speech_text(author: str, content: str) -> str:
    """Keep readable chat and drop markdown, Discord tags and code noise."""
    content = re.sub(r"```[\s\S]*?```", " ", content)
    content = re.sub(r"https?://\S+", " ลิงก์ ", content, flags=re.IGNORECASE)
    content = re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", " ", content)
    content = re.sub(r"<[@#][!&]?\d+>", " ", content)
    content = re.sub(r"[`*_~|]", "", content)
    content = " ".join(content.split())[:180].strip()
    if not content:
        return ""
    name = " ".join(author.split())[:32].strip()
    return f"{name} พิมพ์ว่า {content}" if name else content
