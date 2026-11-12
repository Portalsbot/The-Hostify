from __future__ import annotations

import html
import logging
import re

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from branding import get_branding
from database import get_admins
from smallcaps import sc
import messages as M
from .guard import is_owner, is_admin

logger = logging.getLogger("hostify.plugins.start")

HELP_URL = "https://t.me/Just_astha"
CHANNEL_URL = "https://t.me/+txI4WlZlwdg0NWY9"

def _styled_html(value: str) -> str:
    """Apply small caps to visible text while preserving HTML tags and attributes."""
    parts = re.split(r"(<[^>]+>)", value)
    return "".join(part if part.startswith("<") and part.endswith(">") else sc(part) for part in parts)

def _btn(text: str, data: str, url: str | None = None):
    if url:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=data)

def _close_row():
    return [_btn("Close", "hostify:ui:close")]

def _start_keyboard():
    return InlineKeyboardMarkup([
        [_btn("About", "hostify:start:about")],
        [_btn("Help", "hostify:start:help")],
        [_btn("Channel", "hostify:start:channel"), _btn("Commands", "hostify:start:commands")],
        _close_row(),
    ])

def _page_keyboard():
    return InlineKeyboardMarkup([
        [_btn("Back", "hostify:start:back"), _btn("Close", "hostify:ui:close")]
    ])

def _start_text() -> str:
    branding = get_branding()
    owner_user = branding.get("owner_username", "reactiveHCL").lstrip("@")
    dev_user = branding.get("developer_username", "Just_astha").lstrip("@")
    channel_name = branding.get("bot_channel_name", "AceXbotz")
    channel_url = branding.get("bot_channel_url", CHANNEL_URL)
    description = M.M112_START_DESCRIPTION
    return (
        "<b>🤖 MY NAME IS Hostify 2</b>\n\n"
        f"<blockquote>{html.escape(description)}</blockquote>\n"
        f"<blockquote>◈ OWNER: <a href=\"https://t.me/{html.escape(owner_user)}\">HCL</a></blockquote>\n"
        f"<blockquote>◈ DEVELOPER: <a href=\"https://t.me/{html.escape(dev_user)}\">Astha</a></blockquote>\n"
        f"<blockquote>◈ LANGUAGE: {html.escape(branding.get('language', 'Python 3.13'))}</blockquote>\n"
        f"<blockquote>◈ LIBRARY: {html.escape(branding.get('library', 'Pyrofork (Pyrogram API)'))}</blockquote>\n"
        f"<blockquote>◈ DATABASE: {html.escape(branding.get('database', 'MongoDB (Async PyMongo)'))}</blockquote>\n"
        f"<blockquote>◈ BOT CHANNEL: <a href=\"{html.escape(channel_url, quote=True)}\">{html.escape(channel_name)}</a></blockquote>"
    )

def _about_text() -> str:
    return M.M113_ABOUT_MESSAGE

def _help_text() -> str:
    return M.M114_HELP_MESSAGE

def _channel_text() -> str:
    return M.M115_CHANNEL_MESSAGE

async def _commands_text(user_id: int) -> str:
    lines = [
        "/start — Open the welcome menu",
        "/command — View available commands",
        "/admins — View the admin team",
        "/ping — Check bot connectivity",
    ]
    if await is_admin(user_id) or is_owner(user_id):
        lines.extend([
            "",
            "ADMIN COMMANDS",
            "/deploy — Deploy an authorised repository",
            "/cancel — Cancel deployment setup",
            "/list — View your deployments",
            "/mylogs — View recent Hostify controller logs",
            "/stopme — Request a Hostify shutdown",
            "/restarme — Request a Hostify restart",
        ])
    return "\n".join(lines)

async def _admins_screen(client: Client, chat_id: int, *, reply_to=None):
    admins = await get_admins()
    text = "<blockquote>THESE ARE MY ADMINS.</blockquote>"
    buttons = []
    row = []
    for admin in admins:
        user_id = int(admin.get("user_id", 0))
        if user_id <= 0:
            continue
        name = admin.get("first_name") or (f"@{admin['username']}" if admin.get("username") else str(user_id))
        row.append(InlineKeyboardButton(str(name)[:32], url=f"tg://user?id={user_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([_btn("Back", "hostify:start:back"), _btn("Close", "hostify:ui:close")])
    await client.send_message(
        chat_id,
        _styled_html(text),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
        reply_to_message_id=reply_to,
        disable_web_page_preview=True,
    )

@Client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message):
    user = message.from_user
    if user is None:
        return
    try:
        from database import register_user
        await register_user(user.id, username=user.username or "", first_name=user.first_name or "")
        await message.reply_text(
            _styled_html(_start_text()),
            parse_mode=ParseMode.HTML,
            reply_markup=_start_keyboard(),
            disable_web_page_preview=True,
        )
        command_parts = (message.text or "").split(maxsplit=1)
        if len(command_parts) == 2 and command_parts[1].strip().casefold() == "admin" and await is_admin(user.id):
            await message.reply_text(
                sc(M.M117_ADMIN_ADDED),
                parse_mode=ParseMode.DISABLED,
                reply_markup=InlineKeyboardMarkup([_close_row()]),
            )
    except Exception:
        logger.exception("The /start handler failed")
        await message.reply_text(
            sc("Hostify could not process this request."),
            reply_markup=InlineKeyboardMarkup([_close_row()]),
            parse_mode=ParseMode.DISABLED,
        )

@Client.on_message(filters.command("command") & filters.private)
async def command_handler(client: Client, message):
    user = message.from_user
    if user is None:
        return
    text = await _commands_text(user.id)
    await message.reply_text(
        _styled_html("<blockquote>" + html.escape(text) + "</blockquote>"),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([_close_row()]),
    )

@Client.on_message(filters.command("admins") & filters.private)
async def admins_handler(client: Client, message):
    await _admins_screen(client, message.chat.id, reply_to=message.id)


@Client.on_callback_query(filters.regex(r"^hostify:start:"))
async def start_navigation(client: Client, query):
    user = query.from_user
    if user is None or query.message is None:
        await query.answer()
        return
    action = (query.data or "").rsplit(":", 1)[-1]
    if action == "back":
        text = _start_text()
        keyboard = _start_keyboard()
    elif action == "about":
        text = _about_text()
        keyboard = _page_keyboard()
    elif action == "help":
        text = _help_text()
        keyboard = _page_keyboard()
    elif action == "channel":
        text = _channel_text()
        keyboard = _page_keyboard()
    elif action == "commands":
        text = "<blockquote>" + html.escape(await _commands_text(user.id)) + "</blockquote>"
        keyboard = _page_keyboard()
    else:
        await query.answer("Unknown menu action.", show_alert=True)
        return
    try:
        await query.message.edit_text(
            _styled_html(text),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        await query.answer()
    except Exception:
        logger.exception("Start navigation failed: %s", action)
        await query.answer("Could not open that page.", show_alert=True)
