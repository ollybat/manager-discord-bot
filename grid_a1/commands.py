"""Standalone Grid A1 application commands."""
from __future__ import annotations
import os
import time
import platform
from typing import Optional
import discord
from discord import app_commands
from .embeds import embed
IMAGE_EXTENSIONS={".jpg",".jpeg",".png",".gif",".webp"}
class OwnerConfigurationError(app_commands.CheckFailure): pass
class OwnerOnlyError(app_commands.CheckFailure): pass
def _validate_image(a:Optional[discord.Attachment])->Optional[str]:
    if a is None:return None
    t=(a.content_type or "").lower() or "unknown"; x=os.path.splitext(a.filename.lower())[1] or "none"
    if not t.startswith("image/"):return f"The attachment is not an image (content type: `{t}`)."
    if x not in IMAGE_EXTENSIONS:return "The attachment must use a .jpg, .jpeg, .png, .gif, or .webp extension."
    return None
def register_commands(bot)->None:
    if getattr(bot,"_grid_a1_commands_registered",False):return
    bot._grid_a1_commands_registered=True
    @bot.tree.command(name="help",description="Show the Grid A1 command guide")
    async def help_command(i):
        e=embed("💜 Grid A1 command guide","✨ Available commands for this Discord server.");e.add_field(name="🌐 General",value="`/help` — Show this guide\n`/ping` — Bot status\n`/verifypanel` — Create verification panel",inline=False);e.add_field(name="🎨 Embeds",value="`/embed` — Post a custom embed\n`/embed-edit` — Edit a bot embed",inline=False);
        if isinstance(i.user, discord.Member) and (i.user.guild_permissions.manage_channels or i.user.guild_permissions.manage_guild): e.add_field(name="🛡️ Staff",value="`/setup tickets` — Configure tickets\n`/setup staff` — Configure staff alerts\n`/setup welcomer` — Configure welcomes\n`/ticket claim` — Claim a ticket\n`/ticket transfer` — Transfer a ticket\n`/ticket requestclose` — Request closure\n`/ticket close` — Archive and close",inline=False)
        e.add_field(name="👑 Owner",value="`/sync` — Sync commands",inline=False);await i.response.send_message(embed=e,ephemeral=True)
    @bot.tree.command(name="ping",description="Show detailed bot diagnostics (owner only)")
    async def ping_command(i):
        if bot.settings_owner_id is None or i.user.id != bot.settings_owner_id:
            return await i.response.send_message("🔒 This diagnostic command is owner-only.", ephemeral=True)
        now = time.time()
        gateway = round(bot.latency * 1000) if bot.latency >= 0 else None
        received = max(0, round((now - i.created_at.timestamp()) * 1000))
        uptime = max(0, int(now - getattr(bot, "started_at", now)))
        days, remainder = divmod(uptime, 86400); hours, remainder = divmod(remainder, 3600); minutes, seconds = divmod(remainder, 60)
        uptime_text = f"{days}d {hours}h {minutes}m {seconds}s" if days else f"{hours}h {minutes}m {seconds}s"
        db_path = getattr(bot.database, "path", None); db_size = db_path.stat().st_size if db_path and db_path.exists() else 0
        e = embed("💜 Grid A1 • Owner Diagnostics", "🔍 Private health report for Grid A1 Manager.", discord.Colour.from_rgb(177, 77, 255))
        e.add_field(name="🟢 Status", value="`Online and responding`", inline=True)
        e.add_field(name="🏓 Gateway", value=f"`{gateway} ms`" if gateway is not None else "`Unavailable`", inline=True)
        e.add_field(name="⚡ Interaction", value=f"`{received} ms`", inline=True)
        e.add_field(name="⏱️ Uptime", value=f"`{uptime_text}`", inline=True)
        e.add_field(name="🌐 Servers", value=f"`{len(bot.guilds)}`", inline=True)
        e.add_field(name="👥 Cached members", value=f"`{sum(g.member_count or 0 for g in bot.guilds):,}`", inline=True)
        e.add_field(name="📚 Cached channels", value=f"`{sum(len(g.channels) for g in bot.guilds):,}`", inline=True)
        e.add_field(name="🗃️ SQLite database", value=f"`{db_size / 1024:.1f} KB`", inline=True)
        e.add_field(name="🧩 Runtime", value=f"Python `{platform.python_version()}`\ndiscord.py `{discord.__version__}`", inline=False)
        e.add_field(name="🔐 Safety", value="No tokens, credentials, or secret environment values are displayed.", inline=False)
        e.set_footer(text="Grid A1 • Private owner diagnostics")
        await i.response.send_message(embed=e, ephemeral=True)
    @bot.tree.command(name="embed",description="Post a custom embed with an optional image")
    @app_commands.describe(title="Embed title",description="Embed description",image="Optional image attachment")
    async def embed_command(i,title:str,description:str,image:Optional[discord.Attachment]=None):
        if (err:=_validate_image(image)):return await i.response.send_message(f"❌ {err}",ephemeral=True)
        e=embed(title[:256],description[:4096]);f=await image.to_file() if image else None
        if f:e.set_image(url=f"attachment://{f.filename}")
        await i.response.send_message(embed=e,file=f)
    @bot.tree.command(name="embed-edit",description="Edit a bot-authored embed in this channel")
    @app_commands.describe(message_id="Message ID",title="Optional replacement title",description="Optional replacement description",image="Optional replacement image")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def edit_command(i,message_id:str,title:Optional[str]=None,description:Optional[str]=None,image:Optional[discord.Attachment]=None):
        if not i.channel or not hasattr(i.channel,"fetch_message"):return await i.response.send_message("❌ Use this in a message channel.",ephemeral=True)
        try:m=await i.channel.fetch_message(int(message_id))
        except ValueError:return await i.response.send_message("❌ Message ID must be numeric.",ephemeral=True)
        except discord.NotFound:return await i.response.send_message("❌ Message not found in this channel.",ephemeral=True)
        except discord.Forbidden:return await i.response.send_message("❌ I cannot read that message.",ephemeral=True)
        if not bot.user or m.author.id!=bot.user.id:return await i.response.send_message("❌ I can only edit messages authored by this bot.",ephemeral=True)
        if not m.embeds:return await i.response.send_message("❌ That message has no embed.",ephemeral=True)
        if (err:=_validate_image(image)):return await i.response.send_message(f"❌ {err}",ephemeral=True)
        e=discord.Embed.from_dict(m.embeds[0].to_dict());
        if title is not None:e.title=title[:256]
        if description is not None:e.description=description[:4096]
        f=await image.to_file() if image else None
        if f:e.set_image(url=f"attachment://{f.filename}")
        try:
            if f: await m.edit(embed=e, attachments=[f])
            else: await m.edit(embed=e)
        except discord.Forbidden:return await i.response.send_message("❌ I cannot edit that message.",ephemeral=True)
        await i.response.send_message("✅ Embed updated.",ephemeral=True)
    @bot.tree.command(name="sync",description="Sync application commands (owner only)")
    async def sync_command(i):
        raw=os.getenv("OWNER_ID","").strip()
        if not raw:raise OwnerConfigurationError("OWNER_ID is not configured; /sync is unavailable.")
        try:owner=int(raw)
        except ValueError:raise OwnerConfigurationError("OWNER_ID is not a valid Discord user ID.") from None
        if i.user.id!=owner:raise OwnerOnlyError("Only the configured bot owner can use /sync.")
        try: out=await bot.sync_commands_on_request()
        except discord.HTTPException as error:
            if error.status==429: raise RuntimeError("Discord rate-limited the sync. Wait before trying /sync again; the cooldown is active.") from error
            raise RuntimeError(f"Discord rejected the sync (HTTP {error.status}).") from error
        await i.response.send_message("✅ Explicitly synced commands — "+"; ".join(out)+". Global sync is never automatic.",ephemeral=True)
