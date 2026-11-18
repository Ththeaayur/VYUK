"""Social track (v5.73) — SO-2.5 occasion date model.

The correctness finding, implemented natively (no date library is
allowed to decide policy silently):

- Leap-day birthdays have no default-correct rule: dateutil
  relativedelta clamps Feb 29 -> Feb 28, while an RFC 5545 yearly
  RRULE anchored on Feb 29 SKIPS non-leap years entirely. Two
  permissive libraries, two opposite silent policies. VYUK asks
  once and records the policy; it never assumes one.
- Year-unknown birthdays are inexpressible in datetime/RRULE.
  vCard (RFC 6350) allows truncated BDAY:--0415 (and --0229).
  The Apple/DAVx5 convention year 1604 + X-APPLE-OMIT-YEAR is a
  landmine: treat 1604 as real and age maths explodes. This
  module treats 1604 as year-UNKNOWN, always.

Model: month, day, year|None, leap_policy (feb28 | mar1 |
leap-only), omit_year. Occurrences are generated EXPLICITLY per
year; no bare yearly RRULE is ever persisted for birthdays or
anniversaries. Age is NEVER computed from an unknown or
placeholder year (age_on returns None).

Stdlib only. No store, no I/O, no network.
"""

LEAP_POLICIES = ("feb28", "mar1", "leap-only")
PLACEHOLDER_YEAR = 1604

_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def make_occasion_date(month, day, year=None, leap_policy="feb28",
                       omit_year=False):
    """Validate and build an occasion date dict. Raises ValueError
    on any invalid part. year=None forces omit_year=True. Year
    1604 is the placeholder convention and is stored as unknown,
    never as a real year."""
    if leap_policy not in LEAP_POLICIES:
        raise ValueError(
            f"leap_policy must be one of {LEAP_POLICIES}, "
            f"got {leap_policy!r}")
    if not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError(f"month must be 1..12, got {month!r}")
    if not isinstance(day, int) or not 1 <= day <= _DAYS_IN_MONTH[month - 1]:
        raise ValueError(
            f"day {day!r} is not valid for month {month}")
    if year is not None and (not isinstance(year, int) or year < 1):
        raise ValueError(f"year must be a positive int or None, "
                         f"got {year!r}")
    if year == PLACEHOLDER_YEAR:
        # Apple/DAVx5 omit-year convention: never a real year.
        year = None
        omit_year = True
    if year is None:
        omit_year = True
    return {"month": month, "day": day, "year": year,
            "leap_policy": leap_policy, "omit_year": bool(omit_year)}


def observed_in_year(occ, year):
    """The observed (month, day) for `occ` in `year`, or None when
    the leap-only policy skips a non-leap year. Feb 29 only."""
    month, day = occ["month"], occ["day"]
    if (month, day) != (2, 29):
        return (month, day)
    if is_leap_year(year):
        return (2, 29)
    policy = occ["leap_policy"]
    if policy == "feb28":
        return (2, 28)
    if policy == "mar1":
        return (3, 1)
    return None  # leap-only: the birthday does not occur


def occurrences(occ, start_year, end_year):
    """Explicit per-year occurrences in [start_year, end_year] as
    (year, month, day) tuples, in order. No RRULE is involved."""
    if end_year < start_year:
        raise ValueError("end_year must be >= start_year")
    out = []
    for year in range(start_year, end_year + 1):
        observed = observed_in_year(occ, year)
        if observed is not None:
            out.append((year, observed[0], observed[1]))
    return out


def next_occurrence(occ, from_date):
    """Next occurrence on or after from_date ("YYYY-MM-DD").
    Returns a (year, month, day) tuple. Searches at most 8 years
    (leap-only needs at most 4)."""
    try:
        y, m, d = (int(p) for p in from_date.split("-"))
    except (ValueError, AttributeError):
        raise ValueError(
            f"from_date must be YYYY-MM-DD, got {from_date!r}")
    for year in range(y, y + 9):
        observed = observed_in_year(occ, year)
        if observed is None:
            continue
        if (year, observed[0], observed[1]) >= (y, m, d):
            return (year, observed[0], observed[1])
    raise ValueError("no occurrence found in the next 8 years")


def age_on(occ, on_date):
    """Completed years of age on on_date ("YYYY-MM-DD"), or None
    when the birth year is unknown or omitted. NEVER guesses an
    age from a yearless date or a placeholder year."""
    if occ.get("year") is None or occ.get("omit_year"):
        return None
    try:
        y, m, d = (int(p) for p in on_date.split("-"))
    except (ValueError, AttributeError):
        raise ValueError(f"on_date must be YYYY-MM-DD, got {on_date!r}")
    birth_year = occ["year"]
    if y < birth_year:
        return None
    observed = observed_in_year(occ, y)
    occurred = observed is not None and (y, observed[0], observed[1]) <= (y, m, d)
    return (y - birth_year) - (0 if occurred else 1)


def parse_vcard_bday(value):
    """Parse a vCard BDAY value into an occasion date dict.
    Accepts: --MMDD (year-unknown), --MM-DD, YYYYMMDD,
    YYYY-MM-DD, and the 1604 placeholder (treated as unknown).
    Raises ValueError on anything else. Leap policy defaults to
    feb28 and must be confirmed by the user for Feb 29 dates —
    callers MUST ask once and then set it explicitly; this parser
    never silently chooses for a Feb 29 birthday beyond the
    documented default, and flags it via leap_policy_unconfirmed.
    """
    text = str(value or "").strip()
    if text.startswith("--"):
        body = text[2:].replace("-", "")
        if len(body) != 4 or not body.isdigit():
            raise ValueError(f"not a truncated BDAY: {value!r}")
        month, day = int(body[:2]), int(body[2:])
        occ = make_occasion_date(month, day, year=None)
        occ["leap_policy_unconfirmed"] = (month, day) == (2, 29)
        return occ
    body = text.replace("-", "")
    if len(body) != 8 or not body.isdigit():
        raise ValueError(f"not a BDAY value: {value!r}")
    year, month, day = int(body[:4]), int(body[4:6]), int(body[6:])
    occ = make_occasion_date(month, day, year=year)
    occ["leap_policy_unconfirmed"] = (month, day) == (2, 29)
    return occ
