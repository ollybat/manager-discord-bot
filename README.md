# Grid A1 Manager Discord Bot

This repository preserves the existing SQLite deployment and `DATABASE_PATH` contract. SQLite migrations are additive and schema version 16; use `Database.backup()` before upgrades. A startup schema/integrity check runs before the Discord client starts.

PostgreSQL is optional tooling only and is not implemented as a runtime backend: `grid_a1/database.py` always uses SQLite and `DATABASE_PATH`. `grid_a1/postgres.py` provides an isolated psycopg connection factory, portable baseline schema, and a non-destructive migration plan for operator-led work; configuring `DATABASE_URL` does not switch the bot to PostgreSQL. The SQLite source is never modified. Install the optional adapter from `requirements.txt` only when using that tooling.

## Setup

Run `/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...`. These five roles are both the permission roles and ticket notification targets. There is no `/setup staff`; this preserves the established semantics.

Keep tokens, databases, transcripts, and logs private. Validate with:

```text
python validate_bot.py
python validate_dashboard.py
python -m unittest -v test_core.py
```

These checks parse every Python module, reject truncation placeholders and destructive/tunnel tokens, and exercise migrations, ticket constraints, schema columns, configured roles, `safe_json_list`, channel-name sanitization, and anti-links source behavior. Do not execute the bot as part of validation.
