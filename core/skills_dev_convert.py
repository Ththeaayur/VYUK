# Encode-convert group: base, url, uuid, epoch, color, ip.
import colorsys
import ipaddress
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
# INTENTIONAL: caps shared with the explain group live in helpers;
# this module imports the two it uses (bare-name rule, section 2 P3).
from .skills_dev_helpers import _BASE_DIGITS_CAP, _URL_TEXT_CAP
# ---------------------------------------------------------------------------
# BASE CONVERT — the same number, written in another base
# ---------------------------------------------------------------------------

_DIGIT_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_BASE_WORDS = {"binary": 2, "bin": 2, "octal": 8, "oct": 8,
               "decimal": 10, "dec": 10, "hex": 16,
               "hexadecimal": 16}


def _parse_base(value):
    """(base int, None) or (None, honest error) for a base argument."""
    if isinstance(value, bool):
        return None, ("A base has to be a number from 2 to 36, "
                      "or one of binary, octal, decimal, hex.")
    if isinstance(value, int):
        base = value
    elif isinstance(value, float) and value.is_integer():
        base = int(value)
    elif isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in _BASE_WORDS:
            base = _BASE_WORDS[cleaned]
        elif re.fullmatch(r"[0-9]+", cleaned or ""):
            base = int(cleaned)
        else:
            return None, (f"I don't recognise the base '{value}' "
                          "— bases are numbers from 2 to 36, or "
                          "the words binary, octal, decimal, hex.")
    else:
        return None, ("A base has to be a number from 2 to 36, "
                      "or one of binary, octal, decimal, hex.")
    if base < 2 or base > 36:
        return None, (f"Base {base} is outside the range I can "
                      "write — bases run from 2 to 36.")
    return base, None


def _to_base(number: int, base: int) -> str:
    if number == 0:
        return "0"
    out = []
    rest = number
    while rest:
        out.append(_DIGIT_ALPHABET[rest % base])
        rest //= base
    return "".join(reversed(out))


def base_convert(value="", from_base="", to_base="") -> str:
    """Rewrite a whole number from one base (2–36) into another."""
    try:
        from_int, err = _parse_base(from_base)
        if err:
            return err
        to_int, err = _parse_base(to_base)
        if err:
            return err
        raw = str(value if value is not None else "").strip()
        if isinstance(value, int) and not isinstance(value, bool):
            raw = str(value)
        if not raw:
            return ("Give me the number to convert — like "
                    "base_convert('FF', 'hex', 'decimal').")
        negative = False
        digits = raw
        if digits[:1] in ("-", "+"):
            negative = digits[0] == "-"
            digits = digits[1:]
        digits = digits.replace("_", "").replace(" ", "")
        if not digits:
            return f"'{raw}' has no digits in it to convert."
        if len(digits) > _BASE_DIGITS_CAP:
            return (f"That number has {len(digits)} digits — the "
                    f"longest I convert is {_BASE_DIGITS_CAP}.")
        valid = _DIGIT_ALPHABET[:from_int]
        for char in digits.lower():
            if char not in valid:
                if char in _DIGIT_ALPHABET:
                    return (f"'{char}' isn't a digit in base "
                            f"{from_int} — base {from_int} digits "
                            f"run 0–{valid[-1]}.")
                return (f"'{char}' isn't a digit character at "
                        "all, so that isn't a number I can "
                        "convert.")
        number = int(digits, from_int)
        converted = _to_base(number, to_int)
        if negative and number:
            converted = "-" + converted
        shown = ("-" if negative and number else "") + digits
        return (f"{shown} in base {from_int} is {converted} "
                f"in base {to_int}.")
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Converting that number failed: {e}"


# ---------------------------------------------------------------------------
# URL ENCODE / DECODE — text made safe for a URL, and back
# ---------------------------------------------------------------------------

def url_encode_decode(text: str = "", mode: str = "encode") -> str:
    """Percent-encode text for a URL, or decode it back."""
    try:
        action = str(mode or "encode").strip().lower()
        if action not in ("encode", "decode"):
            return ("The mode is 'encode' (make text safe for a "
                    "URL) or 'decode' (turn a URL-encoded string "
                    "back into text).")
        source = str(text or "")
        if not source:
            return ("Give me the text to "
                    f"{action} — like url_encode_decode"
                    f"(text='a b&c', mode='{action}').")
        notes = []
        if len(source) > _URL_TEXT_CAP:
            source = source[:_URL_TEXT_CAP]
            notes.append(f"(Only the first {_URL_TEXT_CAP} "
                         "characters were used.)")
        if action == "encode":
            result = urllib.parse.quote_plus(source)
            head = "Encoded for a URL: "
        else:
            result = urllib.parse.unquote_plus(source)
            head = "Decoded: "
        return head + result + (
            " " + " ".join(notes) if notes else "")
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"That URL conversion failed: {e}"


# ---------------------------------------------------------------------------
# UUID GENERATE — random (v4) or time-based (v1) identifiers
# ---------------------------------------------------------------------------

def uuid_generate(count: int = 1, version: int = 4) -> str:
    """Generate one or more UUIDs, version 4 (random) or 1 (time-based)."""
    try:
        try:
            how_many = int(count)
        except (TypeError, ValueError):
            return "The count has to be a whole number, 1 to 10."
        try:
            ver = int(version)
        except (TypeError, ValueError):
            return ("The version has to be 4 (random) or 1 "
                    "(time-based).")
        if ver not in (1, 4):
            return ("I can make version 4 UUIDs (random) and "
                    "version 1 (time-based) — versions 3 and 5 "
                    "need a name to hash, which this tool doesn't "
                    "take.")
        clamped = max(1, min(10, how_many))
        maker = uuid.uuid4 if ver == 4 else uuid.uuid1
        made = [str(maker()) for _ in range(clamped)]
        kind = "random" if ver == 4 else "time-based"
        lines = [f"Here {'is' if clamped == 1 else 'are'} "
                 f"{clamped} new UUID(s) (version {ver}, {kind}):"]
        lines += made
        if clamped != how_many:
            lines.append(f"(I make between 1 and 10 at a time — "
                         f"you asked for {how_many}.)")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Generating UUIDs failed: {e}"


# ---------------------------------------------------------------------------
# EPOCH CONVERT — unix timestamps and human dates, both ways
# ---------------------------------------------------------------------------

_MS_THRESHOLD = 100_000_000_000      # 1e11: at/above reads as milliseconds


def _epoch_format(ts: float) -> str:
    utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    local = datetime.fromtimestamp(ts).astimezone()
    zone = local.tzname() or "local time"
    return (f"{utc:%A %d %B %Y, %H:%M:%S} UTC "
            f"({utc.isoformat()}); locally that's "
            f"{local:%A %d %B %Y, %H:%M:%S} {zone}.")


def epoch_convert(value="") -> str:
    """Convert a unix timestamp to human dates, or an ISO date to a timestamp."""
    try:
        if isinstance(value, bool):
            return ("Give me a unix timestamp (digits, seconds "
                    "or milliseconds) or an ISO date/time like "
                    "2026-10-22T08:00:00.")
        number = None
        if isinstance(value, (int, float)):
            number = float(value)
        elif isinstance(value, str):
            cleaned = value.strip()
            if re.fullmatch(r"[+-]?[0-9]+", cleaned or ""):
                number = float(int(cleaned))
            elif re.fullmatch(r"[+-]?[0-9]+\.[0-9]+", cleaned or ""):
                number = float(cleaned)
            elif cleaned:
                iso_text = cleaned
                if iso_text[-1:] in ("Z", "z"):
                    iso_text = iso_text[:-1] + "+00:00"
                try:
                    moment = datetime.fromisoformat(iso_text)
                except ValueError:
                    return (f"I can't tell what '{value}' is — "
                            "give me a unix timestamp (digits) "
                            "or an ISO date/time like "
                            "2026-10-22T08:00:00.")
                if moment.tzinfo is None:
                    seconds = int(moment.timestamp())
                    return (f"{cleaned} is unix time {seconds} "
                            "(seconds). (No time zone was given, "
                            "so I read it as local time.)")
                return (f"{cleaned} is unix time "
                        f"{int(moment.timestamp())} (seconds).")
        if number is None:
            return ("Give me a unix timestamp (digits, seconds "
                    "or milliseconds) or an ISO date/time like "
                    "2026-10-22T08:00:00.")
        magnitude = abs(number)
        if magnitude >= _MS_THRESHOLD:
            seconds = number / 1000.0
            unit = "milliseconds"
        else:
            seconds = number
            unit = "seconds"
        try:
            rendered = _epoch_format(seconds)
        except (OverflowError, OSError, ValueError):
            return ("That timestamp lands outside the range of "
                    "dates I can convert (roughly the years "
                    "1678–2262).")
        shown = (str(int(number)) if number == int(number)
                 else str(number))
        return f"{shown} (read as {unit} since the epoch) is {rendered}"
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Converting that timestamp failed: {e}"


# ---------------------------------------------------------------------------
# COLOR CONVERT — one colour as hex, rgb and hsl
# ---------------------------------------------------------------------------

_COLOR_NAMES = {
    "black": (0, 0, 0), "silver": (192, 192, 192),
    "gray": (128, 128, 128), "grey": (128, 128, 128),
    "white": (255, 255, 255), "maroon": (128, 0, 0),
    "red": (255, 0, 0), "purple": (128, 0, 128),
    "fuchsia": (255, 0, 255), "magenta": (255, 0, 255),
    "green": (0, 128, 0), "lime": (0, 255, 0),
    "olive": (128, 128, 0), "yellow": (255, 255, 0),
    "navy": (0, 0, 128), "blue": (0, 0, 255),
    "teal": (0, 128, 128), "aqua": (0, 255, 255),
    "cyan": (0, 255, 255), "orange": (255, 165, 0),
    "pink": (255, 192, 203), "brown": (165, 42, 42),
    "gold": (255, 215, 0), "indigo": (75, 0, 130),
    "violet": (238, 130, 238),
}
_RGB_RE = re.compile(
    r"^rgb\(\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*,"
    r"\s*([0-9]{1,3})\s*\)$", re.IGNORECASE)


def _percent(fraction: float) -> str:
    value = fraction * 100.0
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def color_convert(color: str = "") -> str:
    """Convert a colour between hex, rgb() and hsl() forms."""
    try:
        raw = str(color or "").strip()
        if not raw:
            return ("Give me a colour — like '#FF0000', "
                    "'rgb(255, 0, 0)', or a basic name like "
                    "'teal'.")
        rgb = None
        match = _RGB_RE.match(raw)
        if match:
            rgb = tuple(int(part) for part in match.groups())
            if any(part > 255 for part in rgb):
                return ("rgb() values run from 0 to 255 each — "
                        f"in '{raw}' one of them is bigger than "
                        "that.")
        elif raw.startswith("#") or re.fullmatch(
                r"[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}", raw or ""):
            digits = raw.lstrip("#")
            if len(digits) == 3:
                digits = "".join(c * 2 for c in digits)
            if len(digits) == 6 and re.fullmatch(
                    r"[0-9A-Fa-f]{6}", digits):
                rgb = tuple(int(digits[i:i + 2], 16)
                            for i in (0, 2, 4))
        elif raw.lower() in _COLOR_NAMES:
            rgb = _COLOR_NAMES[raw.lower()]
        if rgb is None:
            return (f"'{raw}' isn't a colour I can read — give "
                    "me #RRGGBB or #RGB, rgb(r, g, b), or a "
                    "basic name like red, teal, navy or orange.")
        r, g, b = rgb
        hex_form = f"#{r:02X}{g:02X}{b:02X}"
        hue, lightness, saturation = colorsys.rgb_to_hls(
            r / 255.0, g / 255.0, b / 255.0)
        degrees = int(round(hue * 360.0)) % 360
        hsl_form = (f"hsl({degrees}, {_percent(saturation)}%, "
                    f"{_percent(lightness)}%)")
        return (f"{raw} is {hex_form} — rgb({r}, {g}, {b}), "
                f"{hsl_form}.")
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Converting that colour failed: {e}"


# ---------------------------------------------------------------------------
# IP SUBNET CALC — the numbers of a network
# ---------------------------------------------------------------------------

def ip_subnet_calc(cidr: str = "") -> str:
    """Work out a subnet's network, broadcast, mask and host range."""
    try:
        raw = str(cidr or "").strip()
        if not raw:
            return ("Give me a network in CIDR form — like "
                    "ip_subnet_calc('192.168.1.0/24').")
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError as e:
            return (f"'{raw}' isn't a network I can read: {e}")
        lines = []
        given = ipaddress.ip_address(raw.split("/")[0].strip()) \
            if "/" in raw else None
        if given is not None and given != network.network_address:
            lines.append(
                f"You gave a host address — the network it "
                f"belongs to is {network.network_address}/"
                f"{network.prefixlen}.")
        scope = ("a private range (not reachable from the "
                 "public internet)" if network.is_private
                 else "a public range")
        if isinstance(network, ipaddress.IPv6Network):
            lines.append(f"Network: {network.network_address} "
                         f"(prefix /{network.prefixlen}) — "
                         f"{scope}.")
            lines.append(
                f"It holds {network.num_addresses:,} "
                f"address(es), from {network.network_address} "
                f"to {network.broadcast_address}.")
            return "\n".join(lines)
        total = network.num_addresses
        lines.append(f"Network: {network.network_address}/"
                     f"{network.prefixlen} — {scope}.")
        lines.append(f"Netmask: {network.netmask}.")
        if network.prefixlen == 32:
            lines.append(
                "That's a single-host route: the one address "
                f"{network.network_address} is the whole "
                "network, so there's no separate broadcast "
                "address and exactly 1 usable host.")
        elif network.prefixlen == 31:
            lines.append(
                f"Broadcast: {network.broadcast_address}. "
                "A /31 is a point-to-point link (RFC 3021): "
                "both addresses are usable hosts, so the range "
                f"{network.network_address}–"
                f"{network.broadcast_address} gives 2 usable "
                "hosts.")
        else:
            lines.append(
                f"Broadcast: {network.broadcast_address}.")
            lines.append(
                f"Usable hosts: {total - 2:,} — from "
                f"{network.network_address + 1} to "
                f"{network.broadcast_address - 1} "
                f"(the network holds {total:,} addresses in "
                "total).")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Working out that subnet failed: {e}"
