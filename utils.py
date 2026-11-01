from pyrogram import Client
from smallcaps import sc
import messages as M


async def send_message(client: Client, chat_id, text, reply_markup=None, reply_to_message_id=None):
    caption = sc(text)
    try:
        return await client.send_photo(
            chat_id=chat_id,
            photo=M.BOT_IMAGE_URL,
            caption=caption,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception:
        return await client.send_message(
            chat_id=chat_id,
            text=caption,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )


async def reply_message(message, text, reply_markup=None):
    return await send_message(
        client=message._client,
        chat_id=message.chat.id,
        text=text,
        reply_markup=reply_markup,
        reply_to_message_id=message.id,
    )


async def edit_message(msg, text, reply_markup=None):
    """Edit either a photo caption or plain text."""
    caption = sc(text)
    try:
        return await msg.edit_caption(caption=caption, reply_markup=reply_markup)
    except Exception:
        return await msg.edit_text(text=caption, reply_markup=reply_markup)
