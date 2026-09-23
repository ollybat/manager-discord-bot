from __future__ import annotations

import discord

from .database import Database
from .utils import utcnow

NEON_PURPLE = discord.Colour.from_rgb(177, 77, 255)
COLOURS = {"general": NEON_PURPLE, "base": discord.Colour.green(), "clan": discord.Colour.purple(), "shop": discord.Colour.gold(), "raid": discord.Colour.red(), "bug": discord.Colour.orange()}


def embed(title: str, description: str, colour: discord.Colour = NEON_PURPLE) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=colour)


def support_panel(guild: discord.Guild, database: Database) -> discord.Embed:
    counts = database.open_counts(guild.id)
    closed = database.closed_count(guild.id)
    closed_eu = database.closed_count(guild.id, "EU")
    result = embed("💜 Grid A1 • Support Center", "✨ Choose the type of help you need, select the EU region, and complete the short ticket form.", NEON_PURPLE)
    result.add_field(name="📊 Live queue", value=f"🟢 Open: **{sum(counts.values())}**\n🇪🇺 EU: **{counts.get('EU', 0)}**\n📁 Closed: **{closed}** ({closed_eu} EU)", inline=True)
    result.add_field(name="🧭 How it works", value="1️⃣ Choose a category\n2️⃣ Select EU\n3️⃣ Add your in-game name\n4️⃣ Answer the questions\n5️⃣ Staff will help you", inline=True)
    result.add_field(name="🗂️ Support categories", value="📄 General • 🏠 Base • 👥 Clan\n💎 Shop • ⚠️ Raid • 🐛 Bug", inline=False)
    result.add_field(name="📌 Before opening", value="Please include clear details, screenshots when useful, and keep replies inside your ticket.", inline=False)
    result.set_footer(text="Grid A1 • EU Support • Manager")
    return result


def inactivity_indicator(last_activity_at: str, inactivity_hours: int, owner_left: bool = False) -> tuple[str, str]:
    from datetime import datetime, timezone
    last = datetime.fromisoformat(last_activity_at)
    elapsed = max(0, int((datetime.now(timezone.utc) - last).total_seconds()))
    hours, remainder = divmod(elapsed, 3600); minutes = remainder // 60
    duration = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    if owner_left or elapsed >= 2 * inactivity_hours * 3600: return "🔴", duration
    if elapsed >= inactivity_hours * 3600: return "🟡", duration
    return "🟢", duration


def ticket_embed(issue: str, region: str, details: str, status: str = "🟢", inactive_for: str = "0m") -> discord.Embed:
    result = embed(f"{status} 🎫 Grid A1 • Support ticket opened", "Your request is now in the support queue. A moderator will review it shortly.", COLOURS.get(issue, discord.Colour.blurple()))
    result.add_field(name="Issue", value=issue.title()[:1024], inline=True)
    result.add_field(name="Region", value=f"🇪🇺 {region}", inline=True)
    result.add_field(name="Initial report", value=discord.utils.escape_markdown(details)[:1024], inline=False)
    result.add_field(name="Activity", value=(f"Active • **{inactive_for}**" if status == "🟢" and inactive_for == "0m" else f"Inactive for **{inactive_for}**"), inline=True)
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
