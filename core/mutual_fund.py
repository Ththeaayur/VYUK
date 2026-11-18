"""Indian mutual-fund maths (R8-8 mftool port, 2026-11-10) — the
shippable half of NayakwadiS/mftool, as own stdlib code.

WHAT MFTOOL IS (verified by this batch, not taken from the scout):
a tiny pure-Python library for Indian mutual-fund data — live NAV by
AMFI scheme code, all-scheme quotes, full historical NAVs, daily
scheme performance — all read from AMFI's public data (PyPI mftool
3.4, MIT; requires_dist requests + bs4 + httpx + pandas + yfinance
+ matplotlib + deprecated). Its README lists NO standalone SIP
calculator: its returns are daily scheme performance computed FROM
the NAVs. The NAV data is the capability; the SIP/returns maths on
top of it is arithmetic.

WHY THERE IS NO FETCH IN THIS MODULE — THE AMFI DATA-TERMS GATE
(read by this batch from https://www.amfiindia.com/terms-of-use,
2026-11-10; the scout had flagged the terms as unverified for
commercial display, and they do NOT permit it):

  Permitted Uses: "AMFI grants you a non-exclusive, personal,
  non-transferable, non-sublicensable, limited and revocable right
  to access, use and display this Site ... for your personal and
  non-commercial use only (the 'Permitted Uses'). ... No other use
  of the Site is authorised unless you and we have agreed otherwise
  in writing."

  Prohibited uses: "You may not publicly perform, publicly display,
  transmit, publish, participate in the sale or transfer of, modify,
  or create derivative works based on anything available through the
  Site, in whole or in part. You shall not store electronically any
  significant portion of any part of the Site."

VYUK is a commercial product, so showing AMFI NAVs inside it is
commercial display — not personal non-commercial use — and is
expressly prohibited, as is storing any significant portion of the
site (which a fetch-and-cache would do) and publishing NAV-derived
figures (which a fetch-and-compute would do: derivative works).
There is no written agreement with AMFI. So this module contains
NO fetch function, NO network call and NO stored NAV data, by
design — a fetch that may neither be stored, shown nor derived
from has no lawful use in this product. This is the stock_ticker
precedent (Yahoo terms, personal use only): the capability is
gated to an honest refusal, not half-built.

THE PATH BACK (not a build step): a written agreement with AMFI
permitting commercial display, or a commercially-licensed NAV
source configured in its place. When either exists, this is the
file that learns to call it.

FORMAT NOTE (verified by this batch with a real fetch of
https://www.amfiindia.com/spages/NAVAll.txt, HTTP 200,
1,529,314 bytes, dated 09-Nov-2026): semicolon-delimited text,
header row "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div
Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date", bare
header lines for fund houses and categories, "-" for a missing
ISIN. Recorded here so a future licensed build does not have to
re-discover it — it is provenance, not a fetch.

WHAT SHIPS INSTEAD (the maths, on numbers the caller supplies):
- fund_returns: CAGR from a start value, an end value and a span
  in years; XIRR (annualised) from a dated cashflow list. Pure
  arithmetic on the caller's own figures — no AMFI data, no
  network, no lookup. The SIP projection half of the finding
  already exists as compound_growth (monthly additions at a
  steady rate, core/skills_money_growth.py), so it is not rebuilt
  here.
- mutual_fund_nav: the gated display. It answers every NAV
  question with the honest refusal above and makes NO network
  call, so a NAV is never hallucinated and never shown on
  forbidden terms.

Both tools are read-only pure transforms (the R8-3 summarise_text
precedent). Nothing here is investment advice: every result says
so, and the maths is labelled an illustration of the numbers
given, never a prediction.
"""

from __future__ import annotations

import datetime as _datetime
import math as _math

from .skills_money_helpers import _coerce_float, _money

__all__ = ["fund_returns", "mutual_fund_nav"]

_MAX_CASHFLOWS = 1000  # more dated flows than this is a ledger, not a question
_XIRR_MAX_ITER = 100
_XIRR_TOL = 1e-7

_NAV_REFUSAL = (
    "I can't show mutual-fund NAVs: AMFI's own terms of use allow "
    "its site's data for personal, non-commercial use only, and "
    "expressly forbid publicly displaying, transmitting or "
    "publishing it — and VYUK is a commercial product, so showing "
    "you a NAV here would break those terms. I won't guess a NAV "
    "either, so I won't give you a wrong one. NAVs come back if a "
    "written agreement with AMFI, or a properly licensed NAV "
    "source, is set up. What I can do right now is the maths on "
    "figures you give me — a fund's return from its start and end "
    "values, or an annualised return from your dated investments — "
    "ask for fund returns with your numbers."
)


def mutual_fund_nav(scheme: str = "") -> str:
    """The gated NAV display (R8-8): an honest refusal, no network
    call, no lookup — AMFI's terms forbid commercial display."""
    asked = str(scheme or "").strip()
    if asked:
        return (
            f"For {asked}: " + _NAV_REFUSAL
        )
    return _NAV_REFUSAL


def _cagr(start: float, end: float, years: float, cur: str) -> str:
    rate = (end / start) ** (1.0 / years) - 1.0
    pct = rate * 100.0
    return (
        f"From {_money(start, cur)} to {_money(end, cur)} over "
        f"{years:g} year{'s' if years != 1 else ''} works out to "
        f"{pct:.2f}% a year, compounded — the steady yearly rate "
        f"that turns the start into the end. That's the maths on "
        f"the numbers you gave, an illustration not a prediction "
        f"and not financial advice: real returns vary year to year."
    )


def _parse_cashflows(cashflows):
    """A dated cashflow list, or None + the plain-words reason."""
    if not isinstance(cashflows, (list, tuple)):
        return None, "The cashflows need to be a list of dated amounts."
    if len(cashflows) < 2:
        return None, "An annualised return needs at least two dated amounts — money in and money out."
    if len(cashflows) > _MAX_CASHFLOWS:
        return None, (
            f"That's {len(cashflows)} dated amounts — more than the "
            f"{_MAX_CASHFLOWS} this works with. Give the main ones "
            f"(each investment and the final value)."
        )
    parsed = []
    for i, flow in enumerate(cashflows):
        if not isinstance(flow, dict):
            return None, f"Cashflow {i + 1} isn't a dated amount — each one needs a date and an amount."
        raw_date = flow.get("date")
        raw_amount = flow.get("amount")
        if raw_date is None or raw_amount is None:
            return None, (
                f"Cashflow {i + 1} is missing its date or its amount — "
                "each one needs both, e.g. date 2023-01-05 and amount -10000."
            )
        try:
            day = _datetime.date.fromisoformat(str(raw_date).strip())
        except (ValueError, TypeError):
            return None, (
                f"Cashflow {i + 1} has a date I can't read: {raw_date!r} — "
                "use YYYY-MM-DD, e.g. 2023-01-05."
            )
        amount = _coerce_float(raw_amount)
        if amount is None:
            return None, f"Cashflow {i + 1} has an amount I can't read: {raw_amount!r}."
        parsed.append((day, amount))
    parsed.sort(key=lambda item: item[0])
    if parsed[-1][0] == parsed[0][0]:
        return None, "All the amounts fall on one day, so there is no span to annualise over."
    signs = {1 if amount > 0 else -1 if amount < 0 else 0 for _, amount in parsed}
    if not ({1, -1} <= signs):
        return None, (
            "An annualised return needs money going both ways — "
            "amounts in (negative) and a value out (positive). "
            "All yours point one way, so no rate fits them."
        )
    return parsed, ""


def _xirr_npv(flows, rate: float) -> float:
    base = flows[0][0]
    total = 0.0
    for day, amount in flows:
        years = (day - base).days / 365.0
        total += amount / (1.0 + rate) ** years
    return total


def _xirr_derivative(flows, rate: float) -> float:
    base = flows[0][0]
    total = 0.0
    for day, amount in flows:
        years = (day - base).days / 365.0
        if years == 0:
            continue
        total += -years * amount / (1.0 + rate) ** (years + 1.0)
    return total


def _solve_xirr(flows):
    """Newton-Raphson with a bisection fallback; None when no rate
    in (-100%, +1000%] zeroes the net present value."""
    rate = 0.10
    for _ in range(_XIRR_MAX_ITER):
        value = _xirr_npv(flows, rate)
        if abs(value) < _XIRR_TOL:
            return rate
        slope = _xirr_derivative(flows, rate)
        if slope == 0 or not _math.isfinite(slope):
            break
        step = value / slope
        nxt = rate - step
        if not _math.isfinite(nxt) or nxt <= -0.999999 or nxt > 10.0:
            break
        if abs(nxt - rate) < _XIRR_TOL:
            return nxt
        rate = nxt
    # Bisection fallback: NPV is monotone in the rate for a
    # one-sign-change flow, so a sign change brackets the root.
    lo, hi = -0.999999, 10.0
    flo, fhi = _xirr_npv(flows, lo), _xirr_npv(flows, hi)
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if (flo > 0) == (fhi > 0):
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        fmid = _xirr_npv(flows, mid)
        if abs(fmid) < _XIRR_TOL:
            return mid
        if (fmid > 0) == (flo > 0):
            lo, flo = mid, fmid
        else:
            hi = mid
        if hi - lo < 1e-9:
            return (lo + hi) / 2.0
    return (lo + hi) / 2.0


def _xirr(flows, cur: str) -> str:
    rate = _solve_xirr(flows)
    if rate is None:
        return (
            "No annualised rate fits those amounts — the money in "
            "and the money out never balance at any rate. Check the "
            "dates and the signs (money in as a negative amount, "
            "the final value as a positive one)."
        )
    span_days = (flows[-1][0] - flows[0][0]).days
    years = span_days / 365.0
    invested = -sum(a for _, a in flows if a < 0)
    returned = sum(a for _, a in flows if a > 0)
    return (
        f"Those amounts work out to {rate * 100.0:.2f}% a year "
        f"annualised (XIRR), over {years:.2f} years from "
        f"{flows[0][0].isoformat()} to {flows[-1][0].isoformat()}: "
        f"{_money(invested, cur)} in, {_money(returned, cur)} out. "
        f"That's the maths on the numbers and dates you gave, an "
        f"illustration not a prediction and not financial advice: "
        f"real returns vary year to year."
    )


def fund_returns(start_value=None, end_value=None, years=None,
                 cashflows=None, currency: str = "") -> str:
    """CAGR from a start value, an end value and a span in years;
    or XIRR (annualised) from a dated cashflow list. Pure maths on
    the numbers given — it looks up no fund and no NAV."""
    cur = str(currency or "")
    if cashflows:
        flows, problem = _parse_cashflows(cashflows)
        if flows is None:
            return problem
        return _xirr(flows, cur)
    start = _coerce_float(start_value)
    end = _coerce_float(end_value)
    span = _coerce_float(years)
    if start is None or end is None or span is None:
        return (
            "To work out a return I need either a start value, an "
            "end value and the years between them — or a list of "
            "dated amounts, money in as negative and the final "
            "value as positive. Give one of those two."
        )
    if start <= 0:
        return "The start value needs to be more than zero."
    if end < 0:
        return "The end value can't be negative."
    if span <= 0:
        return "The years between them need to be more than zero."
    return _cagr(start, end, span, cur)
