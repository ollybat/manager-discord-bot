from __future__ import annotations

import discord

from .database import Database
from .utils import utcnow

COLOURS = {"general": discord.Colour.blurple(), "base": discord.Colour.green(), "clan": discord.Colour.purple(), "shop": discord.Colour.gold(), "raid": discord.Colour.red(), "bug": discord.Colour.orange()}

def embed(title: str, description: str, colour: discord.Colour = discord.Colour.blurple()) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=colour, timestamp=utcnow())

def support_panel(guild: discord.Guild, database: Database) -> discord.Embed:
    counts = database.open_counts(guild.id)
    result = embed("🎫 Grid A1 Support Tickets", "Select an issue, then choose 🇪🇺 EU and submit details.\n\n🇺🇸 NA: **Coming Soon**")
    for name, value in (("Open tickets", sum(counts.values())), ("Open EU", counts.get("EU", 0)), ("Open NA", 0), ("Closed tickets", database.closed_count(guild.id)), ("Closed EU", database.closed_count(guild.id, "EU"))): result.add_field(name=name, value=str(value), inline=True)
    result.set_footer(text="Grid A1 • Manager | Live status refreshes every 60 seconds")
    return result

def ticket_embed(issue: str, region: str, details: str) -> discord.Embed:
    return embed("🎫 Grid A1 support ticket", f"**Region:** {region}\n**Issue:** {issue}\n\n**Initial report:**\n{discord.utils.escape_markdown(details)}\n\nA moderator will be with you shortly.", COLOURS.get(issue, discord.Colour.blurple()))
