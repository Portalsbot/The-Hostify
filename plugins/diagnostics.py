
import logging

from pyrogram import Client, filters

logger = logging.getLogger("hostify.diagnostics")


@Client.on_message(
    filters.private & filters.text,
    group=-100,
)
async def trace_incoming(client, message):
    user = message.from_user
    text = message.text or ""

    command = (
        text.split(maxsplit=1)[0]
        if text.startswith("/")
        else "<text message>"
    )

    logger.info(
        "INCOMING MESSAGE | user_id=%s | command=%s",
        user.id if user else None,
        command,
    )


@Client.on_message(
    filters.private & filters.command("ping"),
    group=-1,
)
async def ping_handler(client, message):
    try:
        await message.reply_text(
            "Pong! The Hostify is receiving Telegram updates."
        )
        logger.info("PING TEST SUCCESSFUL")
    except Exception:
        logger.exception("PING TEST FAILED")
