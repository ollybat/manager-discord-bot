# Grid A1 Manager Discord Bot

## Ticket inactivity policy

Grid A1 checks open tickets every five minutes using the `inactivity_hours` value from `/setup tickets`.

- 🟢 **Open** — recent activity is below the configured threshold.
- 🟡 **Inactive** — no ticket message for at least `inactivity_hours`.
- 🔴 **Closing soon** — no activity for at least twice `inactivity_hours`.

When a ticket reaches 🔴:

1. Grid A1 sends the owner one friendly private message.
2. The message has clear buttons: **Keep ticket open**, **Request another staff member**, and **Close ticket**.
3. The owner has 24 hours to respond.
4. Keep open resets the activity timer.
5. Request another staff member clears the current claim and records a staff request.
6. Close archives the transcript and removes the ticket.
7. If the owner has left the Discord server, Grid A1 cannot DM them and the ticket proceeds to the automatic-close policy.
8. If there is no response after the 24-hour grace period, the ticket is automatically archived and closed.

A message sent in a ticket resets its activity timer. The five-minute task does not delete tickets merely because they are yellow or red; red first starts the notice/grace process. NA remains Coming Soon and EU remains the only selectable region.

## Setup

```text
/setup tickets panel_channel logs_channel category inactivity_hours
/setup welcomer welcome_channel link_channel bot_commands_channel shop_channel verify_channel
```

## Commands

```text
/help
/rules
/ping
/embed
/embed-edit
/sync
/welcomer preview
/welcomer test
/ticket claim
/ticket transfer
/ticket requestclose
/ticket close
```

Run `python bot.py` after installing `requirements.txt`. Keep `.env`, the Discord token, SQLite files, logs, and transcripts private.
