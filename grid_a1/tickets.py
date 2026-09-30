from __future__ import annotations

import io
import logging
import uuid
from types import SimpleNamespace

import discord

from .database import Database
from .embeds import embed, inactivity_indicator, ticket_archive_embed, ticket_embed
from .utils import (
    is_ticket,
    parse_ticket_topic,
    sanitize_channel_name,
    staff_member,
    ticket_status_title,
    ticket_topic,
    utcnow,
)

log = logging.getLogger(__name__)


async def _respond(interaction, *args, **kwargs):
    """Answer once, using a follow-up when a handler already deferred."""
    if interaction.response.is_done():
        followup = getattr(interaction, "followup", None)
        if followup is None:
            return None
        return await followup.send(*args, **kwargs)

    return await interaction.response.send_message(*args, **kwargs)


async def _followup(interaction, *args, **kwargs):
    """Send a follow-up only when the caller is a user-facing interaction."""
    followup = getattr(interaction, "followup", None)
    if followup is None:
        return None
    return await followup.send(*args, **kwargs)


class TicketService:
    """Own ticket creation, staff notification, transcript, and closure flows."""

    def __init__(self, database: Database) -> None:
        self.db = database

    async def create(
        self,
        interaction: discord.Interaction,
        issue: str,
        label: str,
        region: str,
        details: str,
    ) -> None:
        """Create one private ticket, then save and announce it safely."""
        guild = interaction.guild
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if not guild or region != "EU":
            await interaction.followup.send(
                "❌ Only EU support is currently available. Choose EU and try again.",
                ephemeral=True,
            )
            return

        config = self.db.config(guild.id)
        existing = self.db.open_ticket_for_owner(guild.id, interaction.user.id)
        if existing:
            existing_channel = guild.get_channel(existing["channel_id"])
            if existing_channel:
                await interaction.followup.send(
                    f"📌 You already have an open ticket: {existing_channel.mention}",
                    ephemeral=True,
                )
                return

            try:
                existing_channel = await guild.fetch_channel(existing["channel_id"])
            except discord.NotFound:
                self.db.close_orphaned_ticket(existing["ticket_id"])
                log.info(
                    "Closed orphaned ticket %s after Discord confirmed its channel is missing",
                    existing["ticket_id"],
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "⚠️ I cannot verify your existing ticket channel. "
                    "Please contact staff before opening another ticket.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as error:
                log.warning(
                    "Could not verify existing ticket channel %s: %s",
                    existing["channel_id"],
                    error,
                )
                await interaction.followup.send(
                    "⏳ I could not verify your existing ticket right now. "
                    "Please try again shortly.",
                    ephemeral=True,
                )
                return
            else:
                await interaction.followup.send(
                    f"📌 You already have an open ticket: {existing_channel.mention}",
                    ephemeral=True,
                )
                return

        if not config or not config["ticket_category"] or not config["logs_channel"]:
            await interaction.followup.send(
                "⚠️ Tickets are not set up yet. Ask an admin to configure the panel, logs, "
                "and category in `/dashboard`.",
                ephemeral=True,
            )
            return

        logs_channel = guild.get_channel(config["logs_channel"])
        if not isinstance(logs_channel, discord.TextChannel):
            await interaction.followup.send(
                "❌ The configured ticket transcript channel is missing. "
                "Ask an admin to update `/dashboard` before opening a ticket.",
                ephemeral=True,
            )
            return

        if guild.me:
            archive_permissions = logs_channel.permissions_for(guild.me)
            required_permissions = (
                ("view_channel", "View Channel"),
                ("send_messages", "Send Messages"),
                ("embed_links", "Embed Links"),
                ("attach_files", "Attach Files"),
            )
            missing_permissions = [
                label
                for name, label in required_permissions
                if not getattr(archive_permissions, name)
            ]
            if missing_permissions:
                missing = ", ".join(missing_permissions)
                await interaction.followup.send(
                    f"⚠️ The ticket transcript channel is missing bot permissions: {missing}. "
                    "Ask an admin to update `/dashboard` or the channel permissions.",
                    ephemeral=True,
                )
                return

        category = guild.get_channel(config["ticket_category"])
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                "❌ The configured ticket category is missing. "
                "Ask an admin to update `/dashboard`.",
                ephemeral=True,
            )
            return

        ticket_id = uuid.uuid4().hex[:8].upper()
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                attach_files=True,
            )

        role_ids = set(self.db.ticket_access_role_ids(guild.id))
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )

        channel_name = sanitize_channel_name(
            region,
            issue,
            interaction.user.name,
            ticket_id,
        )
        try:
            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                overwrites=overwrites,
                topic=ticket_topic(ticket_id, interaction.user.id, issue, region),
            )
        except discord.Forbidden:
            log.exception("Ticket channel creation was denied for guild %s", guild.id)
            await interaction.followup.send(
                "❌ I could not create your private ticket channel. Ask staff to check the bot’s "
                "Manage Channels permission and category access.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            log.exception(
                "Ticket channel creation failed for guild %s: %s",
                guild.id,
                error,
            )
            await interaction.followup.send(
                "⚠️ Discord could not create your ticket right now. "
                "Please try again shortly or contact staff.",
                ephemeral=True,
            )
            return

        try:
            self.db.create_ticket(
                ticket_id=ticket_id,
                guild_id=guild.id,
                channel_id=channel.id,
                owner_id=interaction.user.id,
                issue=issue,
                region=region,
                opened_at=utcnow().isoformat(),
                last_activity_at=utcnow().isoformat(),
            )
        except Exception as error:
            log.exception(
                "Could not save ticket %s after creating channel %s: %s",
                ticket_id,
                channel.id,
                error,
            )
            try:
                await channel.delete(reason="Ticket record could not be saved")
            except discord.DiscordException:
                log.exception(
                    "Could not remove orphaned channel %s after ticket database failure",
                    channel.id,
                )
            await interaction.followup.send(
                "❌ I could not save the ticket record, so the request was not opened. "
                "Please try again; if a ticket channel remains, contact staff.",
                ephemeral=True,
            )
            return

        from .views import TicketControls

        welcome = ticket_embed(label, region, details)
        welcome.title = f"🎫 {label.title()} • Private support ticket"
        welcome.description = (
            f"👋 Welcome {interaction.user.mention}! Keep updates here so staff can follow "
            "your request. **Ticket owner:** Urgent help, Check in, Request staff, or "
            "Request closure. **Staff:** Staff claim or Staff close."
        )
        welcome.set_footer(
            text=f"🆔 Ticket {ticket_id} • Keep replies in this private channel"
        )

        welcome_sent = False
        try:
            await channel.send(
                content=interaction.user.mention,
                embed=welcome,
                view=TicketControls(self),
            )
            welcome_sent = True
        except discord.DiscordException:
            log.exception(
                "Ticket %s was created but its welcome message could not be posted",
                ticket_id,
            )

        staff_notified = await self.notify_staff(guild, channel, ticket_id)
        confirmation = f"✅ Ticket **{ticket_id}** created: {channel.mention}"
        if not welcome_sent:
            confirmation += (
                " — staff need to review the channel because I could not post its welcome controls."
            )
        elif not staff_notified:
            confirmation += (
                " — the staff ping could not be delivered; please contact staff directly."
            )

        await interaction.followup.send(confirmation, ephemeral=True)

    async def notify_staff(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket_id: str,
        notice: str = "A support ticket needs attention",
    ) -> bool:
        """Ping configured permission roles without mentioning arbitrary members."""
        roles = [
            guild.get_role(role_id)
            for role_id in self.db.configured_permission_role_ids(guild.id)
        ]
        roles = [role for role in roles if role]
        if not roles:
            return False

        mentions = " ".join(role.mention for role in roles)
        message = f"📣 {mentions} — {notice} (ticket **{ticket_id}**)."
        try:
            await channel.send(
                message,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            return True
        except discord.DiscordException as error:
            log.warning("Staff notification failed for ticket %s: %s", ticket_id, error)
            return False

    async def transcript(self, channel: discord.TextChannel) -> str:
        """Render a complete, oldest-first transcript for an active ticket."""
        from .transcript import render

        messages = []
        async for message in channel.history(limit=None, oldest_first=True):
            messages.append(message)

        data = parse_ticket_topic(channel)
        ticket = self.db.ticket(data.get("id", str(channel.id)))
        return await render(messages, channel, data, ticket)

    async def close_system(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        reason: str,
    ) -> None:
        """Close an inactive ticket from the scheduled system loop."""
        row = self.db.ticket_by_channel(channel.id)
        if not row:
            return

        interaction_proxy = SimpleNamespace(
            channel=channel,
            guild=guild,
            user=guild.me,
            response=SimpleNamespace(is_done=lambda: True),
        )
        await self.close(interaction_proxy, reason, allow_owner=True)

    async def refresh_status(self, channel: discord.TextChannel, row=None) -> None:
        """Refresh the inactivity indicator and controls on one active ticket."""
        row = row or self.db.ticket_by_channel(channel.id)
        if not row:
            return

        config = self.db.config(row["guild_id"])
        threshold = int(config["inactivity_hours"] if config else 24)
        indicator, duration = inactivity_indicator(
            row["last_activity_at"],
            threshold,
            bool(row["owner_left"]),
        )

        try:
            async for message in channel.history(limit=20, oldest_first=True):
                if message.author != channel.guild.me or not message.embeds:
                    continue

                ticket_embed = message.embeds[0].copy()
                ticket_embed.title = ticket_status_title(ticket_embed.title, indicator)
                for index, field in enumerate(ticket_embed.fields):
                    status_fields = {
                        "🟢 Status",
                        "🟡 Status",
                        "🔴 Status",
                        "🟣 Status",
                        "Activity",
                        "🕒 Activity",
                    }
                    if field.name in status_fields:
                        ticket_embed.set_field_at(
                            index,
                            name=field.name,
                            value=f"Inactive for **{duration}**",
                            inline=True,
                        )

                from .views import TicketControls

                await message.edit(embed=ticket_embed, view=TicketControls(self))
                return
        except discord.DiscordException:
            log.info("Could not refresh status for %s", row["ticket_id"], exc_info=True)

    async def resolve_ticket_channel(
        self,
        guild: discord.Guild,
        channel_id: int,
    ) -> discord.abc.GuildChannel | None:
        """Resolve a cached or uncached channel without deleting on API uncertainty."""
        channel = guild.get_channel(channel_id)
        if channel is not None:
            return channel

        try:
            return await guild.fetch_channel(channel_id)
        except discord.NotFound:
            self.db.mark_channel_missing(channel_id)
            return None
        except discord.HTTPException as error:
            log.warning(
                "Could not resolve ticket channel %s: %s",
                channel_id,
                error,
            )
            return None

    async def close_owner_from_dm(
        self,
        interaction: discord.Interaction,
        ticket_id: str,
        reason: str,
    ) -> None:
        """Route an owner's private inactivity-close action through normal archiving."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        row = self.db.ticket(ticket_id)
        guild = interaction.client.get_guild(row["guild_id"]) if row else None
        channel = (
            await self.resolve_ticket_channel(guild, row["channel_id"])
            if guild and row
            else None
        )
        if not row or not guild or not isinstance(channel, discord.TextChannel):
            await _respond(
                interaction,
                "⚠️ This ticket is closed or its channel is currently unavailable.",
                ephemeral=True,
            )
            return

        interaction_proxy = SimpleNamespace(
            channel=channel,
            guild=guild,
            user=interaction.user,
            response=interaction.response,
            followup=interaction.followup,
        )
        await self.close(interaction_proxy, reason, allow_owner=True)

    async def close(
        self,
        interaction,
        reason: str,
        allow_owner: bool = False,
    ) -> None:
        """Archive before marking closed or deleting the channel."""
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket(channel):
            await _respond(
                interaction,
                "❌ This command only works inside an active ticket.",
                ephemeral=True,
            )
            return

        row = self.db.ticket_by_channel(channel.id)
        if not row:
            await _respond(
                interaction,
                "⚠️ This ticket is already closed or unavailable.",
                ephemeral=True,
            )
            return

        if not staff_member(interaction.user, self.db) and not allow_owner:
            await _respond(
                interaction,
                "🔒 Only ticket staff can close tickets.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if not guild:
            await _respond(
                interaction,
                "❌ This ticket cannot be archived outside its server.",
                ephemeral=True,
            )
            return

        data = parse_ticket_topic(channel)
        config = self.db.config(guild.id)
        archive = guild.get_channel(config["logs_channel"]) if config else None
        if not isinstance(archive, discord.TextChannel):
            await _respond(
                interaction,
                "❌ The ticket logs channel is missing. Ask an admin to update `/dashboard`.",
                ephemeral=True,
            )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        try:
            transcript_html = await self.transcript(channel)
        except discord.DiscordException:
            log.exception("Could not read transcript for ticket %s", row["ticket_id"])
            await _followup(
                interaction,
                "❌ I could not read the ticket transcript, so the ticket remains open. "
                "Please retry or contact an administrator.",
                ephemeral=True,
            )
            return

        ticket_id = row["ticket_id"]
        closed_at = utcnow().isoformat()
        filename = f"{channel.name}-transcript.html"
        archive_embed = ticket_archive_embed(
            ticket_id,
            data.get("region", "EU"),
            data.get("issue", "unknown"),
            interaction.user,
            reason,
        )

        try:
            await archive.send(
                embed=archive_embed,
                file=discord.File(
                    io.BytesIO(transcript_html.encode()),
                    filename=filename,
                ),
            )
        except discord.DiscordException as error:
            log.exception("Could not archive ticket %s; leaving it open", ticket_id)
            await _followup(
                interaction,
                "❌ I could not archive the transcript, so the ticket remains open. "
                "Please retry or contact an administrator.",
                ephemeral=True,
            )
            return

        try:
            recorded = self.db.finalize_ticket_close(
                ticket_id,
                closed_by=interaction.user.id,
                reason=reason,
                closed_at=closed_at,
                region=data.get("region", "EU"),
                issue=data.get("issue", "unknown"),
                transcript_filename=filename,
            )
        except Exception:
            log.exception(
                "Transcript for ticket %s was archived, but its closure could not be recorded",
                ticket_id,
            )
            await _followup(
                interaction,
                "⚠️ The transcript was uploaded, but I could not record the closure. "
                "The channel was left in place for staff to review.",
                ephemeral=True,
            )
            return

        if not recorded:
            await _followup(
                interaction,
                "⚠️ The transcript was archived, but this ticket is no longer active; "
                "the channel was not deleted.",
                ephemeral=True,
            )
            return

        try:
            await channel.delete(reason=f"Grid A1 ticket {ticket_id} closed")
        except discord.DiscordException as error:
            log.warning(
                "Ticket %s is closed and archived, but channel deletion failed: %s",
                ticket_id,
                error,
            )
            await _followup(
                interaction,
                "✅ Transcript archived and closure recorded, but I could not delete "
                "the channel. Staff can remove it manually.",
                ephemeral=True,
            )
            return

        await _followup(
            interaction,
            "✅ Transcript archived and ticket channel deleted.",
            ephemeral=True,
        )


async def claim(
    interaction: discord.Interaction,
    service: TicketService,
    member: discord.Member | None = None,
) -> None:
    """Assign an active ticket to the caller or another configured staff member."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    if not staff_member(interaction.user, service.db):
        await interaction.followup.send(
            "🔒 Only ticket staff can claim tickets.",
            ephemeral=True,
        )
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or not is_ticket(channel):
        await interaction.followup.send(
            "❌ This command only works inside an active ticket.",
            ephemeral=True,
        )
        return

    row = service.db.ticket_by_channel(channel.id)
    if not row:
        await interaction.followup.send(
            "⚠️ This ticket is already closed or unavailable.",
            ephemeral=True,
        )
        return

    if member is not None and not staff_member(member, service.db):
        await interaction.followup.send(
            "🔒 Only configured ticket staff can receive a transfer.",
            ephemeral=True,
        )
        return

    target = member or interaction.user
    service.db.update_ticket(row["ticket_id"], claimed_by=target.id)
    await interaction.followup.send(
        embed=embed("🙋 Ticket claimed", f"Assigned to {target.mention}."),
    )
