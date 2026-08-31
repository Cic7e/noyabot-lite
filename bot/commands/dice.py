import asyncio
import random
import re
from typing import Callable, Optional

import discord
from discord.ext import commands

from utils.dice_engine.main_helper import compile_target, format_roll_message, roll_expression
from utils.dice_engine.odds_helper import expression_to_pmf, p_at_least, p_at_most, flavor_for_roll


def _pick_from_table(entries: list[dict], count: int, allow_dupes: bool) -> list[str]:
    texts = [e["text"] for e in entries]
    weights = [e.get("weight", 1) for e in entries]
    if allow_dupes:
        return random.choices(texts, weights=weights, k=count)
    picked = []
    pool_texts = list(texts)
    pool_weights = list(weights)
    for _ in range(min(count, len(pool_texts))):
        choice = random.choices(pool_texts, weights=pool_weights, k=1)[0]
        idx = pool_texts.index(choice)
        picked.append(choice)
        pool_texts.pop(idx)
        pool_weights.pop(idx)
    return picked

def _format_roll_header(mention: str, cmd: str, display_expr: str,
                        alias: Optional[str] = None) -> str:
    if alias:
        suffix = " again" if cmd == "reroll" else "!"
        return f"{mention} uses **{alias}**{suffix}!\n`{display_expr}`"
    return f"{mention} {cmd}ed `{display_expr}`"

async def _execute_roll(ctx, cmd: str, user_input: str, display_expr: str,
                        sort: bool, target: str, alias: Optional[str] = None):
    # Compile target if provided
    predicate: Callable[[float], bool] | None = None
    target_human: str | None = None
    if target:
        try:
            predicate, target_human = compile_target(target)
        except ValueError as e:
            await ctx.followup.send(f"Target error: `{e}`")
            return
    try:
        total, breakdown_string = roll_expression(user_input, sort=sort)
        header = _format_roll_header(ctx.author.mention, cmd, display_expr, alias)
        final_response = format_roll_message(header, total, breakdown_string)
    except (ValueError, TypeError, SyntaxError, KeyError, ZeroDivisionError) as e:
        if re.search(r'[dD].*\(.*[dD].*\(', user_input):
            await ctx.followup.send("My abacus just filed a restraining order. "
                                    "Try something like 2d(5+1d5) instead")
            return
        await ctx.followup.send(f"Sorry, there was an error with your roll: `{e}`")
        return
    # Target verdict (only needs the predicate, not the PMF)
    if predicate is not None and target_human is not None:
        try:
            hit = predicate(total)
            verdict = (f"\n-# ⊹ Cleared the target `{target_human}`!" if hit
                       else f"\n-# Missed the target `{target_human}`")
            final_response += verdict
        except (TypeError, ValueError):
            pass
    try:
        pmf = await asyncio.to_thread(expression_to_pmf, user_input)
        int_total = int(total) if isinstance(total, (int, float)) and float(total).is_integer() else total
        p_better = p_at_least(pmf, int_total)
        p_worse = p_at_most(pmf, int_total)
        flavor = flavor_for_roll(int_total, pmf, p_better, p_worse)
        if flavor:
            final_response += f"\n-# {flavor}"
    except (ValueError, NotImplementedError, TypeError, SyntaxError, ZeroDivisionError, KeyError):
        pass  # PMF too complex for exact computation - skip luck/flavor
    await ctx.followup.send(final_response, allowed_mentions=discord.AllowedMentions.none())


class DiceCog(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot

    macro = discord.SlashCommandGroup("macro", "Use saved dice macros and random tables!",
                                      integration_types={discord.IntegrationType.guild_install})

    @commands.slash_command(name="roll", description="Roll some dice!",
                                integration_types={discord.IntegrationType.guild_install,
                                                   discord.IntegrationType.user_install})
    @commands.cooldown(3, 5, commands.BucketType.member)
    @discord.option("dice", description="A number of sides or dice notation (e.g., 20, 1d20, 2d6+5), 5d6k3")
    @discord.option("sort", description="Display as-is or sort by descending?", default=False)
    @discord.option("target", description="Optional success condition: >=15, =20, 8-12, odd, !even",
                        required=False, default="")
    @discord.option("whisper", description="Should the result be visible only to you?", default=False)
    async def roll(self, ctx,
                   dice: str, sort: bool, target: str, whisper: bool):
        await ctx.defer(ephemeral=whisper)
        user_input = dice.replace(' ', '').lower()
        if len(user_input) > 1024:
            await ctx.followup.send("I'm....not rolling this", ephemeral=True)
            return
        await _execute_roll(ctx, cmd="roll", user_input=user_input, display_expr=dice, sort=sort, target=target)

def setup(bot: discord.Bot):
    bot.add_cog(DiceCog(bot))
