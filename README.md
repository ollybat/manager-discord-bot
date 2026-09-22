# Grid A1 Manager Discord Bot

## Verification panel

Create a persistent verification panel with:

```text
/verifypanel channel role
```

Example:

```text
/verifypanel #verify @Verified
```

Grid A1 posts a simple panel with a ✅ Verify button. Clicking it gives the configured Discord role. It does not verify anything inside the game server.

Requirements:

- The command user needs Manage Server.
- Grid A1 needs Manage Roles.
- The Grid A1 bot role must be above the verification role.
- `@everyone` cannot be used as the verification role.
- The panel and role are stored in SQLite and the button survives restarts.

If the user already has the role, Grid A1 responds without changing anything. Permission and role-hierarchy errors are shown clearly.

## Ticket inactivity policy

Grid A1 checks tickets every five minutes.

- 🟢 Active — recent activity.
- 🟡 Inactive — no messages for the configured inactivity period.
- 🔴 Closing soon — no messages for twice that period.

Red tickets receive one DM with buttons for Keep ticket open, Request another staff member, or Close ticket. The owner has 24 hours to respond. A missing member cannot be DM'd; after the grace period, the ticket is archived and closed. Messages reset the activity timer.

## Main setup

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
```

Keep `.env`, the bot token, SQLite files, logs, and transcripts private.
