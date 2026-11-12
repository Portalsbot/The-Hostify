from __future__ import annotations

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import messages as M
from branding import format_branding
from database import get_custom_messages
from smallcaps import sc


def _with_close(reply_markup=None):
    """Ensure every utility-sent bot message has a working Close button."""
    rows = [list(row) for row in getattr(reply_markup, "inline_keyboard", [])] if reply_markup else []
    if not any(
        getattr(button, "callback_data", None) == "hostify:ui:close"
        for row in rows for button in row
    ):
        rows.append([InlineKeyboardButton("Close", callback_data="hostify:ui:close")])
    return InlineKeyboardMarkup(rows)


def format_output(text: object) -> str:
    """Apply live branding and small-cap styling to outgoing bot text."""
    return sc(format_branding(str(text)))


async def load_message_templates() -> None:
    """Load the owner's persisted Telegram-edited messages into the registry."""
    M.apply_overrides(await get_custom_messages())


async def send_message(
    client: Client,
    chat_id,
    text,
    reply_markup=None,
    reply_to_message_id=None,
):
    styled = format_output(text)
    reply_markup = _with_close(reply_markup)
    try:
        return await client.send_photo(
            chat_id=chat_id,
            photo=M.BOT_IMAGE_URL,
            caption=styled,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
            parse_mode=ParseMode.DISABLED,
        )
    except Exception:
        return await client.send_message(
            chat_id=chat_id,
            text=styled,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
            parse_mode=ParseMode.DISABLED,
            disable_web_page_preview=True,
        )


async def reply_message(message, text, reply_markup=None, **kwargs):
    return await message.reply_text(
        format_output(text),
        reply_markup=_with_close(reply_markup),
        parse_mode=ParseMode.DISABLED,
        **kwargs,
    )


async def edit_message(message, text, reply_markup=None):
    styled = format_output(text)
    reply_markup = _with_close(reply_markup)
    try:
        return await message.edit_caption(
            caption=styled,
            reply_markup=reply_markup,
            parse_mode=ParseMode.DISABLED,
        )
    except Exception:
        return await message.edit_text(
            text=styled,
            reply_markup=reply_markup,
            parse_mode=ParseMode.DISABLED,
            disable_web_page_preview=True,
        )


async def answer_callback(callback, text: object, *args, **kwargs):
    return await callback.answer(format_output(text), *args, **kwargs)
