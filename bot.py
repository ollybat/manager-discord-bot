import html
import io
import os
import random
import sqlite3
from datetime import datetime, timezone

import discord
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


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def setup_db():
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS config (guild_id INTEGER PRIMARY KEY, welcome_channel INTEGER, ticket_category INTEGER, ticket_archive INTEGER)")
        cols = {r[1] for r in con.execute("PRAGMA table_info(config)")}
        if "ticket_archive" not in cols:
            con.execute("ALTER TABLE config ADD COLUMN ticket_archive INTEGER")


def make_embed(title, description, colour=discord.Colour.blurple()):
    return discord.Embed(title=title, description=description, colour=colour, timestamp=datetime.now(timezone.utc))


def is_ticket(channel):
    return isinstance(channel, discord.TextChannel) and bool(channel.topic and channel.topic.startswith("manager-ticket:"))


def archive_channel_id(guild_id):
    with db() as con:
        row = con.execute("SELECT ticket_archive FROM config WHERE guild_id=?", (guild_id,)).fetchone()
    return row["ticket_archive"] if row else None


@bot.event
async def on_ready():
    setup_db()
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")


class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def open_ticket(self, interaction, issue):
        guild = interaction.guild
        with db() as con:
            row = con.execute("SELECT ticket_category FROM config WHERE guild_id=?", (guild.id,)).fetchone()
        category = guild.get_channel(row["ticket_category"]) if row and row["ticket_category"] else None
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)}
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        channel = await guild.create_text_channel(f"ticket-{interaction.user.name[:18]}".lower(), category=category, overwrites=overwrites, topic=f"manager-ticket:owner={interaction.user.id};claimed=none")
        await channel.send(embed=make_embed("Support ticket opened", f"{interaction.user.mention}, staff will help shortly.\n**Issue type:** {issue}\nUse `/ticket claim`, `/ticket transfer`, or `/ticket close` here."))
        await interaction.response.send_message(f"Your ticket is ready: {channel.mention}", ephemeral=True)

    @discord.ui.button(label="Player report", style=discord.ButtonStyle.danger, emoji="🚩", custom_id="manager_ticket_report")
    async def report(self, interaction, button): await self.open_ticket(interaction, "Player report")

    @discord.ui.button(label="Server support", style=discord.ButtonStyle.primary, emoji="🛠️", custom_id="manager_ticket_support")
    async def support(self, interaction, button): await self.open_ticket(interaction, "Server support")

    @discord.ui.button(label="Other", style=discord.ButtonStyle.secondary, emoji="🎫", custom_id="manager_ticket_other")
    async def other(self, interaction, button): await self.open_ticket(interaction, "Other")


bot.add_view(TicketPanel())
ticket_group = discord.app_commands.Group(name="ticket", description="Automated support ticket commands")
bot.tree.add_command(ticket_group)


@ticket_group.command(name="setup", description="Deploy the ticket panel")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def ticket_setup(interaction: discord.Interaction, channel: discord.TextChannel, category: discord.CategoryChannel, archive: discord.TextChannel):
    with db() as con:
        con.execute("INSERT INTO config(guild_id,ticket_category,ticket_archive) VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET ticket_category=excluded.ticket_category,ticket_archive=excluded.ticket_archive", (interaction.guild.id, category.id, archive.id))
    await channel.send(embed=make_embed("Support centre", "Choose your issue type below. Your ticket will be private to you and staff."), view=TicketPanel())
    await interaction.response.send_message(f"Ticket panel deployed in {channel.mention}; transcripts go to {archive.mention}.", ephemeral=True)


@ticket_group.command(name="claim", description="Claim the current ticket")
@discord.app_commands.checks.has_permissions(manage_channels=True)
async def ticket_claim(interaction: discord.Interaction):
    if not is_ticket(interaction.channel): return await interaction.response.send_message("Use this inside a ticket.", ephemeral=True)
    await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False)
    await interaction.channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    await interaction.channel.edit(topic=f"{interaction.channel.topic};claimed={interaction.user.id}")
    await interaction.response.send_message(embed=make_embed("Ticket claimed", f"Assigned to {interaction.user.mention}.", discord.Colour.orange()))


@ticket_group.command(name="transfer", description="Transfer the current ticket")
@discord.app_commands.checks.has_permissions(manage_channels=True)
async def ticket_transfer(interaction: discord.Interaction, staff_member: discord.Member):
    if not is_ticket(interaction.channel): return await interaction.response.send_message("Use this inside a ticket.", ephemeral=True)
    await interaction.channel.set_permissions(staff_member, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    await interaction.response.send_message(embed=make_embed("Ticket transferred", f"Handed to {staff_member.mention}."))


async def transcript(channel):
    rows = [m async for m in channel.history(limit=None, oldest_first=True)]
    body = ["<!doctype html><meta charset='utf-8'><title>Ticket transcript</title><style>body{font:15px system-ui;max-width:900px;margin:2rem auto}article{padding:1rem;border-bottom:1px solid #ddd}time{color:#777}</style><h1>Ticket transcript</h1>"]
    for m in rows:
        text = html.escape(m.content or "(no text)").replace("\n", "<br>")
        files = " ".join(f"<a href='{html.escape(a.url, quote=True)}'>{html.escape(a.filename)}</a>" for a in m.attachments)
        body.append(f"<article><b>{html.escape(str(m.author))}</b> <time>{m.created_at.isoformat()}</time><p>{text}<br>{files}</p></article>")
    return "".join(body)


@ticket_group.command(name="close", description="Archive transcript and delete the current ticket")
@discord.app_commands.checks.has_permissions(manage_channels=True)
async def ticket_close(interaction: discord.Interaction, reason: str = "No reason provided"):
    if not is_ticket(interaction.channel): return await interaction.response.send_message("Use this inside a ticket.", ephemeral=True)
    archive = interaction.guild.get_channel(archive_channel_id(interaction.guild.id))
    if not archive: return await interaction.response.send_message("Archive channel is not configured. Run `/ticket setup` again.", ephemeral=True)
    file = discord.File(io.BytesIO((await transcript(interaction.channel)).encode()), filename=f"{interaction.channel.name}-transcript.html")
    await archive.send(embed=make_embed("Ticket archived", f"Closed by {interaction.user.mention}\nReason: {reason}"), file=file)
    await interaction.response.send_message("Transcript archived. Closing ticket…", ephemeral=True)
    await interaction.channel.delete(reason=f"Closed by {interaction.user}: {reason}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    if isinstance(error, commands.MissingPermissions): return await ctx.send("You do not have permission.", delete_after=5)
    raise error


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it outside Discord.")
bot.run(TOKEN)
