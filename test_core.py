"""Stdlib regression tests for the SQLite core and source safety checks."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord
from pathlib import Path

from grid_a1.config import Settings, validate_runtime
from grid_a1.database import Database
from grid_a1.embeds import inactivity_indicator
from grid_a1.utils import anti_link_config_updates, active_ticket_owner, detected_external_links, inactivity_custom_id, is_http_url, safe_json_list, sanitize_channel_name, ticket_status_title
from grid_a1.commands import (
    BASE_SERVER_SETUP_HELP,
    GIVEAWAY_SETUP_HELP,
    MANAGE_GUILD_HELP,
    POLL_SETUP_HELP,
    _validate_image,
)
from grid_a1.polls import PollVoteView, parse_poll_options, poll_embed
from grid_a1.views import DashboardView, TicketControls


ROOT = Path(__file__).parent


class CoreTests(unittest.TestCase):
    def test_sanitize_channel_name_collapses_dashes(self):
        self.assertEqual(sanitize_channel_name("EU", "Bug / Links", "A--User", "ABC123"), "eu-bug-links-a-user-abc123")

    def test_env_examples_do_not_ship_a_real_owner_id(self):
        for filename in (".env.example", ".env.owner.example"):
            source = (ROOT / filename).read_text(encoding="utf-8")
            owner_line = next(line for line in source.splitlines() if line.startswith("OWNER_ID="))
            self.assertEqual(owner_line.partition("=")[2].strip(), "", filename)

    def test_help_sections_fit_discord_embed_field_limits(self):
        self.assertLessEqual(len(BASE_SERVER_SETUP_HELP + MANAGE_GUILD_HELP), 1024)
        self.assertLessEqual(len(POLL_SETUP_HELP), 1024)
        self.assertLessEqual(len(GIVEAWAY_SETUP_HELP), 1024)

    def test_custom_embed_image_validation_checks_type_and_extension(self):
        self.assertIsNone(_validate_image(None))
        supported = SimpleNamespace(content_type="image/png", filename="image.PNG")
        self.assertIsNone(_validate_image(supported))
        wrong_content = SimpleNamespace(content_type="text/plain", filename="image.png")
        self.assertIn("not an image", _validate_image(wrong_content))
        wrong_extension = SimpleNamespace(content_type="image/png", filename="image.svg")
        self.assertIn("extension", _validate_image(wrong_extension))

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
            self.assertEqual(database.startup_check()["schema_version"], 19)

    def test_ticket_create_rolls_back_when_audit_insert_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            with database.connect() as db:
                db.execute("CREATE TRIGGER fail_audit_insert BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                database.create_ticket(ticket_id="AUDITFAIL", guild_id=42, channel_id=9012, owner_id=7005, issue="general", region="EU", opened_at="now", last_activity_at="now")
            self.assertIsNone(database.ticket("AUDITFAIL"))

    def test_config_upsert_rolls_back_partial_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.upsert_config(42)
            with database.connect() as db:
                db.execute("CREATE TRIGGER fail_co_owner BEFORE UPDATE OF co_owner_role ON guild_config BEGIN SELECT RAISE(ABORT, 'synthetic config failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                database.upsert_config(42, owner_role=101, co_owner_role=102)
            self.assertIsNone(database.config(42)["owner_role"])

    def test_transcript_can_omit_embedded_images_but_keep_attachment_links(self):
        from grid_a1.transcript import render
        from datetime import datetime, timezone

        read_called = False

        async def read_image():
            nonlocal read_called
            read_called = True
            return b"image-bytes"

        attachment = SimpleNamespace(
            content_type="image/png",
            size=12,
            filename="screenshot.png",
            url="https://cdn.example/screenshot.png",
            read=read_image,
        )
        now = datetime.now(timezone.utc)
        message = SimpleNamespace(
            author=SimpleNamespace(display_name="Player"),
            content="Here is the screenshot",
            created_at=now,
            attachments=[attachment],
        )
        guild = SimpleNamespace(get_member=lambda _member_id: None)
        channel = SimpleNamespace(guild=guild, created_at=now, id=456)
        html = asyncio.run(
            render(
                [message],
                channel,
                {"id": "TICKET1", "owner": ""},
                include_images=False,
                max_cached_image_bytes=0,
            )
        )
        self.assertFalse(read_called)
        self.assertIn("Image not embedded", html)
        self.assertIn("https://cdn.example/screenshot.png", html)

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

    def test_ticket_claim_requires_explicit_transfer_and_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.create_ticket(
                ticket_id="CLAIM1",
                guild_id=42,
                channel_id=9012,
                owner_id=7001,
                issue="general",
                region="EU",
                opened_at="now",
                last_activity_at="now",
            )
            self.assertEqual(database.assign_ticket("CLAIM1", 7101), ("assigned", None))
            self.assertEqual(database.assign_ticket("CLAIM1", 7101), ("already_assigned", 7101))
            self.assertEqual(database.assign_ticket("CLAIM1", 7102), ("already_claimed", 7101))
            self.assertEqual(
                database.assign_ticket("CLAIM1", 7102, actor_id=7103, allow_reassign=True),
                ("assigned", 7101),
            )
            self.assertEqual(database.ticket("CLAIM1")["claimed_by"], 7102)
            with database.connect() as db:
                audits = db.execute(
                    "SELECT actor_id, action FROM audit_log WHERE ticket_id='CLAIM1' AND action LIKE 'ticket_%' ORDER BY id"
                ).fetchall()
            self.assertEqual([(row["actor_id"], row["action"]) for row in audits], [
                (7101, "ticket_claimed"),
                (7103, "ticket_reassigned"),
            ])

    def test_inactivity_dm_failure_is_recorded_once_without_auto_close(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.create_ticket(
                ticket_id="NODM1",
                guild_id=42,
                channel_id=9013,
                owner_id=7002,
                issue="general",
                region="EU",
                opened_at="now",
                last_activity_at="old",
            )
            self.assertTrue(database.mark_inactivity_dm_unavailable("NODM1", "2026-01-01T00:00:00+00:00"))
            row = database.ticket("NODM1")
            self.assertIsNotNone(row["inactivity_notice_at"])
            self.assertIsNone(row["auto_close_at"])
            self.assertEqual(row["auto_close_reason"], "dm_unavailable")
            self.assertFalse(database.mark_inactivity_dm_unavailable("NODM1", "2026-01-01T00:05:00+00:00"))
            database.mark_activity("NODM1")
            self.assertTrue(database.mark_inactivity_dm_unavailable("NODM1", "2026-01-01T01:00:00+00:00"))
            with database.connect() as db:
                count = db.execute(
                    "SELECT COUNT(*) FROM audit_log WHERE ticket_id='NODM1' AND action='inactivity_notice_dm_unavailable'"
                ).fetchone()[0]
            self.assertEqual(count, 2)

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

    def test_migration_adds_poll_metadata_and_giveaway_tables_to_legacy_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manager.sqlite3"
            legacy = sqlite3.connect(path)
            legacy.execute(
                "CREATE TABLE polls (poll_id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, "
                "channel_id INTEGER NOT NULL, message_id INTEGER, creator_id INTEGER NOT NULL, "
                "question TEXT NOT NULL, options_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', "
                "created_at TEXT NOT NULL, ends_at TEXT NOT NULL, ended_at TEXT)"
            )
            legacy.execute(
                "INSERT INTO polls(poll_id,guild_id,channel_id,creator_id,question,options_json,created_at,ends_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("OLDPOLL", 42, 9200, 7001, "Legacy question", json.dumps(["Yes", "No"]), "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
            )
            legacy.commit()
            legacy.close()

            database = Database(path)
            database.migrate()
            self.assertTrue(database.startup_check()["ok"])
            self.assertEqual(database.startup_check()["schema_version"], 19)
            self.assertEqual(database.poll_for_guild(42, "OLDPOLL")["description"], "")
            with database.connect() as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("giveaways", tables)
            self.assertIn("giveaway_entries", tables)

    def test_poll_storage_configuration_votes_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            self.assertIsNone(database.poll_settings(42))
            database.upsert_poll_settings(42, channel_id=9200, default_duration_hours=12)
            settings = database.poll_settings(42)
            self.assertEqual(settings["channel_id"], 9200)
            self.assertEqual(settings["default_duration_hours"], 12)
            with self.assertRaises(ValueError):
                database.upsert_poll_settings(42, default_duration_hours=0)

            database.create_poll(poll_id="POLL1234", guild_id=42, channel_id=9200, creator_id=7001, question="Which map?", description="Choose the next community map.", options_json='["Island", "Ragnarok"]', created_at="2026-01-01T00:00:00+00:00", ends_at="2026-01-02T00:00:00+00:00")
            self.assertEqual(database.poll_for_guild(42, "POLL1234")["description"], "Choose the next community map.")
            self.assertIsNone(database.poll_for_guild(99, "POLL1234"))
            self.assertEqual(database.poll_options("POLL1234"), ["Island", "Ragnarok"])
            self.assertTrue(database.set_poll_message_id("POLL1234", 98765))
            self.assertEqual(database.polls_with_messages_all()[0]["message_id"], 98765)
            self.assertEqual(database.cast_poll_vote("POLL1234", 7002, 0, "2026-01-01T01:00:00+00:00"), "recorded")
            self.assertEqual(database.cast_poll_vote("POLL1234", 7002, 1, "2026-01-01T02:00:00+00:00"), "recorded")
            self.assertEqual(database.poll_results("POLL1234"), {1: 1})
            self.assertEqual(database.cast_poll_vote("POLL1234", 7003, 4, "2026-01-01T03:00:00+00:00"), "invalid_option")
            self.assertTrue(database.end_poll("POLL1234", 7001, "2026-01-01T04:00:00+00:00"))
            self.assertEqual(database.cast_poll_vote("POLL1234", 7003, 0, "2026-01-01T05:00:00+00:00"), "closed")
            self.assertEqual(database.polls_with_messages_all()[0]["status"], "ended")
            removed = database.remove_poll("POLL1234", removed_by=7001)
            self.assertEqual(removed["question"], "Which map?")
            self.assertEqual(database.poll_results("POLL1234"), {})
            self.assertIsNone(database.poll_for_guild(42, "POLL1234"))

            database.create_poll(poll_id="EXPIRED1", guild_id=42, channel_id=9200, creator_id=7001, question="Expired?", options_json='["Yes", "No"]', created_at="2026-01-01T00:00:00+00:00", ends_at="2026-01-01T01:00:00+00:00")
            self.assertEqual(database.cast_poll_vote("EXPIRED1", 7002, 0, "2026-01-01T01:00:00+00:00"), "expired")
            self.assertEqual(database.poll_for_guild(42, "EXPIRED1")["status"], "ended")

    def test_giveaway_entries_and_winner_draw_persist_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.create_giveaway(
                giveaway_id="GIVEAWAY1",
                guild_id=42,
                channel_id=9201,
                creator_id=7001,
                reward_type="RCE item pack",
                ping_role_id=None,
                winner_count=2,
                created_at="2026-01-01T00:00:00+00:00",
                ends_at="2026-01-02T00:00:00+00:00",
            )
            self.assertTrue(database.set_giveaway_message_id("GIVEAWAY1", 55501))
            self.assertEqual(len(database.active_giveaways(42)), 1)
            self.assertEqual(database.enter_giveaway(42, "GIVEAWAY1", 7002, "2026-01-01T01:00:00+00:00"), "entered")
            self.assertEqual(database.enter_giveaway(42, "GIVEAWAY1", 7002, "2026-01-01T01:01:00+00:00"), "already_entered")
            self.assertEqual(database.enter_giveaway(99, "GIVEAWAY1", 7003, "2026-01-01T02:00:00+00:00"), "missing")
            self.assertEqual(database.giveaway_entry_count("GIVEAWAY1"), 1)
            row, winners, state = database.finalize_giveaway(42, "GIVEAWAY1", "2026-01-01T12:00:00+00:00")
            self.assertEqual(state, "not_due")
            self.assertEqual(row["status"], "open")
            self.assertEqual(winners, [])
            row, winners, state = database.finalize_giveaway(42, "GIVEAWAY1", "2026-01-01T12:00:00+00:00", ended_by=7001, force=True)
            self.assertEqual(state, "ended")
            self.assertEqual(row["status"], "ended")
            self.assertEqual(winners, [7002])
            row, winners_again, state = database.finalize_giveaway(42, "GIVEAWAY1", "2026-01-01T13:00:00+00:00", ended_by=7001, force=True)
            self.assertEqual(state, "already_ended")
            self.assertEqual(winners_again, winners)
            self.assertEqual(database.active_giveaways(42), [])

    def test_poll_management_commands_are_registered(self):
        tree = ast.parse((ROOT / "grid_a1" / "bot.py").read_text(encoding="utf-8"))
        registered = set()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                command_factory = decorator.func
                if not (
                    isinstance(command_factory, ast.Attribute)
                    and command_factory.attr == "command"
                    and isinstance(command_factory.value, ast.Name)
                    and command_factory.value.id == "poll_group"
                ):
                    continue
                name = next(
                    (keyword.value.value for keyword in decorator.keywords if keyword.arg == "name"),
                    None,
                )
                if name:
                    registered.add(name)
        self.assertEqual(registered, {"config", "create", "dashboard", "end", "remove"})

    def test_poll_and_giveaway_dashboards_expose_the_expected_controls(self):
        from grid_a1.giveaways import GiveawayDashboardView, GiveawayEntryView, GiveawayService
        from grid_a1.polls import PollDashboardView, PollService

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            poll_view = PollDashboardView(database, PollService(database), 42, 7001, 9100)
            giveaway_service = GiveawayService(database)
            giveaway_view = GiveawayDashboardView(giveaway_service, 42, 7001)
            entry_view = GiveawayEntryView(giveaway_service, "GIVEAWAY1")
        self.assertEqual({item.label for item in poll_view.children}, {"Create Poll", "View Active"})
        self.assertEqual({item.label for item in giveaway_view.children}, {"Configure Giveaway", "View Active"})
        self.assertIsNone(entry_view.timeout)
        self.assertEqual(entry_view.children[0].custom_id, "grid-a1:giveaway:GIVEAWAY1:enter")

    def test_giveaway_commands_are_registered_on_the_runtime_tree(self):
        from grid_a1.bot import bot as runtime_bot

        giveaway_group = runtime_bot.tree.get_command("giveaway")
        self.assertIsNotNone(giveaway_group)
        self.assertEqual(
            {command.name for command in giveaway_group.commands},
            {"dashboard", "end"},
        )

    def test_poll_subcommands_are_registered_on_the_runtime_tree(self):
        from grid_a1.bot import bot as runtime_bot

        poll_group = runtime_bot.tree.get_command("poll")
        self.assertIsNotNone(poll_group)
        self.assertEqual(
            {command.name for command in poll_group.commands},
            {"config", "create", "dashboard", "end", "remove"},
        )

    def test_optional_postgres_baseline_lists_poll_tables(self):
        source = (ROOT / "grid_a1" / "postgres.py").read_text(encoding="utf-8")
        for table in ("poll_settings", "polls", "poll_votes"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", source)

    def test_poll_options_are_parsed_and_bounded(self):
        self.assertEqual(parse_poll_options(" Island | Ragnarok "), ["Island", "Ragnarok"])
        with self.assertRaises(ValueError):
            parse_poll_options("Only one")
        with self.assertRaises(ValueError):
            parse_poll_options("Yes|Yes")
        with self.assertRaises(ValueError):
            parse_poll_options("|".join(f"Option {i}" for i in range(11)))
        with self.assertRaises(ValueError):
            parse_poll_options("x" * 101 + "|No")

    def test_poll_embed_shows_results_and_actual_end_time(self):
        poll = {
            "poll_id": "POLL1234",
            "question": "Which map?",
            "description": "Community context for the choice.",
            "status": "ended",
            "ends_at": "2026-01-02T00:00:00+00:00",
            "ended_at": "2026-01-01T00:00:00+00:00",
        }
        result = poll_embed(poll, ["Island", "Ragnarok"], {0: 2, 1: 1})
        self.assertEqual(result.title, "📊 Which map?")
        self.assertTrue(result.description.startswith("Community context for the choice."))
        self.assertIn("Voting has ended", result.description)
        self.assertEqual(result.fields[2].name, "🗳️ Total votes")
        self.assertEqual(result.fields[2].value, "**3**")
        self.assertEqual(result.fields[3].name, "🕒 Closed at")
        self.assertEqual(result.fields[3].value, "<t:1767225600:R>")
        self.assertIn("Poll ID: POLL1234", result.footer.text)
        self.assertNotIn("Change vote anytime", result.footer.text)

    def test_poll_vote_view_has_a_persistent_per_poll_component_id(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            view = PollVoteView(database, "POLL1234", ["Yes", "No"])
            self.assertIsNone(view.timeout)
            selectors = [item for item in view.children if isinstance(item, discord.ui.Select)]
            self.assertEqual(len(selectors), 1)
            self.assertEqual(selectors[0].custom_id, "grid-a1:poll:POLL1234:vote")

    def test_extra_ticket_roles_enforce_the_ten_role_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            for role_id in range(1, 11):
                self.assertTrue(database.add_staff_role(42, role_id))
            self.assertFalse(database.add_staff_role(42, 10))
            with self.assertRaises(ValueError):
                database.add_staff_role(42, 11)
            self.assertEqual(database.staff_role_ids(42), list(range(1, 11)))

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

    def test_dashboard_deferred_updates_fall_back_to_ephemeral_confirmation(self):
        source = (ROOT / "grid_a1" / "dashboard_setup.py").read_text(encoding="utf-8")
        self.assertIn("async def finish(self, interaction, result_embed, view=None):", source)
        self.assertIn("await interaction.followup.send(embed=result_embed, ephemeral=True)", source)
        self.assertEqual(source.count("await interaction.edit_original_response("), 1)

    def test_dashboard_channel_select_resolves_command_channel_values(self):
        from grid_a1.dashboard_setup import _resolve_selected_channel

        selected = SimpleNamespace(id=123)
        cached_channel = object()

        class CachedGuild:
            id = 42

            def get_channel(self, channel_id):
                return cached_channel if channel_id == 123 else None

            def get_thread(self, channel_id):
                return None

            async def fetch_channel(self, channel_id):
                raise AssertionError("cached channel should not be fetched")

        resolved = asyncio.run(_resolve_selected_channel(CachedGuild(), selected))
        self.assertIs(resolved, cached_channel)

        fetched_channel = object()

        class UncachedGuild:
            id = 42

            def get_channel(self, channel_id):
                return None

            def get_thread(self, channel_id):
                return None

            async def fetch_channel(self, channel_id):
                return fetched_channel

        resolved = asyncio.run(_resolve_selected_channel(UncachedGuild(), selected))
        self.assertIs(resolved, fetched_channel)

    def test_dashboard_save_callbacks_acknowledge_before_database_writes(self):
        source = (ROOT / "grid_a1" / "dashboard_setup.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        expected_writes = {
            ("PermissionRolesStepTwoView", "save"): "self.database.upsert_config",
            ("TicketSetupWizardView", "save"): "self.save_settings",
            ("TicketSetupWizardView", "publish"): "self.save_settings",
            ("ExtraTicketAccessWizardView", "apply"): "self.database.add_staff_role",
            ("WelcomeStepTwoView", "save"): "self.database.upsert_config",
            ("VerificationWizardView", "save"): "self.save_settings",
            ("VerificationWizardView", "publish"): "self.save_settings",
            ("AnnouncementSettingsWizardView", "save"): "self.database.upsert_config",
            ("ReportChannelWizardView", "save"): "self.database.upsert_config",
        }
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for method in class_node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                key = (class_node.name, method.name)
                write_marker = expected_writes.get(key)
                if not write_marker:
                    continue
                body = ast.get_source_segment(source, method)
                defer = body.find("await interaction.response.defer()")
                write = body.find(write_marker)
                self.assertGreaterEqual(defer, 0, key)
                self.assertGreaterEqual(write, 0, key)
                self.assertLess(defer, write, key)
                expected_writes.pop(key)
        self.assertFalse(expected_writes, f"dashboard callbacks were not found: {expected_writes}")

    def test_stale_guild_command_cleanup_runs_after_ready(self):
        source = (ROOT / "grid_a1" / "bot.py").read_text(encoding="utf-8")
        setup_hook = source.split("async def setup_hook", 1)[1].split("async def clear_stale_guild_command_copies", 1)[0]
        self.assertNotIn("for existing_guild in self.guilds", setup_hook)
        self.assertIn("async def clear_stale_guild_command_copies", source)
        self.assertIn("async def on_ready():\n    await bot.clear_stale_guild_command_copies()", source)

    def test_custom_embed_requires_manage_messages(self):
        source = (ROOT / "grid_a1" / "commands.py").read_text(encoding="utf-8")
        embed_command = source.split('name="embed"', 1)[1].split('name="sync"', 1)[0]
        self.assertIn("has_permissions(manage_messages=True, send_messages=True)", embed_command)

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

    def test_ticket_modal_passes_all_required_details_and_region(self):
        source = (ROOT / "grid_a1" / "views.py").read_text(encoding="utf-8")
        region_select = source.split("class RegionSelect", 1)[1].split("class RegionView", 1)[0]
        ticket_select = source.split("class TicketTypeSelect", 1)[1].split("class TicketPanel", 1)[0]
        self.assertIn("DetailsModal(self.service, self.issue, self.label, self.values[0])", region_select)
        self.assertIn("RegionView(self.service, option.value, option.label)", ticket_select)

    def test_refresh_panels_isolates_failures_per_guild(self):
        source = (ROOT / "grid_a1" / "bot.py").read_text(encoding="utf-8")
        loop = source.split("@tasks.loop(seconds=60)", 1)[1].split("@tasks.loop(minutes=5)", 1)[0]
        self.assertIn("await self.refresh_guild_panel(guild)", loop)
        self.assertIn("except Exception:", loop)
        self.assertIn("continuing with other guilds", loop)

    def test_postgres_baseline_includes_closed_ticket_archive_table(self):
        source = (ROOT / "grid_a1" / "postgres.py").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS closed_tickets", source)
        for column in ("guild BIGINT", "region TEXT", "issue TEXT", "closed_by BIGINT", "reason TEXT", "closed_at TEXT"):
            self.assertIn(column, source)

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
        for section in ("🌐 Everyone", "🎛️ Easy private dashboard", "👑 Server owner setup", "⚙️ Server setup & safety", "📊 Poll setup", "🎁 Giveaways", "🛡️ Staff tools", "🔧 Bot owner"):
            self.assertIn(section, source)
        for command in ("/report", "/setup tickets", "/setup welcomer", "/ticket transfer", "/ticket close", "/poll dashboard", "/giveaway dashboard", "/anti-links", "/embed-edit", "/sync"):
            self.assertIn(command, source)

    def test_help_does_not_advertise_removed_commands_or_anti_link_options(self):
        source = (ROOT / "grid_a1" / "commands.py").read_text(encoding="utf-8")
        for stale in ("`/staff`", "whitelist domains", "bypass roles", "allowed link roles"):
            self.assertNotIn(stale, source)
        self.assertIn("configure enabled, action, and log channel", source)

    def test_anti_links_ignores_sentence_periods_and_numeric_versions(self):
        for sentence in (
            "Hello there. How are you?",
            "Yes. Thanks",
            "Version 1.2 is out",
        ):
            self.assertEqual(detected_external_links(sentence), [], sentence)
        self.assertTrue(detected_external_links("Visit example . com"))
        self.assertTrue(detected_external_links("Visit https : / / example . com"))
        invite_links = detected_external_links("Join discord . gg / example")
        self.assertEqual(len(invite_links), 1)
        self.assertIn("discord.gg", invite_links[0].casefold())

    def test_anti_link_partial_updates_preserve_omitted_settings(self):
        self.assertEqual(
            anti_link_config_updates(enabled=False),
            {"anti_links_enabled": 0},
        )
        self.assertEqual(
            anti_link_config_updates(action="delete_log"),
            {"anti_links_action": "delete_log"},
        )
        self.assertEqual(
            anti_link_config_updates(clear_log_channel=True),
            {"anti_links_log_channel": None},
        )
        with self.assertRaises(ValueError):
            anti_link_config_updates(action="invalid")

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
