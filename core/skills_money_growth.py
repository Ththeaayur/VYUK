"""Part of the skills_money split (Phase A batch A14): see
skills_money.py (facade) for the pack docstring and registration.
Move only — bodies verbatim from the original module.
"""


# INTENTIONAL: cross-part names — growth functions resolve these bare
# names in their defining module's globals, so they are imported here,
# not relied on from the facade (bare-name rule, plan §2 P3).
from .skills_money_helpers import (
    _coerce_float,
    _money,
    _months_words,
    _per_unit,
)


# ---------------------------------------------------------------------------
# SAVINGS GOAL — months to a target, contributions + growth
# ---------------------------------------------------------------------------

_MAX_GOAL_MONTHS = 1200  # a century of monthly steps, then it says so


def savings_goal(target=0, monthly=0, annual_rate_pct=0,
                 already_saved=0, currency: str = "") -> str:
    """How many months until savings reach a target, putting a
    fixed amount away each month with monthly growth."""
    goal = _coerce_float(target)
    if goal is None or goal <= 0:
        return ("A savings goal needs a target more than zero — "
                "the amount you're aiming for.")
    per_month = _coerce_float(monthly)
    if per_month is None or per_month < 0:
        return ("The monthly amount can't be negative — tell me "
                "what goes in each month (zero is allowed if "
                "there's already money saved).")
    rate = _coerce_float(annual_rate_pct)
    if rate is None or rate < 0:
        return ("The yearly growth rate can't be negative for "
                "this one — give 0 if you just want the saving-up "
                "maths.")
    saved = _coerce_float(already_saved)
    if saved is None or saved < 0:
        return "What's already saved can't be a negative amount."
    cur = str(currency or "")
    if saved >= goal:
        return (f"You're already there: {_money(saved, cur)} "
                f"saved against a goal of {_money(goal, cur)}.")
    monthly_rate = rate / 100.0 / 12.0
    if per_month <= 0 and saved * monthly_rate <= 0:
        return (f"That goal is never reached on these numbers: "
                f"{_money(saved, cur)} saved, nothing added each "
                "month and no growth — the balance stays exactly "
                "where it is. Add a monthly amount or a growth "
                "rate and it becomes reachable.")
    balance = saved
    months = 0
    while balance < goal and months < _MAX_GOAL_MONTHS:
        balance = balance * (1 + monthly_rate) + per_month
        months += 1
    if balance < goal:
        return (f"That would take over 100 years at this pace — "
                f"after {_MAX_GOAL_MONTHS} months the balance is "
                f"only {_money(balance, cur)} of the "
                f"{_money(goal, cur)} goal. A bigger monthly "
                "amount is what it needs.")
    put_in = saved + per_month * months
    result = (f"You'd reach {_money(goal, cur)} in {months} "
              f"month{'s' if months != 1 else ''} — that's "
              f"{_months_words(months)}. You'd put in "
              f"{_money(put_in, cur)} of your own money")
    growth = balance - put_in
    if monthly_rate > 0 and growth > 0:
        result += f" and growth adds {_money(growth, cur)}"
    result += f", ending at {_money(balance, cur)}."
    if monthly_rate > 0:
        result += (" That's the maths at a steady "
                   f"{rate:g}% a year — an illustration, not a "
                   "prediction or financial advice.")
    return result


# ---------------------------------------------------------------------------
# COMPOUND GROWTH — a pot plus monthly additions, compounded monthly
# ---------------------------------------------------------------------------


def compound_growth(principal=0, annual_rate_pct=0, years=0,
                    monthly_add=0, currency: str = "") -> str:
    """What a starting amount grows to over some years at a
    yearly rate, compounded monthly, with optional monthly
    additions — contributions and growth shown separately."""
    start = _coerce_float(principal)
    if start is None or start < 0:
        return ("The starting amount can't be negative — give "
                "what's in the pot now (zero is fine if you're "
                "building it from monthly additions).")
    rate = _coerce_float(annual_rate_pct)
    if rate is None or rate < 0:
        return ("The yearly rate can't be negative for this "
                "one — give 0 to see the no-growth picture.")
    span = _coerce_float(years)
    if span is None or span <= 0:
        return "The number of years needs to be more than zero."
    add = _coerce_float(monthly_add)
    if add is None or add < 0:
        return "The monthly addition can't be negative."
    cur = str(currency or "")
    months = int(round(span * 12))
    if months < 1:
        return ("That's less than a month of growth — give a "
                "span of at least a twelfth of a year.")
    if start <= 0 and add <= 0:
        return ("Nothing goes in — no starting amount and no "
                "monthly addition — so the result is 0 however "
                "long you wait.")
    monthly_rate = rate / 100.0 / 12.0
    balance = start
    for _ in range(months):
        balance = balance * (1 + monthly_rate) + add
    contributed = start + add * months
    growth = balance - contributed
    result = (f"After {_months_words(months)} "
              f"({months} months) at {rate:g}% a year, "
              f"{_money(start, cur)}")
    if add > 0:
        result += (f" plus {_money(add, cur)} a month")
    result += f" becomes {_money(balance, cur)}. "
    result += (f"You'd have put in {_money(contributed, cur)} "
               f"of your own money; growth adds "
               f"{_money(growth, cur)}.")
    result += (" This is an illustration of the maths, not a "
               "prediction — it assumes the same rate every "
               "month, which real returns never do, and ignores "
               "inflation and tax. Not financial advice.")
    return result


# ---------------------------------------------------------------------------
# LOAN PAYMENT — the standard monthly-payment (EMI) formula
# ---------------------------------------------------------------------------


def loan_payment(principal=0, annual_rate_pct=0, months=0,
                 currency: str = "") -> str:
    """The fixed monthly payment on a loan, the total paid over
    the term, and how much of that total is interest."""
    amount = _coerce_float(principal)
    if amount is None or amount <= 0:
        return ("A loan needs an amount more than zero — how "
                "much is being borrowed.")
    rate = _coerce_float(annual_rate_pct)
    if rate is None or rate < 0:
        return ("The yearly interest rate can't be negative — "
                "give 0 for an interest-free loan.")
    term_raw = _coerce_float(months)
    if term_raw is None or term_raw < 1 or term_raw != int(term_raw):
        return ("The term needs to be a whole number of months, "
                "at least 1 — e.g. 12, 36 or 60.")
    term = int(term_raw)
    cur = str(currency or "")
    monthly_rate = rate / 100.0 / 12.0
    if monthly_rate == 0:
        payment = amount / term
    else:
        factor = (1 + monthly_rate) ** term
        payment = amount * monthly_rate * factor / (factor - 1)
    payment = round(payment, 2)
    total = round(payment * term, 2)
    interest = round(total - amount, 2)
    result = (f"Monthly payment: {_money(payment, cur)}. Over "
              f"{term} months you'd pay {_money(total, cur)} in "
              f"total, of which {_money(interest, cur)} is "
              "interest.")
    if monthly_rate == 0:
        result += (" At 0% there's no interest at all — it's "
                   "just the loan split evenly.")
    return result


# ---------------------------------------------------------------------------
# TIP AND TAX — a bill, itemised
# ---------------------------------------------------------------------------


def tip_and_tax(bill=0, tip_pct=15, tax_pct=0, people=1,
                currency: str = "") -> str:
    """Itemise a bill: the tax on it, a tip worked out on the
    bill before tax, the total, and a per-person share when
    it's split."""
    amount = _coerce_float(bill)
    if amount is None or amount <= 0:
        return "A bill needs a total more than zero."
    tip_rate = _coerce_float(tip_pct)
    if tip_rate is None or tip_rate < 0 or tip_rate > 100:
        return "The tip percentage needs to be between 0 and 100."
    tax_rate = _coerce_float(tax_pct)
    if tax_rate is None or tax_rate < 0 or tax_rate > 100:
        return "The tax percentage needs to be between 0 and 100."
    count_raw = _coerce_float(people)
    if count_raw is None or count_raw < 1 or count_raw != int(count_raw):
        return ("The number of people splitting needs to be a "
                "whole number, at least 1.")
    count = int(count_raw)
    cur = str(currency or "")
    tax = round(amount * tax_rate / 100.0, 2)
    tip = round(amount * tip_rate / 100.0, 2)
    total = round(amount + tax + tip, 2)
    lines = [f"Bill: {_money(amount, cur)}."]
    if tax_rate > 0:
        lines.append(f"Tax ({tax_rate:g}%): {_money(tax, cur)}.")
    if tip_rate > 0:
        lines.append(f"Tip ({tip_rate:g}% of the bill before "
                     f"tax): {_money(tip, cur)}.")
    lines.append(f"Total: {_money(total, cur)}.")
    if count > 1:
        each = round(total / count, 2)
        lines.append(f"Split {count} ways: "
                     f"{_money(each, cur)} each.")
        remainder = round(total - each * count, 2)
        if remainder > 0:
            lines.append(f"(Rounding leaves "
                         f"{_money(remainder, cur)} unaccounted "
                         "— one person pays that much less, or it "
                         "stays as a little extra tip.)")
        elif remainder < 0:
            lines.append(f"(Rounding over-collects "
                         f"{_money(abs(remainder), cur)} — one "
                         "person pays that much less to keep it "
                         "exact.)")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# HOURLY RATE CONVERT — the same pay, three ways
# ---------------------------------------------------------------------------

_BASIS_ALIASES = {
    "hourly": "hourly", "hour": "hourly",
    "monthly": "monthly", "month": "monthly",
    "yearly": "yearly", "year": "yearly",
    "annual": "yearly", "annually": "yearly",
}


def hourly_rate_convert(amount=0, basis: str = "",
                        hours_per_week=40, weeks_per_year=52,
                        currency: str = "") -> str:
    """Convert a pay amount between per-hour, per-month and
    per-year, given the working week and year."""
    pay = _coerce_float(amount)
    if pay is None or pay <= 0:
        return "The pay amount needs to be more than zero."
    which = _BASIS_ALIASES.get(str(basis or "").strip().lower())
    if which is None:
        return ("The basis must be 'hourly', 'monthly' or "
                "'yearly' — which of those the amount you gave "
                "is.")
    hpw = _coerce_float(hours_per_week)
    if hpw is None or hpw <= 0 or hpw > 168:
        return ("Hours per week needs to be between a fraction "
                "above 0 and 168 (every hour there is).")
    weeks = _coerce_float(weeks_per_year)
    if weeks is None or weeks <= 0 or weeks > 52:
        return "Weeks per year needs to be more than 0 and at most 52."
    cur = str(currency or "")
    hours_year = hpw * weeks
    if which == "hourly":
        yearly = pay * hours_year
    elif which == "monthly":
        yearly = pay * 12
    else:
        yearly = pay
    monthly = yearly / 12.0
    hourly = yearly / hours_year
    return (f"{_money(pay, cur)} {which} works out to "
            f"{_money(hourly, cur)} an hour, "
            f"{_money(monthly, cur)} a month and "
            f"{_money(yearly, cur)} a year — at {hpw:g} hours "
            f"a week, {weeks:g} weeks a year. (A month here is "
            "the year split into 12 equal parts.)")


# ---------------------------------------------------------------------------
# PRICE PER UNIT — which of two offers is cheaper, fairly compared
# ---------------------------------------------------------------------------


def price_per_unit(price_a=0, size_a=0, price_b=0, size_b=0,
                   unit: str = "", currency: str = "") -> str:
    """Compare two offers by price per unit of size, so a big
    pack and a small pack can be judged fairly."""
    pa = _coerce_float(price_a)
    pb = _coerce_float(price_b)
    if pa is None or pb is None or pa < 0 or pb < 0:
        return ("Prices can't be negative — give what each "
                "option costs (zero is fine; free is a price).")
    sa = _coerce_float(size_a)
    sb = _coerce_float(size_b)
    if sa is None or sb is None or sa <= 0 or sb <= 0:
        return ("Both sizes need to be more than zero — a "
                "per-unit price needs something to divide by.")
    cur = str(currency or "")
    unit_word = str(unit or "").strip()
    per = f"per {unit_word}" if unit_word else "per unit"
    unit_a = pa / sa
    unit_b = pb / sb
    offers = (f"(A: {_money(pa, cur)} for {sa:g}"
              f"{' ' + unit_word if unit_word else ''}; "
              f"B: {_money(pb, cur)} for {sb:g}"
              f"{' ' + unit_word if unit_word else ''}.)")
    if abs(unit_a - unit_b) <= 1e-9 * max(1.0, unit_a, unit_b):
        return (f"They work out exactly the same: both are "
                f"{_per_unit(unit_a, cur)} {per}. {offers}")
    if unit_a < unit_b:
        cheaper, dearer, label = unit_a, unit_b, "A"
    else:
        cheaper, dearer, label = unit_b, unit_a, "B"
    pct = (dearer - cheaper) / dearer * 100.0
    return (f"Option {label} is cheaper {per}: "
            f"{_per_unit(cheaper, cur)} against "
            f"{_per_unit(dearer, cur)} — that's {pct:.1f}% "
            f"less {per}. {offers}")


# ---------------------------------------------------------------------------
# DISCOUNT STACK — percentages applied one after another, then tax
# ---------------------------------------------------------------------------


def discount_stack(price=0, discounts="", tax_pct=0,
                   currency: str = "") -> str:
    """Apply a list of percentage discounts one after another
    (each to what's left), then optional tax — every step
    shown, and the true overall % off stated."""
    start = _coerce_float(price)
    if start is None or start <= 0:
        return "The starting price needs to be more than zero."
    tax_rate = _coerce_float(tax_pct)
    if tax_rate is None or tax_rate < 0 or tax_rate > 100:
        return "The tax percentage needs to be between 0 and 100."
    if isinstance(discounts, (list, tuple)):
        raw_items = list(discounts)
    elif isinstance(discounts, str):
        raw_items = discounts.split(",")
    elif discounts in (None, ""):
        raw_items = []
    else:
        raw_items = [discounts]
    rates = []
    for item in raw_items:
        if isinstance(item, str):
            item = item.strip().rstrip("%").strip()
            if not item:
                continue
        value = _coerce_float(item)
        if value is None or value < 0 or value > 100:
            return ("Every discount must be a percentage "
                    "between 0 and 100 — give them like "
                    "'10, 20' for 10% then 20%.")
        rates.append(value)
    if not rates:
        return ("Give me at least one discount percentage — "
                "like '10, 20' for 10% off, then 20% off what's "
                "left.")
    cur = str(currency or "")
    steps = [f"Start: {_money(start, cur)}."]
    current = start
    for rate in rates:
        current = current * (1 - rate / 100.0)
        steps.append(f"{rate:g}% off takes it to "
                     f"{_money(current, cur)}.")
    discounted = current
    if tax_rate > 0:
        tax = discounted * tax_rate / 100.0
        current = discounted + tax
        steps.append(f"Then {tax_rate:g}% tax adds "
                     f"{_money(tax, cur)}, making "
                     f"{_money(current, cur)}.")
    effective = (start - discounted) / start * 100.0
    tail = (f"All told: {_money(current, cur)}. The discounts "
            f"together take {effective:.1f}% off the original "
            "price")
    if len(rates) > 1:
        tail += (" — each percentage applies to what's left "
                 "after the one before, so they don't simply "
                 "add up")
    steps.append(tail + ".")
    return " ".join(steps)


# ---------------------------------------------------------------------------
# RENT SPLIT — a total divided by named shares, whole units
# ---------------------------------------------------------------------------


def rent_split(total=0, shares: str = "",
               currency: str = "") -> str:
    """Split a total between people by share weights (room
    sizes, fractions, any proportions), rounded to whole
    units, the parts adding up exactly."""
    amount = _coerce_float(total)
    if amount is None or amount <= 0:
        return "The total to split needs to be more than zero."
    if isinstance(shares, (list, tuple)):
        raw_items = [str(s) for s in shares]
    else:
        raw_items = str(shares or "").split(",")
    parsed = []
    seen = set()
    for item in raw_items:
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            return ("Shares need to look like 'name:weight' — "
                    "e.g. rent_split with shares "
                    "'Alex:3, Sam:2, Jo:1', the weights being "
                    "room sizes or any proportions.")
        name, _, weight_text = item.rpartition(":")
        name = " ".join(name.split())
        weight = _coerce_float(weight_text.strip())
        if not name or weight is None or weight <= 0:
            return (f"Couldn't read the share '{item}' — each "
                    "share is a name, a colon, and a weight "
                    "more than zero, like 'Alex:3'.")
        if name.lower() in seen:
            return (f"'{name}' appears twice — give each "
                    "person one share (add their weights "
                    "together if it's one person with two "
                    "parts).")
        seen.add(name.lower())
        parsed.append((name, weight))
    if not parsed:
        return ("Give me at least one share — like "
                "'Alex:3, Sam:2' — so I know who splits it "
                "and in what proportions.")
    cur = str(currency or "")
    weight_total = sum(w for _n, w in parsed)
    raws = [amount * w / weight_total for _n, w in parsed]
    rounded = [int(round(r)) for r in raws]
    target = int(round(amount))
    drift = target - sum(rounded)
    drift_note = ""
    if drift != 0:
        largest = max(range(len(raws)), key=lambda i: raws[i])
        rounded[largest] += drift
        drift_note = (
            f" (Rounding to whole units left a drift of "
            f"{drift:+d}, {'added to' if drift > 0 else 'taken from'} "
            f"{parsed[largest][0]}'s share (the largest) so "
            "the parts add up exactly.)")
    lines = [f"Splitting {_money(amount, cur)} by shares, in "
             "whole units:"]
    for (name, weight), share in zip(parsed, rounded):
        share_text = f"{share:,}"
        if cur:
            share_text = f"{cur}{share_text}"
        lines.append(f"• {name} (share {weight:g} of "
                     f"{weight_total:g}): {share_text}")
    result = "\n".join(lines)
    if abs(target - amount) > 1e-9:
        result += (f" (The total was rounded to "
                   f"{target:,} whole units.)")
    result += drift_note
    return result
