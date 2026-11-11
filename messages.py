"""Default, Telegram-editable Hostify message templates.

Edit these defaults in code or change live copies with /setmsg in Telegram.
Template placeholders such as {name}, {pid} and {developer_id} must be kept
when a replacement message still needs those dynamic values.
"""

BOT_IMAGE_URL = "https://drive.google.com/uc?export=download&id=1d42PoknlnpMfeaT4QjN2rw_c9hwbcEp2"

M01_PUBLIC_WELCOME = """Welcome to {brand_name}.

A Telegram bot hosting controller for deploying and managing Python bots from GitHub.

📣 Updates: {promo_channel_mention}
🔗 Channel: {promo_channel_url} (ID: {promo_channel_id})
🛠 Developer: {developer_mention} (ID: {developer_id})
💬 Owner / Support: {support_mention} (ID: {support_id})"""
M02_NON_OWNER_ALERT = """This is a private {brand_name} controller. Access denied.

Follow updates: {promo_channel_mention}
Channel: {promo_channel_url} (ID: {promo_channel_id})
Developer: {developer_mention} | ID: {developer_id}
Support: {support_mention} (ID: {support_id})"""
M03_OWNER_WELCOME = """Welcome to {brand_name}.

Your private Telegram bot hosting controller is online.

📣 Updates: {promo_channel_mention}
🔗 Channel: {promo_channel_url} (ID: {promo_channel_id})
🛠 Developer: {developer_mention} (ID: {developer_id})
💬 Owner / Support: {support_mention} (ID: {support_id})

Send /help to view commands. Use /settings to edit branding and messages."""
M04_HELP = """Available commands:

/deploy — Deploy a public GitHub repository
/list — List deployed bots
/stop <name> — Stop a bot
/restart <name> — Restart a bot
/delete <name> — Delete a bot (second confirmation required)
/logs <name> — View the last 20 log lines
/status — View controller resource status
/cancel — Cancel an active deployment setup
/ping — Check controller connectivity
/settings — View editable branding and settings
/setbrand <field> <value> — Edit usernames, IDs or display name
/messages — List editable message templates
/setmsg <KEY> <text> — Edit a message from Telegram
/resetmsg <KEY> — Restore a default message

All controller commands are owner-only. Private GitHub repositories are not supported.

📣 Updates: {promo_channel_mention} | {promo_channel_url} (ID: {promo_channel_id})
🛠 Developer: {developer_mention} (ID: {developer_id})\n💬 Support: {support_mention} (ID: {support_id})"""
M07_ASK_REPO = """Step 1 of 3

Send a public GitHub repository URL to deploy.
Private repositories are not supported. Never include access tokens in a URL."""
M08_INVALID_URL = "That is not a valid public GitHub repository URL. Send it again, or /cancel to abort."
M09_ASK_NAME = "Step 2 of 3\n\nSend a short bot name. Use 1-32 letters, numbers or underscores. Example: customer_alice"
M10_INVALID_NAME = "That name is invalid or already in use. Send a different name, or /cancel to abort."
M11_ASK_ENV = """Step 3 of 3

Send environment variables as KEY=VALUE, one per line.
Send skip if none. Never send Hostify controller credentials.

Optional: NOTIFY_CHANNEL=@your_channel (the bot must be an admin there)."""
M12_DEPLOYING = "Deploying {name}…\n\nCloning the repository, installing dependencies and starting the process."
M13_DEPLOY_OK = "Deployment successful.\n\nBot: {name}\nPID: {pid}\nStatus: running\n\nUse /list or /logs {name} to manage it."
M14_DEPLOY_FAIL = "Deployment failed for {name}.\n\nReason: {reason}\n\nInspect the server deployment log before retrying."
M15_LIST_EMPTY = "No bots are deployed yet. Send /deploy to add your first bot."
M16_LIST_HEADER = "Deployed bots ({count}):"
M17_LIST_ROW = "• {name} — {status} — PID {pid}"
M18_STOP_OK = "Bot stopped.\n\n{name} is no longer running."
M19_RESTART_OK = "Bot restarted.\n\n{name} is running again. New PID: {pid}"
M20_DELETE_CONFIRM = "Delete {name}?\n\nThis stops the process and permanently removes its folder and database entry. This cannot be undone."
M21_DELETE_CANCELLED = "Deletion cancelled. {name} was not changed."
M22_DELETE_OK = "Bot deleted.\n\n{name} has been removed from the server."
M23_LOGS_HEADER = "Last {lines} lines for {name}:"
M24_LOGS_EMPTY = "No log output is available for {name}."
M25_NOT_FOUND = "No bot named {name} exists. Use /list to see deployed bots."
M28_STATUS = """Server status

CPU: {cpu}%
RAM: {ram_used} / {ram_total} MB
Disk: {disk_used} / {disk_total} GB
Uptime: {uptime}
Bots marked running: {running} / {total}"""
M29_CHANNEL_DEPLOY = """✅ {bot_mention} is running now

◈ Deployed by: {owner_mention}
◈ Developer: {developer_mention} (ID: {developer_id})
◈ Support: {support_mention} (ID: {support_id})
◈ Repository: {repo_url}
◈ Environment:
{env_lines}

Powered by {brand_name}
📣 Follow: {promo_channel_mention} — {promo_channel_url} (ID: {promo_channel_id})"""
M30_LINE_RESTARTED = "🔄 {bot_mention} was restarted"
M30_LINE_STOPPED = "⏹ {bot_mention} was stopped"
M30_LINE_DELETED = "🗑 {bot_mention} was deleted"
M31_DM_RESTARTED = "🔄 {bot_mention} has been restarted successfully."
M31_DM_STOPPED = "⏹ {bot_mention} has been stopped successfully."
M31_DM_DELETED = "🗑 {bot_mention} has been deleted successfully."
M32_WATCHDOG_RESTARTED = "Watchdog check — {time}\n\n🔄 Restarted: {restarted}"
M32_WATCHDOG_FAIL = "Watchdog check — {time}\n\n❌ Still down: {failed}"
M33_CANCELLED = "Cancelled."
M34_NAME_TAKEN = "That name is already deployed. Pick another name, or /cancel."
M35_SETTINGS = """{brand_name} settings

Editable branding fields:
brand_name, developer_username, developer_id, support_username, support_id, promo_channel_username, promo_channel_id

Commands:
/setbrand <field> <value>
/messages — List editable message template keys
/setmsg <KEY> <new text>
/resetmsg <KEY> — Restore the built-in default

Current channel: {promo_channel_mention} ({promo_channel_id})
Developer: {developer_mention} ({developer_id})
Support: {support_mention} ({support_id})

The controller access OWNER_ID remains fixed in environment settings for security."""
M36_BRAND_UPDATED = "Updated {field} to {value}."
M37_MESSAGE_UPDATED = "Message template {key} has been updated."
M38_MESSAGE_RESET = "Message template {key} has been restored to its default."
M39_SETTINGS_USAGE = "Usage: /setbrand <field> <value>. Send /settings to see editable fields."
M40_MESSAGE_USAGE = "Usage: /setmsg <KEY> <new text>. Use /messages to see valid keys."
M41_MESSAGE_KEY_INVALID = "Unknown message key. Use /messages to see valid keys."
M42_BRAND_VALUE_INVALID = "That branding value is invalid: {reason}"
M43_MESSAGES_HEADER = "Editable message templates ({count}):"
M44_PING = "Pong. The Hostify controller is receiving Telegram updates."
M46_OPERATION_FAILED = "The operation failed: {reason}"
M48_USAGE_STOP = "Usage: /stop <bot_name>. Use /list to see valid names."
M49_USAGE_RESTART = "Usage: /restart <bot_name>. Use /list to see valid names."
M50_USAGE_DELETE = "Usage: /delete <bot_name>. Deletion requires a second confirmation."
M51_DELETE_PREP_FAILED = "Could not prepare deletion. No deletion was performed."
M58_GENERIC_ERROR = "The controller could not complete this request. Check the controller logs."
M64_DELETE_CANCELLED = "Deletion cancelled."
M66_ANNOUNCEMENT_UNAVAILABLE = "Announcement message is unavailable."
M67_ANNOUNCEMENT_UNVERIFIED = "This control is not attached to the registered notification channel."
M68_OPERATION_FAILED = "Operation failed. Check controller logs."
M69_CONFIRM_EXPIRED_CHANNEL = "Confirmation expired. Use the channel controls again."
M70_CONFIRM_WRONG_OWNER = "This confirmation does not belong to you."
M71_RECORD_INVALID = "The deployment record or channel is no longer valid."
M72_DELETE_FAILED = "Deletion failed. Check controller logs."
M73_UNKNOWN_CONTROL = "Unknown or expired Hostify control."
M74_NO_DEPLOY_ACCESS = "Deployments are disabled by default. Set ALLOW_UNSANDBOXED_DEPLOYMENTS=true only for repositories you personally trust."
M75_DEPLOY_TRUST_WARNING = "Security warning: this controller executes customer code on the same host without container sandboxing. Do not use it for untrusted public customer repositories."
M76_UNKNOWN_COMMAND = "Unknown command. Send /help to view the available commands."


M77_START_ERROR = "Hostify could not process this request."
M78_HELP_ERROR = "Unable to display the command list right now."
M79_LIST_ERROR = "Unable to load deployed bots. Check controller logs."
M80_STOP_FAILED = "Could not stop {name}: {reason}"
M81_STOP_ERROR = "Stop failed for {name}. Check controller logs."
M82_RESTART_FAILED = "Could not restart {name}: {reason}"
M83_RESTART_ERROR = "Restart failed for {name}. Check controller logs."
M84_CALLBACK_ACCESS_DENIED = "Access denied."
M85_CONFIRM_INVALID = "Invalid confirmation."
M86_CONFIRM_EXPIRED = "Confirmation expired. Run /delete again."
M87_CONFIRM_WRONG_USER = "This confirmation does not belong to this user."
M88_DELETE_ACCEPTED = "Deletion request accepted."
M89_DELETE_FAILED = "Deletion failed for {name}. Check controller logs."
M90_LOGS_USAGE = "Usage: /logs <bot_name>\nUse /list to see valid names."
M91_LOGS_READ_ERROR = "Could not read logs for {name}. Check controller logs."
M92_STATUS_ERROR = "Unable to collect server status. Check controller logs."
M93_CHANNEL_CONFIRM = "Confirm deletion within 60 seconds."
M94_CHANNEL_OPERATION_FAILED = "Operation failed: {reason}"
M95_CHANNEL_OPERATION_COMPLETED = "Operation completed."
M96_CHANNEL_DELETE_FAILED = "Deletion failed: {reason}"
M97_CHANNEL_BOT_DELETED = "Bot deleted."
M98_DEPLOY_ALREADY_RUNNING = "A deployment is already running. Please wait for its result."
M99_DEPLOY_SESSION_ACTIVE = "A deployment session is already active. Send /cancel first or continue the current step."
M100_CANCEL_NO_SESSION = "There is no active deployment session to cancel."
M101_CANCEL_LOCKED = "Deployment has already started and cannot be cancelled safely at this stage. Wait for the result."
M102_SESSION_INVALID = "Deployment session state was invalid. Start again with /deploy."
M103_ENV_INVALID = "Invalid environment variables: {reason}\n\nSend them again, or /cancel."
M104_SESSION_INCOMPLETE = "Deployment session was incomplete. Start again with /deploy."
M105_DEPLOY_UNEXPECTED_FAILURE = "Deployment failed for {name}. Check controller logs."
M106_BTN_CONFIRM_DELETE = "Confirm deletion"
M107_BTN_CANCEL = "Cancel"
M108_BTN_RESTART = "Restart"
M109_BTN_STOP = "Stop"
M110_BTN_DELETE = "Delete"
M111_DELETE_OPERATION_FAILED = "Could not delete {name}: {reason}"

M112_START_DESCRIPTION = "I am a private hosting bot designed to make bot deployment and management simple, fast, and reliable. You can host multiple bots directly through me with an easy interface, automated updates, logging, and real-time system monitoring."
M113_ABOUT_MESSAGE = "<blockquote>WELCOME TO THE HOSTIFY.</blockquote>\\n\\n<blockquote>YOUR PRIVATE TELEGRAM BOT HOSTING CONTROLLER IS ONLINE.</blockquote>"
M114_HELP_MESSAGE = "<blockquote>If you need help, contact admin <a href='https://t.me/Just_astha'>Astha</a>.</blockquote>"
M115_CHANNEL_MESSAGE = "<blockquote>╭───────────────⍟\\n├➽ DEVELOPER: <a href='https://t.me/Just_astha'>JUST ASTHA</a>\\n├➽ OWNER: <a href='https://t.me/reactivehcl'>HCL</a>\\n├➽ ANIME INDEX: <a href='https://t.me/master_index'>MASTER INDEX</a>\\n├➽ FINISHED ANIME: <a href='https://t.me/Anime_portals'>ANIME PORTALS</a>\\n├➽ ONGOING ANIME: <a href='https://t.me/Ongoing_portals'>ONGOING PORTALS</a>\\n├➽ MANHWA / MANGA: <a href='https://t.me/manga_portals'>MANGA PORTALS</a>\\n├➽ NETWORK: <a href='https://t.me/Anime_gateway'>ANIME GATEWAY</a>\\n├➽ REQ / CHAT: <a href='https://t.me/+lB05vD5bGyszMzg1'>CHATTING HUB</a>\\n╰───────────────⍟</blockquote>"
M116_DEPLOY_CANCELLED = "Deployment cancelled successfully!"
M117_ADMIN_ADDED = "You are now also my admin!"
M118_ADMIN_REMOVED = "Your Hostify administrator access has been removed."
M119_STOP_SHORT = "Bot stopped successfully: {name}."
M120_RESTART_SHORT = "Bot restarted successfully: {name}."
M121_DELETE_SHORT = "Deployment deleted successfully: {name}."
M122_CONTROLLER_STOPPED = "Hostify has been stopped by an administrator."
M123_CONTROLLER_RESTARTED = "Hostify is back online."
M124_ENV_UPDATED = "Environment updated for {name}. A restart is required before the running process receives the new values."
M125_SETTINGS_SAVED = "Settings updated successfully."

# Build this only after every template has been declared, so Telegram controls
# can edit every message key rather than a partial subset.
DEFAULT_MESSAGES = {
    name: value
    for name, value in tuple(globals().items())
    if name.startswith("M") and name[1:2].isdigit() and isinstance(value, str)
}


def apply_overrides(overrides: dict[str, object]) -> None:
    for key, value in overrides.items():
        if key in DEFAULT_MESSAGES and isinstance(value, str) and 1 <= len(value) <= 3500:
            globals()[key] = value


def reset_default(key: str) -> str:
    if key not in DEFAULT_MESSAGES:
        raise KeyError(key)
    globals()[key] = DEFAULT_MESSAGES[key]
    return DEFAULT_MESSAGES[key]
