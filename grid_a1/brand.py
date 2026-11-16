"""Product branding and the starter rule texts used by the rules panel."""
from __future__ import annotations

BRAND_NAME = "SpinBack Manager"
BRAND_SHORT = "SpinBack"
BRAND_FOOTER = "Powered by SpinBack"
BRAND_TAGLINE = "the best competitive server"

RULES_INTRO = (
    "Thank you all for reading and respecting these rules.\n\n"
    "They were established to provide the best gaming experience and were created with the aim of "
    "offering you the best competitive server."
)

RULES_TITLE = "Spinback Rules ✅"
RULES_FOOTER = "Powered by Spinback"

RULES_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("discord", "Discord", "💬", "All discord related rules."),
    ("game", "Game", "🎮", "General in-game rules."),
)
"""Section key, button/label text, fallback emoji, and the one-line summary on the panel."""

RULES_EMOJI_NAMES: dict[str, tuple[str, ...]] = {
    "discord": ("discord", "discordlogo", "discord_logo"),
    "game": ("game", "controller", "gamepad"),
}
"""Server emoji names searched for each section, in order, before the fallback emoji is used."""

RULES_EMBLEM_NAMES: tuple[str, ...] = ("spinback", "spinbacklogo", "spinback_logo", "s")
"""Server emoji names searched for the footer logo."""

RULES_DEFAULTS: dict[str, str] = {
    "discord": (
        "**1. Respect everyone.** No harassment, hate speech, threats, or personal attacks — "
        "in channels, in voice, or in DMs to members.\n"
        "**2. No spam or advertising.** No invite links, DM advertising, or promoting other servers "
        "without staff permission.\n"
        "**3. Use the right channel.** Keep chat on topic and take long conversations to the right "
        "channel; use tickets for anything private.\n"
        "**4. No NSFW, illegal, or scam content.** This includes cheating services, account sales, "
        "and real-money trading.\n"
        "**5. Follow Discord's Terms of Service** and Community Guidelines at all times.\n"
        "**6. Staff decisions are final.** If you disagree with a decision, open a ticket instead of "
        "arguing it out in chat."
    ),
    "game": (
        "**1. No cheating.** No third-party tools, macros, exploits, duping, or modified clients.\n"
        "**2. No raid abuse.** No tower camping, spawn killing, or holding monuments against players "
        "who cannot fight back.\n"
        "**3. No teaming outside the rules.** Max group size is enforced; no teaming with another "
        "clan during official events.\n"
        "**4. No base griefing beyond a raid.** Destroy what you must to raid, then leave — no "
        "walling off monuments, and no blocking other players' access to the map.\n"
        "**5. No glitching.** Report any bug or exploitable spot to staff through a ticket instead of "
        "using it.\n"
        "**6. Play fair with the loot economy.** No trading in-game items or accounts for real money, "
        "and no scamming trades."
    ),
}
