# Grid A1 Manager Bot — Project Map

This repository is a standalone Python Discord bot. `bot.py` is the supported entrypoint (`python bot.py`); it imports `grid_a1.bot.run()`.

## Runtime flow

Startup loads `.env`, validates settings, configures logging, runs SQLite migrations, restores persistent ticket/poll/giveaway views, starts panel/inactivity/poll-expiry/giveaway-expiry loops, publishes the global application command tree, and clears stale guild command copies after the Gateway is ready. The bot is Discord-only and has no unrelated network transport layer. SQLite schema version is 19; migrations are additive and preserve DATABASE_PATH and existing records.

## Modules

- `grid_a1/bot.py`: bot composition, commands, lifecycle, sync safety, event/error handling, anti-links, and loops.
- `grid_a1/config.py`: environment settings, logging, and Railway volume/path persistence warnings.
- `grid_a1/database.py`: SQLite schema repair/migration, ticket/poll/giveaway persistence, indexes, backup, and audit log.
- `grid_a1/postgres.py`: optional operator-only PostgreSQL connection/schema sketch; not imported by the bot and not a data migration tool.
- `grid_a1/commands.py`: standalone commands and owner diagnostics.
- `grid_a1/views.py`: dashboard shell and persistent ticket/verification views.
- `grid_a1/dashboard_setup.py`: role/channel dropdown setup wizards, explicit panel publishing, and report routing.
- `grid_a1/tickets.py`: ticket creation, permissions, defer/followup closure, transcripts, and archive.
- `grid_a1/polls.py`: poll dashboard/metadata modal, persistent poll creation, result embeds, vote controls, and message refresh service.
- `grid_a1/giveaways.py`: private giveaway setup, persistent entry buttons, timer-based winner draws, and active giveaway dashboard.
- `grid_a1/utils.py`: topic parsing, staff checks, link normalization, and safe JSON parsing.
- `grid_a1/embeds.py`, `welcomer.py`, `transcript.py`: user-facing presentation and welcome/transcript rendering.

## Configuration contract

`/setup roles owner_role co_owner_role head_admin_role admin_role moderator_role` accepts exactly five distinct normal roles and is server-owner-only. Those five roles grant staff permissions and are the ticket notification targets. There is no `/setup staff` command. `/dashboard` is restricted to configured owner/co-owner roles and its checks are repeated on every interaction; setup flows use Discord role/channel selectors. Any server member may use `/report`; reports are delivered to the staff-only channel selected in the dashboard. Ticket inactivity is persisted in SQLite and views are re-registered at startup.

## Reliability notes

SQLite startup creates base tables before inspecting or adding columns, then creates indexes and records the schema version. Railway startup checks that `DATABASE_PATH` is inside the attached `RAILWAY_VOLUME_MOUNT_PATH`; a missing/mismatched volume logs a warning because the volume itself must be attached in Railway service settings. Ticket creation and closure defer before slow Discord/history operations and respond through followups. A ticket is marked closed only after the transcript archive succeeds; its ticket, closure-history, and audit rows are finalized atomically. Inactivity DM component IDs are unique per ticket, scans confirm a missing channel with Discord before cleaning an open row, and reachable owners are not auto-closed if their notice could not be delivered. Unexpected loop errors are isolated per guild/ticket so one bad record does not stop maintenance. All operator-facing failures use actionable messages where possible.

Keep tokens, SQLite files, logs, and transcripts private. Validate with `python validate_bot.py` and `python validate_dashboard.py` before publishing.
