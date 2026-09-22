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

`OWNER_ID` is required for `/sync`; it is checked against the invoking Discord user and does not fall back to server permissions. `TEST_GUILD_ID` is optional and controls the fast test-guild sync.

## Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Use `py -3` and the PowerShell activation command on Windows. Invite with both `bot` and `applications.commands` scopes.

## Safe command-sync workflow

Startup deliberately does **not** global-sync commands. This avoids Discord global-command PUT rate limits during restarts.

1. Set `TEST_GUILD_ID` while developing. On boot, only that guild is synced and the log reports the command count.
2. For a production/global update, invoke `/sync` as the configured `OWNER_ID` user. The response reports test-guild and global counts when applicable.
3. `/sync` has an in-memory minimum cooldown and in-progress guard. A cooldown response is expected if it is invoked repeatedly; wait and retry.
4. If Discord reports HTTP 429, wait for the cooldown and retry. Do not restart repeatedly to force a global sync.

Commands are retained; this change only changes when synchronization is requested.

## Optional voice warnings

PyNaCl/davey warnings can be ignored for the bot's current text, ticket, moderation, and setup features. Voice dependencies are only needed if voice support is added or required.

## Commands

| Command | Access | Purpose |
|---|---|---|
| `/help` | Everyone | Grid A1 command guide |
| `/rules` | Everyone | Generic editable rules |
| `/ping` | Everyone | Gateway latency |
| `/embed` | Everyone | Post an embed |
| `/embed-edit` | Manage Messages | Edit a bot-authored embed |
| `/sync` | `OWNER_ID` only | Explicitly sync test guild (if configured) and global commands |

Existing setup, ticket, and welcomer commands remain in the bot architecture. No live Discord runtime test is claimed by this documentation.
