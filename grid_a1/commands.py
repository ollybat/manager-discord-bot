"""Standalone Grid A1 application commands."""
from __future__ import annotations
import os, time, platform
from typing import Optional
import discord
from discord import app_commands
from .embeds import embed
IMAGE_EXTENSIONS={".jpg",".jpeg",".png",".gif",".webp"}
class OwnerConfigurationError(app_commands.CheckFailure): pass
class OwnerOnlyError(app_commands.CheckFailure): pass
def _validate_image(a:Optional[discord.Attachment])->Optional[str]:
    if a is None:return None
    content=(a.content_type or "").lower(); ext=os.path.splitext(a.filename.lower())[1]
    if not content.startswith("image/"): return f"The attachment is not an image (content type: `{content or 'unknown'}`)."
    if ext not in IMAGE_EXTENSIONS:return "The attachment must use a .jpg, .jpeg, .png, .gif, or .webp extension."
    return None
def register_commands(bot)->None:
    if getattr(bot,"_grid_a1_commands_registered",False):return
    bot._grid_a1_commands_registered=True
    @bot.tree.command(name="help",description="Open the Grid A1 command center")
    async def help_command(i):
        e=embed("💜 Grid A1 • Command Center","✨ A concise, permission-aware guide to the commands available in this bot.",discord.Colour.from_rgb(177,77,255))
        e.add_field(name="🌐 Everyone",value="`/help` — This guide\n`/info server` — Server overview\n`/embed` — Post a custom embed\n`/anti-links` — Configure external link protection (Manage Server)",inline=False)
        if not i.guild or not isinstance(i.user,discord.Member):
            e.set_footer(text="Use commands inside a server for permission-aware sections."); return await i.response.send_message(embed=e,ephemeral=True)
        m=i.user; c=bot.database.config(i.guild.id); owner=m.id==i.guild.owner_id or m.id==bot.settings_owner_id
        access={int(c[k]) for k in ("owner_role","co_owner_role") if c and c[k]}; dashboard=bool({r.id for r in m.roles}&access)
        admin=owner or m.guild_permissions.administrator or m.guild_permissions.manage_guild
        staff=admin or m.guild_permissions.manage_channels or bool({r.id for r in m.roles}&set(bot.database.staff_role_ids(i.guild.id)+bot.database.configured_permission_role_ids(i.guild.id)))
        if dashboard:e.add_field(name="🎛️ Owner dashboard",value="`/dashboard` — Private master overview, module drawer, refresh, and safe configuration\n`/setup roles owner_role: ... co_owner_role: ... head_admin_role: ... admin_role: ... moderator_role: ...` — Configure the five staff roles (server owner only)\nAccess: members holding the configured owner or co-owner role only.",inline=False)
        if admin:e.add_field(name="🛡️ Safety & moderation",value="`/anti-links` — Block websites and Discord invites\nConfigure enabled, action, log channel, whitelist domains, bypass roles, and allowed link roles. The bot needs Message Content Intent and Manage Messages.",inline=False)
        if admin:e.add_field(name="⚙️ Setup & community",value="`/setup tickets` — Configure and publish support panel\n`/setup staff` — Add/remove ticket notification roles\n`/setup welcomer` — Configure welcome channels\n`/verifypanel` — Publish verification panel\n`/staff` — View notification roles\n`/roles setchannel` — Post role directory\n`/wipefeed enable` / `/wipefeed send` — Manage announcements\n`/welcomer preview` / `/welcomer test` — Preview or test welcome",inline=False)
        if staff:e.add_field(name="🛡️ Staff tools",value="`/ticket claim` / `/ticket transfer` — Assign tickets\n`/ticket remove` / `/ticket requestclose` / `/ticket close` — Manage tickets\n`/kick` `/ban` `/warn` `/timeout` — Moderation\n`!lock` / `!unlock` — Lock or unlock a channel",inline=False)
        if owner:e.add_field(name="👑 Bot owner",value="`/ping` — Private diagnostics\n`/sync` — Explicit command synchronization",inline=False)
        e.set_footer(text="Protected sections appear only when your current access permits them."); await i.response.send_message(embed=e,ephemeral=True)
    @bot.tree.command(name="ping",description="Show detailed bot diagnostics (owner only)")
    async def ping_command(i):
        if bot.settings_owner_id is None or i.user.id!=bot.settings_owner_id:return await i.response.send_message("🔒 This diagnostic command is owner-only.",ephemeral=True)
        uptime=max(0,int(time.time()-getattr(bot,"started_at",time.time()))); d,rem=divmod(uptime,86400); h,rem=divmod(rem,3600); mi,s=divmod(rem,60); gateway=round(bot.latency*1000) if bot.latency>=0 else None
        e=embed("💜 Grid A1 • Owner Diagnostics","🔍 Private health report.",discord.Colour.from_rgb(177,77,255)); e.add_field(name="🟢 Status",value="`Online and responding`",inline=True); e.add_field(name="🏓 Gateway",value=f"`{gateway} ms`" if gateway is not None else "`Unavailable`",inline=True); e.add_field(name="⏱️ Uptime",value=f"`{d}d {h}h {mi}m {s}s`",inline=True); e.add_field(name="🌐 Servers",value=f"`{len(bot.guilds)}`",inline=True); e.add_field(name="🧩 Runtime",value=f"Python `{platform.python_version()}`\ndiscord.py `{discord.__version__}`",inline=True); e.set_footer(text="No tokens or secret environment values are displayed."); await i.response.send_message(embed=e,ephemeral=True)
    @bot.tree.command(name="embed",description="Post a custom embed with an optional image")
    @app_commands.describe(title="Embed title",description="Embed description",image="Optional image attachment")
    async def embed_command(i,title:str,description:str,image:Optional[discord.Attachment]=None):
        if (err:=_validate_image(image)):return await i.response.send_message(f"❌ {err}",ephemeral=True)
        e=embed(title[:256],description[:4096]); f=await image.to_file() if image else None
        if f:e.set_image(url=f"attachment://{f.filename}")
        await i.response.send_message(embed=e,file=f)
    @bot.tree.command(name="sync",description="Sync application commands (owner only)")
    async def sync_command(i):
        raw=os.getenv("OWNER_ID","").strip()
        if not raw:raise OwnerConfigurationError("OWNER_ID is not configured; /sync is unavailable.")
        try:owner=int(raw)
        except ValueError:raise OwnerConfigurationError("OWNER_ID is not a valid Discord user ID.") from None
        if i.user.id!=owner:raise OwnerOnlyError("Only the configured bot owner can use /sync.")
        out=await bot.sync_commands_on_request(); await i.response.send_message(embed=embed("💜 Commands synchronized","✅ "+"\n".join(out)),ephemeral=True)
    @bot.tree.command(name="embed-edit",description="Edit a bot-authored embed in this channel")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def edit_command(i,message_id:str,title:Optional[str]=None,description:Optional[str]=None,image:Optional[discord.Attachment]=None):
        if not i.channel or not hasattr(i.channel,"fetch_message"):return await i.response.send_message("❌ Use this in a message channel.",ephemeral=True)
        try:m=await i.channel.fetch_message(int(message_id))
        except (ValueError,discord.NotFound,discord.Forbidden):return await i.response.send_message("❌ Message ID is invalid, missing, or inaccessible.",ephemeral=True)
        if not bot.user or m.author.id!=bot.user.id or not m.embeds:return await i.response.send_message("❌ I can only edit bot-authored messages that contain an embed.",ephemeral=True)
        if (err:=_validate_image(image)):return await i.response.send_message(f"❌ {err}",ephemeral=True)
        e=discord.Embed.from_dict(m.embeds[0].to_dict())
        if title is not None:e.title=title[:256]
        if description is not None:e.description=description[:4096]
        f=await image.to_file() if image else None
        if f:e.set_image(url=f"attachment://{f.filename}")
        await m.edit(embed=e,attachments=[f] if f else discord.utils.MISSING); await i.response.send_message("✅ Embed updated.",ephemeral=True)
