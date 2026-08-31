# Noyabot-Lite
Noyabot is a modern, high-polish Discord bot with a full dice engine, moderation, and utility features.
> This is the public lite version, which only retains core features. The full live version is avalible at [noya.lol](https://bot.noya.lol/)!

## Features

**Commands** (py-cord application commands)
- `/roll` — rich dice roller with math expressions, targets, luck scores, and advantage/disadvantage
- `/odds`, `/simulate` — probability tooling for dice expressions
- `/remind` — natural-language reminders with time zone support
- `/random` — pick random server members, with channel and role filters
- `cleanurl` — strip trackers from URLs using self-updating AdGuard lists and entropy scoring

**Moderation** (configurable per-guild, see `config.yaml`)
- Welcome/goodbye messages with placeholders and auto-role assignment on join
- Starboard with a live top-10 leaderboard
- Message reporting with Resolve/Dismiss buttons
- Logging of bans, kicks, timeouts, role changes, and bot permission errors

## Quick Start (Docker)

1. Clone the repo
2. Create `.env` at the root (table below)
3. Point the volume in `docker-compose.yml` at your data directory (default is `/mnt/user/appdata/noyabot`; databases are created automatically)
4. `docker compose up -d --build` — same command rebuilds for updates.

### First-run flow

The bot needs to be **started once, configured, then restarted**:

1. Start the container. It will create the `data/` directory and a template
   `data/config.yaml` (plus the `data/servers/<guild_id>/` runtime databases
   once the bot joins a server)
2. Stop the container and edit `data/config.yaml` - fill in your guild id,
   toggle features, and set channel/role ids.
3. Restart. The bot reloads `config.yaml` on startup.

### Environment variables

| Param       | Description                                            | Required? |
|-------------|--------------------------------------------------------|-----------|
| `TOKEN`     | Main Discord bot token. Required to run.               | **Yes**   |
| `DEV_TOKEN` | Dev bot token; takes precedence over `TOKEN` when set. | No        |

> Enable the **Server Members** and **Message Content** intents in the
> [Discord Developer Portal](https://discord.com/developers/applications)
> under your bot's *Privileged Gateway Intents* — Noyabot requires both.