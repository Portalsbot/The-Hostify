from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pymongo import AsyncMongoClient

from config import MONGO_URI
from secrets_store import decrypt_env_vars, encrypt_env_vars, encryption_enabled

_client = AsyncMongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10_000,
    connectTimeoutMS=10_000,
)
_db = _client.hostify
_bots = _db.deployed_bots
_admins = _db.admins
_users = _db.users
_settings = _db.app_settings
_UNSET = object()
_MESSAGE_KEY = re.compile(r"^M\d{2,3}_[A-Z0-9_]{1,64}$")

DEFAULT_APP_SETTINGS: dict[str, str] = {
    "brand_name": "The Hostify",
    "developer_username": "reactiveHCL",
    "developer_id": "8043083450",
    "support_username": "Just_Astha",
    "support_id": "7271115259",
    "promo_channel_username": "Anime_Portals",
    "promo_channel_id": "-1003545564222",
}


def _decode_bot(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    result = dict(document)
    result["env_vars"] = decrypt_env_vars(result.get("env_vars"))
    return result


async def verify_database_connection() -> None:
    """Verify MongoDB and ensure credential encryption is configured."""
    if not encryption_enabled():
        raise RuntimeError(
            "DATA_ENCRYPTION_KEY is required. Generate a Fernet key and add it to the service environment."
        )
    await _client.admin.command("ping")
    async for document in _bots.find({"env_vars": {"$type": "object"}}):
        env_vars = document.get("env_vars") or {}
        if not env_vars:
            continue
        if "_fernet_v1" in env_vars:
            # Fail startup on a wrong key rather than letting the watchdog later
            # restart bots with missing or unreadable credentials.
            decrypt_env_vars(env_vars)
            continue
        # Legacy versions stored these values in plaintext. Migrate them before
        # the watchdog or management handlers are allowed to read bot records.
        encrypted = encrypt_env_vars({str(k): str(v) for k, v in env_vars.items()})
        await _bots.update_one(
            {"_id": document["_id"]},
            {"$set": {"env_vars": encrypted, "updated_at": datetime.now(timezone.utc)}},
        )


async def add_bot(
    name: str,
    repo_url: str,
    env_vars: dict[str, str] | None = None,
    notify_channel: str | None = None,
    deployed_by: int | None = None,
) -> None:
    """Insert or refresh a bot record; environment secrets are encrypted at rest."""
    now = datetime.now(timezone.utc)
    encrypted_env = encrypt_env_vars(env_vars or {})
    await _bots.update_one(
        {"name": name},
        {
            "$set": {
                "name": name,
                "repo_url": repo_url,
                "env_vars": encrypted_env,
                "notify_channel": notify_channel,
                "deployed_by": int(deployed_by) if deployed_by else None,
                "status": "stopped",
                "pid": None,
                "pid_created_at": None,
                "bot_username": None,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def get_bot(name: str) -> dict[str, Any] | None:
    return _decode_bot(await _bots.find_one({"name": name}))


async def list_bots(limit: int = 1_000, deployed_by: int | None = None) -> list[dict[str, Any]]:
    """Return a bounded result set to avoid unbounded memory growth."""
    if not isinstance(limit, int) or not 1 <= limit <= 10_000:
        raise ValueError("Bot listing limit must be between 1 and 10000.")
    query = {"deployed_by": int(deployed_by)} if deployed_by is not None else {}
    cursor = _bots.find(query).sort("name", 1).limit(limit)
    documents = await cursor.to_list(length=limit)
    return [_decode_bot(document) for document in documents]


async def update_bot_env(name: str, env_vars: dict[str, str]) -> None:
    """Persist encrypted deployment environment values."""
    await _bots.update_one(
        {"name": name},
        {"$set": {
            "env_vars": encrypt_env_vars(env_vars),
            "updated_at": datetime.now(timezone.utc),
        }},
    )


async def add_admin(user_id: int, *, username: str = "", first_name: str = "", added_by: int | None = None) -> None:
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("Admin ID must be a positive Telegram user ID.")
    await _admins.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "username": str(username)[:64],
            "first_name": str(first_name)[:128],
            "added_by": added_by,
            "updated_at": datetime.now(timezone.utc),
        }, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def remove_admin(user_id: int) -> bool:
    result = await _admins.delete_one({"user_id": int(user_id)})
    return bool(result.deleted_count)


async def is_admin(user_id: int | None) -> bool:
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    return await _admins.find_one({"user_id": user_id}, {"_id": 1}) is not None


async def get_admins() -> list[dict[str, Any]]:
    return await _admins.find({}).sort("first_name", 1).to_list(length=500)


async def register_user(user_id: int, *, username: str = "", first_name: str = "") -> None:
    if not isinstance(user_id, int) or user_id <= 0:
        return
    await _users.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "username": str(username)[:64],
            "first_name": str(first_name)[:128],
            "last_seen": datetime.now(timezone.utc),
        }, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def get_registered_users() -> list[dict[str, Any]]:
    return await _users.find({}, {"user_id": 1, "username": 1, "first_name": 1}).to_list(length=20_000)


async def update_status(
    name: str,
    status: str,
    pid: int | None | object = _UNSET,
    pid_created_at: float | None | object = _UNSET,
) -> None:
    """Update process state without accidentally retaining stale PID metadata."""
    if not isinstance(status, str) or not status or len(status) > 32:
        raise ValueError("Invalid bot status.")

    fields: dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc),
    }
    if pid is not _UNSET:
        if pid is not None and (not isinstance(pid, int) or pid <= 0):
            raise ValueError("PID must be a positive integer or None.")
        fields["pid"] = pid

    if pid_created_at is not _UNSET:
        if pid_created_at is not None and not isinstance(pid_created_at, (int, float)):
            raise ValueError("Process creation time must be numeric or None.")
        fields["pid_created_at"] = float(pid_created_at) if pid_created_at is not None else None

    await _bots.update_one({"name": name}, {"$set": fields})


async def set_bot_username(name: str, username: str) -> None:
    await _bots.update_one(
        {"name": name},
        {"$set": {"bot_username": username, "updated_at": datetime.now(timezone.utc)}},
    )


async def delete_bot(name: str) -> None:
    await _bots.delete_one({"name": name})


async def get_app_settings(namespace: str = "global") -> dict[str, str]:
    """Read a namespaced settings dictionary from the single global document."""
    if not isinstance(namespace, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", namespace):
        raise ValueError("Invalid settings namespace.")
    document = await _settings.find_one({"_id": "global"})
    if not document:
        return dict(DEFAULT_APP_SETTINGS) if namespace == "global" else {}
    if namespace == "global":
        values = document.get("values") or {}
        result = dict(DEFAULT_APP_SETTINGS)
        if isinstance(values, dict):
            result.update({str(k): str(v) for k, v in values.items()})
        return result
    sections = document.get("sections") or {}
    values = sections.get(namespace) if isinstance(sections, dict) else None
    return {str(k): str(v) for k, v in values.items()} if isinstance(values, dict) else {}


async def set_app_setting(namespace: str, key: str | None = None, value: str | None = None) -> None:
    """Persist one setting. Supports set_app_setting(key, value) for global settings."""
    if value is None:
        # Backwards-compatible two-argument form: (key, value) arrives with key in namespace.
        value = key
        key = namespace
        namespace = "global"
    if not isinstance(namespace, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", namespace):
        raise ValueError("Invalid settings namespace.")
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", key):
        raise ValueError("Invalid settings key.")
    if not isinstance(value, str) or len(value) > 3500:
        raise ValueError("Setting value must be text of at most 3500 characters.")
    path = f"values.{key}" if namespace == "global" else f"sections.{namespace}.{key}"
    await _settings.update_one(
        {"_id": "global"},
        {"$set": {path: value, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def get_custom_messages() -> dict[str, str]:
    """Return validated custom message templates stored by the owner."""
    document = await _settings.find_one({"_id": "global"}, {"custom_messages": 1})
    custom = (document or {}).get("custom_messages") or {}
    if not isinstance(custom, dict):
        return {}
    return {
        str(key): value
        for key, value in custom.items()
        if isinstance(key, str) and _MESSAGE_KEY.fullmatch(key)
        and isinstance(value, str) and 1 <= len(value) <= 3500
    }


async def set_custom_message(key: str, value: str) -> None:
    if not _MESSAGE_KEY.fullmatch(key):
        raise ValueError("Invalid message key.")
    if not isinstance(value, str) or not value.strip() or len(value) > 3_500:
        raise ValueError("Message text must contain 1–3500 characters.")
    await _settings.update_one(
        {"_id": "global"},
        {"$set": {f"custom_messages.{key}": value, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def reset_custom_message(key: str) -> None:
    if not _MESSAGE_KEY.fullmatch(key):
        raise ValueError("Invalid message key.")
    await _settings.update_one(
        {"_id": "global"},
        {"$unset": {f"custom_messages.{key}": ""}, "$set": {"updated_at": datetime.now(timezone.utc)}},
    )


async def close_database() -> None:
    """Close the async PyMongo client during graceful controller shutdown."""
    await _client.close()
