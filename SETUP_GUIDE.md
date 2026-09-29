# Grid A1 setup guide

## Configure the five staff roles

The Discord server owner must run:

`/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...`

The parameter names and order are exactly `owner_role`, `co_owner_role`, `head_admin_role`, `admin_role`, `moderator_role`. The command accepts only five distinct normal roles from the current guild. It rejects @everyone, managed/integration roles, duplicate roles, and roles from another guild. The values are persisted in SQLite.

Only the configured owner and co-owner roles can run `/dashboard`. The server owner, bot owner, head admin, admin, moderator, and other staff do not receive dashboard access unless they also hold one of those two configured roles. All five configured roles are nevertheless included in staff permission checks.

Use `/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...` for the five staff roles. Use `/setup roles` to configure the five staff roles; those roles are also pinged for new tickets.

## Other commands

`/setup tickets panel_channel logs_channel category inactivity_hours`
`/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel`
`/verifypanel channel role`
`/anti-links enabled:true action:delete_warn`

## Keep data across Railway restarts and redeploys

The bot stores its SQLite database at `DATABASE_PATH`. For Railway, attach a persistent Volume to the bot service and set the volume mount path to `/data`; set the service variable `DATABASE_PATH=/data/manager.sqlite3`. The application cannot create or attach the Railway Volume from source code. On startup it now warns if Railway reports no volume mount or if the database path is outside the mounted directory. Keep the Volume attached when redeploying; deleting it removes the database.

Keep the bot token, database, transcripts, and logs private. Test role hierarchy and permissions in a test server before production.
