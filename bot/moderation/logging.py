import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

from utils.bot_config import get_guild_config

logger = logging.getLogger(__name__)

# Event-type keys stored under the "logging_events" config in config.yaml.
EVENT_MEMBER_BAN = "member_ban"
EVENT_MEMBER_KICK = "member_kick"
EVENT_MEMBER_TIMEOUT = "member_timeout"
EVENT_MEMBER_ROLE_UPDATE = "member_role_update"
EVENT_ROLE_CREATE = "role_create"
EVENT_ROLE_DELETE = "role_delete"
EVENT_ROLE_UPDATE = "role_update"
EVENT_BOT_PERM_ERROR = "bot_permission_error"

AUDIT_LOG_LIMIT = 10
KICK_DETECTION_WINDOW = timedelta(seconds=15)


class LoggingCog(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot


    # -------------------- HELPERS

    @staticmethod
    def _resolve_log_channel(guild: discord.Guild, event_type: str) -> discord.TextChannel | discord.Thread | None:
        config = get_guild_config(guild.id)
        if not config.get_feature_toggle("logging"):
            return None
        logging_config = config.get_logging_config()
        event = config.get_logging_event(event_type)
        if not event or not event.get("enabled"):
            return None
        channel = guild.get_channel(logging_config.get("channel_id") or 0)
        return channel if isinstance(channel, (discord.TextChannel, discord.Thread)) else None

    @staticmethod
    def _embed(title: str, color: discord.Color) -> discord.Embed:
        return discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))

    @staticmethod
    def _fill_moderator(embed: discord.Embed, entry: discord.AuditLogEntry):
        embed.add_field(name="Moderator", value=f"{entry.user} (`{entry.user.id}`)", inline=False)
        if entry.reason:
            embed.add_field(name="Reason", value=entry.reason, inline=False)

    @staticmethod
    def _get_timeout(member: discord.Member):
        return getattr(member, "timed_out_until", None) or getattr(member, "communication_disabled_until", None)

    @staticmethod
    async def _find_audit_entry(guild: discord.Guild, action: discord.AuditLogAction, target_id: int,
                                max_age: Optional[timedelta] = None) -> Optional[discord.AuditLogEntry]:
        """Return the most recent matching audit-log entry, or None"""
        try:
            async for entry in guild.audit_logs(limit=AUDIT_LOG_LIMIT, action=action):
                if not entry.target or entry.target.id != target_id:
                    continue
                if max_age is not None and \
                        datetime.now(timezone.utc) - entry.created_at > max_age:
                    continue
                return entry
        except discord.Forbidden:
            logger.warning("Missing view_audit_log permission in guild %s", guild.id)
        return None

    async def _send_log(self, guild: discord.Guild, event_type: str, embed: discord.Embed):
        channel = self._resolve_log_channel(guild, event_type)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Missing permissions to send log to channel %s in guild %s", channel.id, guild.id)
        except discord.HTTPException:
            logger.exception("Failed to send %s log in guild %s", event_type, guild.id)

    async def _attach_moderator(self, guild: discord.Guild, action: discord.AuditLogAction,
                                target_id: int, embed: discord.Embed):
        """Resolve the responsible moderator from the audit log and add them to the embed"""
        entry = await self._find_audit_entry(guild, action, target_id)
        if entry:
            self._fill_moderator(embed, entry)


    # -------------------- BANS & UNBANS

    async def _log_ban_event(self, guild: discord.Guild, user: discord.User | discord.Member,
                             title: str, color: discord.Color, action: discord.AuditLogAction):
        embed = self._embed(title, color)
        embed.add_field(name="User", value=f"{user} (`{user.id}`)", inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        await self._attach_moderator(guild, action, user.id, embed)
        await self._send_log(guild, EVENT_MEMBER_BAN, embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member):
        await self._log_ban_event(guild, user, "🔨 Member Banned", discord.Color.red(),
                                  discord.AuditLogAction.ban)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        # Unbans share the member_ban event so existing configs keep working
        await self._log_ban_event(guild, user, "🔓 Member Unbanned", discord.Color.green(),
                                  discord.AuditLogAction.unban)


    # -------------------- KICKS

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Fires for kicks *and* voluntary leaves, a recent kick audit entry tells them apart"""
        guild = member.guild
        entry = await self._find_audit_entry(guild, discord.AuditLogAction.kick, member.id,
                                             max_age=KICK_DETECTION_WINDOW)
        if entry is None:
            return
        embed = self._embed("👢 Member Kicked", discord.Color.orange())
        embed.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        self._fill_moderator(embed, entry)
        await self._send_log(guild, EVENT_MEMBER_KICK, embed)


    # -------------------- TIMEOUTS & MEMBER ROLE CHANGES

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        await self._handle_timeout_change(before, after)
        await self._handle_role_change(before, after)

    async def _handle_timeout_change(self, before: discord.Member, after: discord.Member):
        before_timeout = self._get_timeout(before)
        after_timeout = self._get_timeout(after)
        if before_timeout == after_timeout:
            return
        if after_timeout is not None:
            embed = self._embed("⏱️ Member Timed Out", discord.Color.dark_orange())
            embed.add_field(name="User", value=f"{after} (`{after.id}`)", inline=False)
            embed.add_field(name="Until", value=discord.utils.format_dt(after_timeout, "F"), inline=False)
        else:
            embed = self._embed("⏱️ Timeout Removed", discord.Color.green())
            embed.add_field(name="User", value=f"{after} (`{after.id}`)", inline=False)
        embed.set_thumbnail(url=after.display_avatar.url)
        await self._attach_moderator(after.guild, discord.AuditLogAction.member_update,
                                     after.id, embed)
        await self._send_log(after.guild, EVENT_MEMBER_TIMEOUT, embed)

    async def _handle_role_change(self, before: discord.Member, after: discord.Member):
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if not added and not removed:
            return
        embed = self._embed("🎭 Member Roles Updated", discord.Color.blurple())
        embed.add_field(name="User", value=f"{after} (`{after.id}`)", inline=False)
        embed.set_thumbnail(url=after.display_avatar.url)
        if added:
            embed.add_field(name="Added", value=", ".join(r.mention for r in added), inline=False)
        if removed:
            embed.add_field(name="Removed", value=", ".join(r.mention for r in removed), inline=False)
        await self._attach_moderator(after.guild, discord.AuditLogAction.member_role_update, after.id, embed)
        await self._send_log(after.guild, EVENT_MEMBER_ROLE_UPDATE, embed)


    # -------------------- GUILD ROLE CHANGES

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        embed = self._embed("✨ Role Created", discord.Color.green())
        embed.add_field(name="Role", value=f"{role.mention} (`{role.id}`)", inline=False)
        embed.add_field(name="Color", value=str(role.colors.primary), inline=True)
        embed.add_field(name="Mentionable", value=str(role.mentionable), inline=True)
        embed.add_field(name="Hoisted", value=str(role.hoist), inline=True)
        await self._attach_moderator(role.guild, discord.AuditLogAction.role_create, role.id, embed)
        await self._send_log(role.guild, EVENT_ROLE_CREATE, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        embed = self._embed("🗑️ Role Deleted", discord.Color.red())
        embed.add_field(name="Role", value=f"{role.name} (`{role.id}`)", inline=False)
        await self._attach_moderator(role.guild, discord.AuditLogAction.role_delete, role.id, embed)
        await self._send_log(role.guild, EVENT_ROLE_DELETE, embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"**Name:** `{before.name}` → `{after.name}`")
        if before.colors.primary != after.colors.primary:
            changes.append(f"**Color:** `{before.colors.primary}` → `{after.colors.primary}`")
        if before.mentionable != after.mentionable:
            changes.append(f"**Mentionable:** `{before.mentionable}` → `{after.mentionable}`")
        if before.hoist != after.hoist:
            changes.append(f"**Hoisted:** `{before.hoist}` → `{after.hoist}`")
        if before.permissions != after.permissions:
            before_perms = dict(before.permissions)
            after_perms = dict(after.permissions)
            granted = [name for name, value in after_perms.items() if value and not before_perms.get(name)]
            revoked = [name for name, value in before_perms.items() if value and not after_perms.get(name)]
            if granted:
                changes.append("**Permissions granted:** " + ", ".join(granted))
            if revoked:
                changes.append("**Permissions revoked:** " + ", ".join(revoked))
        if not changes:
            return
        embed = self._embed("📝 Role Updated", discord.Color.blurple())
        embed.add_field(name="Role", value=f"{after.mention} (`{after.id}`)", inline=False)
        embed.add_field(name="Changes", value="\n".join(changes), inline=False)
        await self._attach_moderator(after.guild, discord.AuditLogAction.role_update, after.id, embed)
        await self._send_log(after.guild, EVENT_ROLE_UPDATE, embed)


    # -------------------- BOT PERMISSION ERRORS

    @commands.Cog.listener()
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: discord.DiscordException):
        error = getattr(error, "original", error)
        guild = ctx.guild
        if guild is None:
            return
        if ctx.channel.id in set(get_guild_config(guild.id).get_logging_excluded_channels()):
            return
        if isinstance(error, commands.BotMissingPermissions):
            title = "⚠️ Bot Missing Permissions"
            detail_name, detail = "Missing", ", ".join(error.missing_permissions)
        elif isinstance(error, discord.Forbidden):
            title = "⚠️ Bot Forbidden (403)"
            detail_name, detail = "Error", str(error) or "Missing Access (403)"
        else:
            return
        command_name = ctx.command.qualified_name if ctx.command else "unknown"
        embed = self._embed(title, discord.Color.red())
        embed.add_field(name="Command", value=f"`/{command_name}`", inline=False)
        embed.add_field(name=detail_name, value=detail, inline=False)
        embed.add_field(name="Channel", value=ctx.channel.mention, inline=False)
        embed.add_field(name="Triggered by", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
        await self._send_log(guild, EVENT_BOT_PERM_ERROR, embed)


def setup(bot: discord.Bot):
    bot.add_cog(LoggingCog(bot))