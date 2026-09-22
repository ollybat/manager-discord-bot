# 🟦 Grid A1 Manager Bot

Standalone Python `discord.py` bot for Grid A1 community support on Discord.

See **[SETUP_GUIDE.md](SETUP_GUIDE.md)** for installation and command-sync workflow.

## Command sync safety

- On startup, **no global application-command sync is performed**.
- If `TEST_GUILD_ID` is set, startup syncs only that guild and logs the synced command count for fast testing.
- Global sync is explicit through owner-only `/sync`, gated by `OWNER_ID` and protected by an in-memory cooldown/duplicate-request guard. Restarting the process resets that guard.
- If Discord returns HTTP 429, wait for the cooldown and retry; this is a Discord API rate limit, not a missing command. Commands are not removed by this change.

Optional PyNaCl/davey voice-library warnings are harmless for this bot's text, tickets, moderation, and setup features. Install voice dependencies only if voice functionality is added or required.

No live Discord runtime test is claimed by this documentation.
