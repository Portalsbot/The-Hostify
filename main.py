import asyncio
from pyrogram import Client, idle
from config import API_ID, API_HASH, BOT_TOKEN
from watchdog import watchdog_loop


app = Client(
    "the_hostify",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="plugins"),
)


async def main():
    await app.start()
    asyncio.create_task(watchdog_loop(app))
    print("The Hostify is running.")
    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
