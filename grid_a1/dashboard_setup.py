"""Interactive dropdown-based dashboard setup flows."""
from __future__ import annotations

import discord
import hashlib
import json
import logging

from .embeds import embed, support_panel
from .tickets import TicketService
from .utils import inactivity_custom_id
from .views import DashboardView, TicketPanel, VerifyPanel

log = logging.getLogger(__name__)

class _DashboardWizard(discord.ui.View):
    server_owner_only = False

    def __init__(self, dashboard: DashboardView, guild: discord.Guild, values=None):
        super().__init__(timeout=600)
        self.dashboard = dashboard
        self.database = dashboard.database
        self.guild_id = guild.id
        self.config = self.database.config(guild.id)
        self.values = values if values is not None else {}

    def authorized(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id or not isinstance(interaction.user, discord.Member):
            return False
        if self.server_owner_only:
            return interaction.user.id == interaction.guild.owner_id
        return self.dashboard.authorized(interaction)

    async def interaction_check(self, interaction):
        if not self.authorized(interaction):
            await interaction.response.send_message("🔒 Your dashboard access is no longer valid.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error, item):
        """Acknowledge dashboard failures instead of leaving the button spinning."""
        log.error(
            "Dashboard wizard %s failed on %s: %s",
            type(self).__name__,
            getattr(item, "custom_id", type(item).__name__),
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
        try:
            message = (
                "❌ I couldn't complete that dashboard action. Check the bot's "
                "permissions and try again. If this repeats, ask an admin to review the bot logs."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.DiscordException:
            log.exception("Could not send dashboard failure feedback")

    async def finish(self, interaction, result_embed, view=None):
        """Update the ephemeral wizard, with a follow-up confirmation fallback."""
        try:
            await interaction.edit_original_response(embed=result_embed, view=view)
        except discord.DiscordException as error:
            log.warning(
                "Dashboard wizard %s could not edit its source message; sending follow-up: %s",
                type(self).__name__,
                error,
            )
            await interaction.followup.send(embed=result_embed, ephemeral=True)

    def display(self, value):
        if value is None: return "`Not selected`"
        if hasattr(value, "mention"): return value.mention
        return str(value)

    def progress_embed(self, title, instructions, rows):
        if instructions and ord(instructions[0]) < 128:
            instructions = f"💡 {instructions}"
        e = embed(f"🧭 Quick setup • {title}", instructions, discord.Colour.from_rgb(177, 77, 255))
        e.add_field(name="📋 Current choices", value="\n".join(f"**{label}:** {self.display(self.values.get(key))}" for key, label in rows), inline=False)
        e.set_footer(text="Nothing changes until you choose Save or Publish • Cancel keeps current settings")
        return e

    def fresh_dashboard(self):
        return DashboardView(self.database, self.dashboard.bot_owner_id)

    async def back_to_dashboard(self, interaction, message="Returned to the dashboard. No unsaved changes were applied."):
        view = self.fresh_dashboard()
        await interaction.response.edit_message(embed=view.dashboard_embed(interaction.guild), view=view)


async def _resolve_selected_channel(guild, selected):
    """Resolve ChannelSelect's AppCommandChannel/Thread value to a real guild channel."""
    if guild is None:
        return None
    try:
        channel_id = int(selected.id)
    except (AttributeError, TypeError, ValueError):
        return None

    channel = guild.get_channel(channel_id)
    if channel is None:
        get_thread = getattr(guild, "get_thread", None)
        if get_thread is not None:
            channel = get_thread(channel_id)
    if channel is not None:
        return channel

    try:
        return await guild.fetch_channel(channel_id)
    except discord.NotFound:
        return None
    except discord.Forbidden:
        log.warning("Cannot resolve dashboard channel selection %s in guild %s", channel_id, guild.id)
        return None
    except discord.HTTPException:
        log.exception("Could not resolve dashboard channel selection %s in guild %s", channel_id, guild.id)
        return None


class _RoleDropdown(discord.ui.RoleSelect):
    def __init__(self, wizard, key, placeholder, row):
        self.wizard, self.key = wizard, key
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, row=row)

    async def callback(self, interaction):
        self.wizard.values[self.key] = self.values[0]
        await interaction.response.edit_message(embed=self.wizard.progress_embed(), view=self.wizard)


class _ChannelDropdown(discord.ui.ChannelSelect):
    def __init__(self, wizard, key, placeholder, channel_types, row):
        self.wizard, self.key = wizard, key
        super().__init__(placeholder=placeholder, channel_types=channel_types, min_values=1, max_values=1, row=row)

    async def callback(self, interaction):
        selected = self.values[0]
        channel = await _resolve_selected_channel(interaction.guild, selected)
        if channel is None:
            return await interaction.response.edit_message(
                embed=self.wizard.progress_embed(
                    "I couldn't resolve that channel. Choose it again and retry."
                ),
                view=self.wizard,
            )
        self.wizard.values[self.key] = channel
        await interaction.response.edit_message(embed=self.wizard.progress_embed(), view=self.wizard)


class _ChoiceDropdown(discord.ui.Select):
    def __init__(self, wizard, key, placeholder, options, current=None, row=0):
        self.wizard, self.key = wizard, key
        choices = [discord.SelectOption(label=label, value=value, default=value == current) for label, value in options]
        super().__init__(placeholder=placeholder, options=choices, min_values=1, max_values=1, row=row)

    async def callback(self, interaction):
        self.wizard.values[self.key] = self.values[0]
        await interaction.response.edit_message(embed=self.wizard.progress_embed(), view=self.wizard)


def _role_valid(guild, role):
    return bool(isinstance(role, discord.Role) and role.guild.id == guild.id and not role.is_default() and not role.managed)


def _text_channel_valid(guild, channel):
    return bool(isinstance(channel, discord.TextChannel) and channel.guild.id == guild.id)


def _category_valid(guild, channel):
    return bool(isinstance(channel, discord.CategoryChannel) and channel.guild.id == guild.id)


class PermissionRolesStepOneView(_DashboardWizard):
    server_owner_only = True
    FIELDS = (("owner_role", "Owner"), ("co_owner_role", "Co-owner"), ("head_admin_role", "Head admin"), ("admin_role", "Admin"), ("moderator_role", "Moderator"))

    def __init__(self, dashboard, guild, values=None):
        super().__init__(dashboard, guild, values)
        if values is None:
            for key, _ in self.FIELDS:
                role_id = self.config[key] if self.config else None
                role = guild.get_role(int(role_id)) if role_id else None
                if role: self.values[key] = role
        for row, (key, label) in enumerate(self.FIELDS[:3]):
            self.add_item(_RoleDropdown(self, key, f"Choose {label} role", row))

    def progress_embed(self, message=None):
        text = "Step 1 of 2: choose Owner, Co-owner, and Head admin roles." if not message else message
        return super().progress_embed("Permission roles", text, self.FIELDS[:3])

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.primary, emoji="➡️", row=4)
    async def continue_step(self, interaction, button):
        selected = [self.values.get(key) for key, _ in self.FIELDS[:3]]
        if not all(_role_valid(interaction.guild, role) for role in selected) or len({role.id for role in selected}) != 3:
            return await interaction.response.edit_message(embed=self.progress_embed("Choose three different normal roles before continuing."), view=self)
        view = PermissionRolesStepTwoView(self.dashboard, interaction.guild, self.values)
        await interaction.response.edit_message(embed=view.progress_embed(), view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


class PermissionRolesStepTwoView(_DashboardWizard):
    server_owner_only = True
    FIELDS = PermissionRolesStepOneView.FIELDS

    def __init__(self, dashboard, guild, values):
        super().__init__(dashboard, guild, values)
        for row, (key, label) in enumerate(self.FIELDS[3:]):
            self.add_item(_RoleDropdown(self, key, f"Choose {label} role", row))

    def progress_embed(self, message=None):
        text = "Step 2 of 2: choose Admin and Moderator roles, then save all five." if not message else message
        return super().progress_embed("Permission roles", text, self.FIELDS)

    @discord.ui.button(label="Save roles", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction, button):
        roles = [self.values.get(key) for key, _ in self.FIELDS]
        if not all(_role_valid(interaction.guild, role) for role in roles) or len({role.id for role in roles}) != 5:
            return await interaction.response.edit_message(embed=self.progress_embed("Choose five different normal roles before saving."), view=self)
        await interaction.response.defer()
        self.database.upsert_config(interaction.guild.id, **{key: role.id for (key, _), role in zip(self.FIELDS, roles)})
        view = self.fresh_dashboard()
        await self.finish(interaction, embed("✅ Permission roles saved", "The five roles now grant staff permissions and receive ticket pings."), view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def previous(self, interaction, button):
        view = PermissionRolesStepOneView(self.dashboard, interaction.guild, self.values)
        await interaction.response.edit_message(embed=view.progress_embed(), view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


class TicketSetupWizardView(_DashboardWizard):
    HOURS = (("6 hours", "6"), ("12 hours", "12"), ("24 hours", "24"), ("48 hours", "48"), ("72 hours", "72"), ("7 days", "168"), ("14 days", "336"), ("30 days", "720"))

    def __init__(self, dashboard, guild):
        super().__init__(dashboard, guild)
        for key, source in (("panel", "panel_channel"), ("logs", "logs_channel"), ("category", "ticket_category")):
            channel_id = self.config[source] if self.config else None
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            if channel: self.values[key] = channel
        current_hours = str(self.config["inactivity_hours"] if self.config and self.config["inactivity_hours"] else 24)
        hour_options = self.HOURS if any(value == current_hours for _, value in self.HOURS) else ((f"{current_hours} hours (current)", current_hours),) + self.HOURS
        self.values["hours"] = current_hours
        self.add_item(_ChannelDropdown(self, "panel", "Choose support panel channel", [discord.ChannelType.text], 0))
        self.add_item(_ChannelDropdown(self, "logs", "Choose transcript/logs channel", [discord.ChannelType.text], 1))
        self.add_item(_ChannelDropdown(self, "category", "Choose ticket category", [discord.ChannelType.category], 2))
        self.add_item(_ChoiceDropdown(self, "hours", "Choose inactivity period", hour_options, self.values["hours"], 3))

    def progress_embed(self, message=None):
        text = "Choose the three channels and inactivity period. Save settings, or explicitly publish the public support panel." if not message else message
        return super().progress_embed("Ticket setup", text, (("panel", "Panel channel"), ("logs", "Transcript/logs channel"), ("category", "Ticket category"), ("hours", "Inactivity period (hours)")))

    def valid(self, guild):
        return (_text_channel_valid(guild, self.values.get("panel")) and _text_channel_valid(guild, self.values.get("logs")) and _category_valid(guild, self.values.get("category")) and str(self.values.get("hours", "")).isdigit() and 1 <= int(self.values["hours"]) <= 720)

    def save_settings(self, guild):
        previous = self.database.config(guild.id)
        updates = {"panel_channel": self.values["panel"].id, "logs_channel": self.values["logs"].id, "ticket_category": self.values["category"].id, "inactivity_hours": int(self.values["hours"])}
        if previous and previous["panel_channel"] != self.values["panel"].id:
            updates.update(panel_message=None, panel_fingerprint=None)
        self.database.upsert_config(guild.id, **updates)
        return previous

    @discord.ui.button(label="Save settings", style=discord.ButtonStyle.secondary, emoji="💾", row=4)
    async def save(self, interaction, button):
        if not self.valid(interaction.guild):
            return await interaction.response.edit_message(embed=self.progress_embed("Select valid channels and an inactivity period before saving."), view=self)
        await interaction.response.defer()
        self.save_settings(interaction.guild)
        await self.finish(interaction, self.progress_embed("✅ Settings saved. The public panel was not changed; use Publish support panel when ready."), self)

    @discord.ui.button(label="Publish support panel", style=discord.ButtonStyle.success, emoji="📣", row=4)
    async def publish(self, interaction, button):
        if not self.valid(interaction.guild):
            return await interaction.response.edit_message(embed=self.progress_embed("Select valid channels and an inactivity period before publishing."), view=self)
        await interaction.response.defer()
        try:
            previous = self.database.config(interaction.guild.id)
            self.save_settings(interaction.guild)
            panel = support_panel(interaction.guild, self.database)
            channel = self.values["panel"]
            message = None
            if previous and previous["panel_channel"] == channel.id and previous["panel_message"]:
                try:
                    message = await channel.fetch_message(previous["panel_message"])
                    await message.edit(embed=panel, view=TicketPanel(interaction.client.tickets))
                except discord.NotFound:
                    message = None
            if message is None:
                message = await channel.send(embed=panel, view=TicketPanel(interaction.client.tickets))
            fingerprint = hashlib.sha256(json.dumps(panel.to_dict(), sort_keys=True).encode()).hexdigest()
            self.database.upsert_config(interaction.guild.id, panel_message=message.id, panel_fingerprint=fingerprint)
            view = self.fresh_dashboard()
            await self.finish(interaction, view.dashboard_embed(interaction.guild), view)
        except discord.DiscordException as error:
            log.exception("Dashboard support-panel publish failed: %s", error)
            await self.finish(interaction, self.progress_embed("Settings were saved, but publishing failed. Check the bot's channel permissions and try again."), self)
        except Exception as error:
            log.exception("Dashboard support-panel state update failed: %s", error)
            await self.finish(interaction, self.progress_embed("Publishing failed after saving settings. Please check the bot's permissions and retry."), self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


class ExtraTicketAccessWizardView(_DashboardWizard):
    def __init__(self, dashboard, guild):
        super().__init__(dashboard, guild)
        self.values.update(role=None, action="add")
        self.add_item(_RoleDropdown(self, "role", "Choose an optional extra ticket-access role", 0))
        self.add_item(_ChoiceDropdown(self, "action", "Add or remove this role", (("Add role", "add"), ("Remove role", "remove")), "add", 1))

    def progress_embed(self, message=None):
        ids = self.database.staff_role_ids(self.guild_id)
        roles = [f"<@&{role_id}>" for role_id in ids]
        summary = ", ".join(roles) if roles else "None configured"
        text = f"Optional extra ticket-viewer roles: {summary}. The five Permission roles remain the roles pinged for tickets. Choose one role and Add/Remove." if not message else message
        return super().progress_embed("Extra ticket access", text, (("Role", "role"), ("Action", "action")))

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.primary, emoji="✅", row=4)
    async def apply(self, interaction, button):
        role = self.values.get("role")
        action = self.values.get("action")
        if not _role_valid(interaction.guild, role) or action not in ("add", "remove"):
            return await interaction.response.edit_message(embed=self.progress_embed("Choose a valid server role and Add or Remove."), view=self)
        await interaction.response.defer()
        try:
            changed = self.database.add_staff_role(interaction.guild.id, role.id) if action == "add" else self.database.remove_staff_role(interaction.guild.id, role.id)
        except ValueError as error:
            return await self.finish(interaction, self.progress_embed(str(error)), self)
        result = "✅ Role added to ticket access." if action == "add" and changed else "✅ Role removed from ticket access." if action == "remove" and changed else "ℹ️ No change was needed."
        await self.finish(interaction, self.progress_embed(result), self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


class WelcomeStepOneView(_DashboardWizard):
    FIELDS = (("welcome", "Welcome channel"), ("link", "Link channel"), ("commands", "Commands channel"))

    def __init__(self, dashboard, guild, values=None):
        super().__init__(dashboard, guild, values)
        if values is None:
            for key, config_key in (("welcome", "welcome_channel"), ("link", "link_channel"), ("commands", "bot_commands_channel")):
                channel_id = self.config[config_key] if self.config else None
                channel = guild.get_channel(int(channel_id)) if channel_id else None
                if channel: self.values[key] = channel
        for row, (key, label) in enumerate(self.FIELDS):
            self.add_item(_ChannelDropdown(self, key, f"Choose {label.lower()}", [discord.ChannelType.text], row))

    def progress_embed(self, message=None):
        text = "Step 1 of 2: choose Welcome, Link, and Commands text channels." if not message else message
        return super().progress_embed("Welcome system", text, self.FIELDS)

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.primary, emoji="➡️", row=4)
    async def continue_step(self, interaction, button):
        if not all(_text_channel_valid(interaction.guild, self.values.get(key)) for key, _ in self.FIELDS):
            return await interaction.response.edit_message(embed=self.progress_embed("Choose all three text channels before continuing."), view=self)

        view = WelcomeStepTwoView(self.dashboard, interaction.guild, self.values)
        await interaction.response.edit_message(embed=view.progress_embed(), view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


class WelcomeStepTwoView(_DashboardWizard):
    FIELDS = (("shop", "Shop channel"), ("verify", "Verify channel"))

    def __init__(self, dashboard, guild, values):
        super().__init__(dashboard, guild, values)
        for row, (key, label) in enumerate(self.FIELDS):
            config_key = "shop_channel" if key == "shop" else "verify_channel"
            channel_id = self.config[config_key] if self.config else None
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            if key not in self.values and channel: self.values[key] = channel
            self.add_item(_ChannelDropdown(self, key, f"Choose {label.lower()}", [discord.ChannelType.text], row))

    def progress_embed(self, message=None):
        text = "Step 2 of 2: choose Shop and Verify text channels, then save all five." if not message else message
        return super().progress_embed("Welcome system", text, WelcomeStepOneView.FIELDS + self.FIELDS)

    @discord.ui.button(label="Save channels", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction, button):
        all_fields = WelcomeStepOneView.FIELDS + self.FIELDS
        if not all(_text_channel_valid(interaction.guild, self.values.get(key)) for key, _ in all_fields):
            return await interaction.response.edit_message(embed=self.progress_embed("Choose all five text channels before saving."), view=self)
        await interaction.response.defer()
        self.database.upsert_config(interaction.guild.id, welcome_channel=self.values["welcome"].id, link_channel=self.values["link"].id, bot_commands_channel=self.values["commands"].id, shop_channel=self.values["shop"].id, verify_channel=self.values["verify"].id)
        view = self.fresh_dashboard()
        await self.finish(interaction, embed("✅ Welcome channels saved", "All five channel selections were saved."), view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def previous(self, interaction, button):
        view = WelcomeStepOneView(self.dashboard, interaction.guild, self.values)
        await interaction.response.edit_message(embed=view.progress_embed(), view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


class VerificationWizardView(_DashboardWizard):
    def __init__(self, dashboard, guild):
        super().__init__(dashboard, guild)
        channel_id = self.config["verify_panel_channel"] if self.config else None
        role_id = self.config["verify_role"] if self.config else None
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        role = guild.get_role(int(role_id)) if role_id else None
        if channel: self.values["channel"] = channel
        if role: self.values["role"] = role
        self.add_item(_ChannelDropdown(self, "channel", "Choose verification panel channel", [discord.ChannelType.text], 0))
        self.add_item(_RoleDropdown(self, "role", "Choose the role assigned after verification", 1))

    def progress_embed(self, message=None):
        text = "Choose the channel and verification role. Save config, or explicitly publish the public panel." if not message else message
        return super().progress_embed("Verification panel", text, (("Panel channel", "channel"), ("Verification role", "role")))

    def valid(self, guild):
        role = self.values.get("role")
        return _text_channel_valid(guild, self.values.get("channel")) and _role_valid(guild, role) and bool(guild.me and role < guild.me.top_role)

    def save_settings(self, guild):
        previous = self.database.config(guild.id)
        updates = {"verify_panel_channel": self.values["channel"].id, "verify_role": self.values["role"].id}
        if previous and previous["verify_panel_channel"] != self.values["channel"].id:
            updates["verify_panel_message"] = None
        self.database.upsert_config(guild.id, **updates)
        return previous

    @discord.ui.button(label="Save settings", style=discord.ButtonStyle.secondary, emoji="💾", row=4)
    async def save(self, interaction, button):
        if not self.valid(interaction.guild):
            return await interaction.response.edit_message(embed=self.progress_embed("Choose a valid channel and a role the bot can manage."), view=self)
        await interaction.response.defer()
        self.save_settings(interaction.guild)
        await self.finish(interaction, self.progress_embed("✅ Settings saved. Use Publish panel when ready."), self)

    @discord.ui.button(label="Save & publish", style=discord.ButtonStyle.success, emoji="📣", row=4)
    async def publish(self, interaction, button):
        if not self.valid(interaction.guild):
            return await interaction.response.edit_message(embed=self.progress_embed("Choose a valid channel and a role the bot can manage."), view=self)
        await interaction.response.defer()
        try:
            previous = self.database.config(interaction.guild.id)
            self.save_settings(interaction.guild)
            panel = embed("💜 Grid A1 • Secure Verification", "✨ Complete the short verification check to unlock the server.\n\n📜 Rules confirmation\n✅ Verified role for eligible members\n\nDiscord-wide moderation history is private and unavailable to bots.", discord.Colour.from_rgb(177, 77, 255))
            panel.add_field(name="🔐 Verification steps", value="1️⃣ Start verification\n2️⃣ Review your result\n3️⃣ Confirm the rules\n4️⃣ Receive access", inline=False)
            panel.add_field(name="💬 Need help?", value="If you need help, please open a support ticket.", inline=False)
            panel.set_footer(text="Grid A1 • Secure, fair, Discord-only verification")
            channel = self.values["channel"]
            message = None
            if previous and previous["verify_panel_channel"] == channel.id and previous["verify_panel_message"]:
                try:
                    message = await channel.fetch_message(previous["verify_panel_message"])
                    await message.edit(embed=panel, view=VerifyPanel(self.database))
                except discord.NotFound:
                    message = None
            if message is None:
                message = await channel.send(embed=panel, view=VerifyPanel(self.database))
            self.database.upsert_config(interaction.guild.id, verify_panel_message=message.id)
            view = self.fresh_dashboard()
            await self.finish(interaction, view.dashboard_embed(interaction.guild), view)
        except discord.DiscordException as error:
            log.exception("Verification panel publish failed: %s", error)
            await self.finish(interaction, self.progress_embed("Settings were saved, but publishing failed. Check channel and role permissions."), self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


class AnnouncementSettingsWizardView(_DashboardWizard):
    def __init__(self, dashboard, guild):
        super().__init__(dashboard, guild)
        channel_id = self.config["wipefeed_channel"] if self.config else None
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if channel: self.values["channel"] = channel
        self.values["enabled"] = "yes" if self.config and self.config["wipefeed_enabled"] else "no"
        self.add_item(_ChannelDropdown(self, "channel", "Choose wipefeed announcement channel", [discord.ChannelType.text], 0))
        self.add_item(_ChoiceDropdown(self, "enabled", "Enable or disable wipefeed", (("Enabled", "yes"), ("Disabled", "no")), self.values["enabled"], 1))

    def progress_embed(self, message=None):
        text = "Choose the announcement channel and whether wipefeed should be enabled." if not message else message
        return super().progress_embed("Announcement settings", text, (("Channel", "channel"), ("Enabled", "enabled")))

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction, button):
        if not _text_channel_valid(interaction.guild, self.values.get("channel")) or self.values.get("enabled") not in ("yes", "no"):
            return await interaction.response.edit_message(embed=self.progress_embed("Choose a valid text channel and enabled/disabled."), view=self)
        await interaction.response.defer()
        self.database.upsert_config(interaction.guild.id, wipefeed_channel=self.values["channel"].id, wipefeed_enabled=int(self.values["enabled"] == "yes"))
        view = self.fresh_dashboard()
        await self.finish(interaction, embed("✅ Announcement settings saved", "The wipefeed channel and enabled state were saved."), view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


class ReportChannelWizardView(_DashboardWizard):
    def __init__(self, dashboard, guild):
        super().__init__(dashboard, guild)
        channel_id = self.config["report_channel"] if self.config and "report_channel" in self.config.keys() else None
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if channel: self.values["channel"] = channel
        self.add_item(_ChannelDropdown(self, "channel", "Choose staff-only report destination", [discord.ChannelType.text], 0))

    def progress_embed(self, message=None):
        text = "Select a text channel where submitted reports will be delivered. Restrict that channel's permissions to trusted staff." if not message else message
        return super().progress_embed("Report routing", text, (("Report channel", "channel"),))

    @discord.ui.button(label="Save report channel", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save(self, interaction, button):
        channel = self.values.get("channel")
        if not _text_channel_valid(interaction.guild, channel):
            return await interaction.response.edit_message(embed=self.progress_embed("Choose a text channel in this server."), view=self)
        if channel.permissions_for(interaction.guild.default_role).view_channel:
            return await interaction.response.edit_message(embed=self.progress_embed("This channel is visible to @everyone. Make it staff-only in Discord, then choose it again."), view=self)
        if not interaction.guild.me or not channel.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.edit_message(embed=self.progress_embed("The bot cannot send messages in this channel. Grant it View Channel and Send Messages, then retry."), view=self)
        await interaction.response.defer()
        self.database.upsert_config(interaction.guild.id, report_channel=channel.id)
        view = self.fresh_dashboard()
        await self.finish(interaction, embed("✅ Report destination saved", f"Member reports will be sent to {channel.mention}. Restrict channel visibility to staff."), view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
    async def cancel(self, interaction, button):
        await self.back_to_dashboard(interaction)


