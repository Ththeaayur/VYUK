from pathlib import Path
import calendar
import datetime
import json
import math
import re
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_productivity2_helpers import (
    _fmt_duration,
    _parse_iso_date,
)


# ---------------------------------------------------------------------------
# TIMEZONE CONVERT — zoneinfo, both sides narrated
# ---------------------------------------------------------------------------


def _load_zone(name: str):
    """(ZoneInfo, None) or (None, error string)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name), None
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None, (f"I don't know a time zone called '{name}'. "
                      "Use an IANA name like 'Asia/Kolkata', "
                      "'America/New_York', 'Europe/London' or 'UTC'.")
    except Exception as e:  # noqa: BLE001 — a broken tz database, said plainly
        return None, f"Couldn't load the time zone '{name}': {e}"


def _offset_text(moment: datetime.datetime) -> str:
    offset = moment.utcoffset() or datetime.timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, mins = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{mins:02d}"


def timezone_convert(time_text: str, from_zone: str, to_zone: str) -> str:
    """Convert a time between IANA time zones."""
    source_zone, err = _load_zone(str(from_zone or "").strip())
    if err:
        return err
    dest_zone, err = _load_zone(str(to_zone or "").strip())
    if err:
        return err
    raw = str(time_text or "").strip()
    if not raw:
        return ("That needs a time — 'HH:MM' (on today's date) or a "
                "full ISO datetime like 2026-10-21T18:00.")
    moment = None
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", raw):
        try:
            parsed = datetime.time.fromisoformat(raw)
        except ValueError:
            return f"'{raw}' isn't a valid time of day."
        today = datetime.datetime.now(source_zone).date()
        moment = datetime.datetime.combine(today, parsed,
                                           tzinfo=source_zone)
    else:
        try:
            parsed_dt = datetime.datetime.fromisoformat(
                raw.replace("Z", "+00:00"))
        except ValueError:
            return (f"'{raw}' isn't a time I can read — use 'HH:MM' "
                    "or an ISO datetime like 2026-10-21T18:00.")
        moment = (parsed_dt if parsed_dt.tzinfo is not None
                  else parsed_dt.replace(tzinfo=source_zone))
    converted = moment.astimezone(dest_zone)
    diff = (converted.utcoffset() or datetime.timedelta(0)) \
        - (moment.utcoffset() or datetime.timedelta(0))
    diff_minutes = int(diff.total_seconds() // 60)
    if diff_minutes == 0:
        relation = (f"at the same UTC offset as {from_zone}, so the "
                    "clock time doesn't change")
    elif diff_minutes > 0:
        relation = f"{_fmt_duration(diff_minutes * 60)} ahead of {from_zone}"
    else:
        relation = f"{_fmt_duration(-diff_minutes * 60)} behind {from_zone}"
    return (f"{moment.strftime('%H:%M')} on {moment.date().isoformat()} "
            f"in {from_zone} ({_offset_text(moment)}) is "
            f"{converted.strftime('%H:%M')} on "
            f"{converted.date().isoformat()} in {to_zone} "
            f"({_offset_text(converted)}) — {to_zone} is {relation}.")


# ---------------------------------------------------------------------------
# DATE CALCULATOR — days between, add days, weekday
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# DATE CALCULATOR — days between, date + days, weekday. v5.30: this
# tool absorbed the retired date_math — its action vocabulary ('diff',
# 'add'), its relative-date words and its calendar views live here.
# ---------------------------------------------------------------------------

_DATE_WORDS = ("today", "tomorrow", "yesterday")
_OP_ALIASES = {
    "days_between": "days_between", "diff": "days_between",
    "add_days": "add_days", "add": "add_days",
    "add_business_days": "add_business_days",
    "business_days": "add_business_days",
    "age": "age", "how_old": "age",
    "weekday": "weekday",
}


def _parse_date_flexible(text: str):
    """(date, None) or (None, error) — ISO dates/datetimes (via
    _parse_iso_date) plus the words today / tomorrow / yesterday."""
    word = str(text or "").strip().lower()
    if word in _DATE_WORDS:
        today = datetime.date.today()
        if word == "today":
            return today, None
        if word == "tomorrow":
            return today + datetime.timedelta(days=1), None
        return today - datetime.timedelta(days=1), None
    return _parse_iso_date(text)


def _add_months(d, months: int):
    """d + whole calendar months, day clamped to the target month."""
    index = d.month - 1 + months
    year = d.year + index // 12
    month = index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def _humanise_span(start, end) -> str:
    """Calendar view of start..end (start <= end): '1 month 6 days',
    '1 year 2 months 3 days' — real calendar months, no dateutil."""
    months = 0
    while _add_months(start, months + 1) <= end:
        months += 1
    days = (end - _add_months(start, months)).days
    years, rem_months = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} year" + ("s" if years != 1 else ""))
    if rem_months:
        parts.append(f"{rem_months} month"
                     + ("s" if rem_months != 1 else ""))
    if days:
        parts.append(f"{days} day" + ("s" if days != 1 else ""))
    return " ".join(parts) if parts else "0 days"


def _weekdays_between(start, end) -> int:
    """Mon–Fri days in [start, end). Only called for spans ≥ a week."""
    days = (end - start).days
    weeks, rem = divmod(days, 7)
    count = weeks * 5
    cursor = start + datetime.timedelta(days=weeks * 7)
    for _ in range(rem):
        if cursor.weekday() < 5:
            count += 1
        cursor += datetime.timedelta(days=1)
    return count


def _diff_text(a, b) -> str:
    """The days_between answer: signed total, plus week / weekday /
    calendar-month views for longer spans (ported from date_math)."""
    total = (b - a).days
    head = (f"{b.isoformat()} − {a.isoformat()} = {total} "
            f"day{'s' if abs(total) != 1 else ''}")
    if total == 0:
        return f"{head} — the same date ({a.strftime('%A')})."
    earlier, later = (a, b) if total > 0 else (b, a)
    span = abs(total)
    extras = []
    if span >= 30:
        extras.append(_humanise_span(earlier, later))
    if span >= 7:
        weeks, rem = divmod(span, 7)
        week_part = f"{weeks} week" + ("s" if weeks != 1 else "")
        if rem:
            week_part += f" and {rem} day" + ("s" if rem != 1 else "")
        extras.append(week_part)
        weekdays = _weekdays_between(earlier, later)
        extras.append(f"{weekdays} weekday"
                      + ("s" if weekdays != 1 else ""))
    out = head + " (" + "; ".join(extras) + ")" if extras else head
    if total < 0:
        # The pre-merge calculator stated a backwards span plainly;
        # keep that: a bare "-366 days" is easy to misread aloud.
        out += (f" — the second date falls {span} "
                f"day{'s' if span != 1 else ''} BEFORE the first.")
    return out


def _add_business_days(start, count: int):
    """start + count Mon–Fri days (v5.31). Negative counts walk
    backwards. Any 7 consecutive days hold exactly 5 business
    days, so whole weeks jump in one step and only the remainder
    (under a week) is walked day by day."""
    step = 1 if count >= 0 else -1
    weeks, remaining = divmod(abs(count), 5)
    cursor = start + datetime.timedelta(days=step * weeks * 7)
    while remaining:
        cursor += datetime.timedelta(days=step)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


def _age_text(birth, today) -> str:
    """Exact age at `today` (birth <= today): whole calendar
    months from the birth date (via _add_months, day clamped),
    then leftover days — '34 years, 2 months, 5 days old
    (born on Friday, 15 March 1991)'."""
    months_total = ((today.year - birth.year) * 12
                    + (today.month - birth.month))
    anchor = _add_months(birth, months_total)
    if anchor > today:
        months_total -= 1
        anchor = _add_months(birth, months_total)
    days = (today - anchor).days
    years, months = divmod(months_total, 12)

    def _plural(n, word):
        return f"{n} {word}" + ("s" if n != 1 else "")

    return (f"{_plural(years, 'year')}, {_plural(months, 'month')}, "
            f"{_plural(days, 'day')} old "
            f"(born on {birth.strftime('%A, %d %B %Y')})")


def date_calculator(operation: str = "", date_a: str = "",
                    date_b: str = "", days: int = 0,
                    action: str = "") -> str:
    """Date arithmetic: days_between / add_days / add_business_days
    / age / weekday.

    `action` is accepted as an alias of `operation`, and the retired
    date_math tool's words ('diff', 'add') map onto the same
    operations; dates may be ISO or today/tomorrow/yesterday.
    """
    op = _OP_ALIASES.get(str(operation or action or "").strip().lower())
    if op is None:
        return ("Unknown operation — use 'days_between' (the days "
                "from date_a to date_b), 'add_days' (date_a plus "
                "days), 'add_business_days' (date_a plus Mon–Fri "
                "days only), 'age' (the exact age at date_a, a "
                "birth date) or 'weekday' (the day of the week of "
                "date_a). ('diff' and 'add' work too.)")
    first, err = _parse_date_flexible(date_a)
    if err:
        return err
    if op == "weekday":
        return (f"{first.isoformat()} is a "
                f"{first.strftime('%A')}.")
    if op == "add_days":
        try:
            count = int(days)
        except (TypeError, ValueError):
            return f"days must be a whole number (got {days!r})."
        try:
            result = first + datetime.timedelta(days=count)
        except OverflowError:
            return (f"{count} days from {first.isoformat()} lands "
                    "outside the calendar range I can represent.")
        direction = "after" if count >= 0 else "before"
        return (f"{first.isoformat()} plus {count} day(s) is "
                f"{result.isoformat()} — a {result.strftime('%A')} "
                f"({abs(count)} day(s) {direction} the start date).")
    if op == "add_business_days":
        try:
            count = int(days)
        except (TypeError, ValueError):
            return f"days must be a whole number (got {days!r})."
        try:
            result = _add_business_days(first, count)
        except OverflowError:
            return (f"{count} business days from "
                    f"{first.isoformat()} lands outside the calendar "
                    "range I can represent.")
        direction = "after" if count >= 0 else "before"
        return (f"{first.isoformat()} plus {count} business day(s) "
                f"is {result.isoformat()} — a "
                f"{result.strftime('%A')} ({abs(count)} business "
                f"day(s) {direction} the start date, weekends "
                "skipped).")
    if op == "age":
        today = datetime.date.today()
        if first > today:
            return (f"{first.isoformat()} hasn't happened yet — "
                    f"it's {(first - today).days} day(s) in the "
                    "future, so there is no age to give for it yet.")
        return f"{first.isoformat()}: {_age_text(first, today)}."
    # days_between
    second, err = _parse_date_flexible(date_b)
    if err:
        return err
    return _diff_text(first, second)


# ---------------------------------------------------------------------------
# PASSWORD STRENGTH — analysis only; the password is never kept
# or echoed (see the module docstring).
# ---------------------------------------------------------------------------

_COMMON_PASSWORDS = {
    "password", "password1", "password123", "123456", "12345678",
    "123456789", "12345", "qwerty", "abc123", "football", "monkey",
    "letmein", "dragon", "111111", "baseball", "iloveyou", "trustno1",
    "sunshine", "master", "welcome", "shadow", "ashley", "michael",
    "ninja", "mustang", "password1234", "admin", "login", "princess",
    "solo", "starwars", "freedom", "whatever", "charlie", "aa123456",
    "donald", "india", "india123", "cricket", "ganesha", "computer",
}
_KEYBOARD_WALKS = ("qwerty", "wertyu", "asdfgh", "zxcvbn", "qazwsx",
                   "1234567890", "0987654321", "poiuyt", "lkjhgf",
                   "mnbvcx")
_GUESSES_OFFLINE = 10_000_000_000    # 10^10 /s — a fast offline attack
_GUESSES_ONLINE = 10_000             # 10^4 /s — a throttled online attack


def _humanize_seconds(seconds: float) -> str:
    """Crack-time words across the full magnitude range."""
    if seconds < 1:
        return "under a second"
    if seconds < 60:
        return f"about {seconds:.0f} seconds"
    if seconds < 3600:
        return f"about {seconds / 60:.1f} minutes"
    if seconds < 86400:
        return f"about {seconds / 3600:.1f} hours"
    if seconds < 31557600:
        return f"about {seconds / 86400:.1f} days"
    years = seconds / 31557600
    if years < 1000:
        return f"about {years:.1f} years"
    if years < 1_000_000:
        return f"about {years:,.0f} years"
    for name, scale in (("million", 1e6), ("billion", 1e9),
                        ("trillion", 1e12), ("quadrillion", 1e15)):
        if years < scale * 1000:
            return f"about {years / scale:.1f} {name} years"
    return "longer than the age of the universe, many times over"


def _password_penalties(pw: str) -> list[tuple[str, float]]:
    """(reason, bits lost) for each pattern weakness found."""
    penalties: list[tuple[str, float]] = []
    lowered = pw.lower()
    if lowered in _COMMON_PASSWORDS:
        penalties.append(("it is on the common-password list — "
                          "attackers try these first", 40.0))
    runs = 0
    sequence = 1
    for i in range(1, len(lowered)):
        step = ord(lowered[i]) - ord(lowered[i - 1])
        if step in (1, -1) and (i == 1 or
                                ord(lowered[i - 1]) - ord(lowered[i - 2]) == step):
            sequence += 1
        else:
            if sequence >= 3:
                runs += 1
            sequence = 1
    if sequence >= 3:
        runs += 1
    if runs:
        penalties.append((f"{runs} sequential run(s) like 'abc' or "
                          "'321' — sequences are guessed early", 6.0 * runs))
    repeats = len(re.findall(r"(.)\1{2,}", pw))
    if repeats:
        penalties.append((f"{repeats} run(s) of the same character "
                          "repeated 3+ times", 5.0 * repeats))
    chunks = re.findall(r"(.{2,4}?)\1", pw)
    if chunks:
        penalties.append(("a repeated chunk (the same 2–4 characters "
                          "twice in a row)", 6.0))
    if any(walk in lowered or walk[::-1] in lowered
           for walk in _KEYBOARD_WALKS if len(walk) >= 4):
        penalties.append(("a keyboard walk (a straight line across "
                          "the keys)", 8.0))
    if re.fullmatch(r"\d+", pw):
        penalties.append(("digits only — the smallest alphabet "
                          "there is", 6.0))
    return penalties


def password_strength(password: str) -> str:
    """Score a password's strength. Analyses only — never stored,
    never logged, never echoed back in this text."""
    pw = password if isinstance(password, str) else ""
    if not pw:
        return ("That needs a password to analyse — and a note: I "
                "only score it here, I never store or log it.")
    pool = 0
    classes = []
    if re.search(r"[a-z]", pw):
        pool += 26
        classes.append("lowercase")
    if re.search(r"[A-Z]", pw):
        pool += 26
        classes.append("uppercase")
    if re.search(r"\d", pw):
        pool += 10
        classes.append("digits")
    if re.search(r"[^a-zA-Z0-9\s]", pw):
        pool += 33
        classes.append("symbols")
    if re.search(r"\s", pw):
        pool += 1
        classes.append("spaces")
    if pool == 0:
        pool = 26
    entropy = len(pw) * math.log2(pool)
    penalties = _password_penalties(pw)
    lost = sum(bits for _reason, bits in penalties)
    effective = max(0.0, entropy - lost)
    guesses = 2 ** max(effective - 1, 0)  # average case: half the space
    offline = _humanize_seconds(guesses / _GUESSES_OFFLINE)
    online = _humanize_seconds(guesses / _GUESSES_ONLINE)
    if effective < 28:
        verdict = "very weak"
    elif effective < 36:
        verdict = "weak"
    elif effective < 60:
        verdict = "fair"
    elif effective < 80:
        verdict = "strong"
    else:
        verdict = "excellent"
    lines = [
        f"Password strength: {verdict} — about {effective:.0f} bits "
        f"of entropy ({len(pw)} character(s) over "
        f"{' + '.join(classes) if classes else 'an unknown alphabet'}; "
        f"raw estimate {entropy:.0f} bits before pattern penalties).",
        f"Time to crack on average: {offline} against a fast "
        f"offline attack (10 billion guesses a second, e.g. a "
        f"stolen hash database), {online} against a throttled "
        "online attack (10 thousand a second).",
    ]
    for reason, bits in penalties:
        lines.append(f"Penalty (−{bits:.0f} bits): {reason}.")
    if verdict in ("very weak", "weak"):
        lines.append("Advice: length beats cleverness — a longer "
                     "phrase of unrelated words, with a digit and a "
                     "symbol, would raise this a lot.")
    lines.append("This analysis happened here and was forgotten — "
                 "the password wasn't stored, logged or sent anywhere.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON QUERY — a tiny dotted-path reader (read-only)
# ---------------------------------------------------------------------------

_JSON_RENDER_CAP = 1000  # characters of a value shown before disclosure


def json_query(source: str, query: str = "") -> str:
    """Read a JSON value by dotted path from a file or raw JSON text."""
    raw_source = str(source or "")
    if not raw_source.strip():
        return ("That needs either the path of a JSON file or the "
                "JSON text itself.")
    path = Path(raw_source).expanduser()
    if path.exists() and path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            return f"Couldn't read '{path}': {e}"
        origin = f"file '{path.name}'"
    else:
        text = raw_source
        origin = "the JSON text given"
    try:
        document = json.loads(text)
    except ValueError as e:
        return (f"That isn't valid JSON I can read ({origin}): {e}. "
                "Nothing was queried.")
    query = str(query or "").strip()

    def render(value) -> str:
        rendered = json.dumps(value, ensure_ascii=False)
        if len(rendered) > _JSON_RENDER_CAP:
            return (rendered[:_JSON_RENDER_CAP]
                    + f"… (truncated — the full value is "
                      f"{len(rendered)} characters)")
        return rendered

    if not query:
        return f"Found — the whole document from {origin}: {render(document)}"
    segments = query.split(".")
    pseudo = None
    if segments and segments[-1] in ("keys", "length"):
        pseudo = segments[-1]
        segments = segments[:-1]
    current = document
    walked: list[str] = []
    for segment in segments:
        if isinstance(current, dict):
            if segment not in current:
                available = ", ".join(sorted(map(str, current.keys()))[:12])
                return (f"There's no key '{segment}'"
                        + (f" at '{'.'.join(walked)}'" if walked else
                           " at the top level")
                        + f" in {origin}. Keys there: {available}"
                        + (" …and more." if len(current) > 12 else "."))
            current = current[segment]
        elif isinstance(current, list):
            if not segment.lstrip("-").isdigit():
                return (f"'{segment}' isn't an array index — at "
                        f"'{'.'.join(walked)}' there's a list of "
                        f"{len(current)} item(s); index it with a "
                        "number, or end the query with 'length'.")
            index = int(segment)
            if not -len(current) <= index < len(current):
                return (f"Index {index} is out of range at "
                        f"'{'.'.join(walked)}' — the list there has "
                        f"{len(current)} item(s).")
            current = current[index]
        else:
            return (f"Can't walk into '{segment}' — at "
                    f"'{'.'.join(walked)}' the value is "
                    f"{render(current)}, which has nothing inside it.")
        walked.append(segment)
    where = f"at '{'.'.join(walked)}'" if walked else "at the top level"
    if pseudo == "keys":
        if not isinstance(current, dict):
            return (f"The value {where} in {origin} isn't an object, "
                    "so it has no keys — it's " + render(current) + ".")
        keys = sorted(map(str, current.keys()))
        return (f"Found — keys {where} in {origin} ({len(keys)}): "
                + ", ".join(keys) + ".")
    if pseudo == "length":
        if not isinstance(current, (list, dict, str)):
            return (f"The value {where} in {origin} is "
                    f"{render(current)} — it has no length to report.")
        return (f"Found — length {where} in {origin}: {len(current)} "
                f"({'item(s)' if isinstance(current, list) else 'key(s)' if isinstance(current, dict) else 'character(s)'}).")
    return f"Found — value {where} in {origin}: {render(current)}"
