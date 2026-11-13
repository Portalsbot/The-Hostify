from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "8043083450"))
MONGO_URI = os.getenv("MONGO_URI", "").strip()
BASE_DIR = str(Path(os.path.expanduser(os.getenv("BASE_DIR", "~/hostify_bots"))).resolve())
SESSION_DIR = Path(os.path.expanduser(os.getenv("SESSION_DIR", "./data"))).resolve()
PORT = int(os.getenv("PORT", "10000"))

# GitHub App credentials. The private key is supplied as Base64 PEM in Render.
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "").strip()
GITHUB_APP_SLUG = os.getenv("GITHUB_APP_SLUG", "the-hostify").strip()
GITHUB_APP_PRIVATE_KEY_B64 = os.getenv("GITHUB_APP_PRIVATE_KEY_B64", "").strip()
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "").strip()
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "").strip()
RENDER_WORKSPACE_ID = os.getenv("RENDER_WORKSPACE_ID", "").strip()
RENDER_REGION = os.getenv("RENDER_REGION", "singapore").strip().lower()

ALLOW_UNSANDBOXED_DEPLOYMENTS = (
    os.getenv("ALLOW_UNSANDBOXED_DEPLOYMENTS", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is required; refusing to start without a database URI.")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required.")
if not API_ID or not API_HASH:
    raise RuntimeError("API_ID and API_HASH are required.")
if OWNER_ID <= 0:
    raise RuntimeError("OWNER_ID must be a positive Telegram user ID.")
