"""Shared ticket metadata, time, naming, and access-control helpers."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, TypeVar
from urllib.parse import quote, unquote, urlsplit

import discord

TICKET_TOPIC_PREFIX = "grid-a1-ticket"

T = TypeVar("T")

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)
_LINK_TRANSLATION = str.maketrans(
    {
        "。": ".",
        "．": ".",
        "／": "/",
        "：": ":",
    }
)
_LINK_DOMAIN_RE = re.compile(
    r"(?<![\w@])"
    r"(?:https?://|www\.)?"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
    r"(?:[/:?#][^\s<>]*)?",
    re.IGNORECASE,
)
_INVITE_RE = re.compile(
    r"(?:discord(?:app)?\s*\.\s*(?:gg|com\s*/\s*invite)|discord\s*\.\s*gg)",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
    re.IGNORECASE,
)


def utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def sanitize_channel_name(
    region: str,
    issue: str,
    username: str,
    ticket_id: str,
) -> str:
    """Build a Discord-safe, bounded channel name for a new ticket."""
    raw_name = f"{region.lower()}-{issue}-{username}-{ticket_id}"
    sanitized = re.sub(r"[^a-z0-9-]+", "-", raw_name.lower()).strip("-")
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    return (sanitized or f"ticket-{ticket_id}")[:90]


def ticket_status_title(title: str | None, indicator: str) -> str:
    """Replace any existing status prefix without accumulating decorations."""
    base_title = re.sub(
        r"^(?:(?:🟢|🟡|🔴|🟣|💜|🎫)\s*)+",
        "",
        title or "",
    ).strip()
    return f"{indicator} 🎫 {base_title}"[:256]


def inactivity_custom_id(action: str, ticket_id: str) -> str:
    """Give inactivity controls unique, persistent IDs for their ticket."""
    if action not in {"keep", "staff", "close"}:
        raise ValueError("unknown inactivity action")

    safe_ticket_id = re.sub(r"[^A-Za-z0-9_-]", "", str(ticket_id))[:32]
    if not safe_ticket_id:
        raise ValueError("ticket_id is required")

    return f"grid-a1:inactive:{safe_ticket_id}:{action}"[:100]


def ticket_topic(ticket_id: str, owner_id: int, issue: str, region: str) -> str:
    """Encode the minimum routing metadata in a ticket channel topic."""
    encoded_issue = quote(issue, safe="")
    return (
        f"{TICKET_TOPIC_PREFIX};id={ticket_id};owner={owner_id};"
        f"issue={encoded_issue};region={region}"
    )


def parse_ticket_topic(
    channel: discord.abc.GuildChannel | None,
) -> dict[str, str]:
    """Read ticket routing metadata, returning an empty mapping for other channels."""
    if not isinstance(channel, discord.TextChannel):
        return {}
    if not channel.topic or not channel.topic.startswith(f"{TICKET_TOPIC_PREFIX};"):
        return {}

    result: dict[str, str] = {}
    topic_data = channel.topic.split(";", 1)[1]
    for part in topic_data.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key] = unquote(value)

    return result


def is_ticket(channel: discord.abc.GuildChannel | None) -> bool:
    """Return whether a channel carries the Grid A1 ticket topic marker."""
    return bool(parse_ticket_topic(channel))


def active_ticket_owner(row: Any, user_id: int) -> bool:
    """Return whether user_id owns a ticket that is still open to activity."""
    if not row:
        return False

    try:
        status = row["status"]
        owner_id = int(row["owner_id"])
        requested_user_id = int(user_id)
    except (KeyError, IndexError, TypeError, ValueError):
        return False

    return status in {"open", "close_requested"} and owner_id == requested_user_id


def staff_member(member: discord.Member, database: Any = None) -> bool:
    """Check the server owner, configured bot owner, permissions, and staff roles."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild.owner_id == member.id:
        return True

    configured_owner_id = os.getenv("OWNER_ID", "").strip()
    if configured_owner_id and str(member.id) == configured_owner_id:
        return True

    permissions = member.guild_permissions
    if permissions.administrator or permissions.manage_channels or permissions.manage_guild:
        return True
    if database is None:
        return False

    # Notification targets and extra viewer roles do not automatically grant staff access.
    staff_role_ids = set(database.configured_permission_role_ids(member.guild.id))
    return any(role.id in staff_role_ids for role in member.roles)


def mention_or_id(guild: discord.Guild, value: str) -> str:
    """Resolve a member ID to a mention, falling back to a raw mention for departed users."""
    member = guild.get_member(int(value)) if value.isdigit() else None
    return member.mention if member else f"<@{value}>"


def normalize_link_text(text: str) -> str:
    """Normalize Unicode punctuation and split-up URLs before scanning messages."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.translate(_ZERO_WIDTH).translate(_LINK_TRANSLATION)
    return re.sub(
        r"(?<=[a-zA-Z0-9])\s+(?=[./:])|(?<=[./:])\s+(?=[a-zA-Z0-9])",
        "",
        normalized,
    )


def is_http_url(value: str) -> bool:
    """Accept absolute HTTP(S) evidence links without fetching them server-side."""
    try:
        parsed = urlsplit((value or "").strip())
    except ValueError:
        return False

    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def normalize_domain(value: str) -> str | None:
    """Normalize a hostname for the anti-link whitelist, or return None if invalid."""
    domain = value.strip().lower().rstrip(".")
    if not domain or len(domain) > 253 or any(character.isspace() for character in domain):
        return None

    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/", 1)[0].split(":", 1)[0].lstrip(".")
    return domain if _DOMAIN_RE.fullmatch(domain) else None


def safe_json_list(value: Any, item_type: type[T]) -> list[T]:
    """Return a bounded typed list; malformed or hostile DB values never break events."""
    try:
        raw = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    if not isinstance(raw, list):
        return []

    result: list[T] = []
    for item in raw:
        try:
            if isinstance(item, bool):
                continue
            converted = item_type(item)
            if item_type is str and not converted.strip():
                continue
            result.append(converted)
        except (TypeError, ValueError):
            continue

    return result[:100]


def detected_external_links(
    text: str,
    whitelist: tuple[str, ...] = (),
) -> list[str]:
    """Find external URLs and Discord invites, ignoring exact/subdomain whitelist entries."""
    normalized_text = normalize_link_text(text or "")
    allowed_domains = {
        domain
        for domain in (normalize_domain(item) for item in whitelist)
        if domain
    }
    found_links: list[str] = []

    for match in _LINK_DOMAIN_RE.finditer(normalized_text):
        raw_link = match.group(0).rstrip(".,!?;:)]}")
        candidate = (
            raw_link
            if raw_link.startswith(("http://", "https://"))
            else f"https://{raw_link}"
        )
        try:
            host = (urlsplit(candidate).hostname or "").lower().rstrip(".")
        except ValueError:
            continue

        is_whitelisted = any(
            host == allowed or host.endswith(f".{allowed}")
            for allowed in allowed_domains
        )
        if host and not is_whitelisted:
            found_links.append(raw_link)

    for match in _INVITE_RE.finditer(normalized_text):
        invite = match.group(0)
        if invite not in found_links:
            found_links.append(invite)

    return found_links


def contains_external_link(
    text: str,
    whitelist: tuple[str, ...] = (),
) -> bool:
    """Convenience boolean around the full external-link scanner."""
    return bool(detected_external_links(text, whitelist))
