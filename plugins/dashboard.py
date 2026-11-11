from __future__ import annotations

import html
import logging
import re
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database import get_bot, list_bots, update_bot_env
from manager import delete_bot, read_logs, restart_bot, stop_bot
from smallcaps import sc
from .guard import is_owner, is_owner_or_admin

logger = logging.getLogger("hostify.plugins.dashboard")
_NAME = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_env_sessions: dict[int, tuple[int, str]] = {}


def _close():
    return InlineKeyboardButton("Close", callback_data="hostify:ui:close")


def _keyboard(rows):
    rows = [list(row) for row in rows if row]
    rows.append([_close()])
    return InlineKeyboardMarkup(rows)


async def _reply(message, text: str, markup=None):
    return await message.reply_text(
        sc(text),
        parse_mode=ParseMode.DISABLED,
        reply_markup=markup or _keyboard([]),
    )


def _can_view(user_id: int, bot: dict) -> bool:
    if is_owner(user_id):
        return True
    return bool(bot.get("deployed_by") == user_id)


async def _visible_bots(user_id: int):
    all_bots = await list_bots(limit=2000)
    if is_owner(user_id):
        return all_bots
    return [bot for bot in all_bots if bot.get("deployed_by") == user_id]


def _root_keyboard():
    return _keyboard([
        [
            InlineKeyboardButton("Running", callback_data="hostify:list:running:0"),
            InlineKeyboardButton("Stopped", callback_data="hostify:list:stopped:0"),
        ],
        [InlineKeyboardButton("All Bots", callback_data="hostify:list:all:0")],
    ])


def _list_text(category: str, bots: list[dict], page: int) -> str:
    title = {
        "running": "Here are all running deployments.",
        "stopped": "Here are all stopped deployments.",
        "all": "Here is the list of all bots!",
    }.get(category, "Here is the list of all bots!")
    start = page * 10
    end = min(start + 10, len(bots))
    return f"{title}\n\nShowing {start + 1 if bots else 0}-{end} of {len(bots)} bots."


def _list_keyboard(category: str, bots: list[dict], page: int):
    start = page * 10
    page_bots = bots[start:start + 10]
    rows = []
    row = []
    for bot in page_bots:
        name = str(bot.get("name") or "unnamed")
        status_mark = "🟢" if bot.get("status") == "running" else "⚪"
        row.append(InlineKeyboardButton(
            f"{status_mark} {name}"[:60],
            callback_data=f"hostify:bot:view:{name}:{category}:{page}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    pages = (len(bots) + 9) // 10
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("Previous", callback_data=f"hostify:list:{category}:{page-1}"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton("Next", callback_data=f"hostify:list:{category}:{page+1}"))
        if nav:
            rows.append(nav)
    rows.append([InlineKeyboardButton("Back", callback_data="hostify:list:root"), _close()])
    return InlineKeyboardMarkup(rows)


def _detail_text(bot: dict) -> str:
    name = str(bot.get("name") or "Unknown")
    env = bot.get("env_vars") or {}
    env_keys = sorted(key for key in env if key != "NOTIFY_CHANNEL")
    created = bot.get("created_at")
    created_text = created.strftime("%Y-%m-%d %H:%M UTC") if isinstance(created, datetime) else "Unknown"
    pid = bot.get("pid") or "—"
    return (
        f"BOT SETTINGS: {name}\n\n"
        f"Status: {bot.get('status', 'unknown')}\n"
        f"Bot username: @{bot.get('bot_username')}" if bot.get("bot_username") else
        f"BOT SETTINGS: {name}\n\nStatus: {bot.get('status', 'unknown')}\nBot username: Not detected"
    ) + (
        f"\nPID: {pid}\nRepository: {bot.get('repo_url', 'Unknown')}\n"
        f"Deployed by ID: {bot.get('deployed_by', 'Legacy deployment')}\n"
        f"Created: {created_text}\nEnvironment variables: {len(env_keys)}"
    )


def _detail_keyboard(name: str, owner: bool, status: str, back_category: str = "all", back_page: int = 0):
    rows = [[
        InlineKeyboardButton("Edit Environment", callback_data=f"hostify:bot:env:{name}:{back_category}:{back_page}"),
        InlineKeyboardButton("Environment / API", callback_data=f"hostify:bot:envview:{name}:{back_category}:{back_page}"),
    ], [
        InlineKeyboardButton("Logs", callback_data=f"hostify:bot:logs:{name}:{back_category}:{back_page}"),
    ]]
    if owner:
        action = "stop" if status == "running" else "run"
        rows[1].append(InlineKeyboardButton("Stop" if action == "stop" else "Run",
                                           callback_data=f"hostify:bot:{action}:{name}:{back_category}:{back_page}"))
        rows.append([InlineKeyboardButton("Delete", callback_data=f"hostify:bot:delete:{name}:{back_category}:{back_page}")])
    rows.append([InlineKeyboardButton("Back", callback_data=f"hostify:list:{back_category}:{back_page}"), _close()])
    return InlineKeyboardMarkup(rows)


async def _render_list(query, user_id: int, category: str, page: int):
    bots = await _visible_bots(user_id)
    if category in {"running", "stopped"}:
        bots = [b for b in bots if (b.get("status") == "running") == (category == "running")]
    bots.sort(key=lambda item: str(item.get("name", "")).casefold())
    page = max(0, page)
    max_page = max(0, (len(bots) - 1) // 10)
    page = min(page, max_page)
    await query.message.edit_text(
        sc(_list_text(category, bots, page)),
        parse_mode=ParseMode.DISABLED,
        reply_markup=_list_keyboard(category, bots, page),
        disable_web_page_preview=True,
    )


@Client.on_message(filters.command("list") & filters.private)
async def list_command(client: Client, message):
    user = message.from_user
    if not user or not await is_owner_or_admin(user.id):
        await _reply(message, "This command is available to the owner and admins only.")
        return
    await _reply(message, "Here is the list of all bots!", _root_keyboard())


@Client.on_callback_query(filters.regex(r"^hostify:list:"))
async def list_callback(client: Client, query):
    user = query.from_user
    if not user or query.message is None or not await is_owner_or_admin(user.id):
        await query.answer("This menu is not available to you.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if parts[2] == "root":
        await query.message.edit_text(
            sc("Here is the list of all bots!"),
            parse_mode=ParseMode.DISABLED,
            reply_markup=_root_keyboard(),
        )
        await query.answer()
        return
    category = parts[2]
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    if category not in {"running", "stopped", "all"}:
        await query.answer("Unknown list filter.", show_alert=True)
        return
    try:
        await _render_list(query, user.id, category, page)
        await query.answer()
    except Exception:
        logger.exception("Could not render deployment list")
        await query.answer("Could not load deployments.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^hostify:bot:"))
async def bot_callback(client: Client, query):
    user = query.from_user
    if not user or query.message is None or not await is_owner_or_admin(user.id):
        await query.answer("This action is not available to you.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) < 4:
        await query.answer("Invalid control.", show_alert=True)
        return
    action, name = parts[2], parts[3]
    back_category = parts[4] if len(parts) > 4 and parts[4] in {"running", "stopped", "all"} else "all"
    back_page = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
    if not _NAME.fullmatch(name):
        await query.answer("Invalid bot name.", show_alert=True)
        return
    bot = await get_bot(name)
    if not bot or not _can_view(user.id, bot):
        await query.answer("This deployment is not available to your account.", show_alert=True)
        return
    owner = is_owner(user.id)

    if action == "view":
        await query.message.edit_text(
            sc(_detail_text(bot)),
            parse_mode=ParseMode.DISABLED,
            reply_markup=_detail_keyboard(name, owner, str(bot.get("status", "stopped")), back_category, back_page),
            disable_web_page_preview=True,
        )
        await query.answer()
        return

    if action == "envview":
        env = bot.get("env_vars") or {}
        keys = sorted(k for k in env if k != "NOTIFY_CHANNEL")
        lines = [f"ENVIRONMENT / API DETAILS: {name}", "", "Secret values are hidden in Telegram."]
        if keys:
            lines.extend(f"{key}=••••••••" for key in keys)
        else:
            lines.append("No environment variables are configured.")
        await query.message.edit_text(
            sc("\n".join(lines)),
            parse_mode=ParseMode.DISABLED,
            reply_markup=_keyboard([
                [InlineKeyboardButton("Edit Environment", callback_data=f"hostify:bot:env:{name}")],
                [InlineKeyboardButton("Back", callback_data=f"hostify:bot:view:{name}:{back_category}:{back_page}")],
            ]),
        )
        await query.answer()
        return

    if action == "env":
        _env_sessions[user.id] = (query.message.chat.id, name)
        await query.message.edit_text(
            sc(f"Send environment variables for {name} as KEY=VALUE, one per line. Use KEY=DELETE to remove a variable. Secret values are encrypted in MongoDB. Send /cancel to abort."),
            parse_mode=ParseMode.DISABLED,
            reply_markup=_keyboard([[InlineKeyboardButton("Back", callback_data=f"hostify:bot:view:{name}")]]),
        )
        await query.answer()
        return

    if action == "logs":
        try:
            lines = await read_logs(name, 40)
            output = f"LAST {len(lines or [])} LOG LINES: {name}\n\n" + ("\n".join(lines) if lines else "No log output is available.")
            await query.message.edit_text(sc(output[-3800:]), parse_mode=ParseMode.DISABLED,
                reply_markup=_keyboard([[InlineKeyboardButton("Back", callback_data=f"hostify:bot:view:{name}")]]))
            await query.answer()
        except Exception:
            logger.exception("Could not read logs for %s", name)
            await query.answer("Could not read these logs.", show_alert=True)
        return

    if not owner:
        await query.answer("Only the owner can stop, run, restart or delete deployments.", show_alert=True)
        return

    if action == "stop":
        ok, detail = await stop_bot(name)
        result = "Deployment stopped." if ok else f"Could not stop deployment: {detail}"
    elif action == "run":
        ok, detail = await restart_bot(name)
        result = "Deployment started." if ok else f"Could not start deployment: {detail}"
    elif action == "delete":
        ok, detail = await delete_bot(name)
        result = "Deployment deleted." if ok else f"Could not delete deployment: {detail}"
    else:
        await query.answer("Unknown bot action.", show_alert=True)
        return

    if action == "delete" and ok:
        await query.message.edit_text(sc(result), parse_mode=ParseMode.DISABLED, reply_markup=_keyboard([[InlineKeyboardButton("Back", callback_data=f"hostify:list:{back_category}:{back_page}")]]))
    else:
        updated = await get_bot(name)
        if updated:
            await query.message.edit_text(sc(_detail_text(updated)), parse_mode=ParseMode.DISABLED,
                reply_markup=_detail_keyboard(name, owner, str(updated.get("status", "stopped")), back_category, back_page))
        else:
            await query.message.edit_text(sc(result), parse_mode=ParseMode.DISABLED, reply_markup=_keyboard([]))
    await query.answer(result[:180], show_alert=not ok)


def cancel_environment_edit(user_id: int, chat_id: int) -> bool:
    """Clear the user's pending environment edit flow."""
    pending = _env_sessions.get(user_id)
    if pending is None or pending[0] != chat_id:
        return False
    _env_sessions.pop(user_id, None)
    return True


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=6)
async def environment_input(client: Client, message):
    user = message.from_user
    if not user or user.id not in _env_sessions:
        return
    chat_id, name = _env_sessions[user.id]
    if message.chat.id != chat_id:
        return
    if not await is_owner_or_admin(user.id):
        _env_sessions.pop(user.id, None)
        return
    if message.text.strip().casefold() == "cancel":
        _env_sessions.pop(user.id, None)
        await _reply(message, "Environment editing cancelled.")
        return

    bot = await get_bot(name)
    if not bot or not _can_view(user.id, bot):
        _env_sessions.pop(user.id, None)
        await _reply(message, "This deployment is no longer available.")
        return
    env = dict(bot.get("env_vars") or {})
    changed = []
    try:
        for raw in message.text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if "=" not in line:
                raise ValueError("Use KEY=VALUE or KEY=DELETE.")
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", key):
                raise ValueError(f"Invalid environment key: {key}")
            if key in {"PATH", "HOME", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "BOT_TOKEN_HOSTIFY"}:
                raise ValueError(f"{key} is reserved.")
            if value.upper() == "DELETE":
                env.pop(key, None)
            else:
                if len(value) > 4096 or "\x00" in value:
                    raise ValueError(f"Invalid value for {key}.")
                env[key] = value
            changed.append(key)
        if not changed:
            raise ValueError("No environment variables were provided.")
        if len(env) > 40:
            raise ValueError("A deployment may have at most 40 environment variables.")
        await update_bot_env(name, env)
        _env_sessions.pop(user.id, None)
        await _reply(message, f"Environment updated for {name}. Changed keys: {', '.join(changed)}. A restart is required before the running process receives the changes.")
    except ValueError as exc:
        await _reply(message, f"{exc} Send the values again, or /cancel to abort.")
    except Exception:
        logger.exception("Environment update failed for %s", name)
        _env_sessions.pop(user.id, None)
        await _reply(message, "Could not save environment changes. Check controller logs.")
