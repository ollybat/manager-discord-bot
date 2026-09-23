from __future__ import annotations

import discord

from .database import Database
from .embeds import embed

def ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1:"st",2:"nd",3:"rd"}.get(number % 10,"th")
    return f"{number}{suffix}"

def missing(guild: discord.Guild, config: discord.Row | None) -> list[str]:
    if not config: return ["Welcomer is not configured. Run `/setup welcomer` first."]
    labels = {"welcome_channel":"Welcome","verify_channel":"Verify","link_channel":"Links","bot_commands_channel":"Bot commands","shop_channel":"Shop"}
    return [f"{label} channel is missing or not a text channel." for key,label in labels.items() if not config[key] or not isinstance(guild.get_channel(config[key]), discord.TextChannel)]

def welcome_embed(bot: discord.Client, guild: discord.Guild, member: discord.Member, config: discord.Row) -> discord.Embed:
    count = guild.member_count or len(guild.members)
    result = embed("🌙 Welcome to the community", f"Welcome {member.mention} — you are our **{ordinal(count)} member**!\n\nWelcome to the community. Start with verification, connect your account, and explore the useful channels below.")
    if bot.user:
        result.set_author(name="Grid A1 • Manager", icon_url=bot.user.display_avatar.url)
    else:
        result.set_author(name="Grid A1 • Manager")
    result.add_field(name="🧭 Server navigation", value=f"✅ **Verify / Server Selector**\nHead to <#{config['verify_channel']}>.\n\n🔗 **Link Your Account**\nLink your account in <#{config['link_channel']}>.\n\n🤖 **Bot Commands**\nUse <#{config['bot_commands_channel']}>.\n\n🛒 **Visit Our Store**\nCheck <#{config['shop_channel']}>.", inline=False)
    result.add_field(name="🎫 Support", value="Open a ticket from the **Grid A1 Support Center** panel. EU support is available.", inline=False)
    result.set_footer(text="💜 Grid A1 • Manager • Welcome to the community")
    return result

async def send_welcome(bot: discord.Client, database: Database, member: discord.Member) -> None:
    config = database.config(member.guild.id); problems = missing(member.guild, config)
    if problems: return
    channel = member.guild.get_channel(config['welcome_channel'])
    if isinstance(channel, discord.TextChannel): await channel.send(embed=welcome_embed(bot, member.guild, member, config))
