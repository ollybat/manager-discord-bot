# Manager Discord Bot — Premium Ticket System

Standalone Python `discord.py` bot for Rust Console community support. Built from scratch; it is not a website and does not use RCON or control a game server.

## Ticket UX

- `/ticket setup channel category archive` deploys a polished support embed.
- Dropdown menu with 🚩 Player Report, 🛠️ Server Support, ⚖️ Ban Appeal, and 🎫 Other.
- Modal form asks for a detailed initial report before creating a ticket.
- Private channels with emojis and readable issue names.
- One-open-ticket limit per user.
- Interactive buttons for 🙋 Claim, 🔁 Transfer, and 🔒 Close.
- Staff-only claim and transfer controls.
- Transfer modal accepts a staff member ID.
- Close modal requires a reason.
- HTML transcript includes usernames, timestamps, messages, attachment links, and inline images when available.
- Transcript is sent to a hidden archive channel before deletion.
- Persistent buttons and dropdowns survive bot restarts.

## Run

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Enable Message Content Intent and Server Members Intent in Discord Developer Portal. The bot needs View Channel, Send Messages, Read Message History, Manage Channels, Manage Permissions, Attach Files, and Embed Links as appropriate.

Never commit `.env` or your bot token.
