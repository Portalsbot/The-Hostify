from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
from pathlib import Path

import psutil

from config import BASE_DIR
from database import delete_bot as db_delete
from database import get_bot, update_status
from deployer import launch_existing
from render_deployer import render_service_action

_PROCESS_TIME_TOLERANCE = 1.5
_STOP_TIMEOUT_SECONDS = 8
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")


def _bot_directory(name: str) -> Path:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError("Invalid bot name.")
    base = Path(BASE_DIR).expanduser().resolve()
    candidate = base / name
    if candidate.is_symlink():
        raise ValueError("Refusing to operate on a symbolic-link bot directory.")
    resolved = candidate.resolve()
    if resolved.parent != base:
        raise ValueError("Bot directory escapes BASE_DIR.")
    return resolved


def _verify_process_identity(bot: dict, name: str) -> psutil.Process | None:
    """Return a process only when PID, creation time, cwd and interpreter match."""
    pid = bot.get("pid")
    expected_created_at = bot.get("pid_created_at")

    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        process = psutil.Process(pid)
        actual_created_at = process.create_time()
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied as exc:
        raise RuntimeError("Cannot verify process ownership due to OS permission restrictions.") from exc

    if (
        isinstance(expected_created_at, (int, float))
        and abs(actual_created_at - float(expected_created_at)) > _PROCESS_TIME_TOLERANCE
    ):
        raise RuntimeError("PID has been reused by another process; refusing to terminate it.")

    bot_dir = _bot_directory(name)
    if not bot_dir.is_dir():
        raise RuntimeError("Bot directory is missing; refusing to signal an unverified process.")

    try:
        actual_cwd = Path(process.cwd()).resolve()
        command = process.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        raise RuntimeError("Cannot verify the bot process command and working directory.") from exc

    expected_python = os.path.abspath(str(bot_dir / "venv" / "bin" / "python"))
    if actual_cwd != bot_dir or not command or os.path.abspath(command[0]) != expected_python:
        raise RuntimeError("PID does not match this bot's working directory and interpreter.")

    return process


async def _terminate_bot_process(bot: dict, name: str) -> tuple[bool, str | None]:
    """Stop only a process whose persisted identity matches this bot."""
    try:
        process = _verify_process_identity(bot, name)
        if process is None:
            return True, None

        pid = process.pid
        if os.name == "posix":
            try:
                if os.getpgid(pid) != pid:
                    return False, "Bot process is not the expected isolated process-group leader."
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                return True, None
        else:
            process.terminate()

        try:
            await asyncio.to_thread(process.wait, _STOP_TIMEOUT_SECONDS)
        except psutil.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            try:
                await asyncio.to_thread(process.wait, _STOP_TIMEOUT_SECONDS)
            except psutil.TimeoutExpired:
                return False, "Process did not exit after SIGKILL."

        # The bot may have spawned children. The bot is launched in a new
        # session, so any surviving members remain in the same process group.
        if os.name == "posix":
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                pass
            else:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        return True, None

    except (OSError, RuntimeError, ValueError) as exc:
        return False, str(exc)


async def stop_bot(name: str) -> tuple[bool, str | None]:
    bot = await get_bot(name)
    if not bot:
        return False, "not_found"
    if bot.get("deployment_provider") == "render":
        return await render_service_action(name, "stop")

    ok, detail = await _terminate_bot_process(bot, name)
    if not ok:
        return False, detail

    await update_status(name, "stopped", None, None)
    return True, None


async def start_bot(name: str) -> tuple[bool, str | None]:
    bot = await get_bot(name)
    if not bot:
        return False, "not_found"
    if bot.get("deployment_provider") == "render":
        return await render_service_action(name, "run")
    return await launch_existing(name, bot.get("env_vars") or {})


async def restart_bot(name: str) -> tuple[bool, str | None]:
    bot = await get_bot(name)
    if not bot:
        return False, "not_found"
    if bot.get("deployment_provider") == "render":
        return await render_service_action(name, "restart")

    ok, detail = await _terminate_bot_process(bot, name)
    if not ok:
        return False, detail

    await update_status(name, "stopped", None, None)
    await asyncio.sleep(0.5)
    return await launch_existing(name, bot.get("env_vars") or {})


async def delete_bot(name: str) -> tuple[bool, str | None]:
    bot = await get_bot(name)
    if not bot:
        return False, "not_found"
    if bot.get("deployment_provider") == "render":
        return await render_service_action(name, "delete")

    ok, detail = await _terminate_bot_process(bot, name)
    if not ok:
        return False, detail

    try:
        bot_dir = _bot_directory(name)
        if bot_dir.exists():
            shutil.rmtree(bot_dir)
        await db_delete(name)
        return True, None
    except (OSError, RuntimeError, ValueError) as exc:
        # Keep the database record if filesystem deletion failed, so the
        # operator can inspect and retry instead of losing track of the folder.
        await update_status(name, "stopped", None, None)
        return False, str(exc)


def read_logs(name: str, lines: int = 20) -> list[str] | None:
    if not isinstance(lines, int) or not 1 <= lines <= 200:
        raise ValueError("Log line count must be between 1 and 200.")

    bot_dir = _bot_directory(name)
    log_path = bot_dir / "bot.log"
    if log_path.is_symlink() or not log_path.is_file():
        return None

    with log_path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 64 * 1024))
        data = stream.read(64 * 1024).decode("utf-8", errors="replace")

    return data.splitlines()[-lines:]
