import discord
from discord.ext import commands

from utils.bot_config import get_guild_config
from utils.guild_db import GuildDB

LEADERBOARD_META_KEY = "starboard:leaderboard_message_id"


def _emoji_matches(reaction_emoji, config_emoji: str) -> bool:
    if isinstance(reaction_emoji, str):
        return reaction_emoji == config_emoji
    if reaction_emoji.id is not None:
        config_id = config_emoji
        if config_emoji.startswith("<"):
            config_id = config_emoji.strip("<>").rsplit(":", 1)[-1]
        elif ":" in config_emoji:
            config_id = config_emoji.rsplit(":", 1)[-1]
        return config_id.isdigit() and reaction_emoji.id == int(config_id)
    return reaction_emoji.name == config_emoji


class Starboard(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot


    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._process_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._process_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        starboard_config = get_guild_config(guild.id).get_starboard_config()
        with GuildDB(guild.id) as db:
            entry = db.get_starboard_message(payload.message_id)
            if not entry or not entry.get("starboard_message_id"):
                return
            starboard_post_to_delete = (starboard_config.get("channel_id"),
                                        entry["starboard_message_id"])
            db.remove_starboard_message(payload.message_id)
        channel_id, message_id = starboard_post_to_delete
        await self._remove_starboard_post(guild, channel_id, message_id)
        await self._refresh_leaderboard(guild)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        starboard_config = get_guild_config(payload.guild_id).get_starboard_config()
        starboard_post_to_delete = None
        refresh_leaderboard = False

        with GuildDB(payload.guild_id) as db:
            # Case 0: the deleted message IS the leaderboard message
            if db.get_meta(LEADERBOARD_META_KEY) == payload.message_id:
                db.set_meta(LEADERBOARD_META_KEY, None)
                return

            # Case 1: the deleted message IS a starboard post
            entry = db.get_starboard_message_by_starboard_id(payload.message_id)
            if entry:
                db.remove_starboard_message(entry["original_message_id"])
                refresh_leaderboard = True
            else:
                # Case 2: the deleted message is an original that was starboarded
                entry = db.get_starboard_message(payload.message_id)
                if entry and entry.get("starboard_message_id"):
                    starboard_post_to_delete = (starboard_config.get("channel_id"),
                                                entry["starboard_message_id"])
                    db.remove_starboard_message(payload.message_id)
                    refresh_leaderboard = True

        if guild is not None:
            if starboard_post_to_delete is not None:
                channel_id, message_id = starboard_post_to_delete
                await self._remove_starboard_post(guild, channel_id, message_id)
            if refresh_leaderboard:
                await self._refresh_leaderboard(guild)

    @staticmethod
    def _count_stars(message: discord.Message, config_emoji: str) -> int:
        for reaction in message.reactions:
            if _emoji_matches(reaction.emoji, config_emoji):
                return reaction.count
        return 0

    @staticmethod
    def _build_starboard_embed(message: discord.Message, star_count: int,
                               configured_emoji: str) -> discord.Embed:
        embed = discord.Embed(description=(message.content or "*(empty message)*"), color=0xFFB700,
                              timestamp=message.created_at)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})")
        embed.set_footer(text=f"{star_count} {configured_emoji}")
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                embed.set_image(url=attachment.url)
                break
        return embed

    @staticmethod
    async def _remove_starboard_post(guild: discord.Guild, starboard_channel_id: int | None, starboard_message_id: int):
        if not starboard_channel_id:
            return
        channel = guild.get_channel(starboard_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(starboard_message_id)
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _process_reaction(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        guild_config = get_guild_config(guild.id)
        if not guild_config.get_feature_toggle("starboard"):
            return
        config = guild_config.get_starboard_config()
        configured_emoji = config.get("emoji") or "⭐"
        if not _emoji_matches(payload.emoji, configured_emoji):
            return
        if payload.channel_id in set(config.get("excluded_channels", [])):
            return
        starboard_channel_id = config.get("channel_id")
        if not starboard_channel_id or payload.channel_id == starboard_channel_id:
            return
        threshold = config.get("threshold") or 3
        with GuildDB(guild.id) as db:
            entry = db.get_starboard_message(payload.message_id)
        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        star_count = self._count_stars(message, configured_emoji)
        starboard_channel = guild.get_channel(starboard_channel_id)
        if not isinstance(starboard_channel, discord.TextChannel):
            return
        if star_count >= threshold:
            if entry and entry.get("starboard_message_id"):
                await self._update_starboard_post(guild, starboard_channel, entry["starboard_message_id"],
                                                  message, star_count, configured_emoji)
            else:
                created = await self._create_starboard_post(
                    guild, starboard_channel, message, star_count, configured_emoji)
                if created:
                    await self._refresh_leaderboard(guild)
        elif entry and entry.get("starboard_message_id"):
            await self._remove_starboard_post(guild, starboard_channel_id, entry["starboard_message_id"])
            with GuildDB(guild.id) as db:
                db.remove_starboard_message(payload.message_id)
            await self._refresh_leaderboard(guild)

    async def _create_starboard_post(self, guild: discord.Guild, channel: discord.TextChannel,
                                     message: discord.Message, star_count: int,
                                     configured_emoji: str) -> bool:
        embed = self._build_starboard_embed(message, star_count, configured_emoji)
        try:
            starboard_message = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"Failed to create starboard post in guild {guild.id}: {exc}")
            return False

        snippet = (message.content or "").strip()
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        if not snippet:
            snippet = "*(attachment)*" if message.attachments else "*(empty message)*"

        with GuildDB(guild.id) as db:
            db.add_starboard_message(original_message_id=message.id, original_channel_id=message.channel.id,
                                     author_id=message.author.id, starboard_message_id=starboard_message.id,
                                     star_count=star_count, content_snippet=snippet)
        return True

    async def _update_starboard_post(self, guild: discord.Guild, channel: discord.TextChannel,
                                     starboard_message_id: int, original_message: discord.Message,
                                     star_count: int, configured_emoji: str):
        embed = self._build_starboard_embed(original_message, star_count, configured_emoji)
        try:
            message = await channel.fetch_message(starboard_message_id)
            await message.edit(embed=embed)
        except discord.NotFound:
            with GuildDB(guild.id) as db:
                db.remove_starboard_message(original_message.id)
            return
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"Failed to update starboard post in guild {guild.id}: {exc}")
            return
        with GuildDB(guild.id) as db:
            db.update_starboard_message(original_message.id, star_count=star_count)

    async def _refresh_leaderboard(self, guild: discord.Guild):
        config = get_guild_config(guild.id).get_starboard_config()
        starboard_channel_id = config.get("channel_id")
        configured_emoji = config.get("emoji") or "⭐"
        with GuildDB(guild.id) as db:
            old_leaderboard_id = db.get_meta(LEADERBOARD_META_KEY)
            top_messages = db.get_top_starred_messages(10)
        channel = guild.get_channel(starboard_channel_id) if starboard_channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        # Delete the old leaderboard message
        if old_leaderboard_id:
            try:
                old_message = await channel.fetch_message(old_leaderboard_id)
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        # If nothing is on starboard, skip empty leaderboard
        if not top_messages:
            with GuildDB(guild.id) as db:
                db.set_meta(LEADERBOARD_META_KEY, None)
            return
        embed = self._build_leaderboard_embed(guild, top_messages, configured_emoji)
        try:
            leaderboard_message = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"Failed to post starboard leaderboard in guild {guild.id}: {exc}")
            return
        with GuildDB(guild.id) as db:
            db.set_meta(LEADERBOARD_META_KEY, leaderboard_message.id)

    @staticmethod
    def _build_leaderboard_embed(guild: discord.Guild, top_messages: list[dict],
                                 configured_emoji: str) -> discord.Embed:
        lines: list[str] = []
        for i, entry in enumerate(top_messages, 1):
            star_count = entry.get("star_count", 0)
            snippet = entry.get("content_snippet") or "*(no text)*"
            jump_url = (f"https://discord.com/channels/{guild.id}/"
                        f"{entry['original_channel_id']}/{entry['original_message_id']}")
            lines.append(f"**{i}.** {star_count} {configured_emoji} — [{snippet}]({jump_url})")
        embed = discord.Embed(title="🏆 Starboard Leaderboard", description="\n".join(lines), color=0xFFB700)
        embed.set_footer(text=f"Top 10 most starred messages")
        return embed


def setup(bot: discord.Bot):
    bot.add_cog(Starboard(bot))