from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from pyrogram import Client, filters
from pyrogram.enums import ParseMode

from config import ALLOW_UNSANDBOXED_DEPLOYMENTS
from utils import format_output

import messages as M
from database import get_bot
from deployer import deploy_bot, _validate_env_vars, _validate_name, _validate_repo_url
from github_app import GitHubAppError, get_installation_token_for_repository
from .guard import is_owner_or_admin
from .channel import post_deploy_announcement

logger = logging.getLogger("hostify.plugins.deploy_flow")

_SESSION_TTL_SECONDS = 15 * 60
_MAX_DETAIL_CHARS = 2_000


@dataclass(slots=True)
class DeploySession:
    chat_id: int
    stage: str
    created_at: float
    repo_url: str | None = None
    name: str | None = None


# This bot is configured for a single owner. The state is therefore keyed by
# the owner ID and additionally bound to the private chat that began the flow.
_sessions: dict[int, DeploySession] = {}
_session_lock = asyncio.Lock()


async def _reply(message, text: str):
    return await message.reply_text(
        format_output(text),
        parse_mode=ParseMode.DISABLED,
    )


def _validate_github_url(value: str) -> str:
    """Use the exact GitHub URL validator used by the deployment backend."""
    return _validate_repo_url(value)


def _parse_environment(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines, then apply the backend's single validation policy."""
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
    if not ALLOW_UNSANDBOXED_DEPLOYMENTS:
        await _reply(message, f"{M.M74_NO_DEPLOY_ACCESS}\n\n{M.M75_DEPLOY_TRUST_WARNING}")
        return

    async with _session_lock:
        existing = _sessions.get(user.id)
        if existing and (
            existing.stage == "deploying"
            or time.monotonic() - existing.created_at <= _SESSION_TTL_SECONDS
        ):
            if existing.stage == "deploying":
                await _reply(message, M.M98_DEPLOY_ALREADY_RUNNING)
            else:
                await _reply(message, M.M99_DEPLOY_SESSION_ACTIVE)
            return

        _sessions[user.id] = DeploySession(
            chat_id=message.chat.id,
            stage="repository",
            created_at=time.monotonic(),
        )

    await _reply(message, M.M07_ASK_REPO)


@Client.on_message(filters.command("cancel") & filters.private)
async def cancel_deploy(client: Client, message):
    user = message.from_user
    if user is None or not await is_owner_or_admin(user.id):
        return

    async with _session_lock:
        session = _sessions.get(user.id)
        if session is None or session.chat_id != message.chat.id:
            await _reply(message, M.M100_CANCEL_NO_SESSION)
            return

        if session.stage == "deploying":
            await _reply(
                message,
                M.M101_CANCEL_LOCKED,
            )
            return

        _sessions.pop(user.id, None)

    await _reply(message, M.M33_CANCELLED)


@Client.on_message(
    filters.private
    & filters.text
    & ~filters.regex(r"^/")
)
async def deploy_input_handler(client: Client, message):
    user = message.from_user
    if user is None or not message.text or not await is_owner_or_admin(user.id):
        return

    # Keep each state transition atomic. Never hold the lock while installing
    # third-party packages or starting an external process.
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
            await _reply(message, M.M09_ASK_NAME)
            return

        if session.stage == "name":
            name = message.text.strip()
            try:
                _validate_name(name)
            except ValueError:
                await _reply(message, M.M10_INVALID_NAME)
                return

            existing = await get_bot(name)
            if existing:
                await _reply(message, M.M34_NAME_TAKEN)
                return

            session.name = name
            session.stage = "environment"
            session.created_at = time.monotonic()
            await _reply(message, M.M11_ASK_ENV)
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
        if not name or not repo_url:
            _sessions.pop(user.id, None)
            await _reply(message, M.M104_SESSION_INCOMPLETE)
            return

        session.stage = "deploying"

    await _reply(message, M.M12_DEPLOYING.format(name=name))

    try:
        try:
            github_token = await get_installation_token_for_repository(repo_url)
        except GitHubAppError as exc:
            logger.warning("GitHub repository authorization failed for %s: %s", repo_url, exc)
            await _reply(message, M.M14_DEPLOY_FAIL.format(name=name, reason=str(exc)[:_MAX_DETAIL_CHARS]))
            return

        success, detail = await deploy_bot(
            name, repo_url, env_vars,
            github_token=github_token,
            deployed_by=user.id,
        )

        if success:
            # Channel notifications are best-effort. A database or Telegram
            # notification error must never turn a successful deployment into
            # a user-visible deployment failure.
            try:
                await post_deploy_announcement(client, name, repo_url, env_vars)
            except Exception:
                logger.exception(
                    "Bot %s deployed, but its channel announcement failed",
                    name,
                )
            response = M.M13_DEPLOY_OK.format(name=name, pid=detail)
        else:
            safe_detail = str(detail or "Unknown deployment error")[:_MAX_DETAIL_CHARS]
            response = M.M14_DEPLOY_FAIL.format(name=name, reason=safe_detail)

        await _reply(message, response)

    except asyncio.CancelledError:
        logger.warning("Deployment handler cancelled for bot %s", name)
        raise
    except Exception:
        logger.exception("Unexpected deployment-flow failure for bot %s", name)
        await _reply(message, M.M105_DEPLOY_UNEXPECTED_FAILURE.format(name=name))
    finally:
        async with _session_lock:
            current = _sessions.get(user.id)
            if current is session:
                _sessions.pop(user.id, None)
