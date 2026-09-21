import html
import io
import os
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", "!")
DB = "manager.sqlite3"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

COLOURS = {
    "general": discord.Colour.blurple(),
    "base": discord.Colour.green(),
    "clan": discord.Colour.purple(),
    "shop": discord.Colour.gold(),
    "raid": discord.Colour.red(),
    "bug": discord.Colour.orange(),
}


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def setup_db():
    with db() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS config (
                guild_id INTEGER PRIMARY KEY,
                welcome_channel INTEGER,
                link_channel INTEGER,
                bot_commands_channel INTEGER,
                shop_channel INTEGER,
                ticket_category INTEGER,
                logs_channel INTEGER,
                ticket_archive INTEGER,
                inactivity_hours INTEGER DEFAULT 24
            )"""
        )
        columns = {row[1] for row in con.execute("PRAGMA table_info(config)")}
        additions = {
            "welcome_channel": "INTEGER",
            "link_channel": "INTEGER",
            "bot_commands_channel": "INTEGER",
            "shop_channel": "INTEGER",
            "ticket_category": "INTEGER",
            "logs_channel": "INTEGER",
            "ticket_archive": "INTEGER",
            "inactivity_hours": "INTEGER DEFAULT 24",
        }
        for name, definition in additions.items():
            if name not in columns:
                con.execute(f"ALTER TABLE config ADD COLUMN {name} {definition}")
        # Migrate the original archive setting to the new logs_channel name.
        con.execute("UPDATE config SET logs_channel=ticket_archive WHERE logs_channel IS NULL AND ticket_archive IS NOT NULL")


def make_embed(title, description, colour=discord.Colour.blurple()):
    return discord.Embed(title=title, description=description, colour=colour, timestamp=datetime.now(timezone.utc))


def is_ticket(channel):
    return isinstance(channel, discord.TextChannel) and bool(channel.topic and channel.topic.startswith("manager-ticket:"))


def ticket_value(channel, key):
    if not is_ticket(channel):
        return None
    values = {}
    for part in channel.topic.split(":", 1)[1].split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            values[name] = value
    return values.get(key)


def configured_channels(guild_id):
    with db() as con:
        return con.execute("SELECT * FROM config WHERE guild_id=?", (guild_id,)).fetchone()


async def staff_permissions(guild, member):
    return member.guild_permissions.manage_channels or member.guild_permissions.manage_guild


class TicketDetailsModal(discord.ui.Modal, title="Open a support ticket"):
    details = discord.ui.TextInput(
        label="Tell us what happened",
        placeholder="Include names, times, server details, and any useful context…",
        style=discord.TextStyle.paragraph,
        min_length=5,
        max_length=1500,
        required=True,
    )

    def __init__(self, issue_key, issue_label, region):
        super().__init__()
        self.issue_key = issue_key
        self.issue_label = issue_label
        self.region = region

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket(interaction, self.issue_key, self.issue_label, self.region, self.details.value)


class RegionSelect(discord.ui.Select):
    def __init__(self, issue_key, issue_label):
        self.issue_key = issue_key
        self.issue_label = issue_label
        options = [
            discord.SelectOption(label="EU", value="EU", emoji="🇪🇺", description="European support region"),
            discord.SelectOption(label="NA", value="NA", emoji="🇺🇸", description="North American support region"),
        ]
        super().__init__(placeholder="Choose your region…", min_values=1, max_values=1, options=options, custom_id="luna:ticket:region")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketDetailsModal(self.issue_key, self.issue_label, self.values[0]))


class RegionView(discord.ui.View):
    def __init__(self, issue_key, issue_label):
        super().__init__(timeout=180)
        self.add_item(RegionSelect(issue_key, issue_label))


class TransferModal(discord.ui.Modal, title="Transfer ticket"):
    member_id = discord.ui.TextInput(label="Staff member ID", placeholder="Right-click a staff member → Copy User ID", min_length=5, max_length=25)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_ticket(interaction.channel):
            return await interaction.response.send_message("Use this inside a ticket.", ephemeral=True)
        try:
            member = interaction.guild.get_member(int(self.member_id.value.strip()))
        except ValueError:
            member = None
        if not member:
            return await interaction.response.send_message("I could not find that member in this server.", ephemeral=True)
        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
        await interaction.response.send_message(embed=make_embed("🔁 Ticket transferred", f"This ticket has been handed to {member.mention}."), view=TicketControls())


class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Ticket General", value="general", emoji="📄", description="General requests and questions"),
            discord.SelectOption(label="Ticket Base", value="base", emoji="🏠", description="Questions about your base or area"),
            discord.SelectOption(label="Ticket Clan", value="clan", emoji="👥", description="Clan requests or specific problems"),
            discord.SelectOption(label="Ticket Shop", value="shop", emoji="💎", description="Store and product information"),
            discord.SelectOption(label="Ticket Raid", value="raid", emoji="⚠️", description="Bounty raid or raid-related problems"),
            discord.SelectOption(label="Ticket Bug", value="bug", emoji="🐛", description="Report an in-game or bot bug"),
        ]
        super().__init__(placeholder="Choose what you need help with…", min_values=1, max_values=1, options=options, custom_id="luna:ticket:type")

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        label = next(option.label for option in self.options if option.value == selected)
        await interaction.response.send_message("Choose EU or NA before filling in your questions.", view=RegionView(selected, label), ephemeral=True)


class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())

    @discord.ui.button(label="How it works", style=discord.ButtonStyle.secondary, emoji="❔", custom_id="luna:ticket:help")
    async def how_it_works(self, interaction: discord.Interaction, button):
        await interaction.response.send_message(embed=make_embed("🎫 How support works", "1. Pick an issue type.\n2. Choose EU or NA.\n3. Tell staff what happened.\n4. Keep proof in this private channel.\n5. Staff will claim, transfer, or close it when resolved."), ephemeral=True)


class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="luna:ticket:claim")
    async def claim(self, interaction: discord.Interaction, button):
        if not await staff_permissions(interaction.guild, interaction.user):
            return await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
        if not is_ticket(interaction.channel):
            return await interaction.response.send_message("This button only works inside a ticket.", ephemeral=True)
        await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False)
        await interaction.channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
        await interaction.response.send_message(embed=make_embed("🙋 Ticket claimed", f"Assigned to {interaction.user.mention}.", discord.Colour.orange()))

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.secondary, emoji="🔁", custom_id="luna:ticket:transfer")
    async def transfer(self, interaction: discord.Interaction, button):
        if not await staff_permissions(interaction.guild, interaction.user):
            return await interaction.response.send_message("Only staff can transfer tickets.", ephemeral=True)
        await interaction.response.send_modal(TransferModal())

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="luna:ticket:close")
    async def close(self, interaction: discord.Interaction, button):
        if not await staff_permissions(interaction.guild, interaction.user):
            return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
        await interaction.response.send_modal(CloseTicketModal())


class CloseTicketModal(discord.ui.Modal, title="Close support ticket"):
    reason = discord.ui.TextInput(label="Closing reason", placeholder="Resolved, duplicate, insufficient proof…", max_length=500, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await close_ticket(interaction, self.reason.value)


async def create_ticket(interaction, issue_key, issue_label, region, details):
    guild = interaction.guild
    config = configured_channels(guild.id)
    logs_id = config["logs_channel"] or config["ticket_archive"] if config else None
    if not config or not config["ticket_category"] or not logs_id:
        return await interaction.response.send_message("Tickets are not configured yet. Ask an administrator to run `/setup tickets`.", ephemeral=True)
    existing = next((channel for channel in guild.text_channels if ticket_value(channel, "owner") == str(interaction.user.id)), None)
    if existing:
        return await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
    category = guild.get_channel(config["ticket_category"])
    if not isinstance(category, discord.CategoryChannel):
        return await interaction.response.send_message("The configured ticket category is missing. Run `/setup tickets` again.", ephemeral=True)
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)}
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, attach_files=True)
    channel = await guild.create_text_channel(f"🎫・{region.lower()}-{issue_key}-{interaction.user.name[:12]}".lower(), category=category, overwrites=overwrites, topic=f"manager-ticket:owner={interaction.user.id};issue={issue_key};region={region};claimed=none", reason=f"Ticket opened by {interaction.user}")
    await channel.send(content=interaction.user.mention, embed=make_embed("🎫 Luna Manager support ticket", f"**Region:** {region}\n**Issue:** {issue_label}\n\n**Initial report:**\n{discord.utils.escape_markdown(details)}\n\nA moderator will be with you shortly. Please keep all proof and context in this channel.", COLOURS[issue_key]), view=TicketControls())
    await interaction.response.send_message(f"✅ Your private {region} ticket is ready: {channel.mention}", ephemeral=True)


async def build_transcript(channel):
    messages = [message async for message in channel.history(limit=None, oldest_first=True)]
    parts = ["<!doctype html><meta charset='utf-8'><title>Luna Manager ticket transcript</title><style>body{font:15px system-ui;max-width:900px;margin:2rem auto;background:#f7f7f7}main{background:#fff;padding:2rem}article{padding:1rem;border-bottom:1px solid #ddd}time{color:#777;font-size:.8em}img{max-width:500px;max-height:400px}</style><main><h1>🎫 Luna Manager ticket transcript</h1>"]
    for message in messages:
        content = html.escape(message.content or "(no text)").replace("\n", "<br>")
        attachments = " ".join(f"<a href='{html.escape(a.url, quote=True)}'>{html.escape(a.filename)}</a>" for a in message.attachments)
        images = " ".join(f"<br><img src='{html.escape(a.url, quote=True)}' alt='{html.escape(a.filename, quote=True)}'>" for a in message.attachments if a.content_type and a.content_type.startswith("image/"))
        parts.append(f"<article><b>{html.escape(str(message.author))}</b> <time>{message.created_at.isoformat()}</time><p>{content}<br>{attachments}{images}</p></article>")
    return "".join(parts) + "</main>"


async def close_ticket(interaction, reason):
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("This only works inside a ticket.", ephemeral=True)
    config = configured_channels(interaction.guild.id)
    logs_id = config["logs_channel"] or config["ticket_archive"] if config else None
    archive = interaction.guild.get_channel(logs_id) if logs_id else None
    if not archive:
        return await interaction.response.send_message("The logs channel is missing. Run `/setup tickets` again.", ephemeral=True)
    transcript_file = discord.File(io.BytesIO((await build_transcript(interaction.channel)).encode()), filename=f"{interaction.channel.name}-transcript.html")
    await archive.send(embed=make_embed("📁 Ticket archived", f"**Closed by:** {interaction.user.mention}\n**Reason:** {reason}\n**Region:** {ticket_value(interaction.channel, 'region') or 'unknown'}\n**Issue:** {ticket_value(interaction.channel, 'issue') or 'unknown'}", discord.Colour.dark_grey()), file=transcript_file)
    await interaction.response.send_message("✅ Transcript archived. This ticket will now be deleted.", ephemeral=True)
    await interaction.channel.delete(reason=f"Closed by {interaction.user}: {reason}")


def support_panel_embed(guild):
    tickets = [c for c in guild.text_channels if is_ticket(c)]
    eu = sum(ticket_value(c, "region") == "EU" for c in tickets)
    na = sum(ticket_value(c, "region") == "NA" for c in tickets)
    embed = make_embed("Support Tickets", "Select a support option, then choose EU or NA before filling your questions.")
    embed.add_field(name="Open Tickets (Total)", value=str(len(tickets)), inline=True)
    embed.add_field(name="Open EU Tickets", value=str(eu), inline=True)
    embed.add_field(name="Open NA Tickets", value=str(na), inline=True)
    embed.add_field(name="Response Speed", value="Staff monitored", inline=True)
    embed.add_field(name="Estimated Help Time", value="12 mins", inline=True)
    embed.set_footer(text="Luna • Manager bot")
    return embed


setup_group = app_commands.Group(name="setup", description="Configure Luna Manager bot")
bot.tree.add_command(setup_group)


@setup_group.command(name="tickets", description="Configure ticket channels and deploy the support panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_tickets(interaction: discord.Interaction, panel_channel: discord.TextChannel, logs_channel: discord.TextChannel, category: discord.CategoryChannel, inactivity_hours: app_commands.Range[int, 1, 720]):
    with db() as con:
        con.execute("""INSERT INTO config(guild_id,ticket_category,logs_channel,ticket_archive,inactivity_hours) VALUES(?,?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET ticket_category=excluded.ticket_category,logs_channel=excluded.logs_channel,ticket_archive=excluded.ticket_archive,inactivity_hours=excluded.inactivity_hours""", (interaction.guild.id, category.id, logs_channel.id, logs_channel.id, inactivity_hours))
    await panel_channel.send(embed=support_panel_embed(interaction.guild), view=TicketPanel())
    await interaction.response.send_message(f"✅ Luna Manager ticket panel deployed in {panel_channel.mention}; logs go to {logs_channel.mention}.", ephemeral=True)


@setup_group.command(name="welcomer", description="Configure welcome and community channels")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_welcomer(interaction: discord.Interaction, welcome_channel: discord.TextChannel, link_channel: discord.TextChannel, bot_commands_channel: discord.TextChannel, shop_channel: discord.TextChannel):
    with db() as con:
        con.execute("""INSERT INTO config(guild_id,welcome_channel,link_channel,bot_commands_channel,shop_channel) VALUES(?,?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET welcome_channel=excluded.welcome_channel,link_channel=excluded.link_channel,bot_commands_channel=excluded.bot_commands_channel,shop_channel=excluded.shop_channel""", (interaction.guild.id, welcome_channel.id, link_channel.id, bot_commands_channel.id, shop_channel.id))
    await interaction.response.send_message(f"✅ Welcomer configured: welcome {welcome_channel.mention}, links {link_channel.mention}, bot commands {bot_commands_channel.mention}, shop {shop_channel.mention}.", ephemeral=True)


ticket_group = app_commands.Group(name="ticket", description="Manage support tickets")
bot.tree.add_command(ticket_group)


@ticket_group.command(name="claim", description="Claim the current ticket")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_claim(interaction: discord.Interaction):
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("Use this inside a ticket.", ephemeral=True)
    await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False)
    await interaction.channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    await interaction.response.send_message(embed=make_embed("🙋 Ticket claimed", f"Assigned to {interaction.user.mention}.", discord.Colour.orange()))


@ticket_group.command(name="transfer", description="Transfer the current ticket")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_transfer(interaction: discord.Interaction, staff_member: discord.Member):
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("Use this inside a ticket.", ephemeral=True)
    await interaction.channel.set_permissions(staff_member, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    await interaction.response.send_message(embed=make_embed("🔁 Ticket transferred", f"Handed to {staff_member.mention}."))


@ticket_group.command(name="requestclose", description="Request closure of the current ticket with a reason")
@app_commands.describe(reason="Why this ticket should be closed")
async def ticket_requestclose(interaction: discord.Interaction, reason: str):
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("Use this inside a ticket.", ephemeral=True)
    if not await staff_permissions(interaction.guild, interaction.user):
        return await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
    await close_ticket(interaction, reason)


@ticket_group.command(name="close", description="Archive transcript and delete the current ticket")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_close(interaction: discord.Interaction, reason: str = "No reason provided"):
    await close_ticket(interaction, reason)


@bot.event
async def on_ready():
    setup_db()
    bot.add_view(TicketPanel())
    bot.add_view(TicketControls())
    await bot.tree.sync()
    print(f"Luna Manager bot logged in as {bot.user}")


@bot.event
async def on_app_command_error(interaction, error):
    message = "Something went wrong. Check the bot permissions and try again."
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have permission to use that command."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it outside Discord.")
bot.run(TOKEN)
