from __future__ import annotations

import logging
from typing import Any

import aiohttp

from config import BOT_TOKEN

logger = logging.getLogger("hostify.command_menu")
_TIMEOUT = aiohttp.ClientTimeout(total=15)

PUBLIC_COMMANDS = [
    {"command": "start", "description": "Open the Hostify welcome menu"},
    {"command": "command", "description": "View commands available to you"},
    {"command": "admins", "description": "View the Hostify admin team"},
    {"command": "ping", "description": "Check Hostify connectivity"},
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    {"command": "deploy", "description": "Deploy a permitted Python repository"},
    {"command": "cancel", "description": "Cancel the current setup"},
    {"command": "list", "description": "View your deployments"},
    {"command": "mylogs", "description": "View recent Hostify controller logs"},
    {"command": "restartme", "description": "Request a Hostify restart"},
    {"command": "stopme", "description": "Request a Hostify shutdown"},
]

OWNER_COMMANDS = ADMIN_COMMANDS + [
    {"command": "commands", "description": "View owner-only commands"},
    {"command": "stop", "description": "Stop a deployed bot"},
    {"command": "restart", "description": "Restart a deployed bot"},
    {"command": "delete", "description": "Delete a deployed bot"},
    {"command": "logs", "description": "View a deployed bot's logs"},
    {"command": "status", "description": "View Hostify system status"},
    {"command": "settings", "description": "Open Hostify settings"},
    {"command": "setbrand", "description": "Edit Hostify branding"},
    {"command": "messageonly", "description": "Browse editable messages"},
    {"command": "setmsg", "description": "Edit a message template"},
    {"command": "resetmsg", "description": "Restore a default message"},
    {"command": "addadmin", "description": "Add a Hostify administrator"},
    {"command": "removeadmin", "description": "Remove a Hostify administrator"},
    {"command": "github", "description": "Check GitHub App integration"},
]


async def _call(method: str, payload: dict[str, Any]) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(url, json=payload) as response:
                result = await response.json(content_type=None)
                if response.status != 200 or not isinstance(result, dict) or not result.get("ok"):
                    logger.warning(
                        "Telegram %s failed (HTTP %s): %s",
                        method,
                        response.status,
                        str(result.get("description", "unknown error"))[:300]
                        if isinstance(result, dict) else "invalid response",
                    )
                    return False
                return True
    except (aiohttp.ClientError, TimeoutError, ValueError):
        logger.exception("Telegram command-menu request failed: %s", method)
        return False


async def sync_default_commands() -> None:
    """Set the public command list and refresh owner/admin chat-specific menus."""
    await _call("setMyCommands", {
        "commands": PUBLIC_COMMANDS,
        "scope": {"type": "default"},
    })


async def sync_user_commands(user_id: int, role: str | None) -> bool:
    """Set or clear the private-chat command menu for one user."""
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    scope = {"type": "chat", "chat_id": user_id}
    if role == "owner":
        return await _call("setMyCommands", {"commands": OWNER_COMMANDS, "scope": scope})
    if role == "admin":
        return await _call("setMyCommands", {"commands": ADMIN_COMMANDS, "scope": scope})
    return await _call("deleteMyCommands", {"scope": scope})
