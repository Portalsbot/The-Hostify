from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.enums import ParseMode

from utils import format_output
import messages as M
from .guard import owner_only

logger = logging.getLogger("hostify.diagnostics")


@Client.on_message(filters.private & filters.text, group=-100)
async def trace_incoming(client, message):
    """Log command metadata only; never log private message content or secrets."""
    user = message.from_user
    text = message.text or ""
    command = text.split(maxsplit=1)[0] if text.startswith("/") else "<text message>"
    logger.info(
        "INCOMING MESSAGE | user_id=%s | command=%s",
        user.id if user else None,
        command,
    )


@Client.on_message(filters.private & owner_only & filters.command("ping"), group=-1)
async def ping_handler(client, message):
    try:
        await message.reply_text(
            format_output(M.M44_PING),
            parse_mode=ParseMode.DISABLED,
        )
        logger.info("PING TEST SUCCESSFUL")
    except Exception:
        logger.exception("PING TEST FAILED")
