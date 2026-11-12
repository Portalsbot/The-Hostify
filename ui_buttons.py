from __future__ import annotations

from pyrogram.types import InlineKeyboardButton


def button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    """Create a Bot API button, gracefully falling back on older Pyrofork builds."""
    kwargs = {}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if style in {"success", "danger", "primary"}:
        kwargs["style"] = style
    try:
        return InlineKeyboardButton(text, **kwargs)
    except TypeError:
        kwargs.pop("style", None)
        return InlineKeyboardButton(text, **kwargs)
