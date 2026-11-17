"""Persistent Discord polls with one-changeable-vote-per-member semantics."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from .database import Database
from .embeds import NEON_PURPLE, embed
from .guides import build_choices
from .views import ConfirmView
from .utils import safe_json_list, utcnow

log = logging.getLogger(__name__)
_POLL_MESSAGE_LOCKS: dict[str, asyncio.Lock] = {}
MAX_POLL_OPTIONS = 10
MAX_POLL_OPTION_LENGTH = 100
MAX_POLL_QUESTION_LENGTH = 256
DEFAULT_POLL_DURATION_HOURS = 24
MAX_POLL_DURATION_HOURS = 168


def _message_lock(poll_id: str) -> asyncio.Lock:
    """Serialize poll message edits so concurrent votes cannot publish stale totals."""
    return _POLL_MESSAGE_LOCKS.setdefault(poll_id, asyncio.Lock())


def parse_poll_options(raw_options: str) -> list[str]:
    """Parse pipe-separated choices and reject empty, duplicated, or oversized options."""
    options = [choice.strip() for choice in raw_options.split("|") if choice.strip()]
    if not 2 <= len(options) <= MAX_POLL_OPTIONS:
        raise ValueError("A poll needs between 2 and 10 choices, separated by `|`.")
    if any(len(choice) > MAX_POLL_OPTION_LENGTH for choice in options):
        raise ValueError("Each poll choice must be 100 characters or fewer.")

    normalized = [choice.casefold() for choice in options]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Poll choices must be unique.")
    return options


def poll_embed(
    poll,
    options: list[str],
    vote_counts: dict[int, int],
) -> discord.Embed:
    """Render open and ended polls consistently, including live vote totals."""
    total_votes = sum(vote_counts.values())
    is_open = poll["status"] == "open"
    state = "🟢 Voting is open" if is_open else "🔒 Voting has ended"
    saved_description = (poll["description"] or "").strip() if "description" in poll.keys() else ""
    description = (
        (f"{saved_description}\n\n" if saved_description else "")
        + f"{state}\n"
        + "Choose an option below. You can change your vote at any time while voting is open."
    )
    result = discord.Embed(
        title=f"📊 {poll['question']}"[:256],
        description=description,
        colour=NEON_PURPLE,
    )

    for index, option in enumerate(options):
        votes = vote_counts.get(index, 0)
        percentage = round((votes / total_votes) * 100) if total_votes else 0
        filled_blocks = round((votes / total_votes) * 12) if total_votes else 0
        progress_bar = "█" * filled_blocks + "░" * (12 - filled_blocks)
        result.add_field(
            name=f"🔹 {option}"[:256],
            value=f"{progress_bar}  **{votes}** votes • **{percentage}%**",
            inline=False,
        )

    result.add_field(
        name="🗳️ Total votes",
        value=f"**{total_votes}**",
        inline=True,
    )
    try:
        displayed_at = (
            poll["ends_at"]
            if is_open
            else poll["ended_at"] or poll["ends_at"]
        )
        closes_at = datetime.fromisoformat(displayed_at)
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        time_label = "⏰ Closes" if is_open else "🕒 Closed at"
        timestamp = int(closes_at.timestamp())
        result.add_field(
            name=time_label,
            value=f"<t:{timestamp}:R>",
            inline=True,
        )
    except (TypeError, ValueError, OverflowError):
        # A malformed legacy date should not prevent users from seeing poll results.
        pass

    footer = f"Poll ID: {poll['poll_id']} • One vote per member"
    if is_open:
        footer += " • Change vote anytime"
    result.set_footer(text=footer)
    return result


class PollVoteSelect(discord.ui.Select):
    """A persistent selector that lets a member cast or replace one poll vote."""

    def __init__(
        self,
        database: Database,
        poll_id: str,
        options: list[str],
        *,
        disabled: bool = False,
    ) -> None:
        self.database = database
        self.poll_id = poll_id
        select_options = [
            discord.SelectOption(
                label=option[:MAX_POLL_OPTION_LENGTH],
                value=str(index),
                description=f"Vote for choice {index + 1}",
            )
            for index, option in enumerate(options)
        ]
        super().__init__(
            placeholder="🗳️ Cast or change your vote…",
            min_values=1,
            max_values=1,
            options=select_options,
            custom_id=f"grid-a1:poll:{poll_id}:vote",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Record a vote, refresh the public tally, and acknowledge privately."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Poll voting is only available inside its server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        poll = self.database.poll_for_guild(guild.id, self.poll_id)
        if not poll:
            await interaction.followup.send(
                "⚠️ This poll no longer exists.",
                ephemeral=True,
            )
            return

        try:
            option_index = int(self.values[0])
        except (IndexError, TypeError, ValueError):
            await interaction.followup.send(
                "❌ That poll choice is invalid. Please refresh the poll and try again.",
                ephemeral=True,
            )
            return

        result = self.database.cast_poll_vote(
            self.poll_id,
            interaction.user.id,
            option_index,
            utcnow().isoformat(),
        )
        if result == "expired":
            await self._refresh_ended_message(interaction)
            await interaction.followup.send(
                "⏰ Voting has ended; your vote was not recorded.",
                ephemeral=True,
            )
            return
        if result == "closed":
            await self._refresh_ended_message(interaction)
            await interaction.followup.send(
                "🔒 This poll has ended; votes are no longer accepted.",
                ephemeral=True,
            )
            return
        if result == "invalid_option":
            await interaction.followup.send(
                "❌ That choice is no longer available. Please refresh the poll.",
                ephemeral=True,
            )
            return
        if result == "missing":
            await interaction.followup.send(
                "⚠️ This poll no longer exists.",
                ephemeral=True,
            )
            return

        try:
            async with _message_lock(self.poll_id):
                updated_poll = self.database.poll_for_guild(guild.id, self.poll_id)
                if not updated_poll:
                    await interaction.followup.send(
                        "⚠️ This poll was removed before the tally refreshed; your vote is no longer stored.",
                        ephemeral=True,
                    )
                    return

                choices = safe_json_list(updated_poll["options_json"], str)
                refreshed_view = PollVoteView(
                    self.database,
                    self.poll_id,
                    choices,
                    disabled=updated_poll["status"] != "open",
                )
                if interaction.message:
                    await interaction.message.edit(
                        embed=poll_embed(
                            updated_poll,
                            choices,
                            self.database.poll_results(self.poll_id),
                        ),
                        view=refreshed_view,
                    )
        except discord.DiscordException:
            log.exception("Vote for poll %s saved but its display could not refresh", self.poll_id)
            await interaction.followup.send(
                "✅ Your vote was saved. I could not refresh the public tally; ask a poll manager to update the panel.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "✅ Your vote is saved. Select another choice later if you want to change it.",
            ephemeral=True,
        )

    async def _refresh_ended_message(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Disable an expired poll's selector when the final voter reaches it."""
        guild = interaction.guild
        if not guild or not interaction.message:
            return
        try:
            async with _message_lock(self.poll_id):
                ended_poll = self.database.poll_for_guild(guild.id, self.poll_id)
                if not ended_poll:
                    return
                options = safe_json_list(ended_poll["options_json"], str)
                await interaction.message.edit(
                    embed=poll_embed(
                        ended_poll,
                        options,
                        self.database.poll_results(self.poll_id),
                    ),
                    view=PollVoteView(
                        self.database,
                        self.poll_id,
                        options,
                        disabled=True,
                    ),
                )
        except discord.DiscordException:
            log.info("Expired poll %s could not disable its old message", self.poll_id)


class PollVoteView(discord.ui.View):
    """Persistent poll vote controls restored by the bot after restarts."""

    def __init__(
        self,
        database: Database,
        poll_id: str,
        options: list[str],
        *,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.add_item(
            PollVoteSelect(
                database,
                poll_id,
                options,
                disabled=disabled,
            )
        )


class PollService:
    """Publish and refresh persistent poll messages without owning command policy."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def create(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        creator_id: int,
        question: str,
        options: list[str],
        duration_hours: int,
        description: str = "",
    ) -> tuple[str, discord.Message]:
        """Persist a poll, publish its message, and roll back on a Discord failure."""
        poll_id = uuid.uuid4().hex[:10].upper()
        created_at = utcnow()
        ends_at = created_at + timedelta(hours=duration_hours)
        self.database.create_poll(
            poll_id=poll_id,
            guild_id=guild.id,
            channel_id=channel.id,
            creator_id=creator_id,
            question=question,
            description=description,
            options_json=json.dumps(options, ensure_ascii=False),
            created_at=created_at.isoformat(),
            ends_at=ends_at.isoformat(),
        )

        poll = self.database.poll_for_guild(guild.id, poll_id)
        if not poll:
            self.database.remove_poll(poll_id)
            raise RuntimeError("Poll record could not be loaded after creation.")

        message = None
        try:
            message = await channel.send(
                embed=poll_embed(poll, options, {}),
                view=PollVoteView(self.database, poll_id, options),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if not self.database.set_poll_message_id(poll_id, message.id):
                raise RuntimeError("Poll message could not be linked to its database record.")
        except Exception:
            if message is not None:
                try:
                    await message.delete(reason="Poll setup did not complete")
                except discord.DiscordException:
                    log.exception("Could not remove unlinked poll message %s", getattr(message, "id", "unknown"))
            self.database.remove_poll(poll_id)
            raise

        return poll_id, message

    async def update_message(
        self,
        guild: discord.Guild,
        poll_id: str,
        *,
        disabled: bool,
    ) -> bool:
        """Refresh a poll's public embed and, when closed, disable its vote selector."""
        async with _message_lock(poll_id):
            poll = self.database.poll_for_guild(guild.id, poll_id)
            if not poll or not poll["message_id"]:
                return False

            channel = guild.get_channel(poll["channel_id"])
            if channel is None:
                try:
                    channel = await guild.fetch_channel(poll["channel_id"])
                except discord.NotFound:
                    return False
            if not isinstance(channel, discord.TextChannel):
                return False

            try:
                message = await channel.fetch_message(poll["message_id"])
                options = safe_json_list(poll["options_json"], str)
                await message.edit(
                    embed=poll_embed(
                        poll,
                        options,
                        self.database.poll_results(poll_id),
                    ),
                    view=PollVoteView(
                        self.database,
                        poll_id,
                        options,
                        disabled=disabled or poll["status"] != "open",
                    ),
                )
                return True
            except discord.NotFound:
                return False

    async def finish(
        self,
        guild: discord.Guild,
        poll_id: str,
        ended_by: int,
    ) -> tuple[sqlite3.Row | None, bool, bool]:
        """End a poll idempotently and render its final results if possible."""
        poll = self.database.poll_for_guild(guild.id, poll_id)
        if not poll:
            return None, False, False

        changed = self.database.end_poll(
            poll_id,
            ended_by,
            utcnow().isoformat(),
        )
        poll = self.database.poll_for_guild(guild.id, poll_id)
        if not poll:
            return None, changed, False

        try:
            message_updated = await self.update_message(
                guild,
                poll_id,
                disabled=True,
            )
        except discord.DiscordException:
            log.exception("Poll %s ended but its public message could not refresh", poll_id)
            message_updated = False
        return poll, changed, message_updated

    async def remove_public_message(
        self,
        guild: discord.Guild,
        poll,
    ) -> bool:
        """Delete the public poll post before its database row is removed."""
        async with _message_lock(poll["poll_id"]):
            if not poll["message_id"]:
                return True

            channel = guild.get_channel(poll["channel_id"])
            if channel is None:
                try:
                    channel = await guild.fetch_channel(poll["channel_id"])
                except discord.NotFound:
                    return True
            if not isinstance(channel, discord.TextChannel):
                return True

            try:
                message = await channel.fetch_message(poll["message_id"])
            except discord.NotFound:
                return True
            await message.delete(reason=f"Poll {poll['poll_id']} removed by its manager")
            return True


class PollMetadataModal(discord.ui.Modal):
    """Gather the poll title and description shown in the reference screens."""

    def __init__(self, database: Database, service: PollService, guild_id: int, user_id: int, fallback_channel_id: int):
        super().__init__(title="Poll Meta Data")
        self.database = database
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.fallback_channel_id = fallback_channel_id
        settings = database.poll_settings(guild_id)
        default_duration = int(settings["default_duration_hours"] if settings else DEFAULT_POLL_DURATION_HOURS)
        self.title_input = discord.ui.TextInput(label="Poll Title", max_length=MAX_POLL_QUESTION_LENGTH, required=True)
        self.description_input = discord.ui.TextInput(label="Poll Description (Optional)", style=discord.TextStyle.paragraph, max_length=1000, required=False)
        self.options_input = discord.ui.TextInput(label="Choices (separate with |)", placeholder="Island | Ragnarok", max_length=1000, required=True)
        self.duration_input = discord.ui.TextInput(label="Duration in hours", default=str(default_duration), max_length=3, required=True)
        for item in (self.title_input, self.description_input, self.options_input, self.duration_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id or interaction.user.id != self.user_id:
            return await interaction.response.send_message("🔒 This poll form belongs to a different user or server.", ephemeral=True)

        question = str(self.title_input.value).strip()
        description = str(self.description_input.value or "").strip()
        if not question:
            return await interaction.response.send_message("❌ Poll title cannot be empty.", ephemeral=True)
        try:
            options = parse_poll_options(str(self.options_input.value))
            duration_hours = int(str(self.duration_input.value).strip())
            if not 1 <= duration_hours <= MAX_POLL_DURATION_HOURS:
                raise ValueError(f"Poll duration must be between 1 and {MAX_POLL_DURATION_HOURS} hours.")
        except ValueError as error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)

        settings = self.database.poll_settings(guild.id)
        channel_id = settings["channel_id"] if settings and settings["channel_id"] else self.fallback_channel_id
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("⚠️ Choose a valid poll channel with `/poll config`, or reopen the dashboard from a text channel.", ephemeral=True)
        if guild.me:
            permissions = channel.permissions_for(guild.me)
            if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
                return await interaction.response.send_message("⚠️ The bot needs View Channel, Send Messages, and Embed Links in the poll channel.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            poll_id, message = await self.service.create(
                guild,
                channel,
                interaction.user.id,
                question,
                options,
                duration_hours,
                description=description,
            )
        except discord.DiscordException as error:
            log.exception("Dashboard poll creation failed in guild %s: %s", guild.id, error)
            return await interaction.followup.send("❌ I couldn't publish that poll. Check the bot's channel permissions and try again.", ephemeral=True)
        except Exception as error:
            log.exception("Dashboard poll creation failed in guild %s: %s", guild.id, error)
            return await interaction.followup.send("❌ I couldn't save that poll. Please check the poll channel and try again.", ephemeral=True)

        result = discord.Embed(
            title="✅ Poll created",
            description=f"**{question}**\n📣 {channel.mention}\n⏳ {duration_hours} hours\n[Jump to poll]({message.jump_url})\nID: `{poll_id}`",
            colour=discord.Colour.green(),
        )
        await interaction.followup.send(embed=result, ephemeral=True)


def poll_dashboard_embed(database: Database, guild: discord.Guild) -> discord.Embed:
    settings = database.poll_settings(guild.id)
    configured_channel_id = settings["channel_id"] if settings else None
    configured_channel = guild.get_channel(configured_channel_id) if configured_channel_id else None
    channel_text = configured_channel.mention if configured_channel else (f"<#{configured_channel_id}>" if configured_channel_id else "Command channel")
    duration = int(settings["default_duration_hours"] if settings else DEFAULT_POLL_DURATION_HOURS)
    result = discord.Embed(
        title=f"📊 Poll Dashboard — {guild.name}",
        description=(
            "Create polls, review active polls, or configure defaults. Polls support 2–10 choices, "
            "live totals, one changeable vote per member, and automatic expiration."
        ),
        colour=NEON_PURPLE,
    )
    result.add_field(name="Active polls", value=str(len(database.active_polls(guild.id))), inline=True)
    result.add_field(name="Default channel", value=channel_text, inline=True)
    result.add_field(name="Default duration", value=f"{duration} hours", inline=True)
    return result


def poll_settings_embed(database: Database, guild: discord.Guild) -> discord.Embed:
    settings = database.poll_settings(guild.id)
    channel_id = settings["channel_id"] if settings else None
    channel = guild.get_channel(channel_id) if channel_id else None
    channel_text = channel.mention if channel else (f"<#{channel_id}>" if channel_id else "Command channel")
    duration = int(settings["default_duration_hours"] if settings else DEFAULT_POLL_DURATION_HOURS)
    result = discord.Embed(
        title="⚙️ Poll Defaults",
        description="These defaults apply to new polls created from the dashboard. A poll's modal can override its duration.",
        colour=NEON_PURPLE,
    )
    result.add_field(name="📣 Default channel", value=channel_text, inline=False)
    result.add_field(name="⏳ Default duration", value=f"{duration} hours", inline=False)
    return result


class PollSettingsView(discord.ui.View):
    """Private editor for defaults opened from the `/poll config` dashboard."""

    def __init__(self, database: Database, service: PollService, guild_id: int, user_id: int, fallback_channel_id: int):
        super().__init__(timeout=600)
        self.database = database
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.fallback_channel_id = fallback_channel_id
        self.add_item(PollDefaultChannelSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("🔒 These poll settings belong to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Set Default Duration", style=discord.ButtonStyle.primary, emoji="⏳", row=1)
    async def set_duration(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            PollDefaultDurationModal(
                self.database,
                self.service,
                self.guild_id,
                self.user_id,
                self.fallback_channel_id,
            )
        )

    @discord.ui.button(label="Use Command Channel", style=discord.ButtonStyle.secondary, row=1)
    async def clear_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            self.database.clear_poll_channel(self.guild_id)
        except Exception:
            log.exception("Could not clear the default poll channel for guild %s", self.guild_id)
            return await interaction.response.send_message("❌ I couldn't update the default poll channel. Try again.", ephemeral=True)
        await interaction.response.edit_message(
            embed=poll_settings_embed(self.database, interaction.guild),
            view=self,
        )

    @discord.ui.button(label="Back to Poll Config", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=poll_dashboard_embed(self.database, interaction.guild),
            view=PollDashboardView(
                self.database,
                self.service,
                self.guild_id,
                self.user_id,
                self.fallback_channel_id,
            ),
        )


class PollDefaultChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, settings_view: PollSettingsView):
        self.settings_view = settings_view
        super().__init__(
            placeholder="Select a default poll channel…",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        selected = self.values[0]
        channel = guild.get_channel(int(selected.id)) if guild else None
        if channel is None and guild:
            try:
                channel = await guild.fetch_channel(int(selected.id))
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("⚠️ Choose a text channel the bot can access.", ephemeral=True)
        try:
            self.settings_view.database.upsert_poll_settings(guild.id, channel_id=channel.id)
        except Exception:
            log.exception("Could not update the default poll channel for guild %s", guild.id)
            return await interaction.response.send_message("❌ I couldn't save that default channel. Try again.", ephemeral=True)
        await interaction.response.edit_message(
            embed=poll_settings_embed(self.settings_view.database, guild),
            view=self.settings_view,
        )


class PollDefaultDurationModal(discord.ui.Modal):
    def __init__(self, database: Database, service: PollService, guild_id: int, user_id: int, fallback_channel_id: int):
        super().__init__(title="Default Poll Duration")
        self.database = database
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.fallback_channel_id = fallback_channel_id
        settings = database.poll_settings(guild_id)
        duration = int(settings["default_duration_hours"] if settings else DEFAULT_POLL_DURATION_HOURS)
        self.duration_input = discord.ui.TextInput(
            label="Default duration in hours (1–168)",
            default=str(duration),
            max_length=3,
            required=True,
        )
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or interaction.guild.id != self.guild_id or interaction.user.id != self.user_id:
            return await interaction.response.send_message("🔒 These poll settings belong to someone else.", ephemeral=True)
        try:
            duration = int(str(self.duration_input.value).strip())
            if not 1 <= duration <= MAX_POLL_DURATION_HOURS:
                raise ValueError(f"Choose a default duration between 1 and {MAX_POLL_DURATION_HOURS} hours.")
        except ValueError as error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        try:
            self.database.upsert_poll_settings(self.guild_id, default_duration_hours=duration)
        except Exception:
            log.exception("Could not update poll duration for guild %s", self.guild_id)
            return await interaction.response.send_message("❌ I couldn't save the poll duration. Try again.", ephemeral=True)
        await interaction.response.send_message(
            embed=poll_settings_embed(self.database, interaction.guild),
            view=PollSettingsView(
                self.database,
                self.service,
                self.guild_id,
                self.user_id,
                self.fallback_channel_id,
            ),
            ephemeral=True,
        )


class PollDashboardView(discord.ui.View):
    """Private poll dashboard with create and active-poll views."""

    def __init__(self, database: Database, service: PollService, guild_id: int, user_id: int, fallback_channel_id: int):
        super().__init__(timeout=600)
        self.database = database
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.fallback_channel_id = fallback_channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("🔒 Open your own poll dashboard with `/poll config`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create Poll", style=discord.ButtonStyle.success, emoji="", row=0)
    async def create_poll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            PollMetadataModal(
                self.database,
                self.service,
                self.guild_id,
                self.user_id,
                self.fallback_channel_id,
            )
        )

    @discord.ui.button(label="View Active", style=discord.ButtonStyle.primary, emoji="", row=0)
    async def view_active(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        rows = self.database.active_polls(self.guild_id)
        if not rows:
            result = discord.Embed(
                title="❌ No Active Polls",
                description="There are no active polls. Create one from this dashboard.",
                colour=discord.Colour.red(),
            )
            return await interaction.response.edit_message(embed=result, view=self)

        result = discord.Embed(
            title="📊 Active Polls",
            description=f"There are **{len(rows)}** active poll(s). Use `/poll end` or `/poll remove` to manage one.",
            colour=NEON_PURPLE,
        )
        for poll in rows[:25]:
            try:
                end_at = datetime.fromisoformat(poll["ends_at"])
                if end_at.tzinfo is None:
                    end_at = end_at.replace(tzinfo=timezone.utc)
                end_text = f"<t:{int(end_at.timestamp())}:R>"
            except (TypeError, ValueError, OverflowError):
                end_text = "end time unavailable"
            result.add_field(
                name=f"{poll['question'][:200]} • `{poll['poll_id']}`",
                value=f"📣 <#{poll['channel_id']}> • 🗳️ {sum(self.database.poll_results(poll['poll_id']).values())} votes • ⏳ {end_text}",
                inline=False,
            )
        return await interaction.response.edit_message(embed=result, view=self)

    @discord.ui.button(label="Poll Settings", style=discord.ButtonStyle.secondary, emoji="", row=0)
    async def poll_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=poll_settings_embed(self.database, interaction.guild),
            view=PollSettingsView(
                self.database,
                self.service,
                self.guild_id,
                self.user_id,
                self.fallback_channel_id,
            ),
        )


def register_poll_commands(bot, poll_group, *, admin, privileged) -> None:
    """Attach the /poll command group to the shared command tree."""
    def _may_manage_poll(interaction: discord.Interaction, poll) -> bool:
        """Allow a poll creator or server-level manager to control that poll."""
        return bool(
            interaction.guild
            and (
                interaction.user.id == poll["creator_id"]
                or privileged(interaction)
            )
        )


    @poll_group.command(
        name="config",
        description="Open the private poll configuration dashboard",
    )
    @app_commands.guild_only()
    @admin()
    async def poll_config(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Polls are only available inside a server.", ephemeral=True)
        dashboard = poll_dashboard_embed(bot.database, guild)
        fallback_channel_id = interaction.channel.id if isinstance(interaction.channel, discord.TextChannel) else 0
        view = PollDashboardView(
            bot.database,
            bot.polls,
            guild.id,
            interaction.user.id,
            fallback_channel_id,
        )
        await interaction.response.send_message(embed=dashboard, view=view, ephemeral=True)


    @poll_group.command(
        name="create",
        description="Create a poll members can vote on and change their vote",
    )
    @app_commands.guild_only()
    @admin()
    @app_commands.describe(
        question="The question shown on the poll panel (up to 256 characters)",
        options="Two to ten choices separated by | (example: Island | Ragnarok)",
        duration_hours="Optional poll duration; defaults to /poll config",
        description="Optional context shown under the poll title",
    )
    async def poll_create(
        interaction: discord.Interaction,
        question: str,
        options: str,
        duration_hours: app_commands.Range[int, 1, MAX_POLL_DURATION_HOURS] | None = None,
        description: str | None = None,
    ) -> None:
        """Validate and publish a persistent, single-choice poll."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Polls can only be created inside a server.",
                ephemeral=True,
            )
            return

        cleaned_question = question.strip()
        if not cleaned_question:
            await interaction.response.send_message(
                "❌ Poll question cannot be empty.",
                ephemeral=True,
            )
            return
        if len(cleaned_question) > MAX_POLL_QUESTION_LENGTH:
            await interaction.response.send_message(
                f"❌ Poll questions must be {MAX_POLL_QUESTION_LENGTH} characters or fewer.",
                ephemeral=True,
            )
            return

        try:
            choices = parse_poll_options(options)
        except ValueError as error:
            await interaction.response.send_message(
                f"❌ {error}",
                ephemeral=True,
            )
            return

        settings_row = bot.database.poll_settings(guild.id)
        configured_channel_id = settings_row["channel_id"] if settings_row else None
        if configured_channel_id:
            target_channel = guild.get_channel(configured_channel_id)
            if target_channel is None:
                try:
                    target_channel = await guild.fetch_channel(configured_channel_id)
                except discord.NotFound:
                    target_channel = None
                except discord.HTTPException:
                    log.exception("Could not fetch configured poll channel %s", configured_channel_id)
                    await interaction.response.send_message(
                        "⚠️ I could not check the configured poll channel. Try again shortly or choose another channel with `/poll config`.",
                        ephemeral=True,
                    )
                    return
            if not isinstance(target_channel, discord.TextChannel):
                await interaction.response.send_message(
                    "⚠️ The configured poll channel is missing or is not a text channel. Run `/poll config` to choose a new one.",
                    ephemeral=True,
                )
                return
        elif isinstance(interaction.channel, discord.TextChannel):
            target_channel = interaction.channel
        else:
            await interaction.response.send_message(
                "⚠️ Choose a default poll channel with `/poll config` before creating a poll here.",
                ephemeral=True,
            )
            return

        if guild.me:
            permissions = target_channel.permissions_for(guild.me)
            missing = [
                label
                for name, label in (
                    ("view_channel", "View Channel"),
                    ("send_messages", "Send Messages"),
                    ("embed_links", "Embed Links"),
                )
                if not getattr(permissions, name)
            ]
            if missing:
                await interaction.response.send_message(
                    f"⚠️ The bot is missing poll-channel permissions: {', '.join(missing)}.",
                    ephemeral=True,
                )
                return

        cleaned_description = (description or "").strip()
        if len(cleaned_description) > 1000:
            await interaction.response.send_message(
                "❌ Poll descriptions must be 1,000 characters or fewer.",
                ephemeral=True,
            )
            return

        configured_duration = (
            int(settings_row["default_duration_hours"])
            if settings_row
            else DEFAULT_POLL_DURATION_HOURS
        )
        poll_duration = int(duration_hours) if duration_hours is not None else configured_duration
        await interaction.response.defer(ephemeral=True)
        try:
            poll_id, message = await bot.polls.create(
                guild,
                target_channel,
                interaction.user.id,
                cleaned_question,
                choices,
                poll_duration,
                description=cleaned_description,
            )
        except discord.Forbidden:
            log.exception("Poll creation was denied in channel %s", target_channel.id)
            await interaction.followup.send(
                "❌ I could not post the poll. Check the bot's channel permissions and try again.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            log.exception("Discord rejected poll creation in guild %s: %s", guild.id, error)
            await interaction.followup.send(
                "⚠️ Discord could not publish the poll right now. Please try again shortly.",
                ephemeral=True,
            )
            return
        except Exception:
            log.exception("Poll creation failed for guild %s", guild.id)
            await interaction.followup.send(
                "❌ I could not finish creating the poll. Check the poll channel before retrying; if the post is missing, contact an admin.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=embed(
                "✅ Poll created",
                f"📊 **Poll ID:** `{poll_id}`\n"
                f"📣 **Posted in:** {target_channel.mention}\n"
                f"🔗 [Jump to poll]({message.jump_url})\n"
                f"⏳ **Duration:** {poll_duration} hours\n\n"
                "Members can change their vote until the poll closes.",
                discord.Colour.green(),
            ),
            ephemeral=True,
        )


    @poll_group.command(
        name="results",
        description="Show a poll's current results without ending it",
    )
    @app_commands.guild_only()
    @app_commands.describe(poll_id="Start typing to pick a poll, or paste its ID")
    async def poll_results(
        interaction: discord.Interaction,
        poll_id: str,
    ) -> None:
        """Show live standings to the creator or a server manager, without ending the poll."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Polls can only be managed inside a server.",
                ephemeral=True,
            )
            return

        poll = bot.database.poll_for_guild(guild.id, poll_id.strip().upper())
        if not poll:
            await interaction.response.send_message(
                "⚠️ I could not find that poll in this server. Check the poll ID and try again.",
                ephemeral=True,
            )
            return
        if not _may_manage_poll(interaction, poll):
            await interaction.response.send_message(
                "🔒 Only the poll creator or a server manager can see results before the poll ends.",
                ephemeral=True,
            )
            return

        standings = poll_embed(
            poll,
            bot.database.poll_options(poll["poll_id"]),
            bot.database.poll_results(poll["poll_id"]),
        )
        if poll["status"] == "open":
            lead = "📊 Live standings — the poll is still open, so these numbers can still change."
        else:
            lead = "🔒 Final results — this poll has ended."
        standings.description = f"{lead}\n\n{standings.description}"
        await interaction.response.send_message(embed=standings, ephemeral=True)


    @poll_group.command(
        name="end",
        description="End a poll and publish its final results",
    )
    @app_commands.guild_only()
    @app_commands.describe(poll_id="Start typing to pick an open poll, or paste its ID")
    async def poll_end(interaction: discord.Interaction, poll_id: str) -> None:
        """End a poll if the caller created it or manages the server."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Polls can only be managed inside a server.",
                ephemeral=True,
            )
            return

        poll = bot.database.poll_for_guild(guild.id, poll_id.strip().upper())
        if not poll:
            await interaction.response.send_message(
                "⚠️ I could not find that poll in this server. Check the poll ID and try again.",
                ephemeral=True,
            )
            return
        if not _may_manage_poll(interaction, poll):
            await interaction.response.send_message(
                "🔒 Only the poll creator or a server manager can end this poll.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            ended_poll, changed, message_updated = await bot.polls.finish(
                guild,
                poll["poll_id"],
                interaction.user.id,
            )
        except Exception:
            log.exception("Poll end failed for %s", poll["poll_id"])
            await interaction.followup.send(
                "❌ I could not end this poll right now. Check its status before trying again.",
                ephemeral=True,
            )
            return

        if not ended_poll:
            await interaction.followup.send(
                "⚠️ The poll was removed before it could be ended.",
                ephemeral=True,
            )
            return

        result_embed = poll_embed(
            ended_poll,
            bot.database.poll_options(ended_poll["poll_id"]),
            bot.database.poll_results(ended_poll["poll_id"]),
        )
        summary = "✅ Poll ended." if changed else "ℹ️ Poll was already ended."
        if not message_updated:
            summary += " The status was saved, but its public panel could not be refreshed."
        result_embed.description = f"{summary}\n\n{result_embed.description}"
        await interaction.followup.send(embed=result_embed, ephemeral=True)


    @poll_end.autocomplete("poll_id")
    async def poll_end_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Offer this server's open polls with their questions."""
        if interaction.guild_id is None:
            return []
        rows = bot.database.active_polls(interaction.guild_id)
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in build_choices(
                rows,
                ("question", "poll_id"),
                "poll_id",
                current,
            )
        ]


    @poll_group.command(
        name="remove",
        description="Remove a poll message and its stored votes",
    )
    @app_commands.guild_only()
    @app_commands.describe(poll_id="Start typing to pick any of this server's polls, or paste its ID")
    async def poll_remove(interaction: discord.Interaction, poll_id: str) -> None:
        """Delete the poll message and its stored votes for its creator or a manager."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Polls can only be managed inside a server.",
                ephemeral=True,
            )
            return

        normalized_poll_id = poll_id.strip().upper()
        poll = bot.database.poll_for_guild(guild.id, normalized_poll_id)
        if not poll:
            await interaction.response.send_message(
                "⚠️ I could not find that poll in this server. Check the poll ID and try again.",
                ephemeral=True,
            )
            return
        if not _may_manage_poll(interaction, poll):
            await interaction.response.send_message(
                "🔒 Only the poll creator or a server manager can remove this poll.",
                ephemeral=True,
            )
            return

        async def remove_poll(target: discord.Interaction) -> None:
            await target.response.defer(ephemeral=True)
            try:
                await bot.polls.remove_public_message(guild, poll)
            except discord.Forbidden:
                await target.followup.send(
                    "❌ I could not delete the poll message. The poll and votes were kept; check Manage Messages.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as error:
                log.warning("Could not remove poll message %s: %s", normalized_poll_id, error)
                await target.followup.send(
                    "⚠️ Discord could not remove the poll message right now. The poll data was kept.",
                    ephemeral=True,
                )
                return

            try:
                removed = bot.database.remove_poll(
                    normalized_poll_id,
                    removed_by=interaction.user.id,
                )
            except Exception:
                log.exception("Public poll %s was removed but database cleanup failed", normalized_poll_id)
                await target.followup.send(
                    "⚠️ The public message was removed, but I could not remove its stored record. Contact an admin before recreating it.",
                    ephemeral=True,
                )
                return
            if not removed:
                await target.followup.send(
                    "⚠️ The poll message was removed, but its record was already gone.",
                    ephemeral=True,
                )
                return

            await target.followup.send(
                f"🗑️ Poll `{normalized_poll_id}` and its stored votes were removed.",
                ephemeral=True,
            )

        view = ConfirmView(
            remove_poll,
            author_id=interaction.user.id,
            confirm_label="Delete this poll",
        )
        await interaction.response.send_message(
            embed=embed(
                "🗑️ Delete this poll?",
                f"Poll `{normalized_poll_id}` and the votes stored for it will be removed for everyone. This cannot be undone.",
            ),
            view=view,
            ephemeral=True,
        )
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            log.debug("Could not capture the poll confirmation prompt for its timeout message")


    @poll_remove.autocomplete("poll_id")
    async def poll_remove_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Offer every poll in this server, with its status, for removal."""
        if interaction.guild_id is None:
            return []
        rows = bot.database.guild_polls(interaction.guild_id)
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in build_choices(
                rows,
                ("question", "poll_id", "status"),
                "poll_id",
                current,
            )
        ]
