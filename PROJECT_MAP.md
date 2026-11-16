# Grid A1 Manager Bot — Project Map

This repository is a standalone Python Discord bot. `bot.py` is the supported entrypoint (`python bot.py`); it imports `grid_a1.bot.run()`.

## Runtime flow

Startup loads `.env`, validates settings, configures logging, runs SQLite migrations, restores persistent ticket/poll/giveaway/feedback views, starts panel/inactivity/poll-expiry/giveaway-expiry loops, publishes the global application command tree, and clears stale guild command copies after the Gateway is ready. The bot is Discord-only and has no unrelated network transport layer. SQLite schema version is 21; migrations are additive and preserve DATABASE_PATH and existing records. Closing a ticket uploads a plain-text transcript (`<channel>-transcript.txt`) to the transcript channel before the channel is deleted.

## Modules

- `grid_a1/bot.py`: bot composition, commands, lifecycle, sync safety, event/error handling, anti-links, and loops. `/ticket stats` renders the workload panel, `/ticket priority` sets a ticket's triage priority, and `/ticket transcript` writes an on-demand `.txt` transcript without closing the ticket.
- `grid_a1/config.py`: environment settings, logging, and Railway volume/path persistence warnings.
- `grid_a1/database.py`: SQLite schema repair/migration, ticket/poll/giveaway persistence, owner feedback records, indexes, backup, and audit log.
- `grid_a1/postgres.py`: optional operator-only PostgreSQL connection/schema sketch; not imported by the bot and not a data migration tool.
- `grid_a1/commands.py`: standalone commands and owner diagnostics.
- `grid_a1/views.py`: dashboard shell, persistent ticket and verification views, and the persistent 1–5 star feedback view sent privately to ticket owners after closure.
- `grid_a1/dashboard_setup.py`: role/channel dropdown setup wizards, explicit panel publishing, and report routing.
- `grid_a1/tickets.py`: ticket creation, permissions, defer/followup closure, transcripts, and archive.
- `grid_a1/polls.py`: poll dashboard/metadata modal, persistent poll creation, result embeds, vote controls, and message refresh service.
- `grid_a1/giveaways.py`: private giveaway setup, persistent entry buttons, timer-based winner draws, and active giveaway dashboard.
- `grid_a1/stats.py`: read-only ticket workload statistics (queue split, closed-ticket windows, first-claim and closure averages, per-region load, per-priority load, claim leaderboard with each claimer's first-claim average, owner rating average).
- `grid_a1/utils.py`: topic parsing, staff checks, link normalization, safe JSON parsing, and the shared priority/feedback constants.
- `grid_a1/embeds.py`, `welcomer.py`: user-facing presentation and welcome rendering.
- `grid_a1/transcript.py`: plain-text ticket transcript renderer (60-column bordered header, indented message blocks, attachment links, no image downloads).

## Configuration contract

`/setup roles owner_role co_owner_role head_admin_role admin_role moderator_role` accepts exactly five distinct normal roles and is server-owner-only. Those five roles grant staff permissions and are the ticket notification targets. There is no `/setup staff` command. `/dashboard` is restricted to configured owner/co-owner roles and its checks are repeated on every interaction; setup flows use Discord role/channel selectors. Any server member may use `/report`; reports are delivered to the staff-only channel selected in the dashboard. Ticket inactivity is persisted in SQLite and views are re-registered at startup.

## Reliability notes

SQLite startup creates base tables before inspecting or adding columns, so the priority column and the feedback table arrive on existing databases without a rebuild; then creates indexes and records the schema version. Railway startup checks that `DATABASE_PATH` is inside the attached `RAILWAY_VOLUME_MOUNT_PATH`; a missing/mismatched volume logs a warning because the volume itself must be attached in Railway service settings. Ticket creation and closure defer before slow Discord/history operations and respond through followups. A ticket is marked closed only after the transcript archive succeeds; its ticket, closure-history, and audit rows are finalized atomically. Inactivity and feedback component IDs are unique per ticket, a feedback DM that the owner has blocked is logged and skipped without blocking closure, scans confirm a missing channel with Discord before cleaning an open row, and reachable owners are not auto-closed if their notice could not be delivered. Unexpected loop errors are isolated per guild/ticket so one bad record does not stop maintenance. All operator-facing failures use actionable messages where possible.

Keep tokens, SQLite files, logs, and transcripts private. Validate with `python validate_bot.py` and `python validate_dashboard.py` before publishing.
