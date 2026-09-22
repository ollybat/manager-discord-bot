from __future__ import annotations

import discord

from .database import Database
from .utils import utcnow

COLOURS = {"general": discord.Colour.blurple(), "base": discord.Colour.green(), "clan": discord.Colour.purple(), "shop": discord.Colour.gold(), "raid": discord.Colour.red(), "bug": discord.Colour.orange()}


def embed(title: str, description: str, colour: discord.Colour = discord.Colour.blurple()) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=colour, timestamp=utcnow())


def support_panel(guild: discord.Guild, database: Database) -> discord.Embed:
    counts = database.open_counts(guild.id)
    closed = database.closed_count(guild.id)
    closed_eu = database.closed_count(guild.id, "EU")
    result = embed(
        "Support Tickets",
        "Select a support option, then choose EU before filling your questions.\n\n🇺🇸 NA is Coming Soon.\n\nGrid A1 support is organized, private, and easy to follow.",
        discord.Colour.from_rgb(35, 91, 166),
    )
    result.add_field(name="📊 Support Status", value="🟢 **Online**\nPanel refreshes every **60 seconds**", inline=False)
    result.add_field(name="Open Tickets (Total)", value=f"**{sum(counts.values())}**", inline=True)
    result.add_field(name="Open EU Tickets", value=f"🇪🇺 **{counts.get('EU', 0)}**", inline=True)
    result.add_field(name="Open NA Tickets", value="🇺🇸 **0**", inline=True)
    result.add_field(name="Closed Tickets", value=f"**{closed}**", inline=True)
    result.add_field(name="Closed EU Tickets", value=f"🇪🇺 **{closed_eu}**", inline=True)
    result.add_field(name="Response Speed", value="⚡ **Fast**", inline=True)
    result.add_field(name="Estimated Help Time", value="⏱️ **12 mins**", inline=True)
    result.add_field(name="NA availability", value="🇺🇸 **Coming Soon**", inline=True)
    result.add_field(
        name="Category guide",
        value=("📄 **General** — questions and requests\n🏠 **Base** — base or area help\n👥 **Clan** — clan requests\n"
               "💎 **Shop** — store information\n⚠️ **Raid** — raid-related problems\n🐛 **Bug** — in-game or bot bugs"),
        inline=False,
    )
    result.add_field(name="✅ How to open a ticket", value="Use the category menu below → choose **EU** → explain what happened → attach screenshots or proof if useful. Staff will claim and close the ticket when resolved.", inline=False)
    result.set_footer(text=f"Grid A1 • Manager  •  {guild.name}  •  Live status")
    result.timestamp = utcnow()
    return result


def ticket_embed(issue: str, region: str, details: str) -> discord.Embed:
    result = embed("🎫 Grid A1 • Support ticket opened", "Your request is now in the support queue. A moderator will review it shortly.", COLOURS.get(issue, discord.Colour.blurple()))
    result.add_field(name="Issue", value=issue.title()[:1024], inline=True)
    result.add_field(name="Region", value=f"🇪🇺 {region}", inline=True)
    result.add_field(name="Initial report", value=discord.utils.escape_markdown(details)[:1024], inline=False)
    result.set_footer(text="Please keep replies in this channel • Times shown in UTC")
    return result


def ticket_archive_embed(ticket_id: str, region: str, issue: str, closer: discord.abc.User, reason: str) -> discord.Embed:
    result = embed("📁 Grid A1 • Ticket archived", "The ticket transcript is attached for staff records. The ticket channel has been closed.", discord.Colour.dark_grey())
    result.add_field(name="Ticket ID", value=f"`{ticket_id}`", inline=True)
    result.add_field(name="Issue", value=issue.title()[:1024], inline=True)
    result.add_field(name="Region", value=region, inline=True)
    result.add_field(name="Closed by", value=closer.mention, inline=True)
    result.add_field(name="Closing reason", value=discord.utils.escape_markdown(reason)[:1024], inline=False)
    result.set_footer(text="Grid A1 Manager • Transcript attached • All times UTC")
    return result
