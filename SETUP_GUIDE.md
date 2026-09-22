# 🧭 Grid A1 Manager Bot — Setup Guide

This guide is for the **standalone Python Discord bot** in this folder. Follow it from top to bottom: **Discord Portal → local files → install → configure → invite → configure Discord channels → test → deploy using your own hosting process**.

> 🟦 **Branding:** User-facing bot copy is **Grid A1**. The welcome message also references the existing community name **Avoid EU 5X**. **🇺🇸 NA Coming Soon** is intentional: the current ticket selector offers EU only, and NA counters remain zero until NA support is implemented.

## 📚 Contents

- [1. Prerequisites](#1--prerequisites)
- [2. Discord Developer Portal](#2--discord-developer-portal)
- [3. Install locally](#3--install-locally)
- [4. Configure `.env`](#4--configure-env)
- [5. Invite the bot](#5--invite-the-bot)
- [6. Configure the server](#6--configure-the-server)
- [7. Commands](#7--commands)
- [8. Ticket lifecycle](#8--ticket-lifecycle)
- [9. Transcript workflow](#9--transcript-workflow)
- [10. Troubleshooting](#10--troubleshooting)
- [11. Backups and migrations](#11--backups-and-migrations)
- [12. Security rules](#12--security-rules)
- [13. Deployment checklist](#13--deployment-checklist)

## 1. ✅ Prerequisites

You need:

- Python **3.10 or newer** (the code uses modern type-hint syntax).
- A Discord account with permission to manage a test server.
- A Discord application and bot user.
- A local clone/copy of this `manager-bot/` directory.

The bot stores operational data in a local SQLite file (`manager.sqlite3` by default). No hosting-provider procedure is specified here; after local testing, use the process required by your chosen provider and keep the bot process running.

## 2. 🛠️ Discord Developer Portal

Open **Discord Developer Portal → Applications → your application**.

### Create the bot and token

1. Create an application, or open the existing Grid A1 application.
2. Open **Bot → Reset Token** only when needed, then copy the token once.
3. Never commit or paste the token into source control, screenshots, tickets, or chat.

### Enable intents (exact settings)

Open **Bot → Privileged Gateway Intents** and enable:

- ✅ **Server Members Intent** — required for `on_member_join` welcomes and member lookups.
- ✅ **Message Content Intent** — enabled by this code's intent configuration; keep it enabled if the bot or future ticket tooling needs message content.
- ⬜ **Presence Intent** — not required by the current code.

Save changes. If Discord later rejects an intent, check both this page and the code's `discord.Intents` configuration.

### OAuth2 URL settings

Open **OAuth2 → URL Generator**:

**Scopes**

- ✅ `bot`
- ✅ `applications.commands`

**Bot Permissions**

Select the least privileges needed by this project:

- ✅ View Channels
- ✅ Send Messages
- ✅ Manage Channels (creates/deletes ticket channels and applies ticket overwrites)
- ✅ Read Message History (builds transcripts)
- ✅ Embed Links (panels, embeds, and welcome messages)
- ✅ Attach Files (transcript archive uploads and ticket attachments)

Do not select Administrator just to avoid diagnosing permissions. Copy the generated URL and invite the bot to a test server first.

## 3. 💻 Install locally

Run these from the `manager-bot/` directory.

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python bot.py
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in an elevated/approved environment, or activate with `cmd.exe` using `.venv\Scripts\activate.bat`.

### Linux / macOS shell

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Expected flow: `python bot.py` → `.env` is loaded → SQLite migrations run → Discord login → slash commands sync → Grid A1 bot is ready.

Stop with `Ctrl+C`. Keep the virtual environment activated when running or installing dependencies.

## 4. 🔐 Configure `.env`

Copy `.env.example` to `.env`, then set values:

```dotenv
DISCORD_TOKEN=replace_with_the_bot_token
PREFIX=!
TEST_GUILD_ID=123456789012345678
# Optional:
# DATABASE_PATH=manager.sqlite3
# LOG_LEVEL=INFO
```

- `DISCORD_TOKEN` — required; the bot exits if missing.
- `PREFIX` — retained for compatibility; current functionality is slash-command based.
- `TEST_GUILD_ID` — optional Discord **server/guild ID**, digits only. While set, commands are copied and synced to that guild for fast testing. Leave blank for global sync; global propagation can take time.
- `DATABASE_PATH` — optional SQLite path. Keep it on persistent storage.
- `LOG_LEVEL` — optional logging level such as `INFO`, `WARNING`, or `DEBUG`.

To copy a guild ID: enable Discord Developer Mode → right-click the server → **Copy Server ID**. Do not put quotes around values. Restart the bot after changing `.env`.

## 5. 🔗 Invite the bot

1. Generate the OAuth2 URL with the scopes and permissions above.
2. Open it while signed into an account that can add apps to the test server.
3. Select the server and authorize.
4. Confirm the bot role can see the channels used below.

Flow: **Portal settings → generated URL → test server → verify bot role/channel access**.

## 6. ⚙️ Configure the server

Create or identify these Discord channels/objects before running setup:

- A public **support panel** text channel.
- A private **ticket logs/archive** text channel.
- A **ticket category** where new private ticket channels will be created.
- For welcomes: Welcome, Verify/Server Selector, Links, Bot Commands, and Shop text channels.

As an administrator, run:

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
```

In Discord's slash-command UI, select the channel/category values and choose an inactivity value from **1 to 720 hours**. The current code stores this setting; it does not currently run an inactivity-closing loop.

Then verify the panel appears and run:

```text
/welcomer preview
/welcomer test
```

Test in a non-production server or with a designated test category first.

## 7. 🧾 Commands

| Command | Who | Purpose |
|---|---|---|
| `/setup tickets` | Manage Server | Save ticket channels/category and post the Grid A1 panel |
| `/setup welcomer` | Manage Server | Save welcome/navigation channels |
| `/welcomer preview` | Manage Server | Show a private preview of the welcome embed |
| `/welcomer test` | Manage Server | Send a welcome test to the configured welcome channel |
| `/ticket claim` | Manage Channels | Claim the current ticket |
| `/ticket transfer staff_member` | Manage Channels | Assign the ticket to another staff member |
| `/ticket requestclose reason` | Staff | Mark a ticket `close_requested` |
| `/ticket close reason` | Manage Channels | Render, archive, and delete the current ticket |

## 8. 🎫 Ticket lifecycle

`Support panel` → choose a category → choose **EU** → submit details → private channel is created → staff claims/transfers → staff resolves or requests closure → staff closes → transcript is archived → ticket channel is deleted.

Important behavior:

- One open or close-requested ticket per user per guild is enforced.
- Only EU can currently be selected. NA is clearly labeled **Coming Soon**, not simulated as available.
- Ticket channels use topic metadata to identify ticket ID, owner, issue, and region.
- Staff checks are based on Manage Channels or Manage Server permissions.
- The panel refreshes every 60 seconds and can recreate its message if it was deleted.

## 9. 🧾 Transcript workflow

Close action → bot reads channel history oldest-first → renders accessible HTML → escapes message text and links → includes author, UTC time, attachments, and image previews → posts the HTML file with an archive embed in the logs channel → deletes the ticket channel.

Keep the logs channel private to trusted staff. Discord attachment URLs may have access/expiry behavior; download important records into your approved archive process if long-term retention is required.

## 10. 🧯 Troubleshooting

- **`DISCORD_TOKEN is missing`** → confirm `.env` is in `manager-bot/`, is named exactly `.env`, and contains a valid token; restart.
- **`TEST_GUILD_ID must be an integer`** → use digits only, or leave it blank.
- **Slash commands do not appear** → check `applications.commands`, guild/server access, bot logs, and whether global sync propagation is still pending. Set `TEST_GUILD_ID` for fast test sync and restart.
- **Welcome event does not fire** → enable Server Members Intent in the Portal and confirm the bot role can view/send in the welcome channel.
- **Panel or embeds fail** → grant View Channel, Send Messages, Embed Links, and Read Message History; check channel-specific overrides.
- **Ticket cannot be created** → rerun `/setup tickets`; confirm the category exists and the bot has Manage Channels.
- **Close says logs are missing** → confirm the configured logs channel still exists and is a text channel.
- **Transcript is incomplete** → grant Read Message History and ensure attachments still resolve when archived.
- **Database errors** → stop the bot, back up the SQLite file, inspect disk permissions/path, and restore only a known-good backup.

## 11. 💾 Backups and migrations

The database migration runs automatically at startup. It creates/updates the schema in `manager.sqlite3` (or `DATABASE_PATH`) and records schema version **2**. Do not delete the database casually: it contains guild configuration, ticket state, audit entries, and closed-ticket counts.

Safe backup flow: **stop bot → copy SQLite database (and any `-wal`/`-shm` files if present) → start bot**. For a consistent SQLite backup, use SQLite's backup tooling rather than copying a live, actively-written database. Store backups privately, date them, and test a restore copy periodically. Before future code/schema upgrades: back up → upgrade code → start once to migrate → verify → retain the pre-upgrade backup.

There is no separate migration CLI in this repository.

## 12. 🛡️ Security rules

- Keep `.env`, tokens, SQLite files, transcripts, and backups out of public repositories and shared drives.
- Rotate the Discord token immediately if exposed; update `.env` and restart.
- Use a test guild before production.
- Prefer narrow channel/category permissions over Administrator.
- Restrict the logs channel and transcript backups to trusted staff.
- Keep Python and dependencies updated through a reviewable change process.
- Do not paste user transcripts or tokens into issue trackers or chat.
- Treat Discord attachment links and transcript HTML as sensitive support records.

## 13. 🚀 Deployment checklist

- [ ] Code and docs are from the intended local workspace copy.
- [ ] Python version and `requirements.txt` installation succeed.
- [ ] `.env` is created privately; token is not committed.
- [ ] Developer Portal intents are enabled.
- [ ] OAuth scopes are `bot` + `applications.commands`.
- [ ] Required bot permissions are verified in the test guild.
- [ ] `TEST_GUILD_ID` is set for testing, then reviewed before production.
- [ ] `/setup tickets` and `/setup welcomer` complete successfully.
- [ ] Panel, EU ticket creation, staff claim, close, archive, and deletion are tested.
- [ ] Welcome preview/test is verified.
- [ ] SQLite persistence and private backup procedure are ready.
- [ ] Process restart/log collection behavior is understood for your chosen host.
- [ ] Any hosting-provider-specific steps are supplied by that provider; none are assumed by this guide.

✅ **Ready state:** a test ticket opens privately, a staff member can claim it, closing posts a readable transcript to the private logs channel, and the ticket channel is then removed.
