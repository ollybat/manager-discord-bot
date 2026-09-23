# Grid A1 setup guide

## Private master dashboard

Run `/setuproles` first if role-based owner/co-owner access is desired, then use `/dashboard`. The response is ephemeral. Every select, button, and modal submission rechecks access; only the server owner, configured bot owner (`OWNER_ID`), or configured owner/co-owner roles can use it.

The dashboard includes live status lines for Ticket setup, Staff roles, Permission roles, Verification panel, Welcome system, Moderation settings, Server information, Announcement channels, and Bot status. Use the app drawer for details, Refresh configuration for current SQLite values, and the private buttons for configuration.

Forms accept Discord role/channel/category mentions or IDs and strictly validate that each object belongs to this guild and has the required type. Valid values are persisted to SQLite. Ticket setup and verification configuration do **not** post public messages or deploy panels. To publish a panel intentionally, use `/setup tickets` or `/verifypanel`.

## Optional staff notifications

```text
/setup staff action role
```

Choose `add` or `remove`. Maximum 10 roles; duplicate notifications are deduplicated.

## Core commands

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
/verifypanel channel role
```

If commands are missing, the owner can run `/sync`; global command propagation can take time.

Keep the bot token, `.env`, database, transcripts, and logs private. Test role hierarchy and DMs in a test server before production.
