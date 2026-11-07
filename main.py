
from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web
from pyrogram import Client, idle

from config import API_ID, API_HASH, BOT_TOKEN
from database import close_database, verify_database_connection
from branding import load_branding
from utils import load_message_templates
from watchdog import watchdog_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("hostify")
SERVICE_READY = False


async def health(request):
    return web.json_response(
        {
            "status": "ok" if SERVICE_READY else "starting",
            "service": "The Hostify",
        },
        status=200 if SERVICE_READY else 503,
    )


async def main() -> None:
    global SERVICE_READY

    # IMPORTANT:
    # Create the Pyrogram Client inside the running event loop.
    app = Client(
        "the_hostify",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        plugins=dict(root="plugins"),
        no_updates=False,
    )

    # Verify that Pyrogram captured the correct event loop.
    active_loop = asyncio.get_running_loop()

    if app.loop is not active_loop:
        raise RuntimeError(
            "Pyrogram Client is attached to the wrong asyncio event loop."
        )

    logger.info("PYROGRAM EVENT LOOP MATCH: True")

    health_app = web.Application()
    health_app.router.add_get("/", health)
    health_app.router.add_get("/health", health)

    runner = web.AppRunner(health_app)

    watchdog_task = None
    app_started = False
    database_ready = False

    try:
        await runner.setup()

        port = int(os.environ.get("PORT", "10000"))
        site = web.TCPSite(health_app_runner := runner, "0.0.0.0", port)
        await site.start()

        logger.info("Health server listening on port %s", port)

        await verify_database_connection()
        database_ready = True

        await load_branding()
        await load_message_templates()

        logger.info("Starting Telegram client...")
        await app.start()
        app_started = True

        me = await app.get_me()
        SERVICE_READY = True

        logger.info(
            "CONNECTED BOT: @%s | BOT ID: %s",
            me.username,
            me.id,
        )

        watchdog_task = asyncio.create_task(
            watchdog_loop(app),
            name="hostify-watchdog",
        )

        logger.info("The Hostify is running.")
        await idle()

    finally:
        SERVICE_READY = False

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
            try:
                if database_ready:
                    await close_database()
            finally:
                await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
