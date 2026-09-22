from __future__ import annotations

import discord

from .embeds import embed
from .tickets import TicketService, claim

class DetailsModal(discord.ui.Modal, title="Open a support ticket"):
    details = discord.ui.TextInput(label="Tell us what happened", style=discord.TextStyle.paragraph, min_length=5, max_length=1500)
    def __init__(self, service: TicketService, issue: str, label: str, region: str):
        super().__init__(); self.service, self.issue, self.label, self.region = service, issue, label, region
    async def on_submit(self, interaction: discord.Interaction): await self.service.create(interaction, self.issue, self.label, self.region, str(self.details.value))

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
        await interaction.response.send_message("Choose EU, then describe the issue. NA support is coming soon.", view=RegionView(self.service, option.value, option.label), ephemeral=True)

class TicketPanel(discord.ui.View):
    def __init__(self, service: TicketService): super().__init__(timeout=None); self.add_item(TicketTypeSelect(service))
    @discord.ui.button(label="How it works", style=discord.ButtonStyle.secondary, emoji="❔", custom_id="grid-a1:ticket:help")
    async def how_it_works(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.send_message(embed=embed("How support works", "1. Pick a category.\n2. Choose EU.\n3. Describe the issue.\n4. Add screenshots if useful.\n5. Staff will handle the ticket."), ephemeral=True)

class TicketControls(discord.ui.View):
    def __init__(self, service: TicketService): super().__init__(timeout=None); self.service = service
    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="grid-a1:ticket:claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button): await claim(interaction, self.service)
    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="grid-a1:ticket:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
        await interaction.response.send_modal(CloseModal(self.service))

class CloseModal(discord.ui.Modal, title="Close support ticket"):
    reason = discord.ui.TextInput(label="Closing reason", max_length=500)
    def __init__(self, service: TicketService): super().__init__(); self.service = service
    async def on_submit(self, interaction: discord.Interaction): await self.service.close(interaction, str(self.reason.value))
