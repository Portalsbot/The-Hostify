from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from pyrogram import Client, filters
from pyrogram.enums import ParseMode

import messages as M
from database import get_bot
from deployer import deploy_bot
from .guard import owner_only

logger = logging.getLogger("hostify.plugins.deploy_flow")

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_ENV_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
_RESERVED_ENV_KEYS = {
    "PATH", "HOME", "PWD", "OLDPWD", "VIRTUAL_ENV", "PYTHONHOME",
    "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONSTARTUP", "PYTHONUSERBASE",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES",
    "SHELL", "BASH_ENV", "ENV", "IFS",
}
_SESSION_TTL_SECONDS = 15 * 60
_MAX_ENV_VARS = 40
_MAX_ENV_BYTES = 32_768
_MAX_DETAIL_CHARS = 2_000
_REPOSITORY_PROMPT = (
    "Step 1 of 3\n\n"
    "Send a public GitHub repository URL to deploy.\n"
    "Private repository support is not enabled yet. Never include access tokens in a URL."
)
_ENVIRONMENT_PROMPT = (
    "Step 3 of 3\n\n"
    "Send environment variables as KEY=VALUE, one per line.\n"
    "Send skip if none. Never send Hostify controller credentials."
)


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
        text,
        parse_mode=ParseMode.DISABLED,
    )


def _validate_github_url(value: str) -> str:
    """Allow public GitHub repository URLs only; never accept URL credentials."""
    if len(value) > 300:
        raise ValueError("Repository URL is too long.")

    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValueError(
            "Send a credential-free HTTPS GitHub URL, for example:\n"
            "https://github.com/owner/repository"
        )

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("The URL must point directly to a GitHub repository.")

    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]

    segment = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
    if (
        not segment.fullmatch(owner)
        or not segment.fullmatch(repository)
        or owner in {".", ".."}
        or repository in {".", ".."}
    ):
        raise ValueError("The GitHub owner or repository name is invalid.")

    return f"https://github.com/{owner}/{repository}.git"


def _parse_environment(text: str) -> dict[str, str]:
    if text.strip().casefold() == "skip":
        return {}

    result: dict[str, str] = {}
    total_bytes = 0

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"Line {line_number}: expected KEY=VALUE.")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not _ENV_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Line {line_number}: invalid variable name.")
        if key in _RESERVED_ENV_KEYS:
            raise ValueError(f"{key} is reserved and cannot be overridden.")
        if key in result:
            raise ValueError(f"Duplicate environment variable: {key}.")
        if len(value) > 4_096 or "\x00" in value:
            raise ValueError(f"Value for {key} is invalid or too long.")

        total_bytes += len(key.encode("utf-8")) + len(value.encode("utf-8"))
        if total_bytes > _MAX_ENV_BYTES:
            raise ValueError("Combined environment variables exceed 32 KiB.")

        result[key] = value
        if len(result) > _MAX_ENV_VARS:
            raise ValueError(f"At most {_MAX_ENV_VARS} environment variables are allowed.")

    return result


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


@Client.on_message(filters.command("deploy") & filters.private & owner_only)
async def deploy_command(client: Client, message):
    user = message.from_user
    if user is None:
        return

    async with _session_lock:
        existing = _sessions.get(user.id)
        if existing and (
            existing.stage == "deploying"
            or time.monotonic() - existing.created_at <= _SESSION_TTL_SECONDS
        ):
            if existing.stage == "deploying":
                await _reply(message, "A deployment is already running. Please wait for its result.")
            else:
                await _reply(message, "A deployment session is already active. Send /cancel first or continue the current step.")
            return

        _sessions[user.id] = DeploySession(
            chat_id=message.chat.id,
            stage="repository",
            created_at=time.monotonic(),
        )

    await _reply(message, _REPOSITORY_PROMPT)


@Client.on_message(filters.command("cancel") & filters.private & owner_only)
async def cancel_deploy(client: Client, message):
    user = message.from_user
    if user is None:
        return

    async with _session_lock:
        session = _sessions.get(user.id)
        if session is None or session.chat_id != message.chat.id:
            await _reply(message, "There is no active deployment session to cancel.")
            return

        if session.stage == "deploying":
            await _reply(
                message,
                "Deployment has already started and cannot be cancelled safely at this stage. Wait for the result.",
            )
            return

        _sessions.pop(user.id, None)

    await _reply(message, M.M33_CANCELLED)


@Client.on_message(
    filters.private
    & owner_only
    & filters.text
    & ~filters.regex(r"^/")
)
async def deploy_input_handler(client: Client, message):
    user = message.from_user
    if user is None or not message.text:
        return

    # Keep each state transition atomic. Never hold the lock while installing
    # third-party packages or starting an external process.
    async with _session_lock:
        session = _get_valid_session(user.id, message.chat.id)
        if session is None:
            return

        if session.stage == "deploying":
            await _reply(message, "Deployment is already running. Please wait for the result.")
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
            if not _NAME_PATTERN.fullmatch(name):
                await _reply(message, M.M10_INVALID_NAME)
                return

            existing = await get_bot(name)
            if existing:
                await _reply(message, M.M34_NAME_TAKEN)
                return

            session.name = name
            session.stage = "environment"
            session.created_at = time.monotonic()
            await _reply(message, _ENVIRONMENT_PROMPT)
            return

        if session.stage != "environment":
            _sessions.pop(user.id, None)
            await _reply(message, "Deployment session state was invalid. Start again with /deploy.")
            return

        try:
            env_vars = _parse_environment(message.text)
        except ValueError as exc:
            await _reply(message, f"Invalid environment variables: {exc}\n\nSend them again, or /cancel.")
            return

        name = session.name
        repo_url = session.repo_url
        if not name or not repo_url:
            _sessions.pop(user.id, None)
            await _reply(message, "Deployment session was incomplete. Start again with /deploy.")
            return

        session.stage = "deploying"

    await _reply(message, M.M12_DEPLOYING.format(name=name))

    try:
        success, detail = await deploy_bot(name, repo_url, env_vars)

        if success:
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
        await _reply(message, f"Deployment failed for {name}. Check controller logs.")
    finally:
        async with _session_lock:
            current = _sessions.get(user.id)
            if current is session:
                _sessions.pop(user.id, None)
