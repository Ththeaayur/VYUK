"""
Money skills pack for VYUK — everyday money maths: how
long a savings goal takes, compound growth, a loan's monthly
payment, tip-and-tax itemised, pay converted between hour,
month and year, budget envelopes, per-unit price comparisons,
stacked discounts, rent split by shares, and a subscription
audit.

Where each tool's truth lives — and what it honestly can't do:

- The eight calculators are pure arithmetic on the numbers
  they are handed. They know no bank rates, no prices and no
  tax law; a rate or a percentage is whatever the user says it
  is. Projections (savings_goal, compound_growth) are labelled
  as illustrations of the maths, never predictions and never
  financial advice — real returns vary month to month.
- budget_envelope and subscription_audit persist as memory
  facts (core/memory/memory.py) under the fixed categories
  `envelopes` and `subscriptions`, so they survive restarts
  and show up in the user's memory like anything else
  VYUK remembers.
- Amounts are plain numbers; no currency is ever assumed. A
  tool that prints money takes an optional `currency` label
  and prefixes it verbatim when one is given — the maths is
  identical either way.

Same rules as the packs before it: plain functions returning
human-readable strings, never raising; persistence sits behind
the `_store()` seam so tests can substitute a temp store;
stdlib only. Wiring: the parent (core/funnel/tools_registry.py) calls
register_all(registry) — this module never touches the
registry singleton itself.
"""

# INTENTIONAL: the old module's namespace carried its stdlib import;
# re-exported so the facade's dir() matches the old module's exactly
# (dir-diff-empty bar, plan §2 P2; files3/files2 lesson).
import json

# INTENTIONAL: re-exports of every moved name, so importers of the
# facade see the same namespace the old module had, with identity
# (each facade name IS its impl module's object).
from .skills_money_helpers import (
    _coerce_float,
    _money,
    _plain_num,
    _per_unit,
    _months_words,
    _store,
    _facts,
)
# R8-8 mftool port (2026-11-10): fund returns on caller-supplied
# figures + the gated NAV display — own stdlib code, no AMFI fetch
# (AMFI's terms forbid commercial display; see core/mutual_fund.py).
from .mutual_fund import fund_returns, mutual_fund_nav
from .skills_money_growth import (
    _BASIS_ALIASES,
    _MAX_GOAL_MONTHS,
    compound_growth,
    discount_stack,
    hourly_rate_convert,
    loan_payment,
    price_per_unit,
    rent_split,
    savings_goal,
    tip_and_tax,
)
from .skills_money_budget import (
    _ENVELOPES_CATEGORY,
    _SUBSCRIPTIONS_CATEGORY,
    _envelope_line,
    _find_envelope,
    _find_subscription,
    _load_envelopes,
    _load_subscriptions,
    budget_envelope,
    subscription_audit,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register every Money pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="savings_goal",
        description="Work out how long a savings goal takes: give the target, what goes in each month, optionally a yearly growth rate and what's already saved. Answers in months (spelled out as years and months too), with how much you'd put in yourself versus what growth adds, and the final balance. It's a steady-rate illustration of the maths, not a prediction and not financial advice. Use when the user asks how long saving for something will take.",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "number", "description": "The amount being aimed for"},
                "monthly": {"type": "number", "description": "What goes in each month (0 allowed if there's a starting amount and a growth rate)"},
                "annual_rate_pct": {"type": "number", "description": "Yearly growth rate in percent, compounded monthly (default 0)"},
                "already_saved": {"type": "number", "description": "What's already in the pot (default 0)"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD' — the maths is the same either way"},
            },
            "required": ["target", "monthly"],
        },
        handler=savings_goal,
    )
    registry.register(
        name="compound_growth",
        description="See what a pot of money grows to over some years at a yearly rate, compounded monthly, with optional monthly additions. Shows the final amount split into what you put in versus what growth added. It's an illustration at a steady rate, not a prediction and not financial advice — real returns vary and inflation isn't included. Use when the user asks what savings or an investment could grow to.",
        parameters={
            "type": "object",
            "properties": {
                "principal": {"type": "number", "description": "The starting amount (0 allowed if there are monthly additions)"},
                "annual_rate_pct": {"type": "number", "description": "Yearly growth rate in percent, compounded monthly"},
                "years": {"type": "number", "description": "How many years (fractions allowed, e.g. 2.5)"},
                "monthly_add": {"type": "number", "description": "Optional extra put in each month (default 0)"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["principal", "annual_rate_pct", "years"],
        },
        handler=compound_growth,
    )
    registry.register(
        name="fund_returns",
        description="Work out a fund or investment's return from the numbers you give — never from a lookup. Give a start value, an end value and the years between them for the steady yearly rate (CAGR), or a list of dated amounts — money in as negative, the final value as positive — for the annualised return (XIRR). Pure maths on your figures: it cannot look up any fund's NAV, and it never guesses one. An illustration, not a prediction and not financial advice. Use when the user asks what return an investment made, or what their SIP instalments annualised to.",
        parameters={
            "type": "object",
            "properties": {
                "start_value": {"type": "number", "description": "The value at the start, for the CAGR mode"},
                "end_value": {"type": "number", "description": "The value at the end, for the CAGR mode"},
                "years": {"type": "number", "description": "The years between start and end, for the CAGR mode"},
                "cashflows": {"type": "array", "items": {"type": "object", "properties": {"date": {"type": "string", "description": "The date in YYYY-MM-DD"}, "amount": {"type": "number", "description": "Money in as a negative amount, money out as a positive amount"}}, "required": ["date", "amount"]}, "description": "Dated amounts for the XIRR mode — each investment and the final value, with dates"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": [],
        },
        handler=fund_returns,
    )
    registry.register(
        name="mutual_fund_nav",
        description="Ask for a mutual fund's NAV by scheme name or AMFI scheme code. VYUK cannot show NAVs: AMFI's terms allow its data for personal, non-commercial use only and forbid publicly displaying, transmitting or publishing it, and VYUK is a commercial product — so this answers with that honest reason and makes no network call, and never guesses a NAV. Use when the user asks for a fund's NAV or today's price of a mutual fund.",
        parameters={
            "type": "object",
            "properties": {
                "scheme": {"type": "string", "description": "The scheme name or AMFI scheme code asked about"},
            },
            "required": [],
        },
        handler=mutual_fund_nav,
    )
    registry.register(
        name="loan_payment",
        description="Work out the fixed monthly payment on a loan from the amount, the yearly interest rate and the term in months, plus the total paid over the term and how much of it is interest. At 0% it simply splits the loan evenly. Pure maths on the numbers given — it doesn't look up any real loan or rate. Use when the user asks what a loan would cost per month.",
        parameters={
            "type": "object",
            "properties": {
                "principal": {"type": "number", "description": "The amount borrowed"},
                "annual_rate_pct": {"type": "number", "description": "Yearly interest rate in percent (0 for interest-free)"},
                "months": {"type": "integer", "description": "The term in whole months, e.g. 12, 36, 60"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["principal", "annual_rate_pct", "months"],
        },
        handler=loan_payment,
    )
    registry.register(
        name="tip_and_tax",
        description="Itemise a bill: the tax amount, a tip worked out on the bill before tax, and the total — and when it's split, what each person pays, with any rounding leftover honestly flagged. Tip defaults to 15%, tax to 0. Use at the end of a meal or any bill with a tip and tax involved. For a plain split with a tip and no tax, split_bill does the same job.",
        parameters={
            "type": "object",
            "properties": {
                "bill": {"type": "number", "description": "The bill total before tax and tip"},
                "tip_pct": {"type": "number", "description": "Tip percentage of the pre-tax bill (default 15)"},
                "tax_pct": {"type": "number", "description": "Tax percentage of the bill (default 0)"},
                "people": {"type": "integer", "description": "How many people split the total (default 1 — no split shown)"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["bill"],
        },
        handler=tip_and_tax,
    )
    registry.register(
        name="hourly_rate_convert",
        description="Convert the same pay between per-hour, per-month and per-year: give an amount and say which basis it's on, plus the hours worked per week and weeks per year (defaults 40 and 52). A 'month' in the answer is the year split into 12 equal parts. Use when the user compares a salary with an hourly rate or a monthly figure with a yearly one.",
        parameters={
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "The pay amount, on the stated basis"},
                "basis": {"type": "string", "enum": ["hourly", "monthly", "yearly"], "description": "Which basis the amount is on"},
                "hours_per_week": {"type": "number", "description": "Hours worked per week (default 40)"},
                "weeks_per_year": {"type": "number", "description": "Weeks worked per year (default 52)"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["amount", "basis"],
        },
        handler=hourly_rate_convert,
    )
    registry.register(
        name="budget_envelope",
        description="Keep budget envelopes — a named monthly budget for a category like Groceries or Eating out. Actions: 'set' creates an envelope or resets it (spent goes back to zero), 'spend' records spending against one and reports what's left or how far over it is, 'report' lists every envelope with budget, spent, remaining and % used, plus totals. Envelopes persist as memory facts. Amounts are plain numbers; no currency is assumed. Use when the user sets a spending budget or asks how a budget is doing.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'set', 'spend' or 'report'"},
                "name": {"type": "string", "description": "The envelope's name, for 'set' and 'spend'"},
                "amount": {"type": "number", "description": "The budget for 'set', or the spending to record for 'spend'"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["action"],
        },
        handler=budget_envelope,
    )
    registry.register(
        name="price_per_unit",
        description="Compare two offers fairly by price per unit of size — a big pack against a small one. Give each option's price and size (same unit for both, e.g. grams or ml); the answer names the cheaper one, both per-unit prices, and by what percentage it wins. If they work out the same, it says so. Use when the user is deciding between pack sizes or brands.",
        parameters={
            "type": "object",
            "properties": {
                "price_a": {"type": "number", "description": "What option A costs"},
                "size_a": {"type": "number", "description": "How much of it you get (in the shared unit)"},
                "price_b": {"type": "number", "description": "What option B costs"},
                "size_b": {"type": "number", "description": "How much of it you get (in the shared unit)"},
                "unit": {"type": "string", "description": "The shared size unit, e.g. 'g', 'ml', 'kg' (optional)"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["price_a", "size_a", "price_b", "size_b"],
        },
        handler=price_per_unit,
    )
    registry.register(
        name="discount_stack",
        description="Apply percentage discounts one after another to a price, then optional tax on the discounted price. Discounts are given as a comma list like '10, 20' and applied in that order, each to what's left — so the true saving (28% for 10 then 20, not 30%) and every step's price are shown, ending with the final total. Use for sale prices with stacked offers or coupons.",
        parameters={
            "type": "object",
            "properties": {
                "price": {"type": "number", "description": "The starting price before discounts"},
                "discounts": {"type": "string", "description": "Comma-separated discount percentages in the order to apply them, e.g. '10, 20'"},
                "tax_pct": {"type": "number", "description": "Tax percentage added after the discounts (default 0)"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["price", "discounts"],
        },
        handler=discount_stack,
    )
    registry.register(
        name="rent_split",
        description="Split a total — rent, a deposit, any shared cost — between people in proportion to share weights such as room sizes or fractions. Shares are given as 'name:weight' pairs like 'Alex:3, Sam:2'. Amounts are rounded to whole units and adjusted on the largest share so the parts add up exactly, and that adjustment is disclosed. Use whenever a cost is shared unevenly on purpose.",
        parameters={
            "type": "object",
            "properties": {
                "total": {"type": "number", "description": "The total amount to split"},
                "shares": {"type": "string", "description": "Comma-separated 'name:weight' pairs, e.g. 'Alex:3, Sam:2, Jo:1' — weights are proportions, like room sizes"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["total", "shares"],
        },
        handler=rent_split,
    )
    registry.register(
        name="subscription_audit",
        description="Track subscriptions and what they quietly add up to. Actions: 'add' records a subscription at its monthly cost (adding the same name again updates it), 'remove' drops one, 'list' shows each with its yearly cost, the monthly and yearly totals, and names the biggest. Subscriptions persist as memory facts. Use when the user adds a subscription, cancels one, or asks what their subscriptions cost.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'remove' or 'list'"},
                "name": {"type": "string", "description": "The subscription's name, for 'add' and 'remove'"},
                "monthly": {"type": "number", "description": "Its monthly cost, for 'add'"},
                "currency": {"type": "string", "description": "Optional label printed before amounts, like '₹' or 'USD'"},
            },
            "required": ["action"],
        },
        handler=subscription_audit,
    )
