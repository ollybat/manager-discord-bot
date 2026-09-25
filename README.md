# Grid A1 Manager Discord Bot

## Private master dashboard

Use `/dashboard` to open an ephemeral, owner-level control center. Access is rechecked on every select, button, and modal submission and is limited to the server owner, configured `OWNER_ID`, or members holding both configured owner/co-owner access roles as applicable. Moderators, admins, and head admins do not receive dashboard access automatically.

The polished dashboard is titled `🎛️ Master Dashboard — {server name}` and shows live `🟢 Active`, `🟡 Partial`, or `🔴 Disabled / Not Setup` statuses for Ticket setup, Staff roles, Permission roles, Verification panel, Welcome system, Moderation settings, Server information, Announcement channels, and Bot status. Use the app drawer to inspect modules, Refresh configuration to reload SQLite values, or the private configuration buttons for Permission roles, Staff roles, Ticket setup, Welcome system, and Verification panel.

Dashboard forms validate role/channel/category IDs and mentions against the current guild and persist valid settings through SQLite. Ticket setup and verification configuration intentionally save settings only: they never post a public panel unexpectedly. Use `/setup tickets` or `/verifypanel` explicitly when a public panel should be deployed.

## Main setup

```text
/setup roles add @LinkSenders
/setup roles remove @LinkSenders
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
/verifypanel channel role
```

## Ticket behavior

New tickets use a private channel, EU-only region selection, staff controls, readable HTML transcripts, and inactivity indicators. Keep `.env`, the Discord token, SQLite files, logs, and transcripts private.

## Optional staff ticket notifications

```text
/setup staff add @Moderators
/setup staff remove @Moderators
```

Up to 10 roles may receive one DM per new ticket; duplicate role matches are deduplicated.

## Anti-links protection

Admins can run `/anti-links enabled:true` to persistently block websites, bare domains, Discord invites, and common Unicode/spacing obfuscations. Choose `delete`, `delete_warn`, or `delete_log`; the last option records the deleted message metadata in the configured TextChannel without copying message content or secrets. Whitelist domains and bypass roles accept comma-separated domains and role IDs/mentions. When any allowed link role is configured, only members with one of those roles may send links; owner, bot owner, administrator, Manage Messages, and bypass-role exceptions remain unchanged. The bot needs **Manage Messages**. Bot messages, DMs, owners, administrators, and Manage Messages members are ignored. Edited messages are scanned too.
