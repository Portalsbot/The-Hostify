from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import GITHUB_APP_ID, GITHUB_APP_SLUG
from github_app import (
    GitHubAppError,
    get_accessible_repositories,
    is_configured,
)
from smallcaps import sc
from .guard import is_owner, owner_only

logger = logging.getLogger("hostify.plugins.github")

_APP_PAGE = "https://github.com/apps/the-hostify"
_INSTALL_PAGE = "https://github.com/apps/the-hostify/installations/new"


def _keyboard(ready: bool) -> InlineKeyboardMarkup:
    if ready:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Refresh", callback_data="hostify:github:refresh"),
                InlineKeyboardButton("Setup Guide", callback_data="hostify:github:guide"),
            ],
            [InlineKeyboardButton("Close", callback_data="hostify:github:close")],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Open GitHub App", url=_APP_PAGE),
            InlineKeyboardButton("Install App", url=_INSTALL_PAGE),
        ],
        [InlineKeyboardButton("Setup Guide", callback_data="hostify:github:guide")],
        [InlineKeyboardButton("Close", callback_data="hostify:github:close")],
    ])


def _guide_text() -> str:
    return (
        "GITHUB APP SETUP GUIDE\n\n"
        "1. Open your GitHub App: https://github.com/apps/the-hostify\n"
        "2. In General, note the App ID and App slug.\n"
        "3. In Permissions & events, set Repository permissions: Contents = Read-only; Metadata = Read-only. Save changes.\n"
        "4. In Install App, choose your GitHub account and select only the repositories Hostify may deploy.\n"
        "5. In General, generate a Private Key and keep the downloaded .pem file private.\n\n"
        "WINDOWS: Encode the .pem file as Base64 in PowerShell, then copy the result:\n"
        "$pem = Get-ChildItem \"$env:USERPROFILE\\Downloads\\*.pem\" | Sort-Object LastWriteTime -Descending | Select-Object -First 1\n"
        "if (-not $pem) { throw \"No .pem file found in Downloads\" }\n"
        "[Convert]::ToBase64String([IO.File]::ReadAllBytes($pem.FullName)) | Set-Clipboard\n\n"
        "6. In Render → Hostify → Environment, add:\n"
        "GITHUB_APP_ID = your App ID\n"
        "GITHUB_APP_SLUG = your App slug\n"
        "GITHUB_APP_PRIVATE_KEY_B64 = paste the copied Base64 value\n\n"
        "7. Save and redeploy. Then send /github to verify the connection.\n\n"
        "Do not paste the .pem key, Base64 key, Client Secret or other private credentials into Telegram or GitHub. "
        "Hostify does not require a Client Secret or webhook for this App-installation flow."
    )

async def _screen_text() -> tuple[str, bool]:
    if not is_configured():
        app_id = GITHUB_APP_ID or "not configured"
        slug = GITHUB_APP_SLUG or "not configured"
        return (
            "GITHUB INTEGRATION\n\n"
            "The GitHub App has been created and installed, but Hostify cannot authenticate yet.\n\n"
            f"App ID: {app_id}\n"
            f"App slug: {slug}\n\n"
            "Add these variables in Render → Hostify service → Environment:\n"
            "GITHUB_APP_ID\n"
            "GITHUB_APP_SLUG\n"
            "GITHUB_APP_PRIVATE_KEY_B64\n\n"
            "The private-key value must be Base64-encoded from the downloaded .pem file. "
            "Do not send the private key in Telegram or commit it to GitHub.\n\n"
            "After saving the variables, redeploy Hostify and send /github again.",
            False,
        )

    try:
        installations, repositories = await get_accessible_repositories()
    except GitHubAppError as exc:
        logger.warning("GitHub integration status check failed: %s", exc)
        return (
            "GITHUB INTEGRATION\n\n"
            "Credentials are configured, but GitHub could not verify the App connection.\n\n"
            f"Reason: {str(exc)[:500]}\n\n"
            "Check the App ID, private key, installation and repository permissions in GitHub.",
            True,
        )
    except Exception:
        logger.exception("Unexpected GitHub integration status failure")
        return (
            "GITHUB INTEGRATION\n\n"
            "Hostify could not check GitHub right now. Check the Hostify service logs.",
            True,
        )

    accounts = ", ".join(item["account"] for item in installations) or "None"
    lines = [
        "GITHUB INTEGRATION",
        "",
        "Status: Connected" if installations else "Status: App is not installed",
        f"App: {GITHUB_APP_SLUG or 'the-hostify'}",
        f"App ID: {GITHUB_APP_ID}",
        f"Installations: {len(installations)}",
        f"GitHub accounts: {accounts}",
        f"Accessible repositories: {len(repositories)}",
    ]

    if repositories:
        lines.extend(["", "Accessible repositories:"])
        for item in repositories[:25]:
            visibility = "private" if item["private"] else "public"
            lines.append(f"• {item['full_name']} ({visibility})")
        if len(repositories) > 25:
            lines.append(f"…and {len(repositories) - 25} more.")
        lines.extend([
            "",
            "Private repositories can be selected during deployment only when "
            "they are included in the GitHub App installation.",
        ])
    else:
        lines.extend([
            "",
            "No repositories are currently accessible.",
            "Install the app on your GitHub account and select the repositories Hostify may use.",
        ])

    return "\n".join(lines), bool(installations)


async def _send_or_edit(target, *, edit: bool = False):
    text, ready = await _screen_text()
    styled = sc(text)
    keyboard = _keyboard(ready)
    if edit:
        return await target.edit_text(
            styled,
            reply_markup=keyboard,
            parse_mode=ParseMode.DISABLED,
            disable_web_page_preview=True,
        )
    return await target.reply_text(
        styled,
        reply_markup=keyboard,
        parse_mode=ParseMode.DISABLED,
        disable_web_page_preview=True,
    )


@Client.on_message(filters.command("github") & filters.private & owner_only)
async def github_command(client: Client, message):
    await _send_or_edit(message)


@Client.on_callback_query(filters.regex(r"^hostify:github:"))
async def github_callback(client: Client, query):
    user_id = query.from_user.id if query.from_user else None
    if not is_owner(user_id):
        await query.answer("This control is only available to the owner.", show_alert=True)
        return

    if query.message is None or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("Open this control in the owner's private chat.", show_alert=True)
        return

    action = (query.data or "").rsplit(":", 1)[-1]
    if action == "close":
        await query.answer()
        await query.message.delete()
        return

    if action == "guide":
        await query.message.edit_text(
            sc(_guide_text()),
            parse_mode=ParseMode.DISABLED,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Back", callback_data="hostify:github:back"),
                InlineKeyboardButton("Close", callback_data="hostify:github:close"),
            ]]),
            disable_web_page_preview=True,
        )
        await query.answer()
        return

    if action in {"refresh", "back"}:
        try:
            await _send_or_edit(query.message, edit=True)
            await query.answer("GitHub status refreshed." if action == "refresh" else "")
        except Exception:
            logger.exception("Could not refresh the GitHub screen")
            await query.answer("Could not refresh GitHub status.", show_alert=True)
        return

    await query.answer("Unknown or expired GitHub control.", show_alert=True)
