"""Persistent Discord polls with one-changeable-vote-per-member semantics."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import discord

from .database import Database
from .embeds import NEON_PURPLE
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
            await interaction.response.send_message("🔒 Open your own poll dashboard with `/poll dashboard`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create Poll", style=discord.ButtonStyle.success, emoji="✨", row=0)
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

    @discord.ui.button(label="View Active", style=discord.ButtonStyle.primary, emoji="👀", row=0)
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
