"""Small-cap conversion for user-facing Telegram text.

Telegram commands, usernames, links, environment keys and template placeholders
are kept in ordinary ASCII so that they remain usable and copyable.
"""
from __future__ import annotations

import re

_SMALL_CAPS = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ",
    "f": "ꜰ", "g": "ɢ", "h": "ʜ", "i": "ɪ", "j": "ᴊ",
    "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ", "o": "ᴏ",
    "p": "ᴘ", "q": "ꞯ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ",
    "u": "ᴜ", "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
}
_TRANSLATION = str.maketrans({**_SMALL_CAPS, **{k.upper(): v for k, v in _SMALL_CAPS.items()}})

# Ordered alternatives protect each technical token before converting prose.
_PRESERVE = re.compile(
    r"https?://[^\s<>]+|tg://[^\s<>]+|t\.me/[^\s<>]+|"
    r"@[A-Za-z0-9_]{3,}|"
    r"(?<!\w)/[A-Za-z][A-Za-z0-9_]*(?:@[A-Za-z0-9_]+)?|"
    r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b|"
    r"\b[A-Za-z0-9_.-]+\.(?:py|txt|md|log|json|env)\b|"
    r"\{[A-Za-z_][A-Za-z0-9_]*\}|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


def sc(text: object) -> str:
    """Convert ordinary Latin prose to Unicode small caps."""
    value = str(text)
    parts: list[str] = []
    cursor = 0
    for match in _PRESERVE.finditer(value):
        parts.append(value[cursor:match.start()].translate(_TRANSLATION))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(value[cursor:].translate(_TRANSLATION))
    return "".join(parts)
