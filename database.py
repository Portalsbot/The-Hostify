from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI

_client = AsyncIOMotorClient(MONGO_URI)
_db = _client.hostify
_bots = _db.deployed_bots


async def add_bot(name, repo_url, env_vars=None, notify_channel=None):
    await _bots.update_one(
        {"name": name},
        {"$set": {
            "name": name,
            "repo_url": repo_url,
            "env_vars": env_vars or {},
            "notify_channel": notify_channel,
            "status": "stopped",
            "pid": None,
            "bot_username": None,
        }},
        upsert=True,
    )


async def get_bot(name):
    return await _bots.find_one({"name": name})


async def list_bots():
    return await _bots.find({}).to_list(length=None)


async def update_status(name, status, pid=None):
    fields = {"status": status}
    if pid is not None:
        fields["pid"] = pid
    await _bots.update_one({"name": name}, {"$set": fields})


async def set_bot_username(name, username):
    await _bots.update_one({"name": name}, {"$set": {"bot_username": username}})


async def delete_bot(name):
    await _bots.delete_one({"name": name})
