# Grid A1 Manager Bot — Project Map

This repository is a standalone Python Discord bot. `bot.py` is the supported entrypoint (`python bot.py`); it imports `grid_a1.bot.run()`.

## Runtime flow

Startup loads `.env`, validates settings, configures logging, runs SQLite migrations, registers persistent views, starts resilient panel/inactivity loops, removes stale guild command copies, and publishes the global application command tree. The bot is Discord-only and has no unrelated network transport layer. SQLite schema version is 15; migrations are additive and preserve DATABASE_PATH and existing records.

## Modules

- `grid_a1/bot.py`: bot composition, commands, lifecycle, sync safety, event/error handling, anti-links, and loops.
- `grid_a1/config.py`: environment settings and logging.
- `grid_a1/database.py`: SQLite schema repair/migration, ticket persistence, indexes, and audit log.
- `grid_a1/commands.py`: standalone commands and owner diagnostics.
- `grid_a1/views.py`: dashboard, setup modals, persistent ticket and verification views.
- `grid_a1/tickets.py`: ticket creation, permissions, defer/followup closure, transcripts, and archive.
- `grid_a1/utils.py`: topic parsing, staff checks, link normalization, and safe JSON parsing.
- `grid_a1/embeds.py`, `welcomer.py`, `transcript.py`: user-facing presentation and welcome/transcript rendering.

## Configuration contract

`/setup roles owner_role co_owner_role head_admin_role admin_role moderator_role` accepts exactly five distinct normal roles and is server-owner-only. Those five roles are permission roles. `/setup staff` controls separate notification roles only. `/dashboard` is restricted to configured owner/co-owner roles and its checks are repeated on every interaction. Ticket inactivity is persisted in SQLite and views are re-registered at startup.

## Reliability notes

SQLite startup creates base tables before inspecting or adding columns, then creates indexes and records the schema version. Ticket creation and closure defer before slow Discord/history operations and respond through followups. Unexpected loop errors are isolated per guild/ticket so one bad record does not stop maintenance. All operator-facing failures use actionable messages where possible.

Keep tokens, SQLite files, logs, and transcripts private. Validate with `python validate_bot.py` and `python validate_dashboard.py` before publishing.
