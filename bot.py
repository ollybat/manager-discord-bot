"""Grid A1 Discord bot.

All application commands are registered on the global command tree and synced once
from ``setup_hook``.  The bot intentionally keeps its small SQLite database local
so it can be run as a standalone process.
"""

from __future__ import annotations

import html
import io
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", "!")
DB_PATH = Path(os.getenv("DATABASE_PATH", "manager.sqlite3"))
# Set this to the Discord server ID for instant command updates while testing.
# Leave blank for global commands, which can take time to propagate.
TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID", "0") or 0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("grid-a1-manager")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

COLOURS = {
    "general": discord.Colour.blurple(),
    "base": discord.Colour.green(),
    "clan": discord.Colour.purple(),
    "shop": discord.Colour.gold(),
    "raid": discord.Colour.red(),
    "bug": discord.Colour.orange(),
}


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def setup_db() -> None:
    """Create the current schema and migrate older manager-bot databases."""
    with db() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS config (
                guild_id INTEGER PRIMARY KEY,
                welcome_channel INTEGER,
                verify_channel INTEGER,
                link_channel INTEGER,
                bot_commands_channel INTEGER,
                shop_channel INTEGER,
                ticket_category INTEGER,
                logs_channel INTEGER,
                ticket_archive INTEGER,
                panel_channel INTEGER,
                panel_message INTEGER,
                inactivity_hours INTEGER DEFAULT 24
            )"""
        )
        existing = {row[1] for row in connection.execute("PRAGMA table_info(config)")}
        additions = {
            "welcome_channel": "INTEGER",
            "verify_channel": "INTEGER",
            "link_channel": "INTEGER",
            "bot_commands_channel": "INTEGER",
            "shop_channel": "INTEGER",
            "ticket_category": "INTEGER",
            "logs_channel": "INTEGER",
            "ticket_archive": "INTEGER",
            "panel_channel": "INTEGER",
            "panel_message": "INTEGER",
            "inactivity_hours": "INTEGER DEFAULT 24",
        }
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE config ADD COLUMN {name} {definition}")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS closed_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild INTEGER NOT NULL,
                region TEXT NOT NULL,
                issue TEXT NOT NULL,
                closed_by INTEGER NOT NULL,
                reason TEXT NOT NULL,
                closed_at TEXT NOT NULL
            )"""
        )
        # Older releases called the transcript destination ticket_archive.
        connection.execute(
            "UPDATE config SET logs_channel=ticket_archive "
            "WHERE logs_channel IS NULL AND ticket_archive IS NOT NULL"
        )


def configured_channels(guild_id: int) -> sqlite3.Row | None:
    with db() as connection:
        return connection.execute("SELECT * FROM config WHERE guild_id=?", (guild_id,)).fetchone()


def make_embed(title: str, description: str, colour: discord.Colour = discord.Colour.blurple()) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=colour, timestamp=datetime.now(timezone.utc))


def is_ticket(channel: discord.abc.GuildChannel | None) -> bool:
    return isinstance(channel, discord.TextChannel) and bool(
        channel.topic and channel.topic.startswith("manager-ticket:")
    )


def ticket_value(channel: discord.abc.GuildChannel | None, key: str) -> str | None:
    if not is_ticket(channel):
        return None
    values: dict[str, str] = {}
    for part in channel.topic.split(":", 1)[1].split(";"):  # type: ignore[union-attr]
        if "=" in part:
            name, value = part.split("=", 1)
            values[name] = value
    return values.get(key)


def ordinal(number: int) -> str:
    number = int(number)
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def missing_welcomer_channels(guild: discord.Guild, config: sqlite3.Row | None) -> list[str]:
    if not config:
        return ["Welcomer is not configured. Run `/setup welcomer` first."]
    labels = {
        "welcome_channel": "Welcome",
        "verify_channel": "Verify",
        "link_channel": "Links",
        "bot_commands_channel": "Bot commands",
        "shop_channel": "Shop",
    }
    return [
        f"{label} channel is missing or not a text channel (<#{config[key]}>)"
        for key, label in labels.items()
        if not config[key] or not isinstance(guild.get_channel(config[key]), discord.TextChannel)
    ]


def welcomer_error(title: str, problems: list[str]) -> discord.Embed:
    return make_embed(f"⚠️ {title}", "\n".join(f"• {problem}" for problem in problems), discord.Colour.red())


def welcome_embed(guild: discord.Guild, member: discord.Member, config: sqlite3.Row) -> discord.Embed:
    count = guild.member_count or len(guild.members)
    embed = make_embed(
        "🌙 Welcome to Avoid EU 5X",
        f"Welcome {member.mention} — you are our **{ordinal(count)} member**!\n\n"
        "Welcome to the community. Start with verification, choose your server access, and explore the useful channels below.",
    )
    if bot.user:
        embed.set_author(name="Grid A1 • Manager", icon_url=bot.user.display_avatar.url)
    else:
        embed.set_author(name="Grid A1 • Manager")
    embed.add_field(
        name="🧭 Server navigation",
        value=(
            f"✅ **Verify / Server Selector**\nHead to <#{config['verify_channel']}> to get access.\n\n"
            f"🔗 **Link Your Account**\nLink your account in <#{config['link_channel']}>.\n\n"
            f"🤖 **Bot Commands**\nUse <#{config['bot_commands_channel']}> for commands.\n\n"
            f"🛒 **Visit Our Store**\nCheck <#{config['shop_channel']}> for packages and deals."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎫 Support & commands",
        value=("Open a ticket from the **Support Tickets** panel. Select an issue, choose 🇪🇺 EU, then submit details. NA is coming soon.\n\n"
               "`/setup tickets` · `/setup welcomer`\n`/welcomer preview` · `/welcomer test`\n"
               "`/ticket claim` · `/ticket transfer` · `/ticket requestclose` · `/ticket close`"),
        inline=False,
    )
    embed.add_field(name="👥 Community status", value=f"You are member **#{count:,}** of Avoid EU 5X.\nGrid A1 keeps support fast, organized, and friendly.", inline=False)
    embed.set_footer(text="Grid A1 • Manager  •  Community welcome")
    return embed


async def staff_permissions(member: discord.Member) -> bool:
    return member.guild_permissions.manage_channels or member.guild_permissions.manage_guild


async def send_welcome(guild: discord.Guild, member: discord.Member) -> None:
    try:
        config = configured_channels(guild.id)
        problems = missing_welcomer_channels(guild, config)
        if problems:
            log.warning("Welcomer skipped in %s: %s", guild.id, "; ".join(problems))
            return
        channel = guild.get_channel(config["welcome_channel"])  # type: ignore[index]
        await channel.send(embed=welcome_embed(guild, member, config))  # type: ignore[union-attr]
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
        log.error("Welcomer could not send in %s: %s", guild.id, error)
    except Exception:
        log.exception("Unexpected welcomer error in %s", guild.id)


class TicketDetailsModal(discord.ui.Modal, title="Open a support ticket"):
    details = discord.ui.TextInput(label="Tell us what happened", placeholder="Include names, times, server details, and useful proof…", style=discord.TextStyle.paragraph, min_length=5, max_length=1500)

    def __init__(self, issue_key: str, issue_label: str, region: str):
        super().__init__()
        self.issue_key, self.issue_label, self.region = issue_key, issue_label, region

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await create_ticket(interaction, self.issue_key, self.issue_label, self.region, self.details.value)


class RegionSelect(discord.ui.Select):
    def __init__(self, issue_key: str, issue_label: str):
        self.issue_key, self.issue_label = issue_key, issue_label
        super().__init__(placeholder="Choose your region…", options=[discord.SelectOption(label="EU", value="EU", emoji="🇪🇺", description="European support region")], custom_id="grid-a1:ticket:region")

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TicketDetailsModal(self.issue_key, self.issue_label, self.values[0]))


class RegionView(discord.ui.View):
    def __init__(self, issue_key: str, issue_label: str):
        super().__init__(timeout=180)
        self.add_item(RegionSelect(issue_key, issue_label))


class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=f"Ticket {key.title()}", value=key, emoji=emoji, description=desc) for key, emoji, desc in [("general", "📄", "General requests and questions"), ("base", "🏠", "Questions about your base or area"), ("clan", "👥", "Clan requests or specific problems"), ("shop", "💎", "Store and product information"), ("raid", "⚠️", "Raid-related problems"), ("bug", "🐛", "Report an in-game or bot bug")]]
        super().__init__(placeholder="Choose what you need help with…", options=options, custom_id="grid-a1:ticket:type")

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        label = next(option.label for option in self.options if option.value == selected)
        await interaction.response.send_message("Choose EU before filling in your questions. NA is Coming Soon.", view=RegionView(selected, label), ephemeral=True)


class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())

    @discord.ui.button(label="How it works", style=discord.ButtonStyle.secondary, emoji="❔", custom_id="grid-a1:ticket:help")
    async def how_it_works(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=make_embed("🎫 How support works", "1. Pick an issue.\n2. Choose EU.\n3. Explain the issue.\n4. Attach proof.\n5. Staff will help and archive the ticket."), ephemeral=True)


class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="grid-a1:ticket:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await staff_permissions(interaction.user):
            return await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
        if not is_ticket(interaction.channel):
            return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
        await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False)  # type: ignore[union-attr]
        await interaction.channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)  # type: ignore[union-attr]
        await interaction.response.send_message(embed=make_embed("🙋 Ticket claimed", f"Assigned to {interaction.user.mention}.", discord.Colour.orange()))

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="grid-a1:ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await staff_permissions(interaction.user):
            return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
        await interaction.response.send_modal(CloseTicketModal())


class CloseTicketModal(discord.ui.Modal, title="Close support ticket"):
    reason = discord.ui.TextInput(label="Closing reason", placeholder="Resolved, duplicate, insufficient proof…", max_length=500)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await close_ticket(interaction, self.reason.value)


async def create_ticket(interaction: discord.Interaction, issue_key: str, issue_label: str, region: str, details: str) -> None:
    config = configured_channels(interaction.guild.id) if interaction.guild else None
    logs_id = (config["logs_channel"] or config["ticket_archive"]) if config else None
    if not config or not config["ticket_category"] or not logs_id:
        return await interaction.response.send_message("Tickets are not configured yet. Ask an administrator to run `/setup tickets`.", ephemeral=True)
    existing = next((channel for channel in interaction.guild.text_channels if ticket_value(channel, "owner") == str(interaction.user.id)), None)  # type: ignore[union-attr]
    if existing:
        return await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
    category = interaction.guild.get_channel(config["ticket_category"])  # type: ignore[union-attr]
    if not isinstance(category, discord.CategoryChannel):
        return await interaction.response.send_message("The ticket category is missing. Run `/setup tickets` again.", ephemeral=True)
    overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)}  # type: ignore[union-attr]
    if interaction.guild.me:  # type: ignore[union-attr]
        overwrites[interaction.guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, attach_files=True)  # type: ignore[union-attr]
    channel = await interaction.guild.create_text_channel(f"🎫・{region.lower()}-{issue_key}-{interaction.user.name[:12]}".lower(), category=category, overwrites=overwrites, topic=f"manager-ticket:owner={interaction.user.id};issue={issue_key};region={region};claimed=none", reason=f"Ticket opened by {interaction.user}")  # type: ignore[union-attr]
    await channel.send(content=interaction.user.mention, embed=make_embed("🎫 Grid A1 support ticket", f"**Region:** {region}\n**Issue:** {issue_label}\n\n**Initial report:**\n{discord.utils.escape_markdown(details)}\n\nA moderator will be with you shortly.", COLOURS[issue_key]), view=TicketControls())
    await interaction.response.send_message(f"✅ Your private {region} ticket is ready: {channel.mention}", ephemeral=True)


async def build_transcript(channel: discord.TextChannel) -> str:
    parts = ["<!doctype html><meta charset='utf-8'><title>Grid A1 transcript</title><main><h1>🎫 Grid A1 ticket transcript</h1>"]
    async for message in channel.history(limit=None, oldest_first=True):
        content = html.escape(message.content or "(no text)").replace("\n", "<br>")
        attachments = " ".join(f"<a href='{html.escape(a.url, quote=True)}'>{html.escape(a.filename)}</a>" for a in message.attachments)
        parts.append(f"<article><b>{html.escape(str(message.author))}</b> <time>{message.created_at.isoformat()}</time><p>{content}<br>{attachments}</p></article>")
    return "<style>body{font:15px system-ui;max-width:900px;margin:2rem auto}article{padding:1rem;border-bottom:1px solid #ddd}time{color:#777}</style>" + "".join(parts) + "</main>"


async def close_ticket(interaction: discord.Interaction, reason: str) -> None:
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
    config = configured_channels(interaction.guild.id)  # type: ignore[union-attr]
    logs_id = (config["logs_channel"] or config["ticket_archive"]) if config else None
    archive = interaction.guild.get_channel(logs_id) if logs_id else None  # type: ignore[union-attr]
    if not isinstance(archive, discord.TextChannel):
        return await interaction.response.send_message("The logs channel is missing. Run `/setup tickets` again.", ephemeral=True)
    transcript = await build_transcript(interaction.channel)  # type: ignore[arg-type]
    region = ticket_value(interaction.channel, "region") or "unknown"
    issue = ticket_value(interaction.channel, "issue") or "unknown"
    with db() as connection:
        connection.execute("INSERT INTO closed_tickets(guild,region,issue,closed_by,reason,closed_at) VALUES(?,?,?,?,?,?)", (interaction.guild.id, region, issue, interaction.user.id, reason, datetime.now(timezone.utc).isoformat()))  # type: ignore[union-attr]
    await archive.send(embed=make_embed("📁 Ticket archived", f"**Closed by:** {interaction.user.mention}\n**Reason:** {reason}\n**Region:** {region}"), file=discord.File(io.BytesIO(transcript.encode()), filename=f"{interaction.channel.name}-transcript.html"))  # type: ignore[union-attr]
    await interaction.response.send_message("✅ Transcript archived. This ticket will now be deleted.", ephemeral=True)
    await interaction.channel.delete(reason=f"Closed by {interaction.user}: {reason}")  # type: ignore[union-attr]


def support_panel_embed(guild: discord.Guild) -> discord.Embed:
    tickets = [channel for channel in guild.text_channels if is_ticket(channel)]
    eu = sum(ticket_value(channel, "region") == "EU" for channel in tickets)
    na = sum(ticket_value(channel, "region") == "NA" for channel in tickets)
    with db() as connection:
        closed = connection.execute("SELECT COUNT(*) FROM closed_tickets WHERE guild=?", (guild.id,)).fetchone()[0]
    embed = make_embed("Support Tickets", "Select a support option, then choose EU before filling your questions.\n\nNA: Coming Soon.")
    for name, value in (("Open Tickets (Total)", len(tickets)), ("Open EU Tickets", eu), ("Open NA Tickets", 0), ("Response Speed", "Fast"), ("Estimated Help Time", "12 mins"), ("Closed Tickets", closed)):
        embed.add_field(name=name, value=str(value), inline=True)
    embed.set_footer(text="Grid A1 • Manager")
    return embed


class GridA1Bot(commands.Bot):
    _views_registered = False

    @tasks.loop(seconds=60)
    async def refresh_panels(self) -> None:
        for guild in self.guilds:
            try:
                config = configured_channels(guild.id)
                if not config or not config["panel_channel"] or not config["panel_message"]:
                    continue
                channel = guild.get_channel(config["panel_channel"])
                if not isinstance(channel, discord.TextChannel):
                    log.error("Grid A1 panel channel missing in guild %s", guild.id)
                    continue
                message = await channel.fetch_message(config["panel_message"])
                await message.edit(embed=support_panel_embed(guild), view=TicketPanel())
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
                log.error("Grid A1 panel refresh failed in guild %s: %s", guild.id, error)
            except Exception:
                log.exception("Unexpected Grid A1 panel refresh error in guild %s", guild.id)

    @refresh_panels.before_loop
    async def before_refresh_panels(self) -> None:
        await self.wait_until_ready()

    async def setup_hook(self) -> None:
        setup_db()
        if not self._views_registered:
            self.add_view(TicketPanel())
            self.add_view(TicketControls())
            self._views_registered = True
        self.refresh_panels.start()
        try:
            if TEST_GUILD_ID:
                guild = discord.Object(id=TEST_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                guild_synced = await self.tree.sync(guild=guild)
                log.info("Test-guild command sync complete for %s: %d command(s)", TEST_GUILD_ID, len(guild_synced))
            synced = await self.tree.sync()
            log.info("Global application-command sync complete: %d command(s)", len(synced))
        except Exception:
            log.exception("Global application-command sync failed")
            raise


bot = GridA1Bot(command_prefix=PREFIX, intents=intents, help_command=None)

setup_group = app_commands.Group(name="setup", description="Configure Grid A1 bot")
welcomer_group = app_commands.Group(name="welcomer", description="Preview and test welcome messages")
ticket_group = app_commands.Group(name="ticket", description="Manage support tickets")
bot.tree.add_command(setup_group)
bot.tree.add_command(welcomer_group)
bot.tree.add_command(ticket_group)


@setup_group.command(name="tickets", description="Configure ticket channels and deploy the support panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_tickets(interaction: discord.Interaction, panel_channel: discord.TextChannel, logs_channel: discord.TextChannel, category: discord.CategoryChannel, inactivity_hours: app_commands.Range[int, 1, 720]) -> None:
    with db() as connection:
        connection.execute("INSERT INTO config(guild_id,ticket_category,logs_channel,ticket_archive,inactivity_hours) VALUES(?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET ticket_category=excluded.ticket_category,logs_channel=excluded.logs_channel,ticket_archive=excluded.ticket_archive,inactivity_hours=excluded.inactivity_hours", (interaction.guild.id, category.id, logs_channel.id, logs_channel.id, inactivity_hours))  # type: ignore[union-attr]
    panel_message = await panel_channel.send(embed=support_panel_embed(interaction.guild), view=TicketPanel())  # type: ignore[arg-type]
    with db() as connection:
        connection.execute("UPDATE config SET panel_channel=?, panel_message=? WHERE guild_id=?", (panel_channel.id, panel_message.id, interaction.guild.id))  # type: ignore[union-attr]
    await interaction.response.send_message(f"✅ Ticket panel deployed in {panel_channel.mention}; logs go to {logs_channel.mention}.", ephemeral=True)


@setup_group.command(name="welcomer", description="Configure welcome and community channels")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_welcomer(interaction: discord.Interaction, welcome_channel: discord.TextChannel, link_channel: discord.TextChannel, bot_commands_channel: discord.TextChannel, shop_channel: discord.TextChannel, verify_channel: discord.TextChannel) -> None:
    with db() as connection:
        connection.execute("INSERT INTO config(guild_id,welcome_channel,verify_channel,link_channel,bot_commands_channel,shop_channel) VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET welcome_channel=excluded.welcome_channel,verify_channel=excluded.verify_channel,link_channel=excluded.link_channel,bot_commands_channel=excluded.bot_commands_channel,shop_channel=excluded.shop_channel", (interaction.guild.id, welcome_channel.id, verify_channel.id, link_channel.id, bot_commands_channel.id, shop_channel.id))  # type: ignore[union-attr]
    await interaction.response.send_message(f"✅ Welcomer configured for {welcome_channel.mention}.", ephemeral=True)


@welcomer_group.command(name="preview", description="Preview the configured welcome message")
@app_commands.checks.has_permissions(manage_guild=True)
async def welcomer_preview(interaction: discord.Interaction) -> None:
    config = configured_channels(interaction.guild.id)  # type: ignore[union-attr]
    problems = missing_welcomer_channels(interaction.guild, config)  # type: ignore[arg-type]
    if problems:
        return await interaction.response.send_message(embed=welcomer_error("Welcomer is not set up", problems), ephemeral=True)
    await interaction.response.send_message(embed=welcome_embed(interaction.guild, interaction.user, config), ephemeral=True)  # type: ignore[arg-type]


@welcomer_group.command(name="test", description="Send a welcome test to the configured welcome channel")
@app_commands.checks.has_permissions(manage_guild=True)
async def welcomer_test(interaction: discord.Interaction) -> None:
    config = configured_channels(interaction.guild.id)  # type: ignore[union-attr]
    problems = missing_welcomer_channels(interaction.guild, config)  # type: ignore[arg-type]
    if problems:
        return await interaction.response.send_message(embed=welcomer_error("Welcome test unavailable", problems), ephemeral=True)
    channel = interaction.guild.get_channel(config["welcome_channel"])  # type: ignore[union-attr]
    perms = channel.permissions_for(interaction.guild.me) if interaction.guild.me else None  # type: ignore[union-attr]
    if not perms or not perms.view_channel or not perms.send_messages or not perms.embed_links:
        return await interaction.response.send_message(embed=welcomer_error("Welcome permissions missing", [f"Need View Channel, Send Messages, and Embed Links in {channel.mention}."]), ephemeral=True)
    try:
        await channel.send(embed=welcome_embed(interaction.guild, interaction.user, config))  # type: ignore[arg-type]
        await interaction.response.send_message(embed=make_embed("✅ Welcome test sent", f"Sent to {channel.mention}.", discord.Colour.green()), ephemeral=True)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
        await interaction.response.send_message(embed=welcomer_error("Welcome test failed", [str(error)]), ephemeral=True)


@ticket_group.command(name="claim", description="Claim the current ticket")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_claim(interaction: discord.Interaction) -> None:
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
    await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False)  # type: ignore[union-attr]
    await interaction.channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)  # type: ignore[union-attr]
    await interaction.response.send_message(embed=make_embed("🙋 Ticket claimed", f"Assigned to {interaction.user.mention}.", discord.Colour.orange()))


@ticket_group.command(name="transfer", description="Transfer the current ticket")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_transfer(interaction: discord.Interaction, staff_member: discord.Member) -> None:
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
    await interaction.channel.set_permissions(staff_member, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)  # type: ignore[union-attr]
    await interaction.response.send_message(embed=make_embed("🔁 Ticket transferred", f"Handed to {staff_member.mention}."))


@ticket_group.command(name="close", description="Archive transcript and delete the current ticket")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_close(interaction: discord.Interaction, reason: str = "No reason provided") -> None:
    await close_ticket(interaction, reason)


@ticket_group.command(name="requestclose", description="Request closure of the current ticket with a reason")
async def ticket_requestclose(interaction: discord.Interaction, reason: str) -> None:
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
    if not await staff_permissions(interaction.user):
        return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
    await close_ticket(interaction, reason)


@bot.event
async def on_member_join(member: discord.Member) -> None:
    await send_welcome(member.guild, member)


@bot.event
async def on_ready() -> None:
    log.info("Grid A1 bot logged in as %s", bot.user)


@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.exception("Application command failed: %s", error)
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have permission to use that command."
    elif isinstance(error, app_commands.CommandInvokeError):
        message = "Something went wrong while running that command. Check setup and bot permissions."
    else:
        message = "That command could not be completed. Check the command arguments and bot permissions."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it outside Discord.")
    bot.run(TOKEN)
