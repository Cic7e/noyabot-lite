import io
import logging
import traceback
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.bot_config import get_error_log_channel_id

logger = logging.getLogger(__name__)

MAX_TRACEBACK_LENGTH = 1000


class ErrorHandlerCog(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot


    @staticmethod
    async def _respond(ctx: discord.ApplicationContext, message: str, ephemeral: bool = True):
        try:
            if ctx.response.is_done():
                await ctx.followup.send(message, ephemeral=ephemeral)
            else:
                await ctx.respond(message, ephemeral=ephemeral)
        except discord.HTTPException:
            logger.warning("Could not deliver error message to user %s", ctx.author.id, exc_info=True)

    @commands.Cog.listener()
    async def on_application_command_error(self, ctx: discord.ApplicationContext,
                                           error: discord.DiscordException):
        error = getattr(error, "original", error)
        if isinstance(error, commands.CommandOnCooldown):
            await self._respond(ctx, f"You're on cooldown for the next {error.retry_after:.2f} seconds!")
            return
        if isinstance(error, commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            await self._respond(ctx, f"I need the following permissions: `{missing}`")
            return
        if isinstance(error, commands.MissingPermissions):
            missing = ", ".join(error.missing_permissions)
            await self._respond(ctx, f"You need the following permissions to do that: `{missing}`")
            return
        if isinstance(error, commands.NoPrivateMessage):
            await self._respond(ctx, "This command can only be used inside a server!")
            return
        if isinstance(error, commands.MaxConcurrencyReached):
            await self._respond(ctx, "That command is already running — give it a moment!")
            return
        if isinstance(error, commands.CheckFailure):
            await self._respond(ctx, "You're not allowed to use this command!")
            return
        if isinstance(error, discord.HTTPException) and error.code == 50035:
            await self._respond(ctx, "Output is greater than 2000 characters! Try again")
            return
        # Anything else is a bug
        command_name = ctx.command.qualified_name if ctx.command else "unknown"
        logger.error("Unhandled exception in /%s (user %s, guild %s)", command_name, ctx.author.id,
                     ctx.guild.id if ctx.guild else "DM", exc_info=error)
        await self._send_log_embed(ctx, error, command_name)
        await self._respond(ctx, "I messed up :( I let Cic7e know")

    async def _send_log_embed(self, ctx: discord.ApplicationContext,
                              error: BaseException, command_name: str):
        log_channel_id = get_error_log_channel_id()
        if not log_channel_id:
            logger.warning("error_log_channel_id is not set in config.yaml; skipping channel log.")
            return
        log_channel = self.bot.get_channel(log_channel_id)
        if log_channel is None:
            logger.warning("Error log channel %s not found.", log_channel_id)
            return
        traceback_text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        embed = discord.Embed(title="Command Error", description="An unhandled exception occurred.",
                              color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Command", value=f"`/{command_name}`", inline=False)
        embed.add_field(name="Author", value=f"{ctx.author.mention} (`{ctx.author.id}`)", inline=False)
        if ctx.guild:
            embed.add_field(name="Location",
                            value=f"**Server:** {ctx.guild.name} (`{ctx.guild.id}`)\n"
                                  f"**Channel:** {ctx.channel.mention} (`{ctx.channel.id}`)", inline=False)
        else:
            embed.add_field(name="Location", value="Direct Message", inline=False)
        file = None
        if len(traceback_text) > MAX_TRACEBACK_LENGTH:
            embed.add_field(name="Traceback (truncated, full version attached)",
                            value=f"```py\n{traceback_text[-MAX_TRACEBACK_LENGTH:]}\n```", inline=False)
            file = discord.File(io.BytesIO(traceback_text.encode()), filename="traceback.txt")
        else:
            embed.add_field(name="Traceback", value=f"```py\n{traceback_text}\n```", inline=False)
        try:
            if file:
                await log_channel.send(embed=embed, file=file)
            else:
                await log_channel.send(embed=embed)
        except discord.HTTPException:
            logger.exception("Failed to send error log to channel %s", log_channel_id)


def setup(bot: discord.Bot):
    bot.add_cog(ErrorHandlerCog(bot))