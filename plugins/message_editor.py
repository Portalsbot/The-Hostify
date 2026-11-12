from __future__ import annotations

import html
import logging
import re

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import messages as M
from smallcaps import sc
from .guard import is_owner

logger = logging.getLogger("hostify.plugins.message_editor")
_PAGE_SIZE = 20
_pending_edit: dict[int, tuple[int, str, int]] = {}


def _label(key: str) -> str:
    label = re.sub(r"^M\d+_", "", key)
    label = label.replace("_", " ").replace("BTN ", "")
    return label.title()[:32]


def _close():
    return [InlineKeyboardButton("Close", callback_data="hostify:ui:close")]


def _styled_html(value: str) -> str:
    parts = re.split(r"(<[^>]+>)", value)
    return "".join(part if part.startswith("<") and part.endswith(">") else sc(part) for part in parts)


def _list_markup(page: int):
    keys = sorted(M.DEFAULT_MESSAGES)
    total_pages = max(1, (len(keys) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    selected = keys[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]
    rows, row = [], []
    for key in selected:
        row.append(InlineKeyboardButton(
            _label(key),
            callback_data=f"hostify:message:show:{key}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("Previous", callback_data=f"hostify:message:list:{page-1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next", callback_data=f"hostify:message:list:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("Close", callback_data="hostify:ui:close")])
    return InlineKeyboardMarkup(rows), page, total_pages


async def _show_list(target, page: int, *, edit: bool):
    markup, page, total = _list_markup(page)
    text = f"Click any button to edit a message.\n\nPage {page + 1} of {total}."
    if edit:
        await target.edit_text(sc(text), parse_mode=ParseMode.DISABLED, reply_markup=markup)
    else:
        await target.reply_text(sc(text), parse_mode=ParseMode.DISABLED, reply_markup=markup)


def _show_markup(key: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Edit Text", callback_data=f"hostify:message:edit:{key}")],
        [InlineKeyboardButton("Reset Default", callback_data=f"hostify:message:reset:{key}")],
        [InlineKeyboardButton("Back", callback_data="hostify:message:back:0"),
         InlineKeyboardButton("Close", callback_data="hostify:ui:close")],
    ])


@Client.on_message(filters.command("messageonly") & filters.private)
async def messageonly_command(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id):
        return
    await _show_list(message, 0, edit=False)


def cancel_message_edit(user_id: int, chat_id: int) -> bool:
    pending = _pending_edit.get(user_id)
    if pending is None or pending[0] != chat_id:
        return False
    _pending_edit.pop(user_id, None)
    return True


def _template_details(key: str) -> str:
    current = getattr(M, key, M.DEFAULT_MESSAGES[key])
    return (
        f"<b>{html.escape(_label(key))}</b>\n"
        f"Message ID: <code>{html.escape(key)}</code>\n\n"
        f"<blockquote>{html.escape(current)}</blockquote>\n\n"
        "Use Edit Text to replace this message, or Reset Default to restore it."
    )


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=7)
async def message_edit_input(client: Client, message):
    user = message.from_user
    if not user or not is_owner(user.id) or not message.text:
        return
    pending = _pending_edit.get(user.id)
    if pending is None:
        return
    chat_id, key, prompt_id = pending
    if message.chat.id != chat_id:
        return
    if key not in M.DEFAULT_MESSAGES:
        _pending_edit.pop(user.id, None)
        return
    try:
        from database import set_custom_message
        await set_custom_message(key, message.text)
        M.apply_overrides({key: message.text})
        _pending_edit.pop(user.id, None)
        prompt = await client.get_messages(chat_id, prompt_id)
        await prompt.edit_text(
            _styled_html(_template_details(key)),
            parse_mode=ParseMode.HTML,
            reply_markup=_show_markup(key),
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Could not edit message template %s", key)
        try:
            prompt = await client.get_messages(chat_id, prompt_id)
            await prompt.edit_text(
                sc("Message update failed. Try again or send /cancel."),
                parse_mode=ParseMode.DISABLED,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Back", callback_data=f"hostify:message:show:{key}"),
                     InlineKeyboardButton("Close", callback_data="hostify:ui:close")]
                ]),
            )
        except Exception:
            logger.exception("Could not update message editor prompt")


@Client.on_callback_query(filters.regex(r"^hostify:message:"))
async def message_editor_callback(client: Client, query):
    user = query.from_user
    if not user or not is_owner(user.id) or query.message is None:
        await query.answer("Owner access required.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    action = parts[2] if len(parts) > 2 else ""
    value = parts[3] if len(parts) > 3 else "0"

    if action in {"list", "back"}:
        page = int(value) if value.isdigit() else 0
        await _show_list(query.message, page, edit=True)
        await query.answer()
        return

    if action == "show":
        key = value
        if key not in M.DEFAULT_MESSAGES:
            await query.answer("Message template not found.", show_alert=True)
            return
        content = _template_details(key)
        await query.message.edit_text(
            _styled_html(content),
            parse_mode=ParseMode.HTML,
            reply_markup=_show_markup(key),
            disable_web_page_preview=True,
        )
        await query.answer()
        return

    if action == "edit":
        key = value
        if key not in M.DEFAULT_MESSAGES:
            await query.answer("Message template not found.", show_alert=True)
            return
        _pending_edit[user.id] = (query.message.chat.id, key, query.message.id)
        await query.message.edit_text(
            _styled_html(
                f"<b>{html.escape(_label(key))}</b>\n"
                f"Message ID: <code>{html.escape(key)}</code>\n\n"
                "Send the complete replacement text now.\nUse /cancel to abort."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back", callback_data=f"hostify:message:show:{key}"),
                 InlineKeyboardButton("Close", callback_data="hostify:ui:close")]
            ]),
            disable_web_page_preview=True,
        )
        await query.answer()
        return

    if action == "reset":
        key = value
        if key not in M.DEFAULT_MESSAGES:
            await query.answer("Message template not found.", show_alert=True)
            return
        try:
            from database import reset_custom_message
            await reset_custom_message(key)
            M.reset_default(key)
            await query.message.edit_text(
                sc(f"{_label(key)} restored to its default message."),
                parse_mode=ParseMode.DISABLED,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Back", callback_data="hostify:message:back:0"),
                     InlineKeyboardButton("Close", callback_data="hostify:ui:close")]
                ]),
            )
            await query.answer("Default message restored.")
        except Exception:
            logger.exception("Could not reset message template %s", key)
            await query.answer("Could not reset this template.", show_alert=True)
        return

    await query.answer("Unknown message editor action.", show_alert=True)
