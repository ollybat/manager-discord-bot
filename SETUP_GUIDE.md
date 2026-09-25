# Grid A1 setup guide

## Configure the five staff roles

The Discord server owner must run:

`/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...`

The parameter names and order are exactly `owner_role`, `co_owner_role`, `head_admin_role`, `admin_role`, `moderator_role`. The command accepts only five distinct normal roles from the current guild. It rejects @everyone, managed/integration roles, duplicate roles, and roles from another guild. The values are persisted in SQLite.

Only the configured owner and co-owner roles can run `/dashboard`. The server owner, bot owner, head admin, admin, moderator, and other staff do not receive dashboard access unless they also hold one of those two configured roles. All five configured roles are nevertheless included in staff permission checks.

Use `/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...` for the five staff roles. Use `/setup staff` only for optional ticket notification roles.

## Other commands

`/setup tickets panel_channel logs_channel category inactivity_hours`
`/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel`
`/verifypanel channel role`
`/anti-links enabled:true action:delete_warn`

Keep the bot token, database, transcripts, and logs private. Test role hierarchy and permissions in a test server before production.
