
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from pathlib import Path

import psutil
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import messages as M
from config import BASE_DIR
from database import get_bot, list_bots
from manager import delete_bot, restart_bot, stop_bot
from .guard import is_owner, owner_only


logger = logging.getLogger("hostify.plugins.owner")

_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_DELETE_CALLBACK_RE = re.compile(
    r"^hostify:delete:(confirm|cancel):([A-Za-z0-9_-]{12})$"
)

_DELETE_CONFIRM_TTL = 60
_TELEGRAM_TEXT_LIMIT = 3800
_LOG_TAIL_BYTES = 64 * 1024

# token -> (chat_id, owner_user_id, bot_name, expiry_time)
_PENDING_DELETES: dict[str, tuple[int, int, str, float]] = {}


async def _reply(message, text: str, **kwargs):
    """Send plain text without Markdown/HTML parsing."""
    return await message.reply_text(
        text,
        parse_mode=ParseMode.DISABLED,
        **kwargs,
    )


async def _reply_chunks(message, text: str) -> None:
    """Split long output into Telegram-safe messages."""
    chunks: list[str] = []
    current = ""

    for line in text.splitlines() or [text]:
        while len(line) > _TELEGRAM_TEXT_LIMIT:
            if current:
                chunks.append(current)
                current = ""

            chunks.append(line[:_TELEGRAM_TEXT_LIMIT])
            line = line[_TELEGRAM_TEXT_LIMIT:]

        candidate = f"{current}\n{line}" if current else line

        if len(candidate) > _TELEGRAM_TEXT_LIMIT:
            if current:
                chunks.append(current)

            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)

    for chunk in chunks:
        await _reply(message, chunk)


def _command_name(message) -> str | None:
    """Accept exactly one safe bot identifier."""
    parts = (message.text or "").split(maxsplit=1)

    if len(parts) != 2:
        return None

    name = parts[1].strip()

    return name if _NAME_RE.fullmatch(name) else None


def _format_duration(seconds: float) -> str:
    remaining = max(0, int(seconds))

    days, remaining = divmod(remaining, 86_400)
    hours, remaining = divmod(remaining, 3_600)
    minutes, secs = divmod(remaining, 60)

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours or days:
        parts.append(f"{hours}h")

    if minutes or hours or days:
        parts.append(f"{minutes}m")

    parts.append(f"{secs}s")

    return " ".join(parts)


def _read_log_tail(name: str, max_lines: int = 20) -> list[str] | None:
    """Read a bounded log tail instead of loading the entire file."""
    base_dir = Path(BASE_DIR).resolve()
    bot_dir = (base_dir / name).resolve()

    # Prevent reading outside the configured bots directory.
    if base_dir not in bot_dir.parents:
        raise ValueError("Invalid bot directory.")

    log_path = bot_dir / "bot.log"

    if log_path.is_symlink() or not log_path.is_file():
        return None

    with log_path.open("rb") as log_file:
        log_file.seek(0, 2)

        file_size = log_file.tell()
        start = max(0, file_size - _LOG_TAIL_BYTES)

        log_file.seek(start)
        raw = log_file.read(_LOG_TAIL_BYTES)

    decoded = raw.decode("utf-8", errors="replace")

    if start:
        separator = decoded.find("\n")
        decoded = (
            decoded[separator + 1:]
            if separator >= 0
            else decoded[-4_000:]
        )

    return decoded.splitlines()[-max_lines:]


def _purge_expired_delete_tokens() -> None:
    """Remove expired delete confirmations from memory."""
    now = time.monotonic()

    expired = [
        token
        for token, item in _PENDING_DELETES.items()
        if item[3] <= now
    ]

    for token in expired:
        _PENDING_DELETES.pop(token, None)


# --------------------------------------------------
# START
# --------------------------------------------------

@Client.on_message(filters.command("start"))
async def start_handler(client: Client, message):
    """Keep /start public while restricting the owner panel."""
    try:
        is_owner_user = is_owner(
            message.from_user.id if message.from_user else None
        )

        text = (
            M.M03_OWNER_WELCOME.replace(
                "Use the buttons below to explore.",
                "Send /help to view available commands.",
            )
            if is_owner_user
            else M.M02_NON_OWNER_ALERT
        )

        await _reply(message, text)

    except Exception:
        logger.exception("/start failed")
        await _reply(
            message,
            "Hostify could not process this request."
        )


# --------------------------------------------------
# HELP
# --------------------------------------------------

@Client.on_message(filters.command("help") & owner_only)
async def help_handler(client: Client, message):
    try:
        await _reply(message, M.M04_HELP)

    except Exception:
        logger.exception("/help failed")
        await _reply(
            message,
            "Unable to display the command list right now."
        )


# --------------------------------------------------
# LIST
# --------------------------------------------------

@Client.on_message(filters.command("list") & owner_only)
async def list_handler(client: Client, message):
    try:
        bots = await list_bots()

        if not bots:
            await _reply(message, M.M15_LIST_EMPTY)
            return

        bots.sort(
            key=lambda item: str(
                item.get("name", "")
            ).casefold()
        )

        lines = [
            M.M16_LIST_HEADER.format(count=len(bots))
        ]

        for bot in bots:
            name = str(bot.get("name") or "(unnamed)")
            status = str(bot.get("status") or "unknown")
            pid = bot.get("pid")

            pid_text = (
                str(pid)
                if isinstance(pid, int) and pid > 0
                else "—"
            )

            lines.append(
                M.M17_LIST_ROW.format(
                    name=name,
                    status=status,
                    pid=pid_text,
                )
            )

        await _reply_chunks(message, "\n".join(lines))

    except Exception:
        logger.exception("/list failed")
        await _reply(
            message,
            "Unable to load the deployed-bot list. Check controller logs."
        )


# --------------------------------------------------
# STOP
# --------------------------------------------------

@Client.on_message(filters.command("stop") & owner_only)
async def stop_handler(client: Client, message):
    name = _command_name(message)

    if not name:
        await _reply(
            message,
            "Usage: /stop <bot_name>\nUse /list to see valid names."
        )
        return

    try:
        ok, detail = await stop_bot(name)

        if not ok:
            if detail == "not_found":
                text = M.M25_NOT_FOUND.format(name=name)
            else:
                text = (
                    f"Could not stop {name}: "
                    f"{detail or 'operation failed'}"
                )

            await _reply(message, text)
            return

        await _reply(
            message,
            M.M18_STOP_OK.format(name=name)
        )

    except Exception:
        logger.exception(
            "Stop operation failed for %s",
            name
        )

        await _reply(
            message,
            f"Stop failed for {name}. Check controller logs."
        )


# --------------------------------------------------
# RESTART
# --------------------------------------------------

@Client.on_message(filters.command("restart") & owner_only)
async def restart_handler(client: Client, message):
    name = _command_name(message)

    if not name:
        await _reply(
            message,
            "Usage: /restart <bot_name>\nUse /list to see valid names."
        )
        return

    try:
        ok, detail = await restart_bot(name)

        if not ok:
            if detail == "not_found":
                text = M.M25_NOT_FOUND.format(name=name)
            else:
                text = (
                    f"Could not restart {name}: "
                    f"{detail or 'operation failed'}"
                )

            await _reply(message, text)
            return

        await _reply(
            message,
            M.M19_RESTART_OK.format(
                name=name,
                pid=detail
            )
        )

    except Exception:
        logger.exception(
            "Restart operation failed for %s",
            name
        )

        await _reply(
            message,
            f"Restart failed for {name}. Check controller logs."
        )


# --------------------------------------------------
# DELETE - REQUEST CONFIRMATION
# --------------------------------------------------

@Client.on_message(filters.command("delete") & owner_only)
async def delete_handler(client: Client, message):
    name = _command_name(message)

    if not name:
        await _reply(
            message,
            "Usage: /delete <bot_name>\n"
            "Deletion requires a second confirmation."
        )
        return

    try:
        bot = await get_bot(name)

        if not bot:
            await _reply(
                message,
                M.M25_NOT_FOUND.format(name=name)
            )
            return

        _purge_expired_delete_tokens()

        token = secrets.token_urlsafe(9)
        owner_id = message.from_user.id

        _PENDING_DELETES[token] = (
            message.chat.id,
            owner_id,
            name,
            time.monotonic() + _DELETE_CONFIRM_TTL,
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Confirm deletion",
                        callback_data=(
                            f"hostify:delete:confirm:{token}"
                        ),
                    ),
                    InlineKeyboardButton(
                        "Cancel",
                        callback_data=(
                            f"hostify:delete:cancel:{token}"
                        ),
                    ),
                ]
            ]
        )

        await _reply(
            message,
            M.M20_DELETE_CONFIRM.format(name=name),
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception(
            "Could not prepare deletion confirmation for %s",
            name
        )

        await _reply(
            message,
            "Could not prepare deletion. No files were intentionally removed."
        )


# --------------------------------------------------
# DELETE - CONFIRMATION CALLBACK
# --------------------------------------------------

@Client.on_callback_query(
    filters.regex(_DELETE_CALLBACK_RE)
)
async def delete_confirmation_handler(client: Client, query):
    """Bind deletion to the owner, chat and expiring token."""
    user_id = (
        query.from_user.id
        if query.from_user
        else None
    )

    if not is_owner(user_id):
        await query.answer(
            "Access denied.",
            show_alert=True
        )
        return

    match = _DELETE_CALLBACK_RE.fullmatch(
        query.data or ""
    )

    if not match:
        await query.answer(
            "Invalid confirmation.",
            show_alert=True
        )
        return

    action, token = match.groups()

    pending = _PENDING_DELETES.get(token)

    if not pending:
        await query.answer(
            "This confirmation has expired. Run /delete again.",
            show_alert=True
        )
        return

    chat_id, expected_owner_id, name, expires_at = pending

    if (
        query.message is None
        or query.message.chat.id != chat_id
        or user_id != expected_owner_id
    ):
        await query.answer(
            "This confirmation does not belong to this user or chat.",
            show_alert=True
        )
        return

    if time.monotonic() > expires_at:
        _PENDING_DELETES.pop(token, None)

        await query.answer(
            "Confirmation expired. Run /delete again.",
            show_alert=True
        )
        return

    _PENDING_DELETES.pop(token, None)

    if action == "cancel":
        await query.answer("Deletion cancelled.")

        await query.message.edit_text(
            M.M21_DELETE_CANCELLED.format(name=name),
            parse_mode=ParseMode.DISABLED,
        )
        return

    await query.answer("Deletion request accepted.")

    try:
        ok, detail = await delete_bot(name)

        if ok:
            result = M.M22_DELETE_OK.format(name=name)

        elif detail == "not_found":
            result = M.M25_NOT_FOUND.format(name=name)

        else:
            result = (
                f"Could not delete {name}: "
                f"{detail or 'operation failed'}"
            )

        await query.message.edit_text(
            result,
            parse_mode=ParseMode.DISABLED,
        )

    except Exception:
        logger.exception(
            "Confirmed deletion failed for %s",
            name
        )

        await query.message.edit_text(
            f"Deletion failed for {name}. Check controller logs.",
            parse_mode=ParseMode.DISABLED,
        )


# --------------------------------------------------
# LOGS
# --------------------------------------------------

@Client.on_message(filters.command("logs") & owner_only)
async def logs_handler(client: Client, message):
    name = _command_name(message)

    if not name:
        await _reply(
            message,
            "Usage: /logs <bot_name>\nUse /list to see valid names."
        )
        return

    try:
        bot = await get_bot(name)

        if not bot:
            await _reply(
                message,
                M.M25_NOT_FOUND.format(name=name)
            )
            return

        lines = await asyncio.to_thread(
            _read_log_tail,
            name,
            20
        )

        if not lines:
            await _reply(
                message,
                M.M24_LOGS_EMPTY.format(name=name)
            )
            return

        output = M.M23_LOGS_HEADER.format(
            lines=len(lines),
            name=name,
        )

        output += "\n" + "\n".join(lines)

        if len(output) > _TELEGRAM_TEXT_LIMIT:
            output = output[-_TELEGRAM_TEXT_LIMIT:]

        await _reply(message, output)

    except Exception:
        logger.exception(
            "Log retrieval failed for %s",
            name
        )

        await _reply(
            message,
            f"Could not read logs for {name}. Check controller logs."
        )


# --------------------------------------------------
# SERVER STATUS
# --------------------------------------------------

@Client.on_message(filters.command("status") & owner_only)
async def status_handler(client: Client, message):
    try:
        # CPU sampling is blocking, so execute it outside the event loop.
        cpu_percent = await asyncio.to_thread(
            psutil.cpu_percent,
            interval=0.25
        )

        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(BASE_DIR))

        bots = await list_bots()

        running = sum(
            1
            for bot in bots
            if bot.get("status") == "running"
        )

        uptime = _format_duration(
            time.time() - psutil.boot_time()
        )

        text = M.M28_STATUS.format(
            cpu=f"{cpu_percent:.1f}",
            ram_used=f"{memory.used / (1024 ** 2):.0f}",
            ram_total=f"{memory.total / (1024 ** 2):.0f}",
            disk_used=f"{disk.used / (1024 ** 3):.1f}",
            disk_total=f"{disk.total / (1024 ** 3):.1f}",
            uptime=uptime,
            running=running,
            total=len(bots),
        ).replace(
            "Bots running:",
            "Bots marked running:"
        )

        await _reply(message, text)

    except Exception:
        logger.exception("/status failed")

        await _reply(
            message,
            "Unable to collect server status. Check controller logs."
        )
