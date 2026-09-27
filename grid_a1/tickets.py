from __future__ import annotations
import io, logging, uuid
import discord
from .database import Database
from .embeds import embed, ticket_archive_embed, ticket_embed, inactivity_indicator
from .utils import is_ticket, parse_ticket_topic, sanitize_channel_name, staff_member, ticket_topic, utcnow
log=logging.getLogger(__name__)
class TicketService:
    def __init__(self,database:Database): self.db=database
    async def create(self,interaction,issue,label,region,details):
        guild=interaction.guild
        if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
        if not guild or region!='EU': return await interaction.followup.send('Only EU support is currently available.',ephemeral=True)
        config=self.db.config(guild.id)
        if not config or not config['ticket_category'] or not config['logs_channel']: return await interaction.followup.send('Tickets are not configured.',ephemeral=True)
        existing=self.db.open_ticket_for_owner(guild.id,interaction.user.id)
        if existing:
            channel=guild.get_channel(existing['channel_id'])
            if channel:return await interaction.followup.send(f'You already have an open ticket: {channel.mention}',ephemeral=True)
        category=guild.get_channel(config['ticket_category'])
        if not isinstance(category,discord.CategoryChannel):return await interaction.followup.send('The configured ticket category is missing.',ephemeral=True)
        ticket_id=uuid.uuid4().hex[:8].upper(); overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)}
        if guild.me:overwrites[guild.me]=discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True,attach_files=True)
        role_ids=set(self.db.staff_role_ids(guild.id))|set(self.db.configured_permission_role_ids(guild.id))
        for rid in role_ids:
            role=guild.get_role(rid)
            if role:overwrites[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)
        channel=await guild.create_text_channel(sanitize_channel_name(region,issue,interaction.user.name,ticket_id),category=category,overwrites=overwrites,topic=ticket_topic(ticket_id,interaction.user.id,issue,region))
        try:self.db.create_ticket(ticket_id=ticket_id,guild_id=guild.id,channel_id=channel.id,owner_id=interaction.user.id,issue=issue,region=region,opened_at=utcnow().isoformat(),last_activity_at=utcnow().isoformat())
        except Exception:await channel.delete();raise
        from .views import TicketControls
        welcome=ticket_embed(label,region,details); welcome.title=f'💜 {label.title()} Support Ticket'; welcome.description=f'Welcome {interaction.user.mention}! Your private support channel is ready.'
        await channel.send(content=interaction.user.mention,embed=welcome,view=TicketControls(self)); await self.notify_staff(guild,channel,ticket_id,label,region,interaction.user.id); await interaction.followup.send(f'✅ Ticket **{ticket_id}** created: {channel.mention}',ephemeral=True)
    async def notify_staff(self,guild,channel,ticket_id,issue,region,owner_id):
        roles=[guild.get_role(r) for r in self.db.staff_role_ids(guild.id)]; roles=[r for r in roles if r]
        if roles: await channel.send('📣 '+' '.join(r.mention for r in roles),allowed_mentions=discord.AllowedMentions(roles=True))
    async def transcript(self,channel):
        from .transcript import render
        messages=[]
        async for message in channel.history(limit=None,oldest_first=True):messages.append(message)
        data=parse_ticket_topic(channel); return render(messages,channel,data,self.db.ticket(data.get('id',str(channel.id))))
    async def close_system(self,guild,channel,reason):
        row=self.db.ticket_by_channel(channel.id)
        if not row:return
        class S:pass
        i=S();i.channel=channel;i.guild=guild;i.user=guild.me;i.response=type('R',(),{'is_done':lambda s:True,'send_message':lambda *a,**k:None,'defer':lambda *a,**k:None,'is_done':lambda s:True})()
        await self.close(i,reason)
    async def refresh_status(self,channel,row=None):
        row=row or self.db.ticket_by_channel(channel.id)
        if not row:return
        config=self.db.config(row['guild_id']); threshold=int(config['inactivity_hours'] if config else 24); indicator,duration=inactivity_indicator(row['last_activity_at'],threshold,bool(row['owner_left']))
        try:
            async for message in channel.history(limit=20,oldest_first=True):
                if message.author==channel.guild.me and message.embeds:
                    e=message.embeds[0].copy(); e.title=f'{indicator} 🎫 {e.title.lstrip("🟢🟡🔴 ")}'
                    for idx,field in enumerate(e.fields):
                        if field.name in ('🟢 Status','🟡 Status','🔴 Status','🟣 Status','Activity'): e.set_field_at(idx,name=field.name,value=f'Inactive for **{duration}**',inline=True)
                    await message.edit(embed=e); return
        except discord.DiscordException: log.info('Could not refresh status for %s',row['ticket_id'])
    async def close_owner_from_dm(self,interaction,ticket_id,reason):
        row=self.db.ticket(ticket_id); guild=interaction.client.get_guild(row['guild_id']) if row else None; channel=guild.get_channel(row['channel_id']) if guild and row else None
        if not row or not isinstance(channel,discord.TextChannel):return await interaction.response.send_message('This ticket is already closed.',ephemeral=True)
        class P:pass
        p=P();p.channel=channel;p.guild=guild;p.user=interaction.user;p.response=interaction.response;await self.close(p,reason,True)
    async def close(self,interaction,reason,allow_owner=False):
        channel=interaction.channel
        if not isinstance(channel,discord.TextChannel) or not is_ticket(channel):return await interaction.response.send_message('This only works inside a ticket.',ephemeral=True)
        if not staff_member(interaction.user,self.db) and not allow_owner:return await interaction.response.send_message('Only staff can close tickets.',ephemeral=True)
        data=parse_ticket_topic(channel); config=self.db.config(interaction.guild.id); archive=interaction.guild.get_channel(config['logs_channel']) if config else None
        if not isinstance(archive,discord.TextChannel):return await interaction.response.send_message('The logs channel is missing.',ephemeral=True)
        if not interaction.response.is_done():await interaction.response.defer(ephemeral=True)
        # Defer before any transcript I/O so the interaction token remains valid.
        transcript=await self.transcript(channel); tid=data.get('id',str(channel.id)); closed=utcnow().isoformat(); self.db.update_ticket(tid,status='closed',closed_at=closed,closed_by=interaction.user.id,close_reason=reason,transcript_filename=f'{channel.name}-transcript.html')
        await archive.send(embed=ticket_archive_embed(tid,data.get('region','EU'),data.get('issue','unknown'),interaction.user,reason),file=discord.File(io.BytesIO(transcript.encode()),filename=f'{channel.name}-transcript.html'))
        if hasattr(interaction,'followup'): await interaction.followup.send('✅ Transcript archived. This ticket will now be deleted.',ephemeral=True)
        await channel.delete(reason=f'Grid A1 ticket {tid} closed')
async def claim(interaction,service,member=None):
    if not staff_member(interaction.user,service.db):return await interaction.response.send_message('Only staff can claim tickets.',ephemeral=True)
    if not isinstance(interaction.channel,discord.TextChannel) or not is_ticket(interaction.channel):return await interaction.response.send_message('This only works inside a ticket.',ephemeral=True)
    if member is not None and not staff_member(member,service.db):return await interaction.response.send_message('Only staff members can receive a ticket transfer.',ephemeral=True)
    data=parse_ticket_topic(interaction.channel); target=member or interaction.user
    if data.get('id'):service.db.update_ticket(data['id'],claimed_by=target.id)
    await interaction.response.send_message(embed=embed('🙋 Ticket claimed',f'Assigned to {target.mention}.'))
