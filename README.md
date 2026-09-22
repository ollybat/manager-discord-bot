# 🟦 Grid A1 Manager Bot

A standalone Python `discord.py` bot for Grid A1 community support on Discord.

> **Current support region:** 🇪🇺 EU is available. 🇺🇸 **NA Coming Soon** is intentional and means NA ticket handling has not been enabled yet—it is not simulated as live.

## 🧭 Start here

New operator? Follow the full, copy/paste setup path:

➡️ **[Read SETUP_GUIDE.md](SETUP_GUIDE.md)**

It covers Discord Developer Portal settings, OAuth scopes and permissions, Windows/Linux installation, `.env`, guild setup, commands, ticket and transcript workflows, troubleshooting, backups/migrations, security, and a deployment checklist.

## 📚 Contents

- 🛠️ [Full setup guide](SETUP_GUIDE.md)
- 🗺️ [Project map](PROJECT_MAP.md)
- 🔐 [.env template](.env.example)
- 📦 [Python dependencies](requirements.txt)
- ▶️ [Local entrypoint](bot.py)

## ⚡ Quickstart

### Windows PowerShell

```powershell
cd manager-bot
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and set DISCORD_TOKEN (and optionally TEST_GUILD_ID)
python bot.py
```

### Linux / macOS

```bash
cd manager-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env and set DISCORD_TOKEN (and optionally TEST_GUILD_ID)
python bot.py
```

Startup flow: `.env` → Discord login → SQLite migration → slash-command sync → Grid A1 bot ready ✅

## 🎫 First Discord setup

After inviting the bot with `bot` + `applications.commands` scopes, an administrator runs:

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
```

Then test:

```text
/welcomer preview
/welcomer test
```

For exact Portal toggles, permissions, channel requirements, ticket lifecycle, transcript archiving, and safety rules, use the guide rather than guessing. No hosting provider is assumed here.

## 🏷️ Naming and scope

Technical module filenames under `grid_a1/` are intentionally readable and stable; do not rename them casually because package imports depend on them. User-facing branding is Grid A1 throughout the bot. Workspace documentation only was updated; GitHub has not been changed.

> ℹ️ This documentation update does not claim a live Discord or runtime test. Run the setup checklist in a non-production guild before rollout.
