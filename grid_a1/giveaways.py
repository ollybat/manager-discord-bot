"""Persistent giveaways with a private setup wizard and audited random draws."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import discord

from .database import Database
from .utils import utcnow

log = logging.getLogger(__name__)
MAX_GIVEAWAY_WINNERS = 50
MAX_GIVEAWAY_DURATION_HOURS = 720
_DEFAULT_GIVEAWAY_DURATION_HOURS = 24
_GIVEAWAY_MESSAGE_LOCKS: dict[str, asyncio.Lock] = {}


def _giveaway_lock(giveaway_id: str) -> asyncio.Lock:
    return _GIVEAWAY_MESSAGE_LOCKS.setdefault(giveaway_id, asyncio.Lock())


def giveaway_embed(giveaway, entrant_count: int) -> discord.Embed:
    """Render an open or completed giveaway panel from its persisted row."""
    ended = giveaway["status"] != "open"
    reward = discord.utils.escape_mentions(
        discord.utils.escape_markdown(str(giveaway["reward_type"] or "Giveaway reward")[:200])
    )
    result = discord.Embed(
        title=f"🎁 Giveaway • {reward}"[:256],
        description=(
            "🔒 This giveaway has ended."
            if ended
            else "🎉 Click **Enter Giveaway** below for one entry. Winners are selected randomly when the giveaway ends."
        ),
        colour=discord.Colour.gold() if not ended else discord.Colour.dark_grey(),
    )
    result.add_field(name="🏆 Winners", value=str(giveaway["winner_count"]), inline=True)
    result.add_field(name="🎟️ Entries", value=str(entrant_count), inline=True)
    timestamp_value = giveaway["ended_at"] if ended else giveaway["ends_at"]
    try:
        end_time = datetime.fromisoformat(timestamp_value)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        label = "Ended" if ended else "Ends"
        result.add_field(name=f"⏰ {label}", value=f"<t:{int(end_time.timestamp())}:R>", inline=True)
    except (TypeError, ValueError, OverflowError):
        pass
    if ended:
        winners = giveaway["winners_json"]
        if isinstance(winners, str):
            import json
            try:
                winners = json.loads(winners)
            except (TypeError, ValueError):
                winners = []
        mentions = [f"<@{int(user_id)}>" for user_id in winners if str(user_id).isdigit()]
        result.add_field(
            name="🎉 Winner(s)",
            value=", ".join(mentions)[:1024] if mentions else "No one entered this giveaway.",
            inline=False,
        )
    result.set_footer(text=f"Giveaway ID: {giveaway['giveaway_id']} • One entry per member")
    return result


class _GiveawayEnterButton(discord.ui.Button):
    def __init__(self, service: "GiveawayService", giveaway_id: str, *, disabled: bool = False):
        self.service = service
        self.giveaway_id = giveaway_id
        super().__init__(
            label="Enter Giveaway",
            style=discord.ButtonStyle.success,
            emoji="🎟️",
            custom_id=f"grid-a1:giveaway:{giveaway_id}:enter",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Giveaway entries are only available inside the server.", ephemeral=True)
        if interaction.user.bot:
            return await interaction.response.send_message("🤖 Bot accounts cannot enter giveaways.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        state = self.service.database.enter_giveaway(
            guild.id,
            self.giveaway_id,
            interaction.user.id,
            utcnow().isoformat(),
        )
        if state == "missing":
            return await interaction.followup.send("⚠️ This giveaway no longer exists.", ephemeral=True)
        if state == "ended":
            await self.service.finish(guild, self.giveaway_id, ended_by=0, force=False)
            return await interaction.followup.send("🔒 This giveaway has ended; entries are closed.", ephemeral=True)
        if state == "already_entered":
            return await interaction.followup.send("✅ You already have an entry in this giveaway.", ephemeral=True)

        await self.service.refresh_message(guild, self.giveaway_id, interaction.message)
        await interaction.followup.send("🎟️ Your entry is recorded. Good luck!", ephemeral=True)


class GiveawayEntryView(discord.ui.View):
    """Persistent join button restored for open giveaways after restarts."""

    def __init__(self, service: "GiveawayService", giveaway_id: str, *, disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(_GiveawayEnterButton(service, giveaway_id, disabled=disabled))


class GiveawayService:
    def __init__(self, database: Database):
        self.database = database

    async def create(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        creator_id: int,
        reward_type: str,
        ping_role: discord.Role | None,
        winner_count: int,
        duration_hours: int = _DEFAULT_GIVEAWAY_DURATION_HOURS,
    ) -> tuple[str, discord.Message, datetime]:
        reward_type = reward_type.strip()
        if not reward_type or len(reward_type) > 200:
            raise ValueError("Reward type must be between 1 and 200 characters.")
        if not 1 <= int(winner_count) <= MAX_GIVEAWAY_WINNERS:
            raise ValueError(f"Choose between 1 and {MAX_GIVEAWAY_WINNERS} winners.")
        if not 1 <= int(duration_hours) <= MAX_GIVEAWAY_DURATION_HOURS:
            raise ValueError(f"Duration must be between 1 and {MAX_GIVEAWAY_DURATION_HOURS} hours.")

        giveaway_id = uuid.uuid4().hex[:10].upper()
        created_at = utcnow()
        ends_at = created_at + timedelta(hours=int(duration_hours))
        self.database.create_giveaway(
            giveaway_id=giveaway_id,
            guild_id=guild.id,
            channel_id=channel.id,
            creator_id=creator_id,
            reward_type=reward_type,
            ping_role_id=ping_role.id if ping_role else None,
            winner_count=int(winner_count),
            created_at=created_at.isoformat(),
            ends_at=ends_at.isoformat(),
        )
        row = self.database.giveaway_for_guild(guild.id, giveaway_id)
        if row is None:
            self.database.discard_unpublished_giveaway(giveaway_id)
            raise RuntimeError("Giveaway record could not be loaded after creation.")

        content = ping_role.mention if ping_role else None
        allowed_mentions = discord.AllowedMentions(roles=[ping_role]) if ping_role else discord.AllowedMentions.none()
        message = None
        try:
            message = await channel.send(
                content=content,
                embed=giveaway_embed(row, 0),
                view=GiveawayEntryView(self, giveaway_id),
                allowed_mentions=allowed_mentions,
            )
            if not self.database.set_giveaway_message_id(giveaway_id, message.id):
                raise RuntimeError("Giveaway message could not be linked to its record.")
        except Exception:
            if message is not None:
                try:
                    await message.delete(reason="Giveaway setup did not complete")
                except discord.DiscordException:
                    log.exception("Could not remove incomplete giveaway message %s", getattr(message, "id", "unknown"))
            self.database.discard_unpublished_giveaway(giveaway_id)
            raise
        return giveaway_id, message, ends_at

    async def refresh_message(
        self,
        guild: discord.Guild,
        giveaway_id: str,
        message: discord.Message | None = None,
        *,
        disabled: bool | None = None,
    ) -> bool:
        async with _giveaway_lock(giveaway_id):
            giveaway = self.database.giveaway_for_guild(guild.id, giveaway_id)
            if not giveaway or not giveaway["message_id"]:
                return False
            channel = guild.get_channel(giveaway["channel_id"])
            if channel is None:
                try:
                    channel = await guild.fetch_channel(giveaway["channel_id"])
                except discord.NotFound:
                    return False
                except discord.HTTPException:
                    log.exception("Could not resolve giveaway channel %s", giveaway["channel_id"])
                    return False
            if not isinstance(channel, discord.TextChannel):
                return False
            if message is None or message.id != giveaway["message_id"]:
                try:
                    message = await channel.fetch_message(giveaway["message_id"])
                except discord.NotFound:
                    return False
                except discord.HTTPException:
                    log.exception("Could not fetch giveaway message %s", giveaway["message_id"])
                    return False
            is_disabled = giveaway["status"] != "open" if disabled is None else disabled
            try:
                await message.edit(
                    embed=giveaway_embed(giveaway, self.database.giveaway_entry_count(giveaway_id)),
                    view=GiveawayEntryView(self, giveaway_id, disabled=is_disabled),
                )
                return True
            except discord.DiscordException:
                log.exception("Could not refresh giveaway panel %s", giveaway_id)
                return False

    async def finish(
        self,
        guild: discord.Guild,
        giveaway_id: str,
        *,
        ended_by: int = 0,
        force: bool = False,
    ) -> tuple[str, list[int], bool]:
        finalized, winners, state = self.database.finalize_giveaway(
            guild.id,
            giveaway_id,
            utcnow().isoformat(),
            ended_by=ended_by,
            force=force,
        )
        if state != "ended" or finalized is None:
            return state, winners, False

        panel_updated = await self.refresh_message(
            guild,
            giveaway_id,
            disabled=True,
        )
        channel = guild.get_channel(finalized["channel_id"])
        if channel is None:
            try:
                channel = await guild.fetch_channel(finalized["channel_id"])
            except discord.DiscordException:
                log.exception("Could not fetch ended giveaway channel %s", finalized["channel_id"])
                return state, winners, panel_updated
        if not isinstance(channel, discord.TextChannel):
            return state, winners, panel_updated

        if winners:
            mentions = " ".join(f"<@{user_id}>" for user_id in winners)
            content = f"🎉 Giveaway **{giveaway_id}** has ended! Congratulations to {mentions}."
            mentions_allowed = discord.AllowedMentions(users=True)
        else:
            content = f"🎁 Giveaway **{giveaway_id}** has ended with no entries."
            mentions_allowed = discord.AllowedMentions.none()
        try:
            await channel.send(content, allowed_mentions=mentions_allowed)
        except discord.DiscordException:
            log.exception("Giveaway %s ended but winner announcement failed", giveaway_id)
        return state, winners, panel_updated


class GiveawayDashboardView(discord.ui.View):
    """Private giveaway dashboard matching the supplied screenshots."""

    def __init__(self, service: GiveawayService, guild_id: int, user_id: int):
        super().__init__(timeout=600)
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("🔒 Open your own giveaway dashboard with `/giveaway config`.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Configure Giveaway", style=discord.ButtonStyle.success, emoji="✨", row=0)
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Step 1: Channel",
                description="Where should we post this giveaway?",
                colour=discord.Colour.blurple(),
            ),
            view=GiveawayChannelStep(self.service, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="View Active", style=discord.ButtonStyle.primary, emoji="👀", row=0)
    async def view_active(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        rows = self.service.database.active_giveaways(self.guild_id)
        if not rows:
            result = discord.Embed(
                title="❌ No Active Giveaways",
                description="There are no active giveaways. Use Configure Giveaway to create one.",
                colour=discord.Colour.red(),
            )
            return await interaction.response.edit_message(embed=result, view=self)
        result = discord.Embed(
            title="🎁 Active Giveaways",
            description=f"There are **{len(rows)}** active giveaway(s). Use `/giveaway end` to close one early.",
            colour=discord.Colour.gold(),
        )
        for row in rows[:25]:
            entries = self.service.database.giveaway_entry_count(row["giveaway_id"])
            try:
                end = datetime.fromisoformat(row["ends_at"])
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                end_text = f"<t:{int(end.timestamp())}:R>"
            except (TypeError, ValueError, OverflowError):
                end_text = "end time unavailable"
            result.add_field(
                name=f"{row['reward_type'][:150]} • `{row['giveaway_id']}`",
                value=f"🏆 {row['winner_count']} winner(s) • 🎟️ {entries} entries • ⏰ {end_text}",
                inline=False,
            )
        await interaction.response.edit_message(embed=result, view=self)


class GiveawayChannelStep(discord.ui.View):
    def __init__(self, service: GiveawayService, guild_id: int, user_id: int):
        super().__init__(timeout=600)
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(_GiveawayChannelSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("🔒 This giveaway setup belongs to someone else.", ephemeral=True)
            return False
        return True


class _GiveawayChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, wizard: GiveawayChannelStep):
        self.wizard = wizard
        super().__init__(
            placeholder="Select an announcement channel…",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        channel = interaction.guild.get_channel(int(selected.id)) if interaction.guild else None
        if channel is None and interaction.guild:
            try:
                channel = await interaction.guild.fetch_channel(int(selected.id))
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.edit_message(
                embed=discord.Embed(title="⚠️ Channel unavailable", description="Choose a text channel the bot can access.", colour=discord.Colour.orange()),
                view=self.wizard,
            )
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Step 2: Ping Role",
                description=f"Selected channel: {channel.mention}\nWhat role should we ping when the giveaway starts? You can skip this.",
                colour=discord.Colour.blurple(),
            ),
            view=GiveawayPingRoleStep(self.wizard.service, self.wizard.guild_id, self.wizard.user_id, channel),
        )


class _GiveawayRoleSelect(discord.ui.RoleSelect):
    def __init__(self, wizard: "GiveawayPingRoleStep"):
        self.wizard = wizard
        super().__init__(placeholder="Select a ping role…", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        if role.is_default():
            return await interaction.response.send_message("⚠️ Choose a specific role or use Skip / No Ping; @everyone is not accepted.", ephemeral=True)
        await interaction.response.send_modal(
            GiveawayRewardModal(
                self.wizard.service,
                self.wizard.guild_id,
                self.wizard.user_id,
                self.wizard.channel,
                role,
            )
        )


class GiveawayPingRoleStep(discord.ui.View):
    def __init__(self, service: GiveawayService, guild_id: int, user_id: int, channel: discord.TextChannel):
        super().__init__(timeout=600)
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.channel = channel
        self.add_item(_GiveawayRoleSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("🔒 This giveaway setup belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Skip / No Ping", style=discord.ButtonStyle.secondary, row=1)
    async def skip_ping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            GiveawayRewardModal(
                self.service,
                self.guild_id,
                self.user_id,
                self.channel,
                None,
            )
        )


class GiveawayRewardModal(discord.ui.Modal):
    def __init__(self, service: GiveawayService, guild_id: int, user_id: int, channel: discord.TextChannel, ping_role: discord.Role | None):
        super().__init__(title="Step 4: Reward Type")
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.channel = channel
        self.ping_role = ping_role
        self.reward_input = discord.ui.TextInput(
            label="What type of reward are we giving away?",
            placeholder="Type the prize or reward",
            max_length=200,
            required=True,
        )
        self.duration_input = discord.ui.TextInput(
            label="Duration in hours (default 24)",
            default=str(_DEFAULT_GIVEAWAY_DURATION_HOURS),
            max_length=3,
            required=True,
        )
        self.add_item(self.reward_input)
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or interaction.guild.id != self.guild_id or interaction.user.id != self.user_id:
            return await interaction.response.send_message("🔒 This giveaway form belongs to a different user or server.", ephemeral=True)
        reward = str(self.reward_input.value).strip()
        try:
            duration = int(str(self.duration_input.value).strip())
            if not 1 <= duration <= MAX_GIVEAWAY_DURATION_HOURS:
                raise ValueError(f"Duration must be between 1 and {MAX_GIVEAWAY_DURATION_HOURS} hours.")
        except ValueError as error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        if not reward:
            return await interaction.response.send_message("❌ Reward type cannot be empty.", ephemeral=True)
        if self.ping_role and self.ping_role.guild.id != self.guild_id:
            return await interaction.response.send_message("❌ The selected ping role belongs to another server.", ephemeral=True)
        if interaction.guild.me:
            permissions = self.channel.permissions_for(interaction.guild.me)
            if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
                return await interaction.response.send_message("⚠️ The bot needs View Channel, Send Messages, and Embed Links in the selected channel.", ephemeral=True)
            if self.ping_role and not self.ping_role.mentionable and not permissions.mention_everyone:
                return await interaction.response.send_message("⚠️ The selected role is not mentionable, and the bot lacks Mention Everyone. Choose a mentionable role or skip the ping.", ephemeral=True)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Step 5: Number of Winners",
                description=f"Reward: **{discord.utils.escape_markdown(reward)}**\nDuration: **{duration} hours**\nChannel: {self.channel.mention}\nPing role: {self.ping_role.mention if self.ping_role else 'No ping'}",
                colour=discord.Colour.blurple(),
            ),
            view=GiveawayWinnerCountStep(
                self.service,
                self.guild_id,
                self.user_id,
                self.channel,
                self.ping_role,
                reward,
                duration,
            ),
            ephemeral=True,
        )


class GiveawayWinnerCountStep(discord.ui.View):
    def __init__(self, service: GiveawayService, guild_id: int, user_id: int, channel: discord.TextChannel, ping_role: discord.Role | None, reward: str, duration_hours: int):
        super().__init__(timeout=600)
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.channel = channel
        self.ping_role = ping_role
        self.reward = reward
        self.duration_hours = duration_hours

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("🔒 This giveaway setup belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Choose Number of Winners", style=discord.ButtonStyle.success, row=0)
    async def choose_winners(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            GiveawayWinnerCountModal(
                self.service,
                self.guild_id,
                self.user_id,
                self.channel,
                self.ping_role,
                self.reward,
                self.duration_hours,
            )
        )


class GiveawayWinnerCountModal(discord.ui.Modal):
    def __init__(self, service: GiveawayService, guild_id: int, user_id: int, channel: discord.TextChannel, ping_role: discord.Role | None, reward: str, duration_hours: int):
        super().__init__(title="Number of Winners")
        self.service = service
        self.guild_id = guild_id
        self.user_id = user_id
        self.channel = channel
        self.ping_role = ping_role
        self.reward = reward
        self.duration_hours = duration_hours
        self.winner_input = discord.ui.TextInput(
            label="How many winners? (1–50)",
            placeholder="1",
            default="1",
            max_length=2,
            required=True,
        )
        self.add_item(self.winner_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id or interaction.user.id != self.user_id:
            return await interaction.response.send_message("🔒 This giveaway form belongs to a different user or server.", ephemeral=True)
        try:
            winner_count = int(str(self.winner_input.value).strip())
            if not 1 <= winner_count <= MAX_GIVEAWAY_WINNERS:
                raise ValueError(f"Choose between 1 and {MAX_GIVEAWAY_WINNERS} winners.")
        except ValueError as error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            giveaway_id, message, ends_at = await self.service.create(
                guild,
                self.channel,
                interaction.user.id,
                self.reward,
                self.ping_role,
                winner_count,
                self.duration_hours,
            )
        except discord.Forbidden:
            log.exception("Giveaway publish was denied in channel %s", self.channel.id)
            return await interaction.followup.send("❌ I could not post the giveaway. Check View Channel, Send Messages, Embed Links, and role-mention permissions.", ephemeral=True)
        except Exception as error:
            log.exception("Giveaway creation failed in guild %s: %s", guild.id, error)
            return await interaction.followup.send(f"❌ I couldn't create the giveaway: {str(error)[:500]}", ephemeral=True)
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Giveaway started",
                description=f"🎁 **Reward:** {discord.utils.escape_markdown(self.reward)}\n🏆 **Winners:** {winner_count}\n📣 **Channel:** {self.channel.mention}\n⏰ **Ends:** <t:{int(ends_at.timestamp())}:R>\n[Jump to giveaway]({message.jump_url})\nID: `{giveaway_id}`",
                colour=discord.Colour.green(),
            ),
            ephemeral=True,
        )
