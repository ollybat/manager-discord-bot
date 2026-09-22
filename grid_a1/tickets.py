from __future__ import annotations

import html
import io
import logging
import uuid

import discord
from discord import app_commands

from .database import Database
from .embeds import embed, support_panel, ticket_embed
from .utils import is_ticket, parse_ticket_topic, sanitize_channel_name, staff_member, ticket_topic, utcnow

log = logging.getLogger(__name__)

class TicketService:
    def __init__(self, database: Database): self.db = database

    async def create(self, interaction: discord.Interaction, issue: str, label: str, region: str, details: str) -> None:
        guild = interaction.guild
        if not guild or region != "EU": return await interaction.response.send_message("Only EU support is currently available. NA is Coming Soon.", ephemeral=True)
        config = self.db.config(guild.id)
        if not config or not config['ticket_category'] or not config['logs_channel']:
            return await interaction.response.send_message("Tickets are not configured. Ask an administrator to run `/setup tickets`.", ephemeral=True)
        existing = self.db.open_ticket_for_owner(guild.id, interaction.user.id)
        if existing:
            channel = guild.get_channel(existing['channel_id'])
            if channel: return await interaction.response.send_message(f"You already have an open ticket: {channel.mention}", ephemeral=True)
        category = guild.get_channel(config['ticket_category'])
        if not isinstance(category, discord.CategoryChannel): return await interaction.response.send_message("The configured ticket category is missing.", ephemeral=True)
        ticket_id = uuid.uuid4().hex[:8].upper()
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)}
        if guild.me: overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, attach_files=True)
        channel = await guild.create_text_channel(sanitize_channel_name(region, issue, interaction.user.name, ticket_id), category=category, overwrites=overwrites, topic=ticket_topic(ticket_id, interaction.user.id, issue, region), reason=f"Grid A1 ticket {ticket_id} opened")
        try:
            self.db.create_ticket(ticket_id=ticket_id, guild_id=guild.id, channel_id=channel.id, owner_id=interaction.user.id, issue=issue, region=region, opened_at=utcnow().isoformat(), last_activity_at=utcnow().isoformat())
        except Exception:
            await channel.delete(reason="Grid A1 ticket record could not be created")
            raise
        from .views import TicketControls
        await channel.send(content=interaction.user.mention, embed=ticket_embed(label, region, details), view=TicketControls(self))
        await interaction.response.send_message(f"✅ Ticket **{ticket_id}** created: {channel.mention}", ephemeral=True)

    async def transcript(self, channel: discord.TextChannel) -> str:
        parts = ["<!doctype html><meta charset='utf-8'><title>Grid A1 transcript</title><main><h1>Grid A1 ticket transcript</h1>"]
        async for message in channel.history(limit=None, oldest_first=True):
            content = html.escape(message.content or "(no text)").replace("\n", "<br>")
            attachments = " ".join(f"<a href='{html.escape(a.url, quote=True)}'>{html.escape(a.filename)}</a>" for a in message.attachments)
            parts.append(f"<article><b>{html.escape(str(message.author))}</b> <time>{message.created_at.isoformat()}</time><p>{content}<br>{attachments}</p></article>")
        return "<style>body{font:15px system-ui;max-width:900px;margin:2rem auto}article{padding:1rem;border-bottom:1px solid #ddd}time{color:#777}</style>" + "".join(parts) + "</main>"

    async def close(self, interaction: discord.Interaction, reason: str) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket(channel): return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
        if not staff_member(interaction.user): return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
        data = parse_ticket_topic(channel); config = self.db.config(interaction.guild.id) if interaction.guild else None
        archive = interaction.guild.get_channel(config['logs_channel']) if config and config['logs_channel'] and interaction.guild else None
        if not isinstance(archive, discord.TextChannel): return await interaction.response.send_message("The logs channel is missing. Run `/setup tickets` again.", ephemeral=True)
        transcript = await self.transcript(channel); ticket_id = data.get('id', channel.id.__str__())
        closed_at = utcnow().isoformat()
        self.db.update_ticket(ticket_id, status='closed', closed_at=closed_at, closed_by=interaction.user.id, close_reason=reason, transcript_filename=f"{channel.name}-transcript.html")
        with self.db.connect() as connection:
            connection.execute("INSERT INTO closed_tickets(guild, region, issue, closed_by, reason, closed_at) VALUES(?,?,?,?,?,?)", (interaction.guild.id, data.get('region', 'EU'), data.get('issue', 'unknown'), interaction.user.id, reason, closed_at))
        self.db.audit(interaction.guild.id, ticket_id, interaction.user.id, 'closed')
        await archive.send(embed=embed("📁 Ticket archived", f"**Ticket:** {ticket_id}\n**Closed by:** {interaction.user.mention}\n**Reason:** {reason}\n**Region:** {data.get('region', 'EU')}"), file=discord.File(io.BytesIO(transcript.encode()), filename=f"{channel.name}-transcript.html"))
        await interaction.response.send_message("✅ Transcript archived. This ticket will now be deleted.", ephemeral=True)
        await channel.delete(reason=f"Grid A1 ticket {ticket_id} closed by {interaction.user}")

async def claim(interaction: discord.Interaction, service: TicketService, member: discord.Member | None = None) -> None:
    if not staff_member(interaction.user): return await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
    if not isinstance(interaction.channel, discord.TextChannel) or not is_ticket(interaction.channel): return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
    data = parse_ticket_topic(interaction.channel); ticket_id = data.get('id')
    if ticket_id: service.db.update_ticket(ticket_id, claimed_by=(member or interaction.user).id)
    service.db.audit(interaction.guild.id, ticket_id, interaction.user.id, 'claimed')
    await interaction.channel.set_permissions(interaction.user if member is None else member, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    await interaction.response.send_message(embed=embed("🙋 Ticket claimed", f"Assigned to {(member or interaction.user).mention}."))
