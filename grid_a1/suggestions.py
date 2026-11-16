"""Member suggestions: storage helpers, the vote view, and the submit modal."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import discord

from .embeds import embed
from .stats import timestamp_marker
from .utils import suggestion_custom_id, utcnow

log = logging.getLogger(__name__)

MAX_BODY = 1000
MIN_BODY = 3


def new_suggestion_id() -> str:
    """Short identifier shown on every suggestion."""
    return uuid.uuid4().hex[:6].upper()


def suggestion_embed(row: Any, counts: dict[str, int], viewer_vote: int = 0, message: str | None = None) -> discord.Embed:
    """Public panel for one suggestion with its running vote totals."""
    up = int(counts.get("up", 0))
    down = int(counts.get("down", 0))
    total = up + down
    result = embed(
        f"💡 Suggestion {row['suggestion_id']}",
        message or discord.utils.escape_markdown(str(row["body"]))[:3900],
        discord.Colour.from_rgb(177, 77, 255),
    )
    result.add_field(name="Submitted by", value=f"<@{int(row['author_id'])}>", inline=True)
    result.add_field(name="Submitted", value=timestamp_marker(row["created_at"]), inline=True)
    result.add_field(name="Status", value="🟢 open" if str(row["status"]) == "open" else f"🔒 {row['status']}", inline=True)
    share = f"{round(up / total * 100)}% of {total} vote(s) agree" if total else "No votes yet — be the first"
    result.add_field(name="👍 Yes", value=f"**{up}**", inline=True)
    result.add_field(name="👎 No", value=f"**{down}**", inline=True)
    result.add_field(name="Where it stands", value=share, inline=True)
    if viewer_vote:
        result.add_field(name="Your vote", value="👍 yes" if viewer_vote > 0 else "👎 no", inline=False)
    result.set_footer(text="Press a button to vote • press the same button again to take your vote back")
    return result


def suggestion_line(row: Any, counts: dict[str, int]) -> str:
    """One listing line for a suggestion."""
    body = discord.utils.escape_markdown(" ".join(str(row["body"]).split()))[:110]
    return (
        f"💡 {body}\n"
        f"┗ {row['suggestion_id']} • 👍 {int(counts.get('up', 0))} / 👎 {int(counts.get('down', 0))} • "
        f"{timestamp_marker(row['created_at'])}"
    )


class SuggestionVoteView(discord.ui.View):
    """Persistent vote buttons attached to a published suggestion."""

    def __init__(self, service: "SuggestionService", suggestion_id: str):
        super().__init__(timeout=None)
        self.service = service
        self.suggestion_id = suggestion_id
        yes = discord.ui.Button(label="Yes please", style=discord.ButtonStyle.success, emoji="👍", custom_id=suggestion_custom_id("up", suggestion_id))
        yes.callback = self._vote_yes
        no = discord.ui.Button(label="Not for me", style=discord.ButtonStyle.danger, emoji="👎", custom_id=suggestion_custom_id("down", suggestion_id))
        no.callback = self._vote_no
        self.add_item(yes)
        self.add_item(no)

    async def _record(self, interaction: discord.Interaction, value: int) -> None:
        if not interaction.guild:
            return await interaction.response.send_message("❌ Suggestions can only be voted on inside the server.", ephemeral=True)
        row = self.service.database.suggestion(interaction.guild.id, self.suggestion_id)
        if row is None:
            return await interaction.response.send_message("⚠️ That suggestion is no longer available.", ephemeral=True)
        try:
            stored = self.service.database.record_suggestion_vote(self.suggestion_id, interaction.user.id, value, utcnow().isoformat())
        except Exception:
            log.exception("Could not record a suggestion vote for %s", self.suggestion_id)
            return await interaction.response.send_message("⚠️ I could not save your vote right now. Please try again.", ephemeral=True)
        counts = self.service.database.suggestion_votes(self.suggestion_id)
        note = "👍 Your yes vote is counted — press it again to take it back." if stored > 0 else (
            "👎 Your no vote is counted — press it again to take it back." if stored < 0 else "↩️ Your vote was removed."
        )
        embed_result = suggestion_embed(row, counts, viewer_vote=stored)
        embed_result.description = f"{embed_result.description}\n\n**{note}**"
        await interaction.response.edit_message(embed=embed_result, view=self)

    async def _vote_yes(self, interaction: discord.Interaction) -> None:
        await self._record(interaction, 1)

    async def _vote_no(self, interaction: discord.Interaction) -> None:
        await self._record(interaction, -1)


class SuggestionModal(discord.ui.Modal, title="Send a suggestion"):
    """Collect one suggestion and publish it for voting."""

    def __init__(self, service: "SuggestionService"):
        super().__init__(timeout=600.0)
        self.service = service
        self.body = discord.ui.TextInput(
            label="Your suggestion",
            style=discord.TextStyle.paragraph,
            placeholder="What should change, and why would it help?",
            max_length=MAX_BODY,
            required=True,
        )
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.service.publish(interaction, str(self.body.value))


class SuggestionService:
    """Store, publish, list, and vote on member suggestions."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def create(self, guild_id: int, author_id: int, body: str) -> Any:
        """Validate and store a suggestion, returning the stored row."""
        text = " ".join((body or "").split())
        if len(text) < MIN_BODY:
            raise ValueError("Write a little more so staff know what you are suggesting.")
        if len(text) > MAX_BODY:
            raise ValueError(f"Suggestions are limited to {MAX_BODY} characters.")
        suggestion_id = new_suggestion_id()
        self.database.create_suggestion(
            suggestion_id=suggestion_id,
            guild_id=guild_id,
            author_id=author_id,
            body=text,
            created_at=utcnow().isoformat(),
        )
        stored = self.database.suggestion(guild_id, suggestion_id)
        if stored is None:
            raise ValueError("The suggestion could not be saved. Please try again.")
        return stored

    def listing(self, guild_id: int, limit: int = 15) -> list[Any]:
        return self.database.guild_suggestions(guild_id, limit=limit)

    async def publish(self, interaction: discord.Interaction, body: str) -> None:
        """Store a suggestion and post it to the configured channel for voting."""
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Suggestions can only be sent inside a server.", ephemeral=True)
        config = self.database.config(guild.id)
        channel_id = config["suggestion_channel"] if config else None
        if not channel_id:
            return await interaction.response.send_message(
                "⚠️ No suggestion channel is set yet. Ask an admin to run /suggestion config first.",
                ephemeral=True,
            )
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "⚠️ The saved suggestion channel is missing. Ask an admin to run /suggestion config again.",
                ephemeral=True,
            )
        try:
            row = self.create(guild.id, interaction.user.id, body)
        except ValueError as error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        panel = suggestion_embed(row, {"up": 0, "down": 0})
        try:
            message = await channel.send(embed=panel, view=SuggestionVoteView(self, row["suggestion_id"]))
        except discord.Forbidden:
            return await interaction.followup.send(
                f"❌ I cannot post in {channel.mention}. Check View Channel and Send Messages, then try again.",
                ephemeral=True,
            )
        except discord.HTTPException:
            log.exception("Suggestion %s could not be published", row["suggestion_id"])
            return await interaction.followup.send("❌ Discord refused that post. Please try again shortly.", ephemeral=True)

        self.database.attach_suggestion_message(row["suggestion_id"], channel.id, message.id)
        await interaction.followup.send(f"✅ Suggestion {row['suggestion_id']} is live in {channel.mention} — members can vote now.", ephemeral=True)
