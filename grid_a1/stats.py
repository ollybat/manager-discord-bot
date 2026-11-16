"""Read-only ticket workload statistics for the staff statistics command."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database
from .utils import DEFAULT_PRIORITY, PRIORITIES, PRIORITY_LABELS, utcnow

RECENT_WINDOW_DAYS = 7
MONTH_WINDOW_DAYS = 30
CLAIM_ACTIONS = ("ticket_claimed", "ticket_reassigned")
MAX_LEADERBOARD = 5
OPEN_STATUSES = ("open", "close_requested")


@dataclass(frozen=True)
class TicketStats:
    """One guild's support workload, ready to render in an embed."""

    open_total: int = 0
    claimed: int = 0
    unclaimed: int = 0
    close_requested: int = 0
    closed_recent: int = 0
    closed_month: int = 0
    average_claim_minutes: float | None = None
    average_close_minutes: float | None = None
    regions: tuple[tuple[str, int], ...] = ()
    priorities: tuple[tuple[str, int], ...] = ()
    top_claimers: tuple[tuple[int, int, float | None], ...] = ()
    rating_average: float | None = None
    rating_count: int = 0


def parse_time(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp, treating a naive value as UTC."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def minutes_between(start: Any, end: Any) -> float | None:
    """Whole minutes between two stored times, or None when either is unusable."""
    first, second = parse_time(start), parse_time(end)
    if first is None or second is None:
        return None
    return max(0.0, (second - first).total_seconds() / 60)


def average(values: Iterable[float | None]) -> float | None:
    """Average of the usable values, or None when there are none."""
    usable = [value for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def format_duration(minutes: float | None) -> str:
    """Compact duration such as `2h 15m`, or an em dash when nothing was measured."""
    if minutes is None:
        return "—"
    total = max(0, round(minutes))
    if total < 60:
        return f"{total}m"
    hours, remainder = divmod(total, 60)
    if hours < 24:
        return f"{hours}h {remainder}m"
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d {remaining_hours}h"


PRIORITY_ORDER = {
    priority: index for index, priority in enumerate(reversed(PRIORITIES))
}
_MEMBER_PREFIX = re.compile(r"^member=\d+;?\s*")


def relative_age(timestamp: Any, now: datetime | None = None) -> str:
    """Human age of a stored UTC timestamp, such as '3h 20m'."""
    minutes = minutes_between(timestamp, (now or utcnow()).isoformat())
    return format_duration(minutes) if minutes is not None else "unknown age"


def sort_open_queue(rows: Iterable[Any]) -> list[Any]:
    """Order open tickets by priority (most urgent first) then oldest first."""
    return sorted(
        rows,
        key=lambda row: (
            PRIORITY_ORDER.get(row_value(row, "priority", DEFAULT_PRIORITY), len(PRIORITIES)),
            str(row_value(row, "opened_at", "") or ""),
        ),
    )


def queue_overview(rows: Iterable[Any], limit: int = 15) -> tuple[list[str], int]:
    """One line per open ticket plus the number that did not fit."""
    ordered = sort_open_queue(rows)
    lines: list[str] = []
    for row in ordered[: max(1, limit)]:
        priority = row_value(row, "priority", DEFAULT_PRIORITY)
        claimed = row_value(row, "claimed_by")
        claimer = f"<@{claimed}>" if claimed else "unclaimed"
        label = PRIORITY_LABELS.get(priority, str(priority).title())
        lines.append(
            f"{label} • <#{row_value(row, 'channel_id')}> • {claimer} • "
            f"{relative_age(row_value(row, 'opened_at'))} old"
        )
    return lines, max(0, len(ordered) - len(lines))


def history_detail(metadata: Any, limit: int = 140) -> str:
    """Trim stored audit metadata into one readable moderation detail."""
    text = _MEMBER_PREFIX.sub("", str(metadata or "").strip()) or "no details recorded"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def moderation_history_lines(rows: Iterable[Any], limit: int = 10) -> list[str]:
    """One line per recorded moderation action, newest first."""
    lines: list[str] = []
    for row in list(rows)[: max(1, limit)]:
        actor = row_value(row, "actor_id")
        byline = f" by <@{actor}>" if actor else ""
        lines.append(
            f"**{row_value(row, 'action', 'action')}** • "
            f"{relative_age(row_value(row, 'created_at'))} ago{byline} — "
            f"{history_detail(row_value(row, 'metadata'))}"
        )
    return lines


def note_lines(rows: Iterable[Any], limit: int = 10) -> list[str]:
    """Read internal ticket notes out of their stored audit metadata."""
    lines: list[str] = []
    for row in list(rows)[: max(1, limit)]:
        try:
            payload = json.loads(row_value(row, "metadata", "") or "{}")
        except (TypeError, ValueError):
            payload = {}
        note = str(payload.get("note") or "").strip()
        if not note:
            continue
        lines.append(
            f"<@{row_value(row, 'actor_id')}> • "
            f"{relative_age(row_value(row, 'created_at'))} ago — {note}"
        )
    return lines


def row_value(row: Any, key: str, default: Any = None) -> Any:
    """Read one column from a sqlite3.Row or a plain mapping."""
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return default


def _count(values: Iterable[Any]) -> dict[Any, int]:
    """Count repeated values while ignoring empty ones."""
    counts: dict[Any, int] = {}
    for value in values:
        if value in (None, ""):
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def claim_seconds(events: Iterable[Any]) -> dict[str, Any]:
    """Earliest claim event per ticket, keyed by ticket ID."""
    first: dict[str, Any] = {}
    for event in events:
        ticket_id = event["ticket_id"]
        if ticket_id not in first or event["created_at"] < first[ticket_id]["created_at"]:
            first[ticket_id] = event
    return first


def claim_assignee(event: Any) -> int | None:
    """Staff member recorded as the new assignee of a claim event."""
    try:
        metadata = json.loads(event["metadata"] or "{}")
    except (KeyError, TypeError, ValueError):
        metadata = {}
    assignee = metadata.get("new_assignee") if isinstance(metadata, dict) else None
    if isinstance(assignee, int):
        return assignee
    try:
        actor = event["actor_id"]
    except (IndexError, KeyError, TypeError):
        return None
    return actor if isinstance(actor, int) else None


def build_stats(
    tickets: Sequence[Any],
    claims: Sequence[Any],
    *,
    now: datetime,
    ratings: Sequence[int] = (),
) -> TicketStats:
    """Summarise ticket rows, claim events, and ratings without touching the database."""
    now = parse_time(now) or utcnow()
    recent = now - timedelta(days=RECENT_WINDOW_DAYS)
    month = now - timedelta(days=MONTH_WINDOW_DAYS)
    open_tickets = [row for row in tickets if row["status"] in OPEN_STATUSES]
    closed_tickets = [row for row in tickets if row["status"] == "closed"]
    first_claims = claim_seconds(claims)

    claim_minutes = [
        minutes_between(row["opened_at"], first_claims[row["ticket_id"]]["created_at"])
        for row in tickets
        if row["ticket_id"] in first_claims
    ]
    close_minutes = [minutes_between(row["opened_at"], row["closed_at"]) for row in closed_tickets]
    regions = sorted(
        ((region, count) for region, count in _count(row["region"] for row in open_tickets).items()),
        key=lambda item: (-item[1], str(item[0])),
    )
    priority_counts = _count(row_value(row, "priority", DEFAULT_PRIORITY) for row in open_tickets)
    priorities = tuple((priority, priority_counts.get(priority, 0)) for priority in PRIORITIES)
    opened_by_ticket = {row["ticket_id"]: row["opened_at"] for row in tickets}
    staff_minutes: dict[int, list[float]] = {}
    for event in first_claims.values():
        assignee = claim_assignee(event)
        minutes = minutes_between(opened_by_ticket.get(event["ticket_id"]), event["created_at"])
        if assignee is not None and minutes is not None:
            staff_minutes.setdefault(assignee, []).append(minutes)
    leaderboard = sorted(
        (
            (assignee, count, average(staff_minutes.get(assignee, [])))
            for assignee, count in _count(claim_assignee(event) for event in claims).items()
        ),
        key=lambda item: (-item[1], item[0]),
    )[:MAX_LEADERBOARD]

    return TicketStats(
        open_total=len(open_tickets),
        claimed=sum(1 for row in open_tickets if row["claimed_by"] is not None),
        unclaimed=sum(1 for row in open_tickets if row["claimed_by"] is None),
        close_requested=sum(1 for row in open_tickets if row["status"] == "close_requested"),
        closed_recent=sum(
            1
            for row in closed_tickets
            if (closed := parse_time(row["closed_at"])) is not None and closed >= recent
        ),
        closed_month=sum(
            1
            for row in closed_tickets
            if (closed := parse_time(row["closed_at"])) is not None and closed >= month
        ),
        average_claim_minutes=average(claim_minutes),
        average_close_minutes=average(close_minutes),
        regions=tuple(regions),
        priorities=priorities,
        top_claimers=tuple(leaderboard),
        rating_average=average(float(score) for score in ratings),
        rating_count=len(ratings),
    )


def collect(database: Database, guild_id: int, *, now: datetime | None = None) -> TicketStats:
    """Read one guild's tickets and claim history, then summarise them."""
    with database.connect() as db:
        tickets = db.execute(
            "SELECT ticket_id, status, region, opened_at, closed_at, claimed_by, priority "
            "FROM tickets WHERE guild_id=?",
            (guild_id,),
        ).fetchall()
        claims = db.execute(
            "SELECT ticket_id, actor_id, metadata, MIN(created_at) AS created_at "
            "FROM audit_log WHERE guild_id=? AND action IN (?, ?) "
            "GROUP BY ticket_id, actor_id, metadata",
            (guild_id, *CLAIM_ACTIONS),
        ).fetchall()

    return build_stats(
        tickets,
        claims,
        now=now or utcnow(),
        ratings=database.feedback_scores(guild_id),
    )
