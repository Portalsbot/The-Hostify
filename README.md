# Hostify 2

Hostify is a private Telegram repository-hosting controller. It clones GitHub repositories and runs their projects **on the same machine where Hostify is running**. It does not create separate Render services and does not use the Render API.

## Hostify deployment

Deploy this repository as a native Python 3.13 Web Service on Render:

- Build command: `pip install -r requirements.txt`
- Start command: `python main.py`
- Health check path: `/health`

Hostify exposes a small HTTP health endpoint and runs the Telegram controller in the same service.

## How /deploy works

1. Send `/deploy`.
2. Select **Python** or **Docker**.
3. Send the GitHub repository URL.
4. Choose a deployment name.
5. Send environment variables as `KEY=VALUE`, or send `skip`.
6. Hostify clones, builds and starts the repository on its own host.

### Python projects

- Hostify clones the repository into `BASE_DIR/<name>`.
- Creates a dedicated virtual environment.
- Installs `requirements.txt`, when present.
- Starts the first available entry point: `main.py`, `bot.py` or `app.py`.
- Tracks the process ID and stores the encrypted environment variables and repository URL in MongoDB.

Python project code runs as a child process under the Hostify service account. Only deploy repositories and code you trust.

### Docker projects

- The selected repository must contain a root-level `Dockerfile`.
- Hostify runs `docker build`, then starts a named container with `--restart unless-stopped`.
- Environment variables are written to a private `.hostify.env` file and passed using Docker's `--env-file` option.
- Each container receives a named Docker volume mounted at `/data`; configure the project to store persistent runtime files there. Data written elsewhere inside the container may be lost when the container is recreated.
- Run, Stop, Restart, Delete and Logs operate on that Docker container. Delete removes the container, image and its named data volume.

**Docker requires both the Docker CLI and a reachable Docker Engine that the Hostify process is permitted to control.** A native Render Python Web Service does not provide a Docker daemon or privileged container access. Therefore Python deployments can run directly on the current native Render service, but Docker deployments will only work when Hostify is running on a Docker-enabled host. Installing the Docker CLI alone does not provide the Engine.

Do not expose an unauthenticated Docker TCP port. Docker daemon access is highly privileged.

## Storage and redeploy recovery

MongoDB stores deployment metadata, repository URLs, encrypted environment variables, user/admin records and settings.

Render Free services have an ephemeral filesystem. When a redeploy removes a Python project's local folder, Hostify can re-clone the saved GitHub repository, recreate its virtual environment and reinstall dependencies when the watchdog or an operator starts that deployment.

This recovery restores files committed to GitHub. It cannot restore files generated only at runtime and never committed or backed up. Store important runtime data in MongoDB or another external storage destination. A local folder is not permanent storage on Render Free.

Docker container data and images belong to the Docker Engine host. They are not stored in MongoDB.

## Required environment

- `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`
- `MONGO_URI`
- `DATA_ENCRYPTION_KEY` — Fernet key used to encrypt stored deployment environment variables.
- `GITHUB_APP_ID`
- `GITHUB_APP_SLUG`
- `GITHUB_APP_PRIVATE_KEY_B64` — Base64-encoded contents of the GitHub App `.pem` private-key file.
- `ALLOW_UNSANDBOXED_DEPLOYMENTS=true` — required to enable trusted local deployments.

Optional paths:

- `BASE_DIR` — local project directories; defaults to `~/hostify_bots`.
- `SESSION_DIR` — Telegram session directory; defaults to `./data`.
- `PORT` — health endpoint port; defaults to `10000`.
- `DOCKER_HOST` — optional Docker Engine endpoint, only when using a properly secured and reachable Engine.

Generate the encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## GitHub App integration

The GitHub App allows Hostify to read repositories selected in its installation, including private repositories.

The App should have Repository permissions:

- Contents: Read-only
- Metadata: Read-only

Install the App on the GitHub account and grant it access to the repositories Hostify may deploy. Use `/github` in the owner account to verify the installation and list accessible repositories. Hostify obtains a short-lived installation token for private repository clones; the token is not placed in the clone URL or saved in MongoDB.

Keep the private key in Render's secret environment configuration. Never commit it or send it through Telegram.

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
| `/deploy` | Deploy a GitHub repository locally as Python or Docker |
| `/cancel` | Cancel an active deployment setup |
| `/list` | View the admin's own deployments |
| `/mylogs` | View the last 40 Hostify controller log lines |
| `/restarme` | Request a controller restart |
| `/stopme` | Request a controller shutdown |

Admins can view and manage only deployments they created. The owner can view and manage every deployment.

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
| `/setbrand FIELD VALUE` | Edit branding fields and links |
| `/messageonly` | Browse editable message templates |
| `/setmsg KEY TEXT` | Edit a message template |
| `/resetmsg KEY` | Restore a default template |
| `/addadmin` | Add an admin by Telegram user ID |
| `/removeadmin` | Select and remove an admin |
| `/github` | Inspect GitHub App connection status |

## Important platform limits

- Local Python deployments share the CPU, RAM and network resources of the single Hostify service. They do not consume additional Render service slots or invoke the Render API.
- Render Free services may sleep and have ephemeral filesystems. They are not guaranteed 24/7 hosts for long-running child processes.
- Docker builds and containers require a Docker Engine on the Hostify host. This capability is not available inside a standard native Render Python service.
- Hostify does not provide a security sandbox for arbitrary Python code. Restrict deployment access to trusted users and repositories.
