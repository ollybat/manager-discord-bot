"""Read-only ticket workload statistics for the staff statistics command."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database
from .utils import DEFAULT_PRIORITY, PRIORITIES, utcnow

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
