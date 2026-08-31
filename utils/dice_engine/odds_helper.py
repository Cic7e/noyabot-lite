import math
import random
import re
from collections import defaultdict
from fractions import Fraction
from itertools import combinations_with_replacement

from utils.dice_engine.main_helper import safe_eval

MAX_OUTCOMES = 100_000
MAX_DICE_FOR_KEEP = 30
MAX_CONVOLUTION_WORK = 10_000_000
PMF = dict[int, Fraction]  # outcome -> probability
_TERM_RE = re.compile(r'(\d*)[dD](\d+)(?:(k[hl]?|[hl])(\d+))?')

_TIERS_MAX = [  # Best of the best
    "The maximum possible outcome - only a {pct:.2f}% chance!",
    "Legendary. 1 in {one_in:.0f} rolls go this well ({pct:.2f}%)",
    "Couldn't have rolled higher if you tried ({pct:.2f}%)",
    "You're welcome... {pct:.2f}% odds"]
_TIERS_HIGH = [  # ≤ 10% chance of doing this well or better
    "Wildly lucky - only a {pct:.2f}% chance of doing this well",
    "1 in {one_in:.0f} odds ({pct:.2f}%). Did you bribe the dice?",
    "A standout result - top {pct:.2f}% of outcomes",
    "Well above the curve - only {pct:.2f}% of rolls go this well"]
_TIERS_MID = [  # ≤ 25% on either side - somewhat notable
    "A bit off-center - {pct:.2f}% of rolls land here or further out",
    "Slightly off the curve ({pct:.2f}%)",
    "Mildly noteworthy - {pct:.2f}% tail probability",
    "Nudged away from average ({pct:.2f}%)"]
_TIERS_LOW = [  # ≤ 10% chance of doing this badly or worse
    "Statistically miserable - only a {pct:.2f}% chance of rolling this poorly",
    "1 in {one_in:.0f} rolls go this poorly ({pct:.2f}%). RIP",
    "A rough result - bottom {pct:.2f}% of outcomes",
    "Well below the curve - only {pct:.2f}% of rolls go this poorly"]
_TIERS_MIN = [  # Worst of the worst
    "The worst possible outcome - only a {pct:.2f}% chance",
    "Rock bottom. 1 in {one_in:.0f} rolls are this catastrophic ({pct:.2f}%)",
    "Couldn't have rolled lower if you tried ({pct:.2f}%)",
    "The literal worst case - {pct:.2f}% odds"]


def _estimate_sum_dice_cost(num_dice: int, sides: int) -> tuple[int, int]:
    if num_dice < 1:
        return 1, 0
    final_size = num_dice * (sides - 1) + 1
    # Sum of arithmetic series for convolution costs
    work = 0
    current_size = sides
    for _ in range(num_dice - 1):
        work += current_size * sides
        current_size += sides - 1
        if work > MAX_CONVOLUTION_WORK:
            return final_size, work  # bail early, caller will reject
    return final_size, work

def uniform_die(sides: int) -> PMF:
    if sides < 1:
        raise ValueError(f"Die must have at least 1 side, got {sides}")
    if sides > MAX_OUTCOMES:
        raise ValueError(f"A d{sides:,} has more outcomes than /odds can handle! (limit: {MAX_OUTCOMES:,}) "
                         f"Try /simulate")
    p = Fraction(1, sides)
    return {face: p for face in range(1, sides + 1)}

def constant(value: int) -> PMF:
    return {value: Fraction(1)}

def convolve(a: PMF, b: PMF) -> PMF:
    # Pre-flight: refuse before allocating if the work would be massive.
    if len(a) * len(b) > MAX_CONVOLUTION_WORK:
        raise ValueError(f"Computing this exactly would require {len(a) * len(b):,} operations! "
                         f"Try /simulate for an empirical answer")
    result: PMF = defaultdict(Fraction)
    for av, ap in a.items():
        for bv, bp in b.items():
            result[av + bv] += ap * bp
    if len(result) > MAX_OUTCOMES:
        raise ValueError(f"Distribution too large! ({len(result):,} outcomes) Try /simulate for an empirical answer")
    return dict(result)

def negate(a: PMF) -> PMF:
    # PMF of -A
    return {-v: p for v, p in a.items()}

def scale(a: PMF, k: int) -> PMF:
    # PMF of k*A for integer k
    return {v * k: p for v, p in a.items()}

def sum_dice(num_dice: int, sides: int) -> PMF:
    # PMF of NdM via repeated convolution
    if num_dice < 0:
        return negate(sum_dice(-num_dice, sides))
    if num_dice == 0:
        return constant(0)
    # Pre-flight cost estimate before doing any work
    final_size, est_work = _estimate_sum_dice_cost(num_dice, sides)
    if final_size > MAX_OUTCOMES:
        raise ValueError(f"{num_dice}d{sides} has {final_size:,} possible outcomes! (limit: {MAX_OUTCOMES:,}) "
                         f"Try /simulate")
    if est_work > MAX_CONVOLUTION_WORK:
        raise ValueError(f"{num_dice}d{sides} would need ~{est_work:,} operations to compute! Try /simulate")
    die = uniform_die(sides)
    result = die
    for _ in range(num_dice - 1):
        result = convolve(result, die)
    return result

def keep_dice(num_dice: int, sides: int, keep: int, highest: bool = True) -> PMF:
    # PMF of NdMkK via direct enumeration of sorted outcomes
    # Uses the multinomial formula over sorted tuples to avoid listing all
    # M**N raw outcomes. Still bounded by the number of sorted multisets
    num_multisets = math.comb(num_dice + sides - 1, num_dice)
    if num_multisets > MAX_CONVOLUTION_WORK:
        raise ValueError(f"{num_dice}d{sides} keep-{'highest' if highest else 'lowest'}-{keep} "
                         f"has {num_multisets:,} sorted outcomes, try /simulate")
    if not (1 <= keep <= num_dice):
        raise ValueError(f"Cannot keep {keep} of {num_dice} dice")
    result: PMF = defaultdict(Fraction)
    denom = Fraction(1, sides ** num_dice)
    for combo in combinations_with_replacement(range(1, sides + 1), num_dice):
        ways = math.factorial(num_dice)
        for face in set(combo):
            ways //= math.factorial(combo.count(face))
        kept_sum = sum(sorted(combo, reverse=highest)[:keep])
        result[kept_sum] += ways * denom
    return dict(result)

def pmf_for_term(term: str) -> PMF:
    m = _TERM_RE.fullmatch(term)
    if not m:
        raise NotImplementedError(f"Unsupported term {term}, try /simulate")
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    if m.group(3):
        return keep_dice(count, sides, int(m.group(4)), highest='l' not in m.group(3).lower())
    return sum_dice(count, sides)

def expression_to_pmf(expression: str) -> PMF:
    expr = expression.replace(' ', '').lower()

    # Split into dice terms and "everything else" (which must be plain integer math)
    pieces: list[tuple[str, str]] = []  # (kind, text) where kind in {'dice', 'math'}
    last = 0
    for m in re.finditer(_TERM_RE, expr):
        if m.start() > last:
            pieces.append(('math', expr[last:m.start()]))
        pieces.append(('dice', m.group(0)))
        last = m.end()
    if last < len(expr):
        pieces.append(('math', expr[last:]))

    # Walk pieces, accumulating a running PMF. Math pieces between dice terms
    # are interpreted as +/- with optional integer literals
    if not any(kind == 'dice' for kind, _ in pieces):
        val = safe_eval(expr)
        if not float(val).is_integer():
            raise NotImplementedError(f"Integer outcomes only; {expr} evaluates to {val}")
        return constant(int(val))

    result: PMF | None = None
    pending_sign = 1
    for kind, text in pieces:
        if kind == 'math':
            stripped = text.strip()
            if not stripped: continue
            m = re.match(r'^(.*?)([+\-])$', stripped)
            head, head_sign = (m.groups()[0], -1 if m.groups()[1] == '-' else 1) if m else (stripped, None)
            if head:
                try:
                    head_val = int(safe_eval(head)) if head not in ('+', '-') else 0
                except (ValueError, SyntaxError):
                    raise NotImplementedError(f"Can't handle {head} exactly, try /simulate")
                if head_val:
                    contribution = constant(pending_sign * head_val)
                    result = contribution if result is None else convolve(result, contribution)
            if head_sign is not None:
                pending_sign = head_sign
        else:
            term_pmf = negate(pmf_for_term(text)) if pending_sign == -1 else pmf_for_term(text)
            result = term_pmf if result is None else convolve(result, term_pmf)
            pending_sign = 1

    return result or constant(0)

def mean(pmf: PMF) -> float:
    return float(sum(v * p for v, p in pmf.items()))

def variance(pmf: PMF) -> float:
    mu = mean(pmf)
    return float(sum((v - mu) ** 2 * p for v, p in pmf.items()))

def stdev(pmf: PMF) -> float:
    return variance(pmf) ** 0.5

def percentile(pmf: PMF, q: float) -> int:
    cumulative = Fraction(0)
    for v in sorted(pmf.keys()):
        cumulative += pmf[v]
        if cumulative >= q:
            return v
    return max(pmf.keys())

def probability_of(pmf: PMF, predicate) -> Fraction:
    return sum((p for v, p in pmf.items() if predicate(v)), Fraction(0))

def p_at_least(pmf: PMF, value) -> Fraction:
    return sum((p for v, p in pmf.items() if v >= value), Fraction(0))

def p_at_most(pmf: PMF, value) -> Fraction:
    return sum((p for v, p in pmf.items() if v <= value), Fraction(0))

def p_greater(a: PMF, b: PMF) -> Fraction:
    return sum((ap * bp for av, ap in a.items() for bv, bp in b.items() if av > bv), Fraction(0))

def p_equal(a: PMF, b: PMF) -> Fraction:
    return sum((ap * bp for av, ap in a.items() for bv, bp in b.items() if av == bv), Fraction(0))

def margin_pmf(a: PMF, b: PMF) -> PMF:
    return convolve(a, negate(b))

def flavor_for_roll(roll_value, pmf, p_better_or_equal, p_worse_or_equal) -> str | None:
    if not pmf:
        return None
    lo, hi = min(pmf.keys()), max(pmf.keys())
    p_better = float(p_better_or_equal)
    p_worse = float(p_worse_or_equal)
    if roll_value >= hi:
        return _format(random.choice(_TIERS_MAX), p_better)
    if roll_value <= lo:
        return _format(random.choice(_TIERS_MIN), p_worse)
    if p_better <= 0.10:
        return _format(random.choice(_TIERS_HIGH), p_better)
    if p_worse <= 0.10:
        return _format(random.choice(_TIERS_LOW), p_worse)
    if p_better <= 0.25:
        return _format(random.choice(_TIERS_MID), p_better)
    if p_worse <= 0.25:
        return _format(random.choice(_TIERS_MID), p_worse)
    return None

def _format(template: str, p: float) -> str:
    pct = p * 100
    one_in = (1 / p) if p > 0 else float('inf')
    return template.format(pct=pct, one_in=one_in)
