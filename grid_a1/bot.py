from __future__ import annotations
import io
import logging
import time
import hashlib
from datetime import timedelta
import discord
import json
from discord import app_commands
from discord.ext import commands, tasks
from .config import Settings, configure_logging, validate_runtime
from .database import SCHEMA_VERSION, Database
from .embeds import embed, support_panel, inactivity_indicator, priority_embed, ticket_stats_embed
from .tickets import ReimburseModal, TicketService, claim
from .giveaways import GiveawayDashboardView, GiveawayEntryView, GiveawayService
from .suggestions import SuggestionModal, SuggestionService, SuggestionVoteView, suggestion_line
from .brand import BRAND_NAME, BRAND_SHORT, RULES_SECTIONS
from .rules import RulesEditModal, RulesService, RulesView, rules_panel_embed
from .guides import build_choices, prefix_guide_lines
from .health import HealthServer
from .polls import (
    DEFAULT_POLL_DURATION_HOURS,
    MAX_POLL_DURATION_HOURS,
    MAX_POLL_QUESTION_LENGTH,
    PollDashboardView,
    PollService,
    PollVoteView,
    parse_poll_options,
    poll_dashboard_embed,
    poll_embed,
)
from .wipes import (
    WipeDashboardView,
    WipeService,
    announce_for_guilds,
    parse_wipe_time,
    wipe_sections,
)
from .stats import (
    collect as collect_ticket_stats,
    moderation_history_lines,
    note_lines,
    queue_overview,
    timestamp_marker,
)
from .utils import PRIORITIES, PRIORITY_LABELS, anti_link_config_updates, is_http_url, is_ticket, parse_ticket_topic, staff_member, detected_external_links, normalize_domain, safe_json_list, utcnow
from .views import ConfirmView, DashboardView, OwnerInactivityView, TicketControls, TicketFeedbackView, TicketPanel, VerifyPanel
from .welcomer import missing, send_welcome, welcome_embed
from .commands import OwnerConfigurationError, OwnerOnlyError, register_commands
settings = Settings.from_env(); configure_logging(settings.log_level)
log = logging.getLogger("spinback-manager")
intents = discord.Intents.default(); intents.members = True; intents.message_content = True
class GridA1Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=settings.prefix, intents=intents, help_command=None)
        self.database = Database(settings.database_path); self.tickets = TicketService(self.database); self.polls = PollService(self.database); self.giveaways = GiveawayService(self.database); self.wipes = WipeService(self.database); self.suggestions = SuggestionService(self.database); self.rules = RulesService(self.database)
        self._global_sync_last_at = 0.0
        self.settings_owner_id = settings.owner_id
        self.started_at = time.time()
        self._global_sync_in_progress = False
        self._stale_guild_commands_cleared = False
        self.health_server = HealthServer(self.health_report)
        register_commands(self)
    def health_report(self) -> dict[str, object]:
        """Small non-sensitive status document for the optional HTTP status endpoint."""
        gateway_ms = round(self.latency * 1_000) if self.latency >= 0 else None
        return {
            "status": "ok",
            "service": "spinback-manager",
            "uptime_seconds": max(0, int(time.time() - self.started_at)),
            "connected_servers": len(self.guilds),
            "gateway_latency_ms": gateway_ms,
            "schema_version": SCHEMA_VERSION,
            "checked_at": utcnow().isoformat(),
        }
    async def close(self) -> None:
        """Stop the optional status endpoint before disconnecting from Discord."""
        self.health_server.stop()
        await super().close()
    async def setup_hook(self):
        log.info("Starting SpinBack Manager: database=%s prefix=%s owner_configured=%s", self.database.path, settings.prefix, bool(settings.owner_id))
        for problem in validate_runtime(settings):
            log.warning("Startup configuration: %s", problem)
        self.database.migrate()
        store = self.database.startup_check()
        if not store["ok"]:
            raise RuntimeError(f"SQLite startup check failed: {store}")
        log.info("SQLite ready: schema=%s WAL=enabled indexes=verified", store["schema_version"])
        # Optional: answer the host's public domain so platform health checks and uptime
        # monitors see a 200. Discord traffic never depends on this listener.
        self.health_server.start()
        self.add_view(TicketPanel(self.tickets))
        self.add_view(TicketControls(self.tickets))
        self.add_view(VerifyPanel(self.database))
        for row in self.database.open_tickets_all():
            self.add_view(OwnerInactivityView(self.tickets, row["ticket_id"]))
        for ticket_id in self.database.feedback_pending():
            self.add_view(TicketFeedbackView(self.tickets, ticket_id))
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
        for giveaway in self.database.open_giveaways_all():
            self.add_view(GiveawayEntryView(self.giveaways, giveaway["giveaway_id"]))
        for row in self.database.published_suggestions_all():
            self.add_view(SuggestionVoteView(self.suggestions, row["suggestion_id"]))
        self.add_view(RulesView(self.rules))
        self.refresh_panels.start()
        self.inactivity_loop.start()
        self.poll_expiration_loop.start()
        self.giveaway_expiration_loop.start()
        self.wipe_announcer.start()
        poll_command = self.tree.get_command("poll")
        if isinstance(poll_command, app_commands.Group):
            poll_subcommands = ", ".join(
                sorted(command.name for command in poll_command.commands)
            )
            log.info("Poll command group ready for sync: /poll %s", poll_subcommands)
        else:
            log.error("Poll command group is missing from the runtime command tree")
        # Publish the global command tree during setup. Guild-specific stale
        # copies are cleared after the gateway is ready, when self.guilds is populated.
        try:
            synced = await self.tree.sync()
            synced_poll = next(
                (command for command in synced if command.name == "poll"),
                None,
            )
            local_poll_subcommands = (
                ", ".join(sorted(command.name for command in poll_command.commands))
                if isinstance(poll_command, app_commands.Group)
                else "missing from local tree"
            )
            log.info(
                "Command startup sync complete: published %s global command(s), poll root=%s, local poll subcommands=%s",
                len(synced),
                "published" if synced_poll is not None else "missing from sync result",
                local_poll_subcommands,
            )
        except discord.HTTPException as error:
            if error.status == 429:
                log.warning("Global command startup sync rate-limited; continuing startup")
            else:
                raise
    async def clear_stale_guild_command_copies(self):
        """Clear old guild-scoped command copies after the gateway populated guilds."""
        if self._stale_guild_commands_cleared:
            return
        retry_needed = False
        for existing_guild in list(self.guilds):
            guild_scope = discord.Object(id=existing_guild.id)
            self.tree.clear_commands(guild=guild_scope)
            try:
                await self.tree.sync(guild=guild_scope)
            except discord.HTTPException as error:
                retry_needed = True
                log.warning(
                    "Stale guild-command cleanup failed for guild %s (HTTP %s); will retry on reconnect",
                    existing_guild.id,
                    getattr(error, "status", "unknown"),
                )
            except Exception:
                retry_needed = True
                log.exception("Stale guild-command cleanup failed for guild %s", existing_guild.id)
        self._stale_guild_commands_cleared = not retry_needed
        if not retry_needed:
            log.info("Stale guild-command cleanup complete for %s guild(s)", len(self.guilds))

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
    async def wipe_announcer(self):
        """Post every wipe that is due whenever the server enabled automatic posting."""
        try:
            posted = await announce_for_guilds(self)
        except Exception:
            log.exception("Wipe announcer pass failed")
            return
        if posted:
            log.info("Posted %s due wipe announcement(s)", posted)

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
                                if owner is not None:
                                    view = OwnerInactivityView(self.tickets, row['ticket_id'])
                                    try:
                                        await owner.send(f'🔴 Your SpinBack ticket **{row["ticket_id"]}** has been inactive for **{duration}**. Please choose an option below within 24 hours.', view=view)
                                    except discord.Forbidden:
                                        log.info('Ticket owner %s has DMs disabled; recording the failure and leaving the ticket open', row['owner_id'])
                                        self.database.mark_inactivity_dm_unavailable(
                                            row['ticket_id'],
                                            now.isoformat(),
                                        )
                                    except discord.HTTPException as error:
                                        log.warning('Could not send inactivity notice to %s (HTTP %s); ticket stays open and status will refresh', row['owner_id'], getattr(error, 'status', 'unknown'))
                                    else:
                                        self.database.update_ticket(row['ticket_id'], inactivity_notice_at=now.isoformat(), auto_close_at=deadline)
                                        self.database.audit(guild.id,row['ticket_id'],0,'inactivity_notice', '{"indicator":"red"}')
                        elif red and row['auto_close_at'] and datetime.fromisoformat(row['auto_close_at']) <= datetime.now(timezone.utc):
                            await self.tickets.close_system(guild, channel, 'Auto-closed after inactivity grace period')
                            continue
                        await self.tickets.refresh_status(channel, row)
                    except Exception: log.exception('Inactivity ticket failed: %s', row['ticket_id'])
            except Exception: log.exception('Inactivity loop failed for guild %s; continuing', getattr(guild, 'id', 'unknown'))

    @tasks.loop(minutes=1)
    async def giveaway_expiration_loop(self):
        """End due giveaways and draw winners once, preserving state across restarts."""
        now = utcnow().isoformat()
        try:
            due_giveaways = self.database.giveaways_due(now)
        except Exception:
            log.exception("Could not load giveaways due for ending; retrying next cycle")
            return
        for giveaway in due_giveaways:
            guild = self.get_guild(giveaway["guild_id"])
            if guild is None:
                continue
            try:
                state, winners, panel_updated = await self.giveaways.finish(
                    guild,
                    giveaway["giveaway_id"],
                    ended_by=0,
                    force=False,
                )
                if state == "ended" and not panel_updated:
                    log.warning("Giveaway %s ended but its panel could not refresh", giveaway["giveaway_id"])
            except Exception:
                log.exception("Giveaway expiration failed for %s", giveaway["giveaway_id"])

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
    @giveaway_expiration_loop.before_loop
    async def before_giveaway_expiration_loop(self): await self.wait_until_ready()
    @poll_expiration_loop.before_loop
    async def before_poll_expiration_loop(self): await self.wait_until_ready()
    @refresh_panels.before_loop
    async def before_refresh_panels(self): await self.wait_until_ready()
bot = GridA1Bot()
setup_group = app_commands.Group(name="setup", description="⚙️ Configure server roles and support systems")
welcomer_group = app_commands.Group(name="welcomer", description="👋 Preview and test welcome messages")
ticket_group = app_commands.Group(name="ticket", description="🎫 Manage and assign support tickets")
poll_group = app_commands.Group(name="poll", description="📊 Create, vote on, and manage server polls")
giveaway_group = app_commands.Group(name="giveaway", description="🎁 Configure and manage timed giveaways")
bot.tree.add_command(setup_group)
bot.tree.add_command(welcomer_group)
bot.tree.add_command(ticket_group)
bot.tree.add_command(poll_group)
bot.tree.add_command(giveaway_group)
def guild(i): return i.guild
def _member_role_ids(member) -> set[int]:
    return {role.id for role in getattr(member, "roles", ())}

def _elder_of(guild, user) -> bool:
    """The server owner and the bot owner always keep control of their own server."""
    if not guild or not user: return False
    return user.id in {guild.owner_id, settings.owner_id}

def _role_access(guild, member, require_staff: bool) -> bool:
    """Staff access comes only from the roles saved by /setup roles."""
    if not guild or not isinstance(member, discord.Member): return False
    if _elder_of(guild, member): return True
    allowed = set(bot.database.configured_admin_role_ids(guild.id) if not require_staff else bot.database.configured_permission_role_ids(guild.id))
    if not allowed: return False
    return bool(_member_role_ids(member) & allowed)

def _privileged(interaction: discord.Interaction, require_staff: bool = False) -> bool:
    return _role_access(interaction.guild, interaction.user, require_staff)

def _prefix_privileged(ctx, require_staff: bool = False) -> bool:
    return _role_access(ctx.guild, ctx.author, require_staff)

def _access_notice(guild) -> str:
    """Explain a refused staff command, naming the setup step when it is missing."""
    if guild is None: return "❌ That command only works inside a server."
    missing = bot.database.missing_permission_roles(guild.id)
    if missing:
        return ("⚠️ This server has not finished staff setup yet. The server owner must run "
                "/setup roles to choose the staff roles, and only those roles can use staff commands.")
    return "🔒 Only members with a configured staff role can use that. Check /help for what each command needs."

class StaffAccessError(app_commands.CheckFailure):
    """Refusal that carries its own reason, so the error handler can explain the setup step."""

def _permission_check(require_staff: bool = False):
    async def predicate(interaction: discord.Interaction) -> bool:
        if _privileged(interaction, require_staff):
            return True
        raise StaffAccessError(_access_notice(interaction.guild))
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
@app_commands.guild_only()
@admin()
@app_commands.describe(
    enabled="Enable or disable protection; omit to keep the current value",
    action="Action to take; omit to keep the current value",
    log_channel="Optional moderation log channel; omit to keep the current value",
    clear_log_channel="Clear the configured moderation log channel",
)
@app_commands.choices(action=[app_commands.Choice(name="🗑️ Delete", value="delete"),app_commands.Choice(name="⚠️ Delete and warn", value="delete_warn"),app_commands.Choice(name="📋 Delete and log", value="delete_log")])
async def anti_links(
    i: discord.Interaction,
    enabled: bool | None = None,
    action: app_commands.Choice[str] | None = None,
    log_channel: discord.TextChannel | None = None,
    clear_log_channel: bool = False,
):
    await i.response.defer(ephemeral=True)
    if not i.guild:
        return await i.followup.send("❌ Use this command inside a server.", ephemeral=True)
    try:
        updates = anti_link_config_updates(
            enabled,
            action.value if action else None,
            log_channel.id if log_channel else None,
            clear_log_channel=clear_log_channel,
        )
    except ValueError as error:
        return await i.followup.send(f"❌ {error}", ephemeral=True)
    if updates:
        bot.database.upsert_config(i.guild.id, **updates)
    config = bot.database.config(i.guild.id)
    active = bool(config and config["anti_links_enabled"])
    mode = config["anti_links_action"] if config else "delete_warn"
    channel_id = config["anti_links_log_channel"] if config else None
    log_text = f"<#{channel_id}>" if channel_id else "not configured"
    await i.followup.send(
        embed=embed(
            "🛡️ Anti-links settings",
            f"Protection: {'enabled' if active else 'disabled'}\nAction: {mode}\nLog channel: {log_text}"
            + ("\n\n✅ Supplied settings were saved; omitted settings were left unchanged." if updates else "\n\nNo changes requested; showing current settings."),
        ),
        ephemeral=True,
    )

@bot.tree.command(name="report", description="🚩 Privately report a server member to staff")
@app_commands.guild_only()
@app_commands.checks.cooldown(3, 60.0, key=lambda i: (i.guild_id, i.user.id))
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

@bot.tree.command(name="modlog", description="🧾 Show a member's recorded moderation history")
@staff()
@app_commands.guild_only()
@app_commands.describe(member="Member whose recorded kick, ban, warn, and timeout history you want")
@app_commands.checks.cooldown(5, 60.0, key=lambda i: (i.guild_id, i.user.id))
async def modlog(i: discord.Interaction, member: discord.Member):
    """Summarise the moderation actions recorded against one member."""
    rows = bot.database.member_history(i.guild.id, member.id)
    result = embed(
        "🧾 Moderation history",
        f"Recorded actions against {member.mention}, newest first.",
    )
    result.add_field(
        name=f"Records ({len(rows)})",
        value="\n".join(moderation_history_lines(rows)) or "Nothing recorded for this member.",
        inline=False,
    )
    result.set_footer(text="Kick, ban, warn, and timeout records only; warnings are recorded, not applied automatically.")
    await i.response.send_message(embed=result, ephemeral=True)
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
    bot.database.audit(i.guild.id, None, i.user.id, "kick", f"member={member.id}; {discord.utils.escape_markdown(reason)[:450]}")
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
    bot.database.audit(i.guild.id, None, i.user.id, "ban", f"member={member.id}; {discord.utils.escape_markdown(reason)[:450]}")
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

@bot.tree.command(name="unban", description="♻️ Lift a ban so the member can rejoin")
@staff()
@app_commands.guild_only()
@app_commands.describe(user_id="Start typing to pick a banned member, or paste their numeric user ID", reason="Why the ban is being lifted")
async def moderation_unban(i: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    if not i.guild: return await i.response.send_message("❌ Unbans only work inside a server.", ephemeral=True)
    try: target_id = int(str(user_id).strip())
    except (TypeError, ValueError): return await i.response.send_message("❌ Pick a banned member from the list, or paste their numeric user ID.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    try: entry = await i.guild.fetch_ban(discord.Object(id=target_id))
    except discord.NotFound: return await i.followup.send("ℹ️ That user is not banned in this server.", ephemeral=True)
    except discord.Forbidden: return await i.followup.send("❌ I cannot read the ban list. Check the Ban Members permission.", ephemeral=True)
    try: await i.guild.unban(entry.user, reason=f"{reason} • Moderator: {i.user}")
    except discord.Forbidden: return await i.followup.send("❌ Discord denied the unban. Check the Ban Members permission and try again.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "unban", f"member={target_id}; {discord.utils.escape_markdown(reason)[:450]}")
    await i.followup.send(embed=embed("💜 Ban lifted", f"♻️ {entry.user} can join the server again.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@moderation_unban.autocomplete("user_id")
async def moderation_unban_autocomplete(i: discord.Interaction, current: str):
    """Offer this server's current bans so nobody has to hunt for a raw user ID."""
    if not i.guild: return []
    needle = (current or "").strip().lower()
    choices: list[app_commands.Choice[str]] = []
    try:
        async for entry in i.guild.bans(limit=100):
            user = entry.user
            label = f"{user} • {user.id}"
            if needle and needle not in label.lower() and needle not in str(user.id): continue
            choices.append(app_commands.Choice(name=label[:100], value=str(user.id)))
            if len(choices) >= 25: break
    except discord.HTTPException:
        log.warning("Could not read the ban list to autocomplete an unban")
        return []
    return choices

@bot.tree.command(name="untimeout", description="🔓 End a member's timeout early")
@staff()
@app_commands.guild_only()
@app_commands.describe(member="Member whose timeout should end now", reason="Why the timeout is being lifted")
async def moderation_untimeout(i: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not i.guild: return await i.response.send_message("❌ Timeouts only work inside a server.", ephemeral=True)
    if member.id == i.user.id or member.id == i.guild.owner_id: return await i.response.send_message("❌ You cannot change your own timeout or the server owner's.", ephemeral=True)
    if not i.guild.me or member.top_role >= i.guild.me.top_role: return await i.response.send_message("❌ My bot role must be above that member.", ephemeral=True)
    if member.timed_out_until is None: return await i.response.send_message("ℹ️ That member is not timed out right now.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    try: await member.timeout(None, reason=f"{reason} • Moderator: {i.user}")
    except discord.Forbidden: return await i.followup.send("❌ Discord denied the change. Check the Moderate Members permission and role hierarchy.", ephemeral=True)
    bot.database.audit(i.guild.id, None, i.user.id, "untimeout", f"member={member.id}; {discord.utils.escape_markdown(reason)[:450]}")
    await i.followup.send(embed=embed("💜 Timeout lifted", f"🔓 {member.mention} can talk again.\n\n**Reason:** {discord.utils.escape_markdown(reason)[:500]}"), ephemeral=True)

@bot.tree.command(name="purge", description="🧹 Bulk-delete recent messages, or empty the channel")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.cooldown(3, 60.0, key=lambda i: (i.guild_id, i.user.id))
@app_commands.describe(
    count="How many recent messages to delete (1–100); ignored when everything is True",
    everything="Set to True to empty the channel instead of using count",
    reason="Why the messages are being removed",
)
async def moderation_purge(i: discord.Interaction, count: app_commands.Range[int, 1, 100], everything: bool = False, reason: str = "No reason provided"):
    if not isinstance(i.channel, discord.TextChannel): return await i.response.send_message("❌ Purge needs a normal text channel.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    async def reply(embed=None, **kwargs): await i.followup.send(embed=embed, ephemeral=True, **kwargs)
    await _report_clear(reply, i.channel, i.user, None if everything else int(count), reason)

@bot.command(name="lock", help="🔒 Prevent members from sending messages in this channel (staff only).")
async def prefix_lock(ctx):
    if not ctx.guild or not _prefix_privileged(ctx, True): return await ctx.send(_access_notice(ctx.guild))
    try: await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Channel locked by {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ I cannot lock this channel. Check Manage Channels and Manage Permissions.")
    await ctx.send("🔒 This channel is now locked for members.")

@bot.command(name="unlock", help="🔓 Restore member messaging in this channel (staff only).")
async def prefix_unlock(ctx):
    if not ctx.guild or not _prefix_privileged(ctx, True): return await ctx.send(_access_notice(ctx.guild))
    try: await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Channel unlocked by {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ I cannot unlock this channel. Check Manage Channels and Manage Permissions.")
    await ctx.send("🔓 This channel is now unlocked for members.")

_CLEAR_BATCH = 100
_CLEAR_SECONDS = 20.0

async def _clear_channel(channel, *, count: int | None, reason: str, seconds: float = _CLEAR_SECONDS) -> tuple[int, bool]:
    """Delete messages in batches. Returns (deleted, stopped_early).

    A count of None empties the channel, stopping when Discord returns a short batch,
    when the time budget runs out, or on the first refusal so the caller can report it.
    """
    deleted = 0
    deadline = time.monotonic() + seconds
    while True:
        want = _CLEAR_BATCH if count is None else min(_CLEAR_BATCH, count - deleted)
        if want <= 0:
            return deleted, False
        batch = await channel.purge(limit=want, reason=reason)
        if not batch:
            return deleted, False
        deleted += len(batch)
        if len(batch) < want:
            return deleted, False
        if time.monotonic() >= deadline:
            return deleted, True

async def _require_staff_prefix(ctx, require_staff: bool = True) -> bool:
    """Gate a prefix command on the roles selected by /setup roles."""
    if _prefix_privileged(ctx, require_staff):
        return True
    await ctx.send(_access_notice(ctx.guild))
    return False

def _hierarchy_error(guild, author, member, me) -> str | None:
    """Shared safety checks for every prefix moderation action."""
    if member.id == author.id: return "❌ You cannot target yourself with a moderation action."
    if member.id == guild.owner_id: return "❌ The server owner cannot be moderated."
    if author.id != guild.owner_id and member.top_role >= author.top_role: return "❌ That member has an equal or higher role than you."
    if me is None or member.top_role >= me.top_role: return "❌ My bot role must be above that member."
    return None

@bot.command(name="help", help="💜 Show the prefix commands and point at the slash guide.")
async def prefix_help(ctx):
    guide = embed(
        f"💜 {BRAND_SHORT} • Prefix commands",
        f"Everything else lives in slash commands — run `/help` for the full guide, or "
        f"`/help command:` for one command in full. This server's prefix is `{settings.prefix}`.",
        discord.Colour.from_rgb(177, 77, 255),
    )
    guide.add_field(name="⌨️ Prefix commands", value="\n".join(prefix_guide_lines())[:1024], inline=False)
    guide.set_footer(text="Staff commands need one of the roles saved by /setup roles.")
    await ctx.send(embed=guide)

@bot.command(name="ping", help="🏓 Check that the bot answers.")
async def prefix_ping(ctx):
    await ctx.send(f"🏓 Pong! Gateway latency {round(bot.latency * 1000)} ms.")

@bot.command(name="whois", help="🔎 Show a member's account age, join date, roles, and records.")
async def prefix_whois(ctx, member: discord.Member):
    if not ctx.guild: return await ctx.send("❌ Member details are only available inside a server.")
    records = bot.database.member_history(ctx.guild.id, member.id)
    detail = embed(f"🔎 {member}", f"{member.mention} • `{member.id}`", discord.Colour.from_rgb(177, 77, 255))
    detail.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
    detail.add_field(name="Joined here", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "unknown", inline=True)
    detail.add_field(name="Top role", value=getattr(member.top_role, "mention", "none"), inline=True)
    detail.add_field(name="Roles", value=str(max(0, len(member.roles) - 1)), inline=True)
    detail.add_field(name="Timed out", value=discord.utils.format_dt(member.timed_out_until, "R") if member.timed_out_until else "no", inline=True)
    detail.add_field(name="Recorded actions", value=str(len(records)), inline=True)
    if records: detail.add_field(name="Latest record", value=moderation_history_lines(records[:1])[0][:1024], inline=False)
    await ctx.send(embed=detail)

@bot.command(name="serverinfo", help="🌐 Show this server's size, age, and staff setup state.")
async def prefix_serverinfo(ctx):
    if not ctx.guild: return await ctx.send("❌ Server details are only available inside a server.")
    guild = ctx.guild
    missing = bot.database.missing_permission_roles(guild.id)
    detail = embed(f"🌐 {guild.name}", (guild.description or "No description set.")[:300], discord.Colour.from_rgb(177, 77, 255))
    detail.add_field(name="Members", value=str(guild.member_count), inline=True)
    detail.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
    detail.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "R"), inline=True)
    detail.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
    detail.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
    detail.add_field(name="Open tickets", value=str(len(bot.database.open_tickets(guild.id))), inline=True)
    detail.add_field(
        name="Staff setup",
        value="✅ All five staff roles are configured." if not missing else f"⚠️ Missing: {', '.join(missing)} — the server owner should run /setup roles.",
        inline=False,
    )
    await ctx.send(embed=detail)

@bot.command(name="warn", help="⚠️ Record a moderation warning (staff only).")
async def prefix_warn(ctx, member: discord.Member, *, reason: str):
    if not await _require_staff_prefix(ctx): return
    problem = _hierarchy_error(ctx.guild, ctx.author, member, ctx.guild.me)
    if problem and "bot role" not in problem: return await ctx.send(problem)
    body = discord.utils.escape_markdown(reason)[:450]
    bot.database.audit(ctx.guild.id, ctx.channel.id, ctx.author.id, "warn", f"member={member.id}; {body}")
    await ctx.send(embed=embed("💜 Warning recorded", f"⚠️ A warning was recorded for {member.mention}.\n\n**Reason:** {body}"))

@bot.command(name="timeout", help="⏳ Time a member out for 1 minute to 28 days (staff only).")
async def prefix_timeout(ctx, member: discord.Member, minutes: int, *, reason: str = "No reason provided"):
    if not await _require_staff_prefix(ctx): return
    problem = _hierarchy_error(ctx.guild, ctx.author, member, ctx.guild.me)
    if problem: return await ctx.send(problem)
    if not 1 <= minutes <= 40320: return await ctx.send("❌ Timeout length must be between 1 and 40320 minutes (28 days).")
    body = discord.utils.escape_markdown(reason)[:450]
    try: await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=f"{reason} • Moderator: {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ Discord denied the timeout. Check Moderate Members and role hierarchy.")
    bot.database.audit(ctx.guild.id, ctx.channel.id, ctx.author.id, "timeout", f"member={member.id}; minutes={minutes}; {body}")
    await ctx.send(embed=embed("💜 Member timed out", f"⏳ {member.mention} was timed out for **{minutes} minute(s)**.\n\n**Reason:** {body}"))

@bot.command(name="untimeout", help="🔓 End a member's timeout early (staff only).")
async def prefix_untimeout(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await _require_staff_prefix(ctx): return
    if member.timed_out_until is None: return await ctx.send("ℹ️ That member is not timed out right now.")
    if member.id == ctx.author.id or member.id == ctx.guild.owner_id: return await ctx.send("❌ You cannot change your own timeout or the server owner's.")
    try: await member.timeout(None, reason=f"{reason} • Moderator: {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ Discord denied the change. Check Moderate Members and role hierarchy.")
    body = discord.utils.escape_markdown(reason)[:450]
    bot.database.audit(ctx.guild.id, ctx.channel.id, ctx.author.id, "untimeout", f"member={member.id}; {body}")
    await ctx.send(embed=embed("💜 Timeout lifted", f"🔓 {member.mention} can talk again.\n\n**Reason:** {body}"))

@bot.command(name="kick", help="👢 Remove a member with a recorded reason (staff only).")
async def prefix_kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await _require_staff_prefix(ctx): return
    problem = _hierarchy_error(ctx.guild, ctx.author, member, ctx.guild.me)
    if problem: return await ctx.send(problem)
    body = discord.utils.escape_markdown(reason)[:450]
    try: await member.kick(reason=f"{reason} • Moderator: {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ Discord denied the kick. Check Kick Members and role hierarchy.")
    bot.database.audit(ctx.guild.id, ctx.channel.id, ctx.author.id, "kick", f"member={member.id}; {body}")
    await ctx.send(embed=embed("💜 Member kicked", f"✅ {member.mention} was removed from the server.\n\n**Reason:** {body}"))

@bot.command(name="ban", help="🔨 Ban a member with a recorded reason (staff only).")
async def prefix_ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await _require_staff_prefix(ctx): return
    problem = _hierarchy_error(ctx.guild, ctx.author, member, ctx.guild.me)
    if problem: return await ctx.send(problem)
    body = discord.utils.escape_markdown(reason)[:450]
    try: await member.ban(reason=f"{reason} • Moderator: {ctx.author}", delete_message_seconds=0)
    except discord.Forbidden: return await ctx.send("❌ Discord denied the ban. Check Ban Members and role hierarchy.")
    bot.database.audit(ctx.guild.id, ctx.channel.id, ctx.author.id, "ban", f"member={member.id}; {body}")
    await ctx.send(embed=embed("💜 Member banned", f"🔨 {member.mention} was banned from the server.\n\n**Reason:** {body}"))

@bot.command(name="unban", help="♻️ Lift a ban by user ID (staff only).")
async def prefix_unban(ctx, user_id: int, *, reason: str = "No reason provided"):
    if not await _require_staff_prefix(ctx): return
    try: entry = await ctx.guild.fetch_ban(discord.Object(id=int(user_id)))
    except discord.NotFound: return await ctx.send("ℹ️ That user is not banned in this server.")
    except discord.Forbidden: return await ctx.send("❌ I cannot read the ban list. Check the Ban Members permission.")
    try: await ctx.guild.unban(entry.user, reason=f"{reason} • Moderator: {ctx.author}")
    except discord.Forbidden: return await ctx.send("❌ Discord denied the unban. Check the Ban Members permission.")
    body = discord.utils.escape_markdown(reason)[:450]
    bot.database.audit(ctx.guild.id, ctx.channel.id, ctx.author.id, "unban", f"member={entry.user.id}; {body}")
    await ctx.send(embed=embed("💜 Ban lifted", f"♻️ {entry.user} can join the server again.\n\n**Reason:** {body}"))

@bot.command(name="modlog", help="🧾 Show a member's recorded moderation history (staff only).")
async def prefix_modlog(ctx, member: discord.Member):
    if not await _require_staff_prefix(ctx): return
    rows = bot.database.member_history(ctx.guild.id, member.id)
    detail = embed("🧾 Moderation history", f"Recorded actions against {member.mention}, newest first.")
    detail.add_field(name=f"Records ({len(rows)})", value="\n".join(moderation_history_lines(rows)) or "Nothing recorded for this member.", inline=False)
    await ctx.send(embed=detail)

@bot.command(name="stats", help="📊 Show ticket workload and response times (staff only).")
@commands.cooldown(3, 60.0, commands.BucketType.user)
async def prefix_stats(ctx):
    if not await _require_staff_prefix(ctx): return
    await ctx.send(embed=ticket_stats_embed(ctx.guild, collect_ticket_stats(bot.database, ctx.guild.id)))

async def _report_clear(respond, channel, actor, count: int | None, reason: str) -> None:
    """Run a clear and reply with exactly what happened."""
    try:
        deleted, stopped = await _clear_channel(channel, count=count, reason=f"{reason} • Moderator: {actor}")
    except discord.Forbidden:
        return await respond("❌ I need the Manage Messages permission in this channel to delete anything.")
    except discord.HTTPException:
        log.exception("Bulk delete failed in channel %s", channel.id)
        return await respond("❌ Discord rejected that delete. Try again in a moment, or clear a smaller number.")
    bot.database.audit(
        channel.guild.id,
        channel.id,
        actor.id,
        "purge",
        f"channel={channel.id}; deleted={deleted}; {'everything' if count is None else f'limit={count}'}; {discord.utils.escape_markdown(reason)[:360]}",
    )
    if count is None:
        summary = f"Deleted **{deleted}** message(s) in {channel.mention} — the channel is clear." if not stopped else (
            f"Deleted **{deleted}** message(s) in {channel.mention} and stopped after {int(_CLEAR_SECONDS)} seconds "
            "to stay responsive. Run the command again to continue."
        )
    else:
        summary = f"Deleted **{deleted}** message(s) in {channel.mention}."
    result = embed("🧹 Messages removed", summary)
    result.set_footer(text="Messages older than 14 days cannot be bulk-deleted, so those are removed one at a time.")
    await respond(embed=result)

@bot.command(name="clear", help="🧹 Delete the newest messages, or the whole channel when you give no count (staff only).")
@commands.cooldown(2, 60.0, commands.BucketType.user)
async def prefix_clear(ctx, count: int | None = None, *, reason: str = "No reason provided"):
    if not await _require_staff_prefix(ctx): return
    if not isinstance(ctx.channel, discord.TextChannel): return await ctx.send("❌ Clear needs a normal text channel.")
    if count is not None and not 1 <= count <= 10000:
        return await ctx.send("❌ Give a count between 1 and 10000, or leave the count out to empty the channel completely.")
    if count is not None:
        return await _report_clear(ctx.send, ctx.channel, ctx.author, count, reason)
    prompt = embed(
        "🧹 Clear this channel?",
        f"This deletes **every** message in {ctx.channel.mention}, including this prompt. Discord cannot bulk-delete "
        f"messages older than 14 days, so those are removed one at a time and the job stops after about "
        f"{int(_CLEAR_SECONDS)} seconds, reporting what is left.\n\nThis cannot be undone.",
    )
    prompt.set_footer(text="Only you can answer this prompt, and it expires after 90 seconds.")
    async def confirmed(interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=embed("🧹 Clearing…", f"Deleting every message in {ctx.channel.mention}."), view=None
        )
        async def reply(embed=None, **kwargs):
            await interaction.followup.send(embed=embed, ephemeral=True, **kwargs)
        await _report_clear(reply, ctx.channel, ctx.author, None, reason)
    view = ConfirmView(confirmed, author_id=ctx.author.id, confirm_label="Delete everything")
    view.message = await ctx.send(embed=prompt, view=view)

@bot.command(name="slowmode", help="🐌 Set this channel's slowmode in seconds (staff only, 0 disables).")
async def prefix_slowmode(ctx, seconds: int, *, reason: str = "No reason provided"):
    if not await _require_staff_prefix(ctx): return
    if not isinstance(ctx.channel, discord.TextChannel): return await ctx.send("❌ Slowmode needs a normal text channel.")
    if not 0 <= seconds <= 21600: return await ctx.send("❌ Slowmode must be between 0 and 21600 seconds (6 hours). Use 0 to turn it off.")
    try: await ctx.channel.edit(slowmode_delay=seconds, reason=f"{reason} • Moderator: {ctx.author}")
    except (discord.Forbidden, discord.HTTPException): return await ctx.send("❌ I cannot change this channel's slowmode. Check Manage Channels.")
    bot.database.audit(ctx.guild.id, ctx.channel.id, ctx.author.id, "slowmode", f"seconds={seconds}; {discord.utils.escape_markdown(reason)[:400]}")
    state = "turned off" if seconds == 0 else f"set to **{seconds}** second(s)"
    await ctx.send(embed=embed("🐌 Slowmode updated", f"Slowmode in {ctx.channel.mention} is now {state}."))


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
    e = embed(f"💜 {BRAND_SHORT} • Permission roles saved", "✅ Staff access roles are now configured for this server.", discord.Colour.from_rgb(177, 77, 255))
    e.add_field(name="👑 Owner", value=owner_role.mention, inline=True)
    e.add_field(name="🤝 Co-owner", value=co_owner_role.mention, inline=True)
    e.add_field(name="🛡️ Head admin", value=head_admin_role.mention, inline=True)
    e.add_field(name="⚙️ Admin", value=admin_role.mention, inline=True)
    e.add_field(name="🔨 Moderator", value=moderator_role.mention, inline=True)
    e.set_footer(text=f"{BRAND_SHORT} • Permission configuration • Changes saved to SQLite")
    await i.followup.send(embed=e, ephemeral=True)

@setup_group.command(name="status", description="🩺 Check which parts of this server's setup are finished")
@app_commands.guild_only()
@admin()
async def setup_status(interaction: discord.Interaction) -> None:
    """Report the saved setup so a new server knows exactly what to run next."""
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Setup only applies inside a server.", ephemeral=True)
    config = bot.database.config(guild.id)
    labels = ("Owner", "Co-owner", "Head admin", "Admin", "Moderator")
    role_lines = []
    for field, label in zip(bot.database.PERMISSION_ROLE_FIELDS, labels):
        role_id = config[field] if config else None
        role_lines.append(f"{'✅' if role_id else '❌'} {label}: " + (f"<@&{int(role_id)}>" if role_id else "not set"))
    def mark(name: str, ok: bool) -> str:
        return f"{'✅' if ok else '❌'} {name}"
    places = [
        mark("Moderation log channel", bool(config and config["logs_channel"])),
        mark("Report channel", bool(config and config["report_channel"])),
        mark("Ticket category", bool(config and config["ticket_category"])),
        mark("Welcome channel", bool(config and config["welcome_channel"])),
        mark("Staff dashboard panel", bool(config and config["panel_channel"])),
        mark("Anti-links protection", bool(config and config["anti_links_enabled"])),
    ]
    missing = bot.database.missing_permission_roles(guild.id)
    if missing:
        nxt = ("The server owner must run /setup roles and pick all five staff roles — until then no staff "
               "command works for anyone but the server owner.")
    else:
        nxt = "All staff roles are saved. Staff commands now answer only members holding one of them."
    result = embed(
        f"🩺 Setup status — {guild.name}",
        "Everything the bot needs is configured with slash commands, and staff access comes from the roles below.",
    )
    result.add_field(name="Staff roles", value="\n".join(role_lines), inline=False)
    result.add_field(name="Places", value="\n".join(places), inline=False)
    result.add_field(name="Next step", value=nxt, inline=False)
    result.set_footer(text="Run /setup roles, /setup tickets, or /setup welcomer to fill in what is missing.")
    await interaction.response.send_message(embed=result, ephemeral=True)

@bot.tree.command(name="verifypanel", description="✅ Publish a verification panel and assignable role")
@app_commands.guild_only()
@admin()
@app_commands.describe(channel="Text channel where the public panel will appear", role="Role granted after successful verification")
async def verifypanel(i: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if role.is_default(): return await i.response.send_message("❌ You cannot use @everyone as the verification role.", ephemeral=True)
    if not i.guild.me or role >= i.guild.me.top_role: return await i.response.send_message(f"❌ Move {BRAND_SHORT}'s bot role above the verification role first.", ephemeral=True)
    panel = embed(f"💜 {BRAND_SHORT} • Secure Verification", "✨ Complete the short verification check to unlock the server.\n\n📜 Rules confirmation\n✅ Verified role for eligible members\n\nDiscord-wide moderation history is private and unavailable to bots.", discord.Colour.from_rgb(177, 77, 255))
    panel.add_field(name="🔐 Verification steps", value="1️⃣ Start verification\n2️⃣ Review your result\n3️⃣ Confirm the rules\n4️⃣ Receive access", inline=False)
    panel.add_field(name="💬 Need help?", value="If you need help, please open a support ticket.", inline=False)
    panel.set_footer(text=f"{BRAND_SHORT} • Secure, fair, Discord-only verification")
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

wipe_group = app_commands.Group(name="wipe", description="🌍 Configure wipe announcements and the wipe schedule")
bot.tree.add_command(wipe_group)

def _wipe_embed(title: str, rows: list, empty: str):
    """Shared listing embed for the wipe commands and the !wipe prefix command."""
    upcoming, past, hidden = wipe_sections(rows)
    result = embed(title, f"**{len(rows)}** wipe{'s' if len(rows) != 1 else ''} on file. Times below follow each member's own timezone.")
    result.add_field(name="Next wipes", value="\n".join(upcoming) or empty, inline=False)
    if past:
        result.add_field(name="Already wiped", value="\n".join(past), inline=False)
    if hidden:
        result.set_footer(text=f"…and {hidden} more not shown. Raise the limit to see them.")
    return result

@wipe_group.command(name="config", description="🎛️ Open the wipe panel: channel, ping role, automatic posting, and the schedule")
@app_commands.guild_only()
@admin()
async def wipe_config(i: discord.Interaction):
    if not i.guild: return await i.response.send_message("❌ The wipe panel only works inside a server.", ephemeral=True)
    view = WipeDashboardView(bot.wipes, i.guild.id, i.user.id, i.guild)
    await i.response.send_message(embed=view.panel(), view=view, ephemeral=True)

@wipe_group.command(name="add", description="➕ Schedule a wipe")
@app_commands.guild_only()
@admin()
@app_commands.describe(
    region="Region code such as EU or NA",
    when="When the wipe happens: +3h, 2026-12-01 18:00 (UTC), or a Unix timestamp",
    title="What this wipe is, for example Weekly wipe",
)
async def wipe_add(i: discord.Interaction, region: str, when: str, title: str):
    if not i.guild: return await i.response.send_message("❌ Wipes can only be scheduled inside a server.", ephemeral=True)
    try: row = bot.wipes.schedule(i.guild.id, region=region, title=title, when_text=when, actor_id=i.user.id)
    except ValueError as error: return await i.response.send_message(f"❌ {error}", ephemeral=True)
    moment = parse_wipe_time(row["scheduled_at"])
    stamp = f"<t:{int(moment.timestamp())}:F>" if moment else "soon"
    result = embed("🌍 Wipe scheduled", f"**{row['title']}** for {row['region']} at {stamp} ({row['wipe_id']}).\n\nPost it with /wipe announce, or let automatic posting handle it.")
    result.set_footer(text="Members can read the schedule with /wipe list or !wipe.")
    await i.response.send_message(embed=result, ephemeral=True)

@wipe_group.command(name="list", description="📋 Show every wipe this server has scheduled")
@app_commands.guild_only()
@app_commands.describe(limit="How many wipes to show (1–50)")
async def wipe_list(i: discord.Interaction, limit: app_commands.Range[int, 1, 50] = 15):
    if not i.guild: return await i.response.send_message("❌ Wipe schedules only exist inside a server.", ephemeral=True)
    rows = bot.wipes.listing(i.guild.id, limit=int(limit))
    await i.response.send_message(embed=_wipe_embed(f"🌍 Wipe schedule — {i.guild.name}", rows, "Nothing is scheduled yet. Staff can add one with /wipe add."), ephemeral=True)

@wipe_group.command(name="remove", description="🗑️ Delete a scheduled wipe")
@app_commands.guild_only()
@admin()
@app_commands.describe(wipe_id="Start typing to pick a scheduled wipe, or paste its ID")
async def wipe_remove(i: discord.Interaction, wipe_id: str):
    if not i.guild: return await i.response.send_message("❌ Wipes can only be managed inside a server.", ephemeral=True)
    row = bot.database.wipe(i.guild.id, wipe_id)
    if not row: return await i.response.send_message("⚠️ I could not find that wipe in this server. Pick one from the autocomplete list.", ephemeral=True)
    async def confirmed(interaction: discord.Interaction) -> None:
        removed = bot.wipes.cancel(i.guild.id, row["wipe_id"], interaction.user.id)
        if not removed: return await interaction.response.edit_message(embed=embed("⚠️ Already gone", "That wipe was removed before this button was pressed."), view=None)
        await interaction.response.edit_message(embed=embed("🗑️ Wipe removed", f"**{removed['title']}** ({removed['wipe_id']}) is no longer scheduled."), view=None)
    prompt = embed("🗑️ Delete this wipe?", f"**{row['title']}** for {row['region']}, scheduled {timestamp_marker(row['scheduled_at'])} ({row['wipe_id']}).\n\nThis cannot be undone.")
    prompt.set_footer(text="Only you can answer this prompt, and it expires after 90 seconds.")
    view = ConfirmView(confirmed, author_id=i.user.id, confirm_label="Delete this wipe")
    await i.response.send_message(embed=prompt, view=view, ephemeral=True)
    view.message = await i.original_response()

@wipe_remove.autocomplete("wipe_id")
async def wipe_remove_autocomplete(i: discord.Interaction, current: str):
    if not i.guild: return []
    return [
        app_commands.Choice(name=label, value=value)
        for label, value in build_choices(
            bot.database.wipes(i.guild.id, limit=25),
            ("region", "title", "scheduled_at"),
            "wipe_id",
            current,
        )
    ]

@wipe_group.command(name="announce", description="📣 Post a wipe now")
@app_commands.guild_only()
@admin()
@app_commands.describe(wipe_id="Start typing to pick a wipe, or paste its ID")
async def wipe_announce(i: discord.Interaction, wipe_id: str):
    if not i.guild: return await i.response.send_message("❌ Wipes can only be announced inside a server.", ephemeral=True)
    row = bot.database.wipe(i.guild.id, wipe_id)
    if not row: return await i.response.send_message("⚠️ I could not find that wipe in this server. Pick one from the autocomplete list.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    result = await bot.wipes.announce_one(i.guild, row, actor_id=i.user.id)
    await i.followup.send(result["message"], ephemeral=True)

@wipe_announce.autocomplete("wipe_id")
async def wipe_announce_autocomplete(i: discord.Interaction, current: str):
    if not i.guild: return []
    return [
        app_commands.Choice(name=label, value=value)
        for label, value in build_choices(
            bot.database.wipes(i.guild.id, limit=25),
            ("region", "title", "scheduled_at"),
            "wipe_id",
            current,
        )
    ]

@bot.command(name="wipe", help="🌍 Show every wipe this server has scheduled.")
async def prefix_wipe(ctx, limit: int = 15):
    if not ctx.guild: return await ctx.send("❌ Wipe schedules only exist inside a server.")
    count = max(1, min(int(limit), 50))
    rows = bot.wipes.listing(ctx.guild.id, limit=count)
    await ctx.send(embed=_wipe_embed(f"🌍 Wipe schedule — {ctx.guild.name}", rows, "Nothing is scheduled yet. Staff can add one with /wipe add."))

info_group = app_commands.Group(name="info", description="🌐 View server information")
bot.tree.add_command(info_group)

@info_group.command(name="server", description="🏰 View server members, channels, roles, and settings")
@app_commands.checks.cooldown(5, 60.0, key=lambda i: (i.guild_id, i.user.id))
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
    e.set_footer(text=f"{BRAND_SHORT} • Server information")
    await i.response.send_message(embed=e)

roles_group = app_commands.Group(name="roles", description="🎭 Display and publish the server role directory")
bot.tree.add_command(roles_group)
@roles_group.command(name="setchannel", description="📋 Post the server role directory in a text channel")
@app_commands.guild_only()
@admin()
@app_commands.describe(channel="Text channel where the role directory should be posted")
async def roles_setchannel(i: discord.Interaction, channel: discord.TextChannel):
    roles = [role for role in i.guild.roles if not role.is_default()]
    roles.sort(key=lambda role: role.position, reverse=True)
    lines = [f"{role.mention} — {len(role.members)} members" for role in roles]
    description = "\n".join(lines)[:4000] if lines else "No custom roles found."
    e = embed(f"💜 {BRAND_SHORT} • Server role directory", "✨ All custom server roles, arranged from highest to lowest.\n\n" + description, discord.Colour.from_rgb(177, 77, 255))
    e.set_footer(text=f"{len(roles)} custom roles • {BRAND_NAME}")
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
    await i.followup.send(f"✅ {BRAND_SHORT} support panel updated in {panel_channel.mention}; logs go to {logs_channel.mention}.", ephemeral=True)

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
@ticket_group.command(name="priority", description="🚦 Set how urgent this ticket is")
@staff()
@app_commands.describe(priority="Priority this ticket should be worked at")
@app_commands.choices(priority=[app_commands.Choice(name=PRIORITY_LABELS[value], value=value) for value in PRIORITIES])
async def ticket_priority(i: discord.Interaction, priority: app_commands.Choice[str]):
    if not isinstance(i.channel, discord.TextChannel) or not is_ticket(i.channel): return await i.response.send_message("❌ This command only works inside an active ticket.", ephemeral=True)
    row = bot.database.ticket_by_channel(i.channel.id)
    if not row: return await i.response.send_message("⚠️ This ticket is already closed or unavailable.", ephemeral=True)
    if row["priority"] == priority.value: return await i.response.send_message(f"ℹ️ This ticket is already **{PRIORITY_LABELS[priority.value]}**.", ephemeral=True)
    if not bot.database.set_priority(row["ticket_id"], priority.value, i.user.id): return await i.response.send_message("⚠️ This ticket closed while the priority was being set.", ephemeral=True)
    await i.response.send_message(embed=priority_embed(priority.value, i.user))
    if await bot.tickets.sync_channel_name(i.channel, row, priority.value):
        await i.followup.send("🏷️ Renamed the channel to match its new priority.", ephemeral=True)
@ticket_group.command(name="list", description="🗂️ List this server's open tickets, most urgent first")
@staff()
@app_commands.checks.cooldown(5, 60.0, key=lambda i: (i.guild_id, i.user.id))
async def ticket_list(i: discord.Interaction):
    if not i.guild:
        return await i.response.send_message("❌ Open the ticket queue inside a server.", ephemeral=True)
    rows = bot.database.open_tickets(i.guild.id)
    lines, hidden = queue_overview(rows)
    result = embed(
        f"🗂️ Open ticket queue — {i.guild.name}",
        f"**{len(rows)}** open ticket{'s' if len(rows) != 1 else ''}, most urgent first, oldest at the top of each priority.",
    )
    result.add_field(
        name="Tickets",
        value="\n".join(lines) or "Nothing is open right now. 🎉",
        inline=False,
    )
    if hidden:
        result.set_footer(text=f"…and {hidden} more not shown. Clear the oldest tickets first.")
    await i.response.send_message(embed=result, ephemeral=True)
@ticket_group.command(name="note", description="🗒️ Save an internal staff note on this ticket")
@staff()
@app_commands.describe(text="Note only staff can read; the ticket owner never sees it")
async def ticket_note(i: discord.Interaction, text: str):
    if not isinstance(i.channel, discord.TextChannel) or not is_ticket(i.channel):
        return await i.response.send_message("❌ This command only works inside an active ticket.", ephemeral=True)
    row = bot.database.ticket_by_channel(i.channel.id)
    if not row:
        return await i.response.send_message("⚠️ This ticket is already closed or unavailable.", ephemeral=True)
    try:
        stored = bot.database.add_ticket_note(row["ticket_id"], i.guild.id, i.user.id, text)
    except ValueError as error:
        return await i.response.send_message(f"❌ {error}", ephemeral=True)
    await i.response.send_message(
        embed=embed("🗒️ Note saved", f"Internal note recorded on this ticket:\n> {stored}\n\nUse /ticket notes to read the history."),
        ephemeral=True,
    )
@ticket_group.command(name="notes", description="🗂️ Read the internal staff notes on this ticket")
@staff()
async def ticket_notes(i: discord.Interaction):
    if not isinstance(i.channel, discord.TextChannel) or not is_ticket(i.channel):
        return await i.response.send_message("❌ This command only works inside an active ticket.", ephemeral=True)
    row = bot.database.ticket_by_channel(i.channel.id)
    if not row:
        return await i.response.send_message("⚠️ This ticket is already closed or unavailable.", ephemeral=True)
    lines = note_lines(bot.database.ticket_notes(i.guild.id, row["ticket_id"]))
    result = embed("🗂️ Internal ticket notes", f"Staff-only notes on {i.channel.mention}, newest first.")
    result.add_field(name="Notes", value="\n".join(lines) or "No notes yet. Add one with /ticket note.", inline=False)
    await i.response.send_message(embed=result, ephemeral=True)
@ticket_group.command(name="stats", description="📊 Show ticket workload, response times, and top claimers")
@staff()
@app_commands.checks.cooldown(5, 60.0, key=lambda i: (i.guild_id, i.user.id))
async def ticket_stats(i: discord.Interaction):
    g = guild(i)
    if g is None: return await i.response.send_message("❌ Statistics are only available inside a server.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    await i.followup.send(embed=ticket_stats_embed(g, collect_ticket_stats(bot.database, g.id)), ephemeral=True)
@ticket_group.command(name="transcript", description="📄 Get a plain-text transcript without closing the ticket")
@staff()
@app_commands.checks.cooldown(3, 60.0, key=lambda i: (i.guild_id, i.user.id))
async def ticket_transcript(i: discord.Interaction):
    if not isinstance(i.channel, discord.TextChannel) or not is_ticket(i.channel): return await i.response.send_message("❌ This command only works inside an active ticket.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    try: text = await bot.tickets.transcript(i.channel)
    except discord.DiscordException:
        log.warning("Could not render an on-demand transcript for channel %s", i.channel.id)
        return await i.followup.send("❌ I could not read this channel's history. Please retry.", ephemeral=True)
    await i.followup.send(f"📄 Transcript for {i.channel.mention}. The ticket stays open.", file=discord.File(io.BytesIO(text.encode("utf-8")), filename=f"{i.channel.name}-transcript.txt"), ephemeral=True)
@giveaway_group.command(
    name="list",
    description="📋 List this server's active giveaways with entry counts",
)
@app_commands.guild_only()
@admin()
@app_commands.describe(limit="How many giveaways to show (1–25)")
async def giveaway_list(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 1, 25] = 10,
) -> None:
    """Show open giveaways ending soonest first, with entries and winner counts."""
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ Giveaways are only available inside a server.",
            ephemeral=True,
        )
    rows = sorted(
        bot.database.active_giveaways(guild.id, limit=int(limit)),
        key=lambda row: row["ends_at"] or "",
    )
    shown: list[str] = []
    used = 0
    for row in rows:
        entries = bot.database.giveaway_entry_count(row["giveaway_id"])
        ping = f"pings <@&{row['ping_role_id']}>" if row["ping_role_id"] else "no ping"
        line = (
            f"🎁 **{discord.utils.escape_markdown(row['reward_type'])[:80]}** • `{row['giveaway_id']}`\n"
            f"┗ {entries} {'entry' if entries == 1 else 'entries'} • "
            f"{int(row['winner_count'])} winner(s) • {timestamp_marker(row['ends_at'])} • "
            f"{ping} • <#{row['channel_id']}>"
        )
        if used + len(line) + 1 > 1000:
            break
        shown.append(line)
        used += len(line) + 1
    listing = embed(
        f"📋 Active giveaways — {guild.name}",
        f"**{len(rows)}** open giveaway(s), ending soonest first.",
    )
    listing.add_field(
        name="Giveaways",
        value="\n".join(shown) or "Nothing is open right now. Start one with /giveaway config.",
        inline=False,
    )
    if len(shown) < len(rows):
        listing.set_footer(text=f"…and {len(rows) - len(shown)} more not shown. End the soonest ones first.")
    await interaction.response.send_message(embed=listing, ephemeral=True)


@giveaway_group.command(
    name="config",
    description="🎛️ Open the private giveaway setup and active list",
)
@app_commands.guild_only()
@admin()
async def giveaway_config(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Giveaways are only available inside a server.", ephemeral=True)
    active = bot.database.active_giveaways(guild.id)
    dashboard = discord.Embed(
        title=f"🎁 Giveaway Dashboard — {guild.name}",
        description="Create a giveaway, optionally ping a role, set the free-text reward and winner count, and draw winners automatically after its timer expires.",
        colour=discord.Colour.gold(),
    )
    dashboard.add_field(name="Active giveaways", value=str(len(active)), inline=True)
    dashboard.add_field(name="Default duration", value="24 hours", inline=True)
    view = GiveawayDashboardView(bot.giveaways, guild.id, interaction.user.id)
    await interaction.response.send_message(embed=dashboard, view=view, ephemeral=True)


@giveaway_group.command(
    name="end",
    description="🎉 End an active giveaway and draw its winners now",
)
@app_commands.guild_only()
@admin()
@app_commands.describe(giveaway_id="Start typing to pick an active giveaway, or paste its ID")
async def giveaway_end(interaction: discord.Interaction, giveaway_id: str) -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Giveaways can only be managed inside a server.", ephemeral=True)
    normalized_id = giveaway_id.strip().upper()
    if not bot.database.giveaway_for_guild(guild.id, normalized_id):
        return await interaction.response.send_message("⚠️ I could not find that giveaway in this server.", ephemeral=True)
    entries = bot.database.giveaway_entry_count(normalized_id)

    async def draw_winners(target: discord.Interaction) -> None:
        await target.response.defer(ephemeral=True)
        try:
            state, winners, panel_updated = await bot.giveaways.finish(
                guild,
                normalized_id,
                ended_by=interaction.user.id,
                force=True,
            )
        except Exception:
            log.exception("Manual giveaway end failed for %s", normalized_id)
            return await target.followup.send("❌ I could not end that giveaway. Check its public panel and try again.", ephemeral=True)
        if state == "already_ended":
            return await target.followup.send("ℹ️ That giveaway has already ended.", ephemeral=True)
        if state != "ended":
            return await target.followup.send("⚠️ That giveaway is no longer active.", ephemeral=True)
        winner_text = ", ".join(f"<@{user_id}>" for user_id in winners) if winners else "No one entered."
        message = f"✅ Giveaway `{normalized_id}` ended. Winners: {winner_text}"
        if not panel_updated:
            message += " The winner draw was saved, but I could not refresh the public panel."
        await target.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    view = ConfirmView(draw_winners, author_id=interaction.user.id, confirm_label="Draw winners now")
    await interaction.response.send_message(
        embed=embed(
            "🎉 End this giveaway now?",
            f"Giveaway `{normalized_id}` currently has **{entries}** entr{'y' if entries == 1 else 'ies'}.\n"
            "Ending it draws the winners straight away and cannot be undone.",
        ),
        view=view,
        ephemeral=True,
    )
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        log.debug("Could not capture the giveaway confirmation prompt for its timeout message")


def _may_manage_poll(interaction: discord.Interaction, poll) -> bool:
    """Allow a poll creator or server-level manager to control that poll."""
    return bool(
        interaction.guild
        and (
            interaction.user.id == poll["creator_id"]
            or _privileged(interaction)
        )
    )


@giveaway_end.autocomplete("giveaway_id")
async def giveaway_end_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Offer this server's active giveaways instead of a raw identifier."""
    if interaction.guild_id is None:
        return []
    rows = bot.database.active_giveaways(interaction.guild_id)
    return [
        app_commands.Choice(name=label, value=value)
        for label, value in build_choices(
            rows,
            ("reward_type", "giveaway_id"),
            "giveaway_id",
            current,
        )
    ]


@poll_group.command(
    name="config",
    description="🎛️ Open the private poll configuration dashboard",
)
@app_commands.guild_only()
@admin()
async def poll_config(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Polls are only available inside a server.", ephemeral=True)
    dashboard = poll_dashboard_embed(bot.database, guild)
    fallback_channel_id = interaction.channel.id if isinstance(interaction.channel, discord.TextChannel) else 0
    view = PollDashboardView(
        bot.database,
        bot.polls,
        guild.id,
        interaction.user.id,
        fallback_channel_id,
    )
    await interaction.response.send_message(embed=dashboard, view=view, ephemeral=True)


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
    description="Optional context shown under the poll title",
)
async def poll_create(
    interaction: discord.Interaction,
    question: str,
    options: str,
    duration_hours: app_commands.Range[int, 1, MAX_POLL_DURATION_HOURS] | None = None,
    description: str | None = None,
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

    cleaned_description = (description or "").strip()
    if len(cleaned_description) > 1000:
        await interaction.response.send_message(
            "❌ Poll descriptions must be 1,000 characters or fewer.",
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
            description=cleaned_description,
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
    name="results",
    description="📈 Show a poll's current results without ending it",
)
@app_commands.guild_only()
@app_commands.describe(poll_id="Start typing to pick a poll, or paste its ID")
async def poll_results(
    interaction: discord.Interaction,
    poll_id: str,
) -> None:
    """Show live standings to the creator or a server manager, without ending the poll."""
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
            "🔒 Only the poll creator or a server manager can see results before the poll ends.",
            ephemeral=True,
        )
        return

    standings = poll_embed(
        poll,
        bot.database.poll_options(poll["poll_id"]),
        bot.database.poll_results(poll["poll_id"]),
    )
    if poll["status"] == "open":
        lead = "📊 Live standings — the poll is still open, so these numbers can still change."
    else:
        lead = "🔒 Final results — this poll has ended."
    standings.description = f"{lead}\n\n{standings.description}"
    await interaction.response.send_message(embed=standings, ephemeral=True)


@poll_group.command(
    name="end",
    description="🔒 End a poll and publish its final results",
)
@app_commands.guild_only()
@app_commands.describe(poll_id="Start typing to pick an open poll, or paste its ID")
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


@poll_end.autocomplete("poll_id")
async def poll_end_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Offer this server's open polls with their questions."""
    if interaction.guild_id is None:
        return []
    rows = bot.database.active_polls(interaction.guild_id)
    return [
        app_commands.Choice(name=label, value=value)
        for label, value in build_choices(
            rows,
            ("question", "poll_id"),
            "poll_id",
            current,
        )
    ]


@poll_group.command(
    name="remove",
    description="🗑️ Remove a poll message and its stored votes",
)
@app_commands.guild_only()
@app_commands.describe(poll_id="Start typing to pick any of this server's polls, or paste its ID")
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

    async def remove_poll(target: discord.Interaction) -> None:
        await target.response.defer(ephemeral=True)
        try:
            await bot.polls.remove_public_message(guild, poll)
        except discord.Forbidden:
            await target.followup.send(
                "❌ I could not delete the poll message. The poll and votes were kept; check Manage Messages.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            log.warning("Could not remove poll message %s: %s", normalized_poll_id, error)
            await target.followup.send(
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
            await target.followup.send(
                "⚠️ The public message was removed, but I could not remove its stored record. Contact an admin before recreating it.",
                ephemeral=True,
            )
            return
        if not removed:
            await target.followup.send(
                "⚠️ The poll message was removed, but its record was already gone.",
                ephemeral=True,
            )
            return

        await target.followup.send(
            f"🗑️ Poll `{normalized_poll_id}` and its stored votes were removed.",
            ephemeral=True,
        )

    view = ConfirmView(
        remove_poll,
        author_id=interaction.user.id,
        confirm_label="Delete this poll",
    )
    await interaction.response.send_message(
        embed=embed(
            "🗑️ Delete this poll?",
            f"Poll `{normalized_poll_id}` and the votes stored for it will be removed for everyone. This cannot be undone.",
        ),
        view=view,
        ephemeral=True,
    )
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        log.debug("Could not capture the poll confirmation prompt for its timeout message")


@poll_remove.autocomplete("poll_id")
async def poll_remove_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Offer every poll in this server, with its status, for removal."""
    if interaction.guild_id is None:
        return []
    rows = bot.database.guild_polls(interaction.guild_id)
    return [
        app_commands.Choice(name=label, value=value)
        for label, value in build_choices(
            rows,
            ("question", "poll_id", "status"),
            "poll_id",
            current,
        )
    ]


@bot.event
async def on_command_error(ctx, error):
    """Answer a failed prefix command once, in plain language."""
    if isinstance(error, commands.CommandNotFound):
        return
    prefix = settings.prefix
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ {prefix}{ctx.command.qualified_name} still needs **{error.param.name}**. Try {prefix}help.")
        return
    if isinstance(error, (commands.MemberNotFound, commands.UserNotFound)):
        await ctx.send("❌ I could not find that member. Mention them, or paste their user ID.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ That argument did not fit {prefix}{ctx.command.qualified_name}. Try {prefix}help.")
        return
    if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
        await ctx.send(_access_notice(ctx.guild if ctx.guild else None))
        return
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("❌ That command only works inside a server.")
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Slow down a moment — try again in {error.retry_after:.0f}s.")
        return
    log.exception("Prefix command failed", exc_info=error)
    await ctx.send("❌ That command failed. The details are in my logs.")


@bot.event
async def on_ready():
    await bot.clear_stale_guild_command_copies()


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

def _wait_text(seconds: float) -> str:
    """Short human duration used by cooldown feedback."""
    total = max(1, int(round(seconds)))
    minutes, remaining = divmod(total, 60)
    return f"{minutes}m {remaining}s" if minutes else f"{remaining}s"


def command_path(command) -> str:
    """Full slash path of a command object, including its parent groups."""
    names = [getattr(command, "name", "") or ""]
    parent = getattr(command, "parent", None)
    while parent is not None:
        names.append(getattr(parent, "name", "") or "")
        parent = getattr(parent, "parent", None)
    return " ".join(name for name in reversed(names) if name)


def _help_pointer(error) -> str:
    """Link a failed command to its own help entry when the command is known."""
    command = getattr(error, "command", None)
    path = command_path(command) if command is not None else ""
    if path:
        return f" Run `/help command:{path}` for what it needs."
    return " Run `/help` to see what you can use."


@bot.tree.error
async def on_app_command_error(i, error):
    """Answer every failed application command with one clear, ephemeral message."""
    original = getattr(error, "original", error)
    if isinstance(original, discord.NotFound) and getattr(original, "code", None) == 10062:
        log.warning("Application interaction expired or is unknown; response could not be delivered")
        return
    expected = (
        app_commands.CommandOnCooldown,
        app_commands.MissingPermissions,
        app_commands.BotMissingPermissions,
        app_commands.NoPrivateMessage,
        app_commands.CheckFailure,
        OwnerConfigurationError,
        OwnerOnlyError,
    )
    if isinstance(error, expected):
        log.info("Application command declined: %s", error)
    else:
        log.exception("Application command failed", exc_info=error)
    if isinstance(error, app_commands.CommandOnCooldown):
        msg = f"⏳ That command is on cooldown. Try again in {_wait_text(error.retry_after)}."
    elif isinstance(original, discord.HTTPException) and original.status == 429:
        msg = "⏳ Discord rate-limited this request. Please wait a moment and try again."
    elif isinstance(error, app_commands.BotMissingPermissions):
        needed = ", ".join(error.missing_permissions) or "the required permission"
        msg = f"🔒 I am missing **{needed}** here, so I cannot do that. A server administrator can grant it." + _help_pointer(error)
    elif isinstance(error, app_commands.NoPrivateMessage):
        msg = "❌ That command only works inside a server."
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "🔒 You do not have the Discord permissions required for that command." + _help_pointer(error)
    elif isinstance(error, (OwnerConfigurationError, OwnerOnlyError)):
        msg = f"⚠️ {error}"
    elif isinstance(error, app_commands.CheckFailure) and getattr(error, "command", None) and error.command.name == "dashboard":
        msg = "🔒 Dashboard access requires the configured Owner or Co-owner role. The server owner can initialize them with `/setup roles`."
    elif isinstance(error, StaffAccessError):
        msg = f"{error}" + _help_pointer(error)
    elif isinstance(error, app_commands.CheckFailure):
        msg = "🔒 You do not have access to this command." + _help_pointer(error)
    elif isinstance(original, RuntimeError):
        msg = f"⚠️ {error}"
    else:
        msg = ("❌ That command could not be completed. Check its setup, your permissions, and the bot's "
               "channel/role permissions; run `/help` for guidance.")
    try:
        if i.response.is_done(): await i.followup.send(msg, ephemeral=True)
        else: await i.response.send_message(msg, ephemeral=True)
    except discord.NotFound:
        log.warning("Could not deliver application-command error response; interaction expired")


# --- Suggestions and reimbursement -------------------------------------------
suggestion_group = app_commands.Group(name="suggestion", description="💡 Send, read, and configure member suggestions")
bot.tree.add_command(suggestion_group)


@suggestion_group.command(name="send", description="💡 Send an idea and let the server vote on it")
@app_commands.guild_only()
@app_commands.checks.cooldown(3, 600.0, key=lambda i: (i.guild_id, i.user.id))
async def suggestion_send(i: discord.Interaction):
    """Open the suggestion form; the result is posted for public voting."""
    await i.response.send_modal(SuggestionModal(bot.suggestions))


@suggestion_group.command(name="list", description="💡 Show the newest suggestions and how the server voted")
@app_commands.guild_only()
@app_commands.checks.cooldown(4, 60.0, key=lambda i: (i.guild_id, i.user.id))
async def suggestion_list(i: discord.Interaction):
    if not i.guild:
        return await i.response.send_message("💡 Suggestions only exist inside a server.", ephemeral=True)
    rows = bot.suggestions.listing(i.guild.id, limit=10)
    if not rows:
        return await i.response.send_message("💡 No suggestions yet. Use /suggestion send to add the first one.", ephemeral=True)
    counts = bot.database.suggestion_votes_many([row["suggestion_id"] for row in rows])
    panel = embed(
        f"💡 Suggestions — {i.guild.name}",
        f"The **{len(rows)}** newest ideas, newest first, with the votes they have collected.",
    )
    blocks = [suggestion_line(row, counts[row["suggestion_id"]]) for row in rows]
    fields: list[str] = []
    current = ""
    for item in blocks:
        if current and len(current) + len(item) + 2 > 1000:
            fields.append(current)
            current = ""
        current = f"{current}\n\n{item}".strip() if current else item
    if current:
        fields.append(current)
    for index, value in enumerate(fields):
        panel.add_field(name="Newest first" if index == 0 else f"More suggestions ({index + 1})", value=value[:1024], inline=False)
    panel.set_footer(text="Vote on a suggestion in the channel where it was posted")
    await i.response.send_message(embed=panel)


@suggestion_group.command(name="config", description="⚙️ Choose the channel that receives new suggestions (admin)")
@admin()
@app_commands.guild_only()
@app_commands.describe(channel="Text channel where new suggestions should be posted for voting")
async def suggestion_config(i: discord.Interaction, channel: discord.TextChannel):
    await i.response.defer(ephemeral=True)
    bot.database.upsert_config(i.guild.id, suggestion_channel=channel.id)
    await i.followup.send(
        f"✅ Suggestions will be posted in {channel.mention}. Members submit them with the suggestion send "
        "command and everyone can vote with the buttons on each post.",
        ephemeral=True,
    )


@bot.tree.command(name="reimburse", description="🧾 Ask staff for reimbursement of lost items or progress")
@app_commands.guild_only()
@app_commands.checks.cooldown(3, 900.0, key=lambda i: (i.guild_id, i.user.id))
async def reimburse(i: discord.Interaction):
    """Public entry point: everyone gets the reimbursement form and a private ticket."""
    await i.response.send_modal(ReimburseModal(bot.tickets))


# --- Rules panel (SpinBack branding) -----------------------------------------
rules_group = app_commands.Group(name="rules", description="📜 Post and edit the SpinBack rules panel")
bot.tree.add_command(rules_group)


@rules_group.command(name="post", description="📜 Post the rules panel with its Discord and Game buttons")
@admin()
@app_commands.guild_only()
@app_commands.describe(channel="Text channel that should hold the rules panel")
async def rules_post(i: discord.Interaction, channel: discord.TextChannel):
    await bot.rules.post(i, channel)


@rules_group.command(name="edit", description="✏️ Replace the text of one rules section and refresh the panel")
@admin()
@app_commands.guild_only()
@app_commands.describe(section="Which set of rules to rewrite")
@app_commands.choices(section=[app_commands.Choice(name=f"{label} {emoji}".strip(), value=key) for key, label, emoji, _summary in RULES_SECTIONS])
async def rules_edit(i: discord.Interaction, section: app_commands.Choice[str]):
    if not i.guild:
        return await i.response.send_message("❌ Edit the rules inside a server.", ephemeral=True)
    current = bot.rules.text(i.guild.id, section.value)
    await i.response.send_modal(RulesEditModal(bot.rules, section.value).with_current_text(current))


@rules_group.command(name="preview", description="👀 Show the rules panel privately before you post it")
@admin()
@app_commands.guild_only()
async def rules_preview(i: discord.Interaction):
    await i.response.send_message(embed=rules_panel_embed(), view=RulesView(bot.rules), ephemeral=True)


def run():
    if not settings.token:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it outside Discord.")
    bot.run(settings.token, log_handler=None)

if __name__ == "__main__":
    run()
