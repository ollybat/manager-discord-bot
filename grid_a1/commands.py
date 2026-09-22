"""Standalone Grid A1 application commands."""
from __future__ import annotations
import os
import time
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
        e=embed("Grid A1 command guide","Available slash commands for this bot.");e.add_field(name="General",value="`/help` — Guide\n`/rules` — Editable defaults\n`/ping` — Latency",inline=False);e.add_field(name="Embeds",value="`/embed` — Post custom embed\n`/embed-edit` — Edit bot embed",inline=False);e.add_field(name="Owner",value="`/sync` — Sync commands (OWNER_ID)",inline=False);await i.response.send_message(embed=e,ephemeral=True)
    @bot.tree.command(name="rules",description="Show editable default community rules")
    async def rules_command(i):
        rs=["Treat members and staff with respect. Harassment, threats, and hate speech are not allowed.","Do not spam, flood channels, mass-mention people, or abuse support tickets.","Use the correct channel and keep ticket reports clear and useful.","Do not post unauthorised adverts, invite links, scams, or suspicious files.","For reports, include Discord usernames, message links, times, and screenshots when available.","If you disagree with staff, use a private ticket or appeal instead of arguing publicly.","Follow Discord's Terms of Service and Community Guidelines."];e=embed("Grid A1 rules","These rules apply to this Rust Console Manager Discord only.");e.add_field(name="Default rules (editable)",value="\n".join(f"**{n}.** {r}" for n,r in enumerate(rs,1)),inline=False);e.add_field(name="Scope",value="These rules cover Discord messages, channels, members, tickets, and staff actions. Grid A1 does not make or enforce rules for conduct inside the game.",inline=False);e.set_footer(text="Grid A1 • Manager • Discord moderation only");await i.response.send_message(embed=e)
    @bot.tree.command(name="ping",description="Show the bot gateway latency")
    async def ping_command(i):
        gateway=round(bot.latency*1000) if bot.latency>=0 else None
        received=max(0,round((time.time()-i.created_at.timestamp())*1000))
        e=embed("Grid A1 status","Connected to Discord.",discord.Colour.green());e.add_field(name="Gateway latency",value=f"`{gateway} ms`" if gateway is not None else "`Unavailable`",inline=True);e.add_field(name="Interaction",value=f"`{received} ms`",inline=True);e.add_field(name="Status",value="🟢 Ready",inline=True);e.set_footer(text="Grid A1 • Manager");await i.response.send_message(embed=e)
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
