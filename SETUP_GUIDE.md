# Grid A1 setup guide

## Optional staff notifications

Grid A1 can notify staff when a ticket opens. This is disabled until an administrator adds roles.

```text
/setup staff add @Role
/setup staff remove @Role
```

- Maximum: 10 roles per server.
- Members matching more than one role receive one DM only.
- The ticket owner and bot accounts are skipped.
- A closed DM does not stop the ticket from being created.
- No roles configured means no staff notifications.

## Core commands

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
/verifypanel channel role
```

Keep the bot token, `.env`, SQLite database, transcripts, and logs private. Test role hierarchy and DMs in a test server before production.
