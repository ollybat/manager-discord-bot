# Grid A1 — Manager Discord Bot

Standalone Python `discord.py` bot for Rust Console community support.

## Readable ticket transcripts

Ticket transcripts are generated as polished, accessible HTML instead of raw chat text. They include:

- Clear Grid A1 header and plain-language instructions
- Ticket summary with ID, issue, region, owner, opened time, closed time, and closer
- Large high-contrast typography
- One readable message card per Discord message
- Author name, initials avatar, and UTC timestamp
- Escaped text and safe links
- Attachment cards with filenames and byte sizes
- Inline image previews with descriptive alt text
- Message count and empty-transcript handling
- Responsive layout for phones and desktop
- Print-friendly CSS for archiving or sharing

Each transcript is attached to the archive log embed when a ticket is closed.

## Improved support panel

The panel is organized for non-technical users:

- **Support Tickets** title
- Short instructions: choose a support option, then choose EU
- Clear Support Status section
- Open total, EU, and NA counters
- Closed total and EU counters
- Fast response indicator
- 12-minute estimate
- NA Coming Soon notice
- Category guide for General, Base, Clan, Shop, Raid, and Bug
- Step-by-step ticket instructions
- Grid A1 branding and live-refresh footer

The panel remains persistent and refreshes every 60 seconds without deleting tickets.

## Setup and commands

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
/welcomer preview
/welcomer test
/ticket claim
/ticket transfer staff_member
/ticket requestclose reason
/ticket close reason
```

## Run

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Invite with `bot` and `applications.commands` scopes. Enable Server Members Intent for welcomes. Required permissions include Manage Channels, View Channel, Send Messages, Embed Links, Attach Files, and Read Message History.

No runtime or live Discord validation is claimed by this change. Test with a non-production ticket before rollout.
