"""Optional PostgreSQL adapter helpers.

This module is intentionally not selected by the bot runtime. It provides a
small, explicit connection factory for operators who want to port the schema;
SQLite remains the production default and DATABASE_PATH is never changed.
"""
from __future__ import annotations

import os
from typing import Any


def postgres_url() -> str | None:
    value = os.getenv("DATABASE_URL", "").strip()
    return value or None


def connect(url: str | None = None) -> Any:
    """Return a psycopg connection, failing clearly when the optional extra is absent."""
    target = url or postgres_url()
    if not target:
        raise ValueError("DATABASE_URL is not configured")
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on optional environment
        raise RuntimeError("PostgreSQL support requires psycopg[binary]>=3.1,<4") from exc
    return psycopg.connect(target)


def schema_sql() -> str:
    """Portable baseline schema; execute in a transaction on a new PostgreSQL DB."""
    return """
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS guild_config (guild_id BIGINT PRIMARY KEY, panel_channel BIGINT, panel_message BIGINT, panel_fingerprint TEXT, logs_channel BIGINT, ticket_category BIGINT, inactivity_hours INTEGER NOT NULL DEFAULT 24, owner_role BIGINT, co_owner_role BIGINT, head_admin_role BIGINT, admin_role BIGINT, moderator_role BIGINT);
CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, guild_id BIGINT NOT NULL, channel_id BIGINT UNIQUE NOT NULL, owner_id BIGINT NOT NULL, issue TEXT NOT NULL, region TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', opened_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, closed_at TEXT, closed_by BIGINT, close_reason TEXT);
CREATE TABLE IF NOT EXISTS audit_log (id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, ticket_id TEXT, actor_id BIGINT NOT NULL, action TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS closed_tickets (id BIGSERIAL PRIMARY KEY, guild BIGINT NOT NULL, region TEXT NOT NULL, issue TEXT NOT NULL, closed_by BIGINT NOT NULL, reason TEXT NOT NULL, closed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS poll_settings (guild_id BIGINT PRIMARY KEY, channel_id BIGINT, default_duration_hours INTEGER NOT NULL DEFAULT 24);
CREATE TABLE IF NOT EXISTS polls (poll_id TEXT PRIMARY KEY, guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, message_id BIGINT, creator_id BIGINT NOT NULL, question TEXT NOT NULL, options_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, ends_at TEXT NOT NULL, ended_at TEXT);
CREATE TABLE IF NOT EXISTS poll_votes (poll_id TEXT NOT NULL REFERENCES polls(poll_id) ON DELETE CASCADE, voter_id BIGINT NOT NULL, option_index INTEGER NOT NULL, voted_at TEXT NOT NULL, PRIMARY KEY (poll_id, voter_id));
"""


def migration_plan() -> list[str]:
    """Describe, without executing, the safe migration boundary."""
    return ["create missing PostgreSQL tables", "copy only known rows in a transaction", "never modify or delete the SQLite source"]
