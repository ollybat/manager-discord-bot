# 🗺️ Grid A1 Manager Bot — Project Map

This is the standalone Python Discord bot. Technical filenames are intentionally stable and readable; keep imports such as `from grid_a1.tickets import TicketService` unchanged unless a deliberate migration is planned.

## 🌐 Top-level flow

`bot.py` → imports `grid_a1.bot` → loads `.env` → connects Discord → runs SQLite migration → registers persistent views and slash commands → serves Grid A1 ticket/welcome workflows.

## 📁 File map

| File | Responsibility | Arrow flow |
|---|---|---|
| `bot.py` | Safe compatibility entrypoint for local execution. | `python bot.py` → `grid_a1.bot.run()` |
| `.env.example` | Non-secret configuration template. | copy to `.env` → `config.py` reads values |
| `requirements.txt` | Runtime Python dependencies (`discord.py`, `python-dotenv`). | install → imports resolve |
| `README.md` | Friendly orientation, quickstart, and links. | new operator → `SETUP_GUIDE.md` |
| `SETUP_GUIDE.md` | Complete setup and operations instructions. | Portal → install → configure → test |
| `PROJECT_MAP.md` | This architecture and responsibility map. | file question → owner/module |

## 🧩 `grid_a1/` package

| File | Responsibility | Arrow flow |
|---|---|---|
| `__init__.py` | Package identity and version. | `import grid_a1` → package metadata |
| `bot.py` | Discord bot composition, intents, lifecycle, slash commands, panel refresh, event/error handling. | Discord event → command/event handler → service/database |
| `config.py` | Reads environment variables into immutable `Settings`; configures logging. | `.env` → `Settings.from_env()` → bot startup |
| `database.py` | SQLite connection, schema creation/migration, guild config, ticket records, audit log, and counts. | service/events → SQL → persistent `manager.sqlite3` |
| `embeds.py` | Builds Grid A1 support, ticket, archive, and generic embeds. | services/views → embed builders → Discord messages |
| `tickets.py` | Ticket business logic: create private channels, read history, render transcripts, close/archive/delete, claim/transfer. | interaction → `TicketService` → Discord + database |
| `transcript.py` | Converts channel messages and ticket metadata into escaped, responsive HTML. | channel history → HTML transcript → logs attachment |
| `utils.py` | Shared UTC time, channel-name/topic parsing, ticket detection, and staff helpers. | bot/services → utility functions |
| `views.py` | Persistent Discord UI: category select, EU region select, detail/close modals, panel help, claim/close buttons. | panel interaction → modal/view → service |
| `welcomer.py` | Validates welcome channel setup and creates/sends Grid A1 welcome embeds. | member join → config validation → welcome channel |

## 🔄 Runtime workflows

### Startup

`bot.py` → `run()` → `Settings.from_env()` → `GridA1Bot` → `Database.migrate()` → persistent views → command sync (`TEST_GUILD_ID` when present, then global) → ready.

### Support ticket

`TicketPanel` → `TicketTypeSelect` → `RegionView` (**EU only**) → `DetailsModal` → `TicketService.create()` → private Discord channel + SQLite row → `TicketControls`.

### Close and transcript

`/ticket close` or Close button → `TicketService.close()` → channel history → `transcript.render()` → archive embed/file in configured logs channel → SQLite closed/audit records → ticket channel deletion.

### Welcome

`on_member_join` → `welcomer.send_welcome()` → `missing()` validation → `welcome_embed()` → configured welcome channel. The welcome copy says **Grid A1** and **NA Coming Soon** where applicable.

## 🗃️ Persistence map

- `guild_config` → panel, logs, category, welcome/navigation channels, inactivity setting.
- `tickets` → open/closed ticket identity, ownership, region, status, close data, transcript filename.
- `audit_log` → ticket actions and actors.
- `closed_tickets` → aggregate-friendly closed-ticket history.
- `schema_migrations` → applied schema version marker.

`SCHEMA_VERSION = 2` in `database.py`. The bot runs migrations at startup; there is no standalone migration command.

## 🧭 Naming and change guidance

- Keep `grid_a1/` module names stable because imports are already wired across the package.
- Add new responsibilities to the closest existing module or introduce a clearly named module with updated imports; do not create duplicate `tmp-*` or `syntax-*` implementations inside this standalone folder.
- Keep user-facing names branded **Grid A1**. Keep **NA Coming Soon** explicit until a real NA implementation exists.
- Keep secrets/configuration in `.env`, not Python files.
