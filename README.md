# Luna Manager bot — Ticket & Welcomer System

Standalone Python `discord.py` bot for Rust Console community support. It is a bot, not a website, and does not use RCON or control a game server.

## Important: slash-command registration

Luna registers the commands in `setup_hook` and logs the sync result. Discord global commands can take time to appear. For instant testing, set your server ID in `.env`:

```env
TEST_GUILD_ID=123456789012345678
```

Then restart the bot. The bot must be invited with both OAuth scopes:

```text
bot
applications.commands
```

If commands still do not appear, remove and reinvite the bot with those scopes, confirm the bot is in the server identified by `TEST_GUILD_ID`, and check startup logs for `Test-guild command sync complete` or `Global application-command sync complete`.

## Commands

- `/setup tickets panel_channel logs_channel category inactivity_hours`
- `/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel`
- `/welcomer preview`
- `/welcomer test`
- `/ticket claim`
- `/ticket transfer staff_member`
- `/ticket requestclose reason`
- `/ticket close reason`

The ticket panel supports dropdown categories, EU/NA selection, modal intake, persistent buttons, private channels, claim/transfer/close actions, and HTML transcript archiving.

The welcomer sends a rich branded **Luna • Manager** welcome with member number, channel navigation, support workflow, command overview, and clear test/setup errors.

## Run

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Enable **Server Members Intent** in the Discord Developer Portal for join welcomes. Never commit `.env` or your Discord bot token.
