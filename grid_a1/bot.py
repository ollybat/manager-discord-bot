from __future__ import annotations
import logging
import time
import hashlib
import discord
import json
from discord import app_commands
from discord.ext import commands, tasks
from .config import Settings, configure_logging, validate_runtime
from .database import Database
from .embeds import embed, support_panel, inactivity_indicator
from .tickets import TicketService, claim
from .polls import (
    DEFAULT_POLL_DURATION_HOURS,
    MAX_POLL_DURATION_HOURS,
    PollService,
    PollVoteView,
    parse_poll_options,
    poll_embed,
)
from .utils import is_http_url, is_ticket, parse_ticket_topic, staff_member, detected_external_links, normalize_domain, safe_json_list, utcnow
from .views import DashboardView, OwnerInactivityView, TicketControls, TicketPanel, VerifyPanel
from .welcomer import missing, send_welcome, welcome_embed
from .commands import OwnerConfigurationError, OwnerOnlyError, register_commands
settings = Settings.from_env(); configure_logging(settings.log_level)
log = logging.getLogger("grid-a1-manager")
intents = discord.Intents.default(); intents.members = True; intents.message_content = True
class GridA1Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=settings.prefix, intents=intents, help_command=None)
        self.database = Database(settings.database_path); self.tickets = TicketService(self.database); self.polls = PollService(self.database)
        self._global_sync_last_at = 0.0
        self.settings_owner_id = settings.owner_id
        self.started_at = time.time()
        self._global_sync_in_progress = False
        register_commands(self)
    async def setup_hook(self):
        log.info("Starting Grid A1: database=%s prefix=%s owner_configured=%s", self.database.path, settings.prefix, bool(settings.owner_id))
        for problem in validate_runtime(settings):
            log.warning("Startup configuration: %s", problem)
        self.database.migrate()
        store = self.database.startup_check()
        if not store["ok"]:
            raise RuntimeError(f"SQLite startup check failed: {store}")
        log.info("SQLite ready: schema=%s WAL=enabled indexes=verified", store["schema_version"])
        self.add_view(TicketPanel(self.tickets))
        self.add_view(TicketControls(self.tickets))
        self.add_view(VerifyPanel(self.database))
        for row in self.database.open_tickets_all():
            self.add_view(OwnerInactivityView(self.tickets, row["ticket_id"]))
        for poll in self.database.polls_with_messages_all():
            options = self.database.poll_options(poll["poll_id"])
            if len(options) < 2:
                log.warning("Skipping persistent view for malformed poll %s", poll["poll_id"])
                continue
            self.add_view(
                PollVoteView(
                    self.database,
                    poll["poll_id"],
                    options,
                    disabled=poll["status"] != "open",
                )
            )
        self.refresh_panels.start()
        self.inactivity_loop.start()
        self.poll_expiration_loop.start()
        # Clear stale guild registrations, then publish exactly one global tree.
        # Do not copy global commands into guild trees.
        cleared = 0
        for existing_guild in self.guilds:
            guild = discord.Object(id=existing_guild.id)
            self.tree.clear_commands(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                cleared += 1
            except discord.HTTPException as error:
                if error.status == 429:
                    log.warning("Command cleanup rate-limited for guild %s; continuing startup", existing_guild.id)
                else:
                    raise
        try:
            synced = await self.tree.sync()
            log.info("Command startup sync complete: cleared %s guild(s), published %s global command(s)", cleared, len(synced))
        except discord.HTTPException as error:
            if error.status == 429:
                log.warning("Global command startup sync rate-limited; continuing startup")
            else:
                raise
    async def sync_commands_on_request(self):
        """Run an explicit owner-requested sync with an in-memory cooldown/guard."""
        now = time.monotonic(); cooldown = 60.0
        if self._global_sync_in_progress: raise RuntimeError("A command sync is already in progress; please wait for its response.")
        remaining = cooldown - (now - self._global_sync_last_at)
        if remaining > 0: raise RuntimeError(f"Global command sync is on cooldown; try again in {remaining:.0f}s.")
        self._global_sync_in_progress = True; self._global_sync_last_at = now
        try:
            out=[]
            # Remove stale guild-scoped copies from every guild, then publish one global set.
            for existing_guild in self.guilds:
                guild=discord.Object(id=existing_guild.id)
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)
            out.append(f"cleared guild-scoped command copies from {len(self.guilds)} guild(s)")
            synced_global=await self.tree.sync(); out.append(f"global: {len(synced_global)}"); return out
        finally: self._global_sync_in_progress = False
    @tasks.loop(seconds=60)
    async def refresh_panels(self):
        for guild in self.guilds:
            try:
                await self.refresh_guild_panel(guild)
            except Exception:
                log.exception("Panel refresh failed for guild %s; continuing with other guilds", guild.id)

    async def refresh_guild_panel(self, guild):
        """Refresh one guild independently so one bad config never stops the loop."""
        config = self.database.config(guild.id)
        if not config or not config['panel_channel'] or not config['panel_message']: return
        channel = guild.get_channel(config['panel_channel'])
        if not isinstance(channel, discord.TextChannel): return
        try:
            message = await channel.fetch_message(config['panel_message'])
            panel = support_panel(guild, self.database)
            fingerprint = hashlib.sha256(json.dumps(panel.to_dict(), sort_keys=True).encode()).hexdigest()
            if config['panel_fingerprint'] != fingerprint:
                await message.edit(embed=panel, view=TicketPanel(self.tickets))
                self.database.upsert_config(guild.id, panel_fingerprint=fingerprint)
        except discord.NotFound:
            try:
                panel = support_panel(guild, self.database)
                message = await channel.send(embed=panel, view=TicketPanel(self.tickets))
                fingerprint = hashlib.sha256(json.dumps(panel.to_dict(), sort_keys=True).encode()).hexdigest()
                self.database.upsert_config(guild.id, panel_message=message.id, panel_fingerprint=fingerprint)
            except discord.DiscordException:
                log.exception("Panel recovery failed for guild %s", guild.id)
        except discord.DiscordServerError as error:
            log.warning("Panel refresh temporarily unavailable for guild %s (HTTP %s); will retry next cycle", guild.id, getattr(error, "status", "unknown"))
        except discord.HTTPException as error:
            log.warning("Panel refresh request failed for guild %s (HTTP %s); will retry next cycle", guild.id, getattr(error, "status", "unknown"))
        except discord.DiscordException:
            log.warning("Panel refresh encountered a Discord error for guild %s; will retry next cycle", guild.id)

    @tasks.loop(minutes=5)
    async def inactivity_loop(self):
        from datetime import datetime, timezone, timedelta
        for guild in list(self.guilds):
            try:
                config = self.database.config(guild.id)
                if not config: continue
                threshold = int(config['inactivity_hours'])
                for row in self.database.open_tickets(guild.id):
                    try:
                        channel = guild.get_channel(row['channel_id'])
                        if channel is None:
                            try:
                                channel = await guild.fetch_channel(row['channel_id'])
                            except discord.NotFound:
                                self.database.mark_channel_missing(row['channel_id'])
                                continue
                            except discord.Forbidden:
                                log.warning('Cannot verify ticket channel %s; leaving its record untouched', row['channel_id'])
                                continue
                            except discord.HTTPException as error:
                                log.warning('Could not fetch ticket channel %s (HTTP %s); retrying later', row['channel_id'], getattr(error, 'status', 'unknown'))
                                continue
                        if not isinstance(channel, discord.TextChannel):
                            log.warning('Ticket %s resolves to a non-text channel; leaving its record untouched', row['ticket_id'])
                            continue
                        indicator, duration = inactivity_indicator(row['last_activity_at'], threshold, bool(row['owner_left']))
                        red = indicator == '🔴'
                        if red and not row['inactivity_notice_at']:
                            now = datetime.now(timezone.utc)
                            deadline = (now + timedelta(hours=24)).isoformat()
                            if row['owner_left']:
                                self.database.update_ticket(row['ticket_id'], inactivity_notice_at=now.isoformat(), auto_close_at=deadline, auto_close_reason='owner_left')
                                self.database.audit(guild.id,row['ticket_id'],0,'inactivity_owner_left', '{"auto_close_hours":24}')
                            else:
                                owner = guild.get_member(row['owner_id'])
                                if owner is None:
                                    try:
                                        owner = await guild.fetch_member(row['owner_id'])
                                    except discord.NotFound:
                                        self.database.update_ticket(row['ticket_id'], owner_left=1, inactivity_notice_at=now.isoformat(), auto_close_at=deadline, auto_close_reason='owner_left')
                                        self.database.audit(guild.id,row['ticket_id'],0,'inactivity_owner_missing', '{"auto_close_hours":24}')
                                        owner = None
                                    except discord.HTTPException as error:
                                        log.warning('Could not fetch ticket owner %s (HTTP %s); leaving ticket open', row['owner_id'], getattr(error, 'status', 'unknown'))
                                        continue
                                if owner is not None:
                                    view = OwnerInactivityView(self.tickets, row['ticket_id'])
                                    self.add_view(view)
                                    try:
                                        await owner.send(f'🔴 Your Grid A1 ticket **{row["ticket_id"]}** has been inactive for **{duration}**. Please choose an option below within 24 hours.', view=view)
                                    except discord.Forbidden:
                                        log.info('Ticket owner %s has DMs disabled; leaving the ticket open without an auto-close deadline', row['owner_id'])
                                        continue
                                    except discord.HTTPException as error:
                                        log.warning('Could not send inactivity notice to %s (HTTP %s); leaving ticket open', row['owner_id'], getattr(error, 'status', 'unknown'))
                                        continue
                                    self.database.update_ticket(row['ticket_id'], inactivity_notice_at=now.isoformat(), auto_close_at=deadline)
                                    self.database.audit(guild.id,row['ticket_id'],0,'inactivity_notice', '{"indicator":"red"}')
                        elif red and row['auto_close_at'] and datetime.fromisoformat(row['auto_close_at']) <= datetime.now(timezone.utc):
                            await self.tickets.close_system(guild, channel, 'Auto-closed after inactivity grace period')
                            continue
                        await self.tickets.refresh_status(channel, row)
                    except Exception: log.exception('Inactivity ticket failed: %s', row['ticket_id'])
            except Exception: log.exception('Inactivity loop failed for guild %s; continuing', getattr(guild, 'id', 'unknown'))

    @tasks.loop(minutes=1)
    async def poll_expiration_loop(self):
        """Close expired polls and disable their persistent vote selectors."""
        now = utcnow().isoformat()
        try:
            due_polls = self.database.polls_due(now)
        except Exception:
            log.exception("Could not load polls for expiration; retrying next cycle")
            return

        for poll in due_polls:
            try:
                ended = self.database.end_poll(poll["poll_id"], 0, now)
                if not ended:
                    continue
                guild = self.get_guild(poll["guild_id"])
                if guild is None:
                    continue
                updated = await self.polls.update_message(
                    guild,
                    poll["poll_id"],
                    disabled=True,
                )
                if not updated:
                    log.warning("Poll %s expired but its message could not be refreshed", poll["poll_id"])
            except Exception:
                log.exception("Poll expiration failed for %s", poll["poll_id"])

    @inactivity_loop.before_loop
    async def before_inactivity_loop(self): await self.wait_until_ready()
    @poll_expiration_loop.before_loop
    async def before_poll_expiration_loop(self): await self.wait_until_ready()
    @refresh_panels.before_loop
    async def before_refresh_panels(self): await self.wait_until_ready()
bot = GridA1Bot()
setup_group = app_commands.Group(name="setup", description="⚙️ Configure server roles and support systems")
welcomer_group = app_commands.Group(name="welcomer", description="👋 Preview and test welcome messages")
ticket_group = app_commands.Group(name="ticket", description="🎫 Manage and assign support tickets")
poll_group = app_commands.Group(name="poll", description="📊 Create, vote on, and manage server polls")
bot.tree.add_command(setup_group)
bot.tree.add_command(welcomer_group)
bot.tree.add_command(ticket_group)
bot.tree.add_command(poll_group)
def guild(i): return i.guild
def _privileged(interaction: discord.Interaction, require_staff: bool = False) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member): return False
    if interaction.user.id == interaction.guild.owner_id or interaction.user.id == settings.owner_id: return True
    permissions = interaction.user.guild_permissions
    if permissions.administrator or permissions.manage_guild: return True
    return require_staff and (permissions.manage_channels or bool(set(role.id for role in interaction.user.roles) & set(bot.database.configured_permission_role_ids(interaction.guild.id))))

def _prefix_privileged(ctx, require_staff: bool = False) -> bool:
    if not ctx.guild or not isinstance(ctx.author, discord.Member): return False
    if ctx.author.id == ctx.guild.owner_id or ctx.author.id == settings.owner_id: return True
    permissions = ctx.author.guild_permissions
    if permissions.administrator or permissions.manage_guild: return True
    return require_staff and (permissions.manage_channels or bool(set(role.id for role in ctx.author.roles) & set(bot.database.configured_permission_role_ids(ctx.guild.id))))

def _permission_check(require_staff: bool = False):
    async def predicate(interaction: discord.Interaction) -> bool:
        return _privileged(interaction, require_staff)
    return app_commands.check(predicate)

def admin(): return _permission_check(False)
def staff(): return _permission_check(True)

def _dashboard_access(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member): return False
    if interaction.user.id == interaction.guild.owner_id: return True
    config = bot.database.config(interaction.guild.id)
    if not config or not config["owner_role"] or not config["co_owner_role"]: return False
    return bool({role.id for role in interaction.user.roles} & {int(config["owner_role"]), int(config["co_owner_role"])})

def dashboard_access():
    async def predicate(interaction: discord.Interaction) -> bool: return _dashboard_access(interaction)
    return app_commands.check(predicate)

def server_owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.user and interaction.user.id == interaction.guild.owner_id)
    return app_commands.check(predicate)

@bot.tree.command(name="anti-links", description="🛡️ Configure protection against outside links and invites")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(enabled="Enable protection", action="delete, delete_warn, or delete_log", log_channel="Optional moderation log channel")
@app_commands.choices(action=[app_commands.Choice(name="🗑️ Delete", value="delete"),app_commands.Choice(name="⚠️ Delete and warn", value="delete_warn"),app_commands.Choice(name="📋 Delete and log", value="delete_log")])
async def anti_links(i: discord.Interaction, enabled: bool, action: app_commands.Choice[str] = None, log_channel: discord.TextChannel = None):
    await i.response.defer(ephemeral=True)
    mode = action.value if action else "delete_warn"
    bot.database.upsert_config(i.guild.id, anti_links_enabled=int(enabled), anti_links_action=mode, anti_links_log_channel=log_channel.id if log_channel else None)
    await i.followup.send(embed=embed("🛡️ Anti-links settings saved", f"Protection: {'enabled' if enabled else 'disabled'}\nAction: {mode}"), ephemeral=True)

@bot.tree.command(name="report", description="🚩 Privately report a server member to staff")
@app_commands.guild_only()
@app_commands.describe(member="Member being reported", reason="What happened?", proof_link="Optional link to supporting evidence", proof_file="Optional screenshot or evidence file")
async def report_member(i: discord.Interaction, member: discord.Member, reason: str | None = None, proof_link: str | None = None, proof_file: discord.Attachment | None = None):
    guild = i.guild
    if not guild:
        return await i.response.send_message("❌ Use /report inside the server where the incident happened.", ephemeral=True)
    config = bot.database.config(guild.id)
    report_channel = guild.get_channel(config["report_channel"]) if config and config["report_channel"] else None
    if not isinstance(report_channel, discord.TextChannel):
        return await i.response.send_message("⚠️ Reports are not configured yet. Ask a server owner to choose a report channel in /dashboard.", ephemeral=True)
    if report_channel.permissions_for(guild.default_role).view_channel:
        return await i.response.send_message("⚠️ The configured report channel is visible to @everyone. Ask an admin to restrict it to trusted staff before submitting reports.", ephemeral=True)
    link = (proof_link or "").strip()
    if link:
        if len(link) > 1000 or not is_http_url(link):
            return await i.response.send_message("❌ Proof link must be a valid http(s) URL under 1000 characters.", ephemeral=True)
    if proof_file and proof_file.size > 8_000_000:
        return await i.response.send_message("❌ Evidence files must be 8 MB or smaller.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    e = embed("🚩 Member report", "A server member submitted a report for staff review.", discord.Colour.red())
    e.add_field(name="Reported member", value=f"{member.mention} (`{member.id}`)", inline=True)
    e.add_field(name="Submitted by", value=f"{i.user.mention} (`{i.user.id}`)", inline=True)
    clean_reason = (reason or "").strip() or "No reason provided"
    e.add_field(name="Reason", value=discord.utils.escape_markdown(clean_reason)[:1000], inline=False)
    if link: e.add_field(name="Proof link", value=link, inline=False)
    if proof_file: e.add_field(name="Uploaded evidence", value=f"{discord.utils.escape_markdown(proof_file.filename)} ({proof_file.size:,} bytes)", inline=False)
    e.set_footer(text=f"{guild.name} • Report submitted privately")
    try:
        upload = await proof_file.to_file() if proof_file else None
        await report_channel.send(embed=e, file=upload, allowed_mentions=discord.AllowedMentions.none())
    except discord.DiscordException as error:
        log.exception("Could not deliver a member report in guild %s: %s", guild.id, error)
        return await i.followup.send("❌ I could not deliver this report. Please contact a moderator directly.", ephemeral=True)
    metadata = json.dumps({"target_id": member.id, "reason": (reason or "").strip()[:1000], "proof_link": link or None, "proof_filename": proof_file.filename if proof_file else None, "channel_id": report_channel.id})
    try: bot.database.audit(guild.id, None, i.user.id, "report_submitted", metadata)
    except Exception: log.exception("Report was delivered but its audit row could not be recorded (guild %s)", guild.id)
    await i.followup.send("✅ Your report was sent privately to the server's report channel. Thank you.", ephemeral=True)

@bot.tree.command(name="dashboard", description="🎛️ Open your private server setup and status dashboard")
async def dashboard(i: discord.Interaction):
    await i.response.defer(ephemeral=True)
    if not _dashboard_access(i):
        return await i.followup.send("🔒 Dashboard access requires the configured Owner or Co-owner role. The server owner can initialize these with `/setup roles`.", ephemeral=True)
    if not i.guild: return await i.followup.send("❌ The dashboard can only be opened inside a server.", ephemeral=True)
    view = DashboardView(bot.database, settings.owner_id)
    await i.followup.send(embed=view.dashboard_embed(i.guild), view=view, ephemeral=True)

@bot.tree.command(name="kick", description="👢 Remove a member from this server with a recorded reason")
@staff()
@app_commands.describe(member="Member to kick", reason="Why the member is being kicked")
async def moderation_kick(i: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot kick yourself or the server owner.", ephemeral=True)
    if member.top_role >= i.user.top_role and i.user.id != i.guild.owner_id: return await i.response.send_message("❌ That member has an equal or higher role than you.", ephemeral=True)
    if not i.guild.me or member.top_role >= i.guild.me.top_role: return await i.response.send_message("❌ My bot role must be above that member.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    try: await member.kick(reason=f"{reason} • Moderator: {i.user}")
    except discord.Forbidden: return await i.followup.send("❌ Discord denied the kick. Check Kick Members permission and role hierarchy.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "kick", discord.utils.escape_markdown(reason)[:500])
    await i.followup.send(embed=embed("💜 Member kicked", f"✅ {member.mention} was removed from the server.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.tree.command(name="ban", description="🔨 Ban a member from this server with a recorded reason")
@staff()
@app_commands.describe(member="Member to ban", reason="Why the member is being banned")
async def moderation_ban(i: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot ban yourself or the server owner.", ephemeral=True)
    if member.top_role >= i.user.top_role and i.user.id != i.guild.owner_id: return await i.response.send_message("❌ That member has an equal or higher role than you.", ephemeral=True)
    if not i.guild.me or member.top_role >= i.guild.me.top_role: return await i.response.send_message("❌ My bot role must be above that member.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    try: await member.ban(reason=f"{reason} • Moderator: {i.user}", delete_message_seconds=0)
    except discord.Forbidden: return await i.followup.send("❌ Discord denied the ban. Check Ban Members permission and role hierarchy.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "ban", discord.utils.escape_markdown(reason)[:500])
    await i.followup.send(embed=embed("💜 Member banned", f"🔨 {member.mention} was banned from the server.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.tree.command(name="warn", description="⚠️ Record a moderation warning for a member")
@staff()
@app_commands.describe(member="Member receiving the warning", reason="What rule or behavior the warning concerns")
async def moderation_warn(i: discord.Interaction, member: discord.Member, reason: str):
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot warn yourself or the server owner.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "warn", f"member={member.id}; {discord.utils.escape_markdown(reason)[:450]}")
    await i.response.send_message(embed=embed("💜 Warning recorded", f"⚠️ A warning was recorded for {member.mention}.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.tree.command(name="timeout", description="⏳ Temporarily timeout a member for 1 minute to 28 days")
@staff()
@app_commands.describe(member="Member to timeout", minutes="Duration in minutes (1–40320)", reason="Why the timeout is being applied")
async def moderation_timeout(i: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason provided"):
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot timeout yourself or the server owner.", ephemeral=True)
    if member.top_role >= i.user.top_role and i.user.id != i.guild.owner_id: return await i.response.send_message("❌ That member has an equal or higher role than you.", ephemeral=True)
    if not i.guild.me or member.top_role >= i.guild.me.top_role: return await i.response.send_message("❌ My bot role must be above that member.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    try: await member.timeout(discord.utils.utcnow() + __import__("datetime").timedelta(minutes=minutes), reason=f"{reason} • Moderator: {i.user}")
    except discord.Forbidden: return await i.followup.send("❌ Discord denied the timeout. Check Moderate Members permission and role hierarchy.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "timeout", f"member={member.id}; minutes={minutes}; {discord.utils.escape_markdown(reason)[:400]}")
    await i.followup.send(embed=embed("💜 Member timed out", f"⏳ {member.mention} was timed out for **{minutes} minute(s)**.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.command(name="lock", help="🔒 Prevent members from sending messages in this channel (staff only).")
async def prefix_lock(ctx):
    if not ctx.guild or not _prefix_privileged(ctx, True): return await ctx.send("❌ Only staff, the server owner, or the bot owner can lock channels.")
    try: await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Channel locked by {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ I cannot lock this channel. Check Manage Channels and Manage Permissions.")
    await ctx.send("🔒 This channel is now locked for members.")

@bot.command(name="unlock", help="🔓 Restore member messaging in this channel (staff only).")
async def prefix_unlock(ctx):
    if not ctx.guild or not _prefix_privileged(ctx, True): return await ctx.send("❌ Only staff, the server owner, or the bot owner can unlock channels.")
    try: await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Channel unlocked by {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ I cannot unlock this channel. Check Manage Channels and Manage Permissions.")
    await ctx.send("🔓 This channel is now unlocked for members.")

@setup_group.command(name="roles", description="🎭 Set the five distinct staff permission roles")
@server_owner_only()
@app_commands.describe(owner_role="Owner staff role", co_owner_role="Co-owner staff role", head_admin_role="Head administrator staff role", admin_role="Administrator staff role", moderator_role="Moderator staff role")
async def setup_roles(i: discord.Interaction, owner_role: discord.Role, co_owner_role: discord.Role, head_admin_role: discord.Role, admin_role: discord.Role, moderator_role: discord.Role):
    await i.response.defer(ephemeral=True)
    roles = {"owner_role": owner_role, "co_owner_role": co_owner_role, "head_admin_role": head_admin_role, "admin_role": admin_role, "moderator_role": moderator_role}
    invalid = [role.mention for role in roles.values() if role.guild.id != i.guild.id or role.is_default() or role.managed]
    if invalid: return await i.followup.send(embed=embed("💜 Role setup not saved", "❌ These roles cannot be used: " + ", ".join(invalid)), ephemeral=True)
    if len({role.id for role in roles.values()}) != len(roles): return await i.followup.send(embed=embed("💜 Role setup not saved", "❌ Each permission level must use a different role."), ephemeral=True)
    bot.database.upsert_config(i.guild.id, **{name: role.id for name, role in roles.items()})
    e = embed("💜 Grid A1 • Permission roles saved", "✅ Staff access roles are now configured for this server.", discord.Colour.from_rgb(177, 77, 255))
    e.add_field(name="👑 Owner", value=owner_role.mention, inline=True)
    e.add_field(name="🤝 Co-owner", value=co_owner_role.mention, inline=True)
    e.add_field(name="🛡️ Head admin", value=head_admin_role.mention, inline=True)
    e.add_field(name="⚙️ Admin", value=admin_role.mention, inline=True)
    e.add_field(name="🔨 Moderator", value=moderator_role.mention, inline=True)
    e.set_footer(text="Grid A1 • Permission configuration • Changes saved to SQLite")
    await i.followup.send(embed=e, ephemeral=True)

@bot.tree.command(name="verifypanel", description="✅ Publish a verification panel and assignable role")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="Text channel where the public panel will appear", role="Role granted after successful verification")
async def verifypanel(i: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if role.is_default(): return await i.response.send_message("❌ You cannot use @everyone as the verification role.", ephemeral=True)
    if not i.guild.me or role >= i.guild.me.top_role: return await i.response.send_message("❌ Move Grid A1's bot role above the verification role first.", ephemeral=True)
    panel = embed("💜 Grid A1 • Secure Verification", "✨ Complete the short verification check to unlock the server.\n\n📜 Rules confirmation\n✅ Verified role for eligible members\n\nDiscord-wide moderation history is private and unavailable to bots.", discord.Colour.from_rgb(177, 77, 255))
    panel.add_field(name="🔐 Verification steps", value="1️⃣ Start verification\n2️⃣ Review your result\n3️⃣ Confirm the rules\n4️⃣ Receive access", inline=False)
    panel.add_field(name="💬 Need help?", value="If you need help, please open a support ticket.", inline=False)
    panel.set_footer(text="Grid A1 • Secure, fair, Discord-only verification")
    await i.response.defer(ephemeral=True)
    message = await channel.send(embed=panel, view=VerifyPanel(bot.database))
    bot.database.upsert_config(i.guild.id, verify_panel_channel=channel.id, verify_panel_message=message.id, verify_role=role.id)
    await i.followup.send(embed=embed("💜 Verification panel created", f"✅ Panel posted in {channel.mention}.\n🎭 Role: {role.mention}"), ephemeral=True)


@ticket_group.command(name="remove", description="👤 Remove a member from the current ticket")
@staff()
@app_commands.describe(user="Member who should lose access to this ticket")
async def ticket_remove(i: discord.Interaction, user: discord.Member):
    if not isinstance(i.channel, discord.TextChannel) or not is_ticket(i.channel): return await i.response.send_message("❌ This only works inside an active ticket.", ephemeral=True)
    row = bot.database.ticket_by_channel(i.channel.id)
    if not row: return await i.response.send_message("⚠️ This ticket is already closed or unavailable.", ephemeral=True)
    if user.id == row["owner_id"]: return await i.response.send_message("❌ You cannot remove the ticket owner.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    try: await i.channel.set_permissions(user, overwrite=discord.PermissionOverwrite(view_channel=False, send_messages=False, read_message_history=False), reason=f"Removed from ticket by {i.user}")
    except discord.Forbidden: return await i.followup.send("❌ I cannot remove that user from this ticket. Check Manage Channels/Permissions.", ephemeral=True)
    bot.database.audit(i.guild.id, row["ticket_id"], i.user.id, "member_removed", json.dumps({"member_id": user.id}))
    await i.followup.send(f"✅ Removed {user.mention} from this ticket.", ephemeral=True)

wipefeed_group = app_commands.Group(name="wipefeed", description="📣 Configure and post wipe announcements")
bot.tree.add_command(wipefeed_group)

@wipefeed_group.command(name="enable", description="🔔 Turn wipe announcements on or off")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(enabled="Whether new wipe announcements are enabled")
async def wipefeed_enable(i: discord.Interaction, enabled: bool):
    bot.database.upsert_config(i.guild.id, wipefeed_enabled=int(enabled))
    state = "enabled" if enabled else "disabled"
    await i.response.send_message(f"✅ Wipefeed is now **{state}**.", ephemeral=True)

@wipefeed_group.command(name="send", description="📣 Post an EU 6X wipe announcement")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(timestamp="Unix timestamp, for example 1785524400", channel="Channel where the wipe announcement will be posted")
async def wipefeed_send(i: discord.Interaction, timestamp: str, channel: discord.TextChannel):
    config = bot.database.config(i.guild.id)
    if not config or not config["wipefeed_enabled"]: return await i.response.send_message("⚠️ Wipefeed is disabled. Run `/wipefeed enable enabled:true` first.", ephemeral=True)
    try: unix = int(timestamp.strip().replace("<t:", "").split(":", 1)[0])
    except ValueError: return await i.response.send_message("❌ Timestamp must be a Unix timestamp, such as `1785524400`.", ephemeral=True)
    if unix < 0: return await i.response.send_message("❌ Timestamp cannot be negative.", ephemeral=True)
    content = (f"🇪🇺 **EU 6X WIPE ANNOUNCEMENT** • <t:{unix}:R> 🇪🇺\n\n" f"**Server Name**\nVALORA | CLAN | 5X | EU | WEEKLY | .gg/valora5x\n\n" f"Search the name displayed above and add the server to your favorites to be ready.\n\n" f"**Server Information**\n:jack: 5X Gather Rates\n:bp: Instant Crafting\n:crate: Fast Respawn\n:Time: Automatic Events\n:player: 100+ Players\n\n" f"**Latest wipe** • <t:{unix}:F> (<t:{unix}:R>)")
    await i.response.defer(ephemeral=True)
    try: await channel.send(content)
    except discord.Forbidden: return await i.followup.send("❌ I cannot post in that channel. Check View Channel and Send Messages permissions.", ephemeral=True)
    bot.database.upsert_config(i.guild.id, wipefeed_channel=channel.id)
    await i.followup.send(f"✅ EU 6X wipe announcement posted in {channel.mention}.", ephemeral=True)

info_group = app_commands.Group(name="info", description="🌐 View server information")
bot.tree.add_command(info_group)

@info_group.command(name="server", description="🏰 View server members, channels, roles, and settings")
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
    mfa_status = "Enabled" if g.mfa_level else "Not required"
    e.add_field(name="🛡️ Security", value=f"Verification: `{g.verification_level.name.title()}`\n2FA moderation: `{mfa_status}`", inline=True)
    e.add_field(name="🧩 Server features", value=f"`{len(g.features)}` enabled Discord features", inline=True)
    e.set_footer(text="Grid A1 • Server information")
    await i.response.send_message(embed=e)

roles_group = app_commands.Group(name="roles", description="🎭 Display and publish the server role directory")
bot.tree.add_command(roles_group)
@roles_group.command(name="setchannel", description="📋 Post the server role directory in a text channel")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(channel="Text channel where the role directory should be posted")
async def roles_setchannel(i: discord.Interaction, channel: discord.TextChannel):
    roles = [role for role in i.guild.roles if not role.is_default()]
    roles.sort(key=lambda role: role.position, reverse=True)
    lines = [f"{role.mention} — {len(role.members)} members" for role in roles]
    description = "\n".join(lines)[:4000] if lines else "No custom roles found."
    e = embed("💜 Grid A1 • Server role directory", "✨ All custom server roles, arranged from highest to lowest.\n\n" + description, discord.Colour.from_rgb(177, 77, 255))
    e.set_footer(text=f"{len(roles)} custom roles • Grid A1 Manager")
    await i.response.defer(ephemeral=True)
    try: await channel.send(embed=e)
    except discord.Forbidden: return await i.followup.send("❌ I cannot post in that channel.", ephemeral=True)
    await i.followup.send(f"✅ Role directory posted in {channel.mention}.", ephemeral=True)

@setup_group.command(name="tickets", description="🎫 Configure ticket channels and publish the support panel")
@admin()
@app_commands.describe(panel_channel="Public channel for the support panel", logs_channel="Private channel for ticket logs", category="Category used for newly opened tickets", inactivity_hours="Hours before the ticket inactivity reminder (1–720)")
async def setup_tickets(i, panel_channel: discord.TextChannel, logs_channel: discord.TextChannel, category: discord.CategoryChannel, inactivity_hours: app_commands.Range[int,1,720]):
    await i.response.defer(ephemeral=True)
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
            return await i.followup.send("❌ I could not update the existing panel. Check Manage Messages and Embed Links permissions.", ephemeral=True)
    if message is None:
        try:
            message = await panel_channel.send(embed=support_panel(g, bot.database), view=TicketPanel(bot.tickets))
        except discord.Forbidden:
            return await i.followup.send("⚠️ Settings were saved, but I could not post the support panel. Grant the bot View Channel, Send Messages, and Embed Links in the panel channel, then retry `/setup tickets`.", ephemeral=True)
        except discord.HTTPException as error:
            log.exception("Could not publish the support panel in guild %s: %s", g.id, error)
            return await i.followup.send("⚠️ Settings were saved, but Discord could not post the panel. Please retry in a moment or ask an admin to check bot permissions.", ephemeral=True)
    bot.database.upsert_config(g.id, panel_message=message.id)
    await i.followup.send(f"✅ Grid A1 support panel updated in {panel_channel.mention}; logs go to {logs_channel.mention}.", ephemeral=True)

@setup_group.command(name="welcomer", description="👋 Configure welcome, shop, links, and community channels")
@admin()
@app_commands.describe(welcome_channel="Channel for new-member greetings", link_channel="Channel containing server links", bot_commands_channel="Channel for bot commands", shop_channel="Channel for store information", verify_channel="Channel for verification guidance")
async def setup_welcomer(i,welcome_channel:discord.TextChannel,link_channel:discord.TextChannel,bot_commands_channel:discord.TextChannel,shop_channel:discord.TextChannel,verify_channel:discord.TextChannel): bot.database.upsert_config(guild(i).id,welcome_channel=welcome_channel.id,link_channel=link_channel.id,bot_commands_channel=bot_commands_channel.id,shop_channel=shop_channel.id,verify_channel=verify_channel.id); await i.response.send_message(f"✅ Welcomer configured for {welcome_channel.mention}.",ephemeral=True)
@welcomer_group.command(name="preview", description="👀 Preview the welcome message privately")
@admin()
async def welcomer_preview(i):
    config=bot.database.config(guild(i).id); problems=missing(guild(i),config)
    if problems: return await i.response.send_message(embed=embed("⚠️ Welcomer is not set up","\n".join(f"• {p}" for p in problems),discord.Colour.red()),ephemeral=True)
    await i.response.send_message(embed=welcome_embed(bot,guild(i),i.user,config),ephemeral=True)
@welcomer_group.command(name="test", description="✉️ Send a test welcome message to the configured channel")
@admin()
async def welcomer_test(i):
    config=bot.database.config(guild(i).id); problems=missing(guild(i),config)
    if problems: return await i.response.send_message("⚠️ Welcomer is not configured correctly. Run `/dashboard` and choose Welcome system to review the channel selections.",ephemeral=True)
    channel=guild(i).get_channel(config['welcome_channel'])
    if not isinstance(channel,discord.TextChannel): return await i.response.send_message("❌ The configured welcome channel is missing. Update it in `/dashboard`.",ephemeral=True)
    await i.response.defer(ephemeral=True)
    await channel.send(embed=welcome_embed(bot,guild(i),i.user,config)); await i.followup.send(f"✅ Welcome test sent to {channel.mention}.",ephemeral=True)
@ticket_group.command(name="claim", description="🙋 Claim the current support ticket")
@staff()
async def ticket_claim(i): await claim(i,bot.tickets)
@ticket_group.command(name="transfer", description="🔁 Assign the current ticket to another staff member")
@staff()
@app_commands.describe(target_member="Staff member who should receive the ticket")
async def ticket_transfer(i,target_member:discord.Member): await claim(i,bot.tickets,target_member)
@ticket_group.command(name="requestclose", description="🔒 Request closure of the current ticket with a reason")
@app_commands.describe(reason="Why the ticket should be closed")
async def ticket_requestclose(i,reason:str):
    if not isinstance(i.channel, discord.TextChannel) or not is_ticket(i.channel): return await i.response.send_message("❌ This command only works inside an active ticket.",ephemeral=True)
    if not staff_member(i.user, bot.database): return await i.response.send_message("🔒 Only ticket staff can request closure.",ephemeral=True)
    row = bot.database.ticket_by_channel(i.channel.id)
    if not row: return await i.response.send_message("⚠️ This ticket is already closed or unavailable.",ephemeral=True)
    if row['status'] == 'close_requested': return await i.response.send_message("⏳ A closure request is already recorded for this ticket.",ephemeral=True)
    if not bot.database.request_ticket_close(row['ticket_id'],i.user.id): return await i.response.send_message("⚠️ This ticket closed while the request was being submitted.",ephemeral=True)
    bot.database.audit(guild(i).id,row['ticket_id'],i.user.id,'close_requested',json.dumps({'reason':discord.utils.escape_markdown(reason)[:500]}))
    await i.response.send_message(embed=embed("🔒 Ticket closure requested", f"A staff member requested closure.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"))
@ticket_group.command(name="close", description="📦 Save the transcript and close the current ticket")
@staff()
@app_commands.describe(reason="Resolution or closure reason saved with the transcript")
async def ticket_close(i,reason:str="No reason provided"): await bot.tickets.close(i,reason)
def _may_manage_poll(interaction: discord.Interaction, poll) -> bool:
    """Allow a poll creator or server-level manager to control that poll."""
    return bool(
        interaction.guild
        and (
            interaction.user.id == poll["creator_id"]
            or _privileged(interaction)
        )
    )


@poll_group.command(
    name="config",
    description="⚙️ Set the default poll channel and duration",
)
@app_commands.guild_only()
@admin()
@app_commands.describe(
    channel="Default channel where new polls are posted",
    default_duration_hours="Default poll length (1–168 hours)",
)
async def poll_config(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    default_duration_hours: app_commands.Range[int, 1, 168] | None = None,
) -> None:
    """Show current poll defaults or update one or both settings."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Poll settings are only available inside a server.",
            ephemeral=True,
        )
        return

    if channel is None and default_duration_hours is None:
        current = bot.database.poll_settings(guild.id)
        channel_id = current["channel_id"] if current else None
        configured_channel = guild.get_channel(channel_id) if channel_id else None
        duration = int(
            current["default_duration_hours"]
            if current
            else DEFAULT_POLL_DURATION_HOURS
        )
        channel_value = (
            configured_channel.mention
            if configured_channel
            else f"<#{channel_id}>"
            if channel_id
            else "Not set — new polls use the channel where `/poll create` is run"
        )
        settings_embed = embed(
            "📊 Poll settings",
            "Configure where new polls are posted and how long they stay open.",
            discord.Colour.from_rgb(177, 77, 255),
        )
        settings_embed.add_field(
            name="📣 Default channel",
            value=channel_value,
            inline=False,
        )
        settings_embed.add_field(
            name="⏳ Default duration",
            value=f"**{duration} hours**",
            inline=True,
        )
        settings_embed.add_field(
            name="🧭 Quick guide",
            value=(
                "Use `/poll config channel` and/or `default_duration_hours` to change "
                "these defaults. Run `/poll create` to publish a poll."
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=settings_embed, ephemeral=True)
        return

    changes: dict[str, int] = {}
    if channel is not None:
        changes["channel_id"] = channel.id
    if default_duration_hours is not None:
        changes["default_duration_hours"] = int(default_duration_hours)
    bot.database.upsert_poll_settings(guild.id, **changes)

    updated = bot.database.poll_settings(guild.id)
    channel_id = updated["channel_id"] if updated else None
    configured_channel = guild.get_channel(channel_id) if channel_id else None
    duration = int(
        updated["default_duration_hours"]
        if updated
        else DEFAULT_POLL_DURATION_HOURS
    )
    channel_display = (
        configured_channel.mention
        if configured_channel
        else f"<#{channel_id}>"
        if channel_id
        else "Not set"
    )
    await interaction.response.send_message(
        embed=embed(
            "✅ Poll settings saved",
            f"📣 Channel: {channel_display}\n"
            f"⏳ Default duration: **{duration} hours**",
            discord.Colour.green(),
        ),
        ephemeral=True,
    )


@poll_group.command(
    name="create",
    description="🗳️ Create a poll members can vote on and change their vote",
)
@app_commands.guild_only()
@admin()
@app_commands.describe(
    question="The question shown on the poll panel (up to 256 characters)",
    options="Two to ten choices separated by | (example: Island | Ragnarok)",
    duration_hours="Optional poll duration; defaults to /poll config",
)
async def poll_create(
    interaction: discord.Interaction,
    question: str,
    options: str,
    duration_hours: app_commands.Range[int, 1, MAX_POLL_DURATION_HOURS] | None = None,
) -> None:
    """Validate and publish a persistent, single-choice poll."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Polls can only be created inside a server.",
            ephemeral=True,
        )
        return

    cleaned_question = question.strip()
    if not cleaned_question:
        await interaction.response.send_message(
            "❌ Poll question cannot be empty.",
            ephemeral=True,
        )
        return
    if len(cleaned_question) > MAX_POLL_QUESTION_LENGTH:
        await interaction.response.send_message(
            f"❌ Poll questions must be {MAX_POLL_QUESTION_LENGTH} characters or fewer.",
            ephemeral=True,
        )
        return

    try:
        choices = parse_poll_options(options)
    except ValueError as error:
        await interaction.response.send_message(
            f"❌ {error}",
            ephemeral=True,
        )
        return

    settings_row = bot.database.poll_settings(guild.id)
    configured_channel_id = settings_row["channel_id"] if settings_row else None
    if configured_channel_id:
        target_channel = guild.get_channel(configured_channel_id)
        if target_channel is None:
            try:
                target_channel = await guild.fetch_channel(configured_channel_id)
            except discord.NotFound:
                target_channel = None
            except discord.HTTPException:
                log.exception("Could not fetch configured poll channel %s", configured_channel_id)
                await interaction.response.send_message(
                    "⚠️ I could not check the configured poll channel. Try again shortly or choose another channel with `/poll config`.",
                    ephemeral=True,
                )
                return
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message(
                "⚠️ The configured poll channel is missing or is not a text channel. Run `/poll config` to choose a new one.",
                ephemeral=True,
            )
            return
    elif isinstance(interaction.channel, discord.TextChannel):
        target_channel = interaction.channel
    else:
        await interaction.response.send_message(
            "⚠️ Choose a default poll channel with `/poll config` before creating a poll here.",
            ephemeral=True,
        )
        return

    if guild.me:
        permissions = target_channel.permissions_for(guild.me)
        missing = [
            label
            for name, label in (
                ("view_channel", "View Channel"),
                ("send_messages", "Send Messages"),
                ("embed_links", "Embed Links"),
            )
            if not getattr(permissions, name)
        ]
        if missing:
            await interaction.response.send_message(
                f"⚠️ The bot is missing poll-channel permissions: {', '.join(missing)}.",
                ephemeral=True,
            )
            return

    configured_duration = (
        int(settings_row["default_duration_hours"])
        if settings_row
        else DEFAULT_POLL_DURATION_HOURS
    )
    poll_duration = int(duration_hours) if duration_hours is not None else configured_duration
    await interaction.response.defer(ephemeral=True)
    try:
        poll_id, message = await bot.polls.create(
            guild,
            target_channel,
            interaction.user.id,
            cleaned_question,
            choices,
            poll_duration,
        )
    except discord.Forbidden:
        log.exception("Poll creation was denied in channel %s", target_channel.id)
        await interaction.followup.send(
            "❌ I could not post the poll. Check the bot's channel permissions and try again.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as error:
        log.exception("Discord rejected poll creation in guild %s: %s", guild.id, error)
        await interaction.followup.send(
            "⚠️ Discord could not publish the poll right now. Please try again shortly.",
            ephemeral=True,
        )
        return
    except Exception:
        log.exception("Poll creation failed for guild %s", guild.id)
        await interaction.followup.send(
            "❌ I could not finish creating the poll. Check the poll channel before retrying; if the post is missing, contact an admin.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=embed(
            "✅ Poll created",
            f"📊 **Poll ID:** `{poll_id}`\n"
            f"📣 **Posted in:** {target_channel.mention}\n"
            f"🔗 [Jump to poll]({message.jump_url})\n"
            f"⏳ **Duration:** {poll_duration} hours\n\n"
            "Members can change their vote until the poll closes.",
            discord.Colour.green(),
        ),
        ephemeral=True,
    )


@poll_group.command(
    name="end",
    description="🔒 End a poll and publish its final results",
)
@app_commands.guild_only()
@app_commands.describe(poll_id="Poll ID shown in the poll panel footer")
async def poll_end(interaction: discord.Interaction, poll_id: str) -> None:
    """End a poll if the caller created it or manages the server."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Polls can only be managed inside a server.",
            ephemeral=True,
        )
        return

    poll = bot.database.poll_for_guild(guild.id, poll_id.strip().upper())
    if not poll:
        await interaction.response.send_message(
            "⚠️ I could not find that poll in this server. Check the poll ID and try again.",
            ephemeral=True,
        )
        return
    if not _may_manage_poll(interaction, poll):
        await interaction.response.send_message(
            "🔒 Only the poll creator or a server manager can end this poll.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        ended_poll, changed, message_updated = await bot.polls.finish(
            guild,
            poll["poll_id"],
            interaction.user.id,
        )
    except Exception:
        log.exception("Poll end failed for %s", poll["poll_id"])
        await interaction.followup.send(
            "❌ I could not end this poll right now. Check its status before trying again.",
            ephemeral=True,
        )
        return

    if not ended_poll:
        await interaction.followup.send(
            "⚠️ The poll was removed before it could be ended.",
            ephemeral=True,
        )
        return

    result_embed = poll_embed(
        ended_poll,
        bot.database.poll_options(ended_poll["poll_id"]),
        bot.database.poll_results(ended_poll["poll_id"]),
    )
    summary = "✅ Poll ended." if changed else "ℹ️ Poll was already ended."
    if not message_updated:
        summary += " The status was saved, but its public panel could not be refreshed."
    result_embed.description = f"{summary}\n\n{result_embed.description}"
    await interaction.followup.send(embed=result_embed, ephemeral=True)


@poll_group.command(
    name="remove",
    description="🗑️ Remove a poll message and its stored votes",
)
@app_commands.guild_only()
@app_commands.describe(poll_id="Poll ID shown in the poll panel footer")
async def poll_remove(interaction: discord.Interaction, poll_id: str) -> None:
    """Delete the poll message and its stored votes for its creator or a manager."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Polls can only be managed inside a server.",
            ephemeral=True,
        )
        return

    normalized_poll_id = poll_id.strip().upper()
    poll = bot.database.poll_for_guild(guild.id, normalized_poll_id)
    if not poll:
        await interaction.response.send_message(
            "⚠️ I could not find that poll in this server. Check the poll ID and try again.",
            ephemeral=True,
        )
        return
    if not _may_manage_poll(interaction, poll):
        await interaction.response.send_message(
            "🔒 Only the poll creator or a server manager can remove this poll.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        await bot.polls.remove_public_message(guild, poll)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I could not delete the poll message. The poll and votes were kept; check Manage Messages.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as error:
        log.warning("Could not remove poll message %s: %s", normalized_poll_id, error)
        await interaction.followup.send(
            "⚠️ Discord could not remove the poll message right now. The poll data was kept.",
            ephemeral=True,
        )
        return

    try:
        removed = bot.database.remove_poll(
            normalized_poll_id,
            removed_by=interaction.user.id,
        )
    except Exception:
        log.exception("Public poll %s was removed but database cleanup failed", normalized_poll_id)
        await interaction.followup.send(
            "⚠️ The public message was removed, but I could not remove its stored record. Contact an admin before recreating it.",
            ephemeral=True,
        )
        return
    if not removed:
        await interaction.followup.send(
            "⚠️ The poll message was removed, but its record was already gone.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"🗑️ Poll `{normalized_poll_id}` and its stored votes were removed.",
        ephemeral=True,
    )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    log.exception("Prefix command failed", exc_info=error)

_anti_link_warning_cooldown = {}
_ANTI_LINK_COOLDOWN_SECONDS = 30

def _prune_anti_link_cooldown(now):
    cutoff = now - _ANTI_LINK_COOLDOWN_SECONDS
    for key, seen in list(_anti_link_warning_cooldown.items()):
        if seen < cutoff: _anti_link_warning_cooldown.pop(key, None)
async def _scan_link_message(message):
    if message.author.bot or not message.guild or not isinstance(message.channel, discord.TextChannel) or not isinstance(message.author, discord.Member): return
    config=bot.database.config(message.guild.id)
    if not config or not config["anti_links_enabled"]: return
    member=message.author; perms=member.guild_permissions; bypass=set(safe_json_list(config["anti_links_bypass_roles"], int)); allowed=set(safe_json_list(config["anti_links_allowed_roles"], int))
    if member.id in {message.guild.owner_id, settings.owner_id} or perms.administrator or perms.manage_messages or any(r.id in bypass for r in member.roles): return
    if any(r.id in allowed for r in member.roles): return
    links=detected_external_links(message.content or "", tuple(safe_json_list(config["anti_links_whitelist_domains"], str)))
    if not links: return
    try: await message.delete(reason="Anti-links protection")
    except (discord.Forbidden, discord.NotFound, discord.HTTPException): log.warning("Could not delete anti-link message %s", message.id)
    mode=config["anti_links_action"] if config["anti_links_action"] in {"delete", "delete_warn", "delete_log"} else "delete_warn"; key=(message.guild.id,message.channel.id,member.id); now=time.monotonic(); _prune_anti_link_cooldown(now)
    if mode == "delete_log" and config["anti_links_log_channel"]:
        log_channel=message.guild.get_channel(config["anti_links_log_channel"])
        if isinstance(log_channel, discord.TextChannel):
            try:
                safe_domains = discord.utils.escape_markdown(", ".join(links))[:900]
                await log_channel.send(f"🛡️ Deleted external link from {member.mention} in {message.channel.mention}. Domains: `{safe_domains}`", allowed_mentions=discord.AllowedMentions.none())
            except discord.DiscordException: log.warning("Could not write anti-link log for %s", message.id)
    if mode == "delete_warn" and now-_anti_link_warning_cooldown.get(key,0)>_ANTI_LINK_COOLDOWN_SECONDS:
        _anti_link_warning_cooldown[key]=now
        try: await message.channel.send(f"{member.mention}, external links are not allowed here.", delete_after=8, allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.DiscordException: pass
@bot.event
async def on_message(message: discord.Message):
    if not message.author.bot and isinstance(message.channel, discord.TextChannel):
        await _scan_link_message(message)
        row=bot.database.ticket_by_channel(message.channel.id)
        if row: bot.database.mark_activity(row["ticket_id"])
    await bot.process_commands(message)
@bot.event
async def on_message_edit(before, after):
    if after.content != before.content: await _scan_link_message(after)

@bot.event
async def on_member_join(member): await send_welcome(bot,bot.database,member)
@bot.event
async def on_member_remove(member):
    ticket_ids = bot.database.mark_owner_left(member.guild.id, member.id)
    for ticket_id in ticket_ids: bot.database.audit(member.guild.id, ticket_id, 0, 'owner_left')

@bot.tree.error
async def on_app_command_error(i,error):
    original = getattr(error, "original", error)
    if isinstance(original, discord.NotFound) and getattr(original, "code", None) == 10062:
        log.warning("Application interaction expired or is unknown; response could not be delivered")
        return
    log.exception("Application command failed", exc_info=error)
    if isinstance(original, discord.HTTPException) and original.status == 429:
        msg = "⏳ Discord rate-limited this request. Please wait a moment and try again."
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "🔒 You do not have the Discord permissions required for that command. Run `/help` to see access requirements."
    elif isinstance(error, (OwnerConfigurationError, OwnerOnlyError)):
        msg = f"⚠️ {error}"
    elif isinstance(error, app_commands.CheckFailure) and getattr(error, "command", None) and error.command.name == "dashboard":
        msg = "🔒 Dashboard access requires the configured Owner or Co-owner role. The server owner can initialize them with `/setup roles`."
    elif isinstance(error, app_commands.CheckFailure):
        msg = "🔒 You do not have access to this command. Run `/help` to see which roles or permissions are required."
    elif isinstance(original, RuntimeError):
        msg = f"⚠️ {error}"
    else:
        msg = "❌ That command could not be completed. Check its setup, your permissions, and the bot's channel/role permissions; run `/help` for guidance."
    try:
        if i.response.is_done(): await i.followup.send(msg, ephemeral=True)
        else: await i.response.send_message(msg, ephemeral=True)
    except discord.NotFound:
        log.warning("Could not deliver application-command error response; interaction expired")


def run():
    if not settings.token:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it outside Discord.")
    bot.run(settings.token, log_handler=None)

if __name__ == "__main__":
    run()
