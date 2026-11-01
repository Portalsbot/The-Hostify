import os
import signal
import asyncio
import shutil
from config import BASE_DIR
from database import get_bot, update_status, delete_bot as db_delete
from deployer import launch_existing


def _kill(pid):
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass


async def stop_bot(name):
    bot = await get_bot(name)
    if not bot:
        return False, "not_found"
    _kill(bot.get("pid"))
    await update_status(name, "stopped", None)
    return True, None


async def restart_bot(name):
    bot = await get_bot(name)
    if not bot:
        return False, "not_found"
    _kill(bot.get("pid"))
    await asyncio.sleep(1)
    ok, detail = await launch_existing(name, bot.get("env_vars") or {})
    return ok, detail


async def delete_bot(name):
    bot = await get_bot(name)
    if not bot:
        return False, "not_found"
    _kill(bot.get("pid"))
    await asyncio.sleep(0.5)
    bot_dir = os.path.join(BASE_DIR, name)
    if os.path.exists(bot_dir):
        shutil.rmtree(bot_dir, ignore_errors=True)
    await db_delete(name)
    return True, None


def read_logs(name, lines=20):
    path = os.path.join(BASE_DIR, name, "bot.log")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        data = f.read().decode(errors="replace").splitlines()
    return data[-lines:]
