from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import web
from pyrogram import Client, idle

from config import API_ID, API_HASH, BOT_TOKEN, SESSION_DIR, PORT, BASE_DIR
from database import close_database, verify_database_connection
from branding import load_branding
from utils import load_message_templates
from watchdog import watchdog_loop

Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
_file_handler = RotatingFileHandler(
    Path(BASE_DIR) / "hostify.log",
    maxBytes=2 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(), _file_handler],
)

logger = logging.getLogger("hostify")
SERVICE_READY = False


async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok" if SERVICE_READY else "starting",
            "service": "The Hostify",
        },
        status=200 if SERVICE_READY else 503,
    )


async def main() -> None:
    global SERVICE_READY

    # Keep the Pyrogram session in the configured writable SESSION_DIR.
    app = Client(
        "the_hostify",
        workdir=str(SESSION_DIR),
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        plugins=dict(root="plugins"),
        no_updates=False,
    )

    active_loop = asyncio.get_running_loop()
    if app.loop is not active_loop:
        raise RuntimeError("Pyrogram Client is attached to the wrong asyncio event loop.")

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
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info("Health server listening on 0.0.0.0:%s", PORT)

        await verify_database_connection()
        database_ready = True

        await load_branding()
        await load_message_templates()

        logger.info("Starting Telegram client...")
        await app.start()
        app_started = True

        me = await app.get_me()
        from command_menu import sync_default_commands, sync_user_commands
        from database import get_admins
        try:
            await sync_default_commands()
            await sync_user_commands(me.id, "owner")
            for admin in await get_admins():
                admin_id = admin.get("user_id")
                if isinstance(admin_id, int) and admin_id > 0:
                    await sync_user_commands(admin_id, "admin")
        except Exception:
            logger.exception("Could not synchronize Telegram command menus")

        SERVICE_READY = True
        logger.info("CONNECTED BOT: @%s | BOT ID: %s", me.username, me.id)
        from plugins.controller import notify_startup
        await notify_startup(app)

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
