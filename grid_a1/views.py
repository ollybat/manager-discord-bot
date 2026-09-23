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
        super().__init__(); self.service, self.issue, self.label, self.region = service, issue, label, region
        q1, q2 = QUESTION_SETS.get(issue, QUESTION_SETS["general"]); self.question_one.placeholder = q1[:100]; self.question_two.placeholder = q2[:100]
    async def on_submit(self, interaction: discord.Interaction):
        details = f"🎮 In-game name: {self.in_game_name.value}\n❓ {Question 1: {self.question_one.value}\n❓ Question 2: {self.question_two.value}\n📝 Details: {self.details.value}"
        await self.service.create(interaction, self.issue, self.label, self.region, details)

class RegionSelect(discord.ui.Select):
    def __init__(self, service: TicketService, issue: str, label: str):
        self.service, self.issue, self.label = service, issue, label
        super().__init__(placeholder="Choose your region…", options=[discord.SelectOption(label="EU", value="EU", emoji="🇪🇺", description="European support region")], custom_id="grid-a1:ticket:region")
    async def callback(self, interaction: discord.Interaction): await interaction.response.send_modal(DetailsModal(self.service, self.issue, self.label, self.values[0]))

class RegionView(discord.ui.View):
    def __init__(self, service: TicketService, issue: str, label: str): super().__init__(timeout=180); self.add_item(RegionSelect(service, issue, label))

class TicketTypeSelect(discord.ui.Select):
    def __init__(self, service: TicketService):
        self.service = service
        options = [discord.SelectOption(label=f"Ticket {k.title()}", value=k, emoji=e, description=d) for k,e,d in [("general","📄","General requests and questions"),("base","🏠","Base or area questions"),("clan","👥","Clan requests"),("shop","💎","Store information"),("raid","⚠️","Raid-related problems"),("bug","🐛","In-game or bot bug")]]
        super().__init__(placeholder="Choose what you need help with…", options=options, custom_id="grid-a1:ticket:type")
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
        self.service.db.mark_activity(self.ticket_id)
        await interaction.response.send_message("✅ The ticket will stay open. Staff have been notified.", ephemeral=True)
    @discord.ui.button(label="Request another staff member", style=discord.ButtonStyle.secondary, emoji="🙋", custom_id="grid-a1:inactive:staff")
    async def staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_check(interaction): return
        self.service.db.set_claim(self.ticket_id, None); self.service.db.mark_activity(self.ticket_id); self.service.db.audit(interaction.guild.id if interaction.guild else 0, self.ticket_id, interaction.user.id, "staff_requested")
        await interaction.response.send_message("✅ Your request was sent to staff.", ephemeral=True)
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
        roles = [interaction.guild.get_role(role_id) for role_id in self.service.db.staff_role_ids(interaction.guild.id)]
        roles = [role for role in roles if role]
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
        self.service.db.set_claim(data.get('id',''), None); self.service.db.mark_activity(data.get('id','')); self.service.db.audit(interaction.guild.id,data.get('id'),interaction.user.id,'staff_requested')
        await interaction.channel.send('📣 The ticket owner requested another staff member. Please review this ticket.')
        await interaction.response.send_message('✅ Staff have been alerted and the current claim was cleared.', ephemeral=True)
    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="grid-a1:ticket:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
        await interaction.response.send_modal(CloseModal(self.service))

class CloseModal(discord.ui.Modal, title="Close support ticket"):
    reason = discord.ui.TextInput(label="Closing reason", max_length=500)
    def __init__(self, service: TicketService): super().__init__(); self.service = service
    async def on_submit(self, interaction: discord.Interaction): await self.service.close(interaction, str(self.reason.value))
