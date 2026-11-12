from __future__ import annotations

import logging

from pyrogram import Client, filters
from ui_buttons import button

logger = logging.getLogger("hostify.plugins.ui")


@Client.on_callback_query(filters.regex(r"^hostify:ui:close$"))
async def close_message_callback(client: Client, query):
    """Delete the bot's current menu message when Close is pressed."""
    if query.message is None:
        await query.answer()
        return
    try:
        await query.message.delete()
        await query.answer("Closed.")
    except Exception:
        logger.exception("Could not close Hostify menu message")
        await query.answer("I could not delete this message.", show_alert=True)
