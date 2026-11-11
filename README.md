# The Hostify

The Hostify is a Telegram-controlled Python bot deployment manager. The repository can run directly with Python or as a Docker container.

> Important: the Dockerfile packages the Hostify controller itself. The current customer deployment engine still runs Python projects as child processes; it does not build or manage separate customer Docker containers. GitHub App support can access selected private repositories, but deployment execution remains disabled by default until you explicitly accept the current unsandboxed execution risk.

## Run The Hostify controller in Docker

1. Copy .env.example to .env and fill in API_ID, API_HASH, BOT_TOKEN, OWNER_ID, MONGO_URI and DATA_ENCRYPTION_KEY.
2. Generate the Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

3. Install Docker Engine and Docker Compose, then run:

```bash
docker compose up -d --build
```

4. Inspect the service:

```bash
docker compose logs -f hostify
docker compose ps
```

5. Open http://YOUR_SERVER_IP:10000/health. The endpoint returns status=ok after the Telegram controller connects.

The Compose definition stores the Pyrogram session in a persistent named volume at /data and deployed bot files in a second volume at /var/lib/hostify_bots. The controller runs as a non-root user, with a read-only root filesystem, dropped capabilities, no-new-privileges and a health check.

### Build and run manually

```bash
docker build -t the-hostify:latest .
docker volume create hostify-controller-data
docker volume create hostify-bots-data
docker run -d --name the-hostify --restart unless-stopped \
  --env-file .env -e PORT=10000 -e SESSION_DIR=/data \
  -e BASE_DIR=/var/lib/hostify_bots -p 10000:10000 \
  -v hostify-controller-data:/data \
  -v hostify-bots-data:/var/lib/hostify_bots \
  the-hostify:latest
```

## Run directly with Python

Use Python 3.13 on Linux with Git installed.

```bash
python -m pip install -r requirements.txt
python main.py
```

Set SESSION_DIR=./data and BASE_DIR to a persistent writable directory for a native installation.

## Render deployment

For a Render Python Web Service:

- Build command: pip install -r requirements.txt
- Start command: python main.py
- Health check path: /health

Add the variables from .env.example in Render Environment settings. For GitHub App access, also set GITHUB_APP_ID, GITHUB_APP_SLUG and GITHUB_APP_PRIVATE_KEY_B64. Encode the downloaded GitHub App .pem file as Base64 before placing it in the Render secret value. Never commit the private key or send it through Telegram. Render Free Web Services may sleep and use ephemeral local storage; they are not a guarantee that child bot processes and their files will survive restarts or redeploys.

## Current features

- Telegram owner-only deployment and management commands.
- GitHub App authentication and listing of installed repositories.
- Public GitHub repository cloning and selected private repository cloning through short-lived GitHub App installation tokens.
- Python bot deployment using requirements.txt and an entry point.
- Start, stop, restart, delete, logs, resource status and watchdog recovery.
- Encrypted environment-variable storage using Fernet.
- MongoDB-backed branding and editable message templates.
- HTTP health endpoints at / and /health.
- Dockerfile and Compose packaging for the Hostify controller.

## Telegram commands

| Command | Purpose |
|---|---|
| /start | Welcome message and branding |
| /help | Command reference |
| /deploy | Start the public or GitHub-App-authorised repository deployment flow |
| /cancel | Cancel deployment setup |
| /list | List deployed bots and status |
| /stop NAME | Stop a bot |
| /restart NAME | Restart a bot |
| /delete NAME | Request deletion; confirm with the inline button |
| /logs NAME | Show the latest 20 log lines |
| /status | Show host CPU, RAM, disk and tracked bot counts |
| /ping | Owner-only Telegram update check |
| /github | Check GitHub App connection and list accessible repositories |
| /settings | Show editable branding and message controls |
| /setbrand FIELD VALUE | Change a branding field |
| /messages | List editable message-template keys |
| /setmsg KEY TEXT | Save a custom message in MongoDB |
| /resetmsg KEY | Restore the built-in message for a key |

## Editable branding and messages

The owner can use /setbrand FIELD VALUE to update brand_name, developer_username, developer_id, support_username, support_id, promo_channel_username or promo_channel_id.

Use /messages to list editable message keys. Use /setmsg KEY TEXT to save a custom template and /resetmsg KEY to restore its built-in default. Keep dynamic placeholders such as {name}, {pid}, {developer_id} and {promo_channel_url} when needed. Changes are stored in MongoDB. OWNER_ID remains an environment setting to prevent owner lockout.

## Security and limitations

- The controller Docker image is not a customer-container deployment engine.
- Customer Python processes currently run under the Hostify service account. There is no per-customer container or OS sandbox in this version.
- ALLOW_UNSANDBOXED_DEPLOYMENTS=false is the default. Enabling it is an explicit risk acceptance, not a security boundary.
- Do not deploy untrusted repositories or offer public multi-tenant hosting with this version.
- The deployment flow can use public repositories anonymously or selected private repositories through the installed GitHub App. Installation tokens are short-lived and are not stored in the database or inserted into repository URLs. Never put access tokens in repository URLs.
- Docker packaging does not make Render Free a 24/7 host. Use persistent hosting and storage for continuous operation.
- Capacity depends on the host's actual CPU, RAM, disk, network and provider limits; no fixed bot count is guaranteed.

## Health check

- GET / — service health JSON
- GET /health — same health response
- Status is starting until the Telegram client connects, then ok.