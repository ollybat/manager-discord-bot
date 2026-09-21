# Manager Discord Bot — Ticket System

Standalone Python `discord.py` bot, built from scratch for Rust Console community support. This is not a website and does not use RCON or control a game server.

## Ticket commands

- `/ticket setup channel category archive` — saves the category/archive configuration and deploys a private ticket panel with issue buttons.
- `/ticket claim` — locks the ticket to the moderator using it.
- `/ticket transfer staff_member` — grants the ticket to another staff member.
- `/ticket close reason` — creates an HTML transcript with message text and attachment links in the archive channel, then deletes the ticket.

## Run

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Enable **Message Content Intent** and **Server Members Intent** in the Discord Developer Portal. The bot needs Manage Channels, View Channel, Send Messages, Read Message History, Attach Files, and Manage Permissions as appropriate.

Never commit `.env` or your bot token. GitHub publishing is not configured because no repository target has been provided.
