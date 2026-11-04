from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from pathlib import Path

import psutil
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import messages as M
from config import BASE_DIR
from database import get_bot, list_bots
from manager import delete_bot, restart_bot, stop_bot
from .guard import is_owner, owner_only


logger = logging.getLogger("hostify.plugins.owner")

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_DELETE_PATTERN = re.compile(r"^hostify:delete:(confirm|cancel):([A-Za-z0-9_-]{12})$")
_DELETE_CONFIRM_TTL = 60
_TELEGRAM_SAFE_LIMIT = 3800
_LOG_TAIL_BYTES = 64 * 1024

# token -> (chat_id, user_id, bot_name, expires_at, confirmation_message_id)
_PENDING_DELETES: dict[str, tuple[int, int, str, float, int]] = {}

# Keep administrative information and log output out of group chats.
_PRIVATE_OWNER = filters.private & owner_only


async def _reply(message, text: str, **kwargs):
    """Reply as plain text so names and log lines cannot become markup."""
    return await message.reply_text(
        text,
        parse_mode=ParseMode.DISABLED,
        **kwargs,
    )


async def _reply_chunks(message, text: str) -> None:
    """Send long output in bounded Telegram messages."""
    chunks: list[str] = []
    current = ""

    for source_line in (text.splitlines() or [text]):
        line = source_line

        while len(line) > _TELEGRAM_SAFE_LIMIT:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:_TELEGRAM_SAFE_LIMIT])
            line = line[_TELEGRAM_SAFE_LIMIT:]

        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > _TELEGRAM_SAFE_LIMIT:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)

    for chunk in chunks:
        await _reply(message, chunk)


def _requested_name(message) -> str | None:
    """Require exactly one syntactically safe bot name."""
    parts = (message.text or "").split()
    if len(parts) != 2:
        return None

    name = parts[1]
    return name if _NAME_PATTERN.fullmatch(name) else None


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, total = divmod(total, 86_400)
    hours, total = divmod(total, 3_600)
    minutes, secs = divmod(total, 60)

    result = []
    if days:
        result.append(f"{days}d")
    if hours or days:
        result.append(f"{hours}h")
    if minutes or hours or days:
        result.append(f"{minutes}m")
    result.append(f"{secs}s")
    return " ".join(result)


def _purge_expired_confirmations() -> None:
    now = time.monotonic()
    expired = [
        token
        for token, item in _PENDING_DELETES.items()
        if item[3] <= now
    ]
    for token in expired:
        _PENDING_DELETES.pop(token, None)


def _read_log_tail(name: str, max_lines: int = 20) -> list[str] | None:
    """Read at most the final 64 KiB; reject symlinks and path escape."""
    base_dir = Path(BASE_DIR).expanduser().resolve()
    candidate = base_dir / name
    if candidate.is_symlink():
        raise ValueError("Bot directory must not be a symbolic link.")
    bot_dir = candidate.resolve(strict=True)

    if bot_dir.parent != base_dir:
        raise ValueError("Bot path is outside the configured base directory.")

    log_path = bot_dir / "bot.log"
    if log_path.is_symlink() or not log_path.is_file():
        return None

    with log_path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        offset = max(0, size - _LOG_TAIL_BYTES)
        stream.seek(offset)
        raw = stream.read(_LOG_TAIL_BYTES)

    decoded = raw.decode("utf-8", errors="replace")
    if offset:
        first_newline = decoded.find("\n")
        decoded = decoded[first_newline + 1:] if first_newline >= 0 else decoded

    return decoded.splitlines()[-max_lines:]


@Client.on_message(filters.command("start"))
async def start_handler(client: Client, message):
    """Public /start; reveal the owner panel only to the configured owner."""
    try:
        user_id = message.from_user.id if message.from_user else None
        response = (
            M.M03_OWNER_WELCOME.replace(
                "Use the buttons below to explore.",
                "Send /help to view the available commands.",
            )
            if is_owner(user_id)
            else M.M02_NON_OWNER_ALERT
        )
        await _reply(message, response)
    except Exception:
        logger.exception("The /start handler failed")
        await _reply(message, "Hostify could not process this request.")


@Client.on_message(filters.command("help") & _PRIVATE_OWNER)
async def help_handler(client: Client, message):
    try:
        await _reply(message, M.M04_HELP)
    except Exception:
        logger.exception("The /help handler failed")
        await _reply(message, "Unable to display the command list right now.")


@Client.on_message(filters.command("list") & _PRIVATE_OWNER)
async def list_handler(client: Client, message):
    try:
        bots = await list_bots()
        if not bots:
            await _reply(message, M.M15_LIST_EMPTY)
            return

        bots.sort(key=lambda item: str(item.get("name", "")).casefold())
        lines = [M.M16_LIST_HEADER.format(count=len(bots))]

        for bot in bots:
            name = str(bot.get("name") or "(unnamed)")
            status = str(bot.get("status") or "unknown")
            pid = bot.get("pid")
            pid_text = str(pid) if isinstance(pid, int) and pid > 0 else "—"
            lines.append(M.M17_LIST_ROW.format(name=name, status=status, pid=pid_text))

        await _reply_chunks(message, "\n".join(lines))
    except Exception:
        logger.exception("The /list handler failed")
        await _reply(message, "Unable to load deployed bots. Check controller logs.")


@Client.on_message(filters.command("stop") & _PRIVATE_OWNER)
async def stop_handler(client: Client, message):
    name = _requested_name(message)
    if not name:
        await _reply(message, "Usage: /stop <bot_name>\nUse /list to see valid names.")
        return

    try:
        ok, detail = await stop_bot(name)
        if not ok:
            response = M.M25_NOT_FOUND.format(name=name) if detail == "not_found" else (
                f"Could not stop {name}: {detail or 'operation failed'}"
            )
            await _reply(message, response)
            return

        await _reply(message, M.M18_STOP_OK.format(name=name))
    except Exception:
        logger.exception("Stop operation failed for bot %s", name)
        await _reply(message, f"Stop failed for {name}. Check controller logs.")


@Client.on_message(filters.command("restart") & _PRIVATE_OWNER)
async def restart_handler(client: Client, message):
    name = _requested_name(message)
    if not name:
        await _reply(message, "Usage: /restart <bot_name>\nUse /list to see valid names.")
        return

    try:
        ok, detail = await restart_bot(name)
        if not ok:
            response = M.M25_NOT_FOUND.format(name=name) if detail == "not_found" else (
                f"Could not restart {name}: {detail or 'operation failed'}"
            )
            await _reply(message, response)
            return

        await _reply(message, M.M19_RESTART_OK.format(name=name, pid=detail))
    except Exception:
        logger.exception("Restart operation failed for bot %s", name)
        await _reply(message, f"Restart failed for {name}. Check controller logs.")


@Client.on_message(filters.command("delete") & _PRIVATE_OWNER)
async def delete_handler(client: Client, message):
    name = _requested_name(message)
    if not name:
        await _reply(message, "Usage: /delete <bot_name>\nDeletion requires a second confirmation.")
        return

    try:
        bot = await get_bot(name)
        if not bot:
            await _reply(message, M.M25_NOT_FOUND.format(name=name))
            return

        _purge_expired_confirmations()
        token = secrets.token_urlsafe(9)
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Confirm deletion", callback_data=f"hostify:delete:confirm:{token}"),
                InlineKeyboardButton("Cancel", callback_data=f"hostify:delete:cancel:{token}"),
            ]]
        )

        confirmation = await _reply(
            message,
            M.M20_DELETE_CONFIRM.format(name=name),
            reply_markup=keyboard,
        )

        _PENDING_DELETES[token] = (
            message.chat.id,
            message.from_user.id,
            name,
            time.monotonic() + _DELETE_CONFIRM_TTL,
            confirmation.id,
        )
    except Exception:
        logger.exception("Could not prepare delete confirmation for bot %s", name)
        await _reply(message, "Could not prepare deletion. No deletion was performed.")


@Client.on_callback_query(filters.regex(r"^hostify:delete:"))
async def delete_confirmation_handler(client: Client, query):
    """A confirmation is valid only for its owner, private chat, message and TTL."""
    user_id = query.from_user.id if query.from_user else None
    if not is_owner(user_id):
        await query.answer("Access denied.", show_alert=True)
        return

    match = _DELETE_PATTERN.fullmatch(query.data or "")
    if not match:
        await query.answer("Invalid confirmation.", show_alert=True)
        return

    action, token = match.groups()
    pending = _PENDING_DELETES.get(token)

    if pending is None:
        await query.answer("Confirmation expired. Run /delete again.", show_alert=True)
        return

    chat_id, expected_user_id, name, expires_at, message_id = pending
    callback_message = query.message

    if (
        callback_message is None
        or callback_message.chat.type != ChatType.PRIVATE
        or callback_message.chat.id != chat_id
        or callback_message.id != message_id
        or user_id != expected_user_id
    ):
        await query.answer("This confirmation does not belong to this user.", show_alert=True)
        return

    if time.monotonic() > expires_at:
        _PENDING_DELETES.pop(token, None)
        await query.answer("Confirmation expired. Run /delete again.", show_alert=True)
        return

    # Consume before awaiting external work to prevent duplicate-click races.
    _PENDING_DELETES.pop(token, None)

    if action == "cancel":
        await query.answer("Deletion cancelled.")
        await callback_message.edit_text(
            M.M21_DELETE_CANCELLED.format(name=name),
            parse_mode=ParseMode.DISABLED,
        )
        return

    await query.answer("Deletion request accepted.")
    try:
        ok, detail = await delete_bot(name)
        if ok:
            response = M.M22_DELETE_OK.format(name=name)
        elif detail == "not_found":
            response = M.M25_NOT_FOUND.format(name=name)
        else:
            response = f"Could not delete {name}: {detail or 'operation failed'}"

        await callback_message.edit_text(response, parse_mode=ParseMode.DISABLED)
    except Exception:
        logger.exception("Confirmed deletion failed for bot %s", name)
        await callback_message.edit_text(
            f"Deletion failed for {name}. Check controller logs.",
            parse_mode=ParseMode.DISABLED,
        )


@Client.on_message(filters.command("logs") & _PRIVATE_OWNER)
async def logs_handler(client: Client, message):
    name = _requested_name(message)
    if not name:
        await _reply(message, "Usage: /logs <bot_name>\nUse /list to see valid names.")
        return

    try:
        bot = await get_bot(name)
        if not bot:
            await _reply(message, M.M25_NOT_FOUND.format(name=name))
            return

        lines = await asyncio.to_thread(_read_log_tail, name, 20)
        if not lines:
            await _reply(message, M.M24_LOGS_EMPTY.format(name=name))
            return

        output = M.M23_LOGS_HEADER.format(lines=len(lines), name=name)
        output += "\n" + "\n".join(lines)
        await _reply_chunks(message, output)
    except FileNotFoundError:
        await _reply(message, M.M24_LOGS_EMPTY.format(name=name))
    except Exception:
        logger.exception("Log retrieval failed for bot %s", name)
        await _reply(message, f"Could not read logs for {name}. Check controller logs.")


@Client.on_message(filters.command("status") & _PRIVATE_OWNER)
async def status_handler(client: Client, message):
    try:
        cpu = await asyncio.to_thread(psutil.cpu_percent, interval=0.2)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(BASE_DIR))
        bots = await list_bots()

        marked_running = sum(1 for bot in bots if bot.get("status") == "running")
        uptime = _format_duration(time.time() - psutil.boot_time())

        response = M.M28_STATUS.format(
            cpu=f"{cpu:.1f}",
            ram_used=f"{memory.used / (1024 ** 2):.0f}",
            ram_total=f"{memory.total / (1024 ** 2):.0f}",
            disk_used=f"{disk.used / (1024 ** 3):.1f}",
            disk_total=f"{disk.total / (1024 ** 3):.1f}",
            uptime=uptime,
            running=marked_running,
            total=len(bots),
        ).replace("Bots running:", "Bots marked running:")

        await _reply(message, response)
    except Exception:
        logger.exception("The /status handler failed")
        await _reply(message, "Unable to collect server status. Check controller logs.")
