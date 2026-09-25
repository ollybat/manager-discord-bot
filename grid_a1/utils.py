"""Shared ticket metadata, time, naming, and staff helpers."""

from __future__ import annotations

import re
import os
import json
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit
import unicodedata

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
            result[key] = unquote(value)
    return result

def is_ticket(channel: discord.abc.GuildChannel | None) -> bool:
    return bool(parse_ticket_topic(channel))

def staff_member(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member): return False
    if member.guild.owner_id == member.id: return True
    if str(member.id) == os.getenv("OWNER_ID", "").strip(): return True
    permissions = member.guild_permissions
    return permissions.administrator or permissions.manage_channels or permissions.manage_guild

def mention_or_id(guild: discord.Guild, value: str) -> str:
    member = guild.get_member(int(value)) if value.isdigit() else None
    return member.mention if member else f"<@{value}>"


_LINK_DOMAIN_RE = re.compile(r'(?<![\w@])(?:https?://|www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?:[/:?#][^\s<>]*)?', re.I)
_INVITE_RE = re.compile(r'(?:discord(?:app)?\s*\.\s*(?:gg|com\s*/\s*invite)|discord\s*\.\s*gg)', re.I)
_ZERO_WIDTH = dict.fromkeys(map(ord, '\u200b\u200c\u200d\ufeff'), None)
def normalize_link_text(text: str) -> str:
    text=unicodedata.normalize('NFKC',text).translate(_ZERO_WIDTH).translate(str.maketrans({'。':'.','．':'.','／':'/','：':':'}))
    return re.sub(r'(?<=[a-zA-Z0-9])\s+(?=[./:])|(?<=[./:])\s+(?=[a-zA-Z0-9])','',text)
def normalize_domain(value: str) -> str | None:
    value=value.strip().lower().rstrip('.')
    if not value or len(value)>253 or any(ch.isspace() for ch in value): return None
    value=re.sub(r'^https?://','',value).split('/',1)[0].split(':',1)[0].lstrip('.')
    return value if re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+',value) else None
def safe_json_list(value, item_type):
    """Return a bounded, typed JSON list; malformed DB values never break events."""
    try: raw=json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError): return []
    if not isinstance(raw, list): return []
    out=[]
    for item in raw:
        try:
            if isinstance(item, bool): continue
            converted=item_type(item)
            if item_type is str and not converted.strip(): continue
            out.append(converted)
        except (TypeError, ValueError): continue
    return out[:100]

def detected_external_links(text: str, whitelist: tuple[str,...]=()):
    normalized=normalize_link_text(text or ''); allowed={d for d in (normalize_domain(x) for x in whitelist) if d}; found=[]
    for m in _LINK_DOMAIN_RE.finditer(normalized):
        raw=m.group(0).rstrip('.,!?;:)]}'); host=(urlsplit(raw if raw.startswith(('http://','https://')) else 'https://'+raw).hostname or '').lower().rstrip('.')
        if host and not any(host==d or host.endswith('.'+d) for d in allowed): found.append(raw)
    for m in _INVITE_RE.finditer(normalized):
        if m.group(0) not in found: found.append(m.group(0))
    return found
def contains_external_link(text: str, whitelist: tuple[str,...]=()) -> bool: return bool(detected_external_links(text, whitelist))
