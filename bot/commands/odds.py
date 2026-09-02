import asyncio
import math
from fractions import Fraction

import discord
from discord.ext import commands

from utils.dice_engine.main_helper import (compile_target, format_roll_message, roll_expression, scale_bar)
from utils.dice_engine.odds_helper import (expression_to_pmf, mean, stdev, percentile, probability_of,
                                           p_at_least, p_at_most, flavor_for_roll)


def _render_pmf_histogram(pmf: dict[int, Fraction], predicate=None, max_rows: int = 20) -> str:
    items = sorted(pmf.items())
    max_p = float(max(p for _, p in items))

    if len(items) > max_rows:
        lo, hi = items[0][0], items[-1][0]
        bucket_size = max(1, math.ceil((hi - lo + 1) / max_rows))
        buckets, bucket_hits = {}, {}
        for v, p in items:
            idx = (v - lo) // bucket_size
            buckets[idx] = buckets.get(idx, Fraction(0)) + p
            if predicate and predicate(v): bucket_hits[idx] = True
        max_p = float(max(buckets.values()))
        lines = []
        for i, p in sorted(buckets.items()):
            b_lo, b_hi = lo + i * bucket_size, min(lo + (i + 1) * bucket_size - 1, hi)
            label = f"{b_lo:>4d}–{b_hi:<4d}" if b_lo != b_hi else f"{b_lo:>4d}     "
            lines.append(f"{label} | {scale_bar(float(p), max_p, hit=bucket_hits.get(i, False))} "
                         f"{float(p) * 100:5.2f}%")
        return "\n".join(lines)

    width = max(len(str(v)) for v, _ in items)
    return "\n".join(f"{str(v):>{width}} | {scale_bar(float(p), max_p, hit=predicate and predicate(v))} "
                     f"{float(p) * 100:5.2f}%" for v, p in items)

def _render_cdf_table(pmf: dict[int, Fraction], predicate=None, max_rows: int = 20) -> str:
    items = sorted(pmf.items())
    # Compute cumulative over the full PMF before sampling the displayed rows
    cum_lookup = {} # it's cumulative but I am immature
    cum = Fraction(0)
    for v, p in items:
        cum += p
        cum_lookup[v] = cum
    total = cum
    # If too many outcomes, sample evenly across the range
    if len(items) > max_rows:
        step = len(items) // max_rows
        items = items[::step][:max_rows]

    width = max(len(str(v)) for v, _ in items)
    width = max(width, 4)  # at least as wide as "Roll"
    rows = [f"{'Roll':>{width}} | Exactly | At most | At least", f"{'─' * width}─┼─────────┼─────────┼─────────"]
    for v, p in items:
        cdf_le = float(cum_lookup[v])
        cdf_ge = float(total - cum_lookup[v] + p)
        mark = " ◄" if predicate and predicate(v) else ""
        rows.append(f"{str(v):>{width}} | {float(p) * 100:>6.2f}% | {cdf_le * 100:>6.2f}% | "
                    f"{cdf_ge * 100:>6.2f}%{mark}")
    return "\n".join(rows)

def _render(mode: str, pmf: dict[int, Fraction], predicate, target_human):
    mu, sd = mean(pmf), stdev(pmf)
    lo, hi = min(pmf.keys()), max(pmf.keys())
    target_block = ""
    if predicate is not None:
        p_hit = probability_of(pmf, predicate)
        frac_str = f" = {p_hit.numerator}/{p_hit.denominator}" if p_hit.denominator <= 10000 else ""
        if p_hit == Fraction(1):
            target_block = f"\n**P(`{target_human}`) = 100%** - guaranteed"
        elif p_hit == Fraction(0):
            target_block = f"\n**P(`{target_human}`) = 0%** - impossible"
        else:
            target_block = (f"\n**P(`{target_human}`) = {float(p_hit) * 100:.4g}%**{frac_str}\n"
                            f"> Odds: 1 in {1 / float(p_hit):.2f}")
    match mode:
        case "summary":
            p25, p50, p75 = percentile(pmf, 0.25), percentile(pmf, 0.50), percentile(pmf, 0.75)
            mode_v, mode_p = max(pmf.items(), key=lambda kv: kv[1])
            body = (
                f"```\n"
                f"Range:     {lo:g} … {hi:g}\nMean:     {mu:.4g}\nMedian:   {p50:g}\n"
                f"Mode:      {mode_v:g}  ({float(mode_p) * 100:.2f}%)\n"
                f"Quartiles: {p25:g} | {p50:g} | {p75:g}\nStd dev:  ±{sd:.4g}\n\n"
                f"{_render_pmf_histogram(pmf, predicate)}\n```")
            return body + target_block
        case "cdf":
            body = f"```\n{_render_cdf_table(pmf, predicate)}\n```"
            return body + target_block
    return None


class OddsView(discord.ui.View):
    def __init__(self, pmf, predicate, target_human, header, author_id, expression, guild_id=None):
        super().__init__(timeout=300)
        self.pmf = pmf
        self.predicate = predicate
        self.target_human = target_human
        self.header = header
        self.author_id = author_id
        self.expression = expression
        self.guild_id = guild_id
        self.current_mode = "summary"
        self.message = None
        self._update_button_styles()

    def _update_button_styles(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                # The Roll button keeps its green styling and isn't a mode toggle
                if child.custom_id == "roll":
                    continue
                child.style = (discord.ButtonStyle.primary if child.custom_id == self.current_mode
                               else discord.ButtonStyle.secondary)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the user who ran the command can change the view!", ephemeral=True, delete_after=10)
            return False
        return True

    async def _switch_mode(self, interaction: discord.Interaction, mode: str):
        self.current_mode = mode
        self._update_button_styles()
        body = _render(mode, self.pmf, self.predicate, self.target_human)
        msg = f"{self.header}\n{body}"
        if len(msg) > 1950:
            msg = msg[:1940] + "\n> (output truncated)"
        await interaction.response.edit_message(content=msg, view=self)

    @discord.ui.button(label="Roll", style=discord.ButtonStyle.success, custom_id="roll")
    async def roll_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        # Disable the roll button immediately to prevent double-clicks
        button.disabled = True
        try:
            total, breakdown_string = roll_expression(self.expression, sort=False)
        except (ValueError, TypeError, SyntaxError, KeyError, ZeroDivisionError) as e:
            # Update view to keep button disabled, then send error as follow-up
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"Couldn't roll that: `{e}`", ephemeral=True)
            return
        roll_response = format_roll_message(
            f"{interaction.user.mention} tested for `{self.expression}`", total, breakdown_string)
        if self.predicate is not None:
            try:
                hit = self.predicate(total)
                verdict = (f"\n-# ⊹ Cleared the target `{self.target_human}`!" if hit
                           else f"\n-# Missed the target `{self.target_human}`")
                roll_response += verdict
            except (TypeError, ValueError):
                pass
        try:
            int_total = int(total) if float(total).is_integer() else total
            p_better = p_at_least(self.pmf, int_total)
            p_worse = p_at_most(self.pmf, int_total)
            flavor = flavor_for_roll(int_total, self.pmf, p_better, p_worse)
            if flavor:
                roll_response += f"\n-# {flavor}"
        except (TypeError, ValueError, KeyError):
            pass  # Don't let flavor failures break the roll
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(roll_response, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Summary", style=discord.ButtonStyle.primary, custom_id="summary")
    async def summary_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._switch_mode(interaction, "summary")

    @discord.ui.button(label="CDF", style=discord.ButtonStyle.secondary, custom_id="cdf")
    async def cdf_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._switch_mode(interaction, "cdf")

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class OddsCog(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @commands.slash_command(name="odds", description="Compute exact probabilities for a dice expression",
                            integration_types={discord.IntegrationType.guild_install,
                                               discord.IntegrationType.user_install})
    @commands.cooldown(3, 5, commands.BucketType.member)
    @discord.option("expression", description="A dice expression, e.g., 1d20+5, 4d6kh3, 2d6-1d4")
    @discord.option("target", description="Optional condition: >=15, =20, 8-12, odd, !even", default="")
    @discord.option("whisper", description="Should the result be visible only to you?", default=False)
    async def odds(self, ctx, expression: str, target: str, whisper: bool):
        await ctx.defer(ephemeral=whisper)
        user_input = expression.replace(' ', '').lower()
        if len(user_input) > 256:
            await ctx.followup.send("That expression is too long.", ephemeral=True)
            return
        try: predicate, target_human = compile_target(target)
        except ValueError as e:
            await ctx.followup.send(f"Target error: `{e}`")
            return
        try: pmf = await asyncio.to_thread(expression_to_pmf, user_input)
        except NotImplementedError as e:
            await ctx.followup.send(f"Expression error: `{e}`", ephemeral=True)
            return
        except ValueError as e:
            await ctx.followup.send(f"Expression too complex: `{e}`")
            return
        except (TypeError, SyntaxError, ZeroDivisionError) as e:
            await ctx.followup.send(f"Parse error: `{e}`")
            return
        header = f"**Exact odds for `{expression}`** ({len(pmf):,} outcomes)"
        view = OddsView(pmf, predicate, target_human, header, ctx.author.id, expression,
                        guild_id=ctx.guild.id if ctx.guild else None)
        body = _render("summary", pmf, predicate, target_human)
        msg = f"{header}\n{body}"
        if len(msg) > 1950:
            msg = msg[:1940] + "\n> (output truncated)"
        view.message = await ctx.followup.send(msg, view=view, allowed_mentions=discord.AllowedMentions.none())

def setup(bot: discord.Bot):
    bot.add_cog(OddsCog(bot))