# Security Policy

## Supported versions

The `main` branch is the actively maintained version. Security fixes are applied there. Historical ZIP snapshots, forks, and unreleased branches are not supported unless the maintainers explicitly say otherwise.

| Source | Supported |
| --- | --- |
| `main` | Yes |
| Older snapshots / forks | No |

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's **Report a vulnerability** form:

<https://github.com/ollybat/manager-discord-bot/security/advisories/new>

Do not post vulnerability details, bot tokens, database files, transcripts, or personal data in a public issue. Include the affected commit or version, impact, and a safe reproduction if possible. Maintainers will review reports as promptly as possible; no fixed response-time guarantee is made.

If a Discord bot token may have been exposed, revoke and regenerate it in the Discord Developer Portal immediately, then update the deployment secret. Never paste the replacement token into an issue or chat.
