# Grid A1 Manager Discord Bot

Production-oriented standalone `discord.py 2.x` bot for Grid A1 community support. The bot is intentionally independent of GitHub, RCON, websites, and existing workspace projects.

## Architecture

- `bot.py` — compatibility entrypoint (`python bot.py`).
- `grid_a1/bot.py` — Discord lifecycle, exactly-once command registration in `setup_hook`, periodic panel recovery, and slash commands.
- `grid_a1/config.py` — validated environment settings.
- `grid_a1/database.py` — SQLite repository and versioned migrations for guild config, tickets, audit events, and closure statistics.
- `grid_a1/tickets.py` — ticket service, IDs, duplicate prevention, transcripts, lifecycle operations.
- `grid_a1/views.py` — persistent dropdown, modal, and button interactions.
- `grid_a1/embeds.py` — Grid A1 presentation and live panel metrics.
- `grid_a1/welcomer.py` — welcome configuration, preview/test support, and join handling.
- `grid_a1/utils.py` — topic parsing, safe channel names, permissions, and UTC helpers.

## Setup and run

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate; Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Set `DISCORD_TOKEN`. Optional values are `DATABASE_PATH` (default `manager.sqlite3`), `PREFIX` (default `!`), `TEST_GUILD_ID` for fast test-guild sync, and `LOG_LEVEL`.

The invite must include both `bot` and `applications.commands` scopes. Enable Server Members Intent for welcomes and enable Message Content only if other commands require it.

## Commands

- `/setup tickets panel_channel logs_channel category inactivity_hours`
- `/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel`
- `/welcomer preview` and `/welcomer test`
- `/ticket claim`, `/ticket transfer`, `/ticket requestclose`, `/ticket close`

Ticket intake is EU-only; NA is visibly marked **Coming Soon** and cannot be selected. Ticket IDs are stored in both SQLite and channel metadata. Duplicate open tickets are blocked by an application check and a partial unique SQLite index. Channels are private, names are sanitized, and staff checks are applied to administrative actions.

## Operational behavior

The panel refreshes every 60 seconds. If its stored message is deleted, the bot posts a replacement and stores its new ID. Refreshing never deletes tickets. `inactivity_hours` is persisted as metadata/configuration for future policy; this release deliberately does not auto-delete inactive tickets. Closure archives an HTML transcript, writes ticket status and audit data, sends the archive to the configured logs channel, and then deletes the ticket channel.

SQLite migrations run during `setup_hook`; keep the database file backed up. Discord permissions still need to allow the bot to manage channels, send embeds/files, read history, and view the configured category/log/panel channels.

## Compatibility and scope

Existing setup command names and parameter names are preserved. Run from `manager-bot` so the package import resolves. No runtime validation is claimed by this refactor; validate in the target environment with a test guild and a non-production SQLite file before rollout.
