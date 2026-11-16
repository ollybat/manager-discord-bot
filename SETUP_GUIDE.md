# SpinBack Manager setup guide

## Configure the five staff roles

The Discord server owner must run:

`/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...`

The parameter names and order are exactly `owner_role`, `co_owner_role`, `head_admin_role`, `admin_role`, `moderator_role`. The command accepts only five distinct normal roles from the current guild. It rejects @everyone, managed/integration roles, duplicate roles, and roles from another guild. The values are persisted in SQLite.

Only the configured owner and co-owner roles can run `/dashboard`. The server owner, bot owner, head admin, admin, moderator, and other staff do not receive dashboard access unless they also hold one of those two configured roles. All five configured roles are nevertheless included in staff permission checks.

Use `/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...` for the five staff roles. Use `/setup roles` to configure the five staff roles; those roles are also pinged for new tickets.

## Name the Discord application

The name members see above every message (for example `Spinback | Manager`) is the Discord application's username, set in the Developer Portal under **General Information → App name**. Every panel, embed, footer, and transcript the bot sends already says **SpinBack Manager**; the portal name is the only part code cannot change for you.

## Publish the rules panel

`/rules post channel: #rules` publishes the rules panel — a cyan container headed "SpinBack Rules ✅" with the welcome text, one line per rule set, and a button inside each line (Discord, Game) that opens that set privately. The starter texts cover Discord conduct and in-game conduct, and `/rules edit section: ...` replaces one set with your own wording; the posted panel refreshes straight away, and `/rules preview` shows it privately first. Buttons keep working after a restart. Re-run `/rules post` after updating to replace a panel an older version published, and make sure discord.py is 2.7 or newer (`requirements.txt`). Icons are taken from your server: an emoji named `discord`, `game`, and `spinback` (or `s`) is used automatically when it exists; the optional icon and footer-logo boxes in `/rules edit` accept a pasted emoji like `<:discord:1234567890>` when you want a specific one.

## The support panel

`/setup tickets` posts the public ticket panel: the heading 📩 Spinback - Support, the guidance lines, one bold line per issue type with its description, and a footer reading `Support - <your server name> • <year>`. The **Select your issue type ...** menu below it starts the ticket flow (region, then the short form). The footer mark comes from your server: name an emoji `spinback`, `s`, `support`, or `ticket` and it is used; otherwise the panel shows 🏓.

## Other commands

`/setup tickets panel_channel logs_channel category inactivity_hours`
`/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel`
`/verifypanel channel role`
`/anti-links enabled:true action:delete_warn`

## Keep data across Railway restarts and redeploys

The bot stores its SQLite database at `DATABASE_PATH`. For Railway, attach a persistent Volume to the bot service and set the volume mount path to `/data`; set the service variable `DATABASE_PATH=/data/manager.sqlite3`. The application cannot create or attach the Railway Volume from source code. On startup it now warns if Railway reports no volume mount or if the database path is outside the mounted directory. Keep the Volume attached when redeploying; deleting it removes the database.

## Public domain, health checks, and the status endpoint

The bot talks out to Discord and normally never listens on a port, so if Railway attaches a public domain, the edge has nothing to answer with and shows `502 Application failed to respond`. That does not affect the bot: commands keep working through the outbound gateway connection.

Two clean options:

1. **Remove the public domain** in Railway (service → Settings → Networking → delete the generated domain). A gateway-only bot has nothing to serve, so this is the simplest fix and needs no code change.
2. **Keep the domain and serve a status page.** The bot already includes a small read-only endpoint (`grid_a1/health.py`). It binds the port Railway injects (`PORT`, falling back to 8080) and answers the domain root, `/health`, `/healthz`, and `/status` with a compact JSON document:
   ```json
   {"checked_at": "2026-11-16T15:12:42+00:00", "connected_servers": 1, "gateway_latency_ms": 42, "schema_version": 24, "service": "spinback-manager", "status": "ok", "uptime_seconds": 5400}
   ```
   It exposes no tokens, IDs, guild names, ticket contents, or member data. Unknown paths answer 404, anything other than GET/HEAD answers 405, and a failing status report answers 503 so the bot's own uptime monitor can detect it. `HEALTH_DISABLED=1` turns the endpoint off; `HEALTH_PORT` and `HEALTH_HOST` override the port and bind address. Use this when you want Railway health checks or an external uptime monitor to see a 200.

Keep the bot token, database, transcripts, and logs private. Test role hierarchy and permissions in a test server before production.
