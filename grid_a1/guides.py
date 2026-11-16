"""Live command catalog and permission-aware help guide.

The guide is generated from the bot's own application-command tree, so adding, renaming, or
removing a command cannot leave the help text stale. Every command is annotated with the
access it really requires, sections and rendering live here, and the autocomplete helpers
turn database rows into searchable choices. Nothing in this module talks to Discord, which
keeps it fully unit testable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

log = logging.getLogger("grid-a1-guides")

EVERYONE = "everyone"
MANAGE_MESSAGES = "manage_messages"
ADMIN = "admin"
STAFF = "staff"
DASHBOARD = "dashboard"
SERVER_OWNER = "server_owner"
BOT_OWNER = "bot_owner"
TICKET_PARTICIPANT = "ticket_participant"
POLL_CREATOR = "poll_creator"

ACCESS_LABELS: dict[str, str] = {
    EVERYONE: "Everyone",
    MANAGE_MESSAGES: "Manage Messages",
    ADMIN: "Admin or Manage Server",
    STAFF: "Ticket staff",
    DASHBOARD: "Dashboard role",
    SERVER_OWNER: "Server owner",
    BOT_OWNER: "Bot owner",
    TICKET_PARTICIPANT: "Ticket participant or staff",
    POLL_CREATOR: "Poll creator or managers",
}
"""Context-dependent access: the guide always lists these, and the command re-checks."""
CONTEXTUAL_ACCESS = frozenset({TICKET_PARTICIPANT, POLL_CREATOR})

SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("🚀 Start here", ("help", "dashboard")),
    (
        "🌐 Everyone",
        ("info server", "report", "poll end", "poll results", "poll remove"),
    ),
    (
        "🎫 Tickets",
        (
            "ticket list",
            "ticket claim",
            "ticket transfer",
            "ticket remove",
            "ticket requestclose",
            "ticket close",
            "ticket priority",
            "ticket note",
            "ticket notes",
            "ticket stats",
            "ticket transcript",
        ),
    ),
    (
        "🛡️ Moderation",
        ("modlog", "kick", "ban", "unban", "warn", "timeout", "untimeout", "purge"),
    ),
    (
        "⚙️ Server setup",
        (
            "setup roles",
            "setup tickets",
            "setup welcomer",
            "verifypanel",
            "anti-links",
            "wipefeed enable",
            "wipefeed send",
            "roles setchannel",
            "welcomer preview",
            "welcomer test",
        ),
    ),
    (
        "📊 Polls, giveaways and messages",
        (
            "poll config",
            "poll create",
            "giveaway config",
            "giveaway list",
            "giveaway end",
            "embed",
            "embed-edit",
        ),
    ),
    ("🔧 Bot owner", ("ping", "sync")),
)
OTHER_SECTION = "🧩 Other commands"

COMMAND_ACCESS: dict[str, str] = {
    "help": EVERYONE,
    "dashboard": DASHBOARD,
    "info server": EVERYONE,
    "report": EVERYONE,
    "poll end": POLL_CREATOR,
    "poll results": POLL_CREATOR,
    "poll remove": POLL_CREATOR,
    "ticket list": STAFF,
    "ticket claim": STAFF,
    "ticket transfer": STAFF,
    "ticket remove": STAFF,
    "ticket requestclose": TICKET_PARTICIPANT,
    "ticket close": STAFF,
    "ticket priority": STAFF,
    "ticket note": STAFF,
    "ticket notes": STAFF,
    "ticket stats": STAFF,
    "ticket transcript": STAFF,
    "modlog": STAFF,
    "kick": STAFF,
    "ban": STAFF,
    "unban": STAFF,
    "warn": STAFF,
    "timeout": STAFF,
    "untimeout": STAFF,
    "purge": MANAGE_MESSAGES,
    "setup roles": SERVER_OWNER,
    "setup tickets": ADMIN,
    "setup welcomer": ADMIN,
    "verifypanel": ADMIN,
    "anti-links": ADMIN,
    "wipefeed enable": ADMIN,
    "wipefeed send": ADMIN,
    "roles setchannel": ADMIN,
    "welcomer preview": ADMIN,
    "welcomer test": ADMIN,
    "poll config": ADMIN,
    "poll create": ADMIN,
    "giveaway config": ADMIN,
    "giveaway list": ADMIN,
    "giveaway end": ADMIN,
    "embed": MANAGE_MESSAGES,
    "embed-edit": MANAGE_MESSAGES,
    "ping": BOT_OWNER,
    "sync": BOT_OWNER,
}


@dataclass(frozen=True)
class CommandEntry:
    """One slash command flattened for the guide."""

    path: str
    description: str
    access: str = EVERYONE
    options: tuple[tuple[str, bool], ...] = field(default_factory=tuple)

    @property
    def usage(self) -> str:
        """Render the command with its options, marking the required ones."""
        if not self.options:
            return f"/{self.path}"
        rendered = " ".join(name if required else f"[{name}]" for name, required in self.options)
        return f"/{self.path} {rendered}"

    @property
    def access_label(self) -> str:
        """Human-readable requirement for this command."""
        return ACCESS_LABELS.get(self.access, self.access)


def _node_commands(node: Any) -> tuple[Any, ...]:
    """Return a tree-ish or group-ish node's commands without assuming a type."""
    getter = getattr(node, "get_commands", None)
    if callable(getter):
        return tuple(getter() or ())
    return tuple(getattr(node, "commands", ()) or ())


def _options(command: Any) -> tuple[tuple[str, bool], ...]:
    options: list[tuple[str, bool]] = []
    for parameter in getattr(command, "parameters", ()) or ():
        name = str(getattr(parameter, "name", "") or "")
        if name:
            options.append((name, bool(getattr(parameter, "required", False))))
    return tuple(options)


def _flatten(command: Any, prefix: str) -> list[CommandEntry]:
    path = f"{prefix} {getattr(command, 'name', '')}".strip()
    children = _node_commands(command)
    if children:
        entries: list[CommandEntry] = []
        for child in children:
            entries.extend(_flatten(child, path))
        return entries
    return [
        CommandEntry(
            path=path,
            description=str(getattr(command, "description", "") or ""),
            access=COMMAND_ACCESS.get(path, EVERYONE),
            options=_options(command),
        )
    ]


def catalog(tree: Any) -> tuple[CommandEntry, ...]:
    """Flatten every slash command in a runtime tree into guide entries."""
    entries: list[CommandEntry] = []
    for command in _node_commands(tree):
        entries.extend(_flatten(command, ""))
    return tuple(sorted(entries, key=lambda entry: entry.path))


def _configured_role_ids(bot: Any, guild: Any, keys: Sequence[str]) -> set[int]:
    """Read configured role IDs, degrading to none if storage is unavailable."""
    role_ids: set[int] = set()
    try:
        config = bot.database.config(guild.id)
    except Exception:
        log.debug("Guide could not read server configuration", exc_info=True)
        return role_ids
    for key in keys:
        try:
            value = config[key]
        except (KeyError, IndexError, TypeError):
            continue
        if value:
            try:
                role_ids.add(int(value))
            except (TypeError, ValueError):
                continue
    return role_ids


def _staff_role_ids(bot: Any, guild: Any) -> set[int]:
    try:
        return {int(role_id) for role_id in bot.database.configured_permission_role_ids(guild.id)}
    except Exception:
        log.debug("Guide could not read configured staff roles", exc_info=True)
        return set()


def viewer_access(bot: Any, member: Any, guild: Any) -> frozenset[str]:
    """Resolve the guide's access labels for the member using a command."""
    rights = {EVERYONE, *CONTEXTUAL_ACCESS}
    if member is None or guild is None:
        return frozenset(rights)
    member_id = getattr(member, "id", None)
    guild_owner_id = getattr(guild, "owner_id", None)
    bot_owner_id = getattr(bot, "settings_owner_id", None)
    permissions = getattr(member, "guild_permissions", None)
    role_ids = {getattr(role, "id", None) for role in getattr(member, "roles", ()) or ()}
    server_owner = member_id is not None and member_id == guild_owner_id
    bot_owner = member_id is not None and bot_owner_id is not None and member_id == bot_owner_id
    if bot_owner:
        rights.add(BOT_OWNER)
    if server_owner:
        rights.add(SERVER_OWNER)
    if server_owner or bot_owner or bool(getattr(permissions, "administrator", False)) or bool(
        getattr(permissions, "manage_guild", False)
    ):
        rights.add(ADMIN)
    if ADMIN in rights or bool(getattr(permissions, "manage_messages", False)):
        rights.add(MANAGE_MESSAGES)
    if ADMIN in rights or bool(getattr(permissions, "manage_channels", False)) or (
        role_ids & _staff_role_ids(bot, guild)
    ):
        rights.add(STAFF)
    if server_owner or (role_ids & _configured_role_ids(bot, guild, ("owner_role", "co_owner_role"))):
        rights.add(DASHBOARD)
    return frozenset(rights)


def format_entry(entry: CommandEntry) -> str:
    """One guide line: usage, description, and the requirement when it is not public."""
    suffix = "" if entry.access == EVERYONE else f" — *{entry.access_label}*"
    return f"`{entry.usage}` — {entry.description}{suffix}"


def _section_of(path: str) -> str:
    for title, paths in SECTIONS:
        if path in paths:
            return title
    return OTHER_SECTION


def _chunk(title: str, lines: Sequence[str], max_chars: int) -> list[tuple[str, str]]:
    """Split one section into embed fields that stay inside Discord limits."""
    fields: list[tuple[str, str]] = []
    current: list[str] = []
    length = 0
    for line in lines:
        extra = len(line) + 1
        if current and length + extra > max_chars:
            fields.append((title if not fields else f"{title} (cont.)", f"{chr(10)}".join(current)))
            current, length = [], 0
        current.append(line)
        length += extra
    if current:
        fields.append((title if not fields else f"{title} (cont.)", f"{chr(10)}".join(current)))
    return fields


def render_guide(
    entries: Iterable[CommandEntry],
    rights: Iterable[str],
    max_chars: int = 1000,
    max_total: int = 5200,
) -> list[tuple[str, str]]:
    """Group the commands a viewer may use into embed fields, in section order.

    Discord caps an embed at 6000 characters, so the guide keeps a budget and points at the
    per-command detail view when a member can use more commands than fit in one embed.
    """
    allowed = set(rights)
    grouped: dict[str, list[str]] = {}
    for entry in entries:
        if entry.access not in allowed:
            continue
        grouped.setdefault(_section_of(entry.path), []).append(format_entry(entry))
    fields: list[tuple[str, str]] = []
    for title, _ in SECTIONS:
        lines = grouped.pop(title, [])
        if lines:
            fields.extend(_chunk(title, lines, max_chars))
    for title, lines in grouped.items():
        fields.extend(_chunk(title, lines, max_chars))

    kept: list[tuple[str, str]] = []
    total = 0
    for name, value in fields:
        size = len(name) + len(value) + 2
        if kept and total + size > max_total:
            break
        kept.append((name, value))
        total += size
    hidden = sum(
        value.count(f"{chr(10)}") + 1 for _, value in fields[len(kept):]
    )
    if hidden:
        kept.append(
            (
                "➕ Not shown here",
                f"Use {chr(96)}/help command{chr(96)} to search the other {hidden} "
                f"command{'s' if hidden != 1 else ''} you can use.",
            )
        )
    return kept


def match_entries(entries: Iterable[CommandEntry], query: str, limit: int = 25) -> list[CommandEntry]:
    """Rank commands for autocomplete: prefix matches first, then substring matches."""
    needle = (query or "").strip().lower()
    ordered = sorted(entries, key=lambda entry: entry.path)
    if not needle:
        return ordered[: max(1, limit)]
    starts = [entry for entry in ordered if entry.path.startswith(needle)]
    contains = [entry for entry in ordered if needle in entry.path and entry not in starts]
    return (starts + contains)[: max(1, limit)]


def command_detail(entry: CommandEntry) -> str:
    """Describe one command: purpose, usage, options, and access."""
    lines = [entry.description or "No description provided.", "", f"Usage: `{entry.usage}`"]
    if entry.options:
        rendered = ", ".join(
            f"`{name}`" if required else f"`{name}` (optional)"
            for name, required in entry.options
        )
        lines.append(f"Options: {rendered}")
    lines.append(f"Access: {entry.access_label}")
    return f"{chr(10)}".join(lines)


def _row_text(row: Any, key: str) -> str:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return ""
    return "" if value is None else str(value)


def build_choices(
    rows: Iterable[Any],
    label_keys: Sequence[str],
    value_key: str,
    query: str,
    limit: int = 25,
) -> list[tuple[str, str]]:
    """Turn database rows into (label, value) autocomplete pairs, newest row first."""
    needle = (query or "").strip().lower()
    choices: list[tuple[str, str]] = []
    for row in rows:
        value = _row_text(row, value_key)
        if not value:
            continue
        parts = [text for text in (_row_text(row, key) for key in label_keys) if text]
        label = " • ".join(parts) or value
        if needle and needle not in label.lower() and needle not in value.lower():
            continue
        choices.append((label[:100], value))
        if len(choices) >= max(1, min(limit, 25)):
            break
    return choices
