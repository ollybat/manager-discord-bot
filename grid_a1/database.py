from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from .utils import utcnow, safe_json_list

SCHEMA_VERSION = 11

class Database:
    def __init__(self, path: Path): self.path = path
    def connect(self):
        db = sqlite3.connect(self.path); db.row_factory = sqlite3.Row; db.execute('PRAGMA foreign_keys=ON'); return db
    def migrate(self) -> None:
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY, panel_channel INTEGER, panel_message INTEGER,
                logs_channel INTEGER, ticket_category INTEGER, inactivity_hours INTEGER NOT NULL DEFAULT 24,
                welcome_channel INTEGER, verify_channel INTEGER, link_channel INTEGER,
                bot_commands_channel INTEGER, shop_channel INTEGER, verify_panel_channel INTEGER, verify_panel_message INTEGER, verify_role INTEGER, staff_role_1 INTEGER, staff_role_2 INTEGER, staff_role_3 INTEGER, staff_role_4 INTEGER, staff_role_5 INTEGER, staff_role_6 INTEGER, staff_role_7 INTEGER, staff_role_8 INTEGER, staff_role_9 INTEGER, staff_role_10 INTEGER, wipefeed_enabled INTEGER NOT NULL DEFAULT 0, wipefeed_channel INTEGER, urgent_at TEXT, urgent_by INTEGER, owner_role INTEGER, moderator_role INTEGER, admin_role INTEGER, co_owner_role INTEGER, head_admin_role INTEGER, anti_links_enabled INTEGER NOT NULL DEFAULT 0, anti_links_log_channel INTEGER, anti_links_action TEXT NOT NULL DEFAULT 'delete_warn', anti_links_whitelist_domains TEXT NOT NULL DEFAULT '[]', anti_links_bypass_roles TEXT NOT NULL DEFAULT '[]')''')
            db.execute('''CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER UNIQUE NOT NULL,
                owner_id INTEGER NOT NULL, issue TEXT NOT NULL, region TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', claimed_by INTEGER, close_requested_by INTEGER,
                opened_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, inactivity_notice_at TEXT,
                owner_left INTEGER NOT NULL DEFAULT 0, auto_close_at TEXT, auto_close_reason TEXT,
                closed_at TEXT, closed_by INTEGER, close_reason TEXT, transcript_filename TEXT)''')
            config_existing = {r[1] for r in db.execute('PRAGMA table_info(guild_config)')}
            config_columns = {
                **{name: 'INTEGER' for name in ('verify_panel_channel','verify_panel_message','verify_role', *[f'staff_role_{n}' for n in range(1, 11)], 'wipefeed_enabled','wipefeed_channel','urgent_by','owner_role','moderator_role','admin_role','co_owner_role','head_admin_role','anti_links_enabled','anti_links_log_channel')},
                'urgent_at': 'TEXT', 'anti_links_action': "TEXT NOT NULL DEFAULT 'delete_warn'",
                'anti_links_whitelist_domains': "TEXT NOT NULL DEFAULT '[]'", 'anti_links_bypass_roles': "TEXT NOT NULL DEFAULT '[]'", 'anti_links_allowed_roles': "TEXT NOT NULL DEFAULT '[]'",
            }
            for name, definition in config_columns.items():
                if name not in config_existing: db.execute(f'ALTER TABLE guild_config ADD COLUMN {name} {definition}')
            existing = {r[1] for r in db.execute('PRAGMA table_info(tickets)')}
            for name, definition in {'inactivity_notice_at':'TEXT','owner_left':'INTEGER NOT NULL DEFAULT 0','auto_close_at':'TEXT','auto_close_reason':'TEXT'}.items():
                if name not in existing: db.execute(f'ALTER TABLE tickets ADD COLUMN {name} {definition}')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS only_one_open_ticket ON tickets(guild_id, owner_id) WHERE status IN ('open','close_requested')")
            db.execute('''CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, ticket_id TEXT, actor_id INTEGER NOT NULL, action TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS closed_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, region TEXT NOT NULL, issue TEXT NOT NULL, closed_by INTEGER NOT NULL, reason TEXT NOT NULL, closed_at TEXT NOT NULL)''')
            db.execute("UPDATE guild_config SET anti_links_enabled=COALESCE(anti_links_enabled, 0), anti_links_action=COALESCE(NULLIF(anti_links_action, ''), 'delete_warn'), anti_links_whitelist_domains=COALESCE(NULLIF(anti_links_whitelist_domains, ''), '[]'), anti_links_bypass_roles=COALESCE(NULLIF(anti_links_bypass_roles, ''), '[]'), anti_links_allowed_roles=COALESCE(NULLIF(anti_links_allowed_roles, ''), '[]')")
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES (?,?)', (SCHEMA_VERSION, utcnow().isoformat()))
    def config(self, guild_id):
        with self.connect() as db: return db.execute('SELECT * FROM guild_config WHERE guild_id=?',(guild_id,)).fetchone()
    def upsert_config(self, guild_id, **values: Any):
        allowed={'panel_channel','panel_message','logs_channel','ticket_category','inactivity_hours','welcome_channel','verify_channel','link_channel','bot_commands_channel','shop_channel','verify_panel_channel','verify_panel_message','verify_role','staff_role_1','staff_role_2','staff_role_3','staff_role_4','staff_role_5','staff_role_6','staff_role_7','staff_role_8','staff_role_9','staff_role_10','wipefeed_enabled','wipefeed_channel','owner_role','moderator_role','admin_role','co_owner_role','head_admin_role','anti_links_enabled','anti_links_log_channel','anti_links_action','anti_links_whitelist_domains','anti_links_bypass_roles','anti_links_allowed_roles'}
        if not set(values)<=allowed: raise ValueError(f'unknown config field: {set(values)-allowed}')
        with self.connect() as db:
            db.execute('INSERT INTO guild_config(guild_id) VALUES(?) ON CONFLICT DO NOTHING',(guild_id,))
            for key,value in values.items(): db.execute(f'UPDATE guild_config SET {key}=? WHERE guild_id=?',(value,guild_id))
    def open_ticket_for_owner(self,guild_id,owner_id):
        with self.connect() as db: return db.execute("SELECT * FROM tickets WHERE guild_id=? AND owner_id=? AND status IN ('open','close_requested')",(guild_id,owner_id)).fetchone()
    def open_tickets(self,guild_id):
        with self.connect() as db: return db.execute("SELECT * FROM tickets WHERE guild_id=? AND status IN ('open','close_requested')",(guild_id,)).fetchall()
    def ticket(self,ticket_id):
        with self.connect() as db: return db.execute('SELECT * FROM tickets WHERE ticket_id=?',(ticket_id,)).fetchone()
    def create_ticket(self,**values):
        with self.connect() as db:
            db.execute('INSERT INTO tickets(ticket_id,guild_id,channel_id,owner_id,issue,region,opened_at,last_activity_at) VALUES(:ticket_id,:guild_id,:channel_id,:owner_id,:issue,:region,:opened_at,:last_activity_at)',values)
            db.execute('INSERT INTO audit_log(guild_id,ticket_id,actor_id,action,created_at) VALUES(?,?,?,?,?)',(values['guild_id'],values['ticket_id'],values['owner_id'],'opened',utcnow().isoformat()))
    def update_ticket(self,ticket_id,**values):
        allowed={'status','claimed_by','close_requested_by','last_activity_at','inactivity_notice_at','owner_left','auto_close_at','auto_close_reason','closed_at','closed_by','close_reason','transcript_filename','urgent_at','urgent_by'}
        if not set(values)<=allowed: raise ValueError(f'unknown ticket fields: {set(values)-allowed}')
        with self.connect() as db: db.execute(f"UPDATE tickets SET {', '.join(k+'=?' for k in values)} WHERE ticket_id=?",(*values.values(),ticket_id))
    def audit(self,guild_id,ticket_id,actor_id,action,metadata='{}'):
        with self.connect() as db: db.execute('INSERT INTO audit_log(guild_id,ticket_id,actor_id,action,metadata,created_at) VALUES(?,?,?,?,?,?)',(guild_id,ticket_id,actor_id,action,metadata,utcnow().isoformat()))
    def closed_count(self,guild_id,region=None):
        with self.connect() as db:
            q='SELECT COUNT(*) FROM closed_tickets WHERE guild=?'+(' AND region=?' if region else '')
            return int(db.execute(q,(guild_id,region) if region else (guild_id,)).fetchone()[0])
    def open_counts(self,guild_id):
        with self.connect() as db: return {r['region']:int(r['n']) for r in db.execute("SELECT region,COUNT(*) n FROM tickets WHERE guild_id=? AND status IN ('open','close_requested') GROUP BY region",(guild_id,)).fetchall()}
    def mark_owner_left(self,guild_id,owner_id):
        with self.connect() as db:
            rows=db.execute("SELECT ticket_id FROM tickets WHERE guild_id=? AND owner_id=? AND status IN ('open','close_requested')",(guild_id,owner_id)).fetchall()
            db.execute("UPDATE tickets SET owner_left=1,auto_close_reason='owner_left' WHERE guild_id=? AND owner_id=? AND status IN ('open','close_requested')",(guild_id,owner_id))
            return [r['ticket_id'] for r in rows]
    def mark_activity(self,ticket_id): self.update_ticket(ticket_id,last_activity_at=utcnow().isoformat(),inactivity_notice_at=None,auto_close_at=None,auto_close_reason=None)
    def ticket_by_channel(self,channel_id):
        with self.connect() as db: return db.execute("SELECT * FROM tickets WHERE channel_id=? AND status IN ('open','close_requested')",(channel_id,)).fetchone()
    def set_claim(self,ticket_id,claimed_by): self.update_ticket(ticket_id,claimed_by=claimed_by)

    def staff_role_ids(self, guild_id):
        row = self.config(guild_id)
        return [int(row[f"staff_role_{n}"]) for n in range(1, 11) if row and row[f"staff_role_{n}"]]
    def add_staff_role(self, guild_id, role_id):
        self.upsert_config(guild_id)
        roles = self.staff_role_ids(guild_id)
        if role_id in roles: return False
        if len(roles) >= 10: raise ValueError("You can configure up to 10 staff roles.")
        row = self.config(guild_id)
        slot = next(n for n in range(1, 11) if not row[f"staff_role_{n}"])
        self.upsert_config(guild_id, **{f"staff_role_{slot}": role_id})
        return True
    def remove_staff_role(self, guild_id, role_id):
        row = self.config(guild_id)
        if not row: return False
        for n in range(1, 11):
            if row[f"staff_role_{n}"] == role_id:
                self.upsert_config(guild_id, **{f"staff_role_{n}": None}); return True
        return False

    def anti_links_allowed_role_ids(self, guild_id):
        row = self.config(guild_id)
        return safe_json_list(row['anti_links_allowed_roles'] if row else '[]', int)

    def upsert_anti_links_allowed_role(self, guild_id, role_id, enabled):
        roles = self.anti_links_allowed_role_ids(guild_id)
        if enabled:
            if role_id not in roles: roles.append(role_id)
        else:
            roles = [value for value in roles if value != role_id]
        self.upsert_config(guild_id, anti_links_allowed_roles=json.dumps(roles[:100]))
        return role_id in roles

    def configured_permission_role_ids(self, guild_id):
        row = self.config(guild_id)
        if not row: return []
        return [int(row[name]) for name in ("owner_role", "moderator_role", "admin_role", "co_owner_role", "head_admin_role") if row[name]]
