# Grid A1 Manager Discord Bot

## Optional staff ticket notifications

Grid A1 can DM staff when a new ticket is opened. This is optional and supports up to 10 Discord roles.

Configure a role:

```text
/setup staff add @Moderators
```

Remove a role:

```text
/setup staff remove @Moderators
```

When a ticket is created, Grid A1 finds members with any configured role and sends each person one DM. Members who match multiple configured roles are still notified only once. The ticket owner and bot accounts are skipped. If a member has DMs disabled, ticket creation still succeeds and Grid A1 logs the failed notification.

No roles configured means no staff DMs are sent.

## Main setup

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
/verifypanel channel role
```

## Ticket behavior

New tickets use a private channel, EU-only region selection, staff controls, readable HTML transcripts, and inactivity indicators:

- 🟢 active
- 🟡 inactive
- 🔴 closing soon with owner buttons and a 24-hour grace period

Keep `.env`, the Discord token, SQLite files, logs, and transcripts private.
