"""Stdlib regression tests for the SQLite core and source safety checks."""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import discord
from pathlib import Path

from grid_a1.config import Settings, validate_runtime
from grid_a1.database import Database
from grid_a1.embeds import inactivity_indicator
from grid_a1.utils import active_ticket_owner, detected_external_links, inactivity_custom_id, is_http_url, safe_json_list, sanitize_channel_name, ticket_status_title
from grid_a1.views import DashboardView, TicketControls


ROOT = Path(__file__).parent


class CoreTests(unittest.TestCase):
    def test_sanitize_channel_name_collapses_dashes(self):
        self.assertEqual(sanitize_channel_name("EU", "Bug / Links", "A--User", "ABC123"), "eu-bug-links-a-user-abc123")

    def test_ticket_owner_buttons_reject_staff_and_closed_tickets(self):
        open_row = {"status": "open", "owner_id": 42}
        close_requested = {"status": "close_requested", "owner_id": 42}
        closed = {"status": "closed", "owner_id": 42}
        self.assertTrue(active_ticket_owner(open_row, 42))
        self.assertTrue(active_ticket_owner(close_requested, 42))
        self.assertFalse(active_ticket_owner(open_row, 99))
        self.assertFalse(active_ticket_owner(closed, 42))
        self.assertFalse(active_ticket_owner(None, 42))

    def test_safe_json_list_malformed_and_typed_data(self):
        self.assertEqual(safe_json_list("not-json", int), [])
        self.assertEqual(safe_json_list('{"not": "a list"}', int), [])
        self.assertEqual(safe_json_list('[1, true, "2", null, "bad"]', int), [1, 2])

    def test_railway_database_path_must_be_inside_the_attached_volume(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "token", "OWNER_ID": "1", "RAILWAY_VOLUME_MOUNT_PATH": "/data"}, clear=True):
            self.assertEqual(Settings.from_env().database_path, Path("/data/manager.sqlite3"))
        settings = Settings("token", "!", Path("/data/manager.sqlite3"), 1, "INFO")
        with patch.dict(os.environ, {"RAILWAY_SERVICE_ID": "service"}, clear=True):
            problems = validate_runtime(settings)
            self.assertTrue(any("persistent volume is not detected" in problem for problem in problems))
        with patch.dict(os.environ, {"RAILWAY_SERVICE_ID": "service", "RAILWAY_VOLUME_MOUNT_PATH": "/data"}, clear=True):
            self.assertFalse(any("Railway" in problem or "DATABASE_PATH" in problem for problem in validate_runtime(settings)))
        wrong_path = Settings("token", "!", Path("/app/manager.sqlite3"), 1, "INFO")
        with patch.dict(os.environ, {"RAILWAY_SERVICE_ID": "service", "RAILWAY_VOLUME_MOUNT_PATH": "/data"}, clear=True):
            self.assertTrue(any("outside Railway's mounted volume" in problem for problem in validate_runtime(wrong_path)))

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

    def test_ticket_close_request_is_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.create_ticket(ticket_id="REQCLOSE", guild_id=42, channel_id=9011, owner_id=7004, issue="general", region="EU", opened_at="now", last_activity_at="now")
            self.assertTrue(database.request_ticket_close("REQCLOSE", 7004))
            self.assertFalse(database.request_ticket_close("REQCLOSE", 7004))
            self.assertEqual(database.ticket("REQCLOSE")["status"], "close_requested")
            database.update_ticket("REQCLOSE", status="closed")
            self.assertFalse(database.request_ticket_close("REQCLOSE", 7004))

    def test_owner_keep_open_clears_close_request_and_auto_close(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.create_ticket(ticket_id="KEEP", guild_id=42, channel_id=9010, owner_id=7003, issue="general", region="EU", opened_at="now", last_activity_at="old")
            database.update_ticket("KEEP", status="close_requested", close_requested_by=7003, inactivity_notice_at="old", auto_close_at="later", auto_close_reason="owner_left")
            self.assertTrue(database.keep_ticket_open("KEEP"))
            row = database.ticket("KEEP")
            self.assertEqual(row["status"], "open")
            self.assertIsNone(row["close_requested_by"])
            self.assertIsNone(row["inactivity_notice_at"])
            self.assertIsNone(row["auto_close_at"])
            self.assertIsNone(row["auto_close_reason"])
            self.assertFalse(database.keep_ticket_open("missing"))

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

    def test_dashboard_server_owner_can_initialize_before_roles_are_configured(self):
        bot_source = (ROOT / "grid_a1" / "bot.py").read_text(encoding="utf-8")
        views = (ROOT / "grid_a1" / "views.py").read_text(encoding="utf-8")
        command_access = bot_source.split("def _dashboard_access", 1)[1].split("def dashboard_access", 1)[0]
        view_access = views.split("def authorized", 1)[1].split("async def interaction_check", 1)[0]
        self.assertLess(command_access.index("interaction.user.id == interaction.guild.owner_id"), command_access.index("config = bot.database.config"))
        self.assertLess(view_access.index("member.id == guild.owner_id"), view_access.index("config = self.database.config"))

    def test_dashboard_is_simple_and_configure_button_follows_selection(self):
        view = DashboardView(object(), None)
        selectors = [item for item in view.children if isinstance(item, discord.ui.Select)]
        buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
        self.assertEqual(len(selectors), 1)
        self.assertEqual({item.custom_id for item in buttons}, {"grid-a1:dashboard:configure", "grid-a1:dashboard:home", "grid-a1:dashboard:close"})
        configure = next(item for item in buttons if item.custom_id == "grid-a1:dashboard:configure")
        self.assertTrue(configure.disabled)
        self.assertEqual(configure.label, "Choose an area")
        view.selected_module = "ticket"
        view._sync_configure_button()
        self.assertFalse(configure.disabled)
        self.assertEqual(configure.label, "Set up section")
        view.selected_module = "status"
        view._sync_configure_button()
        self.assertTrue(configure.disabled)
        self.assertEqual(configure.label, "View only")
        view.selected_module = "permission"
        view.viewer_id = 200
        view.guild_owner_id = 100
        view._sync_configure_button()
        self.assertTrue(configure.disabled)
        view.viewer_id = 100
        view._sync_configure_button()
        self.assertFalse(configure.disabled)
        setup_source = (ROOT / "grid_a1" / "dashboard_setup.py").read_text(encoding="utf-8")
        for wizard in view.CONFIG_WIZARDS.values():
            self.assertIn(f"class {wizard}(", setup_source)

    def test_dashboard_configuration_uses_native_dropdowns(self):
        source = (ROOT / "grid_a1" / "dashboard_setup.py").read_text(encoding="utf-8")
        self.assertIn("discord.ui.RoleSelect", source)
        self.assertIn("discord.ui.ChannelSelect", source)
        self.assertIn("class TicketSetupWizardView", source)
        self.assertIn("class WelcomeStepOneView", source)
        self.assertIn("class ReportChannelWizardView", source)

    def test_support_panel_uses_requested_copy_and_keeps_issue_picker(self):
        embeds = (ROOT / "grid_a1" / "embeds.py").read_text(encoding="utf-8")
        views = (ROOT / "grid_a1" / "views.py").read_text(encoding="utf-8")
        panel = embeds.split("def support_panel", 1)[1].split("def inactivity_indicator", 1)[0]
        for label in ("Ticket General", "Ticket Base", "Ticket Clan", "Ticket Shop", "Ticket Raid", "Ticket Bug", "Support Status", "Estimated Help Time:", "12 mins"):
            self.assertIn(label, panel)
        self.assertIn("inline=True", panel)
        self.assertIn("sum(counts.values())", panel)
        self.assertIn("class TicketTypeSelect", views)
        self.assertIn("Select your issue type ...", views)
        self.assertNotIn('label="How it works"', views)

    def test_ticket_controls_require_active_records_and_offer_owner_closure_request(self):
        controls = TicketControls(object())
        ids = [item.custom_id for item in controls.children if isinstance(item, discord.ui.Button)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("grid-a1:ticket:request-close", ids)
        buttons = [item for item in controls.children if isinstance(item, discord.ui.Button)]
        self.assertIn("Staff close", [item.label for item in buttons])
        self.assertTrue(all(sum(button.row == row for button in buttons) <= 5 for row in (0, 1)))
        source = (ROOT / "grid_a1" / "views.py").read_text(encoding="utf-8")
        controls = source.split("class TicketControls", 1)[1].split("class CloseModal", 1)[0]
        self.assertIn("ticket_by_channel(interaction.channel.id)", controls)
        self.assertIn("active_ticket_owner(row, interaction.user.id)", controls)
        self.assertIn('custom_id="grid-a1:ticket:request-close"', controls)
        self.assertIn("owner_close_requested", controls)
        self.assertIn("ticket_kept_open", source)

    def test_commands_have_emoji_descriptions_and_slash_options_have_hints(self):
        files = (ROOT / "grid_a1" / "bot.py", ROOT / "grid_a1" / "commands.py")
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                command_calls = [d for d in node.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "command"]
                for call in command_calls:
                    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
                    help_text = keywords.get("description") or keywords.get("help")
                    self.assertIsInstance(help_text, ast.Constant, f"{path}:{node.name} needs a visible command description")
                    self.assertGreater(ord(help_text.value[0]), 127, f"{path}:{node.name} description should start with an emoji")
                    is_slash = "description" in keywords
                    if is_slash:
                        described = {kw.arg for dec in node.decorator_list if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "describe" for kw in dec.keywords if kw.arg}
                        parameters = [arg.arg for arg in node.args.args[1:]]
                        self.assertTrue(set(parameters) <= described, f"{path}:{node.name} needs option descriptions")

    def test_every_command_has_emoji_description_and_slash_options_have_hints(self):
        for path in (ROOT / "grid_a1" / "bot.py", ROOT / "grid_a1" / "commands.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                commands = [decorator for decorator in node.decorator_list if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "command"]
                for decorator in commands:
                    keywords = {keyword.arg: keyword.value for keyword in decorator.keywords if keyword.arg}
                    help_text = keywords.get("description") or keywords.get("help")
                    self.assertIsInstance(help_text, ast.Constant, f"{path}:{node.name} needs a visible command description")
                    self.assertGreater(ord(help_text.value[0]), 127, f"{path}:{node.name} description should start with an emoji")
                    if "description" in keywords:
                        described = {keyword.arg for item in node.decorator_list if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == "describe" for keyword in item.keywords if keyword.arg}
                        parameters = {argument.arg for argument in node.args.args[1:]}
                        self.assertTrue(parameters <= described, f"{path}:{node.name} needs descriptions for every slash option")

    def test_help_lists_all_command_groups(self):
        source = (ROOT / "grid_a1" / "commands.py").read_text(encoding="utf-8")
        for section in ("🌐 Everyone", "🎛️ Easy private dashboard", "👑 Server owner setup", "⚙️ Server setup & safety", "🛡️ Staff tools", "🔧 Bot owner"):
            self.assertIn(section, source)
        for command in ("/report", "/setup tickets", "/setup welcomer", "/ticket transfer", "/ticket close", "/anti-links", "/embed-edit", "/sync"):
            self.assertIn(command, source)

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
