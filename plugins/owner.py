from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import messages as M
from branding import get_branding, set_brand_value
from config import BASE_DIR
from database import (
    add_admin, get_admins, get_bot, is_admin, list_bots, remove_admin,
    reset_custom_message, set_custom_message, update_bot_env,
)
from deployer import _validate_env_vars, _validate_name
from manager import delete_bot, read_logs, restart_bot, stop_bot, launch_existing
from runtime_logs import get_recent_logs
from smallcaps import sc
from utils import format_output
from .guard import is_owner

logger = logging.getLogger("hostify.plugins.owner")

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_DELETE_TTL = 60
_PENDING_INPUT: dict[int, dict[str, Any]] = {}
_PENDING_DELETE: dict[str, tuple[int, int, str, float]] = {}
_PAGE_SIZE = 10
SAFE_LIMIT = 3600


def _button(label: str, *, callback: str | None = None, url: str | None = None, style: str | None = None):
    kwargs: dict[str, Any] = {}
    if callback is not None:
        kwargs["callback_data"] = callback
    if url is not None:
        kwargs["url"] = url
    if style is not None:
        kwargs["style"] = style
    try:
        return InlineKeyboardButton(label, **kwargs)
    except TypeError:
        # Older Pyrofork installs may not expose Bot API button styles.
        kwargs.pop("style", None)
        return InlineKeyboardButton(label, **kwargs)


def _close_button():
    return _button("Close", callback="hostify:ui:close", style="danger")


def _markup(rows: list[list[InlineKeyboardButton]] | None = None, *, close: bool = True):
    final_rows = [list(row) for row in (rows or []) if row]
    if close and not any(
        button.callback_data == "hostify:ui:close"
        for row in final_rows for button in row
    ):
        final_rows.append([_close_button()])
    return InlineKeyboardMarkup(final_rows)


def _html_text(text: str) -> str:
    return html.escape(sc(format_output(text)))


def _quote(text: str) -> str:
    return f"<blockquote>{_html_text(text)}</blockquote>"


async def _reply(message, text: str, *, quote: bool = False, reply_markup=None, **kwargs):
    markup = reply_markup or _markup()
    if quote:
        return await message.reply_text(
            _quote(text),
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
            **kwargs,
        )
    return await message.reply_text(
        format_output(text),
        parse_mode=ParseMode.DISABLED,
        reply_markup=markup,
        **kwargs,
    )


async def _edit(message, text: str, *, quote: bool = False, keyboard=None):
    markup = keyboard or _markup()
    if quote:
        return await message.edit_text(
            _quote(text),
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    return await message.edit_text(
        format_output(text),
        parse_mode=ParseMode.DISABLED,
        reply_markup=markup,
        disable_web_page_preview=True,
    )


async def _answer(query, text: str, *, alert: bool = False):
    try:
        await query.answer(sc(text), show_alert=alert)
    except Exception:
        logger.debug("Could not answer callback query", exc_info=True)


async def _is_owner_message(message) -> bool:
    return bool(message.from_user and is_owner(message.from_user.id))


async def _is_admin_message(message) -> bool:
    return bool(message.from_user and await is_admin(message.from_user.id))


def _requested_name(message) -> str | None:
    parts = (message.text or "").split()
    if len(parts) != 2 or not _NAME_PATTERN.fullmatch(parts[1]):
        return None
    return parts[1]


def _template_label(key: str) -> str:
    value = re.sub(r"^M\d{2,3}_", "", key)
    return value.replace("_", " ").title()


def _template_keyboard():
    keys = sorted(M.DEFAULT_MESSAGES)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key in keys:
        row.append(_button(_template_label(key), callback=f"hostify:msg:open:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_button("Back to Settings", callback="hostify:settings:open")])
    return _markup(rows)


async def _show_template_list(message, *, edit: bool = False):
    text = f"Click any button to edit a message.\n\nAvailable templates: {len(M.DEFAULT_MESSAGES)}"
    if edit:
        await _edit(message, text, keyboard=_template_keyboard())
    else:
        await _reply(message, text, reply_markup=_template_keyboard())


def _template_screen(key: str) -> str:
    template = M.DEFAULT_MESSAGES[key]
    current = getattr(M, key, template)
    # Keep the template identifier in monospace for reliable copying.
    return (
        f"<b>{html.escape(_template_label(key))}</b>\n"
        f"Message ID: <code>{html.escape(key)}</code>\n\n"
        f"<blockquote>{html.escape(str(current))}</blockquote>"
    )


async def _edit_template_screen(message, key: str):
    rows = [
        [
            _button("Edit Text", callback=f"hostify:msg:edit:{key}", style="success"),
            _button("Reset", callback=f"hostify:msg:reset:{key}", style="danger"),
        ],
        [_button("Back", callback="hostify:msg:list")],
    ]
    await message.edit_text(
        _styled_template_screen(key),
        parse_mode=ParseMode.HTML,
        reply_markup=_markup(rows),
        disable_web_page_preview=True,
    )


def _styled_template_screen(key: str) -> str:
    template = M.DEFAULT_MESSAGES[key]
    current = str(getattr(M, key, template))
    return (
        f"<b>{html.escape(sc(_template_label(key)))}</b>\n"
        f"Message ID: <code>{html.escape(key)}</code>\n\n"
        f"<blockquote>{html.escape(sc(current))}</blockquote>"
    )


def _admin_keyboard(admins: list[dict[str, Any]], *, remove_mode: bool):
    rows: list[list[InlineKeyboardButton]] = []
    if remove_mode:
        for admin in admins:
            uid = int(admin.get("user_id") or 0)
            if uid <= 0:
                continue
            label = str(admin.get("first_name") or admin.get("username") or uid)[:28]
            rows.append([
                _button(label, url=f"tg://user?id={uid}"),
                _button("Remove", callback=f"hostify:admin:remove:{uid}", style="danger"),
            ])
    else:
        row: list[InlineKeyboardButton] = []
        for admin in admins:
            uid = int(admin.get("user_id") or 0)
            if uid <= 0:
                continue
            label = str(admin.get("first_name") or admin.get("username") or uid)[:30]
            row.append(_button(label, url=f"tg://user?id={uid}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([_button("Back", callback="hostify:settings:open")])
    return _markup(rows)


async def _show_admins(client: Client, chat_id: int, *, edit_message=None):
    admins = await get_admins()
    lines = ["These are my admins."]
    if admins:
        for item in admins:
            uid = int(item.get("user_id") or 0)
            username = f"@{item['username']}" if item.get("username") else "no username"
            lines.append(f"• {item.get('first_name') or 'Admin'} — {username} — ID {uid}")
    else:
        lines.append("No admins have been added yet.")
    keyboard = _admin_keyboard(admins, remove_mode=False)
    if edit_message:
        await edit_message.edit_text(
            _quote("\n".join(lines)),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        await client.send_message(
            chat_id,
            _quote("\n".join(lines)),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def _show_remove_admins(client: Client, message):
    admins = await get_admins()
    if not admins:
        await _edit(message, "There are no admins to remove.", keyboard=_markup([
            [_button("Back", callback="hostify:settings:open")]
        ]))
        return
    await message.edit_text(
        _quote("Select the admin you want to remove."),
        parse_mode=ParseMode.HTML,
        reply_markup=_admin_keyboard(admins, remove_mode=True),
    )


async def _accessible_bots(user_id: int) -> list[dict[str, Any]]:
    if is_owner(user_id):
        return await list_bots()
    return await list_bots(deployed_by=user_id)


def _list_home_keyboard():
    return _markup([
        [
            _button("Running", callback="hostify:list:page:running:0", style="success"),
            _button("Stopped", callback="hostify:list:page:stopped:0", style="danger"),
        ],
    ])


def _bot_page_keyboard(bots: list[dict[str, Any]], status: str, page: int, total_pages: int):
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for bot in bots:
        name = str(bot.get("name") or "unknown")
        row.append(_button(name[:32], callback=f"hostify:list:open:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(_button("Previous", callback=f"hostify:list:page:{status}:{page-1}"))
    if page + 1 < total_pages:
        nav.append(_button("Next", callback=f"hostify:list:page:{status}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([_button("Back", callback="hostify:list:home")])
    return _markup(rows)


def _bot_owner_controls(bot: dict[str, Any]) -> list[list[InlineKeyboardButton]]:
    name = str(bot.get("name") or "")
    status = str(bot.get("status") or "stopped")
    action = (
        _button("Stop", callback=f"hostify:bot:stop:{name}", style="danger")
        if status == "running"
        else _button("Run", callback=f"hostify:bot:run:{name}", style="success")
    )
    return [
        [
            _button("Edit Environment", callback=f"hostify:bot:editenv:{name}", style="success"),
            _button("Environment / API", callback=f"hostify:bot:env:{name}"),
        ],
        [
            action,
            _button("Restart", callback=f"hostify:bot:restart:{name}", style="success"),
        ],
        [
            _button("Logs", callback=f"hostify:bot:logs:{name}"),
            _button("Delete", callback=f"hostify:bot:delete:{name}", style="danger"),
        ],
    ]


def _bot_detail_text(bot: dict[str, Any]) -> str:
    env_vars = bot.get("env_vars") or {}
    keys = sorted(str(key) for key in env_vars if key != "NOTIFY_CHANNEL")
    keys_text = ", ".join(keys) if keys else "None"
    pid = bot.get("pid") or "Not running"
    created = bot.get("created_at")
    created_text = created.isoformat(timespec="minutes") if isinstance(created, datetime) else "Unknown"
    return (
        f"Deployment: {bot.get('name')}\n"
        f"Status: {str(bot.get('status') or 'unknown').upper()}\n"
        f"Process ID: {pid}\n"
        f"Repository: {bot.get('repo_url') or 'Unknown'}\n"
        f"Bot username: @{bot.get('bot_username')}" if bot.get("bot_username") else
        f"Deployment: {bot.get('name')}\n"
        f"Status: {str(bot.get('status') or 'unknown').upper()}\n"
        f"Process ID: {pid}\n"
        f"Repository: {bot.get('repo_url') or 'Unknown'}\n"
        f"Bot username: Not detected"
    ) + (
        f"\nEnvironment keys: {keys_text}\n"
        "Secret values are hidden.\n"
        f"Created: {created_text}"
    )


async def _show_bot_list(message, user_id: int, status: str, page: int, *, edit: bool = True):
    bots = await _accessible_bots(user_id)
    matching = [
        bot for bot in bots
        if (str(bot.get("status") or "stopped") == "running") == (status == "running")
    ]
    matching.sort(key=lambda item: str(item.get("name", "")).casefold())
    total_pages = max(1, (len(matching) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    selected = matching[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]
    heading = "Here are all running deployments." if status == "running" else "Here are all stopped deployments."
    if not selected:
        heading += "\n\nNo deployments in this category."
    else:
        heading += f"\n\nPage {page + 1}/{total_pages} · {len(matching)} deployments"
    keyboard = _bot_page_keyboard(selected, status, page, total_pages)
    if edit:
        await _edit(message, heading, keyboard=keyboard)
    else:
        await _reply(message, heading, reply_markup=keyboard)


async def _show_bot_detail(message, user_id: int, name: str):
    if not _NAME_PATTERN.fullmatch(name):
        await _edit(message, "Invalid deployment name.")
        return
    bot = await get_bot(name)
    if not bot:
        await _edit(message, "Deployment not found.")
        return
    owner_view = is_owner(user_id)
    if not owner_view and bot.get("deployed_by") != user_id:
        await _edit(message, "This deployment does not belong to your admin account.")
        return
    rows = _bot_owner_controls(bot) if owner_view else []
    rows.append([_button("Back", callback="hostify:list:home")])
    await message.edit_text(
        _quote(_bot_detail_text(bot)),
        parse_mode=ParseMode.HTML,
        reply_markup=_markup(rows),
        disable_web_page_preview=True,
    )


def _settings_keyboard():
    branding = [
        _button("Branding", callback="hostify:settings:branding"),
        _button("Messages", callback="hostify:msg:list"),
    ]
    integrations = [
        _button("GitHub", callback="hostify:settings:github"),
        _button("Admins", callback="hostify:settings:admins"),
    ]
    return _markup([branding, integrations])


def _branding_keyboard():
    values = [
        ("brand_name", "Brand Name"),
        ("owner_username", "Owner Username"),
        ("owner_id", "Owner ID"),
        ("developer_username", "Developer Username"),
        ("developer_id", "Developer ID"),
        ("support_username", "Support Username"),
        ("support_id", "Support ID"),
        ("promo_channel_username", "Promo Channel"),
        ("promo_channel_id", "Promo Channel ID"),
        ("language", "Language"),
        ("library", "Library"),
        ("database_label", "Database"),
        ("bot_channel_name", "Bot Channel Name"),
        ("bot_channel_url", "Bot Channel URL"),
        ("bot_image_url", "Brand Image URL"),
    ]
    rows = []
    row = []
    for key, label in values:
        row.append(_button(label, callback=f"hostify:brand:edit:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_button("Back", callback="hostify:settings:open")])
    return _markup(rows)


async def _show_settings(message):
    branding = get_branding()
    text = (
        "Hostify Settings\n\n"
        f"Brand: {branding.get('brand_name')}\n"
        f"Owner: @{branding.get('owner_username', 'reactiveHCL')}\n"
        f"Developer: @{branding.get('developer_username')}\n"
        f"Support: @{branding.get('support_username')}\n"
        f"Language: {branding.get('language', 'Python 3.13')}\n"
        f"Library: {branding.get('library', 'Pyrofork')}\n"
        f"Database: {branding.get('database_label', 'MongoDB')}"
    )
    await message.edit_text(
        _quote(text),
        parse_mode=ParseMode.HTML,
        reply_markup=_settings_keyboard(),
    )


@Client.on_message(filters.command("commands") & filters.private)
async def commands_handler(client: Client, message):
    user = message.from_user
    if not user:
        return
    if not is_owner(user.id):
        await _reply(message, "This command is not for you senpai.", quote=True)
        return
    text = (
        "Owner Commands\n\n"
        "/commands — Display this owner command list\n"
        "/deploy — Deploy a repository (owner and admins)\n"
        "/cancel — Cancel deployment setup\n"
        "/list — View every deployment\n"
        "/stop NAME — Stop a deployment\n"
        "/restart NAME — Restart a deployment\n"
        "/delete NAME — Delete a deployment\n"
        "/logs NAME — View deployment logs\n"
        "/status — Hostify resource status\n"
        "/ping — Check Telegram connectivity\n"
        "/settings — Open Hostify settings\n"
        "/setbrand FIELD VALUE — Change branding\n"
        "/messageonly — Open editable message templates\n"
        "/setmsg ID TEXT — Edit a message template\n"
        "/resetmsg ID — Restore a default message\n"
        "/addadmin — Add an admin by Telegram ID\n"
        "/removeadmin — Remove an admin\n"
        "/admins — View the admin team\n"
        "/github — Check GitHub App integration\n"
        "/mylogs — View the last 40 Hostify log lines"
    )
    await _reply(message, text, quote=True)


@Client.on_message(filters.command("addadmin") & filters.private)
async def addadmin_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "Only the owner can add admins.", quote=True)
        return
    _PENDING_INPUT[message.from_user.id] = {
        "action": "add_admin",
        "chat_id": message.chat.id,
    }
    await _reply(message, "Send me the new admin ID.", quote=True)


@Client.on_message(filters.command("removeadmin") & filters.private)
async def removeadmin_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "Only the owner can remove admins.", quote=True)
        return
    await _show_remove_admins(client, await message.reply_text(
        "Select the admin you want to remove.",
        reply_markup=_markup(),
    ))


@Client.on_message(filters.command("admins") & filters.private)
async def admins_command_handler(client: Client, message):
    await _show_admins(client, message.chat.id)


@Client.on_message(filters.command("messageonly") & filters.private)
async def messageonly_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "Only the owner can edit message templates.", quote=True)
        return
    await _show_template_list(message)


@Client.on_message(filters.command("setmsg") & filters.private)
async def setmsg_handler(client: Client, message):
    if not await _is_owner_message(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await _reply(message, "Usage: /setmsg MESSAGE_ID new message text.", quote=True)
        return
    _, key, value = parts
    key = key.strip().upper()
    if key not in M.DEFAULT_MESSAGES:
        await _reply(message, "Unknown message ID. Open /messageonly to see valid IDs.", quote=True)
        return
    try:
        await set_custom_message(key, value)
        M.apply_overrides({key: value})
        await _reply(message, f"{_template_label(key)} updated successfully.", quote=True)
    except Exception:
        logger.exception("Could not update message template %s", key)
        await _reply(message, "Message update failed. Check Hostify logs.", quote=True)


@Client.on_message(filters.command("resetmsg") & filters.private)
async def resetmsg_handler(client: Client, message):
    if not await _is_owner_message(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await _reply(message, "Usage: /resetmsg MESSAGE_ID.", quote=True)
        return
    key = parts[1].strip().upper()
    if key not in M.DEFAULT_MESSAGES:
        await _reply(message, "Unknown message ID. Open /messageonly to see valid IDs.", quote=True)
        return
    try:
        await reset_custom_message(key)
        M.reset_default(key)
        await _reply(message, f"{_template_label(key)} restored to its default text.", quote=True)
    except Exception:
        logger.exception("Could not reset message template %s", key)
        await _reply(message, "Message reset failed. Check Hostify logs.", quote=True)


@Client.on_message(filters.command("settings") & filters.private)
async def settings_command_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "Only the owner can open Hostify settings.", quote=True)
        return
    sent = await message.reply_text("Opening settings…", reply_markup=_markup())
    await _show_settings(sent)


@Client.on_message(filters.command("setbrand") & filters.private)
async def setbrand_command_handler(client: Client, message):
    if not await _is_owner_message(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await _reply(message, "Usage: /setbrand FIELD VALUE. Use /settings for editable fields.", quote=True)
        return
    _, field, value = parts
    try:
        cleaned = await set_brand_value(field, value)
        await _reply(message, f"{field} updated successfully: {cleaned}", quote=True)
    except ValueError as exc:
        await _reply(message, f"Invalid branding value: {exc}", quote=True)
    except Exception:
        logger.exception("Branding update failed")
        await _reply(message, "Branding update failed. Check Hostify logs.", quote=True)


@Client.on_message(filters.command("list") & filters.private)
async def list_command_handler(client: Client, message):
    user = message.from_user
    if not user:
        return
    if not (is_owner(user.id) or await is_admin(user.id)):
        await _reply(message, "Deployment access is restricted to the owner and approved admins.", quote=True)
        return
    await _reply(
        message,
        "Here is the list of all bots!",
        quote=True,
        reply_markup=_list_home_keyboard(),
    )


@Client.on_message(filters.command("stop") & filters.private)
async def stop_command_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "Only the owner can stop deployments.", quote=True)
        return
    name = _requested_name(message)
    if not name:
        await _reply(message, "Usage: /stop NAME.", quote=True)
        return
    ok, detail = await stop_bot(name)
    await _reply(message, f"Stopped {name}." if ok else f"Could not stop {name}: {detail or 'operation failed'}.", quote=True)


@Client.on_message(filters.command("restart") & filters.private)
async def restart_command_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "Only the owner can restart deployments.", quote=True)
        return
    name = _requested_name(message)
    if not name:
        await _reply(message, "Usage: /restart NAME.", quote=True)
        return
    ok, detail = await restart_bot(name)
    await _reply(message, f"Restarted {name}." if ok else f"Could not restart {name}: {detail or 'operation failed'}.", quote=True)


@Client.on_message(filters.command("delete") & filters.private)
async def delete_command_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "Only the owner can delete deployments.", quote=True)
        return
    name = _requested_name(message)
    if not name:
        await _reply(message, "Usage: /delete NAME.", quote=True)
        return
    bot = await get_bot(name)
    if not bot:
        await _reply(message, f"No deployment named {name} exists.", quote=True)
        return
    token = secrets.token_urlsafe(9)
    _PENDING_DELETE[token] = (
        message.chat.id, message.from_user.id, name, time.monotonic() + _DELETE_TTL
    )
    keyboard = _markup([[
        _button("Confirm", callback=f"hostify:delete:confirm:{token}", style="danger"),
        _button("Cancel", callback=f"hostify:delete:cancel:{token}"),
    ]])
    await _reply(message, f"Delete {name}? This action permanently removes its files and database record.", quote=True, reply_markup=keyboard)


@Client.on_message(filters.command("logs") & filters.private)
async def logs_command_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "Only the owner can view deployment logs.", quote=True)
        return
    name = _requested_name(message)
    if not name:
        await _reply(message, "Usage: /logs NAME.", quote=True)
        return
    try:
        lines = read_logs(name, 40) or []
        output = f"Last {len(lines)} lines for {name}:\n" + ("\n".join(lines) if lines else "No log output is available.")
        if len(output) > SAFE_LIMIT:
            output = output[-SAFE_LIMIT:]
        await _reply(message, output, quote=True)
    except Exception:
        logger.exception("Could not read deployment logs for %s", name)
        await _reply(message, "Could not read deployment logs. Check Hostify logs.", quote=True)


@Client.on_message(filters.command("status") & filters.private)
async def status_command_handler(client: Client, message):
    if not await _is_owner_message(message):
        await _reply(message, "This command is not for you senpai.", quote=True)
        return
    try:
        cpu = await asyncio.to_thread(psutil.cpu_percent, interval=0.2)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(BASE_DIR))
        bots = await list_bots()
        running = sum(1 for bot in bots if bot.get("status") == "running")
        text = (
            "Hostify Status\n"
            f"CPU: {cpu:.1f}%\n"
            f"RAM: {memory.used / 1024**2:.0f} / {memory.total / 1024**2:.0f} MB\n"
            f"Disk: {disk.used / 1024**3:.1f} / {disk.total / 1024**3:.1f} GB\n"
            f"Deployments running: {running}/{len(bots)}"
        )
        await _reply(message, text, quote=True)
    except Exception:
        logger.exception("Could not collect Hostify status")
        await _reply(message, "Unable to collect system status.", quote=True)


@Client.on_message(filters.command("mylogs") & filters.private)
async def mylogs_command_handler(client: Client, message):
    user = message.from_user
    if not user or not (is_owner(user.id) or await is_admin(user.id)):
        await _reply(message, "This command is not for you senpai.", quote=True)
        return
    lines = get_recent_logs(40)
    output = "Last 40 Hostify log lines:\n" + ("\n".join(lines) if lines else "No controller logs are available yet.")
    await _reply(message, output[-SAFE_LIMIT:], quote=True)


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=5)
async def guided_input_handler(client: Client, message):
    user = message.from_user
    if not user or not message.text:
        return
    session = _PENDING_INPUT.get(user.id)
    if not session or session.get("chat_id") != message.chat.id:
        return
    action = session.get("action")

    if action == "add_admin":
        if not is_owner(user.id):
            _PENDING_INPUT.pop(user.id, None)
            return
        try:
            new_id = int(message.text.strip())
            if new_id <= 0 or is_owner(new_id):
                raise ValueError
        except ValueError:
            await _reply(message, "Send a valid positive Telegram user ID.")
            return
        try:
            profile = await client.get_users(new_id)
            await add_admin(
                new_id,
                username=profile.username or "",
                first_name=profile.first_name or "",
                added_by=user.id,
            )
            _PENDING_INPUT.pop(user.id, None)
            await _reply(message, f"Admin {new_id} added successfully.", quote=True)
            try:
                await client.send_message(
                    new_id,
                    format_output("You are now also my admin! Send /start to open Hostify."),
                    reply_markup=_markup(),
                    parse_mode=ParseMode.DISABLED,
                )
            except Exception:
                await _reply(message, "Admin saved. They must open the bot and send /start before Hostify can DM them.")
        except Exception:
            logger.exception("Adding admin %s failed", new_id)
            await _reply(message, "Could not add this admin. Check the ID and try again.")
        return

    if action == "edit_brand":
        if not is_owner(user.id):
            _PENDING_INPUT.pop(user.id, None)
            return
        field = str(session.get("field") or "")
        try:
            value = await set_brand_value(field, message.text.strip())
            _PENDING_INPUT.pop(user.id, None)
            await _reply(message, f"{field} updated successfully: {value}", quote=True)
        except Exception as exc:
            await _reply(message, f"Invalid value: {exc}. Send it again or /cancel.")
        return

    if action == "edit_template":
        if not is_owner(user.id):
            _PENDING_INPUT.pop(user.id, None)
            return
        key = str(session.get("key") or "")
        try:
            await set_custom_message(key, message.text)
            M.apply_overrides({key: message.text})
            _PENDING_INPUT.pop(user.id, None)
            await _reply(message, f"{_template_label(key)} updated successfully.", quote=True)
        except Exception as exc:
            await _reply(message, f"Could not update this message: {exc}. Try again or /cancel.")
        return

    if action == "edit_env":
        if not is_owner(user.id):
            _PENDING_INPUT.pop(user.id, None)
            return
        name = str(session.get("name") or "")
        try:
            env_vars = {}
            for index, line in enumerate(message.text.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                if "=" not in line:
                    raise ValueError(f"Line {index} must use KEY=VALUE.")
                key, value = line.split("=", 1)
                if key.strip() in env_vars:
                    raise ValueError(f"Duplicate key: {key.strip()}.")
                env_vars[key.strip()] = value
            env_vars = _validate_env_vars(env_vars)
            await update_bot_env(name, env_vars)
            bot = await get_bot(name)
            if bot and bot.get("status") == "running":
                ok, detail = await restart_bot(name)
                if not ok:
                    raise RuntimeError(f"Environment saved, but restart failed: {detail}")
            _PENDING_INPUT.pop(user.id, None)
            await _reply(message, f"Environment updated for {name}.", quote=True)
        except Exception as exc:
            await _reply(message, f"Environment update failed: {exc}. Send the complete KEY=VALUE list again, or /cancel.")
        return


@Client.on_message(filters.command("cancel") & filters.private, group=1)
async def cancel_guided_input(client: Client, message):
    user = message.from_user
    if not user:
        return
    if _PENDING_INPUT.pop(user.id, None) is not None:
        await _reply(message, "Cancelled successfully!", quote=True)


@Client.on_callback_query(filters.regex(r"^hostify:list:"))
async def list_navigation(client: Client, query):
    user = query.from_user
    if not user or query.message is None:
        await _answer(query, "Invalid request.", alert=True)
        return
    if not (is_owner(user.id) or await is_admin(user.id)):
        await _answer(query, "This command is not for you senpai.", alert=True)
        return
    parts = (query.data or "").split(":")
    action = parts[2] if len(parts) > 2 else ""
    try:
        if action == "home":
            await _edit(query.message, "Here is the list of all bots!", keyboard=_list_home_keyboard())
        elif action == "page" and len(parts) == 5:
            await _show_bot_list(query.message, user.id, parts[3], int(parts[4]), edit=True)
        elif action == "open" and len(parts) == 4:
            await _show_bot_detail(query.message, user.id, parts[3])
        await query.answer()
    except Exception:
        logger.exception("List navigation failed")
        await _answer(query, "Could not open this deployment view.", alert=True)


@Client.on_callback_query(filters.regex(r"^hostify:bot:"))
async def bot_detail_actions(client: Client, query):
    user = query.from_user
    if not user or query.message is None:
        await _answer(query, "Invalid request.", alert=True)
        return
    if not is_owner(user.id):
        await _answer(query, "Only the owner can change deployments.", alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) < 4:
        await _answer(query, "Invalid action.", alert=True)
        return
    action, name = parts[2], parts[3]
    if not _NAME_PATTERN.fullmatch(name):
        await _answer(query, "Invalid deployment name.", alert=True)
        return
    bot = await get_bot(name)
    if not bot:
        await _answer(query, "Deployment not found.", alert=True)
        await _edit(query.message, "Deployment not found.")
        return

    try:
        if action == "env":
            variables = bot.get("env_vars") or {}
            lines = [f"{key}: {'configured (hidden)' if value else 'empty'}" for key, value in sorted(variables.items())]
            text = f"Environment and API configuration for {name}:\n" + ("\n".join(lines) if lines else "No variables configured.")
            await _edit(query.message, text, quote=True, keyboard=_markup([
                [_button("Edit Environment", callback=f"hostify:bot:editenv:{name}", style="success")],
                [_button("Back", callback=f"hostify:list:open:{name}")],
            ]))
        elif action == "editenv":
            _PENDING_INPUT[user.id] = {
                "action": "edit_env", "chat_id": query.message.chat.id, "name": name
            }
            await _edit(
                query.message,
                f"Send the complete environment as KEY=VALUE, one per line, for {name}. Values are encrypted in MongoDB. Send /cancel to abort.",
                quote=True,
            )
        elif action == "logs":
            lines = read_logs(name, 40) or []
            output = f"Last {len(lines)} lines for {name}:\n" + ("\n".join(lines) if lines else "No logs available.")
            await _edit(query.message, output[-SAFE_LIMIT:], quote=True, keyboard=_markup([
                [_button("Back", callback=f"hostify:list:open:{name}")]
            ]))
        elif action == "stop":
            ok, detail = await stop_bot(name)
            await _answer(query, f"Stopped {name}." if ok else f"Stop failed: {detail}", alert=not ok)
            await _show_bot_detail(query.message, user.id, name)
        elif action == "run":
            ok, detail = await launch_existing(name, bot.get("env_vars") or {})
            await _answer(query, f"Started {name}." if ok else f"Start failed: {detail}", alert=not ok)
            await _show_bot_detail(query.message, user.id, name)
        elif action == "restart":
            ok, detail = await restart_bot(name)
            await _answer(query, f"Restarted {name}." if ok else f"Restart failed: {detail}", alert=not ok)
            await _show_bot_detail(query.message, user.id, name)
        elif action == "delete":
            token = secrets.token_urlsafe(9)
            _PENDING_DELETE[token] = (
                query.message.chat.id, user.id, name, time.monotonic() + _DELETE_TTL
            )
            await _edit(query.message, f"Confirm permanent deletion of {name}?", quote=True, keyboard=_markup([
                [
                    _button("Confirm Delete", callback=f"hostify:delete:confirm:{token}", style="danger"),
                    _button("Cancel", callback=f"hostify:delete:cancel:{token}"),
                ],
            ]))
        await query.answer()
    except Exception:
        logger.exception("Bot action %s failed for %s", action, name)
        await _answer(query, "Operation failed. Check Hostify logs.", alert=True)


@Client.on_callback_query(filters.regex(r"^hostify:delete:"))
async def delete_confirmation(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await _answer(query, "Only the owner can confirm deletion.", alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 4:
        await _answer(query, "Invalid confirmation.", alert=True)
        return
    action, token = parts[2], parts[3]
    pending = _PENDING_DELETE.pop(token, None)
    if not pending:
        await _answer(query, "Confirmation expired. Use /list again.", alert=True)
        return
    chat_id, owner_id, name, expiry = pending
    if (
        chat_id != query.message.chat.id
        or owner_id != user.id
        or time.monotonic() > expiry
    ):
        await _answer(query, "Confirmation expired or belongs to another user.", alert=True)
        return
    if action == "cancel":
        await _edit(query.message, f"Deletion of {name} cancelled.", quote=True)
        await query.answer()
        return
    ok, detail = await delete_bot(name)
    await _edit(
        query.message,
        f"{name} deleted successfully." if ok else f"Could not delete {name}: {detail or 'operation failed'}",
        quote=True,
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^hostify:admin:remove:"))
async def admin_remove_callback(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id):
        await _answer(query, "Only the owner can remove admins.", alert=True)
        return
    if query.message is None:
        await query.answer()
        return
    try:
        admin_id = int((query.data or "").rsplit(":", 1)[-1])
        if await remove_admin(admin_id):
            await _answer(query, f"Admin {admin_id} removed.")
        else:
            await _answer(query, "This user is not an admin.")
        await _show_remove_admins(client, query.message)
    except Exception:
        logger.exception("Could not remove admin")
        await _answer(query, "Could not remove this admin.", alert=True)


@Client.on_callback_query(filters.regex(r"^hostify:settings:"))
async def settings_navigation(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await _answer(query, "Only the owner can change settings.", alert=True)
        return
    action = (query.data or "").rsplit(":", 1)[-1]
    if action == "open":
        await _show_settings(query.message)
    elif action == "branding":
        await query.message.edit_text(
            _quote("Select a branding field to edit."),
            parse_mode=ParseMode.HTML,
            reply_markup=_branding_keyboard(),
        )
    elif action == "admins":
        await _show_admins(client, query.message.chat.id, edit_message=query.message)
    elif action == "github":
        await query.message.edit_text(
            _quote("Open /github to inspect the GitHub App connection and accessible repositories."),
            parse_mode=ParseMode.HTML,
            reply_markup=_markup([[_button("Open GitHub", callback="hostify:settings:github_open")]]),
        )
    elif action == "github_open":
        await _edit(query.message, "Send /github in this chat to view GitHub integration status.")
    await query.answer()


@Client.on_callback_query(filters.regex(r"^hostify:brand:edit:"))
async def brand_edit_callback(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await _answer(query, "Only the owner can edit branding.", alert=True)
        return
    field = (query.data or "").rsplit(":", 1)[-1]
    _PENDING_INPUT[user.id] = {
        "action": "edit_brand", "chat_id": query.message.chat.id, "field": field
    }
    await _edit(
        query.message,
        f"Send the new value for {field}. Use /cancel to abort.",
        quote=True,
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^hostify:msg:"))
async def message_template_callback(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await _answer(query, "Only the owner can edit messages.", alert=True)
        return
    parts = (query.data or "").split(":")
    action = parts[2] if len(parts) > 2 else ""
    key = parts[3] if len(parts) > 3 else ""
    if action == "list":
        await _show_template_list(query.message, edit=True)
    elif action == "open" and key in M.DEFAULT_MESSAGES:
        await _edit_template_screen(query.message, key)
    elif action == "edit" and key in M.DEFAULT_MESSAGES:
        _PENDING_INPUT[user.id] = {
            "action": "edit_template", "chat_id": query.message.chat.id, "key": key
        }
        await _edit(
            query.message,
            f"Send the new text for {_template_label(key)}.\nMessage ID: {key}\nKeep dynamic placeholders such as {{name}} and {{pid}} when required. Use /cancel to abort.",
            quote=True,
        )
    elif action == "reset" and key in M.DEFAULT_MESSAGES:
        await reset_custom_message(key)
        M.reset_default(key)
        await _edit_template_screen(query.message, key)
        await _answer(query, "Message restored to its default.")
        return
    await query.answer()


@Client.on_message(filters.private, group=10)
async def unknown_owner_command(client: Client, message):
    """Unknown slash commands get a clear response; ordinary text is ignored."""
    if not message.from_user or not is_owner(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lstrip("/").casefold() if parts and parts[0].startswith("/") else ""
    known = {
        "start", "command", "commands", "admins", "deploy", "cancel", "list",
        "stop", "restart", "delete", "logs", "status", "ping", "settings",
        "setbrand", "messageonly", "setmsg", "resetmsg", "addadmin",
        "removeadmin", "github", "mylogs",
    }
    if command and command not in known:
        await _reply(message, "Unknown command. Use /commands to view owner commands.", quote=True)
