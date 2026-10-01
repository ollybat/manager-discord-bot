"""Standalone Grid A1 application commands and their permission-aware help guide."""

from __future__ import annotations

import os
import platform
import time
from typing import Optional

import discord
from discord import app_commands

from .embeds import embed

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})

BASE_SERVER_SETUP_HELP = (
    "`/setup tickets panel_channel logs_channel category inactivity_hours` — "
    "Save ticket locations and publish the support panel.\n"
    "`/setup welcomer welcome_channel link_channel bot_commands_channel "
    "shop_channel verify_channel` — Save community channels.\n"
    "`/welcomer preview` / `/welcomer test` — Preview privately or send a "
    "test greeting."
)
MANAGE_GUILD_HELP = (
    "\n`/verifypanel channel role` — Publish a verification panel; the bot "
    "role must be above the assigned role.\n"
    "`/anti-links enabled action log_channel` — configure enabled, action, and log channel. "
    "The bot needs Message Content Intent and Manage Messages.\n"
    "`/wipefeed enable enabled` / `/wipefeed send timestamp channel` — "
    "Configure and post wipe announcements.\n"
    "`/roles setchannel channel` — Publish the role directory."
)
POLL_SETUP_HELP = (
    "`/poll config [channel] [default_duration_hours]` — View or update the "
    "default channel and duration.\n"
    "`/poll create question options [duration_hours]` — Create a poll with "
    "2–10 choices separated by `|`; duration defaults to 24 hours."
)


class OwnerConfigurationError(app_commands.CheckFailure):
    """Raised when an owner-only command cannot run because OWNER_ID is missing."""


class OwnerOnlyError(app_commands.CheckFailure):
    """Raised when a command is invoked by someone other than the configured owner."""


def _validate_image(attachment: Optional[discord.Attachment]) -> Optional[str]:
    """Validate both the uploaded content type and filename extension."""
    if attachment is None:
        return None

    content_type = (attachment.content_type or "").lower()
    extension = os.path.splitext(attachment.filename.lower())[1]
    if not content_type.startswith("image/"):
        return (
            "The attachment is not an image "
            f"(content type: `{content_type or 'unknown'}`)."
        )
    if extension not in IMAGE_EXTENSIONS:
        return "The attachment must use a .jpg, .jpeg, .png, .gif, or .webp extension."
    return None


def register_commands(bot: discord.Client) -> None:
    """Register this command set once on the bot's shared application-command tree."""
    if getattr(bot, "_grid_a1_commands_registered", False):
        return
    bot._grid_a1_commands_registered = True

    @bot.tree.command(
        name="help",
        description="📚 Open the complete, permission-aware command guide",
    )
    async def help_command(interaction: discord.Interaction) -> None:
        """Show only the command groups the current member can use."""
        guide = embed(
            "💜 Grid A1 • Command Guide",
            "✨ Here’s what you can do. Discord shows each command’s options as you type; "
            "protected actions only work for members with the required access.",
            discord.Colour.from_rgb(177, 77, 255),
        )
        guide.add_field(
            name="🌐 Everyone",
            value=(
                "`/help` — Open this guide\n"
                "`/info server` — View server details\n"
                "`/report member reason proof_link proof_file` — Privately report a member; "
                "reason and HTTP(S) link or upload are optional. Staff must configure a "
                "report channel first.\n"
                "`/poll end poll_id` / `/poll remove poll_id` — manage a poll you created; "
                "server managers can manage any poll."
            ),
            inline=False,
        )

        member = interaction.user
        guild = interaction.guild
        if not guild or not isinstance(member, discord.Member):
            guide.set_footer(
                text="Use commands inside a server to see the sections available to your roles."
            )
            await interaction.response.send_message(embed=guide, ephemeral=True)
            return

        config = bot.database.config(guild.id)
        server_owner = member.id == guild.owner_id
        bot_owner = member.id == bot.settings_owner_id
        owner = server_owner or bot_owner

        dashboard_role_ids = {
            int(config[key])
            for key in ("owner_role", "co_owner_role")
            if config and config[key]
        }
        member_role_ids = {role.id for role in member.roles}
        dashboard_access = server_owner or bool(member_role_ids & dashboard_role_ids)
        manage_guild = (
            server_owner
            or member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
        )
        admin_access = owner or manage_guild
        staff_access = (
            admin_access
            or member.guild_permissions.manage_channels
            or bool(
                member_role_ids
                & set(bot.database.configured_permission_role_ids(guild.id))
            )
        )
        can_manage_messages = (
            server_owner
            or member.guild_permissions.administrator
            or member.guild_permissions.manage_messages
        )

        if dashboard_access:
            guide.add_field(
                name="🎛️ Easy private dashboard",
                value=(
                    "`/dashboard` — Select an area, review its status, then choose "
                    "**Set up section**. The server owner can always open it; configured "
                    "Owner/Co-owner roles can also use it."
                ),
                inline=False,
            )

        if server_owner:
            guide.add_field(
                name="👑 Server owner setup",
                value=(
                    "`/setup roles owner_role co_owner_role head_admin_role admin_role "
                    "moderator_role` — Choose five distinct roles to initialize staff "
                    "access. The server owner can always open `/dashboard`; configured "
                    "Owner/Co-owner roles can open it too."
                ),
                inline=False,
            )

        if admin_access:
            setup_help = BASE_SERVER_SETUP_HELP
            if manage_guild:
                setup_help += MANAGE_GUILD_HELP
            guide.add_field(
                name="⚙️ Server setup & safety",
                value=setup_help,
                inline=False,
            )
            guide.add_field(
                name="📊 Poll setup",
                value=POLL_SETUP_HELP,
                inline=False,
            )

        if can_manage_messages:
            guide.add_field(
                name="📝 Message management",
                value=(
                    "`/embed title description image` — Post a custom bot-branded embed. "
                    "Requires Manage Messages and Send Messages.\n"
                    "`/embed-edit message_id title description image` — Edit a bot-authored "
                    "embed. Requires Manage Messages; title, description, and image are optional."
                ),
                inline=False,
            )

        if staff_access:
            guide.add_field(
                name="🛡️ Staff tools",
                value=(
                    "`/ticket claim` — Claim the current support ticket.\n"
                    "`/ticket transfer target_member` — Assign it to another staff member.\n"
                    "`/ticket remove user` — Remove a member from the ticket.\n"
                    "`/ticket requestclose reason` / `/ticket close reason` — Request or "
                    "complete ticket closure; closure saves a transcript.\n"
                    "`/kick member reason` / `/ban member reason` / `/warn member reason` / "
                    "`/timeout member minutes reason` — Moderation actions. Check role order "
                    "before kick, ban, or timeout.\n"
                    "`!lock` / `!unlock` — Lock or unlock the current channel."
                ),
                inline=False,
            )

        if bot_owner:
            guide.add_field(
                name="🔧 Bot owner",
                value=(
                    "`/ping` — View private latency, uptime, and runtime diagnostics.\n"
                    "`/sync` — Synchronize application commands; cooldown-protected."
                ),
                inline=False,
            )

        guide.set_footer(
            text="Commands keep their existing permission checks. Need access? Ask your server owner."
        )
        await interaction.response.send_message(embed=guide, ephemeral=True)

    @bot.tree.command(
        name="ping",
        description="🏓 View private bot health, latency, and runtime details (owner only)",
    )
    async def ping_command(interaction: discord.Interaction) -> None:
        """Return health information without exposing environment values or tokens."""
        if (
            bot.settings_owner_id is None
            or interaction.user.id != bot.settings_owner_id
        ):
            await interaction.response.send_message(
                "🔒 This diagnostic command is owner-only.",
                ephemeral=True,
            )
            return

        uptime_seconds = max(
            0,
            int(time.time() - getattr(bot, "started_at", time.time())),
        )
        days, remaining = divmod(uptime_seconds, 86_400)
        hours, remaining = divmod(remaining, 3_600)
        minutes, seconds = divmod(remaining, 60)
        gateway_ms = round(bot.latency * 1_000) if bot.latency >= 0 else None

        report = embed(
            "💜 Grid A1 • Owner Diagnostics",
            "🔍 Private health report.",
            discord.Colour.from_rgb(177, 77, 255),
        )
        report.add_field(
            name="🟢 Status",
            value="`Online and responding`",
            inline=True,
        )
        report.add_field(
            name="🏓 Gateway",
            value=f"`{gateway_ms} ms`" if gateway_ms is not None else "`Unavailable`",
            inline=True,
        )
        report.add_field(
            name="⏱️ Uptime",
            value=f"`{days}d {hours}h {minutes}m {seconds}s`",
            inline=True,
        )
        report.add_field(
            name="🌐 Servers",
            value=f"`{len(bot.guilds)}`",
            inline=True,
        )
        report.add_field(
            name="🧩 Runtime",
            value=f"Python `{platform.python_version()}`\ndiscord.py `{discord.__version__}`",
            inline=True,
        )
        report.set_footer(text="No tokens or secret environment values are displayed.")
        await interaction.response.send_message(embed=report, ephemeral=True)

    @bot.tree.command(
        name="embed",
        description="🎨 Post a custom embed with an optional image (Manage Messages)",
    )
    @app_commands.checks.has_permissions(manage_messages=True, send_messages=True)
    @app_commands.describe(
        title="Embed title",
        description="Embed description",
        image="Optional image attachment",
    )
    async def embed_command(
        interaction: discord.Interaction,
        title: str,
        description: str,
        image: Optional[discord.Attachment] = None,
    ) -> None:
        """Post a bounded custom embed, optionally attaching a validated image."""
        validation_error = _validate_image(image)
        if validation_error:
            await interaction.response.send_message(
                f"❌ {validation_error}",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        message_embed = embed(title[:256], description[:4096])
        attachment = await image.to_file() if image else None
        if attachment:
            message_embed.set_image(url=f"attachment://{attachment.filename}")

        await interaction.followup.send(
            embed=message_embed,
            file=attachment,
        )

    @bot.tree.command(
        name="sync",
        description="🔄 Synchronize application commands (bot owner only)",
    )
    async def sync_command(interaction: discord.Interaction) -> None:
        """Run the globally rate-limited application-command synchronization."""
        raw_owner_id = os.getenv("OWNER_ID", "").strip()
        if not raw_owner_id:
            raise OwnerConfigurationError(
                "OWNER_ID is not configured; /sync is unavailable."
            )

        try:
            owner_id = int(raw_owner_id)
        except ValueError:
            raise OwnerConfigurationError(
                "OWNER_ID is not a valid Discord user ID."
            ) from None

        if interaction.user.id != owner_id:
            raise OwnerOnlyError("Only the configured bot owner can use /sync.")

        await interaction.response.defer(ephemeral=True)
        try:
            sync_summary = await bot.sync_commands_on_request()
        except (discord.HTTPException, RuntimeError) as error:
            await interaction.followup.send(
                f"❌ Sync failed: {error}",
                ephemeral=True,
            )
            return

        result = embed(
            "💜 Commands synchronized",
            "✅ " + "\n".join(sync_summary),
        )
        await interaction.followup.send(embed=result, ephemeral=True)

    @bot.tree.command(
        name="embed-edit",
        description="🖊️ Edit a bot-authored embed in this channel",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(
        message_id="Numeric ID of the bot-authored message",
        title="Optional replacement title",
        description="Optional replacement description",
        image="Optional replacement image attachment",
    )
    async def edit_command(
        interaction: discord.Interaction,
        message_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
    ) -> None:
        """Edit only an embed authored by this bot in the current message channel."""
        channel = interaction.channel
        if not channel or not hasattr(channel, "fetch_message"):
            await interaction.response.send_message(
                "❌ Use this in a message channel.",
                ephemeral=True,
            )
            return

        try:
            target_id = int(message_id)
        except ValueError:
            await interaction.response.send_message(
                "❌ Message ID must be a numeric Discord message ID.",
                ephemeral=True,
            )
            return

        validation_error = _validate_image(image)
        if validation_error:
            await interaction.response.send_message(
                f"❌ {validation_error}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            target_message = await channel.fetch_message(target_id)
        except (discord.NotFound, discord.Forbidden):
            await interaction.followup.send(
                "❌ Message is missing or inaccessible.",
                ephemeral=True,
            )
            return

        if (
            not bot.user
            or target_message.author.id != bot.user.id
            or not target_message.embeds
        ):
            await interaction.followup.send(
                "❌ I can only edit bot-authored messages that contain an embed.",
                ephemeral=True,
            )
            return

        updated_embed = discord.Embed.from_dict(target_message.embeds[0].to_dict())
        if title is not None:
            updated_embed.title = title[:256]
        if description is not None:
            updated_embed.description = description[:4096]

        attachment = await image.to_file() if image else None
        if attachment:
            updated_embed.set_image(url=f"attachment://{attachment.filename}")

        await target_message.edit(
            embed=updated_embed,
            attachments=[attachment] if attachment else discord.utils.MISSING,
        )
        await interaction.followup.send("✅ Embed updated.", ephemeral=True)
