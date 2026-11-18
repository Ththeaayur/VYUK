"""Outdoors sky tools — split from core/skills_outdoors.py (skills split Phase A, batch A10).
Move only: the code below is verbatim from the original file apart
from the module-level import lines at the top.
"""

import datetime
import math
import re

# INTENTIONAL: sky tools resolve these bare names in this module's
# globals (bare-name rule); they are defined in skills_outdoors_helpers.
from .skills_outdoors_helpers import (
    _clock,
    _coerce_float,
    _duration_words,
    _fmt_num,
)


# ---------------------------------------------------------------------------
# UV ADVICE — a user-supplied index against the standard bands
# ---------------------------------------------------------------------------

# (upper bound inclusive, band, protection line)
_UV_BANDS = (
    (2, "Low",
     "No protection is normally needed — you can safely stay "
     "outside."),
    (5, "Moderate",
     "Take care around midday: seek shade, wear a hat and "
     "sunglasses, and put sunscreen on exposed skin."),
    (7, "High",
     "Protection is essential: stay in shade around midday, "
     "cover up with clothing and a hat, and use sunscreen."),
    (10, "Very high",
     "Take extra care: avoid the midday sun where you can, "
     "stay in shade, cover up, and reapply sunscreen often."),
    (float("inf"), "Extreme",
     "Avoid being outside in the middle of the day; if you "
     "must go out, full shade, clothing, a hat and sunscreen "
     "are a must, and unprotected skin can burn quickly."),
)


def uv_advice(uv_index) -> str:
    """Sun-protection advice for a UV index number."""
    value = _coerce_float(uv_index)
    if value is None:
        return ("I need the UV index as a number — like 3 or "
                "7.5. Give me the number from your weather app "
                "or forecast and I'll read it against the "
                "bands.")
    if value < 0:
        return ("A UV index can't be below zero — the scale "
                "starts at 0 (no UV) and runs to 11 and beyond "
                "in strong sun. Check the number and try again.")
    shown = _fmt_num(value)
    for bound, band, line in _UV_BANDS:
        if value <= bound:
            return (f"UV index {shown} is in the '{band}' band. "
                    f"{line}")
    band, line = _UV_BANDS[-1][1], _UV_BANDS[-1][2]
    return f"UV index {shown} is in the '{band}' band. {line}"


# ---------------------------------------------------------------------------
# RAIN FROM FORECAST — read the percentages out of a pasted text
# ---------------------------------------------------------------------------

_RAIN_RE = re.compile(r"(\d{1,3})\s*%")
_RAIN_MAX = 8
_RAIN_CONTEXT_CHARS = 20


def rain_from_forecast(forecast_text: str = "") -> str:
    """Pull every rain-chance percentage out of a forecast text."""
    source = str(forecast_text or "")
    if not source.strip():
        return ("Paste the forecast text and I'll pick the rain "
                "chances out of it — I look for figures like "
                "'40%'.")
    finds = []
    for match in _RAIN_RE.finditer(source):
        pct = int(match.group(1))
        raw_start = match.start() - _RAIN_CONTEXT_CHARS
        start = max(0, raw_start)
        context = " ".join(source[start:match.start()].split())
        if raw_start > 0 and not source[raw_start - 1].isspace() \
                and " " in context:
            # The window opened mid-word — drop the fragment.
            context = context.split(" ", 1)[1]
        # Prefer the last clause in the window: in '…showers,
        # Sunday 65%' the label that belongs to the figure is
        # 'Sunday', not the tail of the previous clause.
        for sep in (", ", "; ", ": ", ". ", " — "):
            if sep in context:
                context = context.rsplit(sep, 1)[1]
        context = context.strip(" ,;:—-·•")
        finds.append((pct, context))
    if not finds:
        return ("No rain chances found in that text — I look "
                "for figures like '40%', and there were none.")
    lines = [f"Rain chances in that text ({len(finds)}):"]
    for pct, context in finds[:_RAIN_MAX]:
        where = context if context else "unspecified point"
        lines.append(f"• {where} — {pct}%")
    if len(finds) > _RAIN_MAX:
        lines.append(f"(Only the first {_RAIN_MAX} are shown — "
                     f"there were {len(finds)} percentages in "
                     "all.)")
    wettest_pct, wettest_where = max(finds, key=lambda f: f[0])
    where = wettest_where if wettest_where else "an unspecified point"
    lines.append(f"Wettest point: {wettest_pct}% at {where}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STARGAZING SCORE — two numbers in, one rough score out
# ---------------------------------------------------------------------------


def stargazing_score(cloud_pct, moon_illumination_pct,
                     dark_hours=4) -> str:
    """A rough stargazing score from cloud cover and moonlight."""
    cloud_raw = _coerce_float(cloud_pct)
    moon_raw = _coerce_float(moon_illumination_pct)
    if cloud_raw is None or moon_raw is None:
        return ("I need both numbers: cloud cover as a "
                "percentage and the moon's illumination as a "
                "percentage — like stargazing_score with "
                "cloud_pct 20 and moon_illumination_pct 40.")
    notes = []
    cloud = max(0.0, min(100.0, cloud_raw))
    moon = max(0.0, min(100.0, moon_illumination_pct))
    if cloud != cloud_raw:
        notes.append(f"cloud cover taken as {_fmt_num(cloud)}% "
                     "(percentages run 0 to 100)")
    if moon != moon_raw:
        notes.append(f"moon illumination taken as "
                     f"{_fmt_num(moon)}% (percentages run 0 "
                     "to 100)")
    score = round(100 * (1 - cloud / 100.0)
                  * (1 - 0.6 * moon / 100.0))
    if score >= 75:
        band = "excellent"
    elif score >= 50:
        band = "good"
    elif score >= 25:
        band = "fair"
    else:
        band = "poor"
    hours = _coerce_float(dark_hours)
    hours_line = ""
    if hours is not None and hours > 0:
        hours_line = (f" You have about {_fmt_num(hours)} "
                      "hours of darkness to use it.")
    working = (f"100 × (1 − {_fmt_num(cloud / 100.0)} cloud) "
               f"× (1 − 0.6 × {_fmt_num(moon / 100.0)} moon)")
    result = (f"Stargazing score: {score} out of 100 — "
              f"{band}. The working: {working} = {score}. "
              "It's a rough guide from just these two numbers "
              "— light pollution, haze and where the moon sits "
              "in the sky aren't in it."
              f"{hours_line}")
    if notes:
        result += " (" + "; ".join(notes) + ".)"
    return result


# ---------------------------------------------------------------------------
# DAYLIGHT HOURS — the NOAA approximation at longitude 0
# ---------------------------------------------------------------------------


def daylight_hours(latitude, date: str = "") -> str:
    """Day length plus sunrise/sunset (Greenwich meridian) for
    a latitude, from the NOAA solar approximation."""
    lat = _coerce_float(latitude)
    if lat is None:
        return ("I need the latitude as a number — like "
                "daylight_hours with latitude 51.5.")
    if not (-90.0 <= lat <= 90.0):
        return ("Latitude runs from -90 (the South Pole) to "
                "90 (the North Pole) — check the number.")
    date_text = str(date or "").strip()
    if date_text:
        try:
            day = datetime.date.fromisoformat(date_text)
        except ValueError:
            return ("The date must look like YYYY-MM-DD — "
                    "e.g. '2026-06-21'. Leave it blank for "
                    "today.")
    else:
        day = datetime.date.today()
    n = day.timetuple().tm_yday
    decl = math.radians(
        -23.44 * math.cos(math.radians(360.0 / 365.0 * (n + 10))))
    phi = math.radians(lat)
    cos_h = ((math.cos(math.radians(90.833))
              - math.sin(phi) * math.sin(decl))
             / (math.cos(phi) * math.cos(decl)))
    when = f"on {day:%a} {day.day} {day:%b} {day.year}"
    if cos_h < -1:
        return (f"At latitude {_fmt_num(lat)} the sun doesn't "
                f"set {when} — it's polar day, with about 24 "
                "hours of daylight.")
    if cos_h > 1:
        return (f"At latitude {_fmt_num(lat)} the sun doesn't "
                f"rise {when} — it's polar night, with no "
                "daylight that day.")
    hour_angle = math.degrees(math.acos(cos_h))
    day_minutes = 2 * hour_angle / 15.0 * 60.0
    gamma = 2 * math.pi / 365.0 * (n - 1 + 0.5)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma)
                       - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma)
                       - 0.040849 * math.sin(2 * gamma))
    sunrise = 720 - 4 * hour_angle - eqtime
    sunset = 720 + 4 * hour_angle - eqtime
    return (f"At latitude {_fmt_num(lat)} {when}: about "
            f"{_duration_words(day_minutes)} of daylight. "
            f"Sunrise about {_clock(sunrise)} UTC and sunset "
            f"about {_clock(sunset)} UTC — those clock times "
            "are for the Greenwich meridian (longitude 0); "
            "shift them by about 4 minutes for every degree "
            "of longitude east (earlier) or west (later).")


# ---------------------------------------------------------------------------
# AIR QUALITY ADVICE — a user-supplied AQI against the US bands
# ---------------------------------------------------------------------------

# (upper bound inclusive, band, activity line)
_AQI_BANDS = (
    (50, "Good",
     "Air quality is satisfactory and there's little or no "
     "risk — outdoor activities are fine as usual."),
    (100, "Moderate",
     "Air quality is acceptable. People who are unusually "
     "sensitive to air pollution may want to take it easier "
     "during long or heavy outdoor exertion."),
    (150, "Unhealthy for sensitive groups",
     "Members of sensitive groups may feel health effects; "
     "the general public is less likely to be affected. "
     "Sensitive groups should reduce long or heavy outdoor "
     "exertion."),
    (200, "Unhealthy",
     "Everyone may begin to feel health effects, and "
     "sensitive groups may feel more serious ones. Reduce "
     "long or heavy outdoor exertion, and sensitive groups "
     "should move exercise indoors."),
    (300, "Very unhealthy",
     "Health alert: everyone may feel more serious effects. "
     "Avoid long or heavy outdoor exertion and move "
     "activities indoors."),
    (500, "Hazardous",
     "Health warning of emergency conditions: the whole "
     "population is more likely to be affected. Stay "
     "indoors and avoid outdoor exertion."),
)


def air_quality_advice(aqi) -> str:
    """Activity advice for a US AQI number."""
    value = _coerce_float(aqi)
    if value is None:
        return ("I need the AQI as a number — like 42 or 160. "
                "Give me the number from your weather app or "
                "air report and I'll read it against the US "
                "bands.")
    if value < 0 or value > 500:
        return ("The US AQI scale runs from 0 to 500 — "
                f"{_fmt_num(value)} is outside it. Check the "
                "number (some countries use different scales) "
                "and try again.")
    shown = _fmt_num(value)
    for bound, band, line in _AQI_BANDS:
        if value <= bound:
            return f"AQI {shown} is '{band}'. {line}"
    band, line = _AQI_BANDS[-1][1], _AQI_BANDS[-1][2]
    return f"AQI {shown} is '{band}'. {line}"

