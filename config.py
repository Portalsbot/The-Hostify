import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MONGO_URI = os.getenv("MONGO_URI", "")
BASE_DIR = os.path.expanduser(os.getenv("BASE_DIR", "~/hostify_bots"))

os.makedirs(BASE_DIR, exist_ok=True)
