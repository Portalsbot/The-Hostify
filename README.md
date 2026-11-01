# The Hostify

Private Telegram bot hosting controller. Deploys and manages customer bots from GitHub.

## Setup

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in values
3. `python main.py`

## Usage

Owner sends `/deploy` to the bot and follows 3-step prompts.

## Commands

- `/start` — main menu
- `/help` — command list
- `/deploy` — deploy a bot
- `/list` — list all deployed bots
- `/stop <name>` — stop a bot
- `/restart <name>` — restart a bot
- `/delete <name>` — delete a bot
- `/logs <name>` — last 20 log lines
- `/status` — server health
- `/cancel` — abort deploy flow
