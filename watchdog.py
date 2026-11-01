import asyncio
import os
from datetime import datetime
from database import list_bots, update_status
from deployer import launch_existing
from config import OWNER_ID
from utils import send_message
import messages as M


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def watchdog_loop(client):
    while True:
        await asyncio.sleep(600)  # 10 minutes
        try:
            bots = await list_bots()
            if not bots:
                continue

            restarted = []
            failed = []

            for bot in bots:
                if bot.get("status") != "running":
                    continue
                if _alive(bot.get("pid")):
                    continue

                ok, detail = await launch_existing(
                    bot["name"], bot.get("env_vars") or {}
                )
                if ok:
                    restarted.append(bot["name"])
                else:
                    failed.append(bot["name"])
                    await update_status(bot["name"], "crashed")

            now = datetime.now().strftime("%H:%M")
            if restarted or failed:
                if failed:
                    text = M.M32_WATCHDOG_FAIL.format(
                        time=now, failed=", ".join(failed)
                    )
                else:
                    text = M.M32_WATCHDOG_SOME.format(
                        time=now, restarted=", ".join(restarted)
                    )
                await send_message(client, OWNER_ID, text)
        except Exception:
            continue
