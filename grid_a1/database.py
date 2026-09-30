from __future__ import annotations
import sqlite3
import json
from pathlib import Path
from typing import Any
from .utils import utcnow, safe_json_list
SCHEMA_VERSION = 17
class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection with WAL and bounded lock waits."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection
    def migrate(self):
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS guild_config (guild_id INTEGER PRIMARY KEY, panel_channel INTEGER, panel_message INTEGER, panel_fingerprint TEXT, logs_channel INTEGER, report_channel INTEGER, ticket_category INTEGER, inactivity_hours INTEGER NOT NULL DEFAULT 24, welcome_channel INTEGER, verify_channel INTEGER, link_channel INTEGER, bot_commands_channel INTEGER, shop_channel INTEGER, verify_panel_channel INTEGER, verify_panel_message INTEGER, verify_role INTEGER, staff_role_1 INTEGER, staff_role_2 INTEGER, staff_role_3 INTEGER, staff_role_4 INTEGER, staff_role_5 INTEGER, staff_role_6 INTEGER, staff_role_7 INTEGER, staff_role_8 INTEGER, staff_role_9 INTEGER, staff_role_10 INTEGER, wipefeed_enabled INTEGER NOT NULL DEFAULT 0, wipefeed_channel INTEGER, urgent_at TEXT, urgent_by INTEGER, owner_role INTEGER, moderator_role INTEGER, admin_role INTEGER, co_owner_role INTEGER, head_admin_role INTEGER, anti_links_enabled INTEGER NOT NULL DEFAULT 0, anti_links_log_channel INTEGER, anti_links_action TEXT NOT NULL DEFAULT 'delete_warn', anti_links_whitelist_domains TEXT NOT NULL DEFAULT '[]', anti_links_bypass_roles TEXT NOT NULL DEFAULT '[]', anti_links_allowed_roles TEXT NOT NULL DEFAULT '[]')''')
            db.execute('''CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER UNIQUE NOT NULL, owner_id INTEGER NOT NULL, issue TEXT NOT NULL, region TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', claimed_by INTEGER, close_requested_by INTEGER, opened_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, inactivity_notice_at TEXT, owner_left INTEGER NOT NULL DEFAULT 0, auto_close_at TEXT, auto_close_reason TEXT, closed_at TEXT, closed_by INTEGER, close_reason TEXT, transcript_filename TEXT, urgent_at TEXT, urgent_by INTEGER)''')
            existing={r[1] for r in db.execute('PRAGMA table_info(guild_config)')}
            if 'panel_fingerprint' not in existing: db.execute("ALTER TABLE guild_config ADD COLUMN panel_fingerprint TEXT")
            cols={**{n:'INTEGER' for n in ('report_channel','verify_panel_channel','verify_panel_message','verify_role',*[f'staff_role_{x}' for x in range(1,11)],'wipefeed_enabled','wipefeed_channel','urgent_by','owner_role','moderator_role','admin_role','co_owner_role','head_admin_role','anti_links_enabled','anti_links_log_channel')},'urgent_at':'TEXT','anti_links_action':"TEXT NOT NULL DEFAULT 'delete_warn'",'anti_links_whitelist_domains':"TEXT NOT NULL DEFAULT '[]'",'anti_links_bypass_roles':"TEXT NOT NULL DEFAULT '[]'",'anti_links_allowed_roles':"TEXT NOT NULL DEFAULT '[]'"}
            for n,d in cols.items():
                if n not in existing: db.execute(f'ALTER TABLE guild_config ADD COLUMN {n} {d}')
            existing={r[1] for r in db.execute('PRAGMA table_info(tickets)')}
            for n,d in {'claimed_by':'INTEGER','close_requested_by':'INTEGER','inactivity_notice_at':'TEXT','owner_left':'INTEGER NOT NULL DEFAULT 0','auto_close_at':'TEXT','auto_close_reason':'TEXT','closed_at':'TEXT','closed_by':'INTEGER','close_reason':'TEXT','transcript_filename':'TEXT','urgent_at':'TEXT','urgent_by':'INTEGER'}.items():
                if n not in existing: db.execute(f'ALTER TABLE tickets ADD COLUMN {n} {d}')
            # Create audit_log before any index that references it.
            db.execute('''CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, ticket_id TEXT, actor_id INTEGER NOT NULL, action TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)''')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS only_one_open_ticket ON tickets(guild_id, owner_id) WHERE status IN ('open','close_requested')")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_guild_status_activity ON tickets(guild_id, status, last_activity_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_channel_status ON tickets(channel_id, status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_owner_status ON tickets(guild_id, owner_id, status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_config_panel ON guild_config(panel_channel, panel_message)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_audit_guild_created ON audit_log(guild_id, created_at)")
            db.execute('''CREATE TABLE IF NOT EXISTS closed_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, region TEXT NOT NULL, issue TEXT NOT NULL, closed_by INTEGER NOT NULL, reason TEXT NOT NULL, closed_at TEXT NOT NULL)''')
            db.execute("UPDATE guild_config SET anti_links_enabled=COALESCE(anti_links_enabled,0), anti_links_action=COALESCE(NULLIF(anti_links_action,''),'delete_warn'), anti_links_whitelist_domains=COALESCE(NULLIF(anti_links_whitelist_domains,''),'[]'), anti_links_bypass_roles=COALESCE(NULLIF(anti_links_bypass_roles,''),'[]'), anti_links_allowed_roles=COALESCE(NULLIF(anti_links_allowed_roles,''),'[]')")
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES (?,?)',(SCHEMA_VERSION,utcnow().isoformat()))
    def backup(self, destination: Path) -> Path:
        """Create a consistent SQLite backup without altering the live database."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        try:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        return destination

    def schema_check(self) -> dict[str, object]:
        return self.startup_check()

    def startup_check(self) -> dict[str, object]:
        with self.connect() as db:
            required = {"guild_config", "tickets", "audit_log", "closed_tickets", "schema_migrations"}
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = sorted(required - tables)
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            return {"ok": not missing and integrity == "ok" and version == SCHEMA_VERSION, "missing": missing, "integrity": integrity, "schema_version": version, "expected_version": SCHEMA_VERSION}

    def config(self, guild_id: int) -> sqlite3.Row | None:
        """Fetch one guild's persisted configuration, if it has been initialized."""
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM guild_config WHERE guild_id=?",
                (guild_id,),
            ).fetchone()
    def upsert_config(self, guild_id: int, **values: Any) -> None:
        """Create/update one guild's config atomically, rejecting unknown columns."""
        allowed_fields = {
            "panel_channel", "panel_message", "panel_fingerprint", "logs_channel",
            "report_channel", "ticket_category", "inactivity_hours", "welcome_channel",
            "verify_channel", "link_channel", "bot_commands_channel", "shop_channel",
            "verify_panel_channel", "verify_panel_message", "verify_role", "staff_role_1",
            "staff_role_2", "staff_role_3", "staff_role_4", "staff_role_5", "staff_role_6",
            "staff_role_7", "staff_role_8", "staff_role_9", "staff_role_10",
            "wipefeed_enabled", "wipefeed_channel", "owner_role", "moderator_role",
            "admin_role", "co_owner_role", "head_admin_role", "anti_links_enabled",
            "anti_links_log_channel", "anti_links_action", "anti_links_whitelist_domains",
            "anti_links_bypass_roles", "anti_links_allowed_roles",
        }
        unknown_fields = set(values) - allowed_fields
        if unknown_fields:
            raise ValueError(f"unknown config field: {unknown_fields}")

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO guild_config(guild_id) VALUES(?) ON CONFLICT DO NOTHING",
                    (guild_id,),
                )
                for field, value in values.items():
                    db.execute(
                        f"UPDATE guild_config SET {field}=? WHERE guild_id=?",
                        (value, guild_id),
                    )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
    def open_ticket_for_owner(self, guild_id: int, owner_id: int) -> sqlite3.Row | None:
        """Return the owner's one active ticket, if present."""
        query = "SELECT * FROM tickets WHERE guild_id=? AND owner_id=? AND status IN ('open','close_requested')"
        with self.connect() as db:
            return db.execute(query, (guild_id, owner_id)).fetchone()

    def open_tickets(self, guild_id: int) -> list[sqlite3.Row]:
        """Return all open tickets for one guild in database order."""
        query = "SELECT * FROM tickets WHERE guild_id=? AND status IN ('open','close_requested')"
        with self.connect() as db:
            return db.execute(query, (guild_id,)).fetchall()

    def open_tickets_all(self) -> list[sqlite3.Row]:
        """Return active tickets across every configured guild."""
        query = "SELECT * FROM tickets WHERE status IN ('open','close_requested')"
        with self.connect() as db:
            return db.execute(query).fetchall()

    def ticket(self, ticket_id: str) -> sqlite3.Row | None:
        """Fetch a ticket by its stable, generated ID."""
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM tickets WHERE ticket_id=?",
                (ticket_id,),
            ).fetchone()
    def create_ticket(
        self,
        *,
        ticket_id: str,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        issue: str,
        region: str,
        opened_at: str,
        last_activity_at: str,
    ) -> None:
        """Insert a ticket and its audit event as one all-or-nothing transaction."""
        ticket_values = {
            "ticket_id": ticket_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "owner_id": owner_id,
            "issue": issue,
            "region": region,
            "opened_at": opened_at,
            "last_activity_at": last_activity_at,
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO tickets "
                    "(ticket_id, guild_id, channel_id, owner_id, issue, region, opened_at, last_activity_at) "
                    "VALUES (:ticket_id, :guild_id, :channel_id, :owner_id, :issue, :region, :opened_at, :last_activity_at)",
                    ticket_values,
                )
                db.execute(
                    "INSERT INTO audit_log(guild_id, ticket_id, actor_id, action, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (guild_id, ticket_id, owner_id, "opened", utcnow().isoformat()),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
    def update_ticket(self, ticket_id: str, **values: Any) -> None:
        """Update allowlisted ticket fields without interpolating caller-provided SQL."""
        allowed_fields = {
            "status", "claimed_by", "close_requested_by", "last_activity_at",
            "inactivity_notice_at", "owner_left", "auto_close_at", "auto_close_reason",
            "closed_at", "closed_by", "close_reason", "transcript_filename",
            "urgent_at", "urgent_by",
        }
        unknown_fields = set(values) - allowed_fields
        if unknown_fields:
            raise ValueError(f"unknown ticket fields: {unknown_fields}")
        if not values:
            return

        assignments = ", ".join(f"{field}=?" for field in values)
        parameters = (*values.values(), ticket_id)
        with self.connect() as db:
            db.execute(
                f"UPDATE tickets SET {assignments} WHERE ticket_id=?",
                parameters,
            )

    def audit(
        self,
        guild_id: int,
        ticket_id: str | None,
        actor_id: int,
        action: str,
        metadata: str = "{}",
    ) -> None:
        """Append an immutable audit event with an ISO-8601 UTC timestamp."""
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_log "
                "(guild_id, ticket_id, actor_id, action, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, ticket_id, actor_id, action, metadata, utcnow().isoformat()),
            )
    def add_closed_ticket(self, *, guild_id, region, issue, closed_by, reason, closed_at):
        with self.connect() as db:
            db.execute('INSERT INTO closed_tickets(guild,region,issue,closed_by,reason,closed_at) VALUES(?,?,?,?,?,?)', (guild_id, region, issue, closed_by, reason[:1000], closed_at))
            db.execute('INSERT INTO audit_log(guild_id,actor_id,action,metadata,created_at) VALUES(?,?,?,?,?)', (guild_id, closed_by, 'closed', json.dumps({'region': region, 'issue': issue}), closed_at))

    def closed_count(self, guild_id: int, region: str | None = None) -> int:
        """Count archived tickets, optionally limited to a support region."""
        query = "SELECT COUNT(*) FROM closed_tickets WHERE guild=?"
        parameters: tuple[Any, ...] = (guild_id,)
        if region is not None:
            query += " AND region=?"
            parameters = (guild_id, region)

        with self.connect() as db:
            return int(db.execute(query, parameters).fetchone()[0])

    def open_counts(self, guild_id: int) -> dict[str, int]:
        """Return open-ticket counts keyed by region for the public support panel."""
        query = (
            "SELECT region, COUNT(*) AS ticket_count FROM tickets "
            "WHERE guild_id=? AND status IN ('open','close_requested') GROUP BY region"
        )
        with self.connect() as db:
            rows = db.execute(query, (guild_id,)).fetchall()
        return {row["region"]: int(row["ticket_count"]) for row in rows}
    def mark_owner_left(self,guild_id,owner_id):
        with self.connect() as db:
            rows=db.execute("SELECT ticket_id FROM tickets WHERE guild_id=? AND owner_id=? AND status IN ('open','close_requested')",(guild_id,owner_id)).fetchall();db.execute("UPDATE tickets SET owner_left=1,auto_close_reason='owner_left' WHERE guild_id=? AND owner_id=? AND status IN ('open','close_requested')",(guild_id,owner_id));return [r['ticket_id'] for r in rows]
    def mark_activity(self, ticket_id: str) -> None:
        """Record activity and cancel any pending inactivity auto-close deadline."""
        self.update_ticket(
            ticket_id,
            last_activity_at=utcnow().isoformat(),
            inactivity_notice_at=None,
            auto_close_at=None,
            auto_close_reason=None,
        )

    def keep_ticket_open(self, ticket_id: str) -> bool:
        """Restore a live ticket after its owner explicitly checks in."""
        with self.connect() as db:
            changed = db.execute(
                "UPDATE tickets SET status='open', close_requested_by=NULL, last_activity_at=?, inactivity_notice_at=NULL, auto_close_at=NULL, auto_close_reason=NULL WHERE ticket_id=? AND status IN ('open','close_requested')",
                (utcnow().isoformat(), ticket_id),
            ).rowcount
            return bool(changed)
    def ticket_by_channel(self,channel_id):
        with self.connect() as db:return db.execute("SELECT * FROM tickets WHERE channel_id=? AND status IN ('open','close_requested')",(channel_id,)).fetchone()
    def mark_channel_missing(self, channel_id, reason='channel_missing'):
        with self.connect() as db:
            row=db.execute("SELECT * FROM tickets WHERE channel_id=? AND status IN ('open','close_requested')",(channel_id,)).fetchone()
            if not row:return None
            now=utcnow().isoformat()
            db.execute("UPDATE tickets SET status='closed', closed_at=?, close_reason=? WHERE ticket_id=?",(now,reason,row['ticket_id']))
            db.execute("INSERT INTO audit_log(guild_id,ticket_id,actor_id,action,metadata,created_at) VALUES(?,?,?,?,?,?)",(row['guild_id'],row['ticket_id'],0,'channel_missing',json.dumps({'channel_id':channel_id}),now))
            return row['ticket_id']
    def finalize_ticket_close(self, ticket_id, *, closed_by, reason, closed_at, region, issue, transcript_filename):
        """Atomically mark a ticket closed and record its archive/audit row."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute("SELECT guild_id, status FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
                if not row or row['status'] not in ('open', 'close_requested'):
                    db.execute('COMMIT')
                    return False
                changed = db.execute(
                    "UPDATE tickets SET status='closed', closed_at=?, closed_by=?, close_reason=?, transcript_filename=? "
                    "WHERE ticket_id=? AND status IN ('open','close_requested')",
                    (closed_at, closed_by, reason[:1000], transcript_filename, ticket_id),
                ).rowcount
                if changed:
                    db.execute(
                        'INSERT INTO closed_tickets(guild,region,issue,closed_by,reason,closed_at) VALUES(?,?,?,?,?,?)',
                        (row['guild_id'], region, issue, closed_by, reason[:1000], closed_at),
                    )
                    db.execute(
                        'INSERT INTO audit_log(guild_id,ticket_id,actor_id,action,metadata,created_at) VALUES(?,?,?,?,?,?)',
                        (row['guild_id'], ticket_id, closed_by, 'closed',
                         json.dumps({'region': region, 'issue': issue, 'reason': reason[:1000]}), closed_at),
                    )
                db.execute('COMMIT')
                return bool(changed)
            except Exception:
                db.execute('ROLLBACK')
                raise

    def close_orphaned_ticket(self, ticket_id):
        """Close an open ticket whose Discord channel was confirmed missing."""
        now = utcnow().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute("SELECT guild_id, channel_id, status FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
                if not row or row['status'] not in ('open', 'close_requested'):
                    db.execute('COMMIT')
                    return False
                changed = db.execute(
                    "UPDATE tickets SET status='closed', closed_at=?, close_reason=? "
                    "WHERE ticket_id=? AND status IN ('open','close_requested')",
                    (now, 'Channel missing (auto-cleaned)', ticket_id),
                ).rowcount
                if changed:
                    db.execute(
                        'INSERT INTO audit_log(guild_id,ticket_id,actor_id,action,metadata,created_at) VALUES(?,?,?,?,?,?)',
                        (row['guild_id'], ticket_id, 0, 'channel_missing_cleanup',
                         json.dumps({'channel_id': row['channel_id']}), now),
                    )
                db.execute('COMMIT')
                return bool(changed)
            except Exception:
                db.execute('ROLLBACK')
                raise

    def request_ticket_close(self, ticket_id: str, requested_by: int) -> bool:
        """Set a close request only if the ticket is still open (idempotent under races)."""
        query = (
            "UPDATE tickets SET status='close_requested', close_requested_by=? "
            "WHERE ticket_id=? AND status='open'"
        )
        with self.connect() as db:
            changed = db.execute(query, (requested_by, ticket_id)).rowcount
        return bool(changed)

    def set_claim(self, ticket_id: str, claimed_by: int | None) -> None:
        """Set or clear a ticket's current staff assignee."""
        self.update_ticket(ticket_id, claimed_by=claimed_by)

    def staff_role_ids(self, guild_id: int) -> list[int]:
        """Return configured extra ticket-viewer roles in their saved order."""
        row = self.config(guild_id)
        if not row:
            return []
        return [
            int(row[f"staff_role_{slot}"])
            for slot in range(1, 11)
            if row[f"staff_role_{slot}"]
        ]

    def add_staff_role(self, guild_id: int, role_id: int) -> bool:
        """Add a ticket-viewer role atomically, preserving concurrent selections."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO guild_config(guild_id) VALUES(?) ON CONFLICT DO NOTHING",
                    (guild_id,),
                )
                row = db.execute(
                    "SELECT * FROM guild_config WHERE guild_id=?",
                    (guild_id,),
                ).fetchone()
                current_roles = [
                    int(row[f"staff_role_{slot}"])
                    for slot in range(1, 11)
                    if row[f"staff_role_{slot}"]
                ]
                if role_id in current_roles:
                    db.execute("COMMIT")
                    return False
                if len(current_roles) >= 10:
                    raise ValueError("You can configure up to 10 staff roles.")

                free_slot = next(
                    slot
                    for slot in range(1, 11)
                    if not row[f"staff_role_{slot}"]
                )
                db.execute(
                    f"UPDATE guild_config SET staff_role_{free_slot}=? WHERE guild_id=?",
                    (role_id, guild_id),
                )
                db.execute("COMMIT")
                return True
            except Exception:
                db.execute("ROLLBACK")
                raise

    def remove_staff_role(self, guild_id: int, role_id: int) -> bool:
        """Remove an extra ticket-viewer role while keeping other slots unchanged."""
        row = self.config(guild_id)
        if not row:
            return False

        for slot in range(1, 11):
            if row[f"staff_role_{slot}"] == role_id:
                self.upsert_config(guild_id, **{f"staff_role_{slot}": None})
                return True
        return False
    def anti_links_allowed_role_ids(self,guild_id):
        row=self.config(guild_id);return safe_json_list(row['anti_links_allowed_roles'] if row else '[]',int)
    def upsert_anti_links_allowed_role(self,guild_id,role_id,enabled):
        roles=self.anti_links_allowed_role_ids(guild_id)
        if enabled:
            if role_id not in roles:roles.append(role_id)
        else:roles=[v for v in roles if v!=role_id]
        self.upsert_config(guild_id,anti_links_allowed_roles=json.dumps(roles[:100]));return role_id in roles
    def configured_permission_role_ids(self,guild_id):
        row=self.config(guild_id)
        return [int(row[n]) for n in ('owner_role','co_owner_role','head_admin_role','admin_role','moderator_role') if row and row[n]]

    def ticket_access_role_ids(self, guild_id):
        """Role IDs allowed to view tickets, deduplicated in permission-first order."""
        return list(dict.fromkeys(self.configured_permission_role_ids(guild_id) + self.staff_role_ids(guild_id)))
