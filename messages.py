"""All bot messages live here. Edit text here, not in plugins."""

# --- SIGNATURE IMAGE (Google Drive direct link) ---
BOT_IMAGE_URL = "https://drive.google.com/uc?export=download&id=1d42PoknlnpMfeaT4QjN2rw_c9hwbcEp2"

M01_NON_OWNER = """🤖 My name is @{bot_username}

I am a private hosting bot designed to make bot deployment and management simple, fast, and reliable. You can host multiple bots directly through me with an easy interface, automated updates, logging, and real-time system monitoring.

◈ Owner: @reactiveHCL
◈ Developer: @JustAstha
◈ Language: Python 3
◈ Library: Pyrogram v3
◈ Database: MongoDB
◈ Bot Channel: @Anime_portals
◈ Bot Support: @ReactiveHCL"""

M02_NON_OWNER_ALERT = "Private bot. Access denied."

M03_OWNER_WELCOME = """Welcome to The Hostify.

I am your private hosting controller. I deploy and manage bots from GitHub on your server — nothing more, nothing less.

Use the buttons below to explore."""

M04_HELP = """Available commands:

/deploy — Deploy a new bot from a GitHub repository
/list — Show all deployed bots and their status
/stop <name> — Stop a running bot
/restart <name> — Restart a bot
/delete <name> — Permanently remove a bot
/logs <name> — Show last 20 lines of a bot's log
/status — Server health
/cancel — Abort an ongoing deploy

All commands are owner-only."""

M05_UNKNOWN = """Unknown command.

Send /help to see what I can do."""

M06_ERROR = """Something went wrong.

Details: {error}"""

M07_ASK_REPO = """Step 1 of 3

Send the GitHub repository URL to deploy.
Private repos: include the token in the URL."""

M08_INVALID_URL = """That is not a valid GitHub URL.

Send it again, or /cancel to abort."""

M09_ASK_NAME = """Step 2 of 3

Send a short name for this bot.
Letters, numbers, underscores only. Example: customer_alice"""

M10_INVALID_NAME = """Name is invalid or already in use.

Send a different name, or /cancel to abort."""

M11_ASK_ENV = """Step 3 of 3

Send environment variables as KEY=VALUE, one per line.
Include NOTIFY_CHANNEL=@your_channel to post deploy announcements.

Send "skip" if none."""

M12_DEPLOYING = """Deploying {name}...

Cloning, installing dependencies, starting process."""

M13_DEPLOY_OK = """Deployment successful.

Bot: {name}
PID: {pid}
Status: running

Use /list to see it, /logs {name} to inspect output."""

M14_DEPLOY_FAIL = """Deployment failed for {name}.

Reason: {reason}

Folder kept for inspection. /delete {name} to remove."""

M15_LIST_EMPTY = """No bots deployed yet.

Send /deploy to add your first one."""

M16_LIST_HEADER = """Deployed bots ({count}):"""

M17_LIST_ROW = "• {name} — {status} — PID {pid}"

M18_STOP_OK = """Bot stopped.

{name} is no longer running."""

M19_RESTART_OK = """Bot restarted.

{name} is running again. New PID: {pid}"""

M20_DELETE_CONFIRM = """Delete {name}?

This stops the process and permanently removes its folder and DB entry. Cannot be undone."""

M21_DELETE_CANCELLED = """Delete cancelled. {name} was not touched."""

M22_DELETE_OK = """Bot deleted.

{name} has been removed from the server."""

M23_LOGS_HEADER = """Last {lines} lines for {name}:"""

M24_LOGS_EMPTY = """No log output for {name}."""

M25_NOT_FOUND = """No bot named {name} exists.

Use /list to see what is deployed."""

M26_WATCHDOG_RESTARTED = """Auto-restart triggered.

{name} was dead. Restarted. New PID: {pid}"""

M27_WATCHDOG_FAIL = """Auto-restart failed.

{name} is still down. Reason: {reason}"""

M28_STATUS = """Server status

CPU: {cpu}%
RAM: {ram_used} / {ram_total} MB
Disk: {disk_used} / {disk_total} GB
Uptime: {uptime}
Bots running: {running} / {total}"""

M29_CHANNEL_DEPLOY = """✅ {bot_mention} is running now

◈ Deployed by: {owner_mention}
◈ Repo: {repo_url}
◈ Environment:
{env_lines}"""

M30_LINE_RUNNING = "✅ {bot_mention} is running now"
M30_LINE_RESTARTED = "🔄 {bot_mention} was refreshed now"
M30_LINE_STOPPED = "⏹ {bot_mention} was stopped now"
M30_LINE_DELETED = "🗑 {bot_mention} was deleted now"

M31_DM_RESTARTED = "🔄 {bot_mention} has been successfully restarted"
M31_DM_STOPPED = "⏹ {bot_mention} has been successfully stopped"
M31_DM_DELETED = "🗑 {bot_mention} has been successfully deleted"

M32_WATCHDOG_ALL_OK = """Watchdog check — {time}

✅ All {count} bots are alive."""
M32_WATCHDOG_SOME = """Watchdog check — {time}

🔄 Restarted: {restarted}"""
M32_WATCHDOG_FAIL = """Watchdog check — {time}

❌ Still down: {failed}"""

M33_CANCELLED = "Cancelled."

M34_NAME_TAKEN = "That name is already deployed. Pick another, or /cancel."
