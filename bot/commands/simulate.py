import asyncio
import math
import statistics
import time
from collections import Counter

import discord
from discord.ext import commands

from utils.dice_engine.main_helper import compile_target
from utils.dice_engine.sim_helper import analyze_streaks, render_histogram, run_simulation, wilson_ci

MAX_TRIALS = 1_000_000
TIME_BUDGET_SECONDS = 8.0


def _render(mode: str, results: list[float], predicate, target_human: str | None):
    mean = statistics.fmean(results)
    median = statistics.median(results)
    lo, hi = min(results), max(results)
    stdev = statistics.pstdev(results) if len(results) > 1 else 0.0
    target_hits = None
    target_block = ""
    if predicate is not None:
        hits = sum(1 for r in results if predicate(r))
        target_hits = hits
        rate = hits / len(results)
        ci_lo, ci_hi = wilson_ci(hits, len(results))
        target_block = (
            f"**Target `{target_human}`:**  hit **{hits:,}** times (**{rate * 100:.2f}%**)\n"
            f"> 95% CI: {ci_lo * 100:.2f}% – {ci_hi * 100:.2f}%")
    stats = {"trials": len(results), "mean": mean, "median": median, "min": lo, "max": hi,
             "stdev": stdev, "target_hits": target_hits}
    match mode:
        case "summary":
            counts = Counter(results)
            mode_val, mode_count = counts.most_common(1)[0]
            lo_count = counts[lo]
            hi_count = counts[hi]
            total = len(results)
            # Quartiles (need at least 2 points for statistics.quantiles)
            if total >= 2:
                q1, _, q3 = statistics.quantiles(results, n=4, method="inclusive")
            else:
                q1 = q3 = results[0]
            summary = (
                f"```\n"
                f"Trials:          {total:,}\n"
                f"Unique values:   {len(counts):,}\n"
                f"\n"
                f"Mean:            {mean:.4g}\n"
                f"Median:          {median:g}\n"
                f"Mode:            {mode_val:g}  (came up {mode_count:,}×, {mode_count / total * 100:.2f}%)\n"
                f"\n"
                f"Lowest:          {lo:g}  ({lo_count:,}×, {lo_count / total * 100:.2f}%)\n"
                f"Highest:         {hi:g}  ({hi_count:,}×, {hi_count / total * 100:.2f}%)\n"
                f"Middle 50% fell between {q1:g} and {q3:g}\n"
                f"\n"
                f"Typical spread:  ±{stdev:.4g} from the mean\n"
                f"```")
            return (f"{summary}\n{target_block}" if target_block else summary), stats
        case "distribution":
            hist = render_histogram(results, predicate=predicate)
            stats_line = f"Mean {mean:.4g} · Median {median:g} · σ {stdev:.4g}"
            parts = [f"```\n{hist}\n\n{stats_line}\n```"]
            if target_block:
                parts.append(target_block)
            return "\n".join(parts), stats
        case "streaks":
            s = analyze_streaks(results, predicate)
            total = len(results)
            rate = s["hits"] / total if total else 0
            miss_rate = 1 - rate
            def expected_longest(p: float) -> str:
                return f"~{math.log(total) / math.log(1 / p):.1f}" if 0 < p < 1 else "n/a"
            final_str = f"{s['final_length']}-{s['final_type']} streak" if s["final_type"] else "—"
            streaks_block = (
                f"```\n"
                f"Hits:   {s['hits']:>7,}  ({rate * 100:.2f}%)\n"
                f"Misses: {s['misses']:>7,}  ({miss_rate * 100:.2f}%)\n"
                f"\n"
                f"                   Longest   Average    Runs\n"
                f"Hit streaks:       {s['longest_hit']:>7}   {s['avg_hit']:>7.2f}   {s['num_hit_streaks']:>5,}\n"
                f"Miss streaks:      {s['longest_miss']:>7}   {s['avg_miss']:>7.2f}   {s['num_miss_streaks']:>5,}\n"
                f"\n"
                f"Expected longest hit:  {expected_longest(rate)}\n"
                f"Expected longest miss: {expected_longest(miss_rate)}\n"
                f"\n"
                f"Ended on: {final_str}\n"
                f"```")
            return (f"{streaks_block}\n{target_block}" if target_block else streaks_block), stats
        case _:
            raise ValueError(f"Unknown render mode: {mode!r}")


class SimulationView(discord.ui.View):
    def __init__(self, results: list[float], predicate, target_human: str | None,
                 header: str, author_id: int):
        super().__init__(timeout=300)
        self.results = results
        self.predicate = predicate
        self.target_human = target_human
        self.header = header
        self.author_id = author_id
        self.current_mode = "summary"
        # Disable streaks button if no target was given
        if predicate is None:
            self.streaks_button.disabled = True
        self._update_button_styles()

    def _update_button_styles(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == self.current_mode:
                    child.style = discord.ButtonStyle.primary
                else:
                    child.style = discord.ButtonStyle.secondary
                    # Never override the disabled streak button styling
                    if child.custom_id == "streaks" and self.predicate is None:
                        child.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the user who ran the simulation can change the view!", ephemeral=True, delete_after=10)
            return False
        return True

    async def _switch_mode(self, interaction: discord.Interaction, mode: str):
        self.current_mode = mode
        self._update_button_styles()
        body, _ = _render(mode, self.results, self.predicate, self.target_human)
        msg = f"{self.header}\n{body}"
        if len(msg) > 1950:
            msg = msg[:1940] + "\n> (output truncated)"
        await interaction.response.edit_message(content=msg, view=self)

    @discord.ui.button(label="Summary", style=discord.ButtonStyle.primary, custom_id="summary")
    async def summary_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._switch_mode(interaction, "summary")

    @discord.ui.button(label="Distribution", style=discord.ButtonStyle.secondary, custom_id="distribution")
    async def distribution_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._switch_mode(interaction, "distribution")

    @discord.ui.button(label="Streaks", style=discord.ButtonStyle.secondary, custom_id="streaks")
    async def streaks_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._switch_mode(interaction, "streaks")

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        # Edit the original message if we still have a reference to it
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SimulateCog(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @commands.slash_command(name="simulate", description="Run /roll many times and analyze the outcome",
                            integration_types={discord.IntegrationType.guild_install})
    @commands.cooldown(2, 10, commands.BucketType.member)
    @discord.option("expression", description="A dice or math expression, e.g., 1d20, 2d6+3, 4d6")
    @discord.option("trials", description="How many times to run it (1 – 1,000,000)",
                    min_value=1, max_value=MAX_TRIALS)
    @discord.option("target", description="Optional success condition, e.g., >=15, =20, 8-12, odd",
                    required=False, default="")
    @discord.option("modifier", description="Roll multiple times: >0 keeps highest, <0 keeps lowest",
                    type=int, min_value=-10, max_value=10, default=0)
    @discord.option("whisper", description="Should the result be visible only to you?", default=False)
    async def simulate(self, ctx, expression: str, trials: int, target: str,
                       modifier: int, whisper: bool):
        await ctx.defer(ephemeral=whisper)
        user_input = expression.replace(' ', '').lower()
        if len(user_input) > 512:
            await ctx.followup.send("That expression is too long. Please try something shorter!", ephemeral=True)
            return
        # Compile a target if given
        try:
            predicate, target_human = compile_target(target)
        except ValueError as e:
            await ctx.followup.send(f"Wait, I couldn't parse that: `{e}`")
            return
        # Run simulation off the event loop
        deadline = time.monotonic() + TIME_BUDGET_SECONDS
        try:
            results, timed_out = await asyncio.to_thread(run_simulation, user_input, trials, deadline, modifier)
        except (ValueError, TypeError, SyntaxError, KeyError, ZeroDivisionError) as e:
            await ctx.followup.send(f"Sorry, there was an issue with your expression: `{e}`")
            return
        # Build header
        header = f"Simulated `{expression}` × **{trials:,}**"
        if modifier > 0:
            header += f" *(keep highest of {modifier})*"
        elif modifier < 0:
            header += f" *(keep lowest of {abs(modifier)})*"
        if timed_out:
            header += (f"\n-# ⚠ Simulation hit the {TIME_BUDGET_SECONDS:g}s time limit — showing results from "
                       f"**{len(results):,}** of {trials:,} trials")
        # Build view and initial message
        view = SimulationView(results, predicate, target_human, header, ctx.author.id)
        body, stats = _render("summary", results, predicate, target_human)
        msg = f"{header}\n{body}"
        if len(msg) > 1950:
            msg = msg[:1940] + "\n> (output truncated)"
        view.message = await ctx.followup.send(msg, view=view, allowed_mentions=discord.AllowedMentions.none())

def setup(bot: discord.Bot):
    bot.add_cog(SimulateCog(bot))
