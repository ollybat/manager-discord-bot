from __future__ import annotations

import discord

from .embeds import embed
from .tickets import TicketService, claim
from .utils import is_ticket, parse_ticket_topic, staff_member

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
        ("ticket", "Ticket setup", "Review ticket panel, category, logs, and inactivity", "🎫"),
        ("staff", "Staff roles", "Review ticket notification roles", "🛡️"),
        ("permission", "Permission roles", "Review owner and co-owner access roles", "🔐"),
        ("verification", "Verification panel", "Review verification channel and role", "✅"),
        ("welcome", "Welcome system", "Review welcome and community channels", "👋"),
        ("moderation", "Moderation settings", "Review the bot's moderation command policy", "🧰"),
        ("server", "Server information", "View live server details", "🌐"),
        ("announcement", "Announcement channels", "Review wipefeed announcement settings", "📣"),
        ("status", "Bot status", "View runtime and connectivity status", "💜"),
    ]

    def __init__(self, view: "DashboardView"):
        self.dashboard = view
        super().__init__(
            placeholder="📱 Open the App Drawer to Configure…",
            options=[discord.SelectOption(label=label, value=value, description=description, emoji=emoji) for value, label, description, emoji in self.OPTIONS],
            custom_id="grid-a1:dashboard:module",
        )

    async def callback(self, interaction: discord.Interaction):
        await self.dashboard.show_module(interaction, self.values[0])


class DashboardView(discord.ui.View):
    """Private, owner-level configuration dashboard; never grants moderator access."""

    MODULE_LABELS = {"ticket": "Ticket setup", "staff": "Staff roles", "permission": "Permission roles", "verification": "Verification panel", "welcome": "Welcome system", "moderation": "Moderation settings", "server": "Server information", "announcement": "Announcement channels", "status": "Bot status"}
    NEXT_ACTIONS = {"ticket": "Next action: configure the panel, logs, category, and inactivity hours.", "staff": "Next action: add at least one notification role if staff alerts are needed.", "permission": "Next action: configure all owner/co-owner and staff permission roles.", "verification": "Next action: configure the panel channel and a manageable verification role.", "welcome": "Next action: configure every community channel used by the welcome system.", "moderation": "Next action: moderation commands are ready; review command permissions if needed.", "server": "Next action: no configuration is required; use this page for live server details.", "announcement": "Next action: configure a channel here, then use `/wipefeed enable enabled:true` and `/wipefeed send`.", "status": "Next action: no configuration is required; investigate only if gateway latency is unavailable."}
    def __init__(self, database, bot_owner_id: int | None):
        super().__init__(timeout=600)
        self.database = database
        self.bot_owner_id = bot_owner_id
        self.add_item(DashboardSelect(self))

    def authorized(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            return False
        if member.id == guild.owner_id or (self.bot_owner_id is not None and member.id == self.bot_owner_id):
            return True
        config = self.database.config(guild.id)
        if not config or not config["owner_role"] or not config["co_owner_role"]:
            return False
        allowed = {int(config["owner_role"]), int(config["co_owner_role"])}
        return any(role.id in allowed for role in member.roles)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.authorized(interaction):
            await interaction.response.send_message("🔒 This dashboard is restricted to the server owner, configured bot owner, or configured owner/co-owner roles.", ephemeral=True)
            return False
        return True

    def dashboard_embed(self, guild: discord.Guild) -> discord.Embed:
        config = self.database.config(guild.id)
        e = embed(f"🎛️ Master Dashboard — {guild.name}", "✨ Private control center\nUse the app drawer to inspect and configure each Grid A1 module.", discord.Colour.from_rgb(177, 77, 255))
        e.add_field(name="🔐 Access", value="Owner-level access verified", inline=True)
        e.add_field(name="⚙️ Configuration", value="`Ready`" if config else "`Not initialized`", inline=True)
        e.add_field(name="📡 Session", value="Private • ephemeral", inline=True)
        statuses = self.module_statuses(guild)
        counts = {"🟢 Active": 0, "🟡 Partial": 0, "🔴 Disabled / Not Setup": 0}
        for _, state in statuses: counts[state] = counts.get(state, 0) + 1
        e.add_field(name="📊 Status summary", value=f"🟢 Active: **{counts['🟢 Active']}** • 🟡 Partial: **{counts['🟡 Partial']}** • 🔴 Disabled: **{counts['🔴 Disabled / Not Setup']}**", inline=False)
        e.add_field(name="📊 Live module status", value="\n".join(f"{state} **{label}**" for label, state in statuses), inline=False)
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        e.set_footer(text="Grid A1 • Refresh configuration to reload live values")
        return e

    def module_statuses(self, guild: discord.Guild):
        """Return all nine modules using persisted configuration and live runtime state."""
        config = self.database.config(guild.id)
        def state(required=(), partial=()):
            if not config: return "🔴 Disabled / Not Setup"
            present = sum(config[key] not in (None, "", 0) for key in required)
            if required and present == len(required): return "🟢 Active"
            if present or any(config[key] not in (None, "", 0) for key in partial): return "🟡 Partial"
            return "🔴 Disabled / Not Setup"
        return [
            ("Ticket setup", state(("panel_channel", "logs_channel", "ticket_category", "panel_message"))),
            ("Staff roles", state(("staff_role_1",), tuple(f"staff_role_{n}" for n in range(2, 11)))),
            ("Permission roles", state(("owner_role", "co_owner_role"), ("moderator_role", "admin_role", "head_admin_role"))),
            ("Verification panel", state(("verify_panel_channel", "verify_role", "verify_panel_message"))),
            ("Welcome system", state(("welcome_channel", "link_channel", "bot_commands_channel", "shop_channel", "verify_channel"))),
            ("Moderation settings", "🟢 Active" if config else "🔴 Disabled / Not Setup"),
            ("Server information", "🟢 Active"),
            ("Announcement channels", state(("wipefeed_enabled", "wipefeed_channel"))),
            ("Bot status", "🟢 Active" if guild.me else "🟡 Partial"),
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
            value = f"**Gateway:** `{latency}`\n**Guilds:** `{len(interaction.client.guilds)}`\n**Uptime:** `{getattr(interaction.client, 'started_at', 'active')}`"
        else:
            fields = {
                "ticket": (("Panel", "panel_channel"), ("Logs", "logs_channel"), ("Category", "ticket_category"), ("Inactivity hours", "inactivity_hours")),
                "staff": (("Notification roles", "staff_role_1"),),
                "permission": (("Owner role", "owner_role"), ("Co-owner role", "co_owner_role"), ("Moderator/Admin roles", "moderator_role")),
                "verification": (("Panel channel", "verify_panel_channel"), ("Verification role", "verify_role")),
                "welcome": (("Welcome channel", "welcome_channel"), ("Link channel", "link_channel"), ("Verify channel", "verify_channel")),
                "moderation": (("Policy", None),),
                "announcement": (("Enabled", "wipefeed_enabled"), ("Channel", "wipefeed_channel")),
            }.get(module, ())
            lines = []
            for label, key in fields:
                raw = "Existing moderation commands remain unchanged." if key is None else (config[key] if config else None)
                if key and "channel" in key and raw: raw = f"<#{raw}>"
                if key and key.endswith("role") and raw: raw = f"<@&{raw}>"
                lines.append(f"**{label}:** {raw if raw not in (None, 0, '') else '`Not configured`'}")
            if module == "staff":
                roles = [config[f"staff_role_{n}"] for n in range(1, 11) if config and config[f"staff_role_{n}"]]
                lines = [f"**Notification roles:** {', '.join(f'<@&{role}>' for role in roles) if roles else '`Not configured`'}"]
            value = "\n".join(lines)
        value = f"**Status:** {status}\n{self.NEXT_ACTIONS.get(module, 'Next action: return to the overview and refresh configuration.')}\n\n{value}"
        e = embed(f"💜 Dashboard • {labels.get(module, module.title())}", value, discord.Colour.from_rgb(177, 77, 255))
        e.set_footer(text="Changes are private and saved to SQLite; use Back to return to the overview.")
        await interaction.response.edit_message(embed=e, view=self)

    async def _configure(self, interaction, modal_cls):
        if not self.authorized(interaction): return await interaction.response.send_message("🔒 Your dashboard access is no longer valid.", ephemeral=True)
        await interaction.response.send_modal(modal_cls(self.database, self.bot_owner_id))

    @discord.ui.button(label="Permission roles", style=discord.ButtonStyle.secondary, emoji="🔐", row=1, custom_id="grid-a1:dashboard:permission-config")
    async def permission_config(self, interaction, button): await self._configure(interaction, PermissionRolesModal)

    @discord.ui.button(label="Staff roles", style=discord.ButtonStyle.secondary, emoji="🛡️", row=1, custom_id="grid-a1:dashboard:staff-config")
    async def staff_config(self, interaction, button): await self._configure(interaction, StaffRolesModal)

    @discord.ui.button(label="Ticket setup", style=discord.ButtonStyle.secondary, emoji="🎫", row=2, custom_id="grid-a1:dashboard:ticket-config")
    async def ticket_config(self, interaction, button): await self._configure(interaction, TicketSetupModal)

    @discord.ui.button(label="Welcome system", style=discord.ButtonStyle.secondary, emoji="👋", row=2, custom_id="grid-a1:dashboard:welcome-config")
    async def welcome_config(self, interaction, button): await self._configure(interaction, WelcomeSystemModal)

    @discord.ui.button(label="Verification panel", style=discord.ButtonStyle.secondary, emoji="✅", row=2, custom_id="grid-a1:dashboard:verification-config")
    async def verification_config(self, interaction, button): await self._configure(interaction, VerificationPanelModal)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️", row=3, custom_id="grid-a1:dashboard:back")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.dashboard_embed(interaction.guild), view=self)

    @discord.ui.button(label="Refresh configuration", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="grid-a1:dashboard:refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.dashboard_embed(interaction.guild), view=self)

    @discord.ui.button(label="Announcement config", style=discord.ButtonStyle.secondary, emoji="📣", row=3, custom_id="grid-a1:dashboard:announcement-config")
    async def announcement_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._configure(interaction, AnnouncementSettingsModal)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji="✖️", row=4, custom_id="grid-a1:dashboard:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="💜 Dashboard closed.", embed=None, view=None)


class _ConfigModal(discord.ui.Modal):
    def __init__(self, database, owner_id, title): super().__init__(title=title); self.database=database; self.owner_id=owner_id
    def allowed(self, i):
        g=i.guild; m=i.user
        if not g or not isinstance(m, discord.Member): return False
        if m.id == g.owner_id or m.id == self.owner_id: return True
        c=self.database.config(g.id)
        return bool(c and c['owner_role'] and c['co_owner_role'] and {r.id for r in m.roles}&{int(c['owner_role']),int(c['co_owner_role'])})
    async def deny(self,i):
        if self.allowed(i): return False
        await i.response.send_message('🔒 Dashboard access is no longer valid. Nothing was saved.',ephemeral=True); return True

def _snowflake(v):
    import re
    m=re.search(r'\d{15,25}',str(v)); return int(m.group()) if m else None

def _valid_role(g,v):
    r=g.get_role(_snowflake(v) or 0); return r if r and not r.is_default() and not r.managed else None

def _valid_text(g,v):
    c=g.get_channel(_snowflake(v) or 0); return c if isinstance(c,discord.TextChannel) else None

def _valid_category(g,v):
    c=g.get_channel(_snowflake(v) or 0); return c if isinstance(c,discord.CategoryChannel) else None

class PermissionRolesModal(_ConfigModal):
    def __init__(self,d,o):
        super().__init__(d,o,'Configure permission roles')
        for n,l in [('owner','Owner role'),('co_owner','Co-owner role'),('moderator','Moderator role'),('admin','Admin role'),('head_admin','Head admin role')]: self.add_item(discord.ui.TextInput(label=f'{l} ID or mention',custom_id=n,max_length=30))
    async def on_submit(self,i):
        if await self.deny(i): return
        vals={x.custom_id:_valid_role(i.guild,x.value) for x in self.children}
        if any(not r for r in vals.values()) or len({r.id for r in vals.values()})<5: return await i.response.send_message('❌ Use five different normal roles from this server. Nothing was saved.',ephemeral=True)
        self.database.upsert_config(i.guild.id,**{f'{k}_role':r.id for k,r in vals.items()}); await i.response.send_message('✅ Permission roles saved to SQLite.',ephemeral=True)

class StaffRolesModal(_ConfigModal):
    def __init__(self,d,o):
        super().__init__(d,o,'Configure staff roles'); self.add_item(discord.ui.TextInput(label='Add role ID/mention (optional)',custom_id='add',required=False)); self.add_item(discord.ui.TextInput(label='Remove role ID/mention (optional)',custom_id='remove',required=False))
    async def on_submit(self,i):
        if await self.deny(i): return
        a,r=[x.value.strip() for x in self.children]
        if bool(a)==bool(r): return await i.response.send_message('❌ Fill exactly one field: add or remove.',ephemeral=True)
        role=_valid_role(i.guild,a or r)
        if not role: return await i.response.send_message('❌ Role must be a normal role in this server.',ephemeral=True)
        try: changed=self.database.add_staff_role(i.guild.id,role.id) if a else self.database.remove_staff_role(i.guild.id,role.id)
        except ValueError as e: return await i.response.send_message(f'❌ {e}',ephemeral=True)
        await i.response.send_message('✅ Staff roles updated.' if changed else 'ℹ️ No change was needed.',ephemeral=True)

class TicketSetupModal(_ConfigModal):
    def __init__(self,d,o):
        super().__init__(d,o,'Configure ticket setup')
        for n,l in [('panel','Panel text channel'),('logs','Logs text channel'),('category','Ticket category')]: self.add_item(discord.ui.TextInput(label=f'{l} ID or mention',custom_id=n,max_length=30))
        self.add_item(discord.ui.TextInput(label='Inactivity hours (1-720)',custom_id='hours',default='24',max_length=3))
    async def on_submit(self,i):
        if await self.deny(i): return
        c={x.custom_id:x.value for x in self.children}; panel,logs,cat=_valid_text(i.guild,c['panel']),_valid_text(i.guild,c['logs']),_valid_category(i.guild,c['category'])
        try: hours=int(c['hours'])
        except ValueError: hours=0
        if not panel or not logs or not cat or not 1<=hours<=720: return await i.response.send_message('❌ Invalid guild channels/category or hours. Nothing was saved.',ephemeral=True)
        self.database.upsert_config(i.guild.id,panel_channel=panel.id,logs_channel=logs.id,ticket_category=cat.id,inactivity_hours=hours); await i.response.send_message('✅ Ticket setup saved. No public panel was deployed; use /setup tickets explicitly to deploy one.',ephemeral=True)

class WelcomeSystemModal(_ConfigModal):
    def __init__(self,d,o):
        super().__init__(d,o,'Configure welcome system')
        for n in ['welcome','link','commands','shop','verify']: self.add_item(discord.ui.TextInput(label=f'{n.title()} text channel ID or mention',custom_id=n,max_length=30))
    async def on_submit(self,i):
        if await self.deny(i): return
        vals={x.custom_id:_valid_text(i.guild,x.value) for x in self.children}
        if any(not v for v in vals.values()): return await i.response.send_message('❌ Every value must be a text channel in this server.',ephemeral=True)
        self.database.upsert_config(i.guild.id,welcome_channel=vals['welcome'].id,link_channel=vals['link'].id,bot_commands_channel=vals['commands'].id,shop_channel=vals['shop'].id,verify_channel=vals['verify'].id); await i.response.send_message('✅ Welcome system channels saved to SQLite.',ephemeral=True)

class VerificationPanelModal(_ConfigModal):
    def __init__(self,d,o):
        super().__init__(d,o,'Configure verification panel'); self.add_item(discord.ui.TextInput(label='Panel text channel ID or mention',custom_id='channel',max_length=30)); self.add_item(discord.ui.TextInput(label='Verification role ID or mention',custom_id='role',max_length=30))
    async def on_submit(self,i):
        if await self.deny(i): return
        c=_valid_text(i.guild,self.children[0].value); r=_valid_role(i.guild,self.children[1].value)
        if not c or not r or not i.guild.me or r>=i.guild.me.top_role: return await i.response.send_message('❌ Use a valid text channel and manageable role. Nothing was saved.',ephemeral=True)
        self.database.upsert_config(i.guild.id,verify_panel_channel=c.id,verify_role=r.id); await i.response.send_message('✅ Verification config saved. No public panel was posted; use /verifypanel explicitly to deploy one.',ephemeral=True)


class AnnouncementSettingsModal(_ConfigModal):
    def __init__(self, d, o):
        super().__init__(d, o, "Configure announcement settings")
        self.add_item(discord.ui.TextInput(label="Announcement text channel ID or mention", custom_id="channel", max_length=30))
        self.add_item(discord.ui.TextInput(label="Enable wipefeed? (yes/no)", custom_id="enabled", required=False, max_length=3, placeholder="yes or no"))

    async def on_submit(self, i):
        if await self.deny(i): return
        channel = _valid_text(i.guild, self.children[0].value)
        enabled = self.children[1].value.strip().lower()
        if not channel or enabled not in ("", "yes", "no"):
            return await i.response.send_message("❌ Use a valid text channel and enter yes or no. Nothing was saved.", ephemeral=True)
        updates = {"wipefeed_channel": channel.id}
        if enabled: updates["wipefeed_enabled"] = int(enabled == "yes")
        self.database.upsert_config(i.guild.id, **updates)
        await i.response.send_message("✅ Announcement settings saved. To post: /wipefeed enable enabled:true, then /wipefeed send.", ephemeral=True)


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
        options = [discord.SelectOption(label=f"Ticket {k.title()}", value=k, emoji=e, description=d) for k,e,d in [("general","📄","General requests and questions"),("base","🏠","Base or area questions"),("clan","👥","Clan requests"),("shop","💎","Store information"),("raid","⚠️","Raid-related problems"),("bug","🐛","In-game or bot bug")]]
        super().__init__(placeholder="💜 Choose your support category…", options=options, custom_id="grid-a1:ticket:type")
    async def callback(self, interaction: discord.Interaction):
        option = next(x for x in self.options if x.value == self.values[0])
        await interaction.response.send_message("Select EU, then complete the short ticket form.", view=RegionView(self.service, option.value, option.label), ephemeral=True)

class TicketPanel(discord.ui.View):
    def __init__(self, service: TicketService): super().__init__(timeout=None); self.add_item(TicketTypeSelect(service))
    @discord.ui.button(label="How it works", style=discord.ButtonStyle.secondary, emoji="❔", custom_id="grid-a1:ticket:help")
    async def how_it_works(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.send_message(embed=embed("How support works", "1️⃣ Pick a category.\n2️⃣ Choose EU.\n3️⃣ Add your in-game name.\n4️⃣ Answer the ticket questions.\n5️⃣ Add screenshots if useful.\n6️⃣ Staff will help you."), ephemeral=True)


class VerifyPanel(discord.ui.View):
    """Persistent neon-purple verification panel."""
    def __init__(self, database):
        super().__init__(timeout=None); self.database = database
    @discord.ui.button(label="Start verification", style=discord.ButtonStyle.primary, emoji="💜", custom_id="grid-a1:verify:start")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member): return await interaction.response.send_message("❌ Verification is only available inside the server.", ephemeral=True)
        config = self.database.config(interaction.guild.id); role = interaction.guild.get_role(config["verify_role"]) if config and config["verify_role"] else None; me = interaction.guild.me
        if not isinstance(role, discord.Role): return await interaction.response.send_message("⚠️ Verification is not configured yet.", ephemeral=True)
        if not me or role.is_default() or role.managed or role >= me.top_role: return await interaction.response.send_message("⚠️ Grid A1 cannot manage this role. Move the bot role above it.", ephemeral=True)
        if role in interaction.user.roles: return await interaction.response.send_message("✅ You are already verified.", ephemeral=True)
        age_days = max(0, (discord.utils.utcnow() - interaction.user.created_at).days)
        if age_days < 7: return await interaction.response.send_message(f"🛡️ Your Discord account is **{age_days} days old**. Accounts under 7 days require staff review. Please open a ticket.", ephemeral=True)
        text = f"Your Discord account is **{age_days} days old**.\n\n✅ Confirm you have read and will follow the server rules.\n✅ Confirm this account belongs to you.\n\nClick **Confirm rules** to receive {role.mention}."
        await interaction.response.send_message(embed=embed("💜 Verification review", text), view=VerificationConfirm(self.database, role.id), ephemeral=True)
class VerificationConfirm(discord.ui.View):
    def __init__(self, database, role_id):
        super().__init__(timeout=300); self.database, self.role_id = database, role_id
    @discord.ui.button(label="Confirm rules", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member): return await interaction.response.send_message("❌ Complete this in the server.", ephemeral=True)
        role = interaction.guild.get_role(self.role_id); me = interaction.guild.me
        if not role or not me or role >= me.top_role: return await interaction.response.send_message("⚠️ The verification role is not currently manageable.", ephemeral=True)
        try: await interaction.user.add_roles(role, reason="Grid A1 verification completed")
        except discord.Forbidden: return await interaction.response.send_message("❌ I cannot assign the role. Check Manage Roles and role order.", ephemeral=True)
        await interaction.response.edit_message(embed=embed("💜 Verification complete", f"🎉 Welcome, {interaction.user.mention}! You now have {role.mention}."), view=None)

class OwnerInactivityView(discord.ui.View):
    """Buttons sent privately to a ticket owner after a red inactivity warning."""
    def __init__(self, service: TicketService, ticket_id: str):
        super().__init__(timeout=86400); self.service = service; self.ticket_id = ticket_id
    async def _owner_check(self, interaction):
        row = self.service.db.ticket(self.ticket_id)
        if not row or row["status"] not in ("open", "close_requested") or row["owner_id"] != interaction.user.id:
            await interaction.response.send_message("This inactivity action is no longer available.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Keep ticket open", style=discord.ButtonStyle.success, emoji="🟢", custom_id="grid-a1:inactive:keep")
    async def keep(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_check(interaction): return
        self.service.db.mark_activity(self.ticket_id); await interaction.response.send_message("✅ The ticket will stay open. Staff have been notified.", ephemeral=True)
    @discord.ui.button(label="Request another staff member", style=discord.ButtonStyle.secondary, emoji="🙋", custom_id="grid-a1:inactive:staff")
    async def staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_check(interaction): return
        self.service.db.set_claim(self.ticket_id, None); self.service.db.mark_activity(self.ticket_id); self.service.db.audit(interaction.guild.id if interaction.guild else 0, self.ticket_id, interaction.user.id, "staff_requested"); await interaction.response.send_message("✅ Your request was sent to staff.", ephemeral=True)
    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="grid-a1:inactive:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_check(interaction): return
        channel = interaction.guild.get_channel(self.service.db.ticket(self.ticket_id)["channel_id"]) if interaction.guild and self.service.db.ticket(self.ticket_id) else None
        if not isinstance(channel, discord.TextChannel): return await interaction.response.send_message("This ticket is already closed.", ephemeral=True)
        await self.service.close_owner_from_dm(interaction, self.ticket_id, "Closed by ticket owner from inactivity notice")


class TicketControls(discord.ui.View):
    def __init__(self, service: TicketService): super().__init__(timeout=None); self.service = service
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_ticket(interaction.channel): await interaction.response.send_message('This ticket is no longer active.', ephemeral=True); return False
        return True
    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="grid-a1:ticket:claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button): await claim(interaction, self.service)
    @discord.ui.button(label="Mark urgent", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="grid-a1:ticket:urgent")
    async def urgent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = parse_ticket_topic(interaction.channel); row = self.service.db.ticket(data.get("id", ""))
        if not row or row["status"] not in ("open", "close_requested"): return await interaction.response.send_message("This ticket is no longer active.", ephemeral=True)
        if row["owner_id"] != interaction.user.id: return await interaction.response.send_message("Only the ticket owner can mark a ticket urgent.", ephemeral=True)
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        if row["urgent_at"] and now - datetime.fromisoformat(row["urgent_at"]) < timedelta(hours=1): return await interaction.response.send_message("🚨 This ticket was already marked urgent recently. Staff have been notified.", ephemeral=True)
        self.service.db.update_ticket(row["ticket_id"], urgent_at=now.isoformat(), urgent_by=interaction.user.id)
        roles = [interaction.guild.get_role(role_id) for role_id in self.service.db.staff_role_ids(interaction.guild.id)]; roles = [role for role in roles if role]
        mentions = " ".join(role.mention for role in roles) or "staff"
        await interaction.channel.send(f"🚨 {mentions} **URGENT SUPPORT REQUEST** — the ticket owner needs immediate staff attention.", allowed_mentions=discord.AllowedMentions(roles=True) if roles else discord.AllowedMentions.none())
        await interaction.response.send_message("🚨 Staff have been urgently notified. Please stay available in this ticket.", ephemeral=True)
    @discord.ui.button(label="Keep ticket open", style=discord.ButtonStyle.success, emoji="🟢", custom_id="grid-a1:ticket:keep-open")
    async def keep_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        data=parse_ticket_topic(interaction.channel); self.service.db.mark_activity(data.get('id','')); await interaction.response.send_message('✅ Your ticket will remain open. Thanks for checking in!', ephemeral=True)
    @discord.ui.button(label="Request another staff member", style=discord.ButtonStyle.secondary, emoji="🙋", custom_id="grid-a1:ticket:request-staff")
    async def request_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        data=parse_ticket_topic(interaction.channel)
        if not isinstance(interaction.user, discord.Member) or interaction.user.id != int(data.get('owner','0')): return await interaction.response.send_message('Only the ticket owner can use this button.', ephemeral=True)
        self.service.db.set_claim(data.get('id',''), None); self.service.db.mark_activity(data.get('id','')); self.service.db.audit(interaction.guild.id,data.get('id'),interaction.user.id,'staff_requested'); await interaction.channel.send('📣 The ticket owner requested another staff member. Please review this ticket.')
        await interaction.response.send_message('✅ Staff have been alerted and the current claim was cleared.', ephemeral=True)
    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="grid-a1:ticket:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
        await interaction.response.send_modal(CloseModal(self.service))

class CloseModal(discord.ui.Modal, title="Close support ticket"):
    reason = discord.ui.TextInput(label="Closing reason", max_length=500)
    def __init__(self, service: TicketService): super().__init__(); self.service = service
    async def on_submit(self, interaction: discord.Interaction): await self.service.close(interaction, str(self.reason.value))

