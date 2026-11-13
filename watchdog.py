from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import psutil

from config import OWNER_ID
from database import list_bots, update_status
from deployer import launch_existing
from render_deployer import deploy_to_render, is_render_configured
from github_app import GitHubAppError, get_installation_token_for_repository
from utils import send_message
import messages as M

logger = logging.getLogger("hostify.watchdog")
_PROCESS_TIME_TOLERANCE = 1.5


def _alive(bot: dict) -> bool:
    """Check PID liveness and creation time to avoid PID-reuse false positives."""
    pid = bot.get("pid")
    expected_created_at = bot.get("pid_created_at")

    if not isinstance(pid, int) or pid <= 0:
        return False

    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False

        # Legacy records lack a creation timestamp. Treat an existing PID as
        # alive rather than launching a duplicate process; stop/delete handlers
        # still fail closed until the bot is redeployed with identity metadata.
        if not isinstance(expected_created_at, (int, float)):
            return True

        return abs(process.create_time() - float(expected_created_at)) <= _PROCESS_TIME_TOLERANCE

    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # An inability to inspect a PID must not trigger a duplicate launch.
        logger.warning("Cannot inspect PID %s; watchdog will leave it untouched", pid)
        return True


async def watchdog_loop(client) -> None:
    while True:
        await asyncio.sleep(600)

        try:
            bots = await list_bots()
            if not bots:
                continue

            restarted: list[str] = []
            failed: list[str] = []

            for bot in bots:
                # Render-managed services run independently of this controller.
                # Never restart them from a local folder on the ephemeral filesystem.
                if bot.get("deployment_provider") == "render":
                    continue
                if bot.get("status") != "running":
                    continue
                if _alive(bot):
                    continue

                name = bot.get("name")
                if not isinstance(name, str) or not name:
                    logger.error("Skipping watchdog record with invalid name")
                    continue

                ok, detail = await launch_existing(name, bot.get("env_vars") or {})
                if not ok and detail == "Bot folder missing." and is_render_configured():
                    # A Render redeploy erased the legacy local folder. Recreate
                    # this project as an independent Render service from its GitHub source.
                    repo_url = bot.get("repo_url")
                    try:
                        if not isinstance(repo_url, str) or not repo_url:
                            raise RuntimeError("Legacy deployment has no GitHub repository URL.")
                        github_token = await get_installation_token_for_repository(repo_url)
                        ok, detail = await deploy_to_render(
                            name,
                            repo_url,
                            "python",
                            bot.get("env_vars") or {},
                            github_token=github_token,
                            deployed_by=int(bot.get("deployed_by") or OWNER_ID),
                        )
                    except (GitHubAppError, RuntimeError) as exc:
                        detail = str(exc)
                        ok = False
                    if ok:
                        restarted.append(name)
                        logger.info("Migrated legacy deployment %s to Render after local folder loss", name)
                        continue
                if ok:
                    restarted.append(name)
                else:
                    failed.append(name)
                    await update_status(name, "crashed", None, None)
                    logger.error("Watchdog restart failed for %s: %s", name, detail)

            if restarted or failed:
                now = datetime.now().strftime("%H:%M")
                if restarted:
                    text = M.M32_WATCHDOG_RESTARTED.format(
                        time=now,
                        restarted=", ".join(restarted),
                    )
                    await send_message(client, OWNER_ID, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="hostify:ui:close")]]))
                if failed:
                    text = M.M32_WATCHDOG_FAIL.format(
                        time=now,
                        failed=", ".join(failed),
                    )
                    await send_message(client, OWNER_ID, text)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Watchdog cycle failed")
