# 🧭 Grid A1 Manager Bot — Setup Guide

This guide is for the standalone Python Discord bot in this folder.

## Environment

Copy `.env.example` to `.env` and set:

```dotenv
DISCORD_TOKEN=replace_with_the_bot_token
PREFIX=!
TEST_GUILD_ID=123456789012345678
OWNER_ID=123456789012345678
# DATABASE_PATH=manager.sqlite3
# LOG_LEVEL=INFO
```

`OWNER_ID` is required for `/sync` and must be the Discord user ID of the bot owner. If it is blank or invalid, `/sync` returns a configuration error; it does not fall back to server permissions. `TEST_GUILD_ID` is optional and controls the fast test-guild sync.

## Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Use `py -3` and the PowerShell activation command on Windows. Invite with both `bot` and `applications.commands` scopes. Grant the bot View Channel, Send Messages, Embed Links, Attach Files, Read Message History, and the ticket permissions required by your existing setup.

## Commands

| Command | Access | Purpose |
|---|---|---|
| `/help` | Everyone | Clean Grid A1 command guide |
| `/rules` | Everyone | Generic placeholder/default rules; edit the list in `grid_a1/commands.py` |
| `/ping` | Everyone | Gateway latency |
| `/embed title description image` | Everyone | Post an embed; optional image must have image content type and `.jpg`, `.jpeg`, `.png`, `.gif`, or `.webp` extension |
| `/embed-edit message_id title description image` | Manage Messages | Edit an existing bot-authored embed in the current channel |
| `/sync` | `OWNER_ID` only | Sync test guild when `TEST_GUILD_ID` exists and global commands |

`/embed-edit` reports clear errors for invalid IDs, missing messages, non-embed messages, messages not authored by this bot, missing access, invalid images, or insufficient permissions. Existing ticket/setup/welcomer commands remain in the bot architecture.

No live Discord runtime test is claimed by this documentation.
