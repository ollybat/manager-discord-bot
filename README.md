# Grid A1 Manager Discord Bot

This repository preserves the existing SQLite deployment and `DATABASE_PATH` contract. SQLite migrations are additive and schema version 19; use `Database.backup()` before upgrades. A startup schema/integrity check runs before the Discord client starts.

PostgreSQL is not a runtime backend or a tested SQLite migration path: `grid_a1/database.py` always uses SQLite and `DATABASE_PATH`. The separate `grid_a1/postgres.py` module is an optional operator helper with a baseline schema sketch; it does not migrate data or switch the bot runtime. Setting `DATABASE_URL` has no effect on the bot. The SQLite source is never modified. Install `requirements-postgres-tools.txt` only if you explicitly use that helper.

## Setup

For interactive setup, open `/dashboard` and use the module buttons. Permission roles, ticket channels, welcome channels, verification, announcements, and report routing use Discord role/channel dropdowns instead of copying IDs. Publishing public panels is a separate, clearly labeled action.

### Polls

Server managers can use `/poll config` to open the private Create Poll / View Active panel. Its Poll Settings button sets the default channel and duration; `/poll create` accepts a question, optional description, and pipe-separated choices (for example, `Island | Ragnarok`). Members vote through the persistent select menu and may change their vote while the poll is open. Polls and votes are stored in SQLite, so bot restarts do not reset them. The creator or a server manager can use `/poll end poll_id` or `/poll remove poll_id`; duration expiry automatically ends voting.

Server managers can use `/giveaway config` to choose an announcement channel, optionally ping a role, enter a free-text reward, set 1–50 winners, and choose a duration (24 hours by default). Members get one persistent entry per giveaway; the bot draws winners when the timer expires or when staff run `/giveaway end giveaway_id`. Active giveaways and entries survive restarts.

Run `/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...` if you need to initialize dashboard access first. These five roles are both permission roles and ticket notification targets. There is no `/setup staff` command.

### Keep data across Railway restarts and redeploys

SQLite already writes tickets, settings, reports, and audit records to `DATABASE_PATH`. On Railway, attach a persistent **Volume** to the bot service with mount path `/data`, then set `DATABASE_PATH=/data/manager.sqlite3`. Railway volume attachment is service configuration and cannot be created by a code deploy. Startup now warns if Railway does not expose a mounted volume or if `DATABASE_PATH` points outside it. Do not delete the volume when redeploying; doing so deletes the stored database.

All server members can use `/report member reason proof_link proof_file`. Reports go to the report channel selected in `/dashboard`; restrict that channel's visibility to trusted staff. A proof URL or uploaded evidence file is optional.

Keep tokens, databases, transcripts, and logs private. Validate with:

```text
python validate_bot.py
python validate_dashboard.py
python -m unittest -v test_core.py
```

These checks parse every Python module, reject truncation placeholders and destructive/tunnel tokens, and exercise migrations, report-channel persistence, safe proof URLs, ticket constraints, atomic closure, orphan cleanup, poll persistence/voting, schema columns, configured roles, persistent component IDs, `safe_json_list`, status-title formatting, channel-name sanitization, and command/dashboard dropdown source behavior. Do not execute the bot as part of validation.
