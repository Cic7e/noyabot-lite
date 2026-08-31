import ast
import operator as op
import random
import re
from typing import Callable

# Shared histogram bar rendering (used by odds_helper and sim_helper)
BAR_BLOCKS = " ▏▎▍▌▋▊▉█"
HIT_FULL = "▒"
HIT_FRAC = "░"

ALLOWED_OPERATORS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
                     ast.Pow: op.pow, ast.USub: op.neg, ast.UAdd: op.pos}
ALLOWED_NODES = [ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, *ALLOWED_OPERATORS.keys()]
VALID_MATH_PATTERN = re.compile(r'^[0-9+\-*/^()\s]+$')
DICE_PATTERN_STR = r'(?:[\d.]|\([^)]+\))*[dD](?:[\d.]|\([^)]+\))+(?:(?:k[hl]?|[hl])(?:[\d.]|\([^)]+\))*)?'


def roll_dice(num_dice: int, num_sides: int) -> tuple[int, list[int]]:
    if num_dice > 9999:
        raise ValueError("You can't roll more than 9999 dice at once!")
    if num_sides > 999999999:
        raise ValueError("A die can't have more than 999999999 sides")
    if num_dice < 1 or num_sides < 1:
        return 0, []
    rolls = [random.randint(1, num_sides) for _ in range(num_dice)]
    return sum(rolls), rolls

def safe_eval(expression: str):
    expression = str(expression).replace('^', '**')
    if not expression or not expression.strip():
        return 0
    tree = ast.parse(expression, mode='eval')
    for node in ast.walk(tree):
        if type(node) not in ALLOWED_NODES:
            raise ValueError(f"Invalid expression: Disallowed node {type(node).__name__}")

    def _eval_node(node):
        match node:
            case ast.Constant(value=value):
                if not isinstance(value, (int, float)):
                    raise ValueError("Only numeric constants are allowed.")
                return value
            case ast.BinOp(left=left, op=op_type, right=right):
                return ALLOWED_OPERATORS[type(op_type)](_eval_node(left), _eval_node(right))
            case ast.UnaryOp(op=op_type, operand=operand):
                return ALLOWED_OPERATORS[type(op_type)](_eval_node(operand))
            case ast.Expression(body=body):
                return _eval_node(body)
            case _:
                raise TypeError(node)
    return _eval_node(tree)

def parse_and_roll(dice_string: str, sort: bool = False) -> tuple[str, str]:
    breakdown_parts = []

    def roll_callback(match):
        full_match = match.group(0)
        # Check for keep highest/lowest
        keep_type = None
        keep_count_str = None
        keep_match = re.search(r'(?i)(k[hl]?|[hl])', full_match)
        if keep_match:
            keep_str = keep_match.group(1).lower()
            keep_type = 'kl' if 'l' in keep_str else 'kh'
            base_dice = full_match[:keep_match.start()]
            keep_count_str = full_match[keep_match.end():]
            if not keep_count_str:
                keep_count_str = "1"
        else:
            base_dice = full_match
        sep = 'd' if 'd' in base_dice else 'D'
        parts = base_dice.split(sep, 1)
        raw_dice = parts[0] if parts[0] else "1"
        raw_sides = parts[1]
        if any(x in raw_dice for x in 'dD'):
            dice_resolved, _ = parse_and_roll(raw_dice, sort)
        else:
            dice_resolved = raw_dice
        if any(x in raw_sides for x in 'dD'):
            sides_resolved, sides_breakdown = parse_and_roll(raw_sides, sort)
        else:
            sides_resolved = raw_sides
            sides_breakdown = raw_sides
        if keep_count_str and any(x in keep_count_str for x in 'dD'):
            keep_resolved, _ = parse_and_roll(keep_count_str, sort)
        else:
            keep_resolved = keep_count_str
        try:
            num_dice = int(safe_eval(dice_resolved))
            num_sides = int(safe_eval(sides_resolved))
            keep_count = int(safe_eval(keep_resolved)) if keep_resolved else num_dice
        except (ValueError, SyntaxError, TypeError, IndexError):
            raise ValueError(f"Invalid format in '{full_match}'")
        keep_count = max(1, min(keep_count, num_dice))
        _, rolls = roll_dice(num_dice, num_sides)
        # Sort rolls for keep logic if needed
        if keep_type == 'kh':
            kept_rolls = sorted(rolls, reverse=True)[:keep_count]
        elif keep_type == 'kl':
            kept_rolls = sorted(rolls)[:keep_count]
        else:
            kept_rolls = rolls
        total = sum(kept_rolls)
        is_complex_sides = (raw_sides != sides_resolved) or not str(raw_sides).isdigit()
        if sort:
            rolls.sort(reverse=True)
        # Format breakdown string to show dropped dice
        if keep_type:
            roll_strs = []
            temp_kept = kept_rolls.copy()
            for r in rolls:
                if r in temp_kept:
                    roll_strs.append(str(r))
                    temp_kept.remove(r)
                else:
                    roll_strs.append(f"{{{r}}}")
            rolls_str = " + ".join(roll_strs)
            prefix = f"{keep_match.group(1).lower()}{keep_count}"
        else:
            rolls_str = " + ".join(map(str, rolls))
            prefix = ""
        if is_complex_sides:
            breakdown_parts.append(f"{prefix}[{sides_breakdown} -> d{num_sides}: {rolls_str}]")
        elif num_dice == 1:
            breakdown_parts.append(str(total))
        else:
            breakdown_parts.append(f"{prefix}[{rolls_str} = {total}]")
        return str(total)

    sanitized_for_calc = re.sub(DICE_PATTERN_STR, roll_callback, dice_string, flags=re.IGNORECASE)
    operators = re.split(DICE_PATTERN_STR, dice_string, flags=re.IGNORECASE)
    result_parts = [part + o for part, o in zip(breakdown_parts, operators)]
    full_breakdown = "".join(result_parts) + operators[-1]
    return sanitized_for_calc, full_breakdown

def roll_expression(user_input: str, sort: bool = False) -> tuple[float, str]:
    sanitized_string, breakdown_string = parse_and_roll(user_input, sort=sort)
    if not VALID_MATH_PATTERN.fullmatch(sanitized_string):
        invalid_chars = "".join(sorted(set(re.sub(VALID_MATH_PATTERN, "", sanitized_string))))
        raise ValueError(f"Unsupported characters: {invalid_chars}")
    sanitized_string = re.sub(r'(?<=[\d)])\(', '*(', sanitized_string)
    return safe_eval(sanitized_string), breakdown_string

def evaluate_expression(user_input: str) -> float:
    total, _ = roll_expression(user_input)
    return total

def format_roll_message(prefix: str, total, breakdown_string: str) -> str:
    if breakdown_string != str(total):
        formatted_breakdown = re.sub(r'([+*/^]|-(?!>))', r' \1 ', breakdown_string)
        formatted_breakdown = re.sub(r'\s+', ' ', formatted_breakdown).strip()
        if len(formatted_breakdown) > 1800:
            return f"{prefix} = **{total}!**\n-# (calculations too long to display)"
        return f"{prefix}: `{formatted_breakdown}` = **{total}!**"
    return f"{prefix} = **{total}!**"

def scale_bar(value: float, max_value: float, width: int = 20, hit: bool = False) -> str:
    if max_value <= 0:
        return ""
    full, frac = divmod(int((value / max_value) * width * (len(BAR_BLOCKS) - 1)), len(BAR_BLOCKS) - 1)
    if hit:
        return HIT_FULL * full + (HIT_FRAC if frac else "")
    return "█" * full + (BAR_BLOCKS[frac] if frac else "")

def compile_target(target: str) -> tuple[Callable[[float], bool] | None, str | None]:
    if not target:
        return None, None
    t = target.strip().lower()
    negate = False
    if t.startswith('!'):
        negate = True
        t = t[1:].strip()
        if not t:
            raise ValueError("`!` needs something to negate, e.g. `!10-90` or `!odd`.")
    predicate, human = _compile_target_inner(t)
    if negate:
        inner = predicate
        predicate = lambda x: not inner(x)
        human = f"not {human}"
    return predicate, human

def _compile_target_inner(t: str) -> tuple[Callable[[float], bool], str]:
    if t in ("odd", "even"):
        return (lambda x: int(x) % 2 == (0 if t == "even" else 1)), t
    if m := re.fullmatch(r'(?:between\s+)?(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)', t):
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
        return (lambda x: lo <= x <= hi), f"{lo:g}-{hi:g}"
    if m := re.fullmatch(r'in\s*\{([^}]+)}', t):
        values = {float(v.strip()) for v in m.group(1).split(',') if v.strip()}
        return (lambda x: x in values), f"in {{{', '.join(f'{v:g}' for v in sorted(values))}}}"
    if m := re.fullmatch(r'(>=|<=|>|<|=|==)\s*(-?\d+(?:\.\d+)?)', t):
        op_str, num = m.group(1), float(m.group(2))
        ops = {'>=': op.ge, '<=': op.le, '>': op.gt,
               '<': op.lt, '=': op.eq, '==': op.eq}
        return (lambda x, n=num, oper=ops[op_str]: oper(x, n)), f"{op_str if op_str != '==' else '='}{num:g}"
    raise ValueError(f"Couldn't understand target `{t}`. Try e.g. `>=15`, `=20`, `10-90`, `!odd`.")