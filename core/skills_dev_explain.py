# Explain-diff group: regex, cron, semver, diff.
import difflib
import re
# INTENTIONAL: caps shared with the convert group live in helpers;
# this module imports the four it uses (bare-name rule, section 2 P3).
from .skills_dev_helpers import (
    _DIFF_LINE_CAP,
    _REGEX_MATCH_CAP,
    _REGEX_SHOWN,
    _REGEX_TEXT_CAP,
)
# ---------------------------------------------------------------------------
# REGEX TEST — does it match, where, and what did the groups catch
# ---------------------------------------------------------------------------

_FLAG_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def regex_test(pattern: str = "", text: str = "",
               flags: str = "") -> str:
    """Test a regular expression against text and report the matches."""
    try:
        pat = str(pattern or "")
        if not pat:
            return ("Give me a pattern to test — like "
                    "regex_test(pattern='(\\d+)-(\\d+)', "
                    "text='room 12-34').")
        haystack = str(text or "")
        flag_bits = 0
        for letter in str(flags or "").lower():
            if letter not in _FLAG_MAP:
                return (f"I don't know a regex flag '{letter}' — "
                        "the letters I understand are 'i' (ignore "
                        "case), 'm' (multiline ^ and $) and 's' "
                        "(dot matches newlines too).")
            flag_bits |= _FLAG_MAP[letter]
        try:
            compiled = re.compile(pat, flag_bits)
        except re.error as e:
            return (f"That pattern isn't valid: {e.msg} "
                    f"(at character {e.pos} of the pattern).")
        notes = []
        if len(haystack) > _REGEX_TEXT_CAP:
            haystack = haystack[:_REGEX_TEXT_CAP]
            notes.append(f"(Only the first {_REGEX_TEXT_CAP} "
                         "characters of the text were searched.)")
        matches = list(compiled.finditer(haystack))
        if not matches:
            return (f"No matches — /{pat}/ doesn't appear in "
                    "that text." + (" " + " ".join(notes) if notes
                                    else ""))
        lines = [f"Found {len(matches)} match(es) for /{pat}/:"]
        for index, match in enumerate(matches[:_REGEX_SHOWN], 1):
            shown = match.group(0)
            if len(shown) > _REGEX_MATCH_CAP:
                shown = shown[:_REGEX_MATCH_CAP - 1] + "…"
            line = (f"{index}. '{shown}' at characters "
                    f"{match.start()}–{match.end()}")
            if compiled.groups:
                groups = tuple(
                    g if g is not None else "(didn't take part)"
                    for g in match.groups())
                line += f" — groups: {groups}"
            lines.append(line)
        if len(matches) > _REGEX_SHOWN:
            lines.append(f"…and {len(matches) - _REGEX_SHOWN} "
                         f"more match(es) not listed (the listing "
                         f"caps at {_REGEX_SHOWN}).")
        lines += notes
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Testing that pattern failed: {e}"


# ---------------------------------------------------------------------------
# CRON EXPLAIN — a 5-field schedule in plain words
# ---------------------------------------------------------------------------

_MONTH_NAMES = ("January", "February", "March", "April", "May",
                "June", "July", "August", "September", "October",
                "November", "December")
_WEEKDAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday",
                  "Thursday", "Friday", "Saturday")
# (label, low, high, names-or-None, plural noun for generic phrasing)
_CRON_FIELDS = (
    ("minute", 0, 59, None, "minutes"),
    ("hour", 0, 23, None, "hours"),
    ("day-of-month", 1, 31, None, "days of the month"),
    ("month", 1, 12, _MONTH_NAMES, "months"),
    ("day-of-week", 0, 7, _WEEKDAY_NAMES, "days of the week"),
)


def _cron_name(field_index: int, value: int) -> str:
    names = _CRON_FIELDS[field_index][3]
    if names is None:
        return str(value)
    if field_index == 4:                       # weekday: 7 = Sunday
        return names[value % 7]
    return names[value - 1]                    # months are 1-based


def _cron_field(text: str, field_index: int):
    """Parse one cron field.

    Returns (kind, data) — kind 'all', 'one', 'list', 'range' or
    'step' — or an honest error string. Values are validated
    against the field's range here.
    """
    label, low, high, _names, _plural = _CRON_FIELDS[field_index]

    def check(number: int):
        if number < low or number > high:
            return (f"The {label} field is off: {number} is "
                    f"outside its range ({low}–{high}).")
        return None

    def atom(token: str):
        if not re.fullmatch(r"[0-9]+", token or ""):
            return None
        return int(token)

    parts = text.split(",")
    if len(parts) > 1:
        values = []
        for part in parts:
            number = atom(part.strip())
            if number is None:
                return (f"The {label} field is off: '{part}' "
                        "isn't a plain number, and lists can only "
                        "hold plain numbers.")
            problem = check(number)
            if problem:
                return problem
            values.append(number)
        return ("list", sorted(set(values)))
    part = parts[0].strip()
    if part == "*":
        return ("all", None)
    if "/" in part:
        base_part, _slash, step_part = part.partition("/")
        step = atom(step_part.strip())
        if step is None or step < 1:
            return (f"The {label} field is off: the step in "
                    f"'{part}' has to be a whole number of 1 or "
                    "more.")
        if base_part.strip() == "*":
            return ("step", (low, high, step))
        if "-" in base_part:
            start_tok, _dash, end_tok = base_part.partition("-")
            start, end = atom(start_tok.strip()), atom(end_tok.strip())
            if start is None or end is None:
                return (f"The {label} field is off: '{part}' "
                        "isn't a range I can read.")
            for number in (start, end):
                problem = check(number)
                if problem:
                    return problem
            return ("step", (start, end, step))
        start = atom(base_part.strip())
        if start is None:
            return (f"The {label} field is off: '{part}' isn't "
                    "a step I can read.")
        problem = check(start)
        if problem:
            return problem
        return ("step", (start, high, step))
    if "-" in part:
        start_tok, _dash, end_tok = part.partition("-")
        start, end = atom(start_tok.strip()), atom(end_tok.strip())
        if start is None or end is None:
            return (f"The {label} field is off: '{part}' isn't "
                    "a range I can read.")
        for number in (start, end):
            problem = check(number)
            if problem:
                return problem
        if end < start:
            return (f"The {label} field is off: the range "
                    f"{start}–{end} runs backwards.")
        return ("range", (start, end))
    number = atom(part)
    if number is None:
        return (f"The {label} field is off: '{part}' isn't "
                "something I can read — fields hold numbers, "
                "'*', ranges like 9-17, steps like */5, or "
                "comma lists.")
    problem = check(number)
    if problem:
        return problem
    return ("one", number)


def _cron_values(parsed, field_index: int) -> list[int] | None:
    """The concrete value set of a parsed field, or None if open-ended."""
    kind, data = parsed
    label, low, high, _n, _p = _CRON_FIELDS[field_index]
    if kind == "all":
        return None
    if kind == "one":
        return [data]
    if kind == "list":
        return list(data)
    if kind == "range":
        return list(range(data[0], data[1] + 1))
    start, end, step = data                      # step
    return list(range(start, end + 1, step))


def _cron_join(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _cron_field_phrase(parsed, field_index: int) -> str:
    """One field described in words, for the field-by-field path."""
    label, low, high, _names, plural = _CRON_FIELDS[field_index]
    kind, data = parsed
    if kind == "all":
        return f"every {label if label != 'day-of-month' else 'day'}"
    if kind == "one":
        return f"{label} {_cron_name(field_index, data)}"
    if kind == "list":
        named = [_cron_name(field_index, v) for v in data]
        return f"{label} {_cron_join(named)}"
    if kind == "range":
        return (f"{label} {_cron_name(field_index, data[0])} "
                f"through {_cron_name(field_index, data[1])}")
    start, end, step = data
    if start == low and end == high:
        return f"every {step} {plural}"
    return (f"every {step} {plural} from "
            f"{_cron_name(field_index, start)} through "
            f"{_cron_name(field_index, end)}")


def cron_explain(expression: str = "") -> str:
    """Explain a 5-field cron expression in plain words."""
    try:
        text = str(expression or "").strip()
        if not text:
            return ("Give me a cron expression to explain — "
                    "five fields, like '0 9 * * 1-5'.")
        fields = text.split()
        if len(fields) != 5:
            return (f"A cron expression has 5 fields (minute, "
                    f"hour, day-of-month, month, day-of-week) — "
                    f"that one has {len(fields)}.")
        parsed = []
        for index, field_text in enumerate(fields):
            result = _cron_field(field_text, index)
            if isinstance(result, str):
                return result
            parsed.append(result)
        minute, hour, dom, month, dow = parsed

        def single(p):
            return p[1] if p[0] == "one" else None

        m_val, h_val = single(minute), single(hour)
        if m_val is not None and h_val is not None:
            clock = f"{h_val:02d}:{m_val:02d}"
            if dom[0] == "all" and month[0] == "all" \
                    and dow[0] == "all":
                return f"Every day at {clock}."
            dow_values = _cron_values(dow, 4)
            if dom[0] == "all" and month[0] == "all" \
                    and dow_values is not None:
                days = sorted({v % 7 for v in dow_values})
                named = [_WEEKDAY_NAMES[d] for d in days]
                if days == [1, 2, 3, 4, 5]:
                    when = "on weekdays (Monday to Friday)"
                elif days == [0, 6]:
                    when = "on weekends (Saturday and Sunday)"
                elif len(days) == 1:
                    when = f"on {named[0]}s"
                elif dow[0] == "range":
                    when = (f"on {named[0]} through {named[-1]}")
                else:
                    when = f"on {_cron_join(named)}"
                return f"At {clock} {when}, every week."
            dom_val = single(dom)
            if dom_val is not None and dow[0] == "all":
                month_val = single(month)
                if month[0] == "all":
                    return (f"At {clock} on day {dom_val} of "
                            "every month.")
                if month_val is not None:
                    return (f"At {clock} on {_MONTH_NAMES[month_val - 1]} "
                            f"{dom_val}, every year.")
        if all(p[0] == "all" for p in parsed):
            return "Every minute of every day."
        if minute[0] == "step" and minute[1][0] == 0 \
                and minute[1][1] == 59 \
                and all(p[0] == "all" for p in parsed[1:]):
            return f"Every {minute[1][2]} minutes, around the clock."
        if m_val is not None and hour[0] == "all" \
                and all(p[0] == "all" for p in parsed[2:]):
            return (f"Once an hour, at {m_val} minutes past "
                    "the hour.")
        phrases = [_cron_field_phrase(p, i)
                   for i, p in enumerate(parsed)]
        result = ("That schedule is: " + "; ".join(phrases) + ".")
        if dom[0] != "all" and dow[0] != "all":
            result += (" (Cron quirk: with both a day-of-month "
                       "and a day-of-week set, it runs when "
                       "EITHER matches.)")
        return result
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Explaining that schedule failed: {e}"


# ---------------------------------------------------------------------------
# SEMVER COMPARE — which of two versions is newer
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")


def _semver_parse(text: str):
    """(core tuple, prerelease string or None, build or None) or None."""
    match = _SEMVER_RE.match(text.strip())
    if not match:
        return None
    core = (int(match.group(1)), int(match.group(2)),
            int(match.group(3)))
    return core, match.group(4), match.group(5)


def _semver_pre_cmp(a: str | None, b: str | None) -> int:
    """Semver prerelease precedence: -1 a first, 0 equal, 1 b first."""
    if a is None and b is None:
        return 0
    if a is None:
        return 1            # a release outranks any prerelease
    if b is None:
        return -1
    a_parts, b_parts = a.split("."), b.split(".")
    for x, y in zip(a_parts, b_parts):
        if x == y:
            continue
        x_num, y_num = x.isdigit(), y.isdigit()
        if x_num and y_num:
            return -1 if int(x) < int(y) else 1
        if x_num:
            return -1       # numbers sort before words
        if y_num:
            return 1
        return -1 if x < y else 1
    if len(a_parts) == len(b_parts):
        return 0
    return -1 if len(a_parts) < len(b_parts) else 1


def semver_compare(a: str = "", b: str = "") -> str:
    """Compare two semantic versions and say which is newer."""
    try:
        left_text, right_text = str(a or "").strip(), str(b or "").strip()
        if not left_text or not right_text:
            return ("Give me two versions to compare — like "
                    "semver_compare('1.2.3', '1.3.0-beta.1').")
        left, right = _semver_parse(left_text), _semver_parse(right_text)
        if left is None:
            return (f"'{a}' isn't a version I can read — "
                    "expected major.minor.patch like 1.2.3, with "
                    "an optional prerelease like 1.2.3-beta.1.")
        if right is None:
            return (f"'{b}' isn't a version I can read — "
                    "expected major.minor.patch like 1.2.3, with "
                    "an optional prerelease like 1.2.3-beta.1.")
        l_core, l_pre, l_build = left
        r_core, r_pre, r_build = right
        if l_core != r_core:
            newer, older = ((left_text, right_text)
                            if l_core > r_core
                            else (right_text, left_text))
            return f"{newer} is newer than {older}."
        order = _semver_pre_cmp(l_pre, r_pre)
        if order == 0:
            result = (f"{left_text} and {right_text} are the "
                      "same version.")
            if l_build != r_build:
                result += (" (They differ only in build "
                           "metadata — the bit after '+' — which "
                           "doesn't affect ordering.)")
            return result
        pre_text, rel_text = ((left_text, right_text) if order < 0
                              else (right_text, left_text))
        return (f"{rel_text} is newer than {pre_text} — a "
                f"prerelease like {pre_text} always comes "
                f"before its release, {rel_text}.")
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Comparing those versions failed: {e}"


# ---------------------------------------------------------------------------
# TEXT DIFF — two blocks of text, line by line
# ---------------------------------------------------------------------------

def text_diff(text_a: str = "", text_b: str = "") -> str:
    """Show a unified diff of two blocks of text."""
    try:
        left = str(text_a or "").splitlines()
        right = str(text_b or "").splitlines()
        diff = list(difflib.unified_diff(
            left, right, fromfile="first text",
            tofile="second text", lineterm=""))
        if not diff:
            return "No differences."
        lines = [f"Differences between the two texts "
                 f"({len(diff)} diff line(s)):"]
        lines += diff[:_DIFF_LINE_CAP]
        if len(diff) > _DIFF_LINE_CAP:
            lines.append(
                f"…cut at {_DIFF_LINE_CAP} lines — "
                f"{len(diff) - _DIFF_LINE_CAP} more diff line(s) "
                "not shown.")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Diffing those texts failed: {e}"
