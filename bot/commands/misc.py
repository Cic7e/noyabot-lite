import io
import json
import random
from typing import Union

import discord
from discord.ext import commands

from bot.commands.remind import get_time

FORMATS = [
    ("t", "Short time"),
    ("T", "Long time"),
    ("d", "Short date"),
    ("D", "Long date"),
    ("f", "Date & time"),
    ("F", "Full date & time"),
    ("R", "Relative")]

_COPY_ACKNOWLEDGEMENTS = [
    "Message copied! I am legally required to send this", "Copied to your clipboard (probably lol)",
    "Timestamp copied. GO FORTH AND PASTE", "Done! You can close this now. Or don't. I'm not your boss.",
    "Copied! Discord won't let me not send this, so... here we are. Awkward",
    "Copied! That'll be $0.00. We accept tips in the form of exposure bucks"]

def smart_key(x: str):
    try:
        return 0, float(x)
    except ValueError:
        return 1, x.lower()


class TimestampModal(discord.ui.Modal):
    def __init__(self, unix: int, flag: str, label: str):
        super().__init__(title=f"Copy: {label}")
        self.add_item(discord.ui.InputText(
            label=f"<t:{unix}:{flag}>",
            value=f"<t:{unix}:{flag}>",
            style=discord.InputTextStyle.short,
            required=False))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            random.choice(_COPY_ACKNOWLEDGEMENTS), ephemeral=True, delete_after=5)


class TimestampView(discord.ui.View):
    def __init__(self, unix: int):
        super().__init__(timeout=300)
        self.unix = unix
        self.author_id: int | None = None
        self.message: discord.WebhookMessage | None = None
        for flag, label in FORMATS:
            self.add_item(self._make_button(flag, label))

    def _make_button(self, flag: str, label: str):
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=f"ts_{flag}")
        async def callback(interaction: discord.Interaction):
            if interaction.user is None or interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "This isn't your timestamp!", ephemeral=True, delete_after=10)
                return
            await interaction.response.send_modal(TimestampModal(self.unix, flag, label))
        btn.callback = callback
        return btn

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class MiscCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @commands.message_command(name="Extract raw JSON",
                              integration_types={discord.IntegrationType.guild_install,
                                                 discord.IntegrationType.user_install})
    async def extract_json(self, ctx: discord.ApplicationContext, message: discord.Message):
        ephemeral = True if ctx.guild else False
        await ctx.defer(ephemeral=ephemeral)
        raw = await self.bot.http.get_message(message.channel.id, message.id)
        pretty = json.dumps(raw, indent=2)
        await ctx.followup.send(
            content="Here is the full raw JSON of that message:",
            file=discord.File(io.BytesIO(pretty.encode("utf-8")), filename=f"message_{message.id}.json"),
            ephemeral=ephemeral)

    @commands.slash_command(name="timestamp", description="Convert a date/time to a Discord timestamp",
                                integration_types={discord.IntegrationType.guild_install,
                                                   discord.IntegrationType.user_install})
    @discord.option("time", description="When? e.g. 'next Friday at 3pm est', 'in 2 hours'")
    async def timestamp(self, ctx: discord.ApplicationContext, time: str):
        try:
            unix, tz_name = get_time(time)
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return
        header = (f"## Converting {time}\n"
                  f"Resolved to **<t:{unix}:F>** {tz_name} (<t:{unix}:R>)\n"
                  f"Tap a button to copy that format:\n")
        lines = [f"- **{label}** — `<t:{unix}:{flag}>` → <t:{unix}:{flag}>" for flag, label in FORMATS]
        view = TimestampView(unix)
        if ctx.author is not None:
            view.author_id = ctx.author.id
        await ctx.defer(ephemeral=True)
        view.message = await ctx.followup.send(header + "\n".join(lines), view=view, ephemeral=True)
        return

    @commands.slash_command(name="someone", description="Selects a random user from the server with optional filters.",
                            integration_types={discord.IntegrationType.guild_install})
    @discord.option("channel", description="Filter users by text or voice channel.",
                    type=Union[discord.VoiceChannel, discord.TextChannel], default=None)
    @discord.option("role", description="Filter users by role.", type=discord.Role, default=None)
    @discord.option("ping", description="Ping the selected user?", type=bool, default=False)
    @discord.option("text", description="Additional funny text?", type=str, default="")
    async def someone(self, ctx, channel, role, ping, text):
        member_pool = [member for member in ctx.guild.members if not member.bot]
        if role:
            member_pool = [member for member in member_pool if role in member.roles]
        if channel:
            if isinstance(channel, discord.VoiceChannel):
                member_pool = [member for member in member_pool if member.voice and member.voice.channel == channel]
            elif isinstance(channel, discord.TextChannel):
                member_pool = [member for member in member_pool if channel.permissions_for(member).view_channel]
        if not member_pool:
            await ctx.respond("No users found!", ephemeral=True)
            return
        chosen_member = random.choice(member_pool)
        if ping:
            mentions = discord.AllowedMentions.users
        else:
            mentions = discord.AllowedMentions.none()
        await ctx.respond(f"{chosen_member.mention} {text}", allowed_mentions=mentions)

    @commands.slash_command(name="random", description="Randomly pick a word from a list",
                            integration_types={discord.IntegrationType.guild_install})
    @commands.cooldown(3, 5, commands.BucketType.member)
    @discord.option("choices", description="Separate the choices by a comma, period, or semicolon")
    @discord.option("sort", description="How should I process the list?", default="shuffle",
                    choices=[discord.OptionChoice(name="shuffle", value="shuffle"),
                             discord.OptionChoice(name="keep", value="keep"),
                             discord.OptionChoice(name="reverse", value="reverse"),
                             discord.OptionChoice(name="ascending", value="ascending"),
                             discord.OptionChoice(name="descending", value="descending")])
    @discord.option("split", description="Split the result into chunks (1 = no split, max 100)",
                    required=False, min_value=1, max_value=100)
    @discord.option("pick", description="Number of items to pick randomly",
                    required=False, min_value=1)
    @discord.option("duplicates", description="Allow picking the same item multiple times?",
                    required=False, default=False)
    async def rand(self, ctx, choices: str, sort: str = "shuffle", split: int = 0, pick: int = 0,
                   duplicates: bool = False):
        items = [item.strip() for item in choices.translate(str.maketrans(";.", ",,")).split(",") if item.strip()]
        original_order = {item: idx for idx, item in enumerate(items)}
        if pick > 0:
            items = random.choices(items, k=pick) if duplicates else random.sample(items, min(pick, len(items)))
        match sort:
            case "shuffle":
                random.shuffle(items)
            case "keep":
                items.sort(key=lambda x: original_order.get(x, 0))
            case "reverse":
                items.reverse()
            case "ascending" | "descending":
                items.sort(key=smart_key, reverse=(sort == "descending"))
        split = max(1, min(split, len(items)))
        if split == 1:
            result = ", ".join(items)
        else:
            chunks = (items[i * len(items) // split: (i + 1) * len(items) // split] for i in range(split))
            result = " **—** ".join(f"[{', '.join(chunk)}]" for chunk in chunks)
        header_parts = [f"Sort: {sort.capitalize()}"]
        if pick > 0:
            header_parts.append(f"picked {len(items)}")
        if split > 1:
            header_parts.append(f"split in {split}")
        header = ", ".join(header_parts)
        await ctx.respond(f"**{header}**\n> {result}", allowed_mentions=discord.AllowedMentions.none())


def setup(bot: discord.Bot):
    bot.add_cog(MiscCog(bot))
