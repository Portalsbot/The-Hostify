from __future__ import annotations

import html
import logging
import re
import secrets
import time

import psutil
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import messages as M
from config import BASE_DIR
from database import get_bot, list_bots, reset_custom_message, set_custom_message
from branding import set_brand_value, get_branding
from smallcaps import sc
from utils import format_output
from manager import delete_bot, restart_bot, stop_bot, read_logs
from .guard import is_owner

logger = logging.getLogger("hostify.plugins.owner")

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_DELETE_PATTERN = re.compile(r"^hostify:delete:(confirm|cancel):([A-Za-z0-9_-]{12})$")
_DELETE_CONFIRM_TTL = 60
_PENDING_DELETES: dict[str, tuple[int, int, str, float, int]] = {}

def _close_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]])

def _with_close(markup=None):
    rows = [list(row) for row in markup.inline_keyboard] if markup else []
    rows.append([InlineKeyboardButton("Close", callback_data="hostify:ui:close")])
    return InlineKeyboardMarkup(rows)

async def _reply(message, text: str, **kwargs):
    markup = kwargs.pop("reply_markup", None)
    return await message.reply_text(
        format_output(text),
        parse_mode=ParseMode.DISABLED,
        reply_markup=_with_close(markup),
        **kwargs,
    )

async def _quote(message, text: str):
    return await message.reply_text(
        f"<blockquote>{html.escape(sc(text))}</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=_close_markup(),
    )

async def _answer(query, text: str, **kwargs):
    return await query.answer(format_output(text), **kwargs)

def _requested_name(message) -> str | None:
    parts = (message.text or "").split()
    if len(parts) != 2:
        return None
    return parts[1] if _NAME_PATTERN.fullmatch(parts[1]) else None

def _purge_expired_confirmations() -> None:
    now = time.monotonic()
    for token in [key for key, value in _PENDING_DELETES.items() if value[3] <= now]:
        _PENDING_DELETES.pop(token, None)

def _delete_keyboard(token: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Confirm", callback_data=f"hostify:delete:confirm:{token}"),
        InlineKeyboardButton("Cancel", callback_data=f"hostify:delete:cancel:{token}"),
    ], [
        InlineKeyboardButton("Close", callback_data="hostify:ui:close"),
    ]])

def _settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Branding", callback_data="hostify:settings:branding"),
         InlineKeyboardButton("Message Templates", callback_data="hostify:settings:messages")],
        [InlineKeyboardButton("Admin Management", callback_data="hostify:settings:admins"),
         InlineKeyboardButton("GitHub App", callback_data="hostify:settings:github")],
        [InlineKeyboardButton("Host Status", callback_data="hostify:settings:status"),
         InlineKeyboardButton("Deployment Policy", callback_data="hostify:settings:policy")],
        [InlineKeyboardButton("Close", callback_data="hostify:ui:close")],
    ])

def _html_smallcaps(value: str) -> str:
    parts = re.split(r"(<[^>]+>)", value)
    return "".join(part if part.startswith("<") and part.endswith(">") else sc(part) for part in parts)

@Client.on_message(filters.command("commands") & filters.private)
async def commands_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        await _quote(message, "This command is not for you senpai.")
        return
    text = (
        "OWNER COMMANDS\n\n"
        "/commands — Show owner-only commands\n"
        "/stop NAME — Stop any deployed bot\n"
        "/restart NAME — Restart any deployed bot\n"
        "/delete NAME — Delete a deployment with confirmation\n"
        "/logs NAME — Show deployment logs\n"
        "/status — Show Hostify resource status\n"
        "/settings — Open Hostify settings\n"
        "/setbrand FIELD VALUE — Edit branding\n"
        "/messageonly — Browse editable message templates\n"
        "/setmsg KEY TEXT — Edit a message template\n"
        "/resetmsg KEY — Restore a default message\n"
        "/addadmin — Add an administrator by Telegram ID\n"
        "/removeadmin — Select an administrator to remove\n"
        "/github — Check GitHub App integration"
    )
    await _quote(message, text)

@Client.on_message(filters.command("stop") & filters.private)
async def stop_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    name = _requested_name(message)
    if not name:
        await _quote(message, "Usage: /stop NAME")
        return
    ok, detail = await stop_bot(name)
    await _quote(message, f"Bot stopped successfully: {name}." if ok else f"Could not stop {name}: {detail or 'operation failed'}.")

@Client.on_message(filters.command("restart") & filters.private)
async def restart_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    name = _requested_name(message)
    if not name:
        await _quote(message, "Usage: /restart NAME")
        return
    ok, detail = await restart_bot(name)
    await _quote(message, f"Bot restarted successfully: {name}." if ok else f"Could not restart {name}: {detail or 'operation failed'}.")

@Client.on_message(filters.command("delete") & filters.private)
async def delete_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    name = _requested_name(message)
    if not name:
        await _quote(message, "Usage: /delete NAME")
        return
    bot = await get_bot(name)
    if not bot:
        await _quote(message, f"No bot named {name} exists.")
        return
    _purge_expired_confirmations()
    token = secrets.token_urlsafe(9)
    confirmation = await _reply(
        message,
        f"Delete {name}? This stops the process and permanently removes its deployment files.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Confirm", callback_data=f"hostify:delete:confirm:{token}"),
            InlineKeyboardButton("Cancel", callback_data=f"hostify:delete:cancel:{token}"),
        ]]),
    )
    _PENDING_DELETES[token] = (
        message.chat.id, user.id, name, time.monotonic() + _DELETE_CONFIRM_TTL, confirmation.id
    )

@Client.on_callback_query(filters.regex(r"^hostify:delete:"))
async def delete_confirmation_handler(client: Client, query):
    user_id = query.from_user.id if query.from_user else None
    if not is_owner(user_id):
        await _answer(query, "Owner access required.", show_alert=True)
        return
    match = _DELETE_PATTERN.fullmatch(query.data or "")
    if not match:
        await _answer(query, "Invalid confirmation.", show_alert=True)
        return
    action, token = match.groups()
    pending = _PENDING_DELETES.get(token)
    if pending is None:
        await _answer(query, "Confirmation expired. Run /delete again.", show_alert=True)
        return
    chat_id, expected_user, name, expires, message_id = pending
    msg = query.message
    if not msg or msg.chat.type != ChatType.PRIVATE or msg.chat.id != chat_id or msg.id != message_id or user_id != expected_user:
        await _answer(query, "This confirmation does not belong to you.", show_alert=True)
        return
    if time.monotonic() > expires:
        _PENDING_DELETES.pop(token, None)
        await _answer(query, "Confirmation expired.", show_alert=True)
        return
    _PENDING_DELETES.pop(token, None)
    if action == "cancel":
        await msg.edit_text(format_output("Deletion cancelled."), parse_mode=ParseMode.DISABLED, reply_markup=_close_markup())
        await query.answer("Cancelled.")
        return
    ok, detail = await delete_bot(name)
    response = f"Deployment {name} deleted successfully." if ok else f"Could not delete {name}: {detail or 'operation failed'}"
    await msg.edit_text(format_output(response), parse_mode=ParseMode.DISABLED, reply_markup=_close_markup())
    await query.answer("Deletion complete." if ok else "Deletion failed.", show_alert=not ok)

@Client.on_message(filters.command("logs") & filters.private)
async def logs_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    name = _requested_name(message)
    if not name:
        await _quote(message, "Usage: /logs NAME")
        return
    try:
        lines = await read_logs(name, 40)
        if not lines:
            await _reply(message, f"No log output is available for {name}.")
            return
        await _reply(message, f"Last {len(lines)} lines for {name}:\n\n" + "\n".join(lines)[-3400:])
    except Exception:
        logger.exception("Log retrieval failed for %s", name)
        await _reply(message, f"Could not read logs for {name}.")

def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, total = divmod(total, 86400)
    hours, total = divmod(total, 3600)
    minutes, secs = divmod(total, 60)
    bits = []
    if days: bits.append(f"{days}d")
    if hours or days: bits.append(f"{hours}h")
    if minutes or hours or days: bits.append(f"{minutes}m")
    bits.append(f"{secs}s")
    return " ".join(bits)

@Client.on_message(filters.command("status") & filters.private)
async def status_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    try:
        cpu = await __import__("asyncio").to_thread(psutil.cpu_percent, interval=0.2)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(BASE_DIR))
        bots = await list_bots()
        running = sum(1 for bot in bots if bot.get("status") == "running")
        uptime = _format_duration(__import__("time").time() - psutil.boot_time())
        text = (
            f"Hostify Status\n\nCPU: {cpu:.1f}%\n"
            f"RAM: {memory.used / (1024 ** 2):.0f} / {memory.total / (1024 ** 2):.0f} MB\n"
            f"Disk: {disk.used / (1024 ** 3):.1f} / {disk.total / (1024 ** 3):.1f} GB\n"
            f"Uptime: {uptime}\nRunning deployments: {running} / {len(bots)}"
        )
        await _reply(message, text)
    except Exception:
        logger.exception("Status check failed")
        await _reply(message, "Unable to collect Hostify status. Check controller logs.")

@Client.on_message(filters.command("settings") & filters.private)
async def settings_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    await _reply(
        message,
        "Hostify settings\n\nManage branding, editable messages, administrators, GitHub access, deployment policy and host status.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Branding", callback_data="hostify:settings:branding"),
             InlineKeyboardButton("Message Templates", callback_data="hostify:settings:messages")],
            [InlineKeyboardButton("Admin Management", callback_data="hostify:settings:admins"),
             InlineKeyboardButton("GitHub App", callback_data="hostify:settings:github")],
            [InlineKeyboardButton("Host Status", callback_data="hostify:settings:status"),
             InlineKeyboardButton("Deployment Policy", callback_data="hostify:settings:policy")],
        ]),
    )

@Client.on_callback_query(filters.regex(r"^hostify:settings:"))
async def settings_callback(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await query.answer("Owner access required.", show_alert=True)
        return
    action = (query.data or "").rsplit(":", 1)[-1]
    if action == "root":
        await query.message.edit_text(
            format_output("Hostify settings\\n\\nManage branding, editable messages, administrators, GitHub access, deployment policy and host status."),
            parse_mode=ParseMode.DISABLED,
            reply_markup=_settings_keyboard(),
        )
        await query.answer()
        return
    screens = {
        "branding": "Branding fields: brand_name, owner_username, developer_username, developer_id, support_username, support_id, promo_channel_username, promo_channel_id, bot_channel_name, bot_channel_url, language, library, database.\\n\\nUse /setbrand FIELD VALUE.",
        "messages": "Use /messageonly to browse and edit every message template.",
        "admins": "Use /addadmin to add an admin, /removeadmin to remove one, and /admins to view the team.",
        "github": "Use /github to inspect the GitHub App connection and accessible repositories.",
        "status": "Use /status to view CPU, RAM, storage, uptime and deployment counts.",
        "policy": "Deployments are restricted to the owner and approved admins. The current Python child-process engine is not an isolation boundary. Do not enable deployment for untrusted administrators or projects.",
    }
    text = screens.get(action)
    if text is None:
        await query.answer("Unknown settings page.", show_alert=True)
        return
    await query.message.edit_text(
        format_output(text),
        parse_mode=ParseMode.DISABLED,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Back", callback_data="hostify:settings:root"),
             InlineKeyboardButton("Close", callback_data="hostify:ui:close")]
        ]),
    )
    await query.answer()

@Client.on_message(filters.command("setbrand") & filters.private)
async def setbrand_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        branding = get_branding()
        await _reply(
            message,
            "Usage: /setbrand FIELD VALUE\n\nEditable fields: " + ", ".join(sorted(branding.keys()))
                + "\nUse /settings for the full settings menu.",
        )
        return
    _, field, value = parts
    try:
        cleaned = await set_brand_value(field, value)
        await _quote(message, f"Updated {field} successfully.")
    except ValueError as exc:
        await _quote(message, f"Invalid branding value: {exc}")
    except Exception:
        logger.exception("Could not update branding field %s", field)
        await _reply(message, "Could not update branding. Check controller logs.")

@Client.on_message(filters.command("setmsg") & filters.private)
async def setmsg_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await _quote(message, "Usage: /setmsg KEY NEW_TEXT. Use /messageonly to find the key.")
        return
    _, key, value = parts
    key = key.upper()
    if key not in M.DEFAULT_MESSAGES:
        await _quote(message, "Unknown message key. Use /messageonly.")
        return
    try:
        await set_custom_message(key, value)
        M.apply_overrides({key: value})
        await _quote(message, f"Message template {key} updated successfully.")
    except Exception:
        logger.exception("Could not save message template %s", key)
        await _reply(message, "Could not save this message template.")

@Client.on_message(filters.command("resetmsg") & filters.private)
async def resetmsg_handler(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await _quote(message, "Usage: /resetmsg KEY. Use /messageonly to find the key.")
        return
    key = parts[1].strip().upper()
    if key not in M.DEFAULT_MESSAGES:
        await _quote(message, "Unknown message key. Use /messageonly.")
        return
    try:
        await reset_custom_message(key)
        M.reset_default(key)
        await _quote(message, f"Message template {key} restored to its default.")
    except Exception:
        logger.exception("Could not reset message template %s", key)
        await _reply(message, "Could not reset this message template.")

_KNOWN_COMMANDS = {
    "start", "command", "admins", "ping", "commands", "deploy", "cancel",
    "list", "stop", "restart", "delete", "logs", "status", "settings",
    "setbrand", "messageonly", "messages", "setmsg", "resetmsg",
    "addadmin", "removeadmin", "github", "mylogs", "stopme", "restartme",
}

@Client.on_message(filters.private & filters.regex(r"^/"), group=10)
async def unknown_command_handler(client: Client, message):
    parts = (message.text or "").split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lstrip("/").casefold() if parts else ""
    if command and command not in _KNOWN_COMMANDS and message.from_user and is_owner(message.from_user.id):
        await _reply(message, "Unknown command. Send /commands to view owner commands.")
