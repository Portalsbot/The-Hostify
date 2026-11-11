from __future__ import annotations

from pyrogram import filters

from config import OWNER_ID
from database import is_admin


def is_owner(user_id):
    """True only for the configured owner."""
    return bool(OWNER_ID and user_id is not None and user_id == OWNER_ID)


async def is_owner_or_admin(user_id):
    """Authorise deployments and admin features."""
    return is_owner(user_id) or await is_admin(user_id)


def _owner_filter(_, __, message):
    user = getattr(message, "from_user", None)
    return bool(user and is_owner(user.id))


owner_only = filters.create(_owner_filter)
