from pyrogram import filters
from config import OWNER_ID


def is_owner(user_id):
    """Return True only when the Telegram user is the configured owner."""
    return bool(
        OWNER_ID
        and user_id is not None
        and user_id == OWNER_ID
    )


def _owner_filter(_, __, message):
    """Pyrogram filter that allows messages only from the owner."""
    user = getattr(message, "from_user", None)

    if user is None:
        return False

    return is_owner(user.id)


# Import this filter in other plugin files to protect owner commands.
owner_only = filters.create(_owner_filter)
