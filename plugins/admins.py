from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database import add_admin, get_admins, remove_admin
from smallcaps import sc
from .guard import is_owner
from ui_buttons import button

logger = logging.getLogger("hostify.plugins.admins")

_pending_add: dict[int, int] = {}
_CLOSE = InlineKeyboardMarkup([[button("Close", callback_data="hostify:ui:close", style="danger")]])


def _name(user: dict) -> str:
    return str(user.get("first_name") or (f"@{user['username']}" if user.get("username") else user.get("user_id")))


async def _reply(message, text: str, markup=None):
    return await message.reply_text(
        sc(text),
        parse_mode=ParseMode.DISABLED,
        reply_markup=markup or _CLOSE,
    )


@Client.on_message(filters.command("addadmin") & filters.private)
async def addadmin_command(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    _pending_add[user.id] = message.chat.id
    await _reply(message, "Send me the new admin ID.")


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=5)
async def addadmin_input(client: Client, message):
    owner = message.from_user
    if not owner or not is_owner(owner.id):
        return
    expected_chat = _pending_add.get(owner.id)
    if expected_chat != message.chat.id:
        return
    raw = (message.text or "").strip()
    if not re.fullmatch(r"\d{1,20}", raw):
        await _reply(message, "That is not a valid Telegram user ID. Send a numeric ID, or /cancel to leave.")
        return
    admin_id = int(raw)
    if admin_id <= 0 or admin_id == owner.id:
        await _reply(message, "That ID cannot be added as an admin.")
        return

    try:
        try:
            tg_user = await client.get_users(admin_id)
            username = tg_user.username or ""
            first_name = tg_user.first_name or ""
        except Exception:
            username = ""
            first_name = ""
        await add_admin(admin_id, username=username, first_name=first_name, added_by=owner.id)
        from command_menu import sync_user_commands
        await sync_user_commands(admin_id, "admin")
        _pending_add.pop(owner.id, None)

        bot_username = (await client.get_me()).username
        start_url = f"https://t.me/{bot_username}?start=admin"
        markup = InlineKeyboardMarkup([[
            button("Open Hostify", url=start_url, style="success"),
            button("Close", callback_data="hostify:ui:close", style="danger"),
        ]])
        try:
            await client.send_message(
                admin_id,
                sc("You are now also my admin! Send /start to open Hostify."),
                parse_mode=ParseMode.DISABLED,
                reply_markup=markup,
            )
            notification = "Admin added successfully. The new admin has been notified."
        except Exception:
            notification = (
                "Admin added successfully. Telegram does not allow the bot to message them "
                "until they start it. Send them the link below."
            )
        await _reply(
            message,
            f"{notification}\nAdmin ID: {admin_id}",
            InlineKeyboardMarkup([
                [button("Open Hostify", url=start_url, style="success")],
                [InlineKeyboardButton("Close", callback_data="hostify:ui:close")],
            ]),
        )
    except Exception:
        logger.exception("Could not add admin ID %s", admin_id)
        await _reply(message, "Could not add this admin. Check the controller logs.")


def cancel_admin_add(user_id: int, chat_id: int) -> bool:
    """Clear a pending owner add-admin conversation."""
    if _pending_add.get(user_id) != chat_id:
        return False
    _pending_add.pop(user_id, None)
    return True


@Client.on_message(filters.command("removeadmin") & filters.private)
async def removeadmin_command(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    admins = await get_admins()
    if not admins:
        await _reply(message, "There are no admins to remove.")
        return

    rows = []
    row = []
    for admin in admins:
        admin_id = int(admin.get("user_id", 0))
        if not admin_id:
            continue
        label = _name(admin)[:28]
        row.extend([
            InlineKeyboardButton(label, url=f"tg://user?id={admin_id}"),
            button("Remove", callback_data=f"hostify:admin:remove:{admin_id}", style="danger"),
        ])
        rows.append(row)
        row = []
    rows.append([InlineKeyboardButton("Close", callback_data="hostify:ui:close")])
    await message.reply_text(
        f"<blockquote>{sc('Select the admin you want to remove, senpai.')}</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


@Client.on_callback_query(filters.regex(r"^hostify:admin:remove:\d+$"))
async def removeadmin_callback(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id):
        await query.answer("This action is owner-only.", show_alert=True)
        return
    admin_id = int((query.data or "").rsplit(":", 1)[-1])
    try:
        removed = await remove_admin(admin_id)
        if removed:
            from command_menu import sync_user_commands
            await sync_user_commands(admin_id, None)
        if not removed:
            await query.answer("This admin is no longer registered.", show_alert=True)
            return
        await query.answer("Admin removed.")
        if query.message:
            await query.message.edit_text(
                sc(f"Admin {admin_id} has been removed successfully."),
                parse_mode=ParseMode.DISABLED,
                reply_markup=_CLOSE,
            )
        try:
            await client.send_message(
                admin_id,
                sc("Your Hostify administrator access has been removed."),
                parse_mode=ParseMode.DISABLED,
                reply_markup=_CLOSE,
            )
        except Exception:
            logger.info("Removed admin %s could not be notified by direct message", admin_id)
    except Exception:
        logger.exception("Could not remove admin %s", admin_id)
        await query.answer("Could not remove this admin.", show_alert=True)
