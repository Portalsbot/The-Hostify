# The Hostify

The Hostify is a Telegram-controlled Python bot deployment manager. The controller can clone **public GitHub repositories**, install their `requirements.txt`, start a bot process, store deployment metadata in MongoDB, and manage running processes.

> **Security / hosting limitation:** This version does not run customer code inside Docker or another OS sandbox. Customer processes run on the same host as the controller. Deployment is therefore disabled by default. Do not offer this build as public multi-tenant hosting or deploy repositories you do not personally trust. Enabling `ALLOW_UNSANDBOXED_DEPLOYMENTS=true` is an explicit risk acceptance, not a sandbox.

## Features

- Telegram owner-only deployment and management commands.
- Public GitHub repository deployment (private repositories are not supported).
- Process start, stop, restart, delete, log-tail and resource-status commands.
- Process identity checks to reduce accidental signalling of an unrelated process.
- Periodic watchdog recovery for recorded running bots.
- Optional `NOTIFY_CHANNEL` announcements with Restart / Stop / Delete controls.
- Double-confirmed deletion with an expiring, user/chat/message-bound confirmation token.
- MongoDB-backed branding and message overrides edited from Telegram, using MongoDB’s native asynchronous PyMongo client.
- Small-cap styling on controller messages while preserving Telegram commands, usernames, URLs and environment-variable keys.
- Encrypted environment-variable storage using Fernet.
- HTTP health endpoints at `/` and `/health` for PaaS health checks.

## Setup

Use Python 3.13 on a persistent Linux host. The repository includes `.python-version` so Render uses a compatible, reproducible runtime. The host must have `git` installed. Running many bots requires enough RAM, CPU, disk and process capacity; this software cannot make a free hosting plan support 100+ persistent bot processes.

1. Clone or upload this repository.
2. Install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`, and `MONGO_URI`. On Render, use build command `pip install -r requirements.txt` and start command `python main.py`; add every setting from `.env.example` as a Render environment variable (do not upload the `.env` file).
4. Generate `DATA_ENCRYPTION_KEY` once:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Store this value in your host's secret environment variables. Back it up securely. If it is lost, encrypted customer bot environment values cannot be decrypted.
5. Keep `ALLOW_UNSANDBOXED_DEPLOYMENTS=false` until you understand and accept the shared-host execution risk. To enable deployment of repositories you personally trust, set it to `true` in the service environment and redeploy.
6. Start the controller:

   ```bash
   python main.py
   ```

The health server listens on `0.0.0.0:$PORT` (`10000` by default). MongoDB must be reachable from the host. Use a persistent disk for `BASE_DIR`; ephemeral storage may be lost on redeploy/restart.

## Telegram commands

| Command | Purpose |
|---|---|
| `/start` | Welcome message and branding |
| `/help` | Command reference |
| `/deploy` | Start the public-repository deployment flow |
| `/cancel` | Cancel a deployment setup before process launch |
| `/list` | List deployed bots and status |
| `/stop <name>` | Stop a bot |
| `/restart <name>` | Restart a bot |
| `/delete <name>` | Request deletion; confirm with the inline button |
| `/logs <name>` | Show the latest 20 log lines |
| `/status` | Show host CPU, RAM, disk and tracked bot counts |
| `/ping` | Owner-only Telegram update check |
| `/settings` | Show editable branding and message controls |
| `/setbrand <field> <value>` | Change one branding field |
| `/messages` | List editable message-template keys |
| `/setmsg <KEY> <text>` | Save a custom message in MongoDB |
| `/resetmsg <KEY>` | Restore the built-in message for a key |

### Telegram-editable branding

The default public identity is:

- Developer: `@reactiveHCL` — `8043083450`
- Support: `@Just_Astha` — `7271115259`
- Promotion channel: `@Anime_Portals` — `-1003545564222` (https://t.me/Anime_Portals)

Use `/setbrand <field> <value>` to update `brand_name`, `developer_username`, `developer_id`, `support_username`, `support_id`, `promo_channel_username` or `promo_channel_id`. The numeric controller access ID in `OWNER_ID` is intentionally not editable from Telegram, to avoid locking the owner out.

Use `/messages` to list the available template IDs. For example:

```text
/setmsg M03_OWNER_WELCOME Welcome to {brand_name}. Follow {promo_channel_mention}.
```

Keep placeholders used by a message's dynamic fields (such as `{name}`, `{pid}`, `{developer_id}` or `{promo_channel_url}`) when those values are needed. Message changes are stored in MongoDB and survive controller restarts.

### Channel announcements

To post deployment announcements, include `NOTIFY_CHANNEL=@your_channel` in the deployment environment. The deployed bot/controller must be an administrator with permission to post in that channel. This is separate from the Hostify promotion channel configured under `/settings`.

## Important limitations

- Only public GitHub repositories are supported. Never paste a GitHub token into a URL.
- There is no Docker/container isolation, CPU quota, memory limit or per-customer operating-system account in this build.
- The database layer uses PyMongo Async (`AsyncMongoClient`), not the deprecated Motor driver.
- A customer repository can execute arbitrary code with the controller service account's operating-system permissions. The child process receives a reduced environment, but this is not a security boundary.
- Render/free PaaS plans may not support persistent child processes or persistent storage. A green health check does not guarantee that spawned bots will remain alive.
- “100+ bots for free” cannot be guaranteed. Capacity depends on the host's actual CPU, RAM, disk I/O, network and provider terms.
- Test with one repository you own before exposing the service to others or taking payments.

## Health check

- `GET /` — service health JSON
- `GET /health` — same health response

The endpoint reports `starting` until the Telegram client is connected; it reports `ok` after successful startup.
