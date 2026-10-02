from __future__ import annotations
import sqlite3
import json
import secrets
from pathlib import Path
from typing import Any
from .utils import utcnow, safe_json_list
SCHEMA_VERSION = 19
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
            db.execute("""
                CREATE TABLE IF NOT EXISTS poll_settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    default_duration_hours INTEGER NOT NULL DEFAULT 24
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS polls (
                    poll_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    creator_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    options_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    ended_at TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS poll_votes (
                    poll_id TEXT NOT NULL REFERENCES polls(poll_id) ON DELETE CASCADE,
                    voter_id INTEGER NOT NULL,
                    option_index INTEGER NOT NULL,
                    voted_at TEXT NOT NULL,
                    PRIMARY KEY (poll_id, voter_id)
                )
            """)
            poll_columns = {row[1] for row in db.execute("PRAGMA table_info(polls)")}
            if "description" not in poll_columns:
                db.execute("ALTER TABLE polls ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            db.execute("""
                CREATE TABLE IF NOT EXISTS giveaways (
                    giveaway_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    creator_id INTEGER NOT NULL,
                    reward_type TEXT NOT NULL,
                    ping_role_id INTEGER,
                    winner_count INTEGER NOT NULL DEFAULT 1 CHECK (winner_count BETWEEN 1 AND 50),
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    ended_at TEXT,
                    winners_json TEXT NOT NULL DEFAULT '[]'
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_entries (
                    giveaway_id TEXT NOT NULL REFERENCES giveaways(giveaway_id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    entered_at TEXT NOT NULL,
                    PRIMARY KEY (giveaway_id, user_id)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_giveaways_guild_status ON giveaways(guild_id, status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_giveaways_due ON giveaways(status, ends_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_giveaway_entries_user ON giveaway_entries(giveaway_id, user_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_polls_guild_status ON polls(guild_id, status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_polls_due ON polls(status, ends_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_poll_votes_poll_option ON poll_votes(poll_id, option_index)")
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
            required = {"guild_config", "tickets", "audit_log", "closed_tickets", "poll_settings", "polls", "poll_votes", "giveaways", "giveaway_entries", "schema_migrations"}
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

    def poll_settings(self, guild_id: int) -> sqlite3.Row | None:
        """Return the configured poll channel and default duration for a guild."""
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM poll_settings WHERE guild_id=?",
                (guild_id,),
            ).fetchone()

    def clear_poll_channel(self, guild_id: int) -> None:
        """Return poll posting to the channel where the dashboard/create command runs."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO poll_settings(guild_id) VALUES(?) ON CONFLICT DO NOTHING",
                    (guild_id,),
                )
                db.execute("UPDATE poll_settings SET channel_id=NULL WHERE guild_id=?", (guild_id,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def upsert_poll_settings(
        self,
        guild_id: int,
        *,
        channel_id: int | None = None,
        default_duration_hours: int | None = None,
    ) -> None:
        """Atomically save the supplied poll settings while preserving omitted values."""
        if default_duration_hours is not None and not 1 <= default_duration_hours <= 168:
            raise ValueError("default poll duration must be between 1 and 168 hours")

        updates: dict[str, Any] = {}
        if channel_id is not None:
            updates["channel_id"] = channel_id
        if default_duration_hours is not None:
            updates["default_duration_hours"] = default_duration_hours

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO poll_settings(guild_id) VALUES(?) ON CONFLICT DO NOTHING",
                    (guild_id,),
                )
                for field, value in updates.items():
                    db.execute(
                        f"UPDATE poll_settings SET {field}=? WHERE guild_id=?",
                        (value, guild_id),
                    )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def create_poll(
        self,
        *,
        poll_id: str,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        question: str,
        options_json: str,
        created_at: str,
        ends_at: str,
        description: str = "",
    ) -> None:
        """Persist a poll and its audit event atomically before publishing it."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO polls "
                    "(poll_id, guild_id, channel_id, creator_id, question, description, options_json, created_at, ends_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (poll_id, guild_id, channel_id, creator_id, question, description, options_json, created_at, ends_at),
                )
                db.execute(
                    "INSERT INTO audit_log(guild_id, actor_id, action, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (guild_id, creator_id, "poll_created", json.dumps({"poll_id": poll_id}), created_at),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def set_poll_message_id(self, poll_id: str, message_id: int) -> bool:
        """Attach the published Discord message to its database poll row."""
        with self.connect() as db:
            changed = db.execute(
                "UPDATE polls SET message_id=? WHERE poll_id=? AND status='open'",
                (message_id, poll_id),
            ).rowcount
        return bool(changed)

    def poll_for_guild(self, guild_id: int, poll_id: str) -> sqlite3.Row | None:
        """Look up a poll while enforcing its guild boundary."""
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM polls WHERE guild_id=? AND poll_id=?",
                (guild_id, poll_id),
            ).fetchone()

    def polls_with_messages_all(self) -> list[sqlite3.Row]:
        """Return every published poll so ended stale controls can still be refreshed."""
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM polls WHERE message_id IS NOT NULL ORDER BY created_at"
            ).fetchall()

    def active_polls(self, guild_id: int, limit: int = 25) -> list[sqlite3.Row]:
        """Return the newest active polls for a guild dashboard."""
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM polls WHERE guild_id=? AND status='open' "
                "ORDER BY created_at DESC LIMIT ?",
                (guild_id, max(1, min(int(limit), 25))),
            ).fetchall()

    def polls_due(self, now: str) -> list[sqlite3.Row]:
        """Return open polls whose stored end time has passed."""
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM polls WHERE status='open' AND ends_at<=? ORDER BY ends_at",
                (now,),
            ).fetchall()

    def poll_options(self, poll_id: str) -> list[str]:
        """Read persisted poll choices safely, tolerating malformed legacy JSON."""
        with self.connect() as db:
            row = db.execute(
                "SELECT options_json FROM polls WHERE poll_id=?",
                (poll_id,),
            ).fetchone()
        return safe_json_list(row["options_json"] if row else "[]", str)

    def poll_results(self, poll_id: str) -> dict[int, int]:
        """Return vote counts keyed by the stored option index."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT option_index, COUNT(*) AS vote_count FROM poll_votes "
                "WHERE poll_id=? GROUP BY option_index",
                (poll_id,),
            ).fetchall()
        return {int(row["option_index"]): int(row["vote_count"]) for row in rows}

    def cast_poll_vote(
        self,
        poll_id: str,
        voter_id: int,
        option_index: int,
        voted_at: str,
    ) -> str:
        """Insert or change one member's vote, rejecting invalid or ended polls."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT guild_id, status, ends_at, options_json FROM polls WHERE poll_id=?",
                    (poll_id,),
                ).fetchone()
                if not row:
                    db.execute("COMMIT")
                    return "missing"
                if row["status"] != "open":
                    db.execute("COMMIT")
                    return "closed"

                if row["ends_at"] <= voted_at:
                    db.execute(
                        "UPDATE polls SET status='ended', ended_at=? WHERE poll_id=? AND status='open'",
                        (voted_at, poll_id),
                    )
                    db.execute(
                        "INSERT INTO audit_log(guild_id, actor_id, action, metadata, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (row["guild_id"], 0, "poll_auto_ended", json.dumps({"poll_id": poll_id}), voted_at),
                    )
                    db.execute("COMMIT")
                    return "expired"

                choices = safe_json_list(row["options_json"], str)
                if not 0 <= option_index < len(choices):
                    db.execute("COMMIT")
                    return "invalid_option"

                db.execute(
                    "INSERT INTO poll_votes(poll_id, voter_id, option_index, voted_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(poll_id, voter_id) DO UPDATE SET "
                    "option_index=excluded.option_index, voted_at=excluded.voted_at",
                    (poll_id, voter_id, option_index, voted_at),
                )
                db.execute("COMMIT")
                return "recorded"
            except Exception:
                db.execute("ROLLBACK")
                raise

    def end_poll(self, poll_id: str, ended_by: int, ended_at: str) -> bool:
        """Mark a poll ended once and record the action in the audit log."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT guild_id FROM polls WHERE poll_id=? AND status='open'",
                    (poll_id,),
                ).fetchone()
                if not row:
                    db.execute("COMMIT")
                    return False
                changed = db.execute(
                    "UPDATE polls SET status='ended', ended_at=? "
                    "WHERE poll_id=? AND status='open'",
                    (ended_at, poll_id),
                ).rowcount
                if changed:
                    action = "poll_auto_ended" if ended_by == 0 else "poll_ended"
                    db.execute(
                        "INSERT INTO audit_log(guild_id, actor_id, action, metadata, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (row["guild_id"], ended_by, action, json.dumps({"poll_id": poll_id}), ended_at),
                    )
                db.execute("COMMIT")
                return bool(changed)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def remove_poll(self, poll_id: str, removed_by: int | None = None) -> sqlite3.Row | None:
        """Delete a poll and cascade votes; optionally leave an audit record."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM polls WHERE poll_id=?",
                    (poll_id,),
                ).fetchone()
                if not row:
                    db.execute("COMMIT")
                    return None
                db.execute("DELETE FROM polls WHERE poll_id=?", (poll_id,))
                if removed_by is not None:
                    db.execute(
                        "INSERT INTO audit_log(guild_id, actor_id, action, metadata, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (row["guild_id"], removed_by, "poll_removed", json.dumps({"poll_id": poll_id}), utcnow().isoformat()),
                    )
                db.execute("COMMIT")
                return row
            except Exception:
                db.execute("ROLLBACK")
                raise

    def create_giveaway(
        self,
        *,
        giveaway_id: str,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        reward_type: str,
        ping_role_id: int | None,
        winner_count: int,
        created_at: str,
        ends_at: str,
    ) -> None:
        """Persist a giveaway and its audit event before publishing the entry panel."""
        if not 1 <= int(winner_count) <= 50:
            raise ValueError("Giveaways must have between 1 and 50 winners.")
        reward_type = reward_type.strip()
        if not reward_type or len(reward_type) > 200:
            raise ValueError("Reward type must contain 1–200 characters.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO giveaways "
                    "(giveaway_id, guild_id, channel_id, creator_id, reward_type, ping_role_id, winner_count, created_at, ends_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (giveaway_id, guild_id, channel_id, creator_id, reward_type, ping_role_id, int(winner_count), created_at, ends_at),
                )
                db.execute(
                    "INSERT INTO audit_log(guild_id, actor_id, action, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (guild_id, creator_id, "giveaway_created", json.dumps({"giveaway_id": giveaway_id, "winner_count": int(winner_count)}), created_at),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def discard_unpublished_giveaway(self, giveaway_id: str) -> bool:
        """Remove a draft if no public giveaway message was successfully linked."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT message_id FROM giveaways WHERE giveaway_id=? AND status='open'",
                    (giveaway_id,),
                ).fetchone()
                if not row or row["message_id"] is not None:
                    db.execute("COMMIT")
                    return False
                db.execute("DELETE FROM giveaways WHERE giveaway_id=?", (giveaway_id,))
                db.execute("COMMIT")
                return True
            except Exception:
                db.execute("ROLLBACK")
                raise

    def set_giveaway_message_id(self, giveaway_id: str, message_id: int) -> bool:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE giveaways SET message_id=? WHERE giveaway_id=? AND status='open'",
                (message_id, giveaway_id),
            ).rowcount
        return bool(changed)

    def giveaway_for_guild(self, guild_id: int, giveaway_id: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM giveaways WHERE guild_id=? AND giveaway_id=?",
                (guild_id, giveaway_id),
            ).fetchone()

    def open_giveaways_all(self) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM giveaways WHERE status='open' AND message_id IS NOT NULL"
            ).fetchall()

    def active_giveaways(self, guild_id: int, limit: int = 25) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM giveaways WHERE guild_id=? AND status='open' "
                "ORDER BY created_at DESC LIMIT ?",
                (guild_id, max(1, min(int(limit), 25))),
            ).fetchall()

    def giveaways_due(self, now: str) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM giveaways WHERE status='open' AND ends_at<=? ORDER BY ends_at",
                (now,),
            ).fetchall()

    def giveaway_entry_count(self, giveaway_id: str) -> int:
        with self.connect() as db:
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?",
                    (giveaway_id,),
                ).fetchone()[0]
            )

    def enter_giveaway(
        self,
        guild_id: int,
        giveaway_id: str,
        user_id: int,
        entered_at: str,
    ) -> str:
        """Record at most one entry per member while the giveaway is open."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT status, ends_at FROM giveaways WHERE guild_id=? AND giveaway_id=?",
                    (guild_id, giveaway_id),
                ).fetchone()
                if not row:
                    db.execute("COMMIT")
                    return "missing"
                if row["status"] != "open" or row["ends_at"] <= entered_at:
                    db.execute("COMMIT")
                    return "ended"
                changed = db.execute(
                    "INSERT OR IGNORE INTO giveaway_entries(giveaway_id, user_id, entered_at) VALUES(?, ?, ?)",
                    (giveaway_id, user_id, entered_at),
                ).rowcount
                db.execute("COMMIT")
                return "entered" if changed else "already_entered"
            except Exception:
                db.execute("ROLLBACK")
                raise

    def finalize_giveaway(
        self,
        guild_id: int,
        giveaway_id: str,
        ended_at: str,
        *,
        ended_by: int = 0,
        force: bool = False,
    ) -> tuple[dict[str, Any] | None, list[int], str]:
        """Atomically stop entries and randomly choose winners exactly once."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM giveaways WHERE guild_id=? AND giveaway_id=?",
                    (guild_id, giveaway_id),
                ).fetchone()
                if not row:
                    db.execute("COMMIT")
                    return None, [], "missing"
                if row["status"] != "open":
                    winners = safe_json_list(row["winners_json"], int)
                    db.execute("COMMIT")
                    return dict(row), winners, "already_ended"
                if not force and row["ends_at"] > ended_at:
                    db.execute("COMMIT")
                    return dict(row), [], "not_due"

                entrants = [
                    int(entry[0])
                    for entry in db.execute(
                        "SELECT user_id FROM giveaway_entries WHERE giveaway_id=? ORDER BY entered_at, user_id",
                        (giveaway_id,),
                    ).fetchall()
                ]
                winners = secrets.SystemRandom().sample(
                    entrants,
                    min(int(row["winner_count"]), len(entrants)),
                ) if entrants else []
                db.execute(
                    "UPDATE giveaways SET status='ended', ended_at=?, winners_json=? "
                    "WHERE giveaway_id=? AND status='open'",
                    (ended_at, json.dumps(winners), giveaway_id),
                )
                action = "giveaway_auto_ended" if ended_by == 0 else "giveaway_ended"
                db.execute(
                    "INSERT INTO audit_log(guild_id, actor_id, action, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (guild_id, ended_by, action, json.dumps({"giveaway_id": giveaway_id, "winners": winners}), ended_at),
                )
                final_row = db.execute(
                    "SELECT * FROM giveaways WHERE giveaway_id=?",
                    (giveaway_id,),
                ).fetchone()
                db.execute("COMMIT")
                return dict(final_row), winners, "ended"
            except Exception:
                db.execute("ROLLBACK")
                raise

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

    def mark_inactivity_dm_unavailable(self, ticket_id: str, noticed_at: str) -> bool:
        """Record a permanent DM failure once without scheduling an unannounced close."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT guild_id, status, inactivity_notice_at FROM tickets WHERE ticket_id=?",
                    (ticket_id,),
                ).fetchone()
                if (
                    not row
                    or row["status"] not in ("open", "close_requested")
                    or row["inactivity_notice_at"] is not None
                ):
                    db.execute("COMMIT")
                    return False
                db.execute(
                    "UPDATE tickets SET inactivity_notice_at=?, auto_close_at=NULL, "
                    "auto_close_reason='dm_unavailable' WHERE ticket_id=?",
                    (noticed_at, ticket_id),
                )
                db.execute(
                    "INSERT INTO audit_log(guild_id, ticket_id, actor_id, action, metadata, created_at) "
                    "VALUES (?, ?, 0, ?, ?, ?)",
                    (
                        row["guild_id"],
                        ticket_id,
                        "inactivity_notice_dm_unavailable",
                        json.dumps({"auto_close": False}),
                        noticed_at,
                    ),
                )
                db.execute("COMMIT")
                return True
            except Exception:
                db.execute("ROLLBACK")
                raise

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

    def assign_ticket(
        self,
        ticket_id: str,
        assignee_id: int,
        *,
        actor_id: int | None = None,
        allow_reassign: bool = False,
    ) -> tuple[str, int | None]:
        """Atomically claim or explicitly reassign an active ticket and audit it."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT guild_id, status, claimed_by FROM tickets WHERE ticket_id=?",
                    (ticket_id,),
                ).fetchone()
                if not row or row["status"] not in ("open", "close_requested"):
                    db.execute("COMMIT")
                    return "inactive", None

                current_assignee = row["claimed_by"]
                if current_assignee == assignee_id:
                    db.execute("COMMIT")
                    return "already_assigned", current_assignee
                if current_assignee is not None and not allow_reassign:
                    db.execute("COMMIT")
                    return "already_claimed", current_assignee

                changed = db.execute(
                    "UPDATE tickets SET claimed_by=? WHERE ticket_id=? "
                    "AND status IN ('open','close_requested')",
                    (assignee_id, ticket_id),
                ).rowcount
                if not changed:
                    db.execute("COMMIT")
                    return "inactive", None

                action = "ticket_reassigned" if current_assignee is not None else "ticket_claimed"
                db.execute(
                    "INSERT INTO audit_log(guild_id, ticket_id, actor_id, action, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["guild_id"],
                        ticket_id,
                        assignee_id if actor_id is None else actor_id,
                        action,
                        json.dumps({"previous_assignee": current_assignee, "new_assignee": assignee_id}),
                        utcnow().isoformat(),
                    ),
                )
                db.execute("COMMIT")
                return "assigned", current_assignee
            except Exception:
                db.execute("ROLLBACK")
                raise

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
