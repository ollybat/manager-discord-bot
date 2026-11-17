"""The SpinBack rules panel: stored rule sections, the public panel, and the edit form.

The published panel is built with Discord's Components v2 layout so the accent bar, the text,
and the section buttons live inside one container instead of a button row sitting under an embed.
"""
from __future__ import annotations

import logging
from typing import Any

import discord

from .brand import (
    BRAND_FOOTER,
    RULES_DEFAULTS,
    RULES_EMBLEM_NAMES,
    RULES_EMOJI_NAMES,
    RULES_FOOTER,
    RULES_INTRO,
    RULES_SECTIONS,
    RULES_TITLE,
)
from . import logo
from .embeds import embed
from .stats import timestamp_marker
from .utils import rules_custom_id, server_emoji, utcnow

log = logging.getLogger(__name__)

RULES_COLOUR = discord.Colour.from_rgb(94, 186, 255)
MAX_RULE_CHARS = 4000
MIN_RULE_CHARS = 20


def rules_section_meta(section: str) -> tuple[str, str, str, str]:
    """Look up one rule section, falling back to the Discord section."""
    for entry in RULES_SECTIONS:
        if entry[0] == section:
            return entry
    return RULES_SECTIONS[0]


def guild_section_key(section: str) -> str:
    """Normalize a section argument to a known key."""
    return rules_section_meta(section)[0]


def resolved_emojis(service: "RulesService", guild: Any = None) -> dict[str, str]:
    """Panel icons: a staff override first, then the server's own emoji, then the built-in fallback."""
    overrides = service.emoji_overrides(getattr(guild, "id", None))
    resolved: dict[str, str] = {}
    for section, _label, fallback, _summary in RULES_SECTIONS:
        resolved[section] = (
            overrides.get(section) or server_emoji(guild, RULES_EMOJI_NAMES.get(section, ())) or fallback
        )
    resolved["emblem"] = overrides.get("emblem") or server_emoji(guild, RULES_EMBLEM_NAMES)
    return resolved


def rules_section_embed(section: str, body: str, updated_at: str | None = None, emoji: str | None = None) -> discord.Embed:
    """One set of rules, opened with its own button."""
    _key, label, fallback, _summary = rules_section_meta(section)
    icon = fallback if emoji is None else emoji
    result = embed(f"{RULES_TITLE.rsplit(' ', 1)[0]} • {label} {icon}".strip(), body, RULES_COLOUR)
    footer = f"Press the other button for the remaining rules • {BRAND_FOOTER}"
    if updated_at:
        footer = f"Updated {timestamp_marker(updated_at)} • {footer}"
    result.set_footer(text=footer[:2048])
    return result


def rules_panel_view(service: "RulesService", guild: Any = None) -> discord.ui.LayoutView:
    """The published rules panel: one container holding the text and the section buttons."""
    emojis = resolved_emojis(service, guild)
    view = discord.ui.LayoutView(timeout=None)
    children: list[Any] = [
        discord.ui.TextDisplay(f"# {RULES_TITLE}"),
        discord.ui.TextDisplay(RULES_INTRO),
        discord.ui.Separator(),
    ]
    for section, label, _fallback, summary in RULES_SECTIONS:
        heading = f"**{label} {emojis.get(section, '')}**".strip()
        children.append(
            discord.ui.Section(
                discord.ui.TextDisplay(f"{heading}\n{summary}"),
                accessory=_rules_button(service, section, label),
            )
        )
    emblem = emojis.get("emblem", "")
    footer_line = discord.ui.TextDisplay(f"***{emblem} {RULES_FOOTER}***" if emblem else f"***{RULES_FOOTER}***")
    media = logo.guild_logo_url(getattr(guild, "id", 0), getattr(service, "database", None))
    children.append(discord.ui.Section(footer_line, accessory=discord.ui.Thumbnail(media)))
    view.add_item(discord.ui.Container(*children, accent_colour=RULES_COLOUR))
    return view


def _rules_button(service: "RulesService", section: str, label: str) -> discord.ui.Button:
    """A section button inside the panel container."""
    button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=rules_custom_id(section))

    async def callback(interaction: discord.Interaction) -> None:
        await _show_rules(service, interaction, section)

    button.callback = callback
    return button


async def _respond(interaction: discord.Interaction, *args, **kwargs) -> None:
    """Answer once, using a follow-up when the handler already deferred."""
    if interaction.response.is_done():
        await interaction.followup.send(*args, **kwargs)
    else:
        await interaction.response.send_message(*args, **kwargs)


async def _delete_previous_panel(database: Any, guild: discord.Guild) -> None:
    """Remove the panel a previous post left behind, so re-posting replaces it instead of duplicating it."""
    config = database.config(guild.id)
    if not config or not config["rules_channel"] or not config["rules_message"]:
        return
    channel = guild.get_channel(int(config["rules_channel"]))
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    try:
        message = await channel.fetch_message(int(config["rules_message"]))
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        log.info("Could not find the previous rules panel in guild %s", guild.id, exc_info=True)
        return
    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        log.info("Could not remove the previous rules panel in guild %s", guild.id, exc_info=True)


async def _show_rules(service: "RulesService", interaction: discord.Interaction, section: str) -> None:
    """Open one set of rules privately for the member who pressed its button."""
    if interaction.guild is None:
        return await _respond(interaction, "❌ The rules only open inside the server.", ephemeral=True)
    body, updated_at = service.text_with_meta(interaction.guild.id, section)
    emoji = resolved_emojis(service, interaction.guild).get(section)
    await _respond(interaction, embed=rules_section_embed(section, body, updated_at, emoji), ephemeral=True)


class RulesEditModal(discord.ui.Modal):
    """Replace one section of the server rules."""

    def __init__(self, service: "RulesService", section: str):
        _key, label, emoji, _summary = rules_section_meta(section)
        super().__init__(title=f"Edit {label} rules", timeout=900.0)
        self.service = service
        self.section = section
        self.body = discord.ui.TextInput(
            label=f"{label} {emoji} rules".strip(),
            style=discord.TextStyle.paragraph,
            placeholder="Write the rules here — one rule per line reads best.",
            max_length=MAX_RULE_CHARS,
            required=True,
        )
        self.icon = discord.ui.TextInput(
            label=f"{label} icon (optional)",
            placeholder="A server emoji like <:discord:1234567890>. Leave blank to keep the current one.",
            max_length=120,
            required=False,
        )
        self.emblem = discord.ui.TextInput(
            label="Footer logo (optional)",
            placeholder="A server emoji for the panel footer. Leave blank to keep the current one.",
            max_length=120,
            required=False,
        )
        self.add_item(self.body)
        self.add_item(self.icon)
        self.add_item(self.emblem)

    def with_current_text(self, current: str) -> "RulesEditModal":
        """Prefill the box with the text staff are about to replace."""
        self.body.default = (current or "")[:MAX_RULE_CHARS]
        return self

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.service.save(
            interaction,
            self.section,
            str(self.body.value),
            icon=str(self.icon.value or ""),
            emblem=str(self.emblem.value or ""),
        )


class RulesService:
    """Read, edit, and publish the server's rules panel."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def text(self, guild_id: int, section: str) -> str:
        key = guild_section_key(section)
        stored = self.database.rule_text(guild_id, key) if guild_id else None
        return stored or RULES_DEFAULTS.get(key, "")

    def text_with_meta(self, guild_id: int, section: str) -> tuple[str, str | None]:
        key = guild_section_key(section)
        stored = self.database.rule_row(guild_id, key) if guild_id else None
        if stored:
            return str(stored["body"]), str(stored["updated_at"])
        return RULES_DEFAULTS.get(key, ""), None

    def emoji_overrides(self, guild_id: int | None) -> dict[str, str]:
        """Stored panel icon overrides for one guild."""
        return self.database.rules_emojis(guild_id) if guild_id else {}

    def panel(self, guild: Any = None) -> discord.ui.LayoutView:
        """A fresh panel view; callbacks stay valid across restarts."""
        return rules_panel_view(self, guild)

    async def post(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Post the rules inside a server.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await _delete_previous_panel(self.database, guild)
        try:
            message = await channel.send(view=self.panel(guild), file=logo.logo_file(guild.id, self.database))
        except discord.Forbidden:
            return await interaction.followup.send(
                f"❌ I cannot post in {channel.mention}. Check View Channel and Send Messages, then try again.",
                ephemeral=True,
            )
        except discord.HTTPException:
            log.exception("Posting the rules panel failed for guild %s", guild.id)
            return await interaction.followup.send("❌ Discord refused that post. Please try again shortly.", ephemeral=True)
        self.database.set_rules_panel(guild.id, channel.id, message.id)
        await interaction.followup.send(
            "✅ Rules posted in "
            f"{channel.mention} — the buttons sit inside the panel, on the right of each rule set. "
            "Posting again replaces this panel, and /rules edit changes the text later.",
            ephemeral=True,
        )

    async def refresh_panel(self, guild: discord.Guild) -> bool:
        """Refresh the posted panel after an edit so members always read the current text."""
        config = self.database.config(guild.id)
        if not config or not config["rules_channel"] or not config["rules_message"]:
            return False
        channel = guild.get_channel(int(config["rules_channel"]))
        if channel is None or not hasattr(channel, "fetch_message"):
            return False
        try:
            message = await channel.fetch_message(int(config["rules_message"]))
            await message.edit(view=self.panel(guild), attachments=[logo.logo_file(guild.id, self.database)])
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            log.info("Could not refresh the rules panel in guild %s", guild.id, exc_info=True)
            return False
        return True

    async def save(
        self,
        interaction: discord.Interaction,
        section: str,
        body: str,
        icon: str | None = None,
        emblem: str | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Edit the rules inside a server.", ephemeral=True)
        text = (body or "").strip()
        if len(text) < MIN_RULE_CHARS:
            return await interaction.response.send_message(
                f"❌ That is too short to be a rule set — write at least {MIN_RULE_CHARS} characters.",
                ephemeral=True,
            )
        key = guild_section_key(section)
        _k, label, emoji, _summary = rules_section_meta(key)
        self.database.set_rule_text(guild.id, key, text[:MAX_RULE_CHARS], interaction.user.id, utcnow().isoformat())
        icons: dict[str, str] = {}
        if (icon or "").strip():
            icons[key] = str(icon).strip()
        if (emblem or "").strip():
            icons["emblem"] = str(emblem).strip()
        if icons:
            self.database.set_rules_emojis(guild.id, **icons)
        refreshed = await self.refresh_panel(guild)
        note = (
            "The posted rules panel was updated for everyone."
            if refreshed
            else "No rules panel is posted yet — publish it with /rules post."
        )
        changed = " The panel icon was updated too." if icons else ""
        await interaction.response.send_message(
            f"✅ Saved the {label} {emoji} rules.{changed} {note}",
            ephemeral=True,
        )
