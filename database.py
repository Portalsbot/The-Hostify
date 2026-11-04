from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URI

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is required; refusing to start without a database URI.")

_client = AsyncIOMotorClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10_000,
    connectTimeoutMS=10_000,
)
_db = _client.hostify
_bots = _db.deployed_bots
_UNSET = object()


async def verify_database_connection() -> None:
    """Fail startup early if MongoDB cannot be reached or authenticated."""
    await _client.admin.command("ping")


async def add_bot(
    name: str,
    repo_url: str,
    env_vars: dict[str, str] | None = None,
    notify_channel: str | None = None,
) -> None:
    """Insert or refresh a bot record using a single atomic upsert."""
    now = datetime.now(timezone.utc)
    await _bots.update_one(
        {"name": name},
        {
            "$set": {
                "name": name,
                "repo_url": repo_url,
                "env_vars": env_vars or {},
                "notify_channel": notify_channel,
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
    return await _bots.find_one({"name": name})


async def list_bots(limit: int = 1_000) -> list[dict[str, Any]]:
    """Return a bounded result set to avoid unbounded memory growth."""
    if not isinstance(limit, int) or not 1 <= limit <= 10_000:
        raise ValueError("Bot listing limit must be between 1 and 10000.")
    cursor = _bots.find({}).sort("name", 1).limit(limit)
    return await cursor.to_list(length=limit)


async def update_status(
    name: str,
    status: str,
    pid: int | None | object = _UNSET,
    pid_created_at: float | None | object = _UNSET,
) -> None:
    """Update status and only mutate process identity fields when supplied.

    Passing pid=None and pid_created_at=None explicitly clears stale identity.
    Omitting either argument preserves its stored value.
    """
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
        {
            "$set": {
                "bot_username": username,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


async def delete_bot(name: str) -> None:
    await _bots.delete_one({"name": name})


async def close_database() -> None:
    """Close the Motor client during graceful controller shutdown."""
    _client.close()
