from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .utils import utcnow

SCHEMA_VERSION = 2

class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def migrate(self) -> None:
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY, panel_channel INTEGER, panel_message INTEGER,
                logs_channel INTEGER, ticket_category INTEGER, inactivity_hours INTEGER NOT NULL DEFAULT 24,
                welcome_channel INTEGER, verify_channel INTEGER, link_channel INTEGER,
                bot_commands_channel INTEGER, shop_channel INTEGER)""")
            db.execute("""CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER UNIQUE NOT NULL,
                owner_id INTEGER NOT NULL, issue TEXT NOT NULL, region TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', claimed_by INTEGER, close_requested_by INTEGER,
                opened_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, closed_at TEXT, closed_by INTEGER,
                close_reason TEXT, transcript_filename TEXT)""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS only_one_open_ticket ON tickets(guild_id, owner_id) WHERE status IN ('open','close_requested')")
            db.execute("""CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, ticket_id TEXT,
                actor_id INTEGER NOT NULL, action TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS closed_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, region TEXT NOT NULL,
                issue TEXT NOT NULL, closed_by INTEGER NOT NULL, reason TEXT NOT NULL, closed_at TEXT NOT NULL)""")
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (?, ?)", (SCHEMA_VERSION, utcnow().isoformat()))

    def config(self, guild_id: int) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)).fetchone()

    def upsert_config(self, guild_id: int, **values: Any) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO guild_config(guild_id) VALUES(?) ON CONFLICT DO NOTHING", (guild_id,))
            for key, value in values.items():
                if key not in {"panel_channel", "panel_message", "logs_channel", "ticket_category", "inactivity_hours", "welcome_channel", "verify_channel", "link_channel", "bot_commands_channel", "shop_channel"}:
                    raise ValueError(f"unknown config field: {key}")
                db.execute(f"UPDATE guild_config SET {key}=? WHERE guild_id=?", (value, guild_id))

    def open_ticket_for_owner(self, guild_id: int, owner_id: int) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM tickets WHERE guild_id=? AND owner_id=? AND status IN ('open','close_requested')", (guild_id, owner_id)).fetchone()

    def ticket(self, ticket_id: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()

    def create_ticket(self, **values: Any) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO tickets(ticket_id,guild_id,channel_id,owner_id,issue,region,opened_at,last_activity_at) VALUES(:ticket_id,:guild_id,:channel_id,:owner_id,:issue,:region,:opened_at,:last_activity_at)", values)
            db.execute("INSERT INTO audit_log(guild_id,ticket_id,actor_id,action,created_at) VALUES(?,?,?,?,?)", (values['guild_id'], values['ticket_id'], values['owner_id'], 'opened', utcnow().isoformat()))

    def update_ticket(self, ticket_id: str, **values: Any) -> None:
        with self.connect() as db:
            allowed = {"status", "claimed_by", "close_requested_by", "last_activity_at", "closed_at", "closed_by", "close_reason", "transcript_filename"}
            if not set(values) <= allowed: raise ValueError("unknown ticket field")
            db.execute(f"UPDATE tickets SET {', '.join(f'{k}=?' for k in values)} WHERE ticket_id=?", (*values.values(), ticket_id))

    def audit(self, guild_id: int, ticket_id: str | None, actor_id: int, action: str, metadata: str = '{}') -> None:
        with self.connect() as db: db.execute("INSERT INTO audit_log(guild_id,ticket_id,actor_id,action,metadata,created_at) VALUES(?,?,?,?,?,?)", (guild_id, ticket_id, actor_id, action, metadata, utcnow().isoformat()))

    def closed_count(self, guild_id: int, region: str | None = None) -> int:
        with self.connect() as db:
            if region: return int(db.execute("SELECT COUNT(*) FROM closed_tickets WHERE guild=? AND region=?", (guild_id, region)).fetchone()[0])
            return int(db.execute("SELECT COUNT(*) FROM closed_tickets WHERE guild=?", (guild_id,)).fetchone()[0])

    def open_counts(self, guild_id: int) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute("SELECT region,COUNT(*) n FROM tickets WHERE guild_id=? AND status IN ('open','close_requested') GROUP BY region", (guild_id,)).fetchall()
            return {row['region']: int(row['n']) for row in rows}
