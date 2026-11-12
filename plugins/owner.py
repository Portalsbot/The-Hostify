from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from pathlib import Path

import psutil
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import messages as M
from branding import DEFAULT_BRANDING, get_branding, set_brand_value
from config import BASE_DIR
from database import get_bot, reset_custom_message, set_custom_message
from manager import delete_bot, read_logs, restart_bot, stop_bot
from smallcaps import sc
from utils import format_output
from .guard import is_owner

logger = logging.getLogger("hostify.plugins.owner")
_PENDING_BRAND: dict[int, tuple[int, str, int]] = {}
_PENDING_DELETE: dict[str, tuple[int, int, str, float]] = {}
_DELETE_TTL = 60
_LOG_LIMIT = 3_600


def _button(label: str, callback: str, *, style: str | None = None):
    kwargs = {"callback_data": callback}
    if style:
        kwargs["style"] = style
    try:
        return InlineKeyboardButton(label, **kwargs)
    except TypeError:
        kwargs.pop("style", None)
        return InlineKeyboardButton(label, **kwargs)


def _close():
    try:
        return InlineKeyboardButton("Close", callback_data="hostify:ui:close", style="danger")
    except TypeError:
        return InlineKeyboardButton("Close", callback_data="hostify:ui:close")


def _keyboard(rows=None):
    final = [list(row) for row in (rows or []) if row]
    if not any(button.callback_data == "hostify:ui:close" for row in final for button in row):
        final.append([_close()])
    return InlineKeyboardMarkup(final)


def _quote(text: str) -> str:
    return f"<blockquote>{html.escape(sc(format_output(text)))}</blockquote>"


async def _reply(message, text: str, *, quote: bool = True, markup=None):
    if quote:
        return await message.reply_text(
            _quote(text),
            parse_mode=ParseMode.HTML,
            reply_markup=markup or _keyboard(),
            disable_web_page_preview=True,
        )
    return await message.reply_text(
        format_output(text),
        parse_mode=ParseMode.DISABLED,
        reply_markup=markup or _keyboard(),
        disable_web_page_preview=True,
    )


async def _edit(message, text: str, *, quote: bool = True, markup=None):
    if quote:
        return await message.edit_text(
            _quote(text),
            parse_mode=ParseMode.HTML,
            reply_markup=markup or _keyboard(),
            disable_web_page_preview=True,
        )
    return await message.edit_text(
        format_output(text),
        parse_mode=ParseMode.DISABLED,
        reply_markup=markup or _keyboard(),
        disable_web_page_preview=True,
    )


async def _owner_reply(message) -> bool:
    return bool(message.from_user and is_owner(message.from_user.id))


def _brand_keyboard():
    keys = list(DEFAULT_BRANDING.keys())
    rows, row = [], []
    labels = {
        "brand_name": "Brand Name",
        "owner_username": "Owner Username",
        "developer_username": "Developer Username",
        "developer_id": "Developer ID",
        "support_username": "Support Username",
        "support_id": "Support ID",
        "promo_channel_username": "Promo Channel",
        "promo_channel_id": "Promo Channel ID",
        "bot_channel_name": "Bot Channel Name",
        "bot_channel_url": "Bot Channel URL",
        "language": "Language",
        "library": "Library",
        "database": "Database",
    }
    for key in keys:
        row.append(_button(labels.get(key, key.replace("_", " ").title()), f"hostify:brand:edit:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_button("Back", "hostify:settings:open")])
    return _keyboard(rows)


async def _settings_screen(message):
    branding = get_branding()
    fields = [
        f"Brand: {branding.get('brand_name')}",
        f"Owner: @{branding.get('owner_username')}",
        f"Developer: @{branding.get('developer_username')}",
        f"Support: @{branding.get('support_username')}",
        f"Language: {branding.get('language')}",
        f"Library: {branding.get('library')}",
        f"Database: {branding.get('database')}",
        f"Bot channel: {branding.get('bot_channel_name')}",
    ]
    rows = [
        [
            _button("Branding", "hostify:settings:branding"),
            _button("Messages", "hostify:message:list:0"),
        ],
        [
            _button("Deployments", "hostify:list:root:settings"),
            _button("Admins", "hostify:settings:admins"),
        ],
        [_button("GitHub Integration", "hostify:settings:github")],
        [_button("System Status", "hostify:settings:status")],
    ]
    await _edit(message, "HOSTIFY SETTINGS\n\n" + "\n".join(fields), markup=_keyboard(rows))


def cancel_brand_edit(user_id: int, chat_id: int) -> bool:
    pending = _PENDING_BRAND.get(user_id)
    if pending is None or pending[0] != chat_id:
        return False
    _PENDING_BRAND.pop(user_id, None)
    return True


@Client.on_message(filters.command("commands") & filters.private)
async def commands_handler(client: Client, message):
    user = message.from_user
    if not user:
        return
    if not is_owner(user.id):
        await _reply(message, "This command is not for you senpai.")
        return
    text = (
        "OWNER COMMANDS\\n\\n"
        "/commands — Show owner commands\\n"
        "/deploy — Start repository deployment (owner and admins)\\n"
        "/cancel — Cancel an active setup\\n"
        "/list — View every deployment\\n"
        "/stop NAME — Stop a deployed bot\\n"
        "/restart NAME — Restart a deployed bot\\n"
        "/delete NAME — Delete a deployed bot after confirmation\\n"
        "/logs NAME — Read the last 40 deployment log lines\\n"
        "/status — View Hostify CPU, RAM, storage and deployment status\\n"
        "/setting — Open Hostify settings\\n"
        "/settings — Alias for /setting\\n"
        "/setbrand FIELD VALUE — Edit Hostify branding\\n"
        "/messageonly — Browse every editable message template\\n"
        "/setmsg ID TEXT — Edit a message template\\n"
        "/resetmsg ID — Restore a default template\\n"
        "/addadmin — Add an administrator by Telegram ID\\n"
        "/removeadmin — Select and remove an administrator\\n"
        "/github — Check GitHub App connection and repository access"
    )
    await _reply(message, text)


@Client.on_message(filters.command(["settings", "setting"]) & filters.private)
async def settings_command(client: Client, message):
    if not await _owner_reply(message):
        await _reply(message, "Only the owner can open Hostify settings.")
        return
    sent = await _reply(message, "Opening Hostify settings…")
    await _settings_screen(sent)


@Client.on_message(filters.command("setbrand") & filters.private)
async def setbrand_command(client: Client, message):
    if not await _owner_reply(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await _reply(message, "Usage: /setbrand FIELD VALUE. Open /settings to see editable fields.")
        return
    _, field, value = parts
    try:
        cleaned = await set_brand_value(field, value)
        await _reply(message, f"{field} updated successfully: {cleaned}")
    except ValueError as exc:
        await _reply(message, f"Invalid branding value: {exc}")
    except Exception:
        logger.exception("Branding update failed for %s", field)
        await _reply(message, "Branding update failed. Check Hostify logs.")


@Client.on_message(filters.command("setmsg") & filters.private)
async def setmsg_command(client: Client, message):
    if not await _owner_reply(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await _reply(message, "Usage: /setmsg MESSAGE_ID new message text. Open /messageonly to see IDs.")
        return
    _, key, value = parts
    key = key.strip().upper()
    if key not in M.DEFAULT_MESSAGES:
        await _reply(message, "Unknown message ID. Open /messageonly to see valid IDs.")
        return
    try:
        await set_custom_message(key, value)
        M.apply_overrides({key: value})
        await _reply(message, f"{key} updated successfully.")
    except Exception:
        logger.exception("Could not save message template %s", key)
        await _reply(message, "Message update failed. Check Hostify logs.")


@Client.on_message(filters.command("resetmsg") & filters.private)
async def resetmsg_command(client: Client, message):
    if not await _owner_reply(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await _reply(message, "Usage: /resetmsg MESSAGE_ID.")
        return
    key = parts[1].strip().upper()
    if key not in M.DEFAULT_MESSAGES:
        await _reply(message, "Unknown message ID. Open /messageonly to see valid IDs.")
        return
    try:
        await reset_custom_message(key)
        M.reset_default(key)
        await _reply(message, f"{key} restored to its default message.")
    except Exception:
        logger.exception("Could not reset message template %s", key)
        await _reply(message, "Message reset failed. Check Hostify logs.")


@Client.on_message(filters.command("status") & filters.private)
async def status_command(client: Client, message):
    if not await _owner_reply(message):
        await _reply(message, "This command is not for you senpai.")
        return
    try:
        cpu = await asyncio.to_thread(psutil.cpu_percent, interval=0.2)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(BASE_DIR))
        from database import list_bots
        bots = await list_bots()
        running = sum(1 for bot in bots if bot.get("status") == "running")
        text = (
            "HOSTIFY STATUS\n"
            f"CPU: {cpu:.1f}%\n"
            f"RAM: {memory.used / 1024**2:.0f} / {memory.total / 1024**2:.0f} MB\n"
            f"DISK: {disk.used / 1024**3:.1f} / {disk.total / 1024**3:.1f} GB\n"
            f"DEPLOYMENTS: {running} running / {len(bots)} total"
        )
        await _reply(message, text)
    except Exception:
        logger.exception("Could not collect Hostify status")
        await _reply(message, "Unable to collect system status. Check Hostify logs.")


@Client.on_message(filters.command("stop") & filters.private)
async def stop_command(client: Client, message):
    if not await _owner_reply(message):
        await _reply(message, "Only the owner can stop deployed bots.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await _reply(message, "Usage: /stop NAME.")
        return
    name = parts[1]
    ok, detail = await stop_bot(name)
    await _reply(message, f"Bot stopped successfully: {name}." if ok else f"Could not stop {name}: {detail or 'operation failed'}.")


@Client.on_message(filters.command("restart") & filters.private)
async def restart_command(client: Client, message):
    if not await _owner_reply(message):
        await _reply(message, "Only the owner can restart deployed bots.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await _reply(message, "Usage: /restart NAME.")
        return
    name = parts[1]
    ok, detail = await restart_bot(name)
    await _reply(message, f"Bot restarted successfully: {name}." if ok else f"Could not restart {name}: {detail or 'operation failed'}.")


@Client.on_message(filters.command("delete") & filters.private)
async def delete_command(client: Client, message):
    if not await _owner_reply(message):
        await _reply(message, "Only the owner can delete deployments.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await _reply(message, "Usage: /delete NAME.")
        return
    name = parts[1]
    bot = await get_bot(name)
    if not bot:
        await _reply(message, f"No deployment named {name} exists.")
        return
    token = secrets.token_urlsafe(9)
    _PENDING_DELETE[token] = (message.chat.id, message.from_user.id, name, time.monotonic() + _DELETE_TTL)
    markup = _keyboard([[
        _button("Confirm Delete", f"hostify:ownerdelete:confirm:{token}", style="primary"),
        _button("Cancel", f"hostify:ownerdelete:cancel:{token}", style="danger"),
    ]])
    await _reply(message, f"Delete {name}? This permanently removes its files and database record.", markup=markup)


@Client.on_callback_query(filters.regex(r"^hostify:ownerdelete:"))
async def delete_confirmation(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await query.answer("Only the owner can confirm deletion.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 4:
        await query.answer("Invalid confirmation.", show_alert=True)
        return
    action, token = parts[2], parts[3]
    pending = _PENDING_DELETE.pop(token, None)
    if not pending:
        await query.answer("Confirmation expired. Use /delete again.", show_alert=True)
        return
    chat_id, owner_id, name, expiry = pending
    if chat_id != query.message.chat.id or owner_id != user.id or time.monotonic() > expiry:
        await query.answer("Confirmation expired or belongs to another user.", show_alert=True)
        return
    if action == "cancel":
        await _edit(query.message, f"Deletion cancelled. {name} was not changed.")
        await query.answer()
        return
    ok, detail = await delete_bot(name)
    await _edit(query.message, f"Deployment deleted successfully: {name}." if ok else f"Could not delete {name}: {detail or 'operation failed'}.")
    await query.answer()


@Client.on_message(filters.command("logs") & filters.private)
async def logs_command(client: Client, message):
    if not await _owner_reply(message):
        await _reply(message, "Only the owner can read deployed bot logs.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await _reply(message, "Usage: /logs NAME.")
        return
    try:
        lines = read_logs(parts[1], 40) or []
        output = f"LAST {len(lines)} LOG LINES: {parts[1]}\n\n" + ("\n".join(lines) if lines else "No log output is available.")
        await _reply(message, output[-_LOG_LIMIT:])
    except Exception:
        logger.exception("Could not read deployment logs for %s", parts[1])
        await _reply(message, "Could not read deployment logs. Check Hostify logs.")


@Client.on_callback_query(filters.regex(r"^hostify:settings:"))
async def settings_callback(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await query.answer("Owner access required.", show_alert=True)
        return
    action = (query.data or "").rsplit(":", 1)[-1]
    if action == "open":
        await _settings_screen(query.message)
    elif action == "branding":
        await _edit(query.message, "Select a branding field to edit.", markup=_brand_keyboard())
    elif action == "status":
        try:
            cpu = await asyncio.to_thread(psutil.cpu_percent, interval=0.2)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(str(BASE_DIR))
            from database import list_bots, verify_database_connection
            await verify_database_connection()
            bots = await list_bots(limit=2000)
            running = sum(1 for bot in bots if bot.get("status") == "running")
            overview = (
                "HOSTIFY SYSTEM OVERVIEW\\n\\n"
                f"Python: {__import__('sys').version.split()[0]}\\n"
                f"CPU: {cpu:.1f}%\\n"
                f"RAM: {memory.used / 1024**2:.0f} / {memory.total / 1024**2:.0f} MB\\n"
                f"Storage: {disk.used / 1024**3:.1f} / {disk.total / 1024**3:.1f} GB\\n"
                f"Deployments: {running} running / {len(bots)} total\\n"
                "Database: Connected"
            )
        except Exception:
            logger.exception("System overview check failed")
            overview = "HOSTIFY SYSTEM OVERVIEW\\n\\nDatabase or system metrics could not be collected. Check Hostify logs."
        await _edit(query.message, overview, markup=_keyboard([[_button("Back", "hostify:settings:open")]]))
    elif action == "github":
        await _edit(
            query.message,
            "GitHub App integration\n\nUse /github to inspect the App connection, installation and repositories.\n\nCredentials are stored in Render environment variables, never in Telegram.",
            markup=_keyboard([[_button("Refresh GitHub", "hostify:settings:github_refresh")], [_button("Back", "hostify:settings:open")]]),
        )
    elif action == "github_refresh":
        try:
            from .github import _screen_text
            github_text, connected = await _screen_text()
            rows = [[_button("Refresh", "hostify:settings:github_refresh")]]
            rows.append([_button("Back", "hostify:settings:github")])
            await _edit(query.message, github_text, markup=_keyboard(rows))
        except Exception:
            logger.exception("Could not refresh GitHub status from settings")
            await _edit(
                query.message,
                "Could not refresh GitHub status. Use /github to inspect the connection.",
                markup=_keyboard([[_button("Back", "hostify:settings:github")]]),
            )
    elif action == "admins":
        from .admins import cancel_admin_add
        cancel_admin_add(user.id, query.message.chat.id)
        await _edit(
            query.message,
            "ADMIN MANAGEMENT\\n\\nAdd a trusted administrator, remove existing access, or view the current admin team.",
            markup=_keyboard([
                [
                    _button("Add Admin", "hostify:adminpanel:add", style="success"),
                    _button("Remove Admin", "hostify:adminpanel:remove", style="danger"),
                ],
                [_button("View Admins", "hostify:adminpanel:view")],
                [_button("Back", "hostify:settings:open")],
            ]),
        )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^hostify:brand:edit:"))
async def brand_edit_callback(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await query.answer("Owner access required.", show_alert=True)
        return
    field = (query.data or "").rsplit(":", 1)[-1]
    if field not in DEFAULT_BRANDING:
        await query.answer("Unknown branding field.", show_alert=True)
        return
    _PENDING_BRAND[user.id] = (query.message.chat.id, field, query.message.id)
    await _edit(query.message, f"Send the new value for {field}. Use /cancel to abort.")
    await query.answer()


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=8)
async def brand_value_input(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    pending = _PENDING_BRAND.get(user.id)
    if not pending or pending[0] != message.chat.id:
        return
    field = pending[1]
    prompt_id = pending[2]
    try:
        value = await set_brand_value(field, message.text.strip())
        _PENDING_BRAND.pop(user.id, None)
        prompt = await client.get_messages(message.chat.id, prompt_id)
        await _edit(
            prompt,
            f"{field} updated successfully: {value}.",
            markup=_keyboard([[_button("Back to Branding", "hostify:settings:branding")]]),
        )
    except ValueError as exc:
        prompt = await client.get_messages(message.chat.id, prompt_id)
        await _edit(prompt, f"Invalid value: {exc}. Send it again or /cancel.")


@Client.on_message(filters.private, group=10)
async def unknown_owner_command(client: Client, message):
    if not message.from_user or not is_owner(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lstrip("/").casefold() if parts and parts[0].startswith("/") else ""
    known = {
        "start", "command", "commands", "admins", "deploy", "cancel", "cancle", "list",
        "stop", "restart", "delete", "logs", "status", "ping", "settings",
        "setting", "setbrand", "messageonly", "setmsg", "resetmsg",
        "addadmin", "removeadmin", "github", "mylogs", "restarme", "stopme",
    }
    if command and command not in known:
        await _reply(message, "Unknown command. Use /commands to view owner commands.")
