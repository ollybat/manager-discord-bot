"""Stdlib regression tests for the SQLite core and source safety checks."""
from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from grid_a1.database import Database
from grid_a1.embeds import inactivity_indicator
from grid_a1.utils import detected_external_links, inactivity_custom_id, is_http_url, safe_json_list, sanitize_channel_name, ticket_status_title


ROOT = Path(__file__).parent


class CoreTests(unittest.TestCase):
    def test_sanitize_channel_name_collapses_dashes(self):
        self.assertEqual(sanitize_channel_name("EU", "Bug / Links", "A--User", "ABC123"), "eu-bug-links-a-user-abc123")

    def test_safe_json_list_malformed_and_typed_data(self):
        self.assertEqual(safe_json_list("not-json", int), [])
        self.assertEqual(safe_json_list('{"not": "a list"}', int), [])
        self.assertEqual(safe_json_list('[1, true, "2", null, "bad"]', int), [1, 2])

    def test_fresh_and_repeat_migration_preserve_ticket_and_config(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.upsert_config(42, owner_role=101, co_owner_role=102, head_admin_role=103, admin_role=104, moderator_role=105, report_channel=9900)
            database.create_ticket(ticket_id="ABC123", guild_id=42, channel_id=9001, owner_id=7001,
                                   issue="links", region="EU", opened_at="2024-01-01T00:00:00+00:00",
                                   last_activity_at="2024-01-01T00:00:00+00:00")
            database.migrate()
            self.assertEqual(database.ticket("ABC123")["issue"], "links")
            self.assertEqual(database.config(42)["owner_role"], 101)
            self.assertEqual(database.config(42)["report_channel"], 9900)
            self.assertEqual(database.startup_check()["schema_version"], 17)

    def test_one_open_ticket_constraint(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            values = dict(guild_id=42, owner_id=7001, issue="first", region="EU", opened_at="now", last_activity_at="now")
            database.create_ticket(ticket_id="ONE", channel_id=1, **values)
            with self.assertRaises(sqlite3.IntegrityError):
                database.create_ticket(ticket_id="TWO", channel_id=2, **values)
            database.update_ticket("ONE", status="closed")
            database.create_ticket(ticket_id="TWO", channel_id=2, **values)

    def test_orphaned_ticket_cleanup_is_audited_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.create_ticket(ticket_id="ORPHAN", guild_id=42, channel_id=9002, owner_id=7002,
                                   issue="missing channel", region="EU", opened_at="now", last_activity_at="now")
            self.assertTrue(database.close_orphaned_ticket("ORPHAN"))
            self.assertFalse(database.close_orphaned_ticket("ORPHAN"))
            row = database.ticket("ORPHAN")
            self.assertEqual(row["status"], "closed")
            self.assertEqual(row["close_reason"], "Channel missing (auto-cleaned)")
            with database.connect() as db:
                audit = db.execute("SELECT action FROM audit_log WHERE ticket_id='ORPHAN' AND action='channel_missing_cleanup'").fetchone()
            self.assertEqual(audit["action"], "channel_missing_cleanup")

    def test_ticket_close_is_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.create_ticket(ticket_id="CLOSE1", guild_id=42, channel_id=9003, owner_id=7003,
                                   issue="general", region="EU", opened_at="now", last_activity_at="now")
            values = dict(closed_by=7004, reason="Resolved", closed_at="2025-01-01T00:00:00+00:00",
                          region="EU", issue="general", transcript_filename="CLOSE1.html")
            self.assertTrue(database.finalize_ticket_close("CLOSE1", **values))
            self.assertFalse(database.finalize_ticket_close("CLOSE1", **values))
            row = database.ticket("CLOSE1")
            self.assertEqual(row["status"], "closed")
            self.assertEqual(row["transcript_filename"], "CLOSE1.html")
            self.assertEqual(database.closed_count(42, "EU"), 1)
            with database.connect() as db:
                audit_count = db.execute("SELECT COUNT(*) FROM audit_log WHERE ticket_id='CLOSE1' AND action='closed'").fetchone()[0]
            self.assertEqual(audit_count, 1)

    def test_ticket_access_roles_deduplicate_and_keep_permission_roles_first(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.upsert_config(42, owner_role=11, co_owner_role=12, head_admin_role=13, admin_role=14, moderator_role=15)
            database.add_staff_role(42, 15)
            database.add_staff_role(42, 16)
            self.assertEqual(database.ticket_access_role_ids(42), [11, 12, 13, 14, 15, 16])

    def test_inactivity_component_ids_are_ticket_specific_and_bounded(self):
        first = inactivity_custom_id("keep", "A1B2C3D4")
        second = inactivity_custom_id("keep", "E5F6G7H8")
        self.assertNotEqual(first, second)
        self.assertEqual(first, "grid-a1:inactive:A1B2C3D4:keep")
        self.assertLessEqual(len(inactivity_custom_id("close", "x" * 200)), 100)
        with self.assertRaises(ValueError):
            inactivity_custom_id("unknown", "A1B2C3D4")

    def test_inactivity_indicator_accepts_legacy_naive_timestamps(self):
        from datetime import datetime, timezone
        naive_now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        indicator, duration = inactivity_indicator(naive_now, 24)
        self.assertEqual(indicator, "🟢")
        self.assertTrue(duration.endswith("m"))

    def test_ticket_status_title_is_idempotent_and_bounded(self):
        once = ticket_status_title("💜 Support Ticket", "🟢")
        twice = ticket_status_title(once, "🟡")
        self.assertEqual(once, "🟢 🎫 Support Ticket")
        self.assertEqual(twice, "🟡 🎫 Support Ticket")
        self.assertEqual(ticket_status_title("🟢 🎫 " + "x" * 400, "🔴")[:2], "🔴 ")
        self.assertLessEqual(len(ticket_status_title("🎫 " + "x" * 400, "🔴")), 256)

    def test_schema_urgent_columns_and_configured_permission_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.upsert_config(42, owner_role=11, co_owner_role=12, head_admin_role=13, admin_role=14, moderator_role=15)
            database.create_ticket(ticket_id="URGENT", guild_id=42, channel_id=3, owner_id=9, issue="x", region="EU", opened_at="now", last_activity_at="now")
            database.update_ticket("URGENT", urgent_at="now", urgent_by=99)
            config_columns = {row[1] for row in database.connect().execute("PRAGMA table_info(guild_config)")}
            ticket_columns = {row[1] for row in database.connect().execute("PRAGMA table_info(tickets)")}
            self.assertTrue({"urgent_at", "urgent_by"} <= config_columns)
            self.assertTrue({"urgent_at", "urgent_by"} <= ticket_columns)
            self.assertEqual(database.configured_permission_role_ids(42), [11, 12, 13, 14, 15])

    def test_deferred_interactions_never_answer_with_initial_response(self):
        def call_path(call):
            parts = []
            current = call.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))

        def scoped_nodes(root):
            pending = list(ast.iter_child_nodes(root))
            while pending:
                child = pending.pop()
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                yield child
                pending.extend(ast.iter_child_nodes(child))

        for path in (ROOT / "grid_a1" / "bot.py", ROOT / "grid_a1" / "views.py", ROOT / "grid_a1" / "dashboard_setup.py", ROOT / "grid_a1" / "tickets.py", ROOT / "grid_a1" / "commands.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                calls = [child for child in scoped_nodes(node) if isinstance(child, ast.Call)]
                defer_lines = [call.lineno for call in calls if call_path(call).endswith("response.defer")]
                response_lines = [call.lineno for call in calls if call_path(call).endswith("response.send_message")]
                for defer_line in defer_lines:
                    self.assertFalse(any(line > defer_line for line in response_lines), f"{path}:{node.name} sends an initial response after deferring")

    def test_report_proof_urls_require_absolute_http_or_https(self):
        self.assertTrue(is_http_url("https://evidence.example/report/123"))
        self.assertTrue(is_http_url("http://example.org/file.png"))
        self.assertFalse(is_http_url("javascript:alert(1)"))
        self.assertFalse(is_http_url("relative/path"))
        self.assertFalse(is_http_url("https://"))

    def test_report_command_is_public_and_accepts_link_or_uploaded_proof(self):
        source = (ROOT / "grid_a1" / "bot.py").read_text(encoding="utf-8")
        start = source.index('@bot.tree.command(name="report"')
        end = source.index('@bot.tree.command(name="dashboard"', start)
        command = source[start:end]
        self.assertIn("member: discord.Member", command)
        self.assertIn("proof_link: str | None", command)
        self.assertIn("proof_file: discord.Attachment | None", command)
        self.assertNotIn("has_permissions", command)
        self.assertIn("report_channel", command)

    def test_dashboard_configuration_uses_native_dropdowns(self):
        source = (ROOT / "grid_a1" / "dashboard_setup.py").read_text(encoding="utf-8")
        self.assertIn("discord.ui.RoleSelect", source)
        self.assertIn("discord.ui.ChannelSelect", source)
        self.assertIn("class TicketSetupWizardView", source)
        self.assertIn("class WelcomeStepOneView", source)
        self.assertIn("class ReportChannelWizardView", source)

    def test_help_does_not_advertise_removed_commands_or_anti_link_options(self):
        source = (ROOT / "grid_a1" / "commands.py").read_text(encoding="utf-8")
        for stale in ("`/staff`", "whitelist domains", "bypass roles", "allowed link roles"):
            self.assertNotIn(stale, source)
        self.assertIn("configure enabled, action, and log channel", source)

    def test_anti_links_source_checks(self):
        self.assertTrue(detected_external_links("visit https://example.com or discord.gg/example"))
        self.assertEqual(detected_external_links("example.com is okay", ("example.com",)), [])
        for path in sorted((ROOT / "grid_a1").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            self.assertNotIn("[more lines in file", source.lower(), str(path))
            self.assertNotIn("todo: implement", source.lower(), str(path))
            self.assertIsNotNone(tree)


if __name__ == "__main__":
    unittest.main()


def _json_smoke(value):
    return json.loads(value)
