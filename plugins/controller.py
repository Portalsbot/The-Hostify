from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BASE_DIR, OWNER_ID
from database import get_admins, get_registered_users, get_app_settings, set_app_setting
from smallcaps import sc
from .guard import is_admin, is_owner
from ui_buttons import button

logger = logging.getLogger("hostify.plugins.controller")
REQUEST_URL = "https://t.me/ReactiveHCl"


def _notice_markup():
    return InlineKeyboardMarkup([
        [button("Request to start again", url=REQUEST_URL, style="success")],
        [button("Close", callback_data="hostify:ui:close", style="danger")],
    ])


async def _recipients() -> list[int]:
    ids = {OWNER_ID}
    try:
        for item in await get_admins():
            value = item.get("user_id")
            if isinstance(value, int) and value > 0:
                ids.add(value)
        for item in await get_registered_users():
            value = item.get("user_id")
            if isinstance(value, int) and value > 0:
                ids.add(value)
    except Exception:
        logger.exception("Could not load Hostify notification recipients")
    return sorted(ids)


async def _broadcast(client: Client, text: str, *, include_request: bool = True) -> None:
    markup = _notice_markup() if include_request else InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]])
    recipients = await _recipients()
    for user_id in recipients:
        try:
            await client.send_message(
                user_id,
                f"<blockquote>{sc(text)}</blockquote>",
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.debug("Could not deliver controller notice to user %s", user_id)


async def notify_startup(client: Client) -> None:
    """Announce recovery after a requested controller restart."""
    try:
        state = await get_app_settings("controller")
        pending = state.get("pending_notice", "")
        if pending in {"restart", "stop"}:
            await _broadcast("Hostify is back online.", include_request=False)
            await set_app_setting("controller", "pending_notice", "")
    except Exception:
        logger.exception("Could not send Hostify recovery notice")


def _schedule_termination():
    loop = asyncio.get_running_loop()
    loop.call_later(1.5, os.kill, os.getpid(), signal.SIGTERM)


async def _authorised_admin(message) -> bool:
    user = message.from_user
    if not user or is_owner(user.id):
        return False
    return await is_admin(user.id)


@Client.on_message(filters.command("mylogs") & filters.private)
async def mylogs_command(client: Client, message):
    if not await _authorised_admin(message):
        return
    path = Path(BASE_DIR) / "hostify.log"
    if not path.is_file():
        await message.reply_text(
            sc("Hostify log file is not available yet."),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]]),
            parse_mode=ParseMode.DISABLED,
        )
        return
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        safe = []
        for line in data:
            for marker in ("BOT_TOKEN=", "API_HASH=", "MONGO_URI=", "GITHUB_APP_PRIVATE_KEY_B64="):
                if marker in line:
                    line = line.split(marker, 1)[0] + marker + "[REDACTED]"
            safe.append(line)
        text = "LAST HOSTIFY LOG LINES\n\n" + ("\n".join(safe) if safe else "No log entries.")
        await message.reply_text(
            sc(text[-3800:]),
            parse_mode=ParseMode.DISABLED,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]]),
        )
    except Exception:
        logger.exception("Could not read Hostify controller logs")
        await message.reply_text(
            sc("Could not read Hostify logs."),
            parse_mode=ParseMode.DISABLED,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]]),
        )


@Client.on_message(filters.command("restarme") & filters.private)
async def restartme_command(client: Client, message):
    if not await _authorised_admin(message):
        return
    try:
        await set_app_setting("controller", "pending_notice", "restart")
        await _broadcast("Hostify is restarting.")
        await message.reply_text(
            sc("Hostify restart request accepted."),
            parse_mode=ParseMode.DISABLED,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]]),
        )
        _schedule_termination()
    except Exception:
        logger.exception("Controller restart request failed")
        await message.reply_text(
            sc("Could not request a controller restart."),
            parse_mode=ParseMode.DISABLED,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]]),
        )


@Client.on_message(filters.command("stopme") & filters.private)
async def stopme_command(client: Client, message):
    if not await _authorised_admin(message):
        return
    try:
        await set_app_setting("controller", "pending_notice", "stop")
        await _broadcast("Hostify is going offline. Use the request button to contact the owner.")
        await message.reply_text(
            sc("Hostify shutdown request accepted."),
            parse_mode=ParseMode.DISABLED,
            reply_markup=_notice_markup(),
        )
        _schedule_termination()
    except Exception:
        logger.exception("Controller shutdown request failed")
        await message.reply_text(
            sc("Could not request a controller shutdown."),
            parse_mode=ParseMode.DISABLED,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]]),
        )
