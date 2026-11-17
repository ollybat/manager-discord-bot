from __future__ import annotations

import discord

from .brand import BRAND_NAME, BRAND_SHORT, RULES_EMBLEM_NAMES
from .database import Database
from .stats import MONTH_WINDOW_DAYS, RECENT_WINDOW_DAYS, TicketStats, format_duration
from .utils import PRIORITY_LABELS
from .utils import region_display, server_emoji, utcnow

NEON_PURPLE = discord.Colour.from_rgb(177, 77, 255)
PANEL_COLOUR = discord.Colour.from_rgb(94, 186, 255)
TICKET_PANEL_FALLBACK_EMOJI = "🏓"
COLOURS = {"reimburse": discord.Colour.teal(), "general": NEON_PURPLE, "base": discord.Colour.green(), "clan": discord.Colour.purple(), "shop": discord.Colour.gold(), "raid": discord.Colour.red(), "bug": discord.Colour.orange()}


def embed(title: str, description: str, colour: discord.Colour = NEON_PURPLE) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=colour)


TICKET_PANEL_TITLE = "📩 Spinback - Support"
TICKET_PANEL_INTRO = "For any issue you encounter or questions you have plz select one of the following choices."
TICKET_PANEL_HELP = (
    "If you are looking for the commands list|how they work do `/help` this will provide you the full list of "
    "commands and make response time quicker!"
)
TICKET_PANEL_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("📄", "Ticket General", "For any request or question that is not specified in the following categories"),
    ("🏠", "Ticket Base", "Any questions or problems related to your base or your area can be discussed in this ticket"),
    ("👥", "Ticket Clan", "Ticket made to respond to clan requests or possible specific problems"),
    ("💎", "Ticket Shop", "Information about our online store or any product"),
    ("⚠️", "Ticket Raid", "Ticket made for bounty raid or raid related problems"),
    ("🐛", "Ticket Bug", "For any bug/glitch found in-game or with ours bots"),
)
"""The public ticket panel's category lines: emoji, bold heading, and the line under it."""


def ticket_panel_emblem(guild: discord.Guild | None) -> str:
    """The server's own logo emoji when it defines one, otherwise the design's mark."""
    found = server_emoji(guild, RULES_EMBLEM_NAMES + ("support", "ticket", "tickets"))
    return found or TICKET_PANEL_FALLBACK_EMOJI


def support_panel(guild: discord.Guild, database: Database) -> discord.Embed:
    """The public ticket panel: one heading, the guidance lines, then every issue type."""
    year = utcnow().year
    lines = [TICKET_PANEL_INTRO, "", TICKET_PANEL_HELP]
    for emoji, heading, details in TICKET_PANEL_CATEGORIES:
        lines.append(f"**{emoji} {heading}**")
        lines.append(details)
    result = embed(TICKET_PANEL_TITLE, "\n".join(lines), PANEL_COLOUR)
    name = guild.name if guild is not None else BRAND_SHORT
    result.set_footer(text=f"{ticket_panel_emblem(guild)} Support - {name} • {year}")
    return result


def inactivity_indicator(last_activity_at: str, inactivity_hours: int, owner_left: bool = False) -> tuple[str, str]:
    from datetime import datetime, timezone
    last = datetime.fromisoformat(last_activity_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    else:
        last = last.astimezone(timezone.utc)
    elapsed = max(0, int((datetime.now(timezone.utc) - last).total_seconds()))
    hours, remainder = divmod(elapsed, 3600); minutes = remainder // 60
    duration = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    if owner_left or elapsed >= 2 * inactivity_hours * 3600: return "🔴", duration
    if elapsed >= inactivity_hours * 3600: return "🟡", duration
    return "🟢", duration


TICKET_FIELD_EMOJI = (
    ("gamertag", "🎮"),
    ("clan", "🛡️"),
    ("grid", "📍"),
    ("location", "📍"),
    ("what happened", "📝"),
    ("reason", "📝"),
)
"""Field labels used by the ticket forms, mapped to a matching emoji."""


def ticket_detail_fields(details: str) -> list[tuple[str, str]]:
    """Split a form answer like 'Gamertag: X' into (label, value) pairs."""
    fields: list[tuple[str, str]] = []
    current: tuple[str, list[str]] | None = None
    for raw_line in (details or "").splitlines():
        line = raw_line.rstrip()
        head, separator, tail = line.partition(":")
        if separator and len(head) <= 40 and tail.strip():
            if current:
                fields.append((current[0], " ".join(current[1]).strip()))
            current = (head.strip(), [tail.strip()])
        elif current and line.strip():
            current[1].append(line.strip())
        elif line.strip():
            fields.append(("Details", line.strip()))
    if current:
        fields.append((current[0], " ".join(current[1]).strip()))
    return [(label, value) for label, value in fields if value]


def ticket_embed(
    issue: str,
    region: str,
    details: str,
    status: str = "🟢",
    inactive_for: str = "0m",
    requester: str | None = None,
    ticket_id: str | None = None,
) -> discord.Embed:
    """Welcome panel for one ticket: who asked, what it is, and the submitted form."""
    result = embed(
        f"{status} 🎫 {issue.title()} • Support ticket",
        "Your request is in the staff queue. Everything you write here stays private to you and staff.",
        COLOURS.get(issue, discord.Colour.blurple()),
    )
    if requester:
        result.add_field(name="👤 Requested by", value=requester[:1024], inline=True)
    result.add_field(name="🗂️ Category", value=issue.title()[:1024], inline=True)
    result.add_field(name="🌍 Region", value=region_display(region)[:1024], inline=True)
    if ticket_id:
        result.add_field(name="🆔 Reference", value=f"{ticket_id}"[:1024], inline=True)
    fields = ticket_detail_fields(details)
    if fields:
        for label, value in fields[:6]:
            emoji = next((mark for key, mark in TICKET_FIELD_EMOJI if key in label.lower()), "•")
            result.add_field(name=f"{emoji} {label}"[:256], value=discord.utils.escape_markdown(value)[:1024], inline=False)
    else:
        result.add_field(name="📝 Initial report", value=discord.utils.escape_markdown(details)[:1024] or "No details were given.", inline=False)
    result.add_field(
        name="🕒 Activity",
        value=(f"Active • **{inactive_for}**" if status == "🟢" and inactive_for == "0m" else f"Inactive for **{inactive_for}**"),
        inline=True,
    )
    result.set_footer(text="Keep replies in this channel • Times shown in UTC")
    return result


def priority_embed(priority: str, actor: discord.abc.User) -> discord.Embed:
    """Confirm a staff priority change inside the ticket channel."""
    label = PRIORITY_LABELS.get(priority, priority.title())
    result = embed(f"🚦 Priority set to {label}", "Staff triage this queue in priority order, and the change is recorded in the audit log.", NEON_PURPLE)
    result.set_footer(text=f"Set by {actor.display_name}")
    return result


def feedback_embed() -> discord.Embed:
    """Private rating request sent to a ticket owner after closure."""
    result = embed(
        "⭐ How did we do?",
        "Thanks for using support. Tap a star to rate the help you received — it takes a second, and staff only ever see the average.",
        NEON_PURPLE,
    )
    result.set_footer(text="1 star = poor • 5 stars = excellent • You can change your rating any time")
    return result


def ticket_stats_embed(guild: discord.Guild, stats: TicketStats) -> discord.Embed:
    """Render one guild's ticket workload as a staff-facing statistics panel."""
    server = getattr(guild, "name", "this server")
    result = embed(
        f"📊 {server} • Support statistics",
        "Ticket workload and response times read straight from the ticket database. All times are UTC.",
        NEON_PURPLE,
    )
    result.add_field(
        name="🎫 Open tickets",
        value=(
            f"Queue: **{stats.open_total}**\n"
            f"🙋 Unclaimed: **{stats.unclaimed}**\n"
            f"✅ Claimed: **{stats.claimed}**\n"
            f"🔒 Closure requested: **{stats.close_requested}**"
        ),
        inline=True,
    )
    result.add_field(
        name="📦 Closed tickets",
        value=(
            f"Last {RECENT_WINDOW_DAYS} days: **{stats.closed_recent}**\n"
            f"Last {MONTH_WINDOW_DAYS} days: **{stats.closed_month}**"
        ),
        inline=True,
    )
    result.add_field(
        name="⏱️ Response times",
        value=(
            f"First staff claim: **{format_duration(stats.average_claim_minutes)}**\n"
            f"Ticket closure: **{format_duration(stats.average_close_minutes)}**"
        ),
        inline=True,
    )
    result.add_field(
        name="🌍 Open by region",
        value=(
            "\n".join(f"{region}: **{count}**" for region, count in stats.regions)
            or "No open tickets right now."
        ),
        inline=True,
    )
    result.add_field(
        name="🥇 Most claims",
        value=(
            "\n".join(
                f"<@{staff_id}> — **{count}**"
                + (f" • {format_duration(minutes)} avg" if minutes is not None else "")
                for staff_id, count, minutes in stats.top_claimers
            )
            or "No claims recorded yet."
        ),
        inline=True,
    )
    result.add_field(
        name="🚦 Open by priority",
        value=(
            "\n".join(
                f"{PRIORITY_LABELS.get(priority, priority.title())}: **{count}**"
                for priority, count in stats.priorities
            )
            or "No open tickets right now."
        ),
        inline=True,
    )
    result.add_field(
        name="⭐ Feedback",
        value=(
            f"Average: **{stats.rating_average:.2f}/5**\nRatings: **{stats.rating_count}**"
            if stats.rating_average is not None
            else "No ratings yet. Owners are asked privately after closure."
        ),
        inline=True,
    )
    result.set_footer(text="Staff only • Use /ticket stats to refresh")
    return result


def ticket_archive_embed(ticket_id: str, region: str, issue: str, closer: discord.abc.User, reason: str, priority: str = "normal") -> discord.Embed:
    result = embed(f"📁 {BRAND_SHORT} • Ticket archived", "The ticket transcript is attached for staff records. The ticket channel has been closed.", discord.Colour.dark_grey())
    result.add_field(name="Ticket ID", value=f"`{ticket_id}`", inline=True)
    result.add_field(name="Issue", value=issue.title()[:1024], inline=True)
    result.add_field(name="Region", value=region, inline=True)
    result.add_field(name="Priority", value=PRIORITY_LABELS.get(priority, priority.title()), inline=True)
    result.add_field(name="Closed by", value=closer.mention, inline=True)
    result.add_field(name="Closing reason", value=discord.utils.escape_markdown(reason)[:1024], inline=False)
    result.set_footer(text=f"{BRAND_NAME} • Transcript attached • All times UTC")
    return result
