from __future__ import annotations
import io, logging, uuid
import discord
from .database import Database
from .embeds import embed, ticket_archive_embed, ticket_embed, inactivity_indicator
from .utils import is_ticket, parse_ticket_topic, sanitize_channel_name, staff_member, ticket_status_title, ticket_topic, utcnow
log=logging.getLogger(__name__)

async def _respond(interaction, *args, **kwargs):
    """Answer an interaction safely whether or not it has already been deferred."""
    if interaction.response.is_done():
        followup=getattr(interaction,'followup',None)
        return await followup.send(*args,**kwargs) if followup else None
    return await interaction.response.send_message(*args,**kwargs)

class TicketService:
    def __init__(self,database:Database): self.db=database
    async def create(self,interaction,issue,label,region,details):
        guild=interaction.guild
        if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
        if not guild or region!='EU': return await interaction.followup.send('❌ Only EU support is currently available. Choose EU and try again.',ephemeral=True)
        config=self.db.config(guild.id)
        if not config or not config['ticket_category'] or not config['logs_channel']: return await interaction.followup.send('⚠️ Tickets are not set up yet. Ask an admin to configure the panel, logs, and category in `/dashboard`.',ephemeral=True)
        existing=self.db.open_ticket_for_owner(guild.id,interaction.user.id)
        if existing:
            channel=guild.get_channel(existing['channel_id'])
            if channel:return await interaction.followup.send(f'📌 You already have an open ticket: {channel.mention}',ephemeral=True)
            try:
                channel=await guild.fetch_channel(existing['channel_id'])
            except discord.NotFound:
                self.db.close_orphaned_ticket(existing['ticket_id'])
                log.info('Closed orphaned ticket %s after Discord confirmed its channel is missing', existing['ticket_id'])
            except discord.Forbidden:
                return await interaction.followup.send('⚠️ I cannot verify your existing ticket channel. Please contact staff before opening another ticket.',ephemeral=True)
            except discord.HTTPException as error:
                log.warning('Could not verify existing ticket channel %s: %s', existing['channel_id'], error)
                return await interaction.followup.send('⏳ I could not verify your existing ticket channel right now. Please try again shortly.',ephemeral=True)
            else:
                return await interaction.followup.send(f'📌 You already have an open ticket: {channel.mention}',ephemeral=True)
        category=guild.get_channel(config['ticket_category'])
        if not isinstance(category,discord.CategoryChannel):return await interaction.followup.send('❌ The configured ticket category is missing. Ask an admin to update `/dashboard`.',ephemeral=True)
        ticket_id=uuid.uuid4().hex[:8].upper(); overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)}
        if guild.me:overwrites[guild.me]=discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True,attach_files=True)
        role_ids=set(self.db.ticket_access_role_ids(guild.id))
        for rid in role_ids:
            role=guild.get_role(rid)
            if role:overwrites[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)
        channel=await guild.create_text_channel(sanitize_channel_name(region,issue,interaction.user.name,ticket_id),category=category,overwrites=overwrites,topic=ticket_topic(ticket_id,interaction.user.id,issue,region))
        try:self.db.create_ticket(ticket_id=ticket_id,guild_id=guild.id,channel_id=channel.id,owner_id=interaction.user.id,issue=issue,region=region,opened_at=utcnow().isoformat(),last_activity_at=utcnow().isoformat())
        except Exception:await channel.delete();raise
        from .views import TicketControls
        welcome=ticket_embed(label,region,details); welcome.title=f'💜 {label.title()} Support Ticket'; welcome.description=f'Welcome {interaction.user.mention}! Your private support channel is ready.'
        welcome_sent = False
        try:
            await channel.send(content=interaction.user.mention,embed=welcome,view=TicketControls(self))
            welcome_sent = True
        except discord.DiscordException:
            log.exception('Ticket %s was created but its welcome message could not be posted', ticket_id)
        notified = await self.notify_staff(guild,channel,ticket_id,label,region,interaction.user.id)
        message = f'✅ Ticket **{ticket_id}** created: {channel.mention}'
        if not welcome_sent: message += ' — staff need to review the channel because I could not post its welcome controls.'
        elif not notified: message += ' — the staff ping could not be delivered; please contact staff directly.'
        await interaction.followup.send(message,ephemeral=True)
    async def notify_staff(self,guild,channel,ticket_id,issue,region,owner_id,notice='A support ticket needs attention'):
        roles=[guild.get_role(r) for r in self.db.configured_permission_role_ids(guild.id)]; roles=[r for r in roles if r]
        if not roles: return False
        try:
            await channel.send('📣 '+' '.join(r.mention for r in roles)+f' — {notice} (ticket **{ticket_id}**).',allowed_mentions=discord.AllowedMentions(roles=True))
            return True
        except discord.DiscordException as error:
            log.warning('Staff notification failed for ticket %s: %s', ticket_id, error)
            return False
    async def transcript(self,channel):
        from .transcript import render
        messages=[]
        async for message in channel.history(limit=None,oldest_first=True):messages.append(message)
        data=parse_ticket_topic(channel); return await render(messages,channel,data,self.db.ticket(data.get('id',str(channel.id))))
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
                    e=message.embeds[0].copy(); e.title=ticket_status_title(e.title,indicator)
                    for idx,field in enumerate(e.fields):
                        if field.name in ('🟢 Status','🟡 Status','🔴 Status','🟣 Status','Activity'): e.set_field_at(idx,name=field.name,value=f'Inactive for **{duration}**',inline=True)
                    await message.edit(embed=e); return
        except discord.DiscordException: log.info('Could not refresh status for %s',row['ticket_id'])
    async def resolve_ticket_channel(self,guild,channel_id):
        channel=guild.get_channel(channel_id)
        if channel is not None:return channel
        try:return await guild.fetch_channel(channel_id)
        except discord.NotFound:
            self.db.mark_channel_missing(channel_id)
            return None
        except discord.HTTPException as error:
            log.warning('Could not resolve ticket channel %s: %s',channel_id,error)
            return None
    async def close_owner_from_dm(self,interaction,ticket_id,reason):
        if not interaction.response.is_done():await interaction.response.defer(ephemeral=True)
        row=self.db.ticket(ticket_id); guild=interaction.client.get_guild(row['guild_id']) if row else None
        channel=await self.resolve_ticket_channel(guild,row['channel_id']) if guild and row else None
        if not row or not guild or not isinstance(channel,discord.TextChannel):return await _respond(interaction,'⚠️ This ticket is closed or its channel is currently unavailable.',ephemeral=True)
        class P:pass
        p=P();p.channel=channel;p.guild=guild;p.user=interaction.user;p.response=interaction.response;p.followup=interaction.followup;await self.close(p,reason,True)
    async def close(self,interaction,reason,allow_owner=False):
        channel=interaction.channel
        if not isinstance(channel,discord.TextChannel) or not is_ticket(channel):return await _respond(interaction,'❌ This command only works inside an active ticket.',ephemeral=True)
        if not staff_member(interaction.user,self.db) and not allow_owner:return await _respond(interaction,'🔒 Only ticket staff can close tickets.',ephemeral=True)
        data=parse_ticket_topic(channel); config=self.db.config(interaction.guild.id); archive=interaction.guild.get_channel(config['logs_channel']) if config else None
        if not isinstance(archive,discord.TextChannel):return await _respond(interaction,'❌ The ticket logs channel is missing. Ask an admin to update `/dashboard`.',ephemeral=True)
        if not interaction.response.is_done():await interaction.response.defer(ephemeral=True)
        # Defer before transcript I/O, then archive successfully before changing durable ticket state.
        transcript=await self.transcript(channel); tid=data.get('id',str(channel.id)); closed=utcnow().isoformat(); filename=f'{channel.name}-transcript.html'
        try:
            await archive.send(embed=ticket_archive_embed(tid,data.get('region','EU'),data.get('issue','unknown'),interaction.user,reason),file=discord.File(io.BytesIO(transcript.encode()),filename=filename))
        except discord.DiscordException as error:
            log.exception('Could not archive ticket %s; leaving it open', tid)
            if hasattr(interaction,'followup'): await interaction.followup.send('❌ I could not archive the transcript, so the ticket remains open. Please retry or contact an administrator.',ephemeral=True)
            return
        try:
            recorded=self.db.finalize_ticket_close(tid,closed_by=interaction.user.id,reason=reason,closed_at=closed,region=data.get('region','EU'),issue=data.get('issue','unknown'),transcript_filename=filename)
        except Exception:
            log.exception('Transcript for ticket %s was archived, but its closure could not be recorded', tid)
            if hasattr(interaction,'followup'): await interaction.followup.send('⚠️ The transcript was uploaded, but I could not record the closure. The channel was left in place for staff to review.',ephemeral=True)
            return
        if not recorded:
            if hasattr(interaction,'followup'): await interaction.followup.send('⚠️ The transcript was archived, but this ticket is no longer active; the channel was not deleted.',ephemeral=True)
            return
        try:
            await channel.delete(reason=f'Grid A1 ticket {tid} closed')
        except discord.DiscordException as error:
            log.warning('Ticket %s is closed and archived, but channel deletion failed: %s', tid, error)
            if hasattr(interaction,'followup'): await interaction.followup.send('✅ Transcript archived and closure recorded, but I could not delete the channel. Staff can remove it manually.',ephemeral=True)
            return
        if hasattr(interaction,'followup'): await interaction.followup.send('✅ Transcript archived and ticket channel deleted.',ephemeral=True)
async def claim(interaction,service,member=None):
    if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
    if not staff_member(interaction.user,service.db):return await interaction.followup.send('🔒 Only ticket staff can claim tickets.',ephemeral=True)
    if not isinstance(interaction.channel,discord.TextChannel) or not is_ticket(interaction.channel):return await interaction.followup.send('❌ This command only works inside an active ticket.',ephemeral=True)
    if member is not None and not staff_member(member,service.db):return await interaction.followup.send('🔒 Only configured ticket staff can receive a transfer.',ephemeral=True)
    data=parse_ticket_topic(interaction.channel); target=member or interaction.user
    if data.get('id'):service.db.update_ticket(data['id'],claimed_by=target.id)
    await interaction.followup.send(embed=embed('🙋 Ticket claimed',f'Assigned to {target.mention}.'))
