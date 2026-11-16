"""Wipe scheduling: storage helpers, embeds, the /wipe config panel, and posting."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from .embeds import embed
from .stats import parse_time, timestamp_marker
from .utils import utcnow

log = logging.getLogger(__name__)

MAX_TITLE = 120
MAX_REGION = 24
FIELD_BUDGET = 980
RELATIVE_PATTERN = re.compile(r"^\+(?P<amount>\d{1,4})\s*(?P<unit>m|h|d|w)$", re.IGNORECASE)
STAMP_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<hour>\d{1,2}):(?P<minute>\d{2})$")
REGION_PATTERN = re.compile(r"^[A-Za-z0-9 ._-]{1,24}$")
REGION_EMOJI = {"EU": "🇪🇺", "NA": "🌎", "AS": "🌏", "OCE": "🌊", "SA": "🌴", "AF": "🌍"}
UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080}
TIME_HELP = "Give a time as a Unix timestamp, as YYYY-MM-DD HH:MM (UTC), or as an offset such as +3h or +2d."


def new_wipe_id() -> str:
    """Short, human-readable identifier for a scheduled wipe."""
    return uuid.uuid4().hex[:8].upper()


def parse_wipe_time(text: str, now: datetime | None = None) -> datetime:
    """Read a wipe time from a timestamp, a UTC date and time, or a relative offset."""
    current = now or utcnow()
    raw = " ".join((text or "").split())
    if not raw:
        raise ValueError(TIME_HELP)
    cleaned = raw
    if cleaned.startswith("<t:"):
        cleaned = cleaned[3:].split(":", 1)[0]
    elif cleaned.startswith("<t") and cleaned.endswith(">"):
        cleaned = cleaned[2:-1].split(":", 1)[0]
    relative = RELATIVE_PATTERN.match(cleaned)
    if relative:
        minutes = int(relative.group("amount")) * UNIT_MINUTES[relative.group("unit").lower()]
        if minutes <= 0:
            raise ValueError("A wipe offset must be at least one minute in the future.")
        return current + timedelta(minutes=minutes)
    stamp = STAMP_PATTERN.match(cleaned)
    if stamp:
        try:
            moment = datetime.strptime(f"{stamp.group('date')} {int(stamp.group('hour')):02d}:{stamp.group('minute')}", "%Y-%m-%d %H:%M")
        except ValueError as error:
            raise ValueError(TIME_HELP) from error
        return moment.replace(tzinfo=timezone.utc)
    if re.fullmatch(r"\d{9,11}", cleaned):
        try:
            return datetime.fromtimestamp(int(cleaned), tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise ValueError("That timestamp is out of range.") from error
    raise ValueError(TIME_HELP)


def wipe_region_emoji(region: str) -> str:
    """Flag for a known region code, and a globe for anything else."""
    return REGION_EMOJI.get((region or "").strip().upper(), "🌍")


def wipe_state(row: Any, now: datetime | None = None) -> str:
    """Short state label: scheduled, due, or announced."""
    if row["announced_at"]:
        return "✅ announced"
    moment = parse_time(row["scheduled_at"])
    if moment and moment <= (now or utcnow()):
        return "⏰ due now"
    return "🗓️ scheduled"


def wipe_line(row: Any, now: datetime | None = None) -> str:
    """One listing line for a wipe."""
    title = discord.utils.escape_markdown(str(row["title"]))[:90]
    return (
        f"{wipe_region_emoji(row['region'])} **{discord.utils.escape_markdown(str(row['region']))}** — {title}\n"
        f"┗ {row['wipe_id']} • {timestamp_marker(row['scheduled_at'])} • {wipe_state(row, now)}"
    )


def wipe_sections(rows: list[Any], now: datetime | None = None, limit: int = 15, budget: int = FIELD_BUDGET) -> tuple[list[str], list[str], int]:
    """Split wipes into upcoming and past lines that fit one embed, plus the hidden count."""
    current = now or utcnow()
    upcoming: list[str] = []
    past: list[str] = []
    hidden = 0
    used = 0
    ordered = sorted(rows, key=lambda row: str(row["scheduled_at"]))
    pending = [row for row in ordered if (parse_time(row["scheduled_at"]) or current) >= current]
    finished = [row for row in reversed(ordered) if (parse_time(row["scheduled_at"]) or current) < current]
    for row in pending + finished:
        if len(upcoming) + len(past) >= limit:
            hidden += 1
            continue
        line = wipe_line(row, current)
        if used + len(line) + 1 > budget:
            hidden += 1
            continue
        used += len(line) + 1
        if (parse_time(row["scheduled_at"]) or current) >= current:
            upcoming.append(line)
        else:
            past.append(line)
    return upcoming, past, hidden


def wipe_announcement(row: Any, *, ping_role_id: int | None = None) -> tuple[str | None, discord.Embed]:
    """Content (an optional role ping) and the public embed for one wipe."""
    moment = parse_time(row["scheduled_at"])
    marker = timestamp_marker(row["scheduled_at"])
    long_form = f"<t:{int(moment.timestamp())}:F>" if moment else "an unreadable time"
    announcement = embed(
        f"{wipe_region_emoji(row['region'])} {str(row['region']).upper()} WIPE ANNOUNCEMENT",
        f"**{discord.utils.escape_markdown(str(row['title']))}**\n\n🕒 {marker}\n📅 {long_form}",
        discord.Colour.from_rgb(177, 77, 255),
    )
    announcement.add_field(name="Region", value=discord.utils.escape_markdown(str(row["region"])), inline=True)
    announcement.add_field(name="Wipe ID", value=str(row["wipe_id"]), inline=True)
    announcement.set_footer(text="Posted by Grid A1 • configure this schedule with /wipe config")
    content = f"<@&{int(ping_role_id)}>" if ping_role_id else None
    return content, announcement


def wipe_panel_embed(guild: Any, config: Any, rows: list[Any], message: str | None = None) -> discord.Embed:
    """Panel describing the wipe announcement settings and the stored schedule."""
    upcoming_count = 0
    due_count = 0
    announced_count = 0
    now = utcnow()
    for row in rows:
        moment = parse_time(row["scheduled_at"])
        if moment and moment > now:
            upcoming_count += 1
            continue
        if row["announced_at"]:
            announced_count += 1
        else:
            due_count += 1
    channel_id = config["wipe_channel"] if config else None
    ping_role = config["wipe_ping_role"] if config else None
    enabled = bool(config and config["wipe_announce_enabled"])
    panel = embed(
        f"🌍 Wipe schedule — {guild.name}",
        message or "Schedule wipes once and let the bot post them, or announce one by hand. Everyone can read the schedule with /wipe list or !wipe.",
        discord.Colour.from_rgb(177, 77, 255),
    )
    panel.add_field(name="Announcement channel", value=f"<#{int(channel_id)}>" if channel_id else "not set", inline=True)
    panel.add_field(name="Ping role", value=f"<@&{int(ping_role)}>" if ping_role else "no ping", inline=True)
    panel.add_field(name="Automatic posting", value="🟢 on" if enabled else "🔴 off", inline=True)
    panel.add_field(
        name="Schedule",
        value=f"🗓️ {upcoming_count} upcoming • ⏰ {due_count} due • ✅ {announced_count} announced",
        inline=False,
    )
    upcoming, past, hidden = wipe_sections(rows, now, limit=8)
    if upcoming:
        panel.add_field(name="Next wipes", value="\n".join(upcoming), inline=False)
    if past:
        panel.add_field(name="Already wiped", value="\n".join(past), inline=False)
    if not upcoming and not past:
        panel.add_field(name="Next wipes", value="Nothing scheduled yet — press **Add wipe** to plan one.", inline=False)
    if hidden:
        panel.set_footer(text=f"…and {hidden} more in /wipe list.")
    else:
        panel.set_footer(text="Wipe times are shown in each member's own timezone.")
    return panel


class AddWipeModal(discord.ui.Modal, title="Schedule a wipe"):
    """Collect a region, a time, and a title for a new wipe."""

    def __init__(self, service: "WipeService", guild_id: int, author_id: int, on_saved):
        super().__init__(timeout=300.0)
        self.service = service
        self.guild_id = guild_id
        self.author_id = author_id
        self._on_saved = on_saved
        self.region_input = discord.ui.TextInput(label="Region", placeholder="EU, NA, AS…", max_length=MAX_REGION, required=True, default="EU")
        self.when_input = discord.ui.TextInput(label="When", placeholder="+3h, 2026-12-01 18:00, or 1790000000", max_length=40, required=True)
        self.title_input = discord.ui.TextInput(label="Title", placeholder="Weekly wipe, Season 4 start…", max_length=MAX_TITLE, required=True)
        self.add_item(self.region_input)
        self.add_item(self.when_input)
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            row = self.service.schedule(
                self.guild_id,
                region=str(self.region_input.value),
                title=str(self.title_input.value),
                when_text=str(self.when_input.value),
                actor_id=interaction.user.id,
            )
        except ValueError as error:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        await self._on_saved(interaction, row)


class WipeDashboardView(discord.ui.View):
    """The /wipe config panel: channel, ping role, automatic posting, and the schedule."""

    def __init__(self, service: "WipeService", guild_id: int, author_id: int, guild: Any):
        super().__init__(timeout=600.0)
        self.service = service
        self.guild_id = guild_id
        self.author_id = author_id
        self.guild = guild
        self.channel_select = discord.ui.ChannelSelect(placeholder="Announcement channel", channel_types=[discord.ChannelType.text], min_values=0, max_values=1, row=0)
        self.channel_select.callback = self._pick_channel
        self.add_item(self.channel_select)
        self.role_select = discord.ui.RoleSelect(placeholder="Ping role (optional)", min_values=0, max_values=1, row=1)
        self.role_select.callback = self._pick_role
        self.add_item(self.role_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("🔒 Only the member who opened this panel can use it. Run /wipe config yourself.", ephemeral=True)
            return False
        return True

    def panel(self, message: str | None = None) -> discord.Embed:
        return wipe_panel_embed(self.guild, self.service.database.config(self.guild_id), self.service.listing(self.guild_id), message)

    async def _redraw(self, interaction: discord.Interaction, message: str | None = None) -> None:
        await interaction.response.edit_message(embed=self.panel(message), view=self)

    async def _pick_channel(self, interaction: discord.Interaction) -> None:
        selected = self.channel_select.values
        if not selected:
            self.service.database.upsert_config(self.guild_id, wipe_channel=None)
            return await self._redraw(interaction, "✅ Announcements will not be posted anywhere until you pick a channel again.")
        channel = selected[0]
        self.service.database.upsert_config(self.guild_id, wipe_channel=int(channel.id))
        await self._redraw(interaction, f"✅ Wipes will be announced in <#{int(channel.id)}>.")

    async def _pick_role(self, interaction: discord.Interaction) -> None:
        selected = self.role_select.values
        if not selected:
            self.service.database.upsert_config(self.guild_id, wipe_ping_role=None)
            return await self._redraw(interaction, "✅ Wipe announcements will go out without pinging a role.")
        role = selected[0]
        self.service.database.upsert_config(self.guild_id, wipe_ping_role=int(role.id))
        await self._redraw(interaction, f"✅ Wipe announcements will ping <@&{int(role.id)}>.")

    @discord.ui.button(label="Automatic posting", style=discord.ButtonStyle.primary, emoji="🔁", row=2)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        config = self.service.database.config(self.guild_id)
        enabled = not bool(config and config["wipe_announce_enabled"])
        self.service.database.upsert_config(self.guild_id, wipe_announce_enabled=int(enabled))
        await self._redraw(interaction, "🟢 Due wipes are posted automatically every few minutes." if enabled else "🔴 Automatic posting is off; use **Post due wipes** or /wipe announce by hand.")

    @discord.ui.button(label="Add wipe", style=discord.ButtonStyle.success, emoji="➕", row=2)
    async def add_wipe(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def saved(source: discord.Interaction, row: Any) -> None:
            moment = parse_time(row["scheduled_at"])
            stamp = f"<t:{int(moment.timestamp())}:F>" if moment else "soon"
            await source.response.edit_message(embed=self.panel(f"✅ Scheduled **{row['title']}** for {row['region']} at {stamp} ({row['wipe_id']})."), view=self)
        await interaction.response.send_modal(AddWipeModal(self.service, self.guild_id, self.author_id, saved))

    @discord.ui.button(label="Post due wipes", style=discord.ButtonStyle.secondary, emoji="📣", row=3)
    async def post_due(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        result = await self.service.announce_due(self.guild, actor_id=interaction.user.id)
        await interaction.edit_original_response(embed=self.panel(result["message"]), view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redraw(interaction, "🔄 Schedule reloaded.")


class WipeService:
    """Schedule, list, cancel, and announce wipes for one guild at a time."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def schedule(self, guild_id: int, *, region: str, title: str, when_text: str, actor_id: int) -> Any:
        """Validate and store a wipe, returning the stored row."""
        moment = parse_wipe_time(when_text)
        region = " ".join((region or "").split()).upper()
        title = " ".join((title or "").split())
        if not REGION_PATTERN.match(region):
            raise ValueError("Region must be 1-24 letters, digits, or spaces, such as EU or NA.")
        if not title:
            raise ValueError("Give the wipe a title so members know what it is.")
        if len(title) > MAX_TITLE:
            raise ValueError(f"Titles are limited to {MAX_TITLE} characters.")
        wipe_id = new_wipe_id()
        self.database.create_wipe(
            wipe_id=wipe_id,
            guild_id=guild_id,
            region=region,
            title=title,
            scheduled_at=moment.isoformat(),
            created_by=actor_id,
            created_at=utcnow().isoformat(),
        )
        stored = self.database.wipe(guild_id, wipe_id)
        if stored is None:
            raise ValueError("The wipe could not be saved. Please try again.")
        return stored

    def listing(self, guild_id: int, limit: int = 50) -> list[Any]:
        return self.database.wipes(guild_id, limit=limit)

    def cancel(self, guild_id: int, wipe_id: str, actor_id: int) -> Any:
        return self.database.remove_wipe(guild_id, wipe_id, actor_id, utcnow().isoformat())

    def due(self, guild_id: int) -> list[Any]:
        return self.database.due_wipes(utcnow().isoformat(), guild_id)

    async def announce_due(self, guild: Any, *, actor_id: int | None = None) -> dict[str, Any]:
        """Post every wipe whose time has passed and that was never announced."""
        rows = self.due(guild.id)
        if not rows:
            return {"posted": 0, "message": "✅ Nothing is due — the schedule is up to date."}
        return await self._post(guild, rows, actor_id=actor_id)

    async def announce_one(self, guild: Any, row: Any, *, actor_id: int | None = None) -> dict[str, Any]:
        """Post one wipe by hand, even when its time has not arrived yet."""
        return await self._post(guild, [row], actor_id=actor_id)

    async def _post(self, guild: Any, rows: list[Any], *, actor_id: int | None = None) -> dict[str, Any]:
        config = self.database.config(guild.id)
        channel_id = config["wipe_channel"] if config else None
        if not channel_id:
            return {"posted": 0, "message": "⚠️ Pick an announcement channel in this panel first — then I can post wipes."}
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return {"posted": 0, "message": "⚠️ The saved announcement channel no longer exists. Pick a new one in this panel."}
        ping = config["wipe_ping_role"] if config else None
        posted = 0
        for row in rows:
            content, announcement = wipe_announcement(row, ping_role_id=ping)
            try:
                message = await channel.send(content=content, embed=announcement)
            except discord.Forbidden:
                return {"posted": posted, "message": f"❌ I cannot post in {channel.mention}. Check View Channel and Send Messages."}
            except discord.HTTPException:
                log.exception("Wipe announcement failed for %s", row["wipe_id"])
                return {"posted": posted, "message": "❌ Discord refused that post. Please try again shortly."}
            self.database.mark_wipe_announced(
                guild.id,
                row["wipe_id"],
                channel_id=channel.id,
                message_id=message.id,
                when=utcnow().isoformat(),
                actor_id=int(actor_id or 0),
            )
            posted += 1
        detail = "1 wipe" if posted == 1 else f"{posted} wipes"
        return {"posted": posted, "channel": channel, "message": f"📣 Posted {detail} in {channel.mention}."}


async def announce_for_guilds(bot: Any) -> int:
    """Background pass: post due wipes for every guild that enabled automatic posting."""
    posted = 0
    service = WipeService(bot.database)
    for guild in list(bot.guilds):
        try:
            config = bot.database.config(guild.id)
        except Exception:
            log.debug("Wipe announcer could not read config for guild %s", guild.id, exc_info=True)
            continue
        if not config or not config["wipe_announce_enabled"]:
            continue
        result = await service.announce_due(guild)
        posted += int(result.get("posted", 0))
    return posted
