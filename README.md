# Luna Manager bot — Premium Ticket System

Standalone Python `discord.py` bot for Rust Console community support. This is a bot, not a website; it does not use RCON or control a game server.

## Ticket UX

- `/setup tickets panel_channel logs_channel category inactivity_hours` configures ticket storage and posts the **Support Tickets** panel.
- The panel includes live open-ticket fields: **Open Tickets (Total)**, **Open EU Tickets**, **Open NA Tickets**, **Response Speed**, and **Estimated Help Time: 12 mins**, with the footer `Luna • Manager bot`.
- Dropdown menu offers general, base, clan, shop, raid, and bug support options.
- Users choose **EU or NA before the support modal opens**, then submit their questions.
- Private channels enforce one open ticket per user and preserve claim, transfer, and close controls.
- `/ticket requestclose reason` requests closure with a required reason; `/ticket close` remains available for staff.
- Closing creates an HTML transcript with usernames, timestamps, messages, attachment links, and inline images, sends it to the configured logs channel, and deletes the ticket.
- Persistent buttons and dropdowns survive bot restarts.

## Welcomer setup

`/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel` stores the channels used by the Luna Manager community setup. New members receive the branded **🔷 Welcome to Avoid EU 5X** embed with configured channel mentions, and `/welcomer preview` provides an ephemeral test preview. Existing databases migrate automatically with the new `verify_channel` column.

## Run

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Enable Message Content Intent and Server Members Intent in Discord Developer Portal. The bot needs View Channel, Send Messages, Read Message History, Manage Channels, Manage Permissions, Attach Files, and Embed Links as appropriate.

Never commit `.env` or your bot token.
