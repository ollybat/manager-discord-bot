# Grid A1 Manager Discord Bot

## Staff roles and dashboard

The server owner must run exactly:

`/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...`

This command configures the five persistent staff roles in the exact order shown. It is restricted to the Discord server owner only—not the bot owner, administrators, Manage Server users, or other staff. Each role must be a distinct normal role from the current server; @everyone and managed/integration roles are rejected.

All five configured roles count as staff for staff permissions and ticket controls. Only members holding `owner_role` or `co_owner_role` may open `/dashboard`; head admins, admins, moderators, the bot owner, and other staff cannot open it. Dashboard access is rechecked for every interaction.

There is no separate `/setup roles action role` staff-role command. `/setup staff` remains only for optional ticket notification roles, while `/anti-links` configures link protection.

## Other setup

`/setup tickets panel_channel logs_channel category inactivity_hours`
`/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel`
`/verifypanel channel role`

Ticket setup and verification configuration save settings without unexpectedly posting a public panel. Keep tokens, databases, logs, and transcripts private.
