import asyncio

import discord
from discord.ext import commands

from utils.bot_config import get_guild_config


class WelcomeGoodbye(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot


    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        guild_config = get_guild_config(member.guild.id)
        if guild_config.get_feature_toggle("welcome"):
            await self._send_message(member, guild_config.get_welcome_config())
        if guild_config.get_feature_toggle("auto_roles"):
            await self._apply_auto_roles(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        guild_config = get_guild_config(member.guild.id)
        if guild_config.get_feature_toggle("goodbye"):
            await self._send_message(member, guild_config.get_goodbye_config())

    @staticmethod
    def _placeholders(member: discord.Member) -> dict[str, str]:
        return {
            "{user}": member.display_name,
            "{server}": member.guild.name,
            "{member_count}": str(member.guild.member_count or 0),
            "{user_mention}": member.mention}

    @staticmethod
    def _replace_placeholders(text: str | None, placeholders: dict[str, str]) -> str | None:
        if not text:
            return text
        for placeholder, value in placeholders.items():
            text = text.replace(placeholder, value)
        return text

    @staticmethod
    async def _apply_auto_roles(member: discord.Member):
        auto_roles = get_guild_config(member.guild.id).get_auto_roles()
        if not auto_roles:
            return
        guild = member.guild
        bot_member = guild.me
        roles: list[discord.Role] = []
        max_delay = 0
        for ar in auto_roles:
            role = guild.get_role(ar["role_id"])
            if role is None or role.managed:
                continue
            if bot_member.top_role <= role:
                print(f"Skipping auto role {ar['role_id']} in guild {guild.id} - bot hierarchy too low")
                continue
            roles.append(role)
            max_delay = max(max_delay, ar["delay_seconds"])
        if not roles:
            return
        if max_delay > 0:
            await asyncio.sleep(max_delay)
            member = member.guild.get_member(member.id)
            if member is None:
                return
        try:
            await member.add_roles(*roles, reason="Auto role assignment")
        except discord.Forbidden:
            print(f"Missing permissions to assign auto roles in guild {member.guild.id}")
        except discord.HTTPException as exc:
            print(f"Failed to assign auto roles: {exc}")

    async def _send_message(self, member: discord.Member, config: dict):
        channel_id = config.get("channel_id")
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        placeholders = self._placeholders(member)
        content = self._replace_placeholders(config.get("content"), placeholders)
        if not content:
            return
        try:
            await channel.send(content=content)
        except discord.Forbidden:
            print(f"Missing permissions to send welcome/goodbye message in channel "
                  f"{channel_id} in guild {member.guild.id}")
        except discord.HTTPException as exc:
            print(f"Failed to send welcome/goodbye message: {exc}")


def setup(bot: discord.Bot):
    bot.add_cog(WelcomeGoodbye(bot))