# Grid A1 — Manager Discord Bot

Standalone Python `discord.py` bot for Rust Console community support. It is a bot, not a website, and does not use RCON or control a game server.

## Grid A1 ticket panel

The panel displays:

```text
Support Tickets
Select a support option, then choose EU before filling your questions.

Support Status
Open Tickets (Total): 0
Open EU Tickets: 0
Open NA Tickets: 0
Response Speed: Fast
Estimated Help Time: 12 mins

Grid A1 • Manager
```

NA is explicitly **Coming Soon** and is not offered as a selectable region. Current tickets are EU only. The panel refreshes every 60 seconds without deleting or resetting tickets. Open counts are calculated from live ticket channels, and closed tickets are recorded in SQLite and shown as a closed-ticket statistic.

## Setup

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
```

`/setup tickets` stores the panel message so Grid A1 can refresh it every minute. If the panel message is deleted, rerun setup to deploy a new one.

## Commands

```text
/welcomer preview
/welcomer test
/ticket claim
/ticket transfer staff_member
/ticket requestclose reason
/ticket close reason
```

Tickets use dropdown categories, EU selection, a modal intake form, private channels, persistent buttons, HTML transcripts, and closure records.

## Slash command visibility

For instant testing, set `TEST_GUILD_ID` in `.env` to the Discord server ID and restart. The bot must be invited with both `bot` and `applications.commands` OAuth scopes. Global commands can take time to propagate.

## Run

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Enable **Server Members Intent** for join welcomes. Never commit `.env` or the Discord bot token.
