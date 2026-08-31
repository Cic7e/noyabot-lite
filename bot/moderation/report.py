import re
from typing import Optional

import discord
from discord.ext import commands

from utils.bot_config import get_guild_config
from utils.guild_db import GuildDB

REPORT_RESOLVE_ID = "report:resolve"
REPORT_DISMISS_ID = "report:dismiss"
_REPORT_ID_RE = re.compile(r"Report ID: (\d+)")
MAX_CONTENT_PREVIEW = 1024
_BUTTON_SPECS = (
    (REPORT_RESOLVE_ID, "Resolve", discord.ButtonStyle.success, "✅"),
    (REPORT_DISMISS_ID, "Dismiss", discord.ButtonStyle.secondary, "🗑️"))


def _snippet(text: Optional[str], limit: int = 200) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ReportActionsView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)
        for custom_id, label, style, emoji in _BUTTON_SPECS:
            button = discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=custom_id)
            button.callback = lambda interaction, cid=custom_id: self._handle(interaction, cid)
            self.add_item(button)


    @staticmethod
    def _extract_report_id(message: Optional[discord.Message]) -> Optional[int]:
        if not message or not message.embeds:
            return None
        match = _REPORT_ID_RE.search(message.embeds[0].footer.text or "")
        return int(match.group(1)) if match else None

    async def _handle(self, interaction: discord.Interaction, custom_id: str):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "You need the Manage Messages permission to handle reports!", ephemeral=True)
            return
        report_id = self._extract_report_id(interaction.message)
        if report_id is None:
            await interaction.response.send_message(
                "Couldn't find a report ID on this message!", ephemeral=True)
            return

        status = "resolved" if custom_id == REPORT_RESOLVE_ID else "dismissed"
        with GuildDB(interaction.guild.id) as db:
            report = db.get_report(report_id)
            if not report:
                await interaction.response.send_message("This report no longer exists!", ephemeral=True)
                return
            if report["status"] != "pending":
                await interaction.response.send_message(
                    f"This report was already {report['status']}.", ephemeral=True)
                return
            db.update_report_status(report_id, status, resolved_by=interaction.user.id)

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.colour = discord.Colour.green() if status == "resolved" else discord.Colour.dark_grey()
        embed.add_field(name="Status", value=f"{status.capitalize()} by {interaction.user.mention}", inline=False)
        disabled = ReportActionsView()
        for child in disabled.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=disabled)


class ReportModal(discord.ui.Modal):

    def __init__(self, cog: "ReportCog", message: discord.Message):
        super().__init__(title="Report Message")
        self.cog = cog
        self.message = message
        self.reason_input = discord.ui.InputText(
            label="Why are you reporting this message?", placeholder="Describe the issue...",
            style=discord.InputTextStyle.long, required=True, max_length=1000)
        self.add_item(self.reason_input)

    async def callback(self, interaction: discord.Interaction):
        await self.cog.process_report(interaction, self.message, self.reason_input.value or "")


class ReportCog(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.bot.add_view(ReportActionsView())


    @staticmethod
    def _build_report_embed(interaction: discord.Interaction, message: discord.Message,
                            reason: str, report_id: int) -> discord.Embed:
        embed = discord.Embed(title="📋 Message Report", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Reported by",
                        value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=True)
        embed.add_field(name="Message Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        content = message.content
        if len(content) > MAX_CONTENT_PREVIEW:
            content = content[: MAX_CONTENT_PREVIEW - 3] + "..."
        embed.add_field(name="Message Content", value=content or "*[No content]*", inline=False)
        if message.attachments:
            embed.add_field(name="Attachments", value="\n".join(a.url for a in message.attachments[:5]), inline=False)
        if reason:
            embed.add_field(name="Reason", value=reason[:1024], inline=False)
        embed.add_field(name="Message Link", value=f"[Jump to message]({message.jump_url})", inline=False)
        embed.set_footer(text=f"Report ID: {report_id} • Message ID: {message.id}")
        return embed

    @commands.message_command(name="Report Message", integration_types={discord.IntegrationType.guild_install})
    async def report_message(self, ctx: discord.ApplicationContext, message: discord.Message):
        if message.author.bot:
            await ctx.respond("You can't report bot messages!", ephemeral=True)
            return
        if message.author == ctx.author:
            await ctx.respond("You can't report your own messages!", ephemeral=True)
            return
        await ctx.response.send_modal(ReportModal(self, message))

    async def process_report(self, interaction: discord.Interaction,
                             message: discord.Message, reason: str):
        guild = interaction.guild
        if guild is None:
            return
        guild_config = get_guild_config(guild.id)
        if not guild_config.get_feature_toggle("report"):
            await interaction.response.send_message(
                "Reporting is currently disabled on this server!", ephemeral=True)
            return
        config = guild_config.get_report_config()
        if not config.get("mod_channel_id"):
            await interaction.response.send_message(
                "Reporting isn't set up on this server yet!", ephemeral=True)
            return
        mod_channel = guild.get_channel(config["mod_channel_id"])
        if not isinstance(mod_channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "The report channel seems to be missing. Please tell an admin!", ephemeral=True)
            return

        with GuildDB(guild.id) as db:
            report_id = db.add_report(
                reporter_id=interaction.user.id, reported_message_id=message.id, reported_channel_id=message.channel.id,
                reported_author_id=message.author.id, reason=reason, message_link=message.jump_url,
                message_snippet=_snippet(message.content))
        embed = self._build_report_embed(interaction, message, reason, report_id)
        try:
            await mod_channel.send(embed=embed, view=ReportActionsView())
        except discord.Forbidden:
            with GuildDB(guild.id) as db:
                db.update_report_status(report_id, "failed")
            await interaction.response.send_message(
                "I don't have permission to send messages in the report channel!", ephemeral=True)
            return
        except discord.HTTPException as exc:
            with GuildDB(guild.id) as db:
                db.update_report_status(report_id, "failed")
            await interaction.response.send_message(f"Failed to send report: {exc}", ephemeral=True)
            return
        await interaction.response.send_message("Report sent to the mod team — thanks!", ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(ReportCog(bot))