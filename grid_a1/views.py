from __future__ import annotations

import discord
import time

from .embeds import embed
from .tickets import TicketService, claim
from .utils import active_ticket_owner, inactivity_custom_id, is_ticket, parse_ticket_topic, staff_member

QUESTION_SETS = {
    "general": ("What do you need help with?", "Which server or area is involved?"),
    "base": ("Where is your base or area?", "What happened or what result do you need?"),
    "clan": ("What is your clan name?", "How many members or what clan action is involved?"),
    "shop": ("Which shop item or purchase is this about?", "What order, payment, or issue details can you provide?"),
    "raid": ("Which raid or time was involved?", "What happened and what evidence do you have?"),
    "bug": ("What steps reproduce the bug?", "What device, platform, or error message do you see?"),
}


class DetailsModal(discord.ui.Modal, title="Open a support ticket"):
    in_game_name = discord.ui.TextInput(label="🎮 In-game name", min_length=2, max_length=80, placeholder="Your Rust Console name")
    question_one = discord.ui.TextInput(label="Question 1", max_length=500)
    question_two = discord.ui.TextInput(label="Question 2", max_length=500)
    details = discord.ui.TextInput(label="📝 Tell us what happened", style=discord.TextStyle.paragraph, min_length=5, max_length=1500)

    def __init__(self, service: TicketService, issue: str, label: str, region: str):
        super().__init__()
        self.service, self.issue, self.label, self.region = service, issue, label, region
        q1, q2 = QUESTION_SETS.get(issue, QUESTION_SETS["general"])
        self.question_one.placeholder = q1[:100]
        self.question_two.placeholder = q2[:100]

    async def on_submit(self, interaction: discord.Interaction):
        details = (
            f"🎮 In-game name: {self.in_game_name.value}\n"
            f"❓ Question 1: {self.question_one.placeholder}: {self.question_one.value}\n"
            f"❓ Question 2: {self.question_two.placeholder}: {self.question_two.value}\n"
            f"📝 Details: {self.details.value}"
        )
        await self.service.create(interaction, self.issue, self.label, self.region, details)


class DashboardSelect(discord.ui.Select):
    OPTIONS = [
        ("ticket", "Support tickets", "Set the support panel, ticket channels, and reminders", "🎫"),
        ("staff", "Extra ticket access", "Let extra roles view tickets; staff ping roles stay the same", "🛡️"),
        ("permission", "Staff roles", "Set Owner, Co-owner, Head Admin, Admin, and Moderator", "🔐"),
        ("verification", "Verification", "Choose the panel channel and verified role", "✅"),
        ("welcome", "Welcome messages", "Choose the five community message channels", "👋"),
        ("moderation", "Moderation commands", "Learn about moderation and link protection", "🧰"),
        ("server", "Server overview", "See member, role, and channel counts", "🌐"),
        ("announcement", "Wipe announcements", "Choose the channel and enable or disable announcements", "📣"),
        ("status", "Bot health", "See latency, uptime, and connected servers", "💜"),
        ("reports", "Member reports", "Choose a private staff report destination", "🚩"),
    ]

    def __init__(self, view: "DashboardView"):
        self.dashboard = view
        super().__init__(
            placeholder="Choose an area to view or set up…",
            options=[discord.SelectOption(label=label, value=value, description=description, emoji=emoji) for value, label, description, emoji in self.OPTIONS],
            custom_id="grid-a1:dashboard:module",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self.dashboard.selected_module = self.values[0]
        self.dashboard._sync_configure_button()
        await self.dashboard.show_module(interaction, self.values[0])


class DashboardView(discord.ui.View):
    """Private, owner-level configuration dashboard; never grants moderator access."""

    MODULE_LABELS = {"ticket": "Support tickets", "staff": "Extra ticket access", "permission": "Staff roles", "verification": "Verification", "welcome": "Welcome messages", "moderation": "Moderation commands", "server": "Server overview", "announcement": "Wipe announcements", "status": "Bot health", "reports": "Member reports"}
    NEXT_ACTIONS = {"ticket": "Choose **Set up section** to select the panel, logs, category, and reminder time. Save before publishing.", "staff": "Optional: add roles that may view tickets. Your configured staff roles still receive ticket pings.", "permission": "As the server owner, choose five different roles. This unlocks the dashboard for Owner and Co-owner roles.", "verification": "Choose a panel channel and manageable role, then save or publish.", "welcome": "Choose five text channels for welcome and community messages, then save.", "moderation": "The moderation commands are ready. Use `/help` to see who can run each one.", "server": "This section is view-only. Server details update when you reopen it.", "announcement": "Choose the wipe announcement channel and enable/disable its posting switch.", "status": "This section is view-only. Reopen it to refresh latency and uptime.", "reports": "Choose a private staff-only text channel. Members can then use `/report`."}
    CONFIG_WIZARDS = {"ticket": "TicketSetupWizardView", "staff": "ExtraTicketAccessWizardView", "permission": "PermissionRolesStepOneView", "verification": "VerificationWizardView", "welcome": "WelcomeStepOneView", "announcement": "AnnouncementSettingsWizardView", "reports": "ReportChannelWizardView"}

    def __init__(self, database, bot_owner_id: int | None):
        super().__init__(timeout=600)
        self.database = database
        self.bot_owner_id = bot_owner_id
        self.selected_module = None
        self.viewer_id = None
        self.guild_owner_id = None
        self.add_item(DashboardSelect(self))
        self._sync_configure_button()

    def _sync_configure_button(self):
        enabled = self.selected_module in self.CONFIG_WIZARDS
        label = "Set up section" if enabled else "View only" if self.selected_module else "Choose an area"
        if self.selected_module == "permission" and self.viewer_id is not None and self.guild_owner_id is not None and self.viewer_id != self.guild_owner_id:
            enabled = False
            label = "Server owner only"
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "grid-a1:dashboard:configure":
                item.label = label
                item.disabled = not enabled

    def authorized(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            return False
        # Always let the server owner enter to perform first-time setup.
        if member.id == guild.owner_id:
            return True
        config = self.database.config(guild.id)
        if not config or not config["owner_role"] or not config["co_owner_role"]:
            return False
        allowed = {int(config["owner_role"]), int(config["co_owner_role"])}
        return any(role.id in allowed for role in member.roles)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.authorized(interaction):
            await interaction.response.send_message("🔒 This dashboard is restricted to the server owner and configured Owner/Co-owner roles.", ephemeral=True)
            return False
        self.viewer_id = interaction.user.id
        self.guild_owner_id = interaction.guild.owner_id
        return True

    def dashboard_embed(self, guild: discord.Guild) -> discord.Embed:
        config = self.database.config(guild.id)
        roles_ready = bool(config and config["owner_role"] and config["co_owner_role"])
        intro = "🧭 Choose an area from the dropdown. Review the next step, then press **Set up section**. Changes apply only when you press Save or Publish."
        if not roles_ready:
            intro += "\n\n✨ First time here? Select **Staff roles** first. The server owner can always open this dashboard to finish setup."
        e = embed(f"🎛️ Easy Setup — {guild.name}", intro, discord.Colour.from_rgb(177, 77, 255))
        access = "Server owner can always enter; configured Owner/Co-owner roles can also enter." if roles_ready else "Server owner access • first-time setup is ready."
        configuration = "✅ Owner and Co-owner roles configured" if roles_ready else "🆕 Start with Staff roles"
        e.add_field(name="🔐 Who can use this?", value=access, inline=True)
        e.add_field(name="⚙️ Setup", value=configuration, inline=True)
        e.add_field(name="📡 Visibility", value="Private to you", inline=True)
        statuses = self.module_statuses(guild)
        counts = {"🟢 Active": 0, "🟡 Partial": 0, "🔴 Disabled / Not Setup": 0}
        for _, state in statuses: counts[state] = counts.get(state, 0) + 1
        e.add_field(name="📊 Overview", value=f"🟢 Ready: **{counts['🟢 Active']}** • 🟡 Needs attention: **{counts['🟡 Partial']}** • ⚪ Not set up: **{counts['🔴 Disabled / Not Setup']}**", inline=False)
        e.add_field(name="🧩 Sections", value="\n".join(f"{state} **{label}**" for label, state in statuses), inline=False)
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        e.set_footer(text="Select a section • Save or Publish to apply changes")
        return e

    def module_statuses(self, guild: discord.Guild):
        """Return all ten modules using persisted configuration and live runtime state."""
        config = self.database.config(guild.id)
        def state(required=(), partial=()):
            if not config: return "🔴 Disabled / Not Setup"
            present = sum(config[key] not in (None, "", 0) for key in required)
            if required and present == len(required): return "🟢 Active"
            if present or any(config[key] not in (None, "", 0) for key in partial): return "🟡 Partial"
            return "🔴 Disabled / Not Setup"
        return [
            ("Support tickets", state(("panel_channel", "logs_channel", "ticket_category", "panel_message"))),
            ("Extra ticket access", state(("staff_role_1",), tuple(f"staff_role_{n}" for n in range(2, 11)))),
            ("Staff roles", state(("owner_role", "co_owner_role"), ("moderator_role", "admin_role", "head_admin_role"))),
            ("Verification", state(("verify_panel_channel", "verify_role", "verify_panel_message"))),
            ("Welcome messages", state(("welcome_channel", "link_channel", "bot_commands_channel", "shop_channel", "verify_channel"))),
            ("Moderation commands", "🟢 Active" if config else "🔴 Disabled / Not Setup"),
            ("Server overview", "🟢 Active"),
            ("Wipe announcements", state(("wipefeed_enabled", "wipefeed_channel"))),
            ("Bot health", "🟢 Active" if guild.me else "🟡 Partial"),
            ("Member reports", state(("report_channel",))),
        ]

    async def show_module(self, interaction: discord.Interaction, module: str):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ This dashboard only works inside a server.", ephemeral=True)
        config = self.database.config(guild.id)
        labels = self.MODULE_LABELS
        status = dict(self.module_statuses(guild)).get(labels.get(module, module.title()), "🟡 Partial")
        if module == "server":
            value = f"**Owner:** <@{guild.owner_id}>\n**Members:** `{guild.member_count or 0:,}`\n**Channels:** `{len(guild.channels)}`\n**Roles:** `{max(0, len(guild.roles) - 1)}`"
        elif module == "status":
            latency = f"{round(interaction.client.latency * 1000)} ms" if interaction.client.latency >= 0 else "Unavailable"
            uptime = max(0, int(time.time() - getattr(interaction.client, 'started_at', time.time())))
            days, remainder = divmod(uptime, 86400); hours, remainder = divmod(remainder, 3600); minutes, seconds = divmod(remainder, 60)
            parts = ([f'{days}d'] if days else []) + ([f'{hours}h'] if hours else []) + ([f'{minutes}m'] if minutes else []) + [f'{seconds}s']
            value = f"**Gateway:** `{latency}`\n**Guilds:** `{len(interaction.client.guilds)}`\n**Uptime:** `{' '.join(parts)}`"
        else:
            fields = {
                "ticket": (("Panel", "panel_channel"), ("Logs", "logs_channel"), ("Category", "ticket_category"), ("Inactivity hours", "inactivity_hours")),
                "staff": (("Extra ticket access role", "staff_role_1"),),
                "permission": (("Owner role", "owner_role"), ("Co-owner role", "co_owner_role"), ("Moderator/Admin roles", "moderator_role")),
                "verification": (("Panel channel", "verify_panel_channel"), ("Verification role", "verify_role")),
                "welcome": (("Welcome channel", "welcome_channel"), ("Link channel", "link_channel"), ("Verify channel", "verify_channel")),
                "moderation": (("Policy", None),),
                "announcement": (("Enabled", "wipefeed_enabled"), ("Channel", "wipefeed_channel")),
                "reports": (("Report channel", "report_channel"),),
            }.get(module, ())
            lines = []
            for label, key in fields:
                raw = "Existing moderation commands remain unchanged." if key is None else (config[key] if config else None)
                if key and "channel" in key and raw: raw = f"<#{raw}>"
                if key and key.endswith("role") and raw: raw = f"<@&{raw}>"
                lines.append(f"**{label}:** {raw if raw not in (None, 0, '') else '`Not configured`'}")
            if module == "staff":
                roles = [config[f"staff_role_{n}"] for n in range(1, 11) if config and config[f"staff_role_{n}"]]
                role_mentions = ", ".join(f"<@&{role}>" for role in roles)
                access_summary = role_mentions if roles else "`None (permission roles still receive pings)`"
                lines = [f"**Extra ticket access:** {access_summary}"]
            value = "\n".join(lines)
        value = f"**Status:** {status}\n{self.NEXT_ACTIONS.get(module, 'Next action: return to the overview and refresh configuration.')}\n\n{value}"
        e = embed(f"💜 Dashboard • {labels.get(module, module.title())}", value, discord.Colour.from_rgb(177, 77, 255))
        e.set_footer(text="Changes are private and saved to SQLite; use Back to return to the overview.")
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=e, view=self)
        else:
            await interaction.response.edit_message(embed=e, view=self)

    async def _configure(self, interaction, wizard_name):
        if not self.authorized(interaction): return await interaction.response.send_message("🔒 Your dashboard access is no longer valid.", ephemeral=True)
        from . import dashboard_setup
        wizard_cls = getattr(dashboard_setup, wizard_name)
        if getattr(wizard_cls, "server_owner_only", False) and interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("🔒 Only the server owner can change the five Permission roles.", ephemeral=True)
        wizard = wizard_cls(self, interaction.guild)
        await interaction.response.edit_message(embed=wizard.progress_embed(), view=wizard)

    @discord.ui.button(label="Set up section", style=discord.ButtonStyle.primary, emoji="⚙️", row=1, custom_id="grid-a1:dashboard:configure", disabled=True)
    async def configure_selected(self, interaction: discord.Interaction, button: discord.ui.Button):
        wizard_name = self.CONFIG_WIZARDS.get(self.selected_module)
        if not wizard_name:
            return await interaction.response.send_message("👆 Choose a setup area from the dropdown first.", ephemeral=True)
        await self._configure(interaction, wizard_name)

    @discord.ui.button(label="Home / refresh", style=discord.ButtonStyle.secondary, emoji="🏠", row=1, custom_id="grid-a1:dashboard:home")
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_module = None
        self._sync_configure_button()
        await interaction.response.edit_message(embed=self.dashboard_embed(interaction.guild), view=self)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji="✖️", row=1, custom_id="grid-a1:dashboard:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="💜 Dashboard closed.", embed=None, view=None)


class RegionSelect(discord.ui.Select):
    def __init__(self, service: TicketService, issue: str, label: str):
        self.service, self.issue, self.label = service, issue, label
        super().__init__(placeholder="🇪🇺 Choose your region…", options=[discord.SelectOption(label="EU", value="EU", emoji="🇪🇺", description="European support region")], custom_id="grid-a1:ticket:region")
    async def callback(self, interaction: discord.Interaction): await interaction.response.send_modal(DetailsModal(self.service, self.issue, self.label, self.values[0]))

class RegionView(discord.ui.View):
    def __init__(self, service: TicketService, issue: str, label: str): super().__init__(timeout=180); self.add_item(RegionSelect(service, issue, label))

class TicketTypeSelect(discord.ui.Select):
    def __init__(self, service: TicketService):
        self.service = service
        options = [discord.SelectOption(label=f"Ticket {k.title()}", value=k, emoji=e, description=d) for k,e,d in [("general","📄","Requests and questions not covered by another category"),("base","🏠","Questions or problems about your base or area"),("clan","👥","Clan requests or specific clan-related problems"),("shop","💎","Questions about the store, products, or purchases"),("raid","⚠️","Bounty raids and other raid-related issues"),("bug","🐛","Bugs or glitches in-game or with our bots")]]
        super().__init__(placeholder="Select your issue type ...", options=options, custom_id="grid-a1:ticket:type")
    async def callback(self, interaction: discord.Interaction):
        option = next(x for x in self.options if x.value == self.values[0])
        await interaction.response.send_message("🌍 Choose EU, then complete the short ticket form.", view=RegionView(self.service, option.value, option.label), ephemeral=True)

class TicketPanel(discord.ui.View):
    """Public panel showing only the category selector; detailed instructions stay in the flow."""
    def __init__(self, service: TicketService): super().__init__(timeout=None); self.add_item(TicketTypeSelect(service))


class VerifyPanel(discord.ui.View):
    """Persistent neon-purple verification panel."""
    def __init__(self, database):
        super().__init__(timeout=None); self.database = database
    @discord.ui.button(label="Start verification", style=discord.ButtonStyle.primary, emoji="💜", custom_id="grid-a1:verify:start")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild or not isinstance(interaction.user, discord.Member): return await interaction.followup.send("❌ Verification is only available inside the server.", ephemeral=True)
        config = self.database.config(interaction.guild.id); role = interaction.guild.get_role(config["verify_role"]) if config and config["verify_role"] else None; me = interaction.guild.me
        if not isinstance(role, discord.Role): return await interaction.followup.send("⚠️ Verification is not configured yet.", ephemeral=True)
        if not me or role.is_default() or role.managed or role >= me.top_role: return await interaction.followup.send("⚠️ Grid A1 cannot manage this role. Move the bot role above it.", ephemeral=True)
        if role in interaction.user.roles: return await interaction.followup.send("✅ You are already verified.", ephemeral=True)
        age_days = max(0, (discord.utils.utcnow() - interaction.user.created_at).days)
        if age_days < 7: return await interaction.followup.send(f"🛡️ Your Discord account is **{age_days} days old**. Accounts under 7 days require staff review. Please open a ticket.", ephemeral=True)
        text = f"Your Discord account is **{age_days} days old**.\n\n✅ Confirm you have read and will follow the server rules.\n✅ Confirm this account belongs to you.\n\nClick **Confirm rules** to receive {role.mention}."
        await interaction.followup.send(embed=embed("💜 Verification review", text), view=VerificationConfirm(self.database, role.id), ephemeral=True)
class VerificationConfirm(discord.ui.View):
    def __init__(self, database, role_id):
        super().__init__(timeout=300); self.database, self.role_id = database, role_id
    @discord.ui.button(label="Confirm rules", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild or not isinstance(interaction.user, discord.Member): return await interaction.followup.send("❌ Complete this in the server.", ephemeral=True)
        role = interaction.guild.get_role(self.role_id); me = interaction.guild.me
        if not role or not me or role >= me.top_role: return await interaction.followup.send("⚠️ The verification role is not currently manageable.", ephemeral=True)
        try: await interaction.user.add_roles(role, reason="Grid A1 verification completed")
        except discord.Forbidden: return await interaction.followup.send("❌ I cannot assign the role. Check Manage Roles and role order.", ephemeral=True)
        await interaction.edit_original_response(embed=embed("💜 Verification complete", f"🎉 Welcome, {interaction.user.mention}! You now have {role.mention}."), view=None)

class OwnerInactivityView(discord.ui.View):
    """Buttons sent privately to a ticket owner after a red inactivity warning."""
    def __init__(self, service: TicketService, ticket_id: str):
        super().__init__(timeout=None); self.service = service; self.ticket_id = ticket_id
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id:
                action = item.custom_id.rsplit(":", 1)[-1]
                item.custom_id = inactivity_custom_id(action, ticket_id)
    async def _owner_check(self, interaction):
        row = self.service.db.ticket(self.ticket_id)
        if not active_ticket_owner(row, interaction.user.id):
            await interaction.response.send_message("⏰ This inactivity action is no longer available.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Keep ticket open", style=discord.ButtonStyle.success, emoji="🟢", custom_id="grid-a1:inactive:keep")
    async def keep(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_check(interaction): return
        if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
        if not self.service.db.keep_ticket_open(self.ticket_id):
            return await interaction.followup.send('⚠️ This ticket is no longer active.', ephemeral=True)
        row = self.service.db.ticket(self.ticket_id)
        if not row:
            return await interaction.followup.send('⚠️ This ticket is no longer available.', ephemeral=True)
        self.service.db.audit(row['guild_id'], self.ticket_id, interaction.user.id, 'inactivity_kept_open')
        guild = interaction.client.get_guild(row['guild_id'])
        channel = await self.service.resolve_ticket_channel(guild, row['channel_id']) if guild else None
        notified = False
        if guild and isinstance(channel, discord.TextChannel):
            notified = await self.service.notify_staff(
                guild,
                channel,
                self.ticket_id,
                notice="The ticket owner checked in and kept the ticket open",
            )
        message = '✅ The ticket will stay open.' + (' Staff were notified.' if notified else ' I could not notify staff; please message them in the ticket if you need help.')
        await interaction.followup.send(message, ephemeral=True)
    @discord.ui.button(label="Request another staff member", style=discord.ButtonStyle.secondary, emoji="🙋", custom_id="grid-a1:inactive:staff")
    async def staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_check(interaction): return
        if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
        row = self.service.db.ticket(self.ticket_id)
        if not row or row['status'] not in ('open', 'close_requested'):
            return await interaction.followup.send('⚠️ This ticket is no longer active.', ephemeral=True)
        self.service.db.set_claim(self.ticket_id, None)
        self.service.db.mark_activity(self.ticket_id)
        self.service.db.audit(row['guild_id'], self.ticket_id, interaction.user.id, 'staff_requested')
        guild = interaction.client.get_guild(row['guild_id'])
        channel = await self.service.resolve_ticket_channel(guild, row['channel_id']) if guild else None
        notified = False
        if guild and isinstance(channel, discord.TextChannel):
            notified = await self.service.notify_staff(
                guild,
                channel,
                self.ticket_id,
                notice="The ticket owner requested another staff member",
            )
        message = '✅ Your request was recorded.' + (' Staff were notified in the ticket.' if notified else ' The request was recorded, but I could not notify staff; please try again or contact them directly.')
        await interaction.followup.send(message, ephemeral=True)
    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="grid-a1:inactive:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_check(interaction): return
        await self.service.close_owner_from_dm(interaction, self.ticket_id, "Closed by ticket owner from inactivity notice")


class TicketControls(discord.ui.View):
    def __init__(self, service: TicketService): super().__init__(timeout=None); self.service = service
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.channel, discord.TextChannel) or not is_ticket(interaction.channel):
            await interaction.response.send_message('⚠️ This ticket channel is no longer active.', ephemeral=True); return False
        row = self.service.db.ticket_by_channel(interaction.channel.id)
        if not row:
            await interaction.response.send_message('⚠️ This ticket is already closed or unavailable.', ephemeral=True); return False
        return True
    @discord.ui.button(label="Staff claim", style=discord.ButtonStyle.primary, emoji="🙋", row=0, custom_id="grid-a1:ticket:claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button): await claim(interaction, self.service)
    @discord.ui.button(label="Urgent help", style=discord.ButtonStyle.danger, emoji="🚨", row=0, custom_id="grid-a1:ticket:urgent")
    async def urgent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.service.db.ticket_by_channel(interaction.channel.id)
        if not row: return await interaction.response.send_message("⚠️ This ticket is no longer active.", ephemeral=True)
        if not active_ticket_owner(row, interaction.user.id): return await interaction.response.send_message("🔒 Only the ticket owner can mark a ticket urgent.", ephemeral=True)
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        urgent_at = row["urgent_at"] if "urgent_at" in row.keys() else None
        if urgent_at:
            try:
                previous = datetime.fromisoformat(urgent_at)
                if previous.tzinfo is None: previous = previous.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                previous = None
            if previous and now - previous < timedelta(hours=1): return await interaction.response.send_message("🚨 This ticket was already marked urgent recently.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        notified = await self.service.notify_staff(
            interaction.guild,
            interaction.channel,
            row["ticket_id"],
            notice="🚨 URGENT SUPPORT REQUEST — the ticket owner needs immediate staff attention",
        )
        if not notified:
            return await interaction.followup.send("⚠️ I could not notify the configured staff roles. Please contact staff directly.", ephemeral=True)
        self.service.db.update_ticket(row["ticket_id"], urgent_at=now.isoformat(), urgent_by=interaction.user.id)
        await interaction.followup.send("🚨 Staff were notified. Please stay available in this ticket.", ephemeral=True)
    @discord.ui.button(label="Check in", style=discord.ButtonStyle.success, emoji="🟢", row=0, custom_id="grid-a1:ticket:keep-open")
    async def keep_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.service.db.ticket_by_channel(interaction.channel.id)
        if not row:
            return await interaction.response.send_message('⚠️ This ticket is no longer active.', ephemeral=True)
        if not active_ticket_owner(row, interaction.user.id):
            return await interaction.response.send_message('🔒 Only the ticket owner can check in and keep the ticket open.', ephemeral=True)
        if not self.service.db.keep_ticket_open(row['ticket_id']):
            return await interaction.response.send_message('⚠️ This ticket is no longer active.', ephemeral=True)
        self.service.db.audit(row['guild_id'], row['ticket_id'], interaction.user.id, 'ticket_kept_open')
        await interaction.response.send_message('✅ Your ticket will stay open. Thanks for checking in!', ephemeral=True)
    @discord.ui.button(label="Request staff", style=discord.ButtonStyle.secondary, emoji="🙋", row=1, custom_id="grid-a1:ticket:request-staff")
    async def request_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        row=self.service.db.ticket_by_channel(interaction.channel.id)
        if not row: return await interaction.response.send_message('⚠️ This ticket is no longer active.', ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not active_ticket_owner(row, interaction.user.id): return await interaction.response.send_message('🔒 Only the ticket owner can use this button.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)

        self.service.db.set_claim(row['ticket_id'], None)
        self.service.db.mark_activity(row['ticket_id'])
        self.service.db.audit(interaction.guild.id,row['ticket_id'],interaction.user.id,'staff_requested')
        notified = await self.service.notify_staff(
            interaction.guild,
            interaction.channel,
            row["ticket_id"],
            notice="The ticket owner requested another staff member",
        )
        message='✅ Your request was recorded and the current claim was cleared.' + (' Staff were alerted.' if notified else ' The staff ping could not be delivered; please contact staff directly.')
        await interaction.followup.send(message, ephemeral=True)
    @discord.ui.button(label="Request closure", style=discord.ButtonStyle.secondary, emoji="🔒", row=1, custom_id="grid-a1:ticket:request-close")
    async def request_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.service.db.ticket_by_channel(interaction.channel.id)
        if not row: return await interaction.response.send_message("⚠️ This ticket is no longer active.", ephemeral=True)
        if not active_ticket_owner(row, interaction.user.id): return await interaction.response.send_message("🔒 Only the ticket owner can request closure from this button.", ephemeral=True)
        if row['status'] == 'close_requested': return await interaction.response.send_message("⏳ Staff already have a closure request for this ticket.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if not self.service.db.request_ticket_close(row['ticket_id'], interaction.user.id):
            return await interaction.followup.send('⏳ Staff already have a closure request or this ticket has just closed.', ephemeral=True)
        self.service.db.audit(row['guild_id'], row['ticket_id'], interaction.user.id, 'owner_close_requested')
        notified = await self.service.notify_staff(
            interaction.guild,
            interaction.channel,
            row["ticket_id"],
            notice="The ticket owner requested closure",
        )
        message = '✅ Your closure request was recorded.' + (' Staff were notified.' if notified else ' I could not ping staff; please message them in the ticket.')
        await interaction.followup.send(message, ephemeral=True)
    @discord.ui.button(label="Staff close", style=discord.ButtonStyle.danger, emoji="🔒", row=1, custom_id="grid-a1:ticket:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not staff_member(interaction.user, self.service.db): return await interaction.response.send_message("🔒 Only ticket staff can close tickets.", ephemeral=True)
        await interaction.response.send_modal(CloseModal(self.service))

class CloseModal(discord.ui.Modal, title="Close support ticket"):
    reason = discord.ui.TextInput(label="Closing reason", max_length=500)
    def __init__(self, service: TicketService): super().__init__(); self.service = service
    async def on_submit(self, interaction: discord.Interaction): await self.service.close(interaction, str(self.reason.value))

