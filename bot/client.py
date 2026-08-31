import os

import discord
from dotenv import load_dotenv

from utils.remind_manager import ReminderManager

COGS = ["cleanurl", "dice", "error", "misc", "odds", "remind", "simulate"]
MODERATION_COGS = ["hey_bye", "logging", "report", "starboard"]
load_dotenv()


def get_token() -> str:
    dev_token = os.getenv("DEV_TOKEN")
    if dev_token:
        print("Running in Development Mode...")
        return dev_token
    print("Running in Production Mode...")
    token = os.getenv("TOKEN")
    if not token:
        raise ValueError("Token not found! Set TOKEN (or DEV_TOKEN) in your .env file.")
    return token

def create_bot() -> discord.Bot:
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    mentions = discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=True)
    bot = discord.Bot(intents=intents, allowed_mentions=mentions)

    @bot.event
    async def on_ready():
        print(f"{bot.user} is now online and ready!")
        print("-----------------------------------------")
    return bot

def init_databases() -> None:
    ReminderManager()

def load_cogs(bot: discord.Bot) -> None:
    for cog in COGS:
        bot.load_extension(f"bot.commands.{cog}")
    for cog in MODERATION_COGS:
        bot.load_extension(f"bot.moderation.{cog}")
