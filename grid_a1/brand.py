"""Product branding and the starter rule texts used by the rules panel."""
from __future__ import annotations

BRAND_NAME = "SpinBack Manager"
BRAND_SHORT = "SpinBack"
BRAND_FOOTER = "Powered by SpinBack"
BRAND_TAGLINE = "the best competitive server"

BRAND_PRIMARY = (94, 186, 255)
BRAND_ACCENT = (177, 77, 255)
"""The two brand colours; every panel picks its accent from here so the palette cannot drift."""

RULES_INTRO = (
    "Thank you for reading and respecting these rules.\n\n"
    "They keep the server fair and competitive for everyone, so that every player gets the best "
    "experience we can offer. Playing here means you accept them, and breaking them can cost you "
    "access to the Discord or the server."
)

RULES_TITLE = "Spinback Rules ✅"
RULES_FOOTER = "Powered by Spinback"

RULES_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("discord", "Discord", "💬", "How we treat each other in chat, voice, and DMs."),
    ("game", "Game", "🎮", "How we play on the server: raids, teams, and fair play."),
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
        "**1. Respect everyone.** No harassment, hate speech, racism, threats, or personal attacks — "
        "not in channels, not in voice, not in DMs.\n"
        "**2. No spam or advertising.** No invite links, DM advertising, or promoting other servers "
        "without staff permission.\n"
        "**3. Keep it in the right channel.** Chat stays on topic and voice stays clear — no mic spam, "
        "soundboards, or screaming over other people.\n"
        "**4. No NSFW, illegal, or scam content.** No cheating services, account sales, or real-money "
        "trading, and nothing that breaks Discord's Terms of Service or Community Guidelines.\n"
        "**5. Keep private things private.** Never share anyone's personal information — names, "
        "addresses, IPs, or socials — and do not ask for theirs.\n"
        "**6. Do not pretend to be staff.** No staff names, avatars, or tags, and no promising things "
        "on the team's behalf.\n"
        "**7. Report, do not retaliate.** If someone breaks a rule, open a ticket or mention staff "
        "instead of answering in chat.\n"
        "**8. Staff decisions are final.** If you disagree with one, open a ticket and make your case "
        "there instead of arguing it out in chat."
    ),
    "game": (
        "**1. No cheating.** No third-party tools, macros, scripts, or modified clients — cheating is "
        "a permanent ban, and VAC or console-banned accounts are not welcome here.\n"
        "**2. No exploits or glitches.** If you find a bug, a duping method, or a spot that breaks the "
        "game, report it in a ticket instead of using it.\n"
        "**3. Respect the group limit.** The server's maximum team size is enforced, and teaming "
        "outside your group during events is not allowed. Insiding — using a clan's trust to steal "
        "from them — is a ban.\n"
        "**4. A raid is a raid.** Take what you came for and leave: no griefing beyond the raid, no "
        "walling off monuments or spawn areas, and no blocking other players' access to the map.\n"
        "**5. No spawn killing.** Do not camp spawns, and do not hold a monument against players who "
        "cannot fight back.\n"
        "**6. No real-money trading.** Items, accounts, and loot cannot be sold for money, and a scam "
        "in any trade is punished.\n"
        "**7. No ban evasion.** A ban covers you and your accounts — coming back on another account or "
        "a new profile makes it permanent.\n"
        "**8. Do not attack the server.** No DDoS, IP grabbing, doxxing, or anything else aimed at the "
        "server or the people playing on it."
    ),
}
