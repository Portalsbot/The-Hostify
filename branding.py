"""Runtime-editable Hostify branding stored in MongoDB."""
from __future__ import annotations

import re

from database import get_app_settings, set_app_setting

DEFAULT_BRANDING: dict[str, str] = {
    "brand_name": "Hostify 2",
    "owner_username": "reactiveHCL",
    "developer_username": "Just_astha",
    "developer_id": "8043083450",
    "support_username": "Just_Astha",
    "support_id": "7271115259",
    "promo_channel_username": "Anime_Portals",
    "promo_channel_id": "-1003545564222",
    "bot_channel_name": "AceXbotz",
    "bot_channel_url": "https://t.me/+txI4WlZlwdg0NWY9",
    "language": "Python 3.13",
    "library": "Pyrofork (Pyrogram API)",
    "database": "MongoDB (Async PyMongo)",
}

_EDITABLE_FIELDS = frozenset(DEFAULT_BRANDING)
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_branding: dict[str, str] = dict(DEFAULT_BRANDING)


async def load_branding() -> None:
    stored = await get_app_settings("branding")
    _branding.clear()
    _branding.update(DEFAULT_BRANDING)
    for key, value in stored.items():
        if key in _EDITABLE_FIELDS and isinstance(value, str):
            _branding[key] = value


def get_branding() -> dict[str, str]:
    return dict(_branding)


def _validate_brand_value(field: str, value: str) -> str:
    if field not in _EDITABLE_FIELDS:
        raise ValueError("Unknown branding field. Use /settings to see editable fields.")
    value = value.strip()
    if not value or len(value) > 120 or any(ord(char) < 32 for char in value):
        raise ValueError("Value must contain 1-120 printable characters.")
    if field.endswith("_username"):
        value = value.removeprefix("@").strip()
        if not _USERNAME_RE.fullmatch(value):
            raise ValueError("Enter a valid Telegram username without @ (5-32 letters, digits or underscores).")
    elif field.endswith("_url"):
        if not value.startswith("https://"):
            raise ValueError("Enter a valid HTTPS URL.")
    elif field.endswith("_id"):
        if not re.fullmatch(r"-?\d{5,20}", value):
            raise ValueError("Enter a numeric Telegram ID.")
    return value


async def set_brand_value(field: str, value: str) -> str:
    cleaned = _validate_brand_value(field, value)
    await set_app_setting("branding", field, cleaned)
    _branding[field] = cleaned
    return cleaned


def format_branding(text: str) -> str:
    """Expand known branding placeholders without touching other template fields."""
    promo_user = _branding["promo_channel_username"]
    replacements = {
        "{brand_name}": _branding["brand_name"],
        "{owner_mention}": f"@{_branding['owner_username']}",
        "{developer_mention}": f"@{_branding['developer_username']}",
        "{developer_id}": _branding["developer_id"],
        "{support_mention}": f"@{_branding['support_username']}",
        "{support_id}": _branding["support_id"],
        "{promo_channel_mention}": f"@{promo_user}",
        "{promo_channel_url}": f"https://t.me/{promo_user}",
        "{promo_channel_id}": _branding["promo_channel_id"],
        "{bot_channel_name}": _branding["bot_channel_name"],
        "{bot_channel_url}": _branding["bot_channel_url"],
        "{language}": _branding["language"],
        "{library}": _branding["library"],
        "{database}": _branding["database"],
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text
