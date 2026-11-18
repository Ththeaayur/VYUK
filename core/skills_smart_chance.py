"""
Smart skills pack 3 for VYUK (v5.12) — the sibling of skills_smart
and skills_smart_encode: four more pure-Python tools that touch no OS
surface at all — a loan/EMI calculator, a compound-interest
projector, a dice roller and a random chooser.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise and never fake a
result — a refusal or an error line says exactly why. The money
tools are currency-neutral (no symbol is ever assumed) and their
maths is factored into pure helpers so it can be checked against
independently computed answers:

- loan_calculator runs a month-by-month amortisation simulation
  for BOTH the base schedule and the extra-payment schedule, and
  the simulation is the source of truth for every figure quoted —
  so the payment, the totals and the interest saved can never
  disagree with each other.
- compound_interest uses the standard future-value formulas
  (lump sum plus an ordinary annuity for the contributions), and
  its year-by-year breakdown is computed with the same function,
  so the rows always add up to the headline figure.

All four tools are READ-ONLY: they compute and return text,
changing nothing on the machine. Stdlib-only and identical on
every OS — no platform branches anywhere in this module.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import random
import re

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _money(value: float) -> str:
    """1234567.891 -> '1,234,567.89'. Currency-neutral on purpose:
    no symbol, because VYUK doesn't know which currency a loan is in."""
    return f"{value:,.2f}"


def _as_float(value, default=None):
    """A model-supplied number as a float, or *default* when it isn't
    one. Booleans are rejected even though bool subclasses int —
    True is not a principal."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return default
    return default


def _as_int(value, default=None):
    """A model-supplied whole number as an int, or *default*."""
    number = _as_float(value, None)
    if number is None or number != int(number):
        return default
    return int(number)


# ---------------------------------------------------------------------------
# LOAN CALCULATOR — EMI from the formula, truth from the simulation
# ---------------------------------------------------------------------------

_MAX_LOAN_MONTHS = 1200          # also the simulation iteration cap
_MAX_SIM_MONTHS = 1200
_MAX_RATE = 100.0


def _emi(principal: float, annual_rate_percent: float, months: int) -> float:
    """The standard amortisation payment. r = annual/1200; zero-rate
    loans simply divide the principal by the term."""
    r = annual_rate_percent / 1200.0
    if r == 0:
        return principal / months
    factor = (1 + r) ** months
    return principal * r * factor / (factor - 1)


def _simulate_loan(principal: float, monthly_rate: float, payment: float):
    """Month-by-month amortisation: each month interest accrues on
    the balance, then the payment lands (the final payment is the
    smaller remainder, never a full EMI). Returns
    (months_taken, total_paid, total_interest), or None when the
    payment never dents the principal or the schedule runs past
    the iteration cap. Pure — this is the loan tool's source of
    truth, used for the base schedule AND the extra-payment one."""
    balance = float(principal)
    total_paid = 0.0
    months = 0
    while balance > 1e-9 and months < _MAX_SIM_MONTHS:
        interest = balance * monthly_rate
        due = balance + interest
        pay = payment if payment < due else due
        if pay <= interest:
            return None  # payment doesn't cover interest — never amortises
        balance = due - pay
        total_paid += pay
        months += 1
    if balance > 1e-9:
        return None
    return months, total_paid, total_paid - float(principal)


def loan_calculator(principal, annual_rate_percent, months,
                    extra_monthly=0) -> str:
    """EMI, totals, and what an extra monthly payment buys."""
    amount = _as_float(principal)
    if amount is None or amount <= 0:
        return ("A loan calculation needs a positive principal — "
                "the amount borrowed, e.g. 250000.")
    rate = _as_float(annual_rate_percent)
    if rate is None or rate < 0 or rate > _MAX_RATE:
        return (f"The interest rate needs to be between 0 and "
                f"{_MAX_RATE:g}% a year — '{annual_rate_percent}' "
                "doesn't work.")
    term = _as_int(months)
    if term is None or term < 1 or term > _MAX_LOAN_MONTHS:
        return (f"The term needs to be a whole number of months "
                f"between 1 and {_MAX_LOAN_MONTHS} — "
                f"'{months}' doesn't work.")
    extra = _as_float(extra_monthly, 0.0)
    if extra is None or extra < 0:
        return ("The extra monthly payment can't be negative — "
                "use 0 for the plain schedule.")
    monthly_rate = rate / 1200.0
    payment = _emi(amount, rate, term)
    base = _simulate_loan(amount, monthly_rate, payment)
    if base is None:
        return ("That combination doesn't amortise in the "
                "simulation — the payment never brings the balance "
                "down. Try a shorter rate or a longer term.")
    _base_months, base_total, base_interest = base
    lines = [
        f"Loan of {_money(amount)} at {rate:g}% a year over "
        f"{term} months: {_money(payment)} per month. "
        f"Total paid {_money(base_total)}, of which "
        f"{_money(base_interest)} is interest."
    ]
    if extra > 0:
        boosted = _simulate_loan(amount, monthly_rate, payment + extra)
        if boosted is None:
            lines.append(
                f"(The with-extra schedule ran past the "
                f"{_MAX_SIM_MONTHS}-month simulation cap, so I won't "
                "quote payoff figures for it.)")
        else:
            new_months, _new_total, new_interest = boosted
            saved = base_interest - new_interest
            sooner = term - new_months
            lines.append(
                f"Paying an extra {_money(extra)} per month "
                f"({_money(payment + extra)} in total): the loan is "
                f"repaid in {new_months} months"
                + (f" — {sooner} month{'s' if sooner != 1 else ''} "
                   f"sooner" if sooner > 0 else "")
                + f", and the interest falls to "
                  f"{_money(new_interest)} — {_money(saved)} saved.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMPOUND INTEREST — future value of a lump sum plus contributions
# ---------------------------------------------------------------------------

_MAX_YEARS = 100
_MAX_COMPOUNDS = 365


def _future_value(principal: float, annual_rate_percent: float,
                  years: float, monthly_contribution: float,
                  compounds_per_year: int) -> float:
    """Future value at *years*: the lump sum compounded, plus the
    contributions treated as an ordinary annuity — the monthly
    contribution is spread evenly across the compounding periods
    and each period's contribution lands at the period's end.
    Pure; the yearly breakdown calls this same function, so the
    rows and the headline can never disagree."""
    i = annual_rate_percent / 100.0 / compounds_per_year
    periods = int(round(years * compounds_per_year))
    per_period = monthly_contribution * 12.0 / compounds_per_year
    if i == 0:
        return principal + per_period * periods
    growth = (1 + i) ** periods
    return principal * growth + per_period * ((growth - 1) / i)


def compound_interest(principal, annual_rate_percent, years,
                      monthly_contribution=0, compounds_per_year=12) -> str:
    """Project a lump sum plus monthly contributions forward."""
    amount = _as_float(principal)
    if amount is None or amount < 0:
        return ("Compound interest needs a starting amount of 0 or "
                "more — e.g. 10000 (0 works if there are monthly "
                "contributions).")
    rate = _as_float(annual_rate_percent)
    if rate is None or rate < 0 or rate > _MAX_RATE:
        return (f"The rate needs to be between 0 and {_MAX_RATE:g}% "
                f"a year — '{annual_rate_percent}' doesn't work.")
    span = _as_int(years)
    if span is None or span < 1 or span > _MAX_YEARS:
        return (f"The projection needs a whole number of years "
                f"between 1 and {_MAX_YEARS} — '{years}' doesn't "
                "work.")
    monthly = _as_float(monthly_contribution, 0.0)
    if monthly is None or monthly < 0:
        return ("The monthly contribution can't be negative — "
                "use 0 for none.")
    compounds = _as_int(compounds_per_year, 12)
    if compounds is None or compounds < 1 or compounds > _MAX_COMPOUNDS:
        return (f"Compounding can happen 1 to {_MAX_COMPOUNDS} "
                f"times a year (12 = monthly, 4 = quarterly, "
                f"1 = yearly) — '{compounds_per_year}' doesn't work.")
    if amount == 0 and monthly == 0:
        return ("There's nothing to grow: the starting amount is 0 "
                "and there are no monthly contributions.")
    final = _future_value(amount, rate, span, monthly, compounds)
    contributed = amount + monthly * 12 * span
    lines = [
        f"{_money(amount)} at {rate:g}% a year for {span} "
        f"year{'s' if span != 1 else ''}"
        + (f", adding {_money(monthly)} a month" if monthly > 0 else "")
        + f", grows to {_money(final)}. You put in "
          f"{_money(contributed)} altogether; growth added "
          f"{_money(final - contributed)}."
    ]
    # Year-by-year rows, capped so the answer stays speakable:
    # every year up to 10; beyond that the first five, an ellipsis,
    # and the last five.
    if span <= 10:
        row_years = list(range(1, span + 1))
    else:
        row_years = list(range(1, 6)) + [None] + list(range(span - 4, span + 1))
    lines.append("Year by year:")
    for year in row_years:
        if year is None:
            lines.append("…")
        else:
            value = _future_value(amount, rate, year, monthly, compounds)
            lines.append(f"Year {year}: {_money(value)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DICE — NdM with an optional +/-K modifier
# ---------------------------------------------------------------------------

_DICE_RE = re.compile(
    r"^(\d*)\s*d\s*(\d+)\s*(?:([+-])\s*(\d+))?$", re.IGNORECASE)
_MAX_DICE = 50
_MAX_SIDES = 1000
_DICE_EXAMPLES = ("Dice look like '2d6+3', 'd20' or '3d8-1' — "
                  "count, then 'd', then sides, then an optional "
                  "+/- modifier.")


def _parse_dice(notation):
    """'2d6+3' -> (2, 6, 3); 'd20' -> (1, 20, 0); None when the
    notation isn't dice-shaped at all. Range checks live in the
    handler so they can be reported individually. Pure."""
    if not isinstance(notation, str):
        return None
    match = _DICE_RE.match(notation.strip())
    if not match:
        return None
    count = int(match.group(1)) if match.group(1) else 1
    sides = int(match.group(2))
    modifier = 0
    if match.group(3):
        modifier = int(match.group(4))
        if match.group(3) == "-":
            modifier = -modifier
    return count, sides, modifier


def roll_dice(notation: str = "1d6") -> str:
    """Roll NdM dice (optionally +/- K) and show every roll."""
    parsed = _parse_dice(notation)
    if parsed is None:
        return f"I can't read '{notation}' as dice. {_DICE_EXAMPLES}"
    count, sides, modifier = parsed
    if count < 1 or count > _MAX_DICE:
        return (f"I roll between 1 and {_MAX_DICE} dice at a time — "
                f"'{notation}' asks for {count}.")
    if sides < 2 or sides > _MAX_SIDES:
        return (f"A die needs between 2 and {_MAX_SIDES} sides — "
                f"'{notation}' asks for {sides}.")
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier
    label = f"{count}d{sides}"
    if modifier:
        label += f"{modifier:+d}"
    parts = " + ".join(str(r) for r in rolls)
    if modifier:
        return (f"{label}: rolled {parts}, "
                f"{modifier:+d} → {total}")
    return f"{label}: rolled {parts} → {total}"


# ---------------------------------------------------------------------------
# RANDOM CHOICE — one pick from a list, for coin-flip moments
# ---------------------------------------------------------------------------

_MAX_OPTIONS = 100


def random_choice(options) -> str:
    """Pick one option uniformly at random. *options* arrives as one
    comma-separated string or as a list — both are accepted."""
    if isinstance(options, str):
        items = options.split(",")
    elif isinstance(options, (list, tuple)):
        items = [str(item) for item in options]
    else:
        items = []
    cleaned = [item.strip() for item in items if str(item).strip()]
    if len(cleaned) < 2:
        return ("Give me at least two real options to pick from, "
                "separated by commas — like 'pizza, sushi, tacos'.")
    total = len(cleaned)
    capped = cleaned[:_MAX_OPTIONS]
    pick = random.choice(capped)
    if total > _MAX_OPTIONS:
        return (f"{pick} — picked from the first {_MAX_OPTIONS} of "
                f"the {total} options you gave.")
    return f"{pick} — picked from {total} options."


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Smart-3 pack tool on a ToolRegistry-like object."""
    registry.register(
        name="loan_calculator",
        description="Work out a loan: the monthly payment (EMI) for a principal at an annual interest rate over a term in months, plus the total paid and the total interest. With extra_monthly > 0 it also shows the new payoff time and the interest saved. Every figure comes from a month-by-month simulation, so they always agree. Currency-neutral — amounts are plain numbers. Rate 0–100%, term 1–1200 months.",
        parameters={
            "type": "object",
            "properties": {
                "principal": {"type": "number", "description": "The amount borrowed, e.g. 250000"},
                "annual_rate_percent": {"type": "number", "description": "Annual interest rate in percent, e.g. 9.5"},
                "months": {"type": "integer", "description": "Term in months, e.g. 60"},
                "extra_monthly": {"type": "number", "description": "Optional extra paid each month on top of the EMI (default 0)"},
            },
            "required": ["principal", "annual_rate_percent", "months"],
        },
        handler=loan_calculator,
    )
    registry.register(
        name="compound_interest",
        description="Project savings forward: a starting amount at an annual rate for a number of years, optionally adding a fixed monthly contribution, compounding compounds_per_year times a year (12 monthly = default, 4 quarterly, 1 yearly). Reports the final value, the total put in, the growth earned, and a year-by-year breakdown (condensed for long spans). Currency-neutral.",
        parameters={
            "type": "object",
            "properties": {
                "principal": {"type": "number", "description": "Starting amount, e.g. 10000 (may be 0 if contributing monthly)"},
                "annual_rate_percent": {"type": "number", "description": "Annual interest/growth rate in percent, e.g. 8"},
                "years": {"type": "integer", "description": "Years to project (1–100)"},
                "monthly_contribution": {"type": "number", "description": "Optional amount added every month (default 0)"},
                "compounds_per_year": {"type": "integer", "description": "Compounding frequency per year: 12 monthly (default), 4 quarterly, 1 yearly"},
            },
            "required": ["principal", "annual_rate_percent", "years"],
        },
        handler=compound_interest,
    )
    registry.register(
        name="roll_dice",
        description="Roll dice in tabletop notation: NdM with an optional +K or -K modifier — '2d6+3', 'd20', '3d8-1' (default '1d6'). Shows every individual roll and the total. Up to 50 dice, up to 1000 sides. For games and random numbers, not for anything security-related.",
        parameters={
            "type": "object",
            "properties": {
                "notation": {"type": "string", "description": "Dice notation, e.g. '2d6+3' (default '1d6')"},
            },
            "required": [],
        },
        handler=roll_dice,
    )
    registry.register(
        name="random_choice",
        description="Pick one option at random from a comma-separated list ('pizza, sushi, tacos') — for coin-flip moments, not real decisions. Needs at least two options. For a die roll or a random number, roll_dice.",
        parameters={
            "type": "object",
            "properties": {
                "options": {"type": "string", "description": "The options, separated by commas, e.g. 'pizza, sushi, tacos'"},
            },
            "required": ["options"],
        },
        handler=random_choice,
    )
