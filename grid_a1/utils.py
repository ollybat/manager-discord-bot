from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote

import discord

TICKET_TOPIC_PREFIX = "grid-a1-ticket"

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def sanitize_channel_name(region: str, issue: str, username: str, ticket_id: str) -> str:
    raw = f"{region.lower()}-{issue}-{username}-{ticket_id}"
    value = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    return (value or f"ticket-{ticket_id}")[:90]

def ticket_topic(ticket_id: str, owner_id: int, issue: str, region: str) -> str:
    return f"{TICKET_TOPIC_PREFIX};id={ticket_id};owner={owner_id};issue={quote(issue, safe='')};region={region}"

def parse_ticket_topic(channel: discord.abc.GuildChannel | None) -> dict[str, str]:
    if not isinstance(channel, discord.TextChannel) or not channel.topic or not channel.topic.startswith(TICKET_TOPIC_PREFIX + ";"):
        return {}
    result: dict[str, str] = {}
    for part in channel.topic.split(";", 1)[1].split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key] = value
    return result

def is_ticket(channel: discord.abc.GuildChannel | None) -> bool:
    return bool(parse_ticket_topic(channel))

def staff_member(member: discord.Member) -> bool:
    return member.guild_permissions.manage_channels or member.guild_permissions.manage_guild

def mention_or_id(guild: discord.Guild, value: str) -> str:
    member = guild.get_member(int(value)) if value.isdigit() else None
    return member.mention if member else f"<@{value}>"
