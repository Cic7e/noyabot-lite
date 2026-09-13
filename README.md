# Noyabot-Lite
Noyabot is a modern, high-polish Discord bot with a full dice engine, moderation, and utility features.
> This is the public lite version, which only retains core features. The full live version is available at [noya.lol](https://bot.noya.lol/)!

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
3. Point the volume in `docker-compose.yml` at your data directory (default is `/mnt/user/appdata/noyabot`;
databases are created automatically)
5. Run `docker compose up -d --build` to build or update the container

### First Time?

The bot needs to be **started once, configured, then restarted**:

1. Start the container. If done successfully it will create `data/` and populate it with databases,
plus the `data/config.yaml` template
3. Stop the container and edit `data/config.yaml`: fill in your guild id, toggle features,
and set channel/role ids
5. Restart. The bot reloads `config.yaml` on startup

### Environment variables

| Param       | Description                                            | Required? |
|-------------|--------------------------------------------------------|-----------|
| `TOKEN`     | Main Discord bot token. Required to run.               | **Yes**   |
| `DEV_TOKEN` | Dev bot token; takes precedence over `TOKEN` when set. | No        |

> Noyabot requires the **Server Members** and **Message Content** intents; set them
> in the [Discord Developer Portal](https://discord.com/developers/applications)