from __future__ import annotations

import io
import logging
import uuid

import discord

from .database import Database
from .embeds import embed, ticket_archive_embed, ticket_embed, inactivity_indicator
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
        staff_roles = [guild.get_role(role_id) for role_id in self.db.staff_role_ids(guild.id)]
        for role in staff_roles:
            if role: overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
        channel = await guild.create_text_channel(sanitize_channel_name(region, issue, interaction.user.name, ticket_id), category=category, overwrites=overwrites, topic=ticket_topic(ticket_id, interaction.user.id, issue, region), reason=f"Grid A1 ticket {ticket_id} opened")
        try: self.db.create_ticket(ticket_id=ticket_id, guild_id=guild.id, channel_id=channel.id, owner_id=interaction.user.id, issue=issue, region=region, opened_at=utcnow().isoformat(), last_activity_at=utcnow().isoformat())
        except Exception:
            await channel.delete(reason="Grid A1 ticket record could not be created"); raise
        from .views import TicketControls
        await channel.send(content=interaction.user.mention, embed=ticket_embed(label, region, details), view=TicketControls(self))
        await self.notify_staff(guild, channel, ticket_id, label, region, interaction.user.id)
        await interaction.response.send_message(f"✅ Ticket **{ticket_id}** created: {channel.mention}", ephemeral=True)


    async def notify_staff(self, guild, channel, ticket_id, issue, region, owner_id):
        role_ids = self.db.staff_role_ids(guild.id)
        if not role_ids: return
        roles = [guild.get_role(role_id) for role_id in role_ids]
        roles = [role for role in roles if role]
        if roles:
            mentions = " ".join(role.mention for role in roles)
            try:
                await channel.send(f"📣 {mentions} — a new **{issue.title()} ticket** was created. Please review it.", allowed_mentions=discord.AllowedMentions(roles=True))
            except discord.DiscordException:
                log.exception("Could not post staff ticket notification for %s", ticket_id)
        members = {member.id: member for role in roles for member in role.members}
        for member in members.values():
            if member.id == owner_id or member.bot: continue
            try:
                await member.send(f"📩 New Grid A1 ticket **{ticket_id}** was created in **{guild.name}**.\nIssue: **{issue}** | Region: **{region}**\nPlease review it here: {channel.mention}")
            except discord.DiscordException:
                log.info("Could not DM staff member %s about ticket %s", member.id, ticket_id)

    async def transcript(self, channel: discord.TextChannel) -> str:
        from .transcript import render
        messages = []
        async for message in channel.history(limit=None, oldest_first=True): messages.append(message)
        data = parse_ticket_topic(channel)
        return render(messages, channel, data, self.db.ticket(data.get('id', str(channel.id))))

    async def close_system(self, guild: discord.Guild, channel: discord.TextChannel, reason: str) -> None:
        row = self.db.ticket_by_channel(channel.id)
        if not row: return
        class SystemInteraction:
            pass
        interaction = SystemInteraction(); interaction.channel = channel; interaction.guild = guild; interaction.user = guild.me
        async def noop(*args, **kwargs): pass
        interaction.response = type('Response', (), {'send_message': noop, 'is_done': lambda self: True})()
        await self.close(interaction, reason)

    async def refresh_status(self, channel: discord.TextChannel, row=None) -> None:
        row = row or self.db.ticket_by_channel(channel.id)
        if not row: return
        config = self.db.config(row['guild_id']); threshold = int(config['inactivity_hours'] if config else 24)
        indicator, duration = inactivity_indicator(row['last_activity_at'], threshold, bool(row['owner_left']))
        async for message in channel.history(limit=20, oldest_first=True):
            if message.author == channel.guild.me and message.embeds and message.embeds[0].title and 'Support ticket opened' in message.embeds[0].title:
                embed_obj = message.embeds[0].copy(); embed_obj.title = f"{indicator} 🎫 Grid A1 • Support ticket opened"
                for field in embed_obj.fields:
                    if field.name == 'Activity': embed_obj.set_field_at(embed_obj.fields.index(field), name='Activity', value=f'Inactive for **{duration}**', inline=True)
                await message.edit(embed=embed_obj); break

    async def close_owner_from_dm(self, interaction: discord.Interaction, ticket_id: str, reason: str) -> None:
        row = self.db.ticket(ticket_id)
        guild = interaction.client.get_guild(row["guild_id"]) if row else None
        channel = guild.get_channel(row["channel_id"]) if guild and row else None
        if not row or not guild or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("This ticket is already closed or unavailable.", ephemeral=True)
        class Proxy:
            pass
        proxy = Proxy(); proxy.channel = channel; proxy.guild = guild; proxy.user = interaction.user; proxy.response = interaction.response
        await self.close(proxy, reason, allow_owner=True)

    async def close_as_owner(self, interaction: discord.Interaction, channel: discord.TextChannel, reason: str) -> None:
        data = parse_ticket_topic(channel)
        if str(data.get("owner")) != str(interaction.user.id):
            return await interaction.response.send_message("Only the ticket owner can use this button.", ephemeral=True)
        await self.close(interaction, reason, allow_owner=True)

    async def close(self, interaction: discord.Interaction, reason: str, allow_owner: bool = False) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not is_ticket(channel): return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
        if not staff_member(interaction.user) and not allow_owner: return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
        data = parse_ticket_topic(channel); config = self.db.config(interaction.guild.id) if interaction.guild else None
        archive = interaction.guild.get_channel(config['logs_channel']) if config and config['logs_channel'] and interaction.guild else None
        if not isinstance(archive, discord.TextChannel): return await interaction.response.send_message("The logs channel is missing. Run `/setup tickets` again.", ephemeral=True)
        transcript = await self.transcript(channel); ticket_id = data.get('id', str(channel.id)); closed_at = utcnow().isoformat()
        self.db.update_ticket(ticket_id, status='closed', closed_at=closed_at, closed_by=interaction.user.id, close_reason=reason, transcript_filename=f"{channel.name}-transcript.html")
        with self.db.connect() as connection: connection.execute("INSERT INTO closed_tickets(guild, region, issue, closed_by, reason, closed_at) VALUES(?,?,?,?,?,?)", (interaction.guild.id, data.get('region', 'EU'), data.get('issue', 'unknown'), interaction.user.id, reason, closed_at))
        self.db.audit(interaction.guild.id, ticket_id, interaction.user.id, 'closed')
        await archive.send(embed=ticket_archive_embed(ticket_id, data.get('region', 'EU'), data.get('issue', 'unknown'), interaction.user, reason), file=discord.File(io.BytesIO(transcript.encode()), filename=f"{channel.name}-transcript.html"))
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
