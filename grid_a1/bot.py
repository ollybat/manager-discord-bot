from __future__ import annotations
import logging
import time
import discord
from discord import app_commands
from discord.ext import commands, tasks
from .config import Settings, configure_logging
from .database import Database
from .embeds import embed, support_panel, inactivity_indicator
from .tickets import TicketService, claim
from .utils import is_ticket, parse_ticket_topic, staff_member
from .views import OwnerInactivityView, TicketControls, TicketPanel, VerifyPanel
from .welcomer import missing, send_welcome, welcome_embed
from .commands import OwnerConfigurationError, OwnerOnlyError, register_commands
settings = Settings.from_env(); configure_logging(settings.log_level)
log = logging.getLogger("grid-a1-manager")
intents = discord.Intents.default(); intents.members = True; intents.message_content = True
class GridA1Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=settings.prefix, intents=intents, help_command=None)
        self.database = Database(settings.database_path); self.tickets = TicketService(self.database)
        self._global_sync_last_at = 0.0
        self._global_sync_in_progress = False
        register_commands(self)
    async def setup_hook(self):
        self.database.migrate(); self.add_view(TicketPanel(self.tickets)); self.add_view(TicketControls(self.tickets)); self.add_view(VerifyPanel(self.database)); self.refresh_panels.start(); self.inactivity_loop.start()
        # Never PUT global commands during startup; global sync is owner-only via /sync.
        if settings.test_guild_id:
            guild = discord.Object(id=settings.test_guild_id); self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %d application commands to test guild %s on startup", len(synced), settings.test_guild_id)
            except discord.HTTPException as error:
                if error.status == 429: log.warning("Test-guild sync was rate limited on startup; no global sync was attempted")
                else: log.exception("Test-guild command sync failed on startup")
    async def sync_commands_on_request(self):
        """Run an explicit owner-requested sync with an in-memory cooldown/guard."""
        now = time.monotonic(); cooldown = 60.0
        if self._global_sync_in_progress: raise RuntimeError("A command sync is already in progress; please wait for its response.")
        remaining = cooldown - (now - self._global_sync_last_at)
        if remaining > 0: raise RuntimeError(f"Global command sync is on cooldown; try again in {remaining:.0f}s.")
        self._global_sync_in_progress = True; self._global_sync_last_at = now
        try:
            out=[]
            if settings.test_guild_id:
                guild=discord.Object(id=settings.test_guild_id); self.tree.copy_global_to(guild=guild); synced_guild=await self.tree.sync(guild=guild); out.append(f"test guild `{settings.test_guild_id}`: {len(synced_guild)}")
            synced_global=await self.tree.sync(); out.append(f"global: {len(synced_global)}"); return out
        finally: self._global_sync_in_progress = False
    @tasks.loop(seconds=60)
    async def refresh_panels(self):
        for guild in self.guilds:
            config = self.database.config(guild.id)
            if not config or not config['panel_channel'] or not config['panel_message']: continue
            channel = guild.get_channel(config['panel_channel'])
            if not isinstance(channel, discord.TextChannel): continue
            try:
                message = await channel.fetch_message(config['panel_message']); await message.edit(embed=support_panel(guild, self.database), view=TicketPanel(self.tickets))
            except discord.NotFound:
                try:
                    message = await channel.send(embed=support_panel(guild, self.database), view=TicketPanel(self.tickets)); self.database.upsert_config(guild.id, panel_message=message.id)
                except discord.DiscordException: log.exception("Panel recovery failed")
            except discord.DiscordException: log.exception("Panel refresh failed")
    @tasks.loop(minutes=5)
    async def inactivity_loop(self):
        from datetime import datetime, timezone, timedelta
        for guild in self.guilds:
            config = self.database.config(guild.id)
            if not config: continue
            threshold = int(config['inactivity_hours'])
            for row in self.database.open_tickets(guild.id):
                channel = guild.get_channel(row['channel_id'])
                if not isinstance(channel, discord.TextChannel): continue
                indicator, duration = inactivity_indicator(row['last_activity_at'], threshold, bool(row['owner_left']))
                red = indicator == '🔴'
                if red and not row['inactivity_notice_at']:
                    owner = guild.get_member(row['owner_id'])
                    now = datetime.now(timezone.utc); notice_at = now.isoformat()
                    if owner:
                        try:
                            await owner.send(f'🔴 Your Grid A1 ticket **{row["ticket_id"]}** has been inactive for **{duration}**. Please choose an option below within 24 hours.', view=OwnerInactivityView(self.tickets, row["ticket_id"]))
                        except discord.DiscordException: log.info('Could not DM inactive ticket owner %s', row['owner_id'])
                    self.database.update_ticket(row['ticket_id'], inactivity_notice_at=notice_at, auto_close_at=(now + timedelta(hours=24)).isoformat())
                    self.database.audit(guild.id,row['ticket_id'],0,'inactivity_notice', '{"indicator":"red"}')
                elif red and row['auto_close_at'] and datetime.fromisoformat(row['auto_close_at']) <= datetime.now(timezone.utc):
                    await self.tickets.close_system(guild, channel, 'Auto-closed after inactivity grace period')
                await self.tickets.refresh_status(channel, row)

    @inactivity_loop.before_loop
    async def before_inactivity_loop(self): await self.wait_until_ready()
    @refresh_panels.before_loop
    async def before_refresh_panels(self): await self.wait_until_ready()
bot = GridA1Bot()
setup_group = app_commands.Group(name="setup", description="Configure Grid A1 bot")
welcomer_group = app_commands.Group(name="welcomer", description="Preview and test welcome messages")
ticket_group = app_commands.Group(name="ticket", description="Manage support tickets")
bot.tree.add_command(setup_group); bot.tree.add_command(welcomer_group); bot.tree.add_command(ticket_group)
def guild(i): return i.guild
def admin(): return app_commands.checks.has_permissions(manage_guild=True)
def staff(): return app_commands.checks.has_permissions(manage_channels=True)

@bot.tree.command(name="verifypanel", description="Create a verification panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def verifypanel(i: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if role.is_default(): return await i.response.send_message("❌ You cannot use @everyone as the verification role.", ephemeral=True)
    if not i.guild.me or role >= i.guild.me.top_role: return await i.response.send_message("❌ Move Grid A1's bot role above the verification role first.", ephemeral=True)
    panel = embed("Grid A1 verification", "Click the button below to receive the server verification role. If you already have it, nothing will change.")
    panel.set_footer(text="Grid A1 • Manager")
    message = await channel.send(embed=panel, view=VerifyPanel(bot.database))
    bot.database.upsert_config(i.guild.id, verify_panel_channel=channel.id, verify_panel_message=message.id, verify_role=role.id)
    await i.response.send_message(f"✅ Verification panel created in {channel.mention} for {role.mention}.", ephemeral=True)


@setup_group.command(name="staff", description="Manage optional ticket staff notification roles")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(action="Type add or remove", role="Staff role to notify")
async def setup_staff(i: discord.Interaction, action: str, role: discord.Role):
    action = action.lower()
    if action not in ("add", "remove"):
        return await i.response.send_message("Use action add or remove.", ephemeral=True)
    try:
        changed = bot.database.add_staff_role(i.guild.id, role.id) if action == "add" else bot.database.remove_staff_role(i.guild.id, role.id)
    except ValueError as error:
        return await i.response.send_message(f"❌ {error}", ephemeral=True)
    if action == "add":
        message = f"✅ {role.mention} will be notified when a new ticket is created." if changed else f"{role.mention} is already configured."
    else:
        message = f"✅ Removed {role.mention} from ticket notifications." if changed else f"{role.mention} was not configured."
    await i.response.send_message(message, ephemeral=True)

@setup_group.command(name="tickets", description="Configure ticket channels and deploy the support panel")
@admin()
async def setup_tickets(i, panel_channel: discord.TextChannel, logs_channel: discord.TextChannel, category: discord.CategoryChannel, inactivity_hours: app_commands.Range[int,1,720]):
    g=guild(i); bot.database.upsert_config(g.id,panel_channel=panel_channel.id,logs_channel=logs_channel.id,ticket_category=category.id,inactivity_hours=inactivity_hours); m=await panel_channel.send(embed=support_panel(g,bot.database),view=TicketPanel(bot.tickets)); bot.database.upsert_config(g.id,panel_message=m.id); await i.response.send_message(f"✅ Grid A1 support panel deployed in {panel_channel.mention}; logs go to {logs_channel.mention}.",ephemeral=True)
@setup_group.command(name="welcomer", description="Configure welcome and community channels")
@admin()
async def setup_welcomer(i,welcome_channel:discord.TextChannel,link_channel:discord.TextChannel,bot_commands_channel:discord.TextChannel,shop_channel:discord.TextChannel,verify_channel:discord.TextChannel): bot.database.upsert_config(guild(i).id,welcome_channel=welcome_channel.id,link_channel=link_channel.id,bot_commands_channel=bot_commands_channel.id,shop_channel=shop_channel.id,verify_channel=verify_channel.id); await i.response.send_message(f"✅ Welcomer configured for {welcome_channel.mention}.",ephemeral=True)
@welcomer_group.command(name="preview", description="Preview the configured welcome message")
@admin()
async def welcomer_preview(i):
    config=bot.database.config(guild(i).id); problems=missing(guild(i),config)
    if problems: return await i.response.send_message(embed=embed("⚠️ Welcomer is not set up","\n".join(f"• {p}" for p in problems),discord.Colour.red()),ephemeral=True)
    await i.response.send_message(embed=welcome_embed(bot,guild(i),i.user,config),ephemeral=True)
@welcomer_group.command(name="test", description="Send a welcome test")
@admin()
async def welcomer_test(i):
    config=bot.database.config(guild(i).id); problems=missing(guild(i),config)
    if problems: return await i.response.send_message("Welcomer is not configured correctly.",ephemeral=True)
    channel=guild(i).get_channel(config['welcome_channel'])
    if not isinstance(channel,discord.TextChannel): return await i.response.send_message("Welcome channel is missing.",ephemeral=True)
    await channel.send(embed=welcome_embed(bot,guild(i),i.user,config)); await i.response.send_message("✅ Welcome test sent.",ephemeral=True)
@ticket_group.command(name="claim", description="Claim the current ticket")
@staff()
async def ticket_claim(i): await claim(i,bot.tickets)
@ticket_group.command(name="transfer", description="Transfer the current ticket")
@staff()
async def ticket_transfer(i,staff_member:discord.Member): await claim(i,bot.tickets,staff_member)
@ticket_group.command(name="requestclose", description="Request closure of the current ticket")
async def ticket_requestclose(i,reason:str):
    if not is_ticket(i.channel): return await i.response.send_message("This only works inside a ticket.",ephemeral=True)
    if not staff_member(i.user): return await i.response.send_message("Only staff can request closure.",ephemeral=True)
    data=parse_ticket_topic(i.channel); bot.database.update_ticket(data.get('id',''),status='close_requested',close_requested_by=i.user.id); bot.database.audit(guild(i).id,data.get('id',''),i.user.id,'close_requested'); await i.response.send_message(f"🔒 Closure requested: {reason}")
@ticket_group.command(name="close", description="Archive transcript and delete current ticket")
@staff()
async def ticket_close(i,reason:str="No reason provided"): await bot.tickets.close(i,reason)
@bot.event
async def on_message(message: discord.Message):
    if not message.author.bot and isinstance(message.channel, discord.TextChannel):
        row = bot.database.ticket_by_channel(message.channel.id)
        if row:
            bot.database.mark_activity(row["ticket_id"])
    await bot.process_commands(message)

@bot.event
async def on_member_join(member): await send_welcome(bot,bot.database,member)
@bot.event
async def on_member_remove(member):
    ticket_ids = bot.database.mark_owner_left(member.guild.id, member.id)
    for ticket_id in ticket_ids: bot.database.audit(member.guild.id, ticket_id, 0, 'owner_left')
@bot.event
async def on_ready(): log.info("Grid A1 bot logged in as %s",bot.user)
@bot.tree.error
async def on_app_command_error(i,error):
    log.exception("Application command failed",exc_info=error)
    original=getattr(error,"original",error)
    if isinstance(original,discord.HTTPException) and original.status==429: msg="Discord rate-limited this sync. Please wait before trying /sync again; startup does not perform a global sync."
    elif isinstance(error,app_commands.MissingPermissions): msg="You do not have permission to use that command."
    elif isinstance(error,(OwnerConfigurationError,OwnerOnlyError)): msg=str(error)
    elif isinstance(error,RuntimeError): msg=str(error)
    else: msg="That command could not be completed. Check setup and bot permissions."
    if i.response.is_done(): await i.followup.send(msg,ephemeral=True)
    else: await i.response.send_message(msg,ephemeral=True)
def run():
    if not settings.token: raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it outside Discord.")
    bot.run(settings.token)
if __name__ == "__main__": run()
