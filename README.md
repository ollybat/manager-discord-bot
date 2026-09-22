# Luna Manager bot — Premium Ticket & Welcomer System

Standalone Python `discord.py` bot for Rust Console community support. This is a bot, not a website; it does not use RCON or control a game server.

## Welcomer

Configure all channels:

```text
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
```

New members receive a rich Luna welcome embed with their member number, verification/server selector, account linking, bot commands, shop navigation, support workflow, and the main command overview.

Test it safely before relying on joins:

```text
/welcomer preview
/welcomer test
```

- `/welcomer preview` returns an ephemeral preview.
- `/welcomer test` sends a real test message to the configured welcome channel.
- If setup is incomplete, channels are missing, or permissions are unavailable, Luna returns a clear red error embed instead of silently failing.

Required welcome-channel permissions: View Channel, Send Messages, and Embed Links. Enable **Server Members Intent** in the Discord Developer Portal for `on_member_join`.

## Tickets

- `/setup tickets panel_channel logs_channel category inactivity_hours` configures ticket storage and posts the **Support Tickets** panel.
- Users select General, Base, Clan, Shop, Raid, or Bug; choose EU or NA; and complete the support modal.
- Buttons support Claim, Transfer, and Close. Slash commands include `/ticket claim`, `/ticket transfer`, `/ticket requestclose reason`, and `/ticket close reason`.
- Closing creates an HTML transcript with usernames, timestamps, messages, attachment links, and inline images, sends it to logs, and deletes the ticket.
- The support panel displays total, EU, and NA open tickets, response speed, and estimated help time.

## Run

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Never commit `.env` or your Discord bot token.
