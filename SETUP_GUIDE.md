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

`/setup tickets` posts the public ticket panel: the heading 📩 Spinback - Support, the guidance lines, one bold line per issue type with its description, and a footer reading `Support - <your server name> • <year>`. The **Select your issue type ...** menu below it starts the ticket flow: pick the region (🇪🇺 EU or 🇺🇸 NA), then answer the short form. Ticket channels carry the region in their name, for example `🔵-eu-general-olly-a1b2c3d4`. The panel's main picture is your server logo (see below), and the footer mark comes from your server: name an emoji `spinback`, `s`, `support`, or `ticket` and it is used; otherwise the panel shows 🏓.

## Your own logo

Every panel and message uses the SpinBack picture that ships with the bot. To use your own, run `/setup logo image:` and attach a PNG, JPEG, GIF, or WebP up to 512 KB. The picture is checked from its own bytes, saved for your server in SQLite, and the posted support and rules panels are refreshed immediately; `/setup status` then reports `Server logo: Your uploaded logo`. `/setup logo clear: True` removes it and returns to the bundled mark. Panels show the picture as a small mark beside their heading — a compact 96×96 copy of it — so the panel text reads first, while the support panel uses the full-size picture. Servers that never upload a picture need no configuration.

## Who can see which command

Staff and admin commands are hidden from members who lack the matching Discord permission, so the picker only shows what a member can actually use:

- **Manage Messages** — moderation (`/kick`, `/ban`, `/warn`, `/timeout`, `/unban`, `/untimeout`, `/purge`, `/modlog`, `/modstats`, `/slowmode`), the `/ticket` staff set, `/embed` and `/embed-edit`.
- **Manage Server** — `/setup …`, `/rules …`, `/wipe …`, `/giveaway …`, `/poll config`, `/poll create`, `/verifypanel`, `/anti-links`, `/dashboard`.
- **Administrator** — `/ping` and `/sync` (bot owner diagnostics). `/setup roles` is server-owner only.
- **Everyone** — `/help`, `/info …`, `/avatar`, `/report`, `/reimburse`, `/suggestion send|list`, `/wipe list`, and the ticket opener. `/poll results|end|remove` and `/ticket requestclose` stay visible because they are checked per poll or per ticket.

Discord only decides what the picker shows; the role checks stay the real gate. Give the roles you saved in `/setup roles` the permissions above if you want your staff to see the tooling.

## Other commands

`/setup tickets panel_channel logs_channel category inactivity_hours`
`/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel`
`/verifypanel channel role`
`/anti-links enabled:true action:delete_warn`

Day to day, `/info user` shows a member card (account age, join date, roles, staff access, and their recorded moderation history), `/info bot` shows this instance has latency, uptime, servers, command count, and stored-data check, `/avatar` posts a full-size avatar, `/slowmode seconds: 30` sets a channel slowmode (staff only), and `/modstats days: 7` summarises the recorded moderation activity in that window.

## Keep data across Railway restarts and redeploys

The bot stores its SQLite database at `DATABASE_PATH`. For Railway, attach a persistent Volume to the bot service and set the volume mount path to `/data`; set the service variable `DATABASE_PATH=/data/manager.sqlite3`. The application cannot create or attach the Railway Volume from source code. On startup it now warns if Railway reports no volume mount or if the database path is outside the mounted directory. Keep the Volume attached when redeploying; deleting it removes the database.

## Public domain, health checks, and the status endpoint

The bot talks out to Discord and normally never listens on a port, so if Railway attaches a public domain, the edge has nothing to answer with and shows `502 Application failed to respond`. That does not affect the bot: commands keep working through the outbound gateway connection.

Two clean options:

1. **Remove the public domain** in Railway (service → Settings → Networking → delete the generated domain). A gateway-only bot has nothing to serve, so this is the simplest fix and needs no code change.
2. **Keep the domain and serve a status page.** The bot already includes a small read-only endpoint (`grid_a1/health.py`). It binds the port Railway injects (`PORT`, falling back to 8080) and answers the domain root, `/health`, `/healthz`, and `/status` with a compact JSON document:
   ```json
   {"checked_at": "2026-11-16T15:12:42+00:00", "connected_servers": 1, "gateway_latency_ms": 42, "schema_version": 26, "service": "spinback-manager", "status": "ok", "uptime_seconds": 5400}
   ```
   It exposes no tokens, IDs, guild names, ticket contents, or member data. Unknown paths answer 404, anything other than GET/HEAD answers 405, and a failing status report answers 503 so the bot's own uptime monitor can detect it. `HEALTH_DISABLED=1` turns the endpoint off; `HEALTH_PORT` and `HEALTH_HOST` override the port and bind address. Use this when you want Railway health checks or an external uptime monitor to see a 200.

Keep the bot token, database, transcripts, and logs private. Test role hierarchy and permissions in a test server before production.
