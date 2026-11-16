# SpinBack Manager Discord Bot

This repository preserves the existing SQLite deployment and `DATABASE_PATH` contract. SQLite migrations are additive and schema version 23; use `Database.backup()` before upgrades. A startup schema/integrity check runs before the Discord client starts.

PostgreSQL is not a runtime backend or a tested SQLite migration path: `grid_a1/database.py` always uses SQLite and `DATABASE_PATH`. The separate `grid_a1/postgres.py` module is an optional operator helper with a baseline schema sketch; it does not migrate data or switch the bot runtime. Setting `DATABASE_URL` has no effect on the bot. The SQLite source is never modified. Install `requirements-postgres-tools.txt` only if you explicitly use that helper.

## Setup

For interactive setup, open `/dashboard` and use the module buttons. Permission roles, ticket channels, welcome channels, verification, announcements, and report routing use Discord role/channel dropdowns instead of copying IDs. Publishing public panels is a separate, clearly labeled action.

### Polls

Server managers can use `/poll config` to open the private Create Poll / View Active panel. Its Poll Settings button sets the default channel and duration; `/poll create` accepts a question, optional description, and pipe-separated choices (for example, `Island | Ragnarok`). Members vote through the persistent select menu and may change their vote while the poll is open. Polls and votes are stored in SQLite, so bot restarts do not reset them. The creator or a server manager can use `/poll end poll_id` or `/poll remove poll_id`; duration expiry automatically ends voting.

Server managers can use `/giveaway config` to choose an announcement channel, optionally ping a role, enter a free-text reward, set 1–50 winners, and choose a duration (24 hours by default). Members get one persistent entry per giveaway; the bot draws winners when the timer expires or when staff run `/giveaway end giveaway_id`. Active giveaways and entries survive restarts.

### Ticket transcripts

Closing a ticket uploads a plain-text transcript to the transcript channel before the channel is deleted: a bordered header with the server, ticket channel, message count, created and closed times, the ticket owner and the staff member who claimed it, then one indented block per message, then a generated-by footer. All times are UTC. Attachments are kept as links, so transcripts stay small and upload quickly.

### Owner feedback

When a ticket closes, the owner receives a private message asking them to rate the help they received from 1 to 5 stars. Ratings are stored per ticket, can be changed at any time, and staff only ever see the average and the number of ratings in `/ticket stats`. If the owner has private messages blocked the request is logged and skipped, and it never blocks the closure.

Staff can run `/ticket stats` for a live workload panel: queue size split into claimed and unclaimed, closures in the last 7 and 30 days, average time to first staff claim, average time to closure, open tickets per region, open tickets per priority, the most active claimers with each claimer's average time to first claim, and the owner rating average. `/ticket transcript` writes a plain-text transcript of the current ticket on demand without closing it.

### Ticket priority and readable ticket names

Staff run `/ticket priority` inside a ticket to set it to 🟢 Low, 🔵 Normal, 🟠 High or 🔴 Urgent; new tickets default to Normal. The choice is confirmed in the ticket channel, recorded in the audit log, summarised in `/ticket stats`, and shown on the archive embed in the transcript channel. Changing the priority renames the channel so the urgency marker at the front of its name follows the ticket: 🔴-reimburse-olly-a1b2c3d4. The opening embed now shows who asked, the category, the region, the ticket reference, and each answer of the ticket form on its own line, so staff read a filled-in form instead of one wall of text.

### Reimbursement tickets

`/reimburse` is open to everyone and opens a private reimbursement ticket after a short four-question form: Gamertag, Clan Name, Grid Location, and What Happened. The answers become the ticket's opening report, one labeled field per answer, so staff see the full picture in the channel and in the plain-text transcript. The support panel offers the same category as 🧾 Ticket Reimbursement, and each member can open one request every 15 minutes.

### Wipe announcements

Wipes are scheduled per region instead of announced by hand. `/wipe config` opens a panel for the announcement channel, an optional ping role, and automatic posting; `/wipe add` accepts a wipe as an ISO timestamp or a relative offset (`+2d`, `6h`), `/wipe list` shows what is scheduled soonest-first with a copy-ready countdown, `/wipe remove` cancels one, and `/wipe announce` posts a chosen wipe immediately. With automatic posting on, a background job announces each wipe once when its time arrives and marks it announced, so nothing is posted twice across restarts. `!wipe` prints the same schedule from chat.

### Rules panel

`/rules post channel: #rules` publishes the SpinBack rules panel: one container with a cyan accent bar, the heading "SpinBack Rules ✅", the thank-you and purpose text, then one line per rule set with its own button inside that line — **Discord 🎧 (All discord related rules.)** and **Game 🎮 (General in-game rules.)** — and a *Powered by SpinBack* footer. Each button opens that set privately for the member who pressed it. The panel uses Discord's Components v2 layout, so the buttons sit inside the panel container instead of in a row underneath it. The starter texts are sensible Discord and in-game rules; `/rules edit section:` opens a form to replace one set with your own wording, and the posted panel refreshes immediately (the buttons never need re-posting). `/rules preview` shows the panel privately before you publish it, and the buttons keep working after a restart. Posting again removes the panel your previous post left behind, so re-running `/rules post` once replaces an panel an older version published — that is also how an existing panel moves onto the new layout. The panel needs discord.py 2.7 or newer, which `requirements.txt` now asks for.

### Suggestions

Members send ideas with `/suggestion send`; the request is posted in the channel chosen by `/suggestion config`, with 👍 Yes and 👎 No buttons that anyone can use. Votes are stored per member and per suggestion: pressing the same button again removes the vote, switching buttons moves it, and the totals, share, and your own vote are refreshed on the post. `/suggestion list` shows the newest ideas with their vote totals, and voting buttons keep working after a restart.

Run `/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...` if you need to initialize dashboard access first. These five roles are both permission roles and ticket notification targets. There is no `/setup staff` command.

### Command experience

`/help` is generated from the bot's live command tree, so it can never drift out of date: it lists every command the member may use, grouped into sections, with each usage line and the access that command needs, and it hides sections the member cannot use. `/help command:` opens a searchable picker and shows one command in full — purpose, usage, options, and access.

### Staff access comes from the server, not from Discord permissions

Run `/setup roles` first. The five roles saved there (owner, co-owner, head admin, admin, moderator) are the **only** roles that pass a staff command — Discord's own Administrator and Manage Server permissions no longer grant access on their own. The server owner and the bot owner keep control of their own server, which is what makes the first `/setup roles` possible. `/setup status` shows every role and setting with ✅ or ❌ and tells you the next command to run.

### Prefix commands

The same moderation work is available with `!` for people who prefer typing: `!help`, `!ping`, `!whois`, `!serverinfo`, `!warn`, `!timeout`, `!untimeout`, `!kick`, `!ban`, `!unban`, `!modlog`, `!stats`, `!clear`, `!slowmode`, `!lock`, and `!unlock`. They share the role-based guard, the audit log, and the same plain-language errors as the slash commands, and `!help` lists them all.

`!clear` takes an optional count (`!clear 50`) or nothing at all: with no count it empties the whole channel behind a confirmation prompt that only the caller can answer. Discord cannot bulk-delete messages older than 14 days, so those are removed one at a time; the job stops after about 20 seconds and reports how many are left, and running it again continues. `/purge` does the same thing with `everything:True`, and every run is recorded in the audit log with the channel and the number actually deleted.

Moderation covers both directions: `/kick`, `/ban`, `/warn`, and `/timeout` record every action against the member, and `/unban` (with a picker built from the server's live ban list), `/untimeout`, and `/purge` are there for the clean-up side, each reporting exactly what changed and what Discord refused. `/poll results` shows the standings of a poll you created without ending it, and `/giveaway list` lists open giveaways soonest-ending first with entry and winner counts.

Staff tooling is built into the same layer: `/ticket list` shows the open queue ordered by priority and age with claimer and age per ticket, `/ticket note` stores an internal staff note that the ticket owner never sees, `/ticket notes` reads the note history back, and `/modlog member:` lists the recorded kicks, bans, warnings, and timeouts for a member (newest first). Destructive commands ask first: `/giveaway end` and `/poll remove` send a confirmation prompt that only the member who ran the command can answer, and an unanswered prompt expires with a clear "nothing was changed" note.

Command identifiers are autocompleted instead of copied by hand: `/giveaway end` suggests this server's active giveaways by reward, `/poll end` suggests its open polls by question, and `/poll remove` lists every poll in the server with its status. Expensive commands (`/report`, `/ticket stats`, `/ticket transcript`, `/info server`, `/embed`, `/embed-edit`) are rate-limited per member, and every failure answers once, privately and in plain language: a cooldown says how long to wait, a permission problem names the missing permission or points at `/help command:<name>`, and an unexpected error is logged while the member still gets a short explanation.

### Optional status endpoint (public domain / health checks)

The bot only connects outward to Discord, so a host that attaches a public domain gets nothing to answer with and shows `502 Application failed to respond` — that never affects commands, which run over the gateway connection. Two options: remove the domain in the host's settings (a gateway-only bot has nothing to serve), or keep it and let the built-in read-only endpoint answer. It binds the port the host injects (`PORT`, falling back to 8080) and answers `/`, `/health`, `/healthz`, and `/status` with a small JSON document (status, uptime, connected servers, gateway latency, schema version). It never exposes tokens, IDs, guild names, ticket contents, or member data; a failing report answers 503, unknown paths 404, and anything but GET/HEAD 405. `HEALTH_DISABLED=1` turns it off, `HEALTH_PORT` and `HEALTH_HOST` override the port and bind address, and startup keeps running normally if the port cannot be bound.

### Keep data across Railway restarts and redeploys

SQLite already writes tickets, settings, reports, and audit records to `DATABASE_PATH`. On Railway, attach a persistent **Volume** to the bot service with mount path `/data`, then set `DATABASE_PATH=/data/manager.sqlite3`. Railway volume attachment is service configuration and cannot be created by a code deploy. Startup now warns if Railway does not expose a mounted volume or if `DATABASE_PATH` points outside it. Do not delete the volume when redeploying; doing so deletes the stored database.

All server members can use `/report member reason proof_link proof_file`. Reports go to the report channel selected in `/dashboard`; restrict that channel's visibility to trusted staff. A proof URL or uploaded evidence file is optional.

Keep tokens, databases, transcripts, and logs private. Validate with:

```text
python validate_bot.py
python validate_dashboard.py
python -m unittest -v test_core.py test_features.py
```

These checks parse every Python module, reject truncation placeholders and destructive/tunnel tokens, and exercise migrations, report-channel persistence, safe proof URLs, ticket constraints, atomic closure, orphan cleanup, poll persistence/voting, schema columns, configured roles, persistent component IDs, `safe_json_list`, status-title formatting, channel-name sanitization, command/dashboard dropdown source behavior, ticket channel naming, the reimbursement form, suggestion votes, and the rules panel. `test_features.py` holds the newer SpinBack feature suite and `test_core.py` the SQLite core; CI runs both through `pytest`. Do not execute the bot as part of validation.
