import asyncio
import logging

from bot.client import create_bot, init_databases, load_cogs, get_token
from utils.bot_config import Config
from utils.rule_updater import update_rules_from_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _safe_step(label, func):
    """Run an optional setup step, logging a warning (with traceback) on failure"""
    try:
        logger.info(label)
        result = func()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.warning("Optional step failed: %s", label, exc_info=True)

async def main():
    Config.get()
    bot = create_bot()
    await _safe_step("Starting databases...", init_databases)
    await _safe_step("Updating URL rules...", update_rules_from_source)
    # Cog load failures are fatal, the bot would come up missing commands
    logger.info("Loading commands...")
    load_cogs(bot)
    try:
        await bot.start(get_token())
    finally:
        if not bot.is_closed():
            logger.info("Shutting down bot...")
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass