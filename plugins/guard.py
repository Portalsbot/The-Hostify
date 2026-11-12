from __future__ import annotations

from pyrogram import filters

from config import OWNER_ID
from database import is_admin as _database_is_admin


def is_owner(user_id):
    """True only for the configured Hostify owner."""
    return bool(OWNER_ID and user_id is not None and user_id == OWNER_ID)


async def is_admin(user_id):
    """Check whether a Telegram account is a registered Hostify admin."""
    if is_owner(user_id):
        return False
    return await _database_is_admin(user_id)


async def is_owner_or_admin(user_id):
    """Allow the owner or a registered admin."""
    return is_owner(user_id) or await is_admin(user_id)


def _owner_filter(_, __, message):
    user = getattr(message, "from_user", None)
    return bool(user and is_owner(user.id))


owner_only = filters.create(_owner_filter)
