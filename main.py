from __future__ import annotations

import asyncio
import logging

from pyrogram import Client, idle

from config import API_ID, API_HASH, BOT_TOKEN
from database import close_database, verify_database_connection
from watchdog import watchdog_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("hostify")

app = Client(
    "the_hostify",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="plugins"),
)


async def main() -> None:
    watchdog_task: asyncio.Task | None = None
    app_started = False

    try:
        # Fail before connecting Telegram if MongoDB is unavailable.
        await verify_database_connection()
        await app.start()
        app_started = True

        watchdog_task = asyncio.create_task(
            watchdog_loop(app),
            name="hostify-watchdog",
        )

        logger.info("The Hostify is running.")
        await idle()

    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Watchdog shutdown failed")

        try:
            if app_started:
                await app.stop()
        finally:
            await close_database()


if __name__ == "__main__":
    asyncio.run(main())
