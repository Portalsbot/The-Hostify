from __future__ import annotations

import logging
import re
from typing import Any

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import messages as M
from branding import get_branding
from config import OWNER_ID
from database import get_bot
from manager import restart_bot, stop_bot
from utils import format_output
from .guard import is_owner
from ui_buttons import button

logger = logging.getLogger("hostify.plugins.channel")

_ACTION_PATTERN = re.compile(r"^hc:(restart|stop):([A-Za-z0-9_]{1,32})$")


async def _answer(callback: CallbackQuery, text: object, **kwargs):
    return await callback.answer(format_output(text), **kwargs)


def _action_keyboard(name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            button(format_output(M.M108_BTN_RESTART), callback_data=f"hc:restart:{name}", style="success"),
            button(format_output(M.M109_BTN_STOP), callback_data=f"hc:stop:{name}", style="danger"),
        ],
        [button("Close", callback_data="hostify:ui:close", style="danger")],
    ])


def _hidden_environment_summary(env_vars: dict[str, str] | None) -> str:
    """Display variable names only; environment values may contain credentials."""
    keys = sorted(key for key in (env_vars or {}) if key != "NOTIFY_CHANNEL")
    if not keys:
        return "• No custom environment variables"
    return "\n".join(f"• {key}: configured (value hidden)" for key in keys)


async def _send_announcement(
    client: Client,
    chat_id: int | str,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> Message:
    styled = format_output(text)
    try:
        return await client.send_photo(
            chat_id=chat_id,
            photo=M.BOT_IMAGE_URL,
            caption=styled,
            parse_mode=ParseMode.DISABLED,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.warning("Announcement photo failed; retrying as text", exc_info=True)
        return await client.send_message(
            chat_id=chat_id,
            text=styled,
            parse_mode=ParseMode.DISABLED,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )


async def post_deploy_announcement(
    client: Client,
    name: str,
    repo_url: str,
    env_vars: dict[str, str] | None = None,
) -> bool:
    """Post an optional deployment notice with owner-only restart/stop controls."""
    channel = (env_vars or {}).get("NOTIFY_CHANNEL")
    if not channel:
        return False

    bot = await get_bot(name)
    if not bot:
        logger.error("Cannot announce deployment: database record missing for %s", name)
        return False

    username = bot.get("bot_username")
    bot_mention = f"@{username}" if username else name
    try:
        me = await client.get_me()
        owner_mention = f"@{me.username}" if me.username else str(OWNER_ID)
        branding = get_branding()
        text = M.M29_CHANNEL_DEPLOY.format(
            bot_mention=bot_mention,
            owner_mention=owner_mention,
            repo_url=repo_url,
            env_lines=_hidden_environment_summary(env_vars),
            brand_name=branding["brand_name"],
            developer_mention=f"@{branding['developer_username']}",
            developer_id=branding["developer_id"],
            support_mention=f"@{branding['support_username']}",
            support_id=branding["support_id"],
            promo_channel_mention=f"@{branding['promo_channel_username']}",
            promo_channel_url=f"https://t.me/{branding['promo_channel_username']}",
            promo_channel_id=branding["promo_channel_id"],
        )
        await _send_announcement(client, channel, text, _action_keyboard(name))
        logger.info("Deployment announcement posted for %s", name)
        return True
    except Exception:
        logger.exception("Could not post deployment announcement for %s", name)
        return False


async def _is_valid_announcement(
    client: Client,
    callback: CallbackQuery,
    bot: dict[str, Any],
) -> bool:
    """Bind controls to the bot record's configured channel and bot-authored post."""
    message = callback.message
    configured_channel = bot.get("notify_channel")
    if message is None or message.chat.type != ChatType.CHANNEL or not configured_channel:
        return False
    try:
        expected_chat = await client.get_chat(configured_channel)
        if expected_chat.id != message.chat.id:
            return False
        me = await client.get_me()
        if message.from_user is not None and message.from_user.id != me.id:
            return False
        return True
    except Exception:
        logger.warning("Could not validate announcement channel", exc_info=True)
        return False


async def _edit_announcement(message: Message, text: str, keyboard=None) -> None:
    styled = format_output(text)
    if message.caption is not None:
        await message.edit_caption(
            caption=styled,
            parse_mode=ParseMode.DISABLED,
            reply_markup=keyboard,
        )
    else:
        await message.edit_text(
            text=styled,
            parse_mode=ParseMode.DISABLED,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )


def _replace_status_line(message: Message, replacement: str) -> str:
    original = message.caption or message.text or ""
    lines = original.splitlines()
    if lines:
        lines[0] = replacement
        return "\n".join(lines)
    return replacement


@Client.on_callback_query(filters.regex(r"^hc:"))
async def channel_controls(client: Client, callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if not is_owner(user_id):
        await _answer(callback, M.M02_NON_OWNER_ALERT, show_alert=True)
        return

    match = _ACTION_PATTERN.fullmatch(callback.data or "")
    if not match:
        await _answer(callback, "Unknown or expired Hostify control.", show_alert=True)
        return

    action, name = match.groups()
    bot = await get_bot(name)
    if not bot:
        await _answer(callback, M.M25_NOT_FOUND.format(name=name), show_alert=True)
        return
    if not await _is_valid_announcement(client, callback, bot):
        await _answer(callback, M.M67_ANNOUNCEMENT_UNVERIFIED, show_alert=True)
        return

    message = callback.message
    if message is None:
        await _answer(callback, M.M66_ANNOUNCEMENT_UNAVAILABLE, show_alert=True)
        return

    username = bot.get("bot_username") or name
    bot_mention = f"@{username}"
    try:
        if action == "restart":
            ok, detail = await restart_bot(name)
            replacement = M.M30_LINE_RESTARTED.format(bot_mention=bot_mention)
            dm_text = M.M31_DM_RESTARTED.format(bot_mention=bot_mention)
        else:
            ok, detail = await stop_bot(name)
            replacement = M.M30_LINE_STOPPED.format(bot_mention=bot_mention)
            dm_text = M.M31_DM_STOPPED.format(bot_mention=bot_mention)

        if not ok:
            error = M.M25_NOT_FOUND.format(name=name) if detail == "not_found" else M.M94_CHANNEL_OPERATION_FAILED.format(reason=detail or "unknown error")
            await _answer(callback, error[:180], show_alert=True)
            return

        await _edit_announcement(message, _replace_status_line(message, replacement), _action_keyboard(name))
        try:
            await client.send_message(
                OWNER_ID,
                format_output(dm_text),
                parse_mode=ParseMode.DISABLED,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Close", callback_data="hostify:ui:close")
                ]]),
            )
        except Exception:
            logger.warning("Could not send owner DM for %s", action, exc_info=True)
        await _answer(callback, M.M95_CHANNEL_OPERATION_COMPLETED)
    except Exception:
        logger.exception("Channel %s action failed for %s", action, name)
        await _answer(callback, M.M68_OPERATION_FAILED, show_alert=True)
