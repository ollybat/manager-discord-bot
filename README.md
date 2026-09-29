# Grid A1 Manager Discord Bot

This repository preserves the existing SQLite deployment and `DATABASE_PATH` contract. SQLite migrations are additive and schema version 16; use `Database.backup()` before upgrades. A startup schema/integrity check runs before the Discord client starts.

PostgreSQL is not a runtime backend or a tested SQLite migration path: `grid_a1/database.py` always uses SQLite and `DATABASE_PATH`. The separate `grid_a1/postgres.py` module is an optional operator helper with a baseline schema sketch; it does not migrate data or switch the bot runtime. Setting `DATABASE_URL` has no effect on the bot. The SQLite source is never modified. Install `requirements-postgres-tools.txt` only if you explicitly use that helper.

## Setup

Run `/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...`. These five roles are both the permission roles and ticket notification targets. There is no `/setup staff`; this preserves the established semantics.

Keep tokens, databases, transcripts, and logs private. Validate with:

```text
python validate_bot.py
python validate_dashboard.py
python -m unittest -v test_core.py
```

These checks parse every Python module, reject truncation placeholders and destructive/tunnel tokens, and exercise migrations, ticket constraints, atomic closure, orphan cleanup, schema columns, configured roles, `safe_json_list`, status-title formatting, channel-name sanitization, and anti-links/help source behavior. Do not execute the bot as part of validation.
