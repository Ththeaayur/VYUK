"""
TRAVEL skills for VYUK — pack 1: ten small, pure, offline
travel calculators: a trip countdown, driving time, a fuel-cost
splitter, layover lengths with honest tightness bands, a
meeting-time converter across cities, sunrise and sunset from
the NOAA solar approximation, the moon's phase from its synodic
age, compass bearings and crow-flies distances, and a
speed/distance/time solver.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise — bad input
becomes an honest "I can't…" line, and every assumption (a
next-day layover, a UTC label, an approximation) is stated in
the result. No I/O at all here: everything works from the
arguments alone, with no network, so sunrise times are the
standard NOAA approximation — good to a few minutes, not an
almanac to the second.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import math
from datetime import date as _date_cls
from datetime import datetime, time as _time_cls, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The world clock's fuzzy city resolver is the one source of
# truth for city -> timezone (same precedent as other packs
# borrowing helpers across modules).
from .skills_smart import _resolve_zone


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _num(value) -> float | None:
    """Coerce an argument to a float, or None when it isn't a
    usable number (booleans don't count as numbers here)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _parse_date(text: str):
    """-> a date, or None. ISO yyyy-mm-dd, the house format."""
    try:
        return _date_cls.fromisoformat((text or "").strip())
    except (TypeError, ValueError):
        return None


def _parse_hhmm(text: str):
    """-> (hour, minute), or None. Accepts '9:05' and '09:05'."""
    parts = (text or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _duration_words(total_minutes: float) -> str:
    """'1 hour 40 minutes' / '45 minutes' / '1 hour'."""
    minutes = int(round(total_minutes))
    if minutes <= 0:
        return "no time at all"
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if mins:
        parts.append(f"{mins} minute" + ("s" if mins != 1 else ""))
    return " ".join(parts) if parts else "less than a minute"


def _clock_words(dt: datetime) -> str:
    """'4:30 PM' — the world clock's spoken style."""
    return dt.strftime("%I:%M %p").lstrip("0")


# ---------------------------------------------------------------------------
# TRIP COUNTDOWN — days to a date, in words
# ---------------------------------------------------------------------------


def trip_countdown(destination: str = "", date: str = "") -> str:
    """Count the days to (or from) a trip date, in words."""
    target = _parse_date(date)
    if target is None:
        return ("I couldn't read that date — give it as "
                "yyyy-mm-dd, like 2026-12-25.")
    today = _date_cls.today()
    delta = (target - today).days
    weekday = target.strftime("%A")
    where = (destination or "").strip()
    subject = f"Your trip to {where}" if where else "That date"
    if delta == 0:
        return f"{subject} is today! ({weekday} {target.isoformat()})."
    if delta == 1:
        return (f"{subject} is tomorrow — {weekday} "
                f"{target.isoformat()}, just 1 day away.")
    if delta > 1:
        return (f"{subject} is in {delta} days — {weekday} "
                f"{target.isoformat()}.")
    if delta == -1:
        return (f"{subject} was yesterday — {weekday} "
                f"{target.isoformat()}.")
    return (f"{subject} was {-delta} days ago — {weekday} "
            f"{target.isoformat()}.")


# ---------------------------------------------------------------------------
# JOURNEY TIME — distance over speed, plus stops
# ---------------------------------------------------------------------------


def journey_time(distance_km: float = 0, speed_kmh: float = 0,
                 stops: int = 0, minutes_per_stop: int = 0) -> str:
    """Driving/travel time for a distance at a steady speed,
    with optional stops added on top."""
    distance = _num(distance_km)
    speed = _num(speed_kmh)
    if distance is None or speed is None:
        return ("I need numbers for this — a distance in "
                "kilometres and a speed in kilometres per hour.")
    if distance <= 0:
        return "The distance has to be more than zero kilometres."
    if speed <= 0:
        return ("The speed has to be more than zero — at zero "
                "you'd never get there.")
    try:
        stop_count = int(stops or 0)
        stop_minutes = int(minutes_per_stop or 0)
    except (TypeError, ValueError):
        return ("Stops have to be whole numbers — how many "
                "stops, and how many minutes each.")
    if stop_count < 0 or stop_minutes < 0:
        return "Stops can't be negative."
    moving = distance / speed * 60.0
    stopped = stop_count * stop_minutes
    total = moving + stopped
    lines = [f"{distance:g} km at {speed:g} km/h takes "
             f"{_duration_words(total)} of travel."]
    if stopped:
        lines.append(f"(That's {_duration_words(moving)} moving "
                     f"plus {_duration_words(stopped)} across "
                     f"{stop_count} stop(s).)")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# FUEL COST SPLIT — litres, total cost, per-person share
# ---------------------------------------------------------------------------


def fuel_cost_split(distance_km: float = 0, km_per_litre: float = 0,
                    price_per_litre: float = 0,
                    people: int = 1) -> str:
    """Fuel needed for a trip and how its cost splits."""
    distance = _num(distance_km)
    economy = _num(km_per_litre)
    price = _num(price_per_litre)
    if distance is None or economy is None or price is None:
        return ("I need numbers for this — the trip distance in "
                "km, the car's km per litre, and the fuel price "
                "per litre.")
    if distance <= 0:
        return "The distance has to be more than zero kilometres."
    if economy <= 0:
        return ("The fuel economy has to be more than zero km "
                "per litre.")
    if price < 0:
        return "The fuel price can't be negative."
    try:
        count = int(people)
    except (TypeError, ValueError):
        return "The number of people has to be a whole number."
    if count < 1:
        return "Tell me how many people are splitting it — at least one."
    litres = distance / economy
    total = litres * price
    lines = [f"The trip needs about {litres:,.2f} litres of fuel "
             f"({distance:g} km at {economy:g} km per litre), "
             f"costing {total:,.2f} in total."]
    if count > 1:
        lines.append(f"Split {count} ways, that's "
                     f"{total / count:,.2f} each.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# LAYOVER TIME — the gap between two clock times, honestly banded
# ---------------------------------------------------------------------------


def layover_time(arrival: str = "", departure: str = "") -> str:
    """How long a layover lasts, from an arrival and a departure
    clock time."""
    arr = _parse_hhmm(arrival)
    dep = _parse_hhmm(departure)
    if arr is None or dep is None:
        return ("Give me both times as HH:MM — like arrival "
                "'14:35' and departure '17:05'.")
    start = arr[0] * 60 + arr[1]
    end = dep[0] * 60 + dep[1]
    assumption = "the same day"
    if end < start:
        end += 24 * 60
        assumption = "the next day (the departure is after midnight)"
    minutes = end - start
    if minutes < 45:
        band = "that's a very tight connection"
    elif minutes <= 90:
        band = "that's tight but usual for a connection"
    elif minutes <= 180:
        band = "that's a comfortable connection"
    else:
        band = ("that's a long layover — enough time to leave the "
                "airport area briefly and still get back")
    return (f"Layover from {arrival.strip()} to "
            f"{departure.strip()} ({assumption}) is "
            f"{_duration_words(minutes)} — {band}.")


# ---------------------------------------------------------------------------
# TIMEZONE MEETING PLANNER — one moment, many cities
# ---------------------------------------------------------------------------

_MAX_CITIES = 10


def timezone_meeting_planner(time: str = "", from_city: str = "",
                             cities: str = "") -> str:
    """What a clock time in one city is in several others,
    today, with day shifts called out."""
    parsed = _parse_hhmm(time)
    if parsed is None:
        return ("Give me the time as HH:MM — like '15:30'.")
    origin = (from_city or "").strip()
    if not origin:
        return "Tell me which city the time starts in."
    zone_name, problem = _resolve_zone(origin)
    if problem:
        return problem
    names = [c.strip() for c in (cities or "").split(",") if c.strip()]
    if not names:
        return ("Tell me which cities to convert to — a "
                "comma-separated list like 'London, Tokyo, "
                "New York'.")
    capped = names[:_MAX_CITIES]
    try:
        origin_zone = ZoneInfo(zone_name)
        today = datetime.now(origin_zone).date()
        moment = datetime.combine(
            today, _time_cls(parsed[0], parsed[1]),
            tzinfo=origin_zone)
    except ZoneInfoNotFoundError:
        return ("Timezone data isn't available on this system, "
                "so I can't convert times between cities.")
    origin_label = zone_name.rsplit("/", 1)[-1].replace("_", " ")
    lines = [f"{_clock_words(moment)} in {origin_label} today is:"]
    unknown = []
    for name in capped:
        target_zone_name, issue = _resolve_zone(name)
        if issue:
            unknown.append(name)
            continue
        try:
            target = moment.astimezone(ZoneInfo(target_zone_name))
        except ZoneInfoNotFoundError:
            unknown.append(name)
            continue
        label = target_zone_name.rsplit(
            "/", 1)[-1].replace("_", " ")
        shift = (target.date() - moment.date()).days
        note = ""
        if shift == 1:
            note = " (+1 day — the next day there)"
        elif shift == -1:
            note = " (−1 day — the day before there)"
        elif shift:
            note = f" ({shift:+d} days there)"
        lines.append(f"• {_clock_words(target)} in {label}{note}")
    if len(names) > _MAX_CITIES:
        lines.append(f"(I converted the first {_MAX_CITIES} "
                     "cities — the list goes up to ten.)")
    if unknown:
        lines.append("(I couldn't find a timezone for: "
                     + ", ".join(unknown) + " — try names like "
                     "Tokyo, New York, London, Mumbai or Sydney.)")
    if len(lines) == 1:
        return ("I couldn't convert to any of those cities — "
                "try names like Tokyo, New York, London, Mumbai "
                "or Sydney.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SUNRISE & SUNSET — the NOAA solar approximation
# ---------------------------------------------------------------------------


def _offset_label_hours(offset: float) -> str:
    """'UTC' when zero, else 'UTC+5:30' / 'UTC−4'."""
    if offset == 0:
        return "UTC"
    sign = "+" if offset > 0 else "−"
    absolute = abs(offset)
    hours = int(absolute)
    minutes = int(round((absolute - hours) * 60))
    if minutes == 60:
        hours += 1
        minutes = 0
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def _minutes_clock(minutes: float) -> str:
    """Minutes since midnight -> '6:04 AM'."""
    total = int(round(minutes)) % (24 * 60)
    hour, minute = divmod(total, 60)
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def sunrise_sunset(latitude: float = 0, longitude: float = 0,
                   date: str = "",
                   tz_offset_hours: float = 0) -> str:
    """Sunrise and sunset for a place on a date, from the NOAA
    fractional-year approximation."""
    lat = _num(latitude)
    lng = _num(longitude)
    if lat is None or lng is None:
        return ("I need numbers for this — a latitude between "
                "−90 and 90 and a longitude between −180 and 180.")
    if not -90 <= lat <= 90:
        return (f"Latitude {lat:g} is outside the possible "
                "range, −90 to 90.")
    if not -180 <= lng <= 180:
        return (f"Longitude {lng:g} is outside the possible "
                "range, −180 to 180.")
    if (date or "").strip():
        day = _parse_date(date)
        if day is None:
            return ("I couldn't read that date — give it as "
                    "yyyy-mm-dd, like 2026-12-25.")
    else:
        day = _date_cls.today()
    offset = _num(tz_offset_hours)
    if offset is None or not -14 <= offset <= 14:
        return ("The timezone offset has to be a number of "
                "hours between −14 and +14, like 5.5 for "
                "India or −4.")
    # NOAA fractional year (evaluated at midday) and its series.
    gamma = 2 * math.pi / 365.0 * (day.timetuple().tm_yday - 1 + 0.5)
    eqtime = 229.18 * (0.000075
                       + 0.001868 * math.cos(gamma)
                       - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma)
                       - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918
            - 0.399912 * math.cos(gamma)
            + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma)
            + 0.00148 * math.sin(3 * gamma))
    lat_rad = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833))
              / (math.cos(lat_rad) * math.cos(decl))
              - math.tan(lat_rad) * math.tan(decl))
    label = _offset_label_hours(offset)
    when = f"on {day.strftime('%A')} {day.isoformat()}"
    place = f"at latitude {lat:g}, longitude {lng:g}"
    if cos_ha > 1:
        return (f"The sun never rises {place} {when} — that's "
                "polar night; it stays below the horizon all day.")
    if cos_ha < -1:
        return (f"The sun never sets {place} {when} — that's "
                "polar day, the midnight sun; it stays up "
                "all day.")
    ha = math.degrees(math.acos(cos_ha))
    sunrise_utc = 720 - 4 * (lng + ha) - eqtime
    sunset_utc = 720 - 4 * (lng - ha) - eqtime
    shift = offset * 60
    sunrise = _minutes_clock(sunrise_utc + shift)
    sunset = _minutes_clock(sunset_utc + shift)
    length = _duration_words((sunset_utc - sunrise_utc) % (24 * 60))
    return (f"{place} {when}: sunrise is {sunrise} and sunset "
            f"is {sunset} ({label}), giving {length} of "
            "daylight. (NOAA approximation — within a few "
            "minutes, not exact to the second.)")


# ---------------------------------------------------------------------------
# MOON PHASE — synodic age from a known new moon
# ---------------------------------------------------------------------------

_SYNODIC = 29.53058867
_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
_PHASE_NAMES = (
    "New Moon", "Waxing Crescent", "First Quarter",
    "Waxing Gibbous", "Full Moon", "Waning Gibbous",
    "Last Quarter", "Waning Crescent",
)


def moon_phase(date: str = "") -> str:
    """The moon's phase name and illumination for a date."""
    if (date or "").strip():
        day = _parse_date(date)
        if day is None:
            return ("I couldn't read that date — give it as "
                    "yyyy-mm-dd, like 2026-12-25.")
    else:
        day = _date_cls.today()
    moment = datetime(day.year, day.month, day.day, 12, 0,
                      tzinfo=timezone.utc)
    age = (moment - _NEW_MOON).total_seconds() / 86400.0 % _SYNODIC
    fraction = age / _SYNODIC
    illumination = (1 - math.cos(2 * math.pi * fraction)) / 2 * 100
    name = _PHASE_NAMES[int(fraction * 8 + 0.5) % 8]
    half = _SYNODIC / 2
    to_full = (half - age) % _SYNODIC
    if to_full < 0.75 or to_full > _SYNODIC - 0.75:
        full_note = "the moon is essentially full right now"
    elif to_full < 1.5:
        full_note = "the next full moon is about a day away"
    else:
        full_note = (f"the next full moon is in about "
                     f"{to_full:.0f} days")
    return (f"On {day.strftime('%A')} {day.isoformat()} the "
            f"moon is in its {name} phase, about "
            f"{illumination:.0f}% lit, and {full_note}. "
            "(Worked from the moon's 29.53-day cycle — an "
            "approximation, as phase timing is.)")


# ---------------------------------------------------------------------------
# BEARINGS & DISTANCES — one haversine helper, two tools
# ---------------------------------------------------------------------------

_EARTH_KM = 6371.0
_COMPASS16 = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def _coords(lat1, lon1, lat2, lon2):
    """-> (lat1, lon1, lat2, lon2) floats, or an error string."""
    values = [_num(v) for v in (lat1, lon1, lat2, lon2)]
    if any(v is None for v in values):
        return ("I need numbers for this — latitudes and "
                "longitudes for both points.")
    la1, lo1, la2, lo2 = values
    for value, limit, label in ((la1, 90, "latitude"),
                                (la2, 90, "latitude"),
                                (lo1, 180, "longitude"),
                                (lo2, 180, "longitude")):
        if not -limit <= value <= limit:
            return (f"A {label} of {value:g} is outside the "
                    f"possible range, −{limit} to {limit}.")
    return la1, lo1, la2, lo2


def _haversine_km(lat1: float, lon1: float,
                  lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def distance_between(lat1: float = 0, lon1: float = 0,
                     lat2: float = 0, lon2: float = 0) -> str:
    """Straight-line (great-circle) distance between two
    points given as latitude/longitude."""
    coords = _coords(lat1, lon1, lat2, lon2)
    if isinstance(coords, str):
        return coords
    la1, lo1, la2, lo2 = coords
    km = _haversine_km(la1, lo1, la2, lo2)
    if km < 0.01:
        return ("Those are effectively the same point — the "
                "distance between them is 0 km.")
    return (f"As the crow flies, ({la1:g}, {lo1:g}) to "
            f"({la2:g}, {lo2:g}) is {km:,.1f} km "
            f"({km * 0.621371:,.1f} miles) in a straight line — "
            "any real road or route will be longer.")


def compass_bearing(lat1: float = 0, lon1: float = 0,
                    lat2: float = 0, lon2: float = 0) -> str:
    """Initial compass bearing from one point to another,
    plus the crow-flies distance."""
    coords = _coords(lat1, lon1, lat2, lon2)
    if isinstance(coords, str):
        return coords
    la1, lo1, la2, lo2 = coords
    km = _haversine_km(la1, lo1, la2, lo2)
    if km < 0.01:
        return ("Those are effectively the same point — "
                "there's no direction of travel between them.")
    p1, p2 = math.radians(la1), math.radians(la2)
    dlmb = math.radians(lo2 - lo1)
    y = math.sin(dlmb) * math.cos(p2)
    x = (math.cos(p1) * math.sin(p2)
         - math.sin(p1) * math.cos(p2) * math.cos(dlmb))
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    point = _COMPASS16[int((bearing + 11.25) / 22.5) % 16]
    return (f"From ({la1:g}, {lo1:g}) to ({la2:g}, {lo2:g}), "
            f"the starting direction is {point} — a bearing "
            f"of {bearing:.0f}° — and the distance is "
            f"{km:,.1f} km as the crow flies. (The bearing is "
            "where you start out; a great-circle path curves "
            "from there.)")


# ---------------------------------------------------------------------------
# SPEED / DISTANCE / TIME — solve for the third value
# ---------------------------------------------------------------------------

_SDT_MODES = ("solve_time", "solve_distance", "solve_speed")


def speed_distance_time(mode: str = "", a: float = 0,
                        b: float = 0) -> str:
    """Solve a speed–distance–time problem for whichever of
    the three values is missing."""
    mode_key = (mode or "").strip().lower()
    if mode_key not in _SDT_MODES:
        return ("Tell me what to solve for: 'solve_time' "
                "(a = distance in km, b = speed in km/h), "
                "'solve_distance' (a = speed in km/h, b = time "
                "in hours), or 'solve_speed' (a = distance in "
                "km, b = time in hours).")
    first, second = _num(a), _num(b)
    if first is None or second is None:
        return "Both values have to be numbers."
    if mode_key == "solve_time":
        if first <= 0:
            return "The distance has to be more than zero kilometres."
        if second <= 0:
            return ("The speed has to be more than zero — at "
                    "zero you'd never get there.")
        minutes = first / second * 60.0
        return (f"Covering {first:g} kilometres at {second:g} "
                f"km/h takes {_duration_words(minutes)}.")
    if mode_key == "solve_distance":
        if first <= 0:
            return "The speed has to be more than zero km/h."
        if second <= 0:
            return "The time has to be more than zero hours."
        return (f"At {first:g} km/h for {second:g} hours, you "
                f"cover {first * second:,.1f} kilometres.")
    # solve_speed
    if first <= 0:
        return "The distance has to be more than zero kilometres."
    if second <= 0:
        return ("The time has to be more than zero hours — "
                "covering ground in no time isn't a speed.")
    return (f"Covering {first:g} kilometres in {second:g} "
            f"hours means an average speed of "
            f"{first / second:,.1f} km/h.")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Travel pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="trip_countdown",
        description="Count down to a trip or any future date: how many days away it is, in words — 'in 23 days', 'tomorrow', 'today!' — plus which weekday it falls on. Past dates are counted back just as plainly ('5 days ago'). Give the date as yyyy-mm-dd. Use when someone is looking forward to (or dreading) a date.",
        parameters={
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "What the date is for, e.g. 'Goa' or 'the exam' (optional)"},
                "date": {"type": "string", "description": "The date, as yyyy-mm-dd"},
            },
            "required": ["date"],
        },
        handler=trip_countdown,
    )
    registry.register(
        name="journey_time",
        description="Work out how long a journey takes from its distance in kilometres and a steady speed in km/h, with optional stops added on top (how many stops, and minutes per stop). Gives the total in hours and minutes, and breaks out moving time versus stopped time when stops are included. Use for road-trip and travel-time estimates.",
        parameters={
            "type": "object",
            "properties": {
                "distance_km": {"type": "number", "description": "Journey distance in kilometres"},
                "speed_kmh": {"type": "number", "description": "Average speed in kilometres per hour"},
                "stops": {"type": "integer", "description": "Number of stops along the way (default 0)"},
                "minutes_per_stop": {"type": "integer", "description": "Minutes spent at each stop (default 0)"},
            },
            "required": ["distance_km", "speed_kmh"],
        },
        handler=journey_time,
    )
    registry.register(
        name="fuel_cost_split",
        description="Work out the fuel for a trip: litres needed from the distance and the car's kilometres-per-litre figure, the total cost at a given price per litre, and — when more than one person is splitting it — each person's share. Use before a shared drive or road trip.",
        parameters={
            "type": "object",
            "properties": {
                "distance_km": {"type": "number", "description": "Trip distance in kilometres"},
                "km_per_litre": {"type": "number", "description": "The car's fuel economy in kilometres per litre"},
                "price_per_litre": {"type": "number", "description": "Fuel price per litre"},
                "people": {"type": "integer", "description": "How many people split the cost (default 1)"},
            },
            "required": ["distance_km", "km_per_litre", "price_per_litre"],
        },
        handler=fuel_cost_split,
    )
    registry.register(
        name="layover_time",
        description="How long a layover or connection lasts, from the arrival clock time and the departure clock time (both HH:MM). If the departure is earlier than the arrival it is taken to be after midnight, and the result says so. Also gives an honest tightness verdict — very tight, tight but usual, comfortable, or long. Use when judging a flight connection.",
        parameters={
            "type": "object",
            "properties": {
                "arrival": {"type": "string", "description": "Arrival time as HH:MM, e.g. '14:35'"},
                "departure": {"type": "string", "description": "Departure time as HH:MM, e.g. '17:05'"},
            },
            "required": ["arrival", "departure"],
        },
        handler=layover_time,
    )
    registry.register(
        name="timezone_meeting_planner",
        description="Take a clock time in one city and show what that same moment is in several other cities today — handy for scheduling a call across timezones. Each city line calls out a day shift (+1 day / −1 day) when the moment lands on a different date there, and any city that can't be found is named honestly rather than dropped. Up to ten cities, comma-separated.",
        parameters={
            "type": "object",
            "properties": {
                "time": {"type": "string", "description": "The time as HH:MM, e.g. '15:30'"},
                "from_city": {"type": "string", "description": "The city that time is in, e.g. 'Mumbai'"},
                "cities": {"type": "string", "description": "Comma-separated cities to convert to, e.g. 'London, Tokyo, New York'"},
            },
            "required": ["time", "from_city", "cities"],
        },
        handler=timezone_meeting_planner,
    )
    registry.register(
        name="sunrise_sunset",
        description="Sunrise and sunset times for a place given as latitude and longitude, for a date (yyyy-mm-dd, default today), computed with the NOAA solar approximation — accurate to a few minutes. Times are in UTC unless tz_offset_hours gives the place's offset (5.5 for India, −4 for New York in daylight time). Also gives the day length, and says plainly when the sun never rises or never sets (polar night / midnight sun). Use for photography, outdoor plans and curiosity.",
        parameters={
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude, −90 to 90"},
                "longitude": {"type": "number", "description": "Longitude, −180 to 180"},
                "date": {"type": "string", "description": "Date as yyyy-mm-dd (default: today)"},
                "tz_offset_hours": {"type": "number", "description": "The place's offset from UTC in hours, e.g. 5.5 (default 0 = UTC)"},
            },
            "required": ["latitude", "longitude"],
        },
        handler=sunrise_sunset,
    )
    registry.register(
        name="moon_phase",
        description="The moon's phase for a date (yyyy-mm-dd, default today): its name — New Moon, Waxing Crescent, First Quarter, Full Moon and so on — roughly how much of it is lit as a percentage, and about how many days until the next full moon. Worked from the moon's 29.53-day cycle, so treat it as a good approximation. Use for stargazing, photography and night plans.",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date as yyyy-mm-dd (default: today)"},
            },
            "required": [],
        },
        handler=moon_phase,
    )
    registry.register(
        name="compass_bearing",
        description="The starting compass direction from one point on Earth to another, given as latitudes and longitudes: the bearing in degrees and its 16-point compass word (like NNW), plus the straight-line distance. The bearing is the direction you set out in — a great-circle path curves from there. Use for navigation questions and geography curiosity. For just the distance, distance_between is the simpler tool.",
        parameters={
            "type": "object",
            "properties": {
                "lat1": {"type": "number", "description": "Latitude of the starting point"},
                "lon1": {"type": "number", "description": "Longitude of the starting point"},
                "lat2": {"type": "number", "description": "Latitude of the destination"},
                "lon2": {"type": "number", "description": "Longitude of the destination"},
            },
            "required": ["lat1", "lon1", "lat2", "lon2"],
        },
        handler=compass_bearing,
    )
    registry.register(
        name="distance_between",
        description="The straight-line ('as the crow flies') great-circle distance between two points given as latitudes and longitudes, in kilometres and miles. The result says plainly that any real road or route will be longer. Use for 'how far is X from Y' questions when coordinates are known. For the direction as well, use compass_bearing.",
        parameters={
            "type": "object",
            "properties": {
                "lat1": {"type": "number", "description": "Latitude of the first point"},
                "lon1": {"type": "number", "description": "Longitude of the first point"},
                "lat2": {"type": "number", "description": "Latitude of the second point"},
                "lon2": {"type": "number", "description": "Longitude of the second point"},
            },
            "required": ["lat1", "lon1", "lat2", "lon2"],
        },
        handler=distance_between,
    )
    registry.register(
        name="speed_distance_time",
        description="Solve a speed–distance–time problem for whichever value is missing. mode 'solve_time': a is the distance in km, b the speed in km/h. mode 'solve_distance': a is the speed in km/h, b the time in hours. mode 'solve_speed': a is the distance in km, b the time in hours. Use for quick travel and motion sums; journey_time adds stops on top for a fuller trip estimate.",
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["solve_time", "solve_distance", "solve_speed"], "description": "Which value to solve for"},
                "a": {"type": "number", "description": "First known value — see the mode meanings in the description"},
                "b": {"type": "number", "description": "Second known value — see the mode meanings in the description"},
            },
            "required": ["mode", "a", "b"],
        },
        handler=speed_distance_time,
    )
