# Hostify 2

Hostify is a native Python 3.13 Telegram controller for deploying and managing approved Python bot projects.

This repository intentionally uses Render's **native Python runtime**. It does not include a controller Dockerfile or Compose file. The customer deployment engine currently creates a Python virtual environment for each deployed project and launches it as a child process.

## Render deployment

Create a **Python 3 Web Service** from this repository.

- Runtime: Python 3.13 (from `.python-version`)
- Build command: `pip install -r requirements.txt`
- Start command: `python main.py`
- Health check path: `/health`

Set the variables from `.env.example` in Render's Environment page. Use persistent storage for the Telegram session and deployed projects if your Render plan supports it. Render Free services may sleep and have ephemeral storage; they are not suitable for guaranteed continuous hosting of child bots.

## Required environment

- `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`
- `MONGO_URI`
- `DATA_ENCRYPTION_KEY` — Fernet key used to encrypt stored deployment environment variables.
- `BASE_DIR` — writable directory for deployed project files.
- `SESSION_DIR` — writable directory for the Telegram session.
- `PORT` — Render web service port.

Generate the encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## GitHub App integration

The GitHub App lets Hostify inspect installations and read selected repositories, including private repositories.

Render variables:

- `GITHUB_APP_ID`
- `GITHUB_APP_SLUG`
- `GITHUB_APP_PRIVATE_KEY_B64` — Base64-encoded contents of the downloaded GitHub App `.pem` file.

Keep the private key in Render's secret environment configuration. Never commit it or send it through Telegram.

The App should have Repository permissions:
- Contents: Read-only
- Metadata: Read-only

Install the App on the GitHub account and grant it access only to repositories Hostify is intended to deploy.

Use `/github` in the owner account to check the connection and list accessible repositories. During deployment, Hostify obtains a short-lived installation token for a selected private repository. The token is not placed in the clone URL or saved in the database.

## Telegram commands

### Public commands

| Command | Purpose |
|---|---|
| `/start` | Open the welcome menu |
| `/command` | View the commands available to your role |
| `/admins` | View the current admin team |
| `/ping` | Check Telegram connectivity |

### Approved admin commands

| Command | Purpose |
|---|---|
| `/deploy` | Deploy a repository |
| `/cancel` | Cancel an active deployment setup |
| `/list` | View the admin's own deployments |
| `/mylogs` | View the last 40 Hostify controller log lines |
| `/restarme` | Request a controller restart |
| `/stopme` | Request a controller shutdown |

Admins can view and manage only deployments they created: they can edit their environment and use Run, Stop, Restart and Delete on those deployments. They cannot access or control another admin's deployments. The owner can view and manage every deployment.

### Owner-only commands

| Command | Purpose |
|---|---|
| `/commands` | Show owner command reference |
| `/list` | View every deployment |
| `/stop NAME` | Stop a deployment |
| `/restart NAME` | Restart a deployment |
| `/delete NAME` | Delete a deployment after confirmation |
| `/logs NAME` | Read deployment logs |
| `/status` | View CPU, RAM, storage and deployment counts |
| `/setting` or `/settings` | Open Hostify settings |
| `/setbrand FIELD VALUE` | Edit welcome-page branding, owner/developer/support links, channel links, language, library and database labels |
| `/messageonly` | Browse editable message templates |
| `/setmsg KEY TEXT` | Edit a message template |
| `/resetmsg KEY` | Restore a default template |
| `/addadmin` | Add an admin by Telegram user ID |
| `/removeadmin` | Select and remove an admin |
| `/github` | Inspect GitHub App connection status |

## Current deployment capability and security

- The controller is Python 3.13 and runs natively on Render.
- Hostify can deploy Python projects with a root-level `requirements.txt` and one of `main.py`, `bot.py` or `app.py`.
- Public repositories can be cloned anonymously. Selected private repositories use short-lived GitHub App installation tokens.
- Each deployment's environment variables are encrypted in MongoDB.
- Each deployment is recorded with the Telegram user ID of the admin who created it.
- Owners can view and manage every deployment. Admins can view, edit environment variables, run, stop, restart and delete only deployments they created; they cannot access another admin's or the owner's deployments.
- `ALLOW_UNSANDBOXED_DEPLOYMENTS=false` is the safe default. Enabling it allows trusted admin-submitted Python code to execute under the Hostify service account. This is **not a sandbox**. Only enable it when every admin and repository is trusted.
- Docker Engine integration for customer projects, Java runtime deployment, and isolated per-project containers are not implemented in this version. The `/docker` command is intentionally not registered.
- Controller shutdown/restart commands rely on the hosting provider's process restart behaviour. Use the Render dashboard to suspend or manually restart the service when required.

## Health endpoint

- `GET /`
- `GET /health`

The endpoint reports `starting` until the Telegram client connects, then `ok`.


## Planned Docker Engine integration (not enabled)

The `/docker` command is intentionally not registered yet. When implemented, it will connect Hostify to a Docker Engine that you control, verify the connection, and manage isolated project containers. It will require a Docker endpoint plus secure TLS client credentials (CA certificate, client certificate and client key) for a remote VPS. Do not expose an unauthenticated Docker TCP port; Docker daemon access is effectively host-root access. The future setup wizard should offer Connect, Test Connection, Runtime Status, Disconnect and Close actions, with the endpoint and certificate instructions shown inside Telegram. Hostify itself will continue to run as a native Python service on Render.
