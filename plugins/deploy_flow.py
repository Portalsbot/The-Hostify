from __future__ import annotations

import asyncio
import html
import logging
import time
from dataclasses import dataclass

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup

import messages as M
from database import get_bot
from deployer import _validate_env_vars, _validate_name, _validate_repo_url
from github_app import GitHubAppError, get_installation_token_for_repository
from render_deployer import deploy_to_render, is_render_configured
from utils import format_output
from .guard import is_owner_or_admin
from .admins import cancel_admin_add
from .dashboard import cancel_environment_edit
from .owner import cancel_brand_edit
from .message_editor import cancel_message_edit
from .channel import post_deploy_announcement
from ui_buttons import button

logger = logging.getLogger("hostify.plugins.deploy_flow")

_SESSION_TTL_SECONDS = 15 * 60
_MAX_DETAIL_CHARS = 2_000


@dataclass(slots=True)
class DeploySession:
    chat_id: int
    stage: str
    created_at: float
    runtime_type: str | None = None
    repo_url: str | None = None
    name: str | None = None


_sessions: dict[int, DeploySession] = {}
_session_lock = asyncio.Lock()


async def _reply(message, text: str, *, plain: bool = False):
    visible = str(text) if plain else format_output(text)
    return await message.reply_text(
        f"<blockquote>{html.escape(visible)}</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            button("Close", callback_data="hostify:ui:close", style="danger")
        ]]),
        disable_web_page_preview=True,
    )


def _type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [button("Python", callback_data="hostify:deploy:type:python", style="success")],
        [button("Java", callback_data="hostify:deploy:type:java")],
        [button("Docker", callback_data="hostify:deploy:type:docker")],
        [button("Close", callback_data="hostify:ui:close", style="danger")],
    ])


def _validate_github_url(value: str) -> str:
    return _validate_repo_url(value)


def _parse_environment(text: str) -> dict[str, str]:
    if text.strip().casefold() == "skip":
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"Line {line_number}: expected KEY=VALUE.")
        key, value = line.split("=", 1)
        key = key.strip()
        if key in values:
            raise ValueError(f"Line {line_number}: duplicate environment variable {key}.")
        values[key] = value.strip()
    return _validate_env_vars(values)


def _get_valid_session(user_id: int, chat_id: int) -> DeploySession | None:
    session = _sessions.get(user_id)
    if session is None:
        return None
    if (
        session.stage != "deploying"
        and time.monotonic() - session.created_at > _SESSION_TTL_SECONDS
    ):
        _sessions.pop(user_id, None)
        return None
    if session.chat_id != chat_id:
        return None
    return session


@Client.on_message(filters.command("deploy") & filters.private)
async def deploy_command(client: Client, message):
    user = message.from_user
    if user is None:
        return
    if not await is_owner_or_admin(user.id):
        await _reply(message, "Deployment access is restricted to the owner and approved admins.")
        return
    if not is_render_configured():
        await _reply(
            message,
            "Render deployment is not configured yet. Add RENDER_API_KEY and RENDER_WORKSPACE_ID to Hostify's Render environment, then redeploy Hostify.",
        )
        return

    async with _session_lock:
        existing = _sessions.get(user.id)
        if existing and (
            existing.stage == "deploying"
            or time.monotonic() - existing.created_at <= _SESSION_TTL_SECONDS
        ):
            await _reply(
                message,
                M.M98_DEPLOY_ALREADY_RUNNING if existing.stage == "deploying" else M.M99_DEPLOY_SESSION_ACTIVE,
            )
            return
        _sessions[user.id] = DeploySession(
            chat_id=message.chat.id,
            stage="type",
            created_at=time.monotonic(),
        )

    await message.reply_text(
        "<blockquote>Select what you want to deploy.</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=_type_keyboard(),
    )


@Client.on_callback_query(filters.regex(r"^hostify:deploy:type:(python|java|docker)$"))
async def deploy_type_callback(client: Client, query):
    user = query.from_user
    if user is None or query.message is None or not await is_owner_or_admin(user.id):
        await query.answer("Deployment access is restricted.", show_alert=True)
        return

    runtime_type = (query.data or "").rsplit(":", 1)[-1]
    async with _session_lock:
        session = _get_valid_session(user.id, query.message.chat.id)
        if session is None or session.stage != "type":
            await query.answer("This deployment session has expired. Send /deploy again.", show_alert=True)
            return
        session.runtime_type = runtime_type
        session.stage = "repository"
        session.created_at = time.monotonic()

    await query.message.edit_text(
        f"<blockquote>Selected: {runtime_type.title()}\n\nSend the GitHub repository URL.</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            button("Close", callback_data="hostify:ui:close", style="danger")
        ]]),
        disable_web_page_preview=True,
    )
    await query.answer()


@Client.on_message(filters.command(["cancel", "cancle"]) & filters.private)
async def cancel_deploy(client: Client, message):
    user = message.from_user
    if user is None or not await is_owner_or_admin(user.id):
        return
    if cancel_admin_add(user.id, message.chat.id):
        await _reply(message, "Admin creation cancelled.")
        return
    if cancel_environment_edit(user.id, message.chat.id):
        await _reply(message, "Environment editing cancelled.")
        return
    if cancel_brand_edit(user.id, message.chat.id):
        await _reply(message, "Branding edit cancelled.")
        return
    if cancel_message_edit(user.id, message.chat.id):
        await _reply(message, "Message editing cancelled.")
        return

    async with _session_lock:
        session = _sessions.get(user.id)
        if session is None or session.chat_id != message.chat.id:
            await _reply(message, M.M100_CANCEL_NO_SESSION)
            return
        if session.stage == "deploying":
            await _reply(message, M.M101_CANCEL_LOCKED)
            return
        _sessions.pop(user.id, None)
    await _reply(message, M.M116_DEPLOY_CANCELLED)


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
async def deploy_input_handler(client: Client, message):
    user = message.from_user
    if user is None or not message.text or not await is_owner_or_admin(user.id):
        return

    async with _session_lock:
        session = _get_valid_session(user.id, message.chat.id)
        if session is None:
            return
        if session.stage == "deploying":
            await _reply(message, M.M98_DEPLOY_ALREADY_RUNNING)
            return

        if session.stage == "repository":
            try:
                session.repo_url = _validate_github_url(message.text)
            except ValueError as exc:
                await _reply(message, f"{M.M08_INVALID_URL}\n\n{exc}")
                return
            session.stage = "name"
            session.created_at = time.monotonic()
            await _reply(message, M.M09_ASK_NAME, plain=True)
            return

        if session.stage == "name":
            name = message.text.strip()
            try:
                _validate_name(name)
            except ValueError:
                await _reply(message, M.M10_INVALID_NAME)
                return
            if await get_bot(name):
                await _reply(message, M.M34_NAME_TAKEN)
                return
            session.name = name
            session.stage = "environment"
            session.created_at = time.monotonic()
            await _reply(message, M.M11_ASK_ENV, plain=True)
            return

        if session.stage != "environment":
            _sessions.pop(user.id, None)
            await _reply(message, M.M102_SESSION_INVALID)
            return

        try:
            env_vars = _parse_environment(message.text)
        except ValueError as exc:
            await _reply(message, M.M103_ENV_INVALID.format(reason=str(exc)))
            return

        name = session.name
        repo_url = session.repo_url
        runtime_type = session.runtime_type
        if not name or not repo_url or runtime_type not in {"python", "java", "docker"}:
            _sessions.pop(user.id, None)
            await _reply(message, M.M104_SESSION_INCOMPLETE)
            return
        session.stage = "deploying"

    await _reply(message, f"Creating your {runtime_type.title()} service on Render: {name}. Deployment may take several minutes.")

    try:
        try:
            github_token = await get_installation_token_for_repository(repo_url)
        except GitHubAppError as exc:
            logger.warning("GitHub repository authorization failed for %s: %s", repo_url, exc)
            await _reply(message, M.M14_DEPLOY_FAIL.format(name=name, reason=str(exc)[:_MAX_DETAIL_CHARS]))
            return

        success, detail = await deploy_to_render(
            name,
            repo_url,
            runtime_type,
            env_vars,
            github_token=github_token,
            deployed_by=user.id,
        )

        if success:
            service_id, dashboard_url = detail.split("|", 1)
            try:
                await post_deploy_announcement(client, name, repo_url, env_vars)
            except Exception:
                logger.exception("Render service %s deployed, but announcement failed", name)
            response = (
                f"Deployment successful: {name}\n"
                f"Runtime: {runtime_type.title()}\n"
                f"Render service: {service_id}\n"
                f"Dashboard: {dashboard_url}"
            )
        else:
            response = M.M14_DEPLOY_FAIL.format(
                name=name,
                reason=str(detail or "Unknown Render deployment error")[:_MAX_DETAIL_CHARS],
            )
        await _reply(message, response)

    except asyncio.CancelledError:
        logger.warning("Render deployment handler cancelled for %s", name)
        raise
    except Exception:
        logger.exception("Unexpected Render deployment-flow failure for %s", name)
        await _reply(message, M.M105_DEPLOY_UNEXPECTED_FAILURE.format(name=name))
    finally:
        async with _session_lock:
            current = _sessions.get(user.id)
            if current is session:
                _sessions.pop(user.id, None)
