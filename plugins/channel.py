from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Any

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import messages as M
from config import OWNER_ID
from database import get_bot
from manager import delete_bot, restart_bot, stop_bot
from .guard import is_owner

logger = logging.getLogger("hostify.plugins.channel")

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_ACTION_PATTERN = re.compile(
    r"^hc:(restart|stop|delete):([A-Za-z0-9_]{1,32})$"
)
_CONFIRM_PATTERN = re.compile(r"^hc:(confirm|cancel):([A-Za-z0-9_-]{12})$")
_DELETE_CONFIRM_TTL = 60

# token -> (owner_id, channel_id, message_id, bot_name, expiry_monotonic)
_PENDING_CHANNEL_DELETES: dict[str, tuple[int, int, int, str, float]] = {}


def _action_keyboard(name: str) -> InlineKeyboardMarkup:
    """Controls attached only to Hostify deployment announcements."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Restart", callback_data=f"hc:restart:{name}"),
                InlineKeyboardButton("Stop", callback_data=f"hc:stop:{name}"),
            ],
            [InlineKeyboardButton("Delete", callback_data=f"hc:delete:{name}")],
        ]
    )


def _delete_confirmation_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Confirm deletion", callback_data=f"hc:confirm:{token}"),
            InlineKeyboardButton("Cancel", callback_data=f"hc:cancel:{token}"),
        ]]
    )


def _purge_expired_confirmations() -> None:
    now = time.monotonic()
    expired = [
        token
        for token, item in _PENDING_CHANNEL_DELETES.items()
        if item[4] <= now
    ]
    for token in expired:
        _PENDING_CHANNEL_DELETES.pop(token, None)


def _hidden_environment_summary(env_vars: dict[str, str] | None) -> str:
    """Show configured variable names only; never disclose secret values."""
    keys = sorted(
        key for key in (env_vars or {})
        if key != "NOTIFY_CHANNEL"
    )
    if not keys:
        return "• No custom environment variables"
    return "\n".join(
        f"• {key}: configured (value hidden)"
        for key in keys
    )


async def _send_announcement(
    client: Client,
    chat_id: int | str,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> Message:
    """Prefer the configured signature image, with a text-only fallback."""
    try:
        return await client.send_photo(
            chat_id=chat_id,
            photo=M.BOT_IMAGE_URL,
            caption=text,
            parse_mode=ParseMode.DISABLED,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.warning(
            "Announcement photo failed; retrying as text",
            exc_info=True,
        )
        return await client.send_message(
            chat_id=chat_id,
            text=text,
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
    """Post a deployment notice without leaking environment-variable values.

    Notification failure is intentionally non-fatal: a successfully deployed
    bot must not be reported as failed merely because a channel post failed.
    """
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
        text = M.M29_CHANNEL_DEPLOY.format(
            bot_mention=bot_mention,
            owner_mention=owner_mention,
            repo_url=repo_url,
            env_lines=_hidden_environment_summary(env_vars),
        )

        await _send_announcement(
            client,
            channel,
            text,
            _action_keyboard(name),
        )
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
    """Bind controls to the bot's configured channel and a bot-authored post."""
    message = callback.message
    configured_channel = bot.get("notify_channel")

    if (
        message is None
        or message.chat.type != ChatType.CHANNEL
        or not configured_channel
    ):
        return False

    try:
        expected_chat = await client.get_chat(configured_channel)
        if expected_chat.id != message.chat.id:
            return False

        me = await client.get_me()
        # In channel posts from_user can be None; when present it must be this bot.
        if message.from_user is not None and message.from_user.id != me.id:
            return False

        return True

    except Exception:
        logger.warning("Could not validate announcement channel", exc_info=True)
        return False


async def _edit_announcement(message: Message, text: str, keyboard=None) -> None:
    """Edit either a photo caption or a text announcement."""
    if message.caption is not None:
        await message.edit_caption(
            caption=text,
            parse_mode=ParseMode.DISABLED,
            reply_markup=keyboard,
        )
    else:
        await message.edit_text(
            text=text,
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
    """Owner-only controls for channel deployment announcements."""
    user_id = callback.from_user.id if callback.from_user else None
    if not is_owner(user_id):
        await callback.answer(M.M02_NON_OWNER_ALERT, show_alert=True)
        return

    data = callback.data or ""
    action_match = _ACTION_PATTERN.fullmatch(data)
    confirm_match = _CONFIRM_PATTERN.fullmatch(data)

    if action_match:
        action, name = action_match.groups()
        bot = await get_bot(name)
        if not bot:
            await callback.answer(
                M.M25_NOT_FOUND.format(name=name),
                show_alert=True,
            )
            return

        if not await _is_valid_announcement(client, callback, bot):
            await callback.answer(
                "This control is not attached to the registered notification channel.",
                show_alert=True,
            )
            return

        message = callback.message
        if message is None:
            await callback.answer("Announcement message is unavailable.", show_alert=True)
            return

        username = bot.get("bot_username") or name
        bot_mention = f"@{username}"

        if action == "delete":
            _purge_expired_confirmations()
            token = secrets.token_urlsafe(9)
            _PENDING_CHANNEL_DELETES[token] = (
                user_id,
                message.chat.id,
                message.id,
                name,
                time.monotonic() + _DELETE_CONFIRM_TTL,
            )
            await message.edit_reply_markup(
                reply_markup=_delete_confirmation_keyboard(token)
            )
            await callback.answer("Confirm deletion within 60 seconds.")
            return

        try:
            if action == "restart":
                ok, detail = await restart_bot(name)
                replacement = M.M30_LINE_RESTARTED.format(bot_mention=bot_mention)
            else:
                ok, detail = await stop_bot(name)
                replacement = M.M30_LINE_STOPPED.format(bot_mention=bot_mention)

            if not ok:
                error = (
                    M.M25_NOT_FOUND.format(name=name)
                    if detail == "not_found"
                    else f"Operation failed: {detail or 'unknown error'}"
                )
                await callback.answer(error[:180], show_alert=True)
                return

            new_text = _replace_status_line(message, replacement)
            await _edit_announcement(message, new_text, _action_keyboard(name))

            dm_text = (
                M.M31_DM_RESTARTED.format(bot_mention=bot_mention)
                if action == "restart"
                else M.M31_DM_STOPPED.format(bot_mention=bot_mention)
            )
            try:
                await client.send_message(
                    OWNER_ID,
                    dm_text,
                    parse_mode=ParseMode.DISABLED,
                )
            except Exception:
                logger.warning("Could not send owner DM for %s", action, exc_info=True)

            await callback.answer("Operation completed.")

        except Exception:
            logger.exception("Channel %s action failed for %s", action, name)
            await callback.answer("Operation failed. Check controller logs.", show_alert=True)

        return

    if confirm_match:
        action, token = confirm_match.groups()
        pending = _PENDING_CHANNEL_DELETES.get(token)
        if pending is None:
            await callback.answer("Confirmation expired. Use the channel controls again.", show_alert=True)
            return

        expected_owner, expected_chat, expected_message, name, expiry = pending
        message = callback.message

        if (
            message is None
            or message.chat.type != ChatType.CHANNEL
            or message.chat.id != expected_chat
            or message.id != expected_message
            or user_id != expected_owner
        ):
            await callback.answer("This confirmation does not belong to you.", show_alert=True)
            return

        bot = await get_bot(name)
        if not bot or not await _is_valid_announcement(client, callback, bot):
            _PENDING_CHANNEL_DELETES.pop(token, None)
            await callback.answer("The deployment record or channel is no longer valid.", show_alert=True)
            return

        if time.monotonic() > expiry:
            _PENDING_CHANNEL_DELETES.pop(token, None)
            await callback.answer("Confirmation expired. Use the channel controls again.", show_alert=True)
            return

        # Consume before awaiting the delete operation to prevent duplicate clicks.
        _PENDING_CHANNEL_DELETES.pop(token, None)

        if action == "cancel":
            await message.edit_reply_markup(reply_markup=_action_keyboard(name))
            await callback.answer("Deletion cancelled.")
            return

        try:
            ok, detail = await delete_bot(name)
            if not ok:
                await callback.answer(
                    f"Deletion failed: {detail or 'unknown error'}"[:180],
                    show_alert=True,
                )
                await message.edit_reply_markup(reply_markup=_action_keyboard(name))
                return

            username = bot.get("bot_username") or name
            new_text = _replace_status_line(
                message,
                M.M30_LINE_DELETED.format(bot_mention=f"@{username}"),
            )
            await _edit_announcement(message, new_text, keyboard=None)

            try:
                await client.send_message(
                    OWNER_ID,
                    M.M31_DM_DELETED.format(bot_mention=f"@{username}"),
                    parse_mode=ParseMode.DISABLED,
                )
            except Exception:
                logger.warning("Could not send delete notification to owner", exc_info=True)

            await callback.answer("Bot deleted.")

        except Exception:
            logger.exception("Channel deletion failed for %s", name)
            await callback.answer("Deletion failed. Check controller logs.", show_alert=True)
        return

    await callback.answer("Unknown or expired Hostify control.", show_alert=True)
