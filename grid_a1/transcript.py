"""Render a ticket channel's history as the plain-text archive staff keep."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from .brand import BRAND_NAME

BORDER = "═" * 60
DIVIDER = "─" * 60
LABEL_WIDTH = 13
INDENT = "    "
BOT_MARKER = " [BOT]"
UNKNOWN = "Unknown"

RECORD_FIELDS = ("closed_at", "claimed_by")


def single_line(value: Any) -> str:
    """Collapse whitespace so a name or URL cannot break the label column."""
    return " ".join(str(value).split())


def format_timestamp(value: Any) -> str:
    """Format a UTC time as `10/24/2026, 6:23:54 AM` on every platform."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return UNKNOWN
    if not isinstance(value, datetime):
        return UNKNOWN
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    moment = value.astimezone(timezone.utc)
    hour = moment.strftime("%I").lstrip("0") or "12"
    return f"{moment.month}/{moment.day}/{moment.year}, {hour}:{moment.strftime('%M:%S %p')}"


def display_name(user: Any) -> str:
    """Return the best available name for a Discord user or member."""
    for attribute in ("display_name", "global_name", "name"):
        value = getattr(user, attribute, None)
        if isinstance(value, str) and value.strip():
            return single_line(value)
    return UNKNOWN


def server_tag(user: Any) -> str:
    """Return the member's four-character Discord server tag, when they have one."""
    tag = getattr(getattr(user, "primary_guild", None), "tag", None)
    return single_line(tag) if isinstance(tag, str) and tag.strip() else ""


def author_label(user: Any) -> str:
    """Name shown in a message header, with the bot marker or the member's server tag."""
    name = display_name(user)
    if getattr(user, "bot", False):
        return f"{name}{BOT_MARKER}"
    tag = server_tag(user)
    return f"{name} | {tag}" if tag else name


def record_value(record: Any, key: str) -> Any:
    """Read one column from a sqlite3.Row without assuming the column exists."""
    if key not in RECORD_FIELDS or record is None:
        return None
    try:
        return record[key]
    except (IndexError, KeyError, TypeError):
        return None


def _member(guild: Any, user_id: Any) -> Any:
    """Resolve a stored user ID against the guild, ignoring unusable values."""
    text = str(user_id or "").strip()
    if guild is None or not text.isdigit():
        return None
    try:
        return guild.get_member(int(text))
    except (AttributeError, TypeError, ValueError):
        return None


def _person_name(guild: Any, user_id: Any, fallback: str) -> str:
    """Name of a stored member ID, or the fallback when they cannot be resolved."""
    member = _member(guild, user_id)
    return display_name(member) if member is not None else fallback


def _row(label: str, value: str) -> str:
    """One aligned metadata row in the transcript header."""
    return f"{label:<{LABEL_WIDTH}}  {value}"


def _attachment_lines(message: Any) -> list[str]:
    """Attachment references, kept as links instead of downloading image bytes."""
    lines = []
    for attachment in getattr(message, "attachments", None) or []:
        filename = single_line(getattr(attachment, "filename", "attachment"))
        url = single_line(getattr(attachment, "url", ""))
        lines.append(f"{INDENT}📎 {filename} — {url}" if url else f"{INDENT}📎 {filename}")
    return lines


def _message_lines(message: Any) -> list[str]:
    """The header line and indented body for one message, followed by a blank line."""
    created_at = format_timestamp(getattr(message, "created_at", None))
    lines = [f"[{created_at}] {author_label(getattr(message, 'author', None))}"]
    body = str(getattr(message, "content", "") or "").strip()
    if body:
        lines.extend(f"{INDENT}{line}" for line in body.splitlines())
    lines.extend(_attachment_lines(message))
    lines.append("")
    return lines


async def render(
    messages: Iterable[Any],
    channel: Any,
    data: dict[str, str] | None = None,
    record: Any = None,
) -> str:
    """Render the plain-text transcript archived when a ticket closes.

    The layout follows the transcript staff expect: a bordered header of ticket
    metadata, one indented block per message, then a generated-by footer.
    Attachment bytes are never read, so the file stays small and closes fast.
    """
    message_list = list(messages)
    guild = getattr(channel, "guild", None)
    bot_user = getattr(guild, "me", None)
    topic = data or {}
    generated_at = datetime.now(timezone.utc)
    closed_at = record_value(record, "closed_at") or generated_at
    bot_name = display_name(bot_user) if bot_user is not None else BRAND_NAME

    lines = [BORDER, "  TICKET TRANSCRIPT", BORDER, ""]
    lines.append(_row("Server:", single_line(getattr(guild, "name", UNKNOWN))))
    lines.append(_row("Ticket:", "#" + single_line(getattr(channel, "name", "ticket"))))
    lines.append(_row("Messages:", str(len(message_list))))
    lines.append(_row("Created:", format_timestamp(getattr(channel, "created_at", None))))
    lines.append(_row("Closed:", format_timestamp(closed_at)))
    lines.append(_row("Ticket Owner:", _person_name(guild, topic.get("owner"), UNKNOWN)))
    lines.append(_row("Claimed By:", _person_name(guild, record_value(record, "claimed_by"), "Not claimed")))
    lines.extend(["", DIVIDER, ""])

    for message in message_list:
        lines.extend(_message_lines(message))

    lines.extend(
        ["", DIVIDER, f"Generated by {bot_name} • {format_timestamp(generated_at)}", BORDER]
    )
    return "\n".join(lines) + "\n"
