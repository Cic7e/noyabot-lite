import math
import statistics
import time
from collections import Counter

from utils.dice_engine.main_helper import evaluate_expression, scale_bar


def wilson_ci(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    # 95% Wilson confidence interval for a proportion
    if trials == 0:
        return 0.0, 0.0
    p = successes / trials
    denom = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denom
    margin = (z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)

def render_histogram(results: list[float], max_rows: int = 20, predicate=None) -> str:
    all_ints = all(float(r).is_integer() for r in results)
    counts = Counter(int(r) if all_ints else round(r, 2) for r in results)
    keys = sorted(counts.keys())
    total = len(results)
    max_count = max(counts.values())
    # Bucket if too many distinct values
    if len(keys) > max_rows:
        lo, hi = keys[0], keys[-1]
        if all_ints:
            # Ensure each bucket spans a whole number of ints
            span = hi - lo + 1
            bucket_size = max(1, math.ceil(span / max_rows))
            num_buckets = math.ceil(span / bucket_size)
        else:
            bucket_size = (hi - lo) / max_rows or 1
            num_buckets = max_rows
        buckets = Counter()
        bucket_hits: dict[int, bool] = {}
        for v, c in counts.items():
            idx = min(int((v - lo) / bucket_size), num_buckets - 1)
            buckets[idx] += c
            if predicate is not None and predicate(v):
                bucket_hits[idx] = True
        bucket_max = max(buckets.values()) if buckets else 1
        lines = []
        for i in range(num_buckets):
            b_lo = lo + i * bucket_size
            b_hi = lo + (i + 1) * bucket_size
            c = buckets.get(i, 0)
            is_hit = bucket_hits.get(i, False)
            bar = scale_bar(c, bucket_max, hit=is_hit)
            pct = (c / total * 100) if total else 0
            if all_ints:
                # Inclusive integer range, clamp upper edge to hi
                b_hi_int = int(min(int(b_hi) - 1, hi))
                label = f"{int(b_lo):>4d}–{b_hi_int:<4d}"
            else:
                label = f"{b_lo:>7.2f}–{b_hi:<7.2f}"
            lines.append(f"{label} | {bar} {c:>6,} ({pct:.2f}%)")
        return "\n".join(lines)
    lines = []
    key_width = max(len(str(k)) for k in keys)
    for k in keys:
        c = counts[k]
        is_hit = predicate is not None and predicate(k)
        bar = scale_bar(c, max_count, hit=is_hit)
        pct = (c / total * 100) if total else 0
        lines.append(f"{str(k):>{key_width}} | {bar} {c:>6,} ({pct:.2f}%)")
    return "\n".join(lines)

def analyze_streaks(results: list[float], predicate) -> dict:
    hit_streaks, miss_streaks = [], []
    cur_hit = cur_miss = hits = 0
    for r in results:
        if predicate(r):
            hits += 1
            cur_hit += 1
            if cur_miss > 0:
                miss_streaks.append(cur_miss)
                cur_miss = 0
        else:
            cur_miss += 1
            if cur_hit > 0:
                hit_streaks.append(cur_hit)
                cur_hit = 0
    # Close out the final streak
    if cur_hit > 0: hit_streaks.append(cur_hit)
    elif cur_miss > 0: miss_streaks.append(cur_miss)
    return {"hits": hits, "misses": len(results) - hits, "longest_hit": max(hit_streaks, default=0),
            "longest_miss": max(miss_streaks, default=0),
            "avg_hit": statistics.fmean(hit_streaks) if hit_streaks else 0.0,
            "avg_miss": statistics.fmean(miss_streaks) if miss_streaks else 0.0, "num_hit_streaks": len(hit_streaks),
            "num_miss_streaks": len(miss_streaks),
            "final_type": "hit" if cur_hit > 0 else ("miss" if cur_miss > 0 else None),
            "final_length": cur_hit or cur_miss}


def run_simulation(expression: str, trials: int, deadline: float, modifier: int = 0) -> tuple[list[float], bool]:
    results = []
    num_rolls = max(1, abs(modifier))
    # Check deadline periodically to avoid overhead on every iter
    check_every = max(1, trials // 100)
    for i in range(trials):
        if i % check_every == 0 and time.monotonic() > deadline:
            return results, True
        if modifier > 0:
            results.append(max(evaluate_expression(expression) for _ in range(num_rolls)))
        elif modifier < 0:
            results.append(min(evaluate_expression(expression) for _ in range(num_rolls)))
        else:
            results.append(evaluate_expression(expression))
    return results, False