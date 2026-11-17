"""Regression tests for the SpinBack features added on top of the SQLite core.

Covers ticket naming, the reimbursement form, member suggestions, the rules panel, and the
SpinBack branding: these grew out of the core suite and are kept separate so each file stays
small enough to read and review on its own.
"""
from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

from grid_a1.database import Database, SCHEMA_VERSION


ROOT = Path(__file__).parent


class SpinBackFeatureTests(unittest.TestCase):
    def test_ticket_channel_names_show_priority_and_stay_bounded(self):
        from grid_a1.utils import PRIORITY_MARKERS, sanitize_channel_name, suggestion_custom_id, ticket_channel_name

        self.assertEqual(sanitize_channel_name("EU", "Bug / Links", "A--User", "ABC123"), "eu-bug-links-a-user-abc123")
        self.assertEqual(sanitize_channel_name(""), "ticket")
        self.assertEqual(sanitize_channel_name("EU", "General", limit=6), "eu-gen")

        urgent = ticket_channel_name("reimburse", "Olly Bat", "A1B2C3", "urgent")
        self.assertTrue(urgent.startswith("🔴-"))
        self.assertIn("reimburse-olly-bat-a1b2c3", urgent)
        self.assertLessEqual(len(urgent), 95)
        self.assertTrue(ticket_channel_name("general", "player", "ID1").startswith(f"{PRIORITY_MARKERS['normal']}-"))
        self.assertTrue(ticket_channel_name("general", "player", "ID1", "not-a-priority").startswith("🔵-"))
        self.assertEqual(
            ticket_channel_name("general", "player", "ID1", region="NA"),
            f"{PRIORITY_MARKERS['normal']}-na-general-player-id1",
        )
        self.assertEqual(
            ticket_channel_name("general", "player", "ID1", "urgent", region="EU"),
            f"{PRIORITY_MARKERS['urgent']}-eu-general-player-id1",
        )
        self.assertEqual(sorted(PRIORITY_MARKERS), ["high", "low", "normal", "urgent"])

        self.assertEqual(suggestion_custom_id("up", "AB12"), "grid-a1:suggestion:AB12:up")
        self.assertEqual(suggestion_custom_id("down", "x!" * 40), "grid-a1:suggestion:" + "x" * 32 + ":down")
        self.assertLessEqual(len(suggestion_custom_id("down", "Z" * 90)), 100)
        for broken in ("sideways", ""):
            with self.assertRaises(ValueError):
                suggestion_custom_id(broken, "AB12")
        with self.assertRaises(ValueError):
            suggestion_custom_id("up", "")

    def test_reimburse_form_collects_and_formats_the_four_answers(self):
        from grid_a1.tickets import REIMBURSE_PROMPTS, ReimburseModal, reimbursement_details

        self.assertEqual([label for label, _, _ in REIMBURSE_PROMPTS], ["Gamertag", "Clan Name", "Grid Location", "What Happened"])

        calls = []

        class FakeService:
            async def create(self, interaction, issue, label, region, details):
                calls.append((issue, label, region, details))

        modal = ReimburseModal(FakeService())
        self.assertEqual(
            [item.label for item in modal.children],
            ["Region", "Gamertag", "Clan Name", "Grid Location", "What Happened"],
        )
        self.assertEqual([item.required for item in modal.children], [True, True, False, True, True])
        self.assertEqual(modal.children[-1].style, discord.TextStyle.paragraph)
        self.assertLessEqual(modal.children[-1].max_length, 1024)

        details = reimbursement_details({"Gamertag": "  Olly  ", "Grid Location": "Grid A1", "What Happened": "Lost my base"})
        self.assertIn("Gamertag: Olly", details)
        self.assertIn("Clan Name: Not provided", details)
        self.assertTrue(details.endswith("What Happened: Lost my base"))

        class FakeInteraction:
            def __init__(self):
                self.user = SimpleNamespace(id=77, name="olly")

        async def submit():
            for item, value in zip(modal.children, ["NA", "Olly", "", "Grid A1", "Lost my base"]):
                item._value = value
            await modal.on_submit(FakeInteraction())

        asyncio.run(submit())
        self.assertEqual(len(calls), 1)
        issue, label, region, submitted = calls[0]
        self.assertEqual((issue, label, region), ("reimburse", "Reimbursement", "NA"))
        self.assertIn("Gamertag: Olly", submitted)
        self.assertIn("Clan Name: Not provided", submitted)

    def test_reimburse_command_is_public_and_opens_the_form(self):
        import grid_a1.bot as bot_module
        from grid_a1.guides import ACCESS_LABELS, COMMAND_ACCESS, catalog, render_guide

        command = bot_module.bot.tree.get_command("reimburse")
        self.assertIsNotNone(command)
        self.assertEqual(command.description, "Ask staff for reimbursement of lost items or progress")
        self.assertEqual(COMMAND_ACCESS["reimburse"], "everyone")
        self.assertIsNotNone(bot_module.ReimburseModal)

        entries = catalog(bot_module.bot.tree)
        rendered = "\n".join(value for _, value in render_guide(entries, list(ACCESS_LABELS), max_total=6000))
        self.assertIn("/reimburse", rendered)

    def test_suggestions_store_votes_and_toggle_cleanly(self):
        from grid_a1.suggestions import SuggestionService, new_suggestion_id, suggestion_embed, suggestion_line

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            service = SuggestionService(database)

            self.assertEqual(len(new_suggestion_id()), 6)
            with self.assertRaises(ValueError):
                database.create_suggestion(suggestion_id="SHORT", guild_id=1, author_id=2, body="hi", created_at="now")
            with self.assertRaises(ValueError):
                database.create_suggestion(suggestion_id="LONG", guild_id=1, author_id=2, body="x" * 1001, created_at="now")

            row = service.create(1, 2, "  Add   a   replay   night  ")
            self.assertEqual(row["body"], "Add a replay night")
            self.assertIsNone(database.suggestion(99, row["suggestion_id"]))
            self.assertEqual([item["suggestion_id"] for item in service.listing(1)], [row["suggestion_id"]])

            self.assertEqual(database.record_suggestion_vote(row["suggestion_id"], 2, 1, "t1"), 1)
            self.assertEqual(database.record_suggestion_vote(row["suggestion_id"], 3, 1, "t1"), 1)
            self.assertEqual(database.suggestion_votes(row["suggestion_id"]), {"up": 2, "down": 0})
            self.assertEqual(database.record_suggestion_vote(row["suggestion_id"], 2, 1, "t2"), 0)
            self.assertEqual(database.suggestion_votes(row["suggestion_id"]), {"up": 1, "down": 0})
            self.assertEqual(database.record_suggestion_vote(row["suggestion_id"], 2, -1, "t3"), -1)
            self.assertEqual(database.suggestion_votes(row["suggestion_id"]), {"up": 1, "down": 1})
            self.assertEqual(database.suggestion_vote_of(row["suggestion_id"], 2), -1)
            self.assertEqual(database.suggestion_vote_of(row["suggestion_id"], 4), 0)
            with self.assertRaises(ValueError):
                database.record_suggestion_vote(row["suggestion_id"], 2, 5, "t4")

            totals = database.suggestion_votes_many([row["suggestion_id"], "MISSING"])
            self.assertEqual(totals[row["suggestion_id"]], {"up": 1, "down": 1})
            self.assertEqual(totals["MISSING"], {"up": 0, "down": 0})
            self.assertEqual(database.suggestion_votes_many([]), {})

            self.assertEqual(database.published_suggestions_all(), [])
            self.assertTrue(database.attach_suggestion_message(row["suggestion_id"], 55, 66))
            self.assertFalse(database.attach_suggestion_message("NOPE", 55, 66))
            published = database.published_suggestions_all()
            self.assertEqual([item["suggestion_id"] for item in published], [row["suggestion_id"]])
            self.assertEqual(published[0]["message_id"], 66)

            panel = suggestion_embed(row, {"up": 3, "down": 1}, viewer_vote=1)
            values = "\n".join(field.value for field in panel.fields)
            self.assertIn("**3**", values)
            self.assertIn("<@2>", values)
            self.assertIn("75% of 4", values)
            self.assertIn("👍 yes", values)
            self.assertIn("vote back", (panel.footer.text or "").lower())
            self.assertIn("No votes yet", suggestion_embed(row, {}) .fields[5].value)
            self.assertIn(row["suggestion_id"], suggestion_line(row, {"up": 1, "down": 0}))

    def test_suggestion_commands_and_vote_view_are_wired(self):
        import grid_a1.bot as bot_module
        from grid_a1.guides import ACCESS_LABELS, COMMAND_ACCESS, catalog, render_guide
        from grid_a1.suggestions import SuggestionModal, SuggestionVoteView

        group = bot_module.bot.tree.get_command("suggestion")
        self.assertIsNotNone(group)
        self.assertEqual({command.name for command in group.commands}, {"send", "list", "config"})
        self.assertIsNotNone(bot_module.bot.suggestions)
        self.assertEqual(COMMAND_ACCESS["suggestion send"], "everyone")
        self.assertEqual(COMMAND_ACCESS["suggestion config"], "admin")

        modal = SuggestionModal(bot_module.bot.suggestions)
        self.assertEqual(len(modal.children), 1)
        self.assertEqual(modal.children[0].max_length, 1000)
        self.assertEqual(modal.children[0].style, discord.TextStyle.paragraph)

        view = SuggestionVoteView(bot_module.bot.suggestions, "AB12CD")
        self.assertEqual({item.custom_id for item in view.children}, {"grid-a1:suggestion:AB12CD:up", "grid-a1:suggestion:AB12CD:down"})

        entries = catalog(bot_module.bot.tree)
        rendered = "\n".join(value for _, value in render_guide(entries, list(ACCESS_LABELS), max_total=6000))
        for command in ("/suggestion send", "/suggestion list", "/suggestion config"):
            self.assertIn(command, rendered)

    def test_suggestion_vote_view_counts_toggles_and_reports(self):
        from grid_a1.suggestions import SuggestionService, SuggestionVoteView

        class Recorder:
            def __init__(self):
                self.calls = []

            async def __call__(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        class FakeInteraction:
            def __init__(self, user_id, guild_id=3):
                self.guild = SimpleNamespace(id=guild_id)
                self.user = SimpleNamespace(id=user_id, name=f"member-{user_id}")
                self.replied = Recorder()
                self.edited = Recorder()
                self.response = SimpleNamespace(send_message=self.replied, edit_message=self.edited)

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            service = SuggestionService(database)
            row = service.create(3, 900, "Add a weekly replay night")
            view = SuggestionVoteView(service, row["suggestion_id"])

            first = FakeInteraction(900)
            asyncio.run(view._record(first, 1))
            self.assertEqual(database.suggestion_votes(row["suggestion_id"]), {"up": 1, "down": 0})
            embed_items = first.edited.calls[0][1]
            self.assertIn("**1**", "\n".join(field.value for field in embed_items["embed"].fields))

            second = FakeInteraction(900)
            asyncio.run(view._record(second, 1))
            self.assertEqual(database.suggestion_votes(row["suggestion_id"]), {"up": 0, "down": 0})
            self.assertIn("vote was removed", second.edited.calls[0][1]["embed"].description)

            third = FakeInteraction(901)
            asyncio.run(view._record(third, -1))
            self.assertEqual(database.suggestion_votes(row["suggestion_id"]), {"up": 0, "down": 1})

            outsider = FakeInteraction(902, guild_id=77)
            asyncio.run(view._record(outsider, 1))
            self.assertIn("no longer available", outsider.replied.calls[0][0][0])

    def test_suggestion_publish_requires_a_configured_channel(self):
        from grid_a1.suggestions import SuggestionService

        class Recorder:
            def __init__(self):
                self.calls = []

            async def __call__(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        class FakeDatabase:
            def __init__(self, config):
                self._config = config

            def config(self, guild_id):
                return self._config

        class FakeInteraction:
            def __init__(self, guild_id=3):
                self.guild = SimpleNamespace(id=guild_id)
                self.user = SimpleNamespace(id=5, name="member")
                self.sent = Recorder()
                self.response = SimpleNamespace(send_message=self.sent, defer=Recorder())

        service = SuggestionService(FakeDatabase(None))
        first = FakeInteraction()
        asyncio.run(service.publish(first, "Add a shop"))
        self.assertIn("No suggestion channel is set", first.sent.calls[0][0][0])

        service = SuggestionService(FakeDatabase({"suggestion_channel": 4242}))
        second = FakeInteraction()
        second.guild = SimpleNamespace(id=3, get_channel=lambda channel_id: None)
        asyncio.run(service.publish(second, "Add a shop"))
        self.assertIn("suggestion channel is missing", second.sent.calls[0][0][0].lower())


    def test_rules_panel_puts_the_buttons_inside_the_container(self):
        from grid_a1.brand import BRAND_NAME, BRAND_SHORT
        from grid_a1.rules import RULES_COLOUR, RulesService, rules_panel_view

        self.assertEqual(BRAND_NAME, "SpinBack Manager")
        self.assertEqual(BRAND_SHORT, "SpinBack")

        class FakeDatabase:
            def rule_text(self, guild_id, section):
                return None

            def rule_row(self, guild_id, section):
                return None

        view = rules_panel_view(RulesService(FakeDatabase()))
        self.assertIsInstance(view, discord.ui.LayoutView)
        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 1)
        container = view.children[0]
        self.assertIsInstance(container, discord.ui.Container)
        self.assertEqual(container.accent_colour, RULES_COLOUR)

        walk = list(view.walk_children())
        texts = [item.content for item in walk if isinstance(item, discord.ui.TextDisplay)]
        self.assertEqual(texts[0], "# Spinback Rules ✅")
        self.assertIn("Thank you all for reading and respecting these rules.", texts[1])
        self.assertIn("best competitive server", texts[1])
        self.assertIn("**Discord", texts[2])
        self.assertIn("All discord related rules.", texts[2])
        self.assertIn("**Game 🎮**", texts[3])
        self.assertIn("General in-game rules.", texts[3])
        self.assertIn("Powered by Spinback", texts[-1])

        separators = [item for item in walk if isinstance(item, discord.ui.Separator)]
        self.assertEqual(len(separators), 1)
        sections = [item for item in walk if isinstance(item, discord.ui.Section)]
        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[-1].accessory.media.url, "attachment://spinback-logo.jpg")
        buttons = [item for item in walk if isinstance(item, discord.ui.Button)]
        self.assertEqual([button.label for button in buttons], ["Discord", "Game"])
        self.assertEqual(
            [button.custom_id for button in buttons],
            ["grid-a1:rules:discord", "grid-a1:rules:game"],
        )
        for button in buttons:
            self.assertIsNotNone(button.callback)
        footer = view.children[0].children[-1]
        self.assertIsInstance(footer, discord.ui.Section)
        self.assertIsInstance(footer.children[0], discord.ui.TextDisplay)
        self.assertEqual(footer.accessory.media.url, "attachment://spinback-logo.jpg")
        self.assertIsInstance(view.children[0].children[0], discord.ui.TextDisplay)

    def test_rules_storage_defaults_and_service_reads(self):
        from grid_a1.brand import RULES_DEFAULTS
        from grid_a1.rules import RulesService, rules_section_embed

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            service = RulesService(database)

            self.assertIsNone(database.rule_text(42, "discord"))
            self.assertEqual(database.rules_texts(42), {})
            self.assertEqual(service.text(42, "discord"), RULES_DEFAULTS["discord"])
            self.assertEqual(service.text_with_meta(42, "game"), (RULES_DEFAULTS["game"], None))
            self.assertEqual(service.text(42, "not-a-section"), RULES_DEFAULTS["discord"])

            database.set_rule_text(42, "GAME", "1. No teaming.", 900, "2026-11-16T10:00:00+00:00")
            row = database.rule_row(42, "game")
            self.assertEqual(row["body"], "1. No teaming.")
            self.assertEqual(row["section"], "game")
            self.assertEqual(row["updated_by"], 900)
            self.assertEqual(database.rules_texts(42), {"game": "1. No teaming."})
            self.assertEqual(service.text_with_meta(42, "game")[0], "1. No teaming.")
            self.assertIsNotNone(service.text_with_meta(42, "game")[1])
            with self.assertRaises(ValueError):
                database.set_rule_text(42, "game", "   ")

            section = rules_section_embed("game", "1. No teaming.", "2026-11-16T10:00:00+00:00")
            self.assertIn("Game", section.title)
            self.assertEqual(section.description, "1. No teaming.")
            self.assertIn("<t:", section.footer.text)

            database.set_rules_panel(42, 500, 600)
            config = database.config(42)
            self.assertEqual((config["rules_channel"], config["rules_message"]), (500, 600))

    def test_rules_buttons_and_edit_modal(self):
        from grid_a1.rules import RulesEditModal, RulesService, rules_panel_view, rules_section_meta
        from grid_a1.utils import rules_custom_id

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            service = RulesService(database)

            self.assertEqual([rules_custom_id(section) for section, _, _, _ in [("discord", "a", "b", "c"), ("game", "a", "b", "c")]],
                             ["grid-a1:rules:discord", "grid-a1:rules:game"])
            self.assertEqual(rules_custom_id("DISCORD!"), "grid-a1:rules:discord")
            with self.assertRaises(ValueError):
                rules_custom_id("")

            view = rules_panel_view(service)
            buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
            self.assertEqual([item.custom_id for item in buttons], ["grid-a1:rules:discord", "grid-a1:rules:game"])

            class Recorder:
                def __init__(self):
                    self.calls = []

                async def __call__(self, *args, **kwargs):
                    self.calls.append((args, kwargs))

            class FakeInteraction:
                def __init__(self):
                    self.guild = SimpleNamespace(id=3, name="Grid A1")
                    self.response = SimpleNamespace(send_message=Recorder(), is_done=lambda: False)

            interaction = FakeInteraction()
            asyncio.run(buttons[1].callback(interaction))
            sent = interaction.response.send_message.calls[0][1]
            self.assertIn("No cheating", sent["embed"].description)
            self.assertIn("Game", sent["embed"].title)
            self.assertTrue(sent["ephemeral"])

            self.assertEqual(rules_section_meta("nope")[0], "discord")
            modal = RulesEditModal(service, "discord")
            self.assertEqual(len(modal.children), 3)
            self.assertEqual(modal.children[0].style, discord.TextStyle.paragraph)
            self.assertEqual(modal.children[0].max_length, 4000)
            self.assertEqual(modal.children[1].label, "Discord icon (optional)")
            self.assertEqual(modal.children[2].label, "Footer logo (optional)")
            self.assertFalse(modal.children[1].required)
            self.assertEqual(modal.with_current_text("old rules").children[0].default, "old rules")

    def test_rules_post_replaces_the_previous_panel_and_restart_refreshes_it(self):
        from grid_a1.rules import RulesService

        class FakeMessage:
            def __init__(self, message_id):
                self.id = message_id
                self.deleted = False
                self.edits = []

            async def delete(self):
                self.deleted = True

            async def edit(self, **kwargs):
                self.edits.append(kwargs)

        class FakeChannel:
            def __init__(self, channel_id):
                self.id = channel_id
                self.mention = f"<#{channel_id}>"
                self.sent = []
                self.known = {}
                self._next_id = channel_id + 100

            async def send(self, **kwargs):
                self.sent.append(kwargs)
                self._next_id += 1
                message = FakeMessage(self._next_id)
                self.known[message.id] = message
                return message

            async def fetch_message(self, message_id):
                if message_id in self.known:
                    return self.known[message_id]
                raise discord.NotFound(response=object(), message="unknown message")

        class Recorder:
            def __init__(self):
                self.calls = []

            async def __call__(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            service = RulesService(database)
            channel = FakeChannel(700)
            old_panel = FakeMessage(800)
            channel.known[800] = old_panel
            database.set_rules_panel(3, 700, 800)

            guild = SimpleNamespace(id=3, get_channel=lambda channel_id: channel if channel_id == 700 else None)

            class FakeInteraction:
                def __init__(self):
                    self.guild = guild
                    self.user = SimpleNamespace(id=4)
                    self.response = SimpleNamespace(
                        is_done=lambda: False, defer=Recorder(), send_message=Recorder()
                    )
                    self.followup = SimpleNamespace(send=Recorder())

            interaction = FakeInteraction()
            asyncio.run(service.post(interaction, channel))
            self.assertTrue(interaction.response.defer.calls[0][1]["ephemeral"])
            self.assertTrue(old_panel.deleted)
            self.assertEqual(len(channel.sent), 1)
            self.assertIsInstance(channel.sent[0]["view"], discord.ui.LayoutView)
            self.assertIsNone(channel.sent[0]["view"].timeout)
            posted = database.config(3)
            self.assertEqual(posted["rules_channel"], 700)
            self.assertNotEqual(posted["rules_message"], 800)
            self.assertIn("buttons sit inside the panel", interaction.followup.send.calls[0][0][0])

            restarted = FakeChannel(700)
            restarted.known[posted["rules_message"]] = FakeMessage(posted["rules_message"])
            guild2 = SimpleNamespace(id=3, get_channel=lambda channel_id: restarted if channel_id == 700 else None)
            self.assertTrue(asyncio.run(RulesService(database).refresh_panel(guild2)))
            self.assertFalse(asyncio.run(RulesService(database).refresh_panel(SimpleNamespace(id=99, get_channel=lambda channel_id: None))))
            edited = restarted.known[posted["rules_message"]].edits
            self.assertEqual(len(edited), 1)
            self.assertIsInstance(edited[0]["view"], discord.ui.LayoutView)
            self.assertNotIn("embed", edited[0])

    def test_rules_commands_are_registered_and_guided(self):
        import grid_a1.bot as bot_module
        from grid_a1.guides import ACCESS_LABELS, COMMAND_ACCESS, catalog, render_guide

        group = bot_module.bot.tree.get_command("rules")
        self.assertIsNotNone(group)
        self.assertEqual({command.name for command in group.commands}, {"post", "edit", "preview"})
        self.assertIsNotNone(bot_module.bot.rules)
        for name in ("rules post", "rules edit", "rules preview"):
            self.assertEqual(COMMAND_ACCESS[name], "admin")

        entries = catalog(bot_module.bot.tree)
        rendered = "\n".join(value for _, value in render_guide(entries, list(ACCESS_LABELS), max_total=6000))
        for command in ("/rules post", "/rules edit", "/rules preview"):
            self.assertIn(command, rendered)

    def test_rules_panel_icons_prefer_overrides_then_the_server_emoji(self):
        from grid_a1.brand import RULES_INTRO
        from grid_a1.rules import RulesService, resolved_emojis, rules_panel_view, server_emoji

        class FakeEmoji:
            def __init__(self, name, emoji_id):
                self.name = name
                self.id = emoji_id

            def __str__(self):
                return f"<:{self.name}:{self.id}>"

        class FakeDatabase:
            def __init__(self, overrides=None):
                self.overrides = overrides or {}

            def rules_emojis(self, guild_id):
                return dict(self.overrides)

            def rule_text(self, guild_id, section):
                return None

            def rule_row(self, guild_id, section):
                return None

        guild = SimpleNamespace(id=7, emojis=[FakeEmoji("discord", 111), FakeEmoji("spinback", 222)])
        service = RulesService(FakeDatabase())

        self.assertEqual(server_emoji(None, ("discord",)), "")
        self.assertEqual(server_emoji(guild, ("discord", "discord_logo")), "<:discord:111>")
        self.assertEqual(server_emoji(guild, ("game",)), "")

        resolved = resolved_emojis(service, guild)
        self.assertEqual(resolved["discord"], "<:discord:111>")
        self.assertEqual(resolved["game"], "🎮")
        self.assertEqual(resolved["emblem"], "<:spinback:222>")

        texts = [
            item.content
            for item in rules_panel_view(service, guild).walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ]
        self.assertEqual(texts[0], "# Spinback Rules ✅")
        self.assertEqual(texts[1], RULES_INTRO)
        self.assertIn("**Discord <:discord:111>**", texts[2])
        self.assertIn("**Game 🎮**", texts[3])
        self.assertEqual(texts[-1], "***<:spinback:222> Powered by Spinback***")

        overridden = RulesService(FakeDatabase({"discord": "<:custom:999>", "game": "<:pad:5>"}))
        chosen = resolved_emojis(overridden, guild)
        self.assertEqual(chosen["discord"], "<:custom:999>")
        self.assertEqual(chosen["game"], "<:pad:5>")
        self.assertEqual(chosen["emblem"], "<:spinback:222>")

        bare = resolved_emojis(RulesService(FakeDatabase()), None)
        self.assertEqual((bare["discord"], bare["game"], bare["emblem"]), ("💬", "🎮", ""))
        plain = [
            item.content
            for item in rules_panel_view(RulesService(FakeDatabase()), None).walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ]
        self.assertEqual(plain[-1], "***Powered by Spinback***")

    def test_saving_rules_from_the_modal_can_set_the_panel_icon(self):
        from grid_a1.rules import RulesService

        class Recorder:
            def __init__(self):
                self.calls = []

            async def __call__(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            service = RulesService(database)

            class FakeInteraction:
                def __init__(self):
                    self.guild = SimpleNamespace(id=3, name="Grid A1", emojis=[])
                    self.user = SimpleNamespace(id=4)
                    self.response = SimpleNamespace(send_message=Recorder(), is_done=lambda: False)

            interaction = FakeInteraction()
            asyncio.run(service.save(interaction, "game", "1. No cheating or macros.", icon="<:game:42>"))
            message = interaction.response.send_message.calls[0][0][0]
            self.assertIn("Saved the Game", message)
            self.assertIn("panel icon was updated too", message)
            self.assertIn("No rules panel is posted yet", message)
            self.assertEqual(database.rule_text(3, "game"), "1. No cheating or macros.")
            self.assertEqual(database.rules_emojis(3), {"game": "<:game:42>"})
            self.assertIn("**Game <:game:42>**", [
                item.content
                for item in service.panel(interaction.guild).walk_children()
                if isinstance(item, discord.ui.TextDisplay)
            ][3])

            asyncio.run(service.save(interaction, "game", "2. No teaming with other clans.", emblem="<:spinback:7>"))
            self.assertEqual(database.rules_emojis(3), {"game": "<:game:42>", "emblem": "<:spinback:7>"})
            self.assertIn("panel icon was updated too", interaction.response.send_message.calls[1][0][0])

            asyncio.run(service.save(interaction, "game", "3. Play fair with the loot economy."))
            self.assertEqual(database.rules_emojis(3), {"game": "<:game:42>", "emblem": "<:spinback:7>"})
            self.assertNotIn("panel icon", interaction.response.send_message.calls[2][0][0])

    def test_rules_emoji_overrides_are_stored_per_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()

            self.assertEqual(database.rules_emojis(3), {})
            self.assertEqual(
                database.set_rules_emojis(3, discord="<:discord:1>", emblem="<:spinback:2>"),
                {"discord": "<:discord:1>", "emblem": "<:spinback:2>"},
            )
            self.assertEqual(database.rules_emojis(3), {"discord": "<:discord:1>", "emblem": "<:spinback:2>"})
            self.assertEqual(database.set_rules_emojis(3, discord=""), {"emblem": "<:spinback:2>"})
            self.assertEqual(database.rules_emojis(3), {"emblem": "<:spinback:2>"})
            self.assertEqual(database.rules_emojis(99), {})
            self.assertEqual(database.rules_emojis(0), {})
            database.upsert_config(3, rules_emojis="not json")
            self.assertEqual(database.rules_emojis(3), {})

    def test_ticket_panel_follows_the_support_design(self):
        from grid_a1.embeds import PANEL_COLOUR, TICKET_PANEL_CATEGORIES, support_panel
        from grid_a1.utils import utcnow

        class FakeEmoji:
            name = "spinback"

            def __str__(self):
                return "<:spinback:9>"

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()

            guild = SimpleNamespace(id=3, name="Spinback 5X [EU]", emojis=[FakeEmoji()])
            panel = support_panel(guild, database)
            self.assertEqual(panel.title, "📩 Spinback - Support")
            self.assertEqual(panel.colour, PANEL_COLOUR)
            self.assertEqual(
                panel.footer.text,
                f"<:spinback:9> Support - Spinback 5X [EU] • {utcnow().year}",
            )
            for emoji, heading, details in TICKET_PANEL_CATEGORIES:
                self.assertIn(f"**{emoji} {heading}**\n{details}", panel.description)
            self.assertIn("plz select one of the following choices", panel.description)
            self.assertIn("`/help`", panel.description)
            self.assertNotIn("Support Status", panel.description)

            plain = support_panel(SimpleNamespace(id=4, name="Other Server", emojis=[]), database)
            self.assertEqual(
                plain.footer.text,
                f"🏓 Support - Other Server • {utcnow().year}",
            )

    def test_panels_and_messages_carry_the_server_logo(self):
        from grid_a1 import logo
        from grid_a1.embeds import support_panel, ticket_embed
        from grid_a1.welcomer import welcome_embed

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            guild = SimpleNamespace(id=8, name="Spinback 5X [EU]", emojis=[], member_count=3)
            self.assertEqual(support_panel(guild, database).image.url, "attachment://spinback-logo.jpg")
            ticket = ticket_embed("general", "NA", "Gamertag: Oll", guild_id=8, database=database)
            self.assertEqual(ticket.thumbnail.url, "attachment://spinback-logo.jpg")
            member = SimpleNamespace(mention="<@1>", display_name="Oll")
            config = {"welcome_channel": 1, "link_channel": 2, "bot_commands_channel": 3, "shop_channel": 4, "verify_channel": 5}
            welcome = welcome_embed(SimpleNamespace(user=None, database=database), guild, member, config)
            self.assertEqual(welcome.thumbnail.url, "attachment://spinback-logo.jpg")
            database.set_guild_logo(8, b"\x89PNG\r\n\x1a\n" + b"mine-picture", "spinback-logo.png", "image/png")
            uploaded = support_panel(guild, database)
            self.assertEqual(uploaded.image.url, "attachment://spinback-logo.png")
            self.assertEqual(logo.logo_file(8, database).filename, "spinback-logo.png")
            uploaded_ticket = ticket_embed("general", "EU", "Detail: x", guild_id=8, database=database)
            self.assertEqual(uploaded_ticket.thumbnail.url, "attachment://spinback-logo.png")

    def test_rules_panel_uses_the_server_logo(self):
        from grid_a1.rules import RulesService, rules_panel_view

        class FakeDatabase:
            def __init__(self, row=None):
                self.row = row

            def rule_text(self, guild_id, section):
                return None

            def rule_row(self, guild_id, section):
                return None

            def rules_emojis(self, guild_id):
                return {}

            def guild_logo(self, guild_id):
                return self.row

        guild = SimpleNamespace(id=11, emojis=[])
        default_view = rules_panel_view(RulesService(FakeDatabase()), guild)
        thumbs = [item for item in default_view.walk_children() if isinstance(item, discord.ui.Thumbnail)]
        self.assertEqual([item.media.url for item in thumbs], ["attachment://spinback-logo.jpg"])
        row = {"logo": b"\x89PNG\r\n\x1a\n" + b"mine-picture", "filename": "spinback-logo.png", "content_type": "image/png"}
        uploaded_view = rules_panel_view(RulesService(FakeDatabase(row)), guild)
        thumbs = [item for item in uploaded_view.walk_children() if isinstance(item, discord.ui.Thumbnail)]
        self.assertEqual([item.media.url for item in thumbs], ["attachment://spinback-logo.png"])

    def test_setup_logo_is_guided_and_documented(self):
        import grid_a1.bot as bot_module
        from grid_a1 import guides

        tree = bot_module.bot.tree
        self.assertEqual(sorted(command.name for command in tree.get_command("setup").commands), ["logo", "roles", "status", "tickets", "welcomer"])
        entries = {entry.path: entry for entry in guides.catalog(tree)}
        self.assertIn("setup logo", entries)
        self.assertEqual(entries["setup logo"].access, guides.ADMIN)
        rendered = "\n".join(value for _, value in guides.render_guide(guides.catalog(tree), list(guides.ACCESS_LABELS), max_total=20000))
        self.assertIn("/setup logo", rendered)

    def test_docs_describe_the_server_logo(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "SETUP_GUIDE.md").read_text(encoding="utf-8")
        project_map = (ROOT / "PROJECT_MAP.md").read_text(encoding="utf-8")
        for text in (readme, guide):
            self.assertIn("/setup logo image:", text)
            self.assertIn("512 KB", text)
            self.assertIn("bundled", text)
        self.assertIn("### Your own logo", readme)
        self.assertIn("## Your own logo", guide)
        self.assertIn("schema version is 26", project_map)
        self.assertIn("grid_a1/logo.py", project_map)
        self.assertIn("grid_a1/brand_logo.py", project_map)

    def test_new_utility_commands_are_registered_with_plain_descriptions(self):
        import grid_a1.bot as bot_module
        from grid_a1 import guides

        tree = bot_module.bot.tree
        info = tree.get_command("info")
        self.assertEqual(sorted(command.name for command in info.commands), ["bot", "server", "user"])
        for name in ("avatar", "slowmode", "modstats"):
            self.assertIsNotNone(tree.get_command(name), name)

        entries = {entry.path: entry for entry in guides.catalog(tree)}
        for command_path in ("info user", "info bot", "avatar", "slowmode", "modstats"):
            self.assertIn(command_path, entries)
            self.assertTrue(entries[command_path].description, command_path)
            self.assertFalse(
                any(ord(character) > 0x2100 for character in entries[command_path].description),
                command_path,
            )
        rendered = "\n".join(
            value
            for _, value in guides.render_guide(
                guides.catalog(tree),
                list(guides.ACCESS_LABELS),
                max_total=8000,
            )
        )
        for command_path in ("/info user", "/info bot", "/avatar", "/slowmode", "/modstats"):
            self.assertIn(command_path, rendered)

    def test_staff_only_commands_refuse_members_without_a_configured_role(self):
        import grid_a1.bot as bot_module
        from discord import app_commands

        for name in ("slowmode", "modstats"):
            command = bot_module.bot.tree.get_command(name)
            self.assertTrue(command.checks, name)
            self.assertIn("staff_member", inspect.getsource(command.checks[0]))

        class FakeMember:
            def __init__(self):
                self.id = 4242
                self.roles = ()
                self.guild = SimpleNamespace(id=77, owner_id=1)

        class FakeInteraction:
            def __init__(self):
                self.guild = SimpleNamespace(id=77, owner_id=1)
                self.user = FakeMember()

        command = bot_module.bot.tree.get_command("slowmode")
        with self.assertRaises(app_commands.CheckFailure):
            asyncio.run(command.checks[0](FakeInteraction()))

    def test_moderation_activity_summary_counts_actions_and_moderators(self):
        import grid_a1.bot as bot_module
        from datetime import timedelta

        from grid_a1.utils import utcnow

        class FakeResponse:
            def __init__(self):
                self.embed = None
                self.ephemeral = None

            async def send_message(self, embed=None, ephemeral=False, **kwargs):
                self.embed = embed
                self.ephemeral = ephemeral

        class FakeInteraction:
            def __init__(self):
                self.guild = SimpleNamespace(id=77)
                self.user = SimpleNamespace(id=9001, __str__=lambda self: "moderator")
                self.response = FakeResponse()

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            database.audit(77, None, 9001, "kick", '{"member=5"}')
            database.audit(77, None, 9001, "warn", '{"member=6"}')
            database.audit(77, None, 9002, "warn", '{"member=7"}')
            database.audit(77, None, 9001, "slowmode", "seconds=5")

            since = (utcnow() - timedelta(days=7)).isoformat()
            rows = database.moderation_activity(77, since)
            self.assertEqual([row["action"] for row in rows], ["warn", "warn", "kick"])
            self.assertEqual(database.moderation_activity(77, utcnow().isoformat()), [])

            command = bot_module.bot.tree.get_command("modstats")
            interaction = FakeInteraction()
            original = bot_module.bot.database
            bot_module.bot.database = database
            try:
                asyncio.run(command.callback(interaction, 7))
            finally:
                bot_module.bot.database = original

            card = interaction.response.embed
            self.assertEqual(card.description, "Recorded actions in the last 7 day(s): 3")
            fields = {field.name: field.value for field in card.fields}
            self.assertEqual(fields["By action"], "warn: 2\nkick: 1")
            self.assertIn("<@9001>: 2", fields["Most active moderators"])
            self.assertIn("Newest record", fields)

    def test_ticket_regions_cover_eu_and_na(self):
        from grid_a1.utils import TICKET_REGIONS, TICKET_REGION_VALUES, region_display, region_flag, region_hint
        from grid_a1.views import RegionSelect

        self.assertEqual([value for value, _, _ in TICKET_REGIONS], ["EU", "NA"])
        self.assertEqual(TICKET_REGION_VALUES, frozenset({"EU", "NA"}))
        self.assertEqual(region_flag("na"), "🇺🇸")
        self.assertEqual(region_display("EU"), "🇪🇺 EU")
        self.assertEqual(region_display("Asia"), "🌍 ASIA")
        self.assertIn("North American", region_hint("NA"))

        select = RegionSelect(SimpleNamespace(), "general", "Ticket General")
        self.assertEqual([option.value for option in select.options], ["EU", "NA"])
        self.assertTrue(all(option.emoji for option in select.options))
        self.assertTrue(all(option.description for option in select.options))
        self.assertEqual(select.custom_id, "grid-a1:ticket:region")

    def test_ticket_embed_shows_the_region_that_was_chosen(self):
        from grid_a1.embeds import ticket_embed

        for region, flag in (("EU", "🇪🇺"), ("NA", "🇺🇸")):
            panel = ticket_embed("general", region, "Details of the problem")
            fields = {field.name: field.value for field in panel.fields}
            self.assertEqual(fields["🌍 Region"], f"{flag} {region}")

    def test_ticket_creation_refuses_regions_outside_eu_and_na(self):
        from grid_a1.tickets import TicketService

        class FakeResponse:
            def is_done(self):
                return False

            async def defer(self, ephemeral=False):
                return None

        class FakeFollowup:
            def __init__(self):
                self.messages = []

            async def send(self, content=None, **kwargs):
                self.messages.append(content)

        class FakeInteraction:
            def __init__(self):
                self.guild = SimpleNamespace(id=77)
                self.user = SimpleNamespace(id=900, name="olly", display_name="Olly", mention="<@900>")
                self.channel = SimpleNamespace(id=1)
                self.response = FakeResponse()
                self.followup = FakeFollowup()

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "manager.sqlite3")
            database.migrate()
            service = TicketService(database)
            interaction = FakeInteraction()
            asyncio.run(service.create(interaction, "general", "Ticket General", "ASIA", "details"))
            self.assertEqual(
                interaction.followup.messages,
                ["❌ Choose EU or NA support and try again."],
            )

    def test_every_shipped_file_uses_the_spinback_brand(self):
        stale = ("Grid A1", "grid-a1-manager")
        for path in sorted((ROOT / "grid_a1").glob("*.py")) + [
            ROOT / "README.md",
            ROOT / "PROJECT_MAP.md",
            ROOT / "SETUP_GUIDE.md",
        ]:
            source = path.read_text(encoding="utf-8")
            for needle in stale:
                self.assertNotIn(needle, source, f"{path.name} still mentions {needle}")


if __name__ == "__main__":
    unittest.main()
