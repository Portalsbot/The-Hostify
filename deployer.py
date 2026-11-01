import os
import asyncio
import subprocess
import aiohttp
from config import BASE_DIR
from database import add_bot, update_status, set_bot_username


ENTRY_CANDIDATES = ("main.py", "bot.py", "app.py")


async def _fetch_bot_username(token: str):
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                data = await r.json()
                return data.get("result", {}).get("username")
    except Exception:
        return None


def _find_entry(bot_dir):
    for f in ENTRY_CANDIDATES:
        if os.path.exists(os.path.join(bot_dir, f)):
            return f
    return None


def _launch(bot_dir, env_vars):
    """Start the bot's process from its venv. Returns Popen object."""
    entry = _find_entry(bot_dir)
    if not entry:
        raise FileNotFoundError("No entry point (main.py/bot.py/app.py).")

    venv_python = os.path.join(bot_dir, "venv", "bin", "python")
    if not os.path.exists(venv_python):
        raise FileNotFoundError("venv missing — deploy was incomplete.")

    env = os.environ.copy()
    env.update(env_vars or {})

    log_path = os.path.join(bot_dir, "bot.log")
    log_file = open(log_path, "ab")
    return subprocess.Popen(
        [venv_python, entry],
        cwd=bot_dir,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


async def deploy_bot(name, repo_url, env_vars):
    """Full deploy: clone, venv, install, launch."""
    bot_dir = os.path.join(BASE_DIR, name)

    if os.path.exists(bot_dir):
        return False, "Directory already exists. Use /delete first or pick a new name."

    r = subprocess.run(
        ["git", "clone", repo_url, bot_dir],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False, f"git clone failed: {r.stderr.strip()[:300]}"

    venv_dir = os.path.join(bot_dir, "venv")
    subprocess.run(["python3", "-m", "venv", venv_dir], check=True)
    pip = os.path.join(venv_dir, "bin", "pip")

    req = os.path.join(bot_dir, "requirements.txt")
    if os.path.exists(req):
        subprocess.run([pip, "install", "-r", req], capture_output=True)

    if not _find_entry(bot_dir):
        return False, "No entry point (main.py, bot.py, or app.py) found in repo."

    proc = _launch(bot_dir, env_vars)

    notify_channel = (env_vars or {}).get("NOTIFY_CHANNEL")
    await add_bot(name, repo_url, env_vars, notify_channel)
    await update_status(name, "running", proc.pid)

    token = (env_vars or {}).get("BOT_TOKEN")
    if token:
        username = await _fetch_bot_username(token)
        if username:
            await set_bot_username(name, username)

    return True, str(proc.pid)


async def launch_existing(name, env_vars):
    """Restart without re-cloning. Assumes folder + venv already exist."""
    bot_dir = os.path.join(BASE_DIR, name)
    if not os.path.exists(bot_dir):
        return False, "Bot folder missing."
    try:
        proc = _launch(bot_dir, env_vars)
    except FileNotFoundError as e:
        return False, str(e)
    await update_status(name, "running", proc.pid)
    return True, str(proc.pid)
