"""Standalone Grid A1 application commands and their permission-aware help guide."""

from __future__ import annotations

import os
import platform
import time
from typing import Optional

import discord
from discord import app_commands

from .embeds import embed
from .guides import (
    catalog,
    command_detail,
    match_entries,
    prefix_guide_lines,
    render_guide,
    viewer_access,
)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


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
        description="📚 Browse every command you can use, with usage and access",
    )
    @app_commands.describe(
        command="Leave empty for the full guide, or pick one command for details"
    )
    async def help_command(
        interaction: discord.Interaction,
        command: str | None = None,
    ) -> None:
        """Render the live command catalog filtered to what this member may use."""
        entries = catalog(bot.tree)
        selected = command.strip().lower().lstrip("/") if command else ""
        if selected:
            entry = next((item for item in entries if item.path == selected), None)
            if entry is None:
                await interaction.response.send_message(
                    "❓ I could not find that command. Pick one from the autocomplete list.",
                    ephemeral=True,
                )
                return
            detail = embed(
                f"💜 /{entry.path}",
                command_detail(entry),
                discord.Colour.from_rgb(177, 77, 255),
            )
            detail.set_footer(text="Every command keeps its own permission checks.")
            await interaction.response.send_message(embed=detail, ephemeral=True)
            return

        member = interaction.user if interaction.guild else None
        fields = render_guide(entries, viewer_access(bot, member, interaction.guild))
        guide = embed(
            "💜 Grid A1 • Command Guide",
            "✨ Built from the live command tree, so it always matches this bot. Discord shows "
            "each command's options as you type, and protected commands only answer members "
            "holding one of the staff roles saved by `/setup roles`.\n\n🔎 For one command in full, "
            "run `/help command:` and pick it.",
            discord.Colour.from_rgb(177, 77, 255),
        )
        for name, value in fields:
            guide.add_field(name=name, value=value, inline=False)
        guide.add_field(
            name="⌨️ Prefix commands",
            value="\n".join(prefix_guide_lines())[:1024],
            inline=False,
        )
        if not fields:
            guide.add_field(
                name="🌐 Nothing to list yet",
                value="Ask your server owner to finish setup, then run /help again.",
                inline=False,
            )
        guide.set_footer(
            text="Members only see the sections their roles allow. Need access? Ask your server owner."
        )
        await interaction.response.send_message(embed=guide, ephemeral=True)

    @help_command.autocomplete("command")
    async def help_command_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Offer the live catalog so nobody has to memorise command paths."""
        return [
            app_commands.Choice(name=f"/{entry.path}", value=entry.path)
            for entry in match_entries(catalog(bot.tree), current)
        ]
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
    @app_commands.checks.cooldown(5, 60.0, key=lambda i: (i.guild_id, i.user.id))
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
    @app_commands.checks.cooldown(5, 60.0, key=lambda i: (i.guild_id, i.user.id))
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
