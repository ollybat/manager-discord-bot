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
        self.settings_owner_id = settings.owner_id
        self.started_at = time.time()
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
            except discord.DiscordServerError as error:
                log.warning("Panel refresh temporarily unavailable (HTTP %s); will retry next cycle", getattr(error, "status", "unknown"))
            except discord.HTTPException as error:
                log.warning("Panel refresh request failed (HTTP %s); will retry next cycle", getattr(error, "status", "unknown"))
            except discord.DiscordException:
                log.warning("Panel refresh encountered a Discord error; will retry next cycle")
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
def _privileged(interaction: discord.Interaction, require_staff: bool = False) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member): return False
    if interaction.user.id == interaction.guild.owner_id or interaction.user.id == settings.owner_id: return True
    permissions = interaction.user.guild_permissions
    if permissions.administrator or permissions.manage_guild: return True
    return require_staff and (permissions.manage_channels or bool(set(role.id for role in interaction.user.roles) & set(bot.database.staff_role_ids(interaction.guild.id))))

def _prefix_privileged(ctx, require_staff: bool = False) -> bool:
    if not ctx.guild or not isinstance(ctx.author, discord.Member): return False
    if ctx.author.id == ctx.guild.owner_id or ctx.author.id == settings.owner_id: return True
    permissions = ctx.author.guild_permissions
    if permissions.administrator or permissions.manage_guild: return True
    return require_staff and (permissions.manage_channels or bool(set(role.id for role in ctx.author.roles) & set(bot.database.staff_role_ids(ctx.guild.id))))

def _permission_check(require_staff: bool = False):
    async def predicate(interaction: discord.Interaction) -> bool:
        return _privileged(interaction, require_staff)
    return app_commands.check(predicate)

def admin(): return _permission_check(False)
def staff(): return _permission_check(True)

@bot.tree.command(name="kick", description="Kick a member from this Discord server")
@staff()
async def moderation_kick(i: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot kick yourself or the server owner.", ephemeral=True)
    if member.top_role >= i.user.top_role and i.user.id != i.guild.owner_id: return await i.response.send_message("❌ That member has an equal or higher role than you.", ephemeral=True)
    if not i.guild.me or member.top_role >= i.guild.me.top_role: return await i.response.send_message("❌ My bot role must be above that member.", ephemeral=True)
    try: await member.kick(reason=f"{reason} • Moderator: {i.user}")
    except discord.Forbidden: return await i.response.send_message("❌ Discord denied the kick. Check Kick Members permission and role hierarchy.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "kick", discord.utils.escape_markdown(reason)[:500])
    await i.response.send_message(embed=embed("💜 Member kicked", f"✅ {member.mention} was removed from the server.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.tree.command(name="ban", description="Ban a member from this Discord server")
@staff()
async def moderation_ban(i: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot ban yourself or the server owner.", ephemeral=True)
    if member.top_role >= i.user.top_role and i.user.id != i.guild.owner_id: return await i.response.send_message("❌ That member has an equal or higher role than you.", ephemeral=True)
    if not i.guild.me or member.top_role >= i.guild.me.top_role: return await i.response.send_message("❌ My bot role must be above that member.", ephemeral=True)
    try: await member.ban(reason=f"{reason} • Moderator: {i.user}", delete_message_days=0)
    except discord.Forbidden: return await i.response.send_message("❌ Discord denied the ban. Check Ban Members permission and role hierarchy.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "ban", discord.utils.escape_markdown(reason)[:500])
    await i.response.send_message(embed=embed("💜 Member banned", f"🔨 {member.mention} was banned from the server.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.tree.command(name="warn", description="Record a warning for a member")
@staff()
async def moderation_warn(i: discord.Interaction, member: discord.Member, reason: str):
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot warn yourself or the server owner.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "warn", f"member={member.id}; {discord.utils.escape_markdown(reason)[:450]}")
    await i.response.send_message(embed=embed("💜 Warning recorded", f"⚠️ A warning was recorded for {member.mention}.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.tree.command(name="timeout", description="Timeout a member")
@staff()
async def moderation_timeout(i: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason provided"):
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot timeout yourself or the server owner.", ephemeral=True)
    if member.top_role >= i.user.top_role and i.user.id != i.guild.owner_id: return await i.response.send_message("❌ That member has an equal or higher role than you.", ephemeral=True)
    if not i.guild.me or member.top_role >= i.guild.me.top_role: return await i.response.send_message("❌ My bot role must be above that member.", ephemeral=True)
    try: await member.timeout(discord.utils.utcnow() + __import__("datetime").timedelta(minutes=minutes), reason=f"{reason} • Moderator: {i.user}")
    except discord.Forbidden: return await i.response.send_message("❌ Discord denied the timeout. Check Moderate Members permission and role hierarchy.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "timeout", f"member={member.id}; minutes={minutes}; {discord.utils.escape_markdown(reason)[:400]}")
    await i.response.send_message(embed=embed("💜 Member timed out", f"⏳ {member.mention} was timed out for **{minutes} minute(s)**.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.command(name="lock")
async def prefix_lock(ctx):
    if not ctx.guild or not _prefix_privileged(ctx, True): return await ctx.send("❌ Only staff, the server owner, or the bot owner can lock channels.")
    try: await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Channel locked by {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ I cannot lock this channel. Check Manage Channels and Manage Permissions.")
    await ctx.send("🔒 This channel is now locked for members.")

@bot.command(name="unlock")
async def prefix_unlock(ctx):
    if not ctx.guild or not _prefix_privileged(ctx, True): return await ctx.send("❌ Only staff, the server owner, or the bot owner can unlock channels.")
    try: await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Channel unlocked by {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ I cannot unlock this channel. Check Manage Channels and Manage Permissions.")
    await ctx.send("🔓 This channel is now unlocked for members.")

@bot.tree.command(name="verifypanel", description="Create a verification panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def verifypanel(i: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if role.is_default(): return await i.response.send_message("❌ You cannot use @everyone as the verification role.", ephemeral=True)
    if not i.guild.me or role >= i.guild.me.top_role: return await i.response.send_message("❌ Move Grid A1's bot role above the verification role first.", ephemeral=True)
    panel = embed("💜 Grid A1 • Secure Verification", "✨ Complete the short verification check to unlock the server.\n\n📜 Rules confirmation\n✅ Verified role for eligible members\n\nDiscord-wide moderation history is private and unavailable to bots.", discord.Colour.from_rgb(177, 77, 255))
    panel.add_field(name="🔐 Verification steps", value="1️⃣ Start verification\n2️⃣ Review your result\n3️⃣ Confirm the rules\n4️⃣ Receive access", inline=False)
    panel.add_field(name="💬 Need help?", value="If you need help, please open a support ticket.", inline=False)
    panel.set_footer(text="Grid A1 • Secure, fair, Discord-only verification")
    message = await channel.send(embed=panel, view=VerifyPanel(bot.database))
    bot.database.upsert_config(i.guild.id, verify_panel_channel=channel.id, verify_panel_message=message.id, verify_role=role.id)
    await i.response.send_message(embed=embed("💜 Verification panel created", f"✅ Panel posted in {channel.mention}.\n🎭 Role: {role.mention}"), ephemeral=True)


@ticket_group.command(name="remove", description="Remove a user from the current ticket")
@staff()
async def ticket_remove(i: discord.Interaction, user: discord.Member):
    if not isinstance(i.channel, discord.TextChannel) or not is_ticket(i.channel): return await i.response.send_message("❌ This only works inside a ticket.", ephemeral=True)
    data = parse_ticket_topic(i.channel)
    if user.id == int(data.get("owner", "0")): return await i.response.send_message("❌ You cannot remove the ticket owner.", ephemeral=True)
    try: await i.channel.set_permissions(user, overwrite=None, reason=f"Removed from ticket by {i.user}")
    except discord.Forbidden: return await i.response.send_message("❌ I cannot remove that user from this ticket.", ephemeral=True)
    await i.response.send_message(f"✅ Removed {user.mention} from this ticket.", ephemeral=True)

@bot.tree.command(name="staff", description="List configured ticket staff roles")
@app_commands.checks.has_permissions(manage_guild=True)
async def staff_list(i: discord.Interaction):
    roles = [i.guild.get_role(role_id) for role_id in bot.database.staff_role_ids(i.guild.id)]
    roles = [role for role in roles if role]
    e = embed("💜 Grid A1 • Staff notification roles", "Roles that receive ticket alerts.", discord.Colour.from_rgb(177, 77, 255))
    e.add_field(name="Configured roles", value="\n".join(f"{n}. {role.mention}" for n, role in enumerate(roles, 1)) if roles else "No staff notification roles configured.", inline=False)
    await i.response.send_message(embed=e, ephemeral=True)

wipefeed_group = app_commands.Group(name="wipefeed", description="Manage EU 6X wipe announcements")
bot.tree.add_command(wipefeed_group)

@wipefeed_group.command(name="enable", description="Enable or disable wipefeed announcements")
@app_commands.checks.has_permissions(manage_guild=True)
async def wipefeed_enable(i: discord.Interaction, enabled: bool):
    bot.database.upsert_config(i.guild.id, wipefeed_enabled=int(enabled))
    state = "enabled" if enabled else "disabled"
    await i.response.send_message(f"✅ Wipefeed is now **{state}**.", ephemeral=True)

@wipefeed_group.command(name="send", description="Post an EU 6X wipe announcement")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(timestamp="Unix timestamp, for example 1785524400", channel="Channel where the wipe announcement will be posted")
async def wipefeed_send(i: discord.Interaction, timestamp: str, channel: discord.TextChannel):
    config = bot.database.config(i.guild.id)
    if not config or not config["wipefeed_enabled"]: return await i.response.send_message("⚠️ Wipefeed is disabled. Run `/wipefeed enable enabled:true` first.", ephemeral=True)
    try: unix = int(timestamp.strip().replace("<t:", "").split(":", 1)[0])
    except ValueError: return await i.response.send_message("❌ Timestamp must be a Unix timestamp, such as `1785524400`.", ephemeral=True)
    if unix < 0: return await i.response.send_message("❌ Timestamp cannot be negative.", ephemeral=True)
    content = (f"🇪🇺 **a rust server EU 6X WIPE 2 months ago !** <t:{unix}:R> 🇪🇺\n\n" f"**Server Name**\nVALORA | CLAN | 5X | EU | WEEKLY | .gg/valora5x\n\n" f"Search the name displayed above and add the server to your favorites to be ready.\n\n" f"**Server Information**\n:jack: 5X Gather Rates\n:bp: Instant Crafting\n:crate: Fast Respawn\n:Time: Automatic Events\n:player: 100+ Players\n\n" f"**Latest wipe** • <t:{unix}:F> (<t:{unix}:R>)")
    try: await channel.send(content)
    except discord.Forbidden: return await i.response.send_message("❌ I cannot post in that channel. Check View Channel and Send Messages permissions.", ephemeral=True)
    bot.database.upsert_config(i.guild.id, wipefeed_channel=channel.id)
    await i.response.send_message(f"✅ EU 6X wipe announcement posted in {channel.mention}.", ephemeral=True)

info_group = app_commands.Group(name="info", description="Show Discord information")
bot.tree.add_command(info_group)

@info_group.command(name="server", description="Show information about this server")
async def info_server(i: discord.Interaction):
    g = i.guild
    if not g: return await i.response.send_message("❌ This command can only be used inside a server.", ephemeral=True)
    owner = g.owner.mention if g.owner else f"<@{g.owner_id}>"
    created = discord.utils.format_dt(g.created_at, style="F")
    member_count = g.member_count or len(g.members)
    bots = sum(1 for member in g.members if member.bot)
    humans = max(0, member_count - bots)
    text_channels = len(g.text_channels)
    voice_channels = len(g.voice_channels)
    categories = len(g.categories)
    role_count = max(0, len(g.roles) - 1)
    boost_level = getattr(g.premium_tier, "name", str(g.premium_tier))
    icon = g.icon.url if g.icon else None
    e = embed(f"💜 {g.name} • Server information", "✨ A detailed overview of this Discord server.", discord.Colour.from_rgb(177, 77, 255))
    if icon: e.set_thumbnail(url=icon)
    e.add_field(name="👑 Owner", value=owner, inline=True)
    e.add_field(name="🆔 Server ID", value=f"`{g.id}`", inline=True)
    e.add_field(name="📅 Created", value=created, inline=True)
    e.add_field(name="👥 Members", value=f"`{member_count:,}` total\n`{humans:,}` humans • `{bots:,}` bots", inline=True)
    e.add_field(name="💬 Channels", value=f"`{text_channels}` text\n`{voice_channels}` voice\n`{categories}` categories", inline=True)
    e.add_field(name="🎭 Roles", value=f"`{role_count}` custom roles", inline=True)
    e.add_field(name="🚀 Boosts", value=f"Level `{g.premium_tier}`\n`{g.premium_subscription_count or 0}` boosts", inline=True)
    e.add_field(name="🛡️ Security", value=f"Verification: `{g.verification_level.name.title()}`\n2FA moderation: `{"Enabled" if g.mfa_level else "Not required"}`", inline=True)
    e.add_field(name="🧩 Server features", value=f"`{len(g.features)}` enabled Discord features", inline=True)
    e.set_footer(text="Grid A1 • Server information")
    await i.response.send_message(embed=e)

roles_group = app_commands.Group(name="roles", description="Display server roles")
bot.tree.add_command(roles_group)
@roles_group.command(name="setchannel", description="Post a stylish list of server roles")
@app_commands.checks.has_permissions(manage_guild=True)
async def roles_setchannel(i: discord.Interaction, channel: discord.TextChannel):
    roles = [role for role in i.guild.roles if not role.is_default()]
    roles.sort(key=lambda role: role.position, reverse=True)
    lines = [f"{role.mention} — {len(role.members)} members" for role in roles]
    description = "\n".join(lines)[:4000] if lines else "No custom roles found."
    e = embed("💜 Grid A1 • Server role directory", "✨ All custom server roles, arranged from highest to lowest.\n\n" + description, discord.Colour.from_rgb(177, 77, 255))
    e.set_footer(text=f"{len(roles)} custom roles • Grid A1 Manager")
    try: await channel.send(embed=e)
    except discord.Forbidden: return await i.response.send_message("❌ I cannot post in that channel.", ephemeral=True)
    await i.response.send_message(f"✅ Role directory posted in {channel.mention}.", ephemeral=True)

@setup_group.command(name="staff", description="Manage optional ticket staff notification roles")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(action="Choose whether to add or remove this staff role", role="Staff role to notify")
@app_commands.choices(action=[app_commands.Choice(name="Add staff notifications", value="add"), app_commands.Choice(name="Remove staff notifications", value="remove")])
async def setup_staff(i: discord.Interaction, action: app_commands.Choice[str], role: discord.Role):
    if role.is_default() or role.managed: return await i.response.send_message("❌ Choose a normal staff role, not @everyone or an integration role.", ephemeral=True)
    action = action.value.lower()
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
    g = guild(i)
    previous = bot.database.config(g.id)
    bot.database.upsert_config(g.id, panel_channel=panel_channel.id, logs_channel=logs_channel.id, ticket_category=category.id, inactivity_hours=inactivity_hours)
    message = None
    if previous and previous["panel_channel"] == panel_channel.id and previous["panel_message"]:
        try:
            message = await panel_channel.fetch_message(previous["panel_message"])
            await message.edit(embed=support_panel(g, bot.database), view=TicketPanel(bot.tickets))
        except discord.NotFound:
            message = None
        except discord.DiscordException:
            return await i.response.send_message("❌ I could not update the existing panel. Check Manage Messages and Embed Links permissions.", ephemeral=True)
    if message is None:
        message = await panel_channel.send(embed=support_panel(g, bot.database), view=TicketPanel(bot.tickets))
    bot.database.upsert_config(g.id, panel_message=message.id)
    await i.response.send_message(f"✅ Grid A1 support panel updated in {panel_channel.mention}; logs go to {logs_channel.mention}.", ephemeral=True)

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

@bot.tree.error
async def on_app_command_error(i,error):
    log.exception("Application command failed", exc_info=error)
    original = getattr(error, "original", error)
    if isinstance(original, discord.HTTPException) and original.status == 429:
        msg = "Discord rate-limited this sync. Please wait before trying /sync again."
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "You do not have permission to use that command."
    elif isinstance(error, (OwnerConfigurationError, OwnerOnlyError)):
        msg = str(error)
    elif isinstance(error, RuntimeError):
        msg = str(error)
    else:
        msg = "That command could not be completed. Check setup and bot permissions."
    if i.response.is_done(): await i.followup.send(msg, ephemeral=True)
    else: await i.response.send_message(msg, ephemeral=True)

def run():
    if not settings.token: raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it outside Discord.")
    bot.run(settings.token, log_handler=None)

if __name__ == "__main__": run()
