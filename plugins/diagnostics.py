from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from smallcaps import sc

logger = logging.getLogger("hostify.diagnostics")


@Client.on_raw_update(group=-101)
async def trace_raw_updates(client, update, users, chats):
    """Observe raw Telegram updates without logging message contents."""
    update_type = type(update).__name__
    if "Message" in update_type:
        logger.info("RAW MESSAGE UPDATE RECEIVED | type=%s", update_type)


@Client.on_message(group=-100)
async def trace_all_messages(client, message):
    chat = getattr(message, "chat", None)
    if chat is None:
        return
    if chat.type == ChatType.PRIVATE:
        user = getattr(message, "from_user", None)
        logger.info(
            "PRIVATE MESSAGE EVENT | user_id=%s | outgoing=%s | message_id=%s | has_text=%s",
            user.id if user else None,
            bool(getattr(message, "outgoing", False)),
            message.id,
            bool(message.text),
        )


@Client.on_message(filters.private & filters.command("ping"), group=-1)
async def ping_handler(client, message):
    try:
        await message.reply_text(
            sc("Pong. The Hostify controller is receiving Telegram updates."),
            parse_mode=ParseMode.DISABLED,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Close", callback_data="hostify:ui:close")
            ]]),
        )
        logger.info("PING TEST SUCCESSFUL")
    except Exception:
        logger.exception("PING TEST FAILED")
