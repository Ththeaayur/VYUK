"""
Smart skills pack for VYUK (v5.4) — small, exact, pure-Python tools
that need no OS surface at all: a SAFE arithmetic evaluator (an AST
whitelist, never eval()), a unit converter with exact factors, a
world clock on zoneinfo, a password generator that writes ONLY to
the clipboard and never prints its own output, and a text-file
differ.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise and never fake a
result — a refusal or an error line says exactly why. Every cap
(expression length, power exponent, diff size, file size, password
length) is enforced in the open and disclosed in the result.

This pack is stdlib-only and behaves identically on Linux and
Windows: no platform branches except the clipboard writer in
generate_password, which tries the native route first and named
command-line clipboard tools second, in a fixed order.

Wiring: core/funnel/tools_registry.py calls register_all(registry) from
_register_skills_tools() — this module never touches the registry
singleton itself.
"""

import ast
import difflib
import math
import secrets
import string
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

# ---------------------------------------------------------------------------
# Shared number formatting (quick_math + unit_convert)
# ---------------------------------------------------------------------------


def _format_number(value) -> str:
    """Format a numeric result the way a person would say it:
    integers without a trailing .0 (inside float-exact range),
    everything else rounded to 10 significant digits, trimmed."""
    if isinstance(value, bool):  # never produced by our evaluators
        return str(value)
    if isinstance(value, int):
        return str(value)
    f = float(value)
    if math.isnan(f):
        return "not a number"
    if math.isinf(f):
        return "infinity" if f > 0 else "-infinity"
    if f == 0:
        return "0"
    if f.is_integer() and abs(f) < 1e16:
        return str(int(f))
    return f"{f:.10g}"


# ---------------------------------------------------------------------------
# QUICK MATH — a calculator that can only ever do arithmetic
# ---------------------------------------------------------------------------

_MATH_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "log": math.log, "log10": math.log10,
    "exp": math.exp, "abs": abs, "round": round,
    "min": min, "max": max, "floor": math.floor, "ceil": math.ceil,
}
_MATH_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MAX_EXPONENT = 1000
_MAX_EXPRESSION_CHARS = 500
_MAX_RESULT_DIGITS = 4300  # stay under Python's int->str safety limit

_MATH_REFUSAL = (
    "I can't evaluate that — quick_math only allows numbers, the "
    "operators + - * / // % **, parentheses, the functions sqrt, "
    "sin, cos, tan, asin, acos, atan, log, log10, exp, abs, round, "
    "min, max, floor and ceil, and the constants pi, e and tau. "
    "No other names, attributes or calls."
)


class _MathRefused(Exception):
    """The expression stepped outside the arithmetic whitelist."""


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)):
            raise _MathRefused("only plain numbers are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op = node.op
        if isinstance(op, ast.Pow):
            if abs(right) > _MAX_EXPONENT:
                raise _MathRefused(
                    f"power exponents are capped at {_MAX_EXPONENT} "
                    "— that one would explode")
            result = left ** right
        elif isinstance(op, ast.Add):
            result = left + right
        elif isinstance(op, ast.Sub):
            result = left - right
        elif isinstance(op, ast.Mult):
            result = left * right
        elif isinstance(op, ast.Div):
            result = left / right
        elif isinstance(op, ast.FloorDiv):
            result = left // right
        elif isinstance(op, ast.Mod):
            result = left % right
        else:
            raise _MathRefused("that operator isn't allowed")
        if isinstance(result, int) and abs(result) >= 10 ** _MAX_RESULT_DIGITS:
            raise _MathRefused("the result is too large to show")
        return result
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.USub):
            return -value
        raise _MathRefused("that unary operator isn't allowed")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _MATH_FUNCS:
            raise _MathRefused("that function isn't allowed")
        if node.keywords:
            raise _MathRefused("keyword arguments aren't allowed")
        args = [_eval_node(a) for a in node.args]
        name = node.func.id
        if name in ("min", "max"):
            if not args:
                raise _MathRefused(f"{name} needs at least one number")
        elif name == "round":
            if len(args) not in (1, 2):
                raise _MathRefused("round takes one or two numbers")
        elif len(args) != 1:
            raise _MathRefused(f"{name} takes exactly one number")
        return _MATH_FUNCS[name](*args)
    if isinstance(node, ast.Name):
        if node.id in _MATH_NAMES:
            return _MATH_NAMES[node.id]
        raise _MathRefused(f"the name '{node.id}' isn't allowed")
    raise _MathRefused(f"a {type(node).__name__} isn't allowed")


def quick_math(expression: str) -> str:
    """Evaluate a pure-arithmetic expression safely (AST whitelist)."""
    expr = (expression or "").strip()
    if not expr:
        return ("Give me an expression to evaluate, e.g. '23 * 1.5' "
                "or 'sqrt(2) + pi'.")
    if len(expr) > _MAX_EXPRESSION_CHARS:
        return (f"That expression is too long ({len(expr)} characters; "
                f"the cap is {_MAX_EXPRESSION_CHARS}).")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return _MATH_REFUSAL
    try:
        result = _eval_node(tree)
    except _MathRefused as refused:
        return f"{_MATH_REFUSAL} ({refused})"
    except ZeroDivisionError:
        return f"'{expr}' doesn't evaluate: division by zero."
    except OverflowError:
        return f"'{expr}' doesn't evaluate: the result overflows."
    except (ValueError, TypeError) as e:
        return f"'{expr}' doesn't evaluate: {e}."
    if isinstance(result, complex):
        return f"'{expr}' doesn't evaluate to a real number."
    if isinstance(result, float) and (math.isinf(result) or math.isnan(result)):
        return f"'{expr}' doesn't evaluate to a finite number."
    return f"{expr} = {_format_number(result)}"


# ---------------------------------------------------------------------------
# UNIT CONVERT — exact factors to a per-category base unit
# ---------------------------------------------------------------------------

# category -> {canonical unit: (factor to base, (aliases...))}
# Temperature is NOT here: it is affine and special-cased below.
_UNIT_TABLE: dict[str, dict[str, tuple[float, tuple[str, ...]]]] = {
    "length": {  # base: metre
        "mm": (0.001, ("millimeter", "millimeters", "millimetre", "millimetres")),
        "cm": (0.01, ("centimeter", "centimeters", "centimetre", "centimetres")),
        "m": (1.0, ("meter", "meters", "metre", "metres")),
        "km": (1000.0, ("kilometer", "kilometers", "kilometre", "kilometres")),
        "in": (0.0254, ("inch", "inches", '"')),
        "ft": (0.3048, ("foot", "feet", "'")),
        "yd": (0.9144, ("yard", "yards")),
        "mi": (1609.344, ("mile", "miles")),
    },
    "mass": {  # base: gram
        "mg": (0.001, ("milligram", "milligrams")),
        "g": (1.0, ("gram", "grams", "gramme", "grammes")),
        "kg": (1000.0, ("kilogram", "kilograms")),
        "t": (1_000_000.0, ("tonne", "tonnes", "metric ton", "metric tons")),
        "oz": (28.349523125, ("ounce", "ounces")),
        "lb": (453.59237, ("lbs", "pound", "pounds")),
        "stone": (6350.29318, ("stones", "st")),
    },
    "volume": {  # base: millilitre (US liquid measures)
        "ml": (1.0, ("milliliter", "milliliters", "millilitre", "millilitres", "cc")),
        "l": (1000.0, ("liter", "liters", "litre", "litres")),
        "tsp": (4.92892159375, ("teaspoon", "teaspoons")),
        "tbsp": (14.78676478125, ("tablespoon", "tablespoons")),
        "cup": (236.5882365, ("cups",)),
        "pint": (473.176473, ("pints", "pt")),
        "quart": (946.352946, ("quarts", "qt")),
        "gallon": (3785.411784, ("gallons", "gal")),
        "floz": (29.5735295625, ("fl oz", "fl_oz", "fluid ounce", "fluid ounces")),
    },
    "data": {  # base: byte, 1024-based (binary) throughout
        "bit": (0.125, ("bits",)),
        "B": (1.0, ("byte", "bytes")),
        "KB": (1024.0, ("kib", "kilobyte", "kilobytes")),
        "MB": (1024.0 ** 2, ("mib", "megabyte", "megabytes")),
        "GB": (1024.0 ** 3, ("gib", "gigabyte", "gigabytes")),
        "TB": (1024.0 ** 4, ("tib", "terabyte", "terabytes")),
        "PB": (1024.0 ** 5, ("pib", "petabyte", "petabytes")),
    },
    "data rate": {  # base: bit per second; bit rates are
        # decimal (kbps = 1000), byte rates use the same
        # 1024-based bytes as the data family
        "bps": (1.0, ("bit per second", "bits per second")),
        "kbps": (1000.0, ("kilobit per second", "kilobits per second", "kbit/s")),
        "mbps": (1000.0 ** 2, ("megabit per second", "megabits per second", "mbit/s")),
        "gbps": (1000.0 ** 3, ("gigabit per second", "gigabits per second", "gbit/s")),
        "KB/s": (8192.0, ("kilobyte per second", "kilobytes per second")),
        "MB/s": (8192.0 * 1024.0, ("megabyte per second", "megabytes per second")),
        "GB/s": (8192.0 * 1024.0 ** 2, ("gigabyte per second", "gigabytes per second")),
    },
    "speed": {  # base: metre per second
        "mps": (1.0, ("m/s", "metre per second", "meter per second")),
        "kmh": (1000.0 / 3600.0, ("km/h", "km-h", "kph")),
        "mph": (0.44704, ("mile per hour", "miles per hour")),
        "knot": (1852.0 / 3600.0, ("knots", "kt")),
    },
    "time": {  # base: second
        "ms": (0.001, ("millisecond", "milliseconds")),
        "s": (1.0, ("sec", "secs", "second", "seconds")),
        "min": (60.0, ("mins", "minute", "minutes")),
        "h": (3600.0, ("hr", "hrs", "hour", "hours")),
        "day": (86400.0, ("days", "d")),
        "week": (604800.0, ("weeks",)),
    },
    "pressure": {  # base: pascal
        "pa": (1.0, ("pascal", "pascals")),
        "kpa": (1000.0, ("kilopascal", "kilopascals")),
        "mpa": (1_000_000.0, ("megapascal", "megapascals")),
        "bar": (100_000.0, ("bars",)),
        "mbar": (100.0, ("millibar", "millibars", "hpa")),
        "psi": (6894.757293168, ("pound per square inch", "pounds per square inch")),
        "atm": (101325.0, ("atmosphere", "atmospheres")),
    },
    "energy": {  # base: joule
        "j": (1.0, ("joule", "joules")),
        "kj": (1000.0, ("kilojoule", "kilojoules")),
        "mj": (1_000_000.0, ("megajoule", "megajoules")),
        "cal": (4.184, ("calorie", "calories")),
        "kcal": (4184.0, ("kilocalorie", "kilocalories", "food calorie", "food calories")),
        "wh": (3600.0, ("watt hour", "watt hours", "watt-hour", "watt-hours")),
        "kwh": (3_600_000.0, ("kilowatt hour", "kilowatt hours", "kilowatt-hour", "kilowatt-hours", "unit", "units")),
        "btu": (1055.05585262, ("british thermal unit", "british thermal units")),
    },
}

_TEMP_UNITS = {
    "c": ("c", "celsius", "centigrade", "°c"),
    "f": ("f", "fahrenheit", "°f"),
    "k": ("k", "kelvin", "kelvins"),
}

_UNIT_LOOKUP: dict[str, tuple[str, str]] = {}  # alias -> (category, canonical)
for _category, _units in _UNIT_TABLE.items():
    for _canonical, (_factor, _aliases) in _units.items():
        _UNIT_LOOKUP[_canonical.lower()] = (_category, _canonical)
        for _alias in _aliases:
            _UNIT_LOOKUP[_alias.lower()] = (_category, _canonical)
for _canonical, _aliases in _TEMP_UNITS.items():
    for _alias in _aliases:
        _UNIT_LOOKUP[_alias.lower()] = ("temperature", _canonical)

_CATEGORIES_LINE = ("length, mass, volume, data, data rate, speed, time, "
                    "pressure, energy and temperature")


def _to_celsius(value: float, unit: str) -> float:
    if unit == "c":
        return value
    if unit == "f":
        return (value - 32.0) * 5.0 / 9.0
    return value - 273.15  # kelvin


def _from_celsius(value: float, unit: str) -> float:
    if unit == "c":
        return value
    if unit == "f":
        return value * 9.0 / 5.0 + 32.0
    return value + 273.15  # kelvin


def unit_convert(value, from_unit: str, to_unit: str) -> str:
    """Convert a quantity between units of one category (exact factors)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return f"'{value}' isn't a number I can convert."
    src_raw = (from_unit or "").strip().lower()
    dst_raw = (to_unit or "").strip().lower()
    src = _UNIT_LOOKUP.get(src_raw)
    dst = _UNIT_LOOKUP.get(dst_raw)
    if src is None or dst is None:
        unknown = from_unit if src is None else to_unit
        known = dst if src is None else src
        if known is not None:
            category = known[0]
            if category == "temperature":
                supported = "c, f, k (or celsius, fahrenheit, kelvin)"
            else:
                supported = ", ".join(_UNIT_TABLE[category].keys())
            return (f"I don't know the unit '{unknown}'. For "
                    f"{category} I support: {supported}. Conversions "
                    f"work within one category: {_CATEGORIES_LINE}.")
        return (f"I don't know the unit '{unknown}'. I convert within "
                f"one category: {_CATEGORIES_LINE} — e.g. km to mi, "
                "kg to lb, GB to MB, c to f.")
    (src_cat, src_unit), (dst_cat, dst_unit) = src, dst
    if src_cat != dst_cat:
        return (f"Can't convert {from_unit} to {to_unit}: {from_unit} "
                f"is a {src_cat} unit and {to_unit} is a {dst_cat} "
                f"unit. I convert within one category only: "
                f"{_CATEGORIES_LINE}.")
    if src_cat == "temperature":
        result = _from_celsius(_to_celsius(amount, src_unit), dst_unit)
    else:
        result = amount * (_UNIT_TABLE[src_cat][src_unit][0]
                           / _UNIT_TABLE[dst_cat][dst_unit][0])
    return (f"{_format_number(amount)} {src_unit} = "
            f"{_format_number(result)} {dst_unit}")


# ---------------------------------------------------------------------------
# WORLD CLOCK — zoneinfo, fuzzy city matching, honest relative offsets
# ---------------------------------------------------------------------------

_CITY_ALIASES = {
    "nyc": "America/New_York", "new york": "America/New_York",
    "la": "America/Los_Angeles", "los angeles": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "chicago": "America/Chicago", "toronto": "America/Toronto",
    "london": "Europe/London", "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "dubai": "Asia/Dubai",
    "mumbai": "Asia/Kolkata", "bombay": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "kolkata": "Asia/Kolkata",
    "calcutta": "Asia/Kolkata", "india": "Asia/Kolkata",
    "singapore": "Asia/Singapore", "hong kong": "Asia/Hong_Kong",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "tokyo": "Asia/Tokyo", "sydney": "Australia/Sydney",
}

_PANEL = (
    ("New York", "America/New_York"),
    ("London", "Europe/London"),
    ("Dubai", "Asia/Dubai"),
    ("Singapore", "Asia/Singapore"),
    ("Tokyo", "Asia/Tokyo"),
    ("Sydney", "Australia/Sydney"),
)


def _norm(text: str) -> str:
    """Lowercase, alphanumerics only — 'New York' / 'new_york' agree."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _offset_label(dt: datetime) -> str:
    offset = dt.utcoffset()
    seconds = int(offset.total_seconds()) if offset else 0
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def _clock_line(name: str, dt: datetime) -> str:
    time_str = dt.strftime("%I:%M %p").lstrip("0")
    abbr = dt.tzname() or ""
    return f"{time_str} in {name} ({abbr}, {_offset_label(dt)})"


def _relative_phrase(diff_seconds: float) -> str:
    if diff_seconds == 0:
        return "the same time as where you are"
    ahead = diff_seconds > 0
    total_minutes = int(round(abs(diff_seconds) / 60.0))
    hours, minutes = divmod(total_minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    span = " ".join(parts) if parts else "less than a minute"
    return f"that's {span} " + ("ahead of you" if ahead else "behind you")


def _here_line(diff_seconds: float) -> str:
    """Single-city closer (v5.31): the city's offset from THIS
    machine's local time, compact — "That's 4h 30m ahead of here."
    The offsets are read at the current instant, so DST is already
    accounted for by zoneinfo."""
    total_minutes = int(round(abs(diff_seconds) / 60.0))
    if total_minutes == 0:
        return "Same time as here."
    hours, minutes = divmod(total_minutes, 60)
    span = " ".join(p for p in (f"{hours}h" if hours else "",
                                f"{minutes}m" if minutes else "") if p)
    return (f"That's {span} "
            + ("ahead of here." if diff_seconds > 0
               else "behind here."))


def _resolve_zone(city: str):
    """-> (zone_name, None) or (None, message) — fuzzy, honest."""
    key = (city or "").strip().lower()
    if key in _CITY_ALIASES:
        return _CITY_ALIASES[key], None
    try:
        zones = sorted(available_timezones())
    except Exception:  # noqa: BLE001 — tzdata missing entirely
        zones = []
    if not zones:
        return None, ("Timezone data isn't available on this system, "
                      "so I can't look up world times.")
    wanted = _norm(key)
    exact = [z for z in zones if _norm(z.rsplit("/", 1)[-1]) == wanted]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, _ambiguous(city, exact)
    partial = [z for z in zones
               if wanted and wanted in _norm(z.rsplit("/", 1)[-1])]
    if len(partial) == 1:
        return partial[0], None
    if len(partial) > 1:
        return None, _ambiguous(city, partial)
    return None, (f"I couldn't find a timezone for '{city}'. Try a "
                  "city name like Tokyo, New York, London, Mumbai "
                  "or Sydney.")


def _ambiguous(city: str, zones: list[str]) -> str:
    shown = ", ".join(zones[:5])
    extra = f" (and {len(zones) - 5} more)" if len(zones) > 5 else ""
    return (f"'{city}' matches more than one timezone: {shown}{extra}. "
            "Which one did you mean?")


def _one_city_line(city: str, now_utc) -> str:
    zone_name, problem = _resolve_zone(city)
    if problem:
        return problem
    target = now_utc.astimezone(ZoneInfo(zone_name))
    local = now_utc.astimezone()
    name = zone_name.rsplit("/", 1)[-1].replace("_", " ")
    diff = (target.utcoffset() - local.utcoffset()).total_seconds()
    return f"{_clock_line(name, target)} — {_relative_phrase(diff)}"


def world_clock(city: str = "") -> str:
    """Local time in a city (fuzzy-matched), several cities when
    comma-separated, or a six-city panel."""
    now_utc = datetime.now(timezone.utc)
    city = (city or "").strip()
    try:
        if not city:
            lines = ["World clock:"]
            for name, zone in _PANEL:
                lines.append("• " + _clock_line(
                    name, now_utc.astimezone(ZoneInfo(zone))))
            return "\n".join(lines)
        parts = [p.strip() for p in city.split(",") if p.strip()]
        if len(parts) > 1:
            return "\n".join("• " + _one_city_line(p, now_utc)
                              for p in parts[:8])
        line = _one_city_line(city, now_utc)
        zone_name, problem = _resolve_zone(city)
        if problem:
            return line
        target = now_utc.astimezone(ZoneInfo(zone_name))
        local = now_utc.astimezone()
        diff = (target.utcoffset() - local.utcoffset()).total_seconds()
        return line + "\n" + _here_line(diff)
    except ZoneInfoNotFoundError:
        return ("Timezone data isn't available on this system, so I "
                "can't look up world times.")
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the world clock: {e}"


# ---------------------------------------------------------------------------
# GENERATE PASSWORD — the result goes to the clipboard, never to chat
# ---------------------------------------------------------------------------

_PASSWORD_SYMBOLS = "!@#$%^&*()-_=+[]{}<>?"
_MIN_PASSWORD = 8
_MAX_PASSWORD = 64


def _write_clipboard(text: str) -> bool:
    """Put text on the system clipboard; True only on confirmed success.

    Order: native Windows API, then whichever command-line clipboard
    tool exists (xclip / xsel / wl-copy / pbcopy / clip / PowerShell).
    Factored out so tests can stand in for the real clipboard. The
    text is passed on stdin or via the API — never on a command line.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            data = (text + "\0").encode("utf-16-le")
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if handle:
                locked = kernel32.GlobalLock(handle)
                ctypes.memmove(locked, data, len(data))
                kernel32.GlobalUnlock(handle)
                if user32.OpenClipboard(None):
                    try:
                        user32.EmptyClipboard()
                        if user32.SetClipboardData(CF_UNICODETEXT, handle):
                            return True
                    finally:
                        user32.CloseClipboard()
        except Exception:  # noqa: BLE001 — fall through to the tools
            pass
    import shutil

    payload = text.encode("utf-8")
    candidates = (
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
        ("wl-copy", ["wl-copy"]),
        ("pbcopy", ["pbcopy"]),
        ("clip", ["clip"]),
    )
    for exe, argv in candidates:
        if shutil.which(exe):
            try:
                subprocess.run(argv, input=payload,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=5)
                return True
            except Exception:  # noqa: BLE001 — try the next tool
                continue
    if sys.platform.startswith("win") and shutil.which("powershell"):
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                input=payload, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10)
            return True
        except Exception:  # noqa: BLE001
            pass
    return False


def generate_password(length=16, symbols=True) -> str:
    """Generate a strong password and copy it — never display it."""
    try:
        n = int(length)
    except (TypeError, ValueError):
        n = 16
    n = max(_MIN_PASSWORD, min(_MAX_PASSWORD, n))
    if isinstance(symbols, str):
        symbols = symbols.strip().lower() in ("true", "yes", "1", "on")
    classes = [string.ascii_lowercase, string.ascii_uppercase,
               string.digits]
    if symbols:
        classes.append(_PASSWORD_SYMBOLS)
    alphabet = "".join(classes)
    chars = [secrets.choice(cls) for cls in classes]
    chars += [secrets.choice(alphabet)
              for _ in range(n - len(classes))]
    secrets.SystemRandom().shuffle(chars)
    password = "".join(chars)
    if not _write_clipboard(password):
        return ("I generated a password but couldn't reach any "
                "clipboard on this system, so there's nowhere safe "
                "to put it — and I won't print it here or save it. "
                "On Linux, installing xclip or xsel gives me one; "
                "then ask again.")
    return (f"Generated a {n}-character password and put it on your "
            "clipboard — paste it where you need it; it isn't shown "
            "here or saved.")


# ---------------------------------------------------------------------------
# COMPARE FILES — a capped, disclosed unified diff
# ---------------------------------------------------------------------------

_MAX_COMPARE_BYTES = 2 * 1024 * 1024
_MAX_DIFF_LINES = 120


def _read_text_file(path: Path):
    """-> (text, None) or (None, honest problem string)."""
    if not path.exists():
        return None, f"File not found: {path}"
    if path.is_dir():
        return None, f"{path} is a folder, not a file I can compare."
    try:
        data = path.read_bytes()
    except OSError as e:
        return None, f"Could not read {path}: {e}"
    if len(data) > _MAX_COMPARE_BYTES:
        return None, (f"{path} is {len(data) / (1024 * 1024):.1f} MB — "
                      f"too large to compare here (the cap is "
                      f"{_MAX_COMPARE_BYTES // (1024 * 1024)} MB).")
    if b"\x00" in data:
        # A NUL byte never appears in real text files — binary,
        # whatever the decoder would have made of the rest.
        return None, (f"{path} looks like a binary file, so I "
                      "can't compare it as text.")
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        replaced = data.decode("utf-8", errors="replace")
        if replaced.count("�") > max(8, len(replaced) // 100):
            # � = U+FFFD: too much of the file failed to decode.
            return None, (f"{path} looks like a binary file, so I "
                          "can't compare it as text.")
        return replaced, None


def compare_files(path_a: str, path_b: str) -> str:
    """Unified diff of two text files (capped, with disclosure)."""
    a_path = Path(path_a or "").expanduser()
    b_path = Path(path_b or "").expanduser()
    text_a, problem = _read_text_file(a_path)
    if problem:
        return problem
    text_b, problem = _read_text_file(b_path)
    if problem:
        return problem
    if text_a == text_b:
        return "The files are identical."
    diff = list(difflib.unified_diff(
        text_a.splitlines(), text_b.splitlines(),
        fromfile=str(a_path), tofile=str(b_path), n=3, lineterm=""))
    header = f"Differences between {a_path} and {b_path}:"
    if len(diff) > _MAX_DIFF_LINES:
        shown = diff[:_MAX_DIFF_LINES]
        return "\n".join(
            [header, *shown,
             f"[diff truncated: showing {_MAX_DIFF_LINES} of "
             f"{len(diff)} lines]"])
    return "\n".join([header, *diff])


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Smart pack tool on a ToolRegistry-like object."""
    registry.register(
        name="quick_math",
        description="Evaluate a pure-arithmetic expression and return the exact result: + - * / // % **, parentheses, sqrt/sin/cos/tan/asin/acos/atan/log/log10/exp/abs/round/min/max/floor/ceil, and pi/e/tau. Nothing else is allowed — no names, code or system access. Use whenever the user asks for a calculation.",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "The arithmetic to evaluate, e.g. '23 * 1.5' or '(4 + 2) / sqrt(9)'"},
            },
            "required": ["expression"],
        },
        handler=quick_math,
    )
    registry.register(
        name="unit_convert",
        description="Convert a quantity between units of the same kind: length (mm cm m km in ft yd mi), mass (mg g kg t oz lb stone), volume (ml l tsp tbsp cup pint quart gallon floz), data (bit B KB MB GB TB PB, 1024-based), data rate (bps kbps mbps gbps and KB/s MB/s GB/s), speed (mps kmh mph knot), time (ms s min h day week), pressure (pa kpa mpa bar mbar psi atm), energy (j kj mj cal kcal wh kwh btu — food energy and electricity units included), temperature (c f k). The volume units double as cooking measures — teaspoons, tablespoons, cups, pints, quarts, gallons and fluid ounces ('fl_oz' works too). Cross-category conversions are refused honestly.",
        parameters={
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "The quantity to convert, e.g. 26.2"},
                "from_unit": {"type": "string", "description": "Unit the value is in, e.g. 'mi' or 'miles'"},
                "to_unit": {"type": "string", "description": "Unit to convert to, e.g. 'km'"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
        handler=unit_convert,
    )
    registry.register(
        name="world_clock",
        description="The current local time in a city anywhere in the world, with its UTC offset and how far ahead or behind the user it is. A single-city answer ends with a compact line giving the difference from this machine's own clock (e.g. 'That's 5h 30m ahead of here.'). With no city, returns a six-city panel (New York, London, Dubai, Singapore, Tokyo, Sydney). City names match fuzzily ('bombay' and 'nyc' work). Pass several cities separated by commas ('Tokyo, London, New York') to compare them in one call.",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. 'Tokyo', or several separated by commas — optional; empty gives the world panel"},
            },
            "required": [],
        },
        handler=world_clock,
    )
    registry.register(
        name="generate_password",
        description="Generate a strong random password (8–64 characters, one guaranteed character from each enabled class) and copy it straight to the clipboard. The password itself is NEVER shown in chat, spoken, or saved — the result only confirms it is on the clipboard. Use when the user asks for a new password.",
        parameters={
            "type": "object",
            "properties": {
                "length": {"type": "integer", "description": "Password length (default 16, clamped to 8–64)"},
                "symbols": {"type": "boolean", "description": "Include symbol characters (default true)"},
            },
            "required": [],
        },
        handler=generate_password,
    )
    registry.register(
        name="compare_files",
        description="Compare two text files and return their differences as a unified diff (3 lines of context, capped at 120 lines with the total disclosed). Says plainly when the files are identical, missing, or binary. For diffing two texts you already hold as strings, text_diff.",
        parameters={
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "Full path of the first file"},
                "path_b": {"type": "string", "description": "Full path of the second file"},
            },
            "required": ["path_a", "path_b"],
        },
        handler=compare_files,
    )
