"""
Outdoors skills pack for VYUK — sun, rain, sky and garden
maths that all work offline: UV and air-quality readings against
the standard public bands, a rain-chance reader for a pasted
forecast, plant-watering guidance with a care log, a pet-feeding
log, walking time and step maths, a stargazing score, and a
daylight calculator.

Where each tool's truth lives — and what it honestly can't know:

- Nothing here touches the network. uv_advice, stargazing_score
  and air_quality_advice read a number the USER supplies against
  fixed public-guidance tables (the WHO/EPA UV bands, the US
  AQI bands) — they never fetch a live forecast, and their
  descriptions say so, so the model doesn't imply otherwise.
- rain_from_forecast only reads the text it is handed: it finds
  percentage figures and the words just before them.
- garden_watering works from a small built-in table of typical
  watering intervals; plants not in the table fall back to a
  generic interval and the answer says so.
- plant_care_log and pet_feeding_log persist as memory facts
  (core/memory/memory.py) under the categories `plants` and
  `pet_feeding`, one JSON payload per plant / per feeding, so
  they survive restarts like anything else VYUK remembers.
  Neither log gives advice — they record and report.
- daylight_hours is the standard NOAA approximation run at
  longitude 0: the day length is right for the latitude, and the
  sunrise/sunset clock times are Greenwich-meridian times (the
  result says how to shift them).

Same rules as the packs before it: plain functions returning
human-readable strings, never raising; persistence sits behind
the _store() seam so tests can substitute a temp MemoryStore;
stdlib only. Wiring: the parent (core/funnel/tools_registry.py) calls
register_all(registry) — this module never touches the registry
singleton itself.
"""

import datetime
import json
import math
import re

# INTENTIONAL: re-exports of the old module's stdlib imports, so the
# facade namespace matches the old module's (dir-diff-empty bar).
# The names below are the real stdlib modules, as before.

# INTENTIONAL: re-exports of every moved name, so importers of the
# old module keep working unchanged (hands3 discipline).
from .skills_outdoors_helpers import (
    _coerce_float,
    _fmt_num,
    _duration_words,
    _clock,
    _store,
    _facts,
    _PLANTS_CATEGORY,
    _PET_CATEGORY,
)

from .skills_outdoors_sky import (
    _UV_BANDS,
    uv_advice,
    _RAIN_RE,
    _RAIN_MAX,
    _RAIN_CONTEXT_CHARS,
    rain_from_forecast,
    stargazing_score,
    daylight_hours,
    _AQI_BANDS,
    air_quality_advice,
)

from .skills_outdoors_home import (
    _WATER_INTERVALS,
    _GENERIC_INTERVAL,
    _watering_interval,
    _watering_verdict,
    garden_watering,
    _load_plants,
    _save_plant,
    plant_care_log,
    _load_feedings,
    pet_feeding_log,
    _PACES,
    _STRIDE_M,
    _GOAL_STEPS,
    walk_time,
    step_goal_math,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Outdoors pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="uv_advice",
        description="Give sun-protection advice for a UV index number you supply — this doesn't fetch a live forecast, it reads your number against the standard bands (0–2 low up to 11+ extreme) and says what protection each band calls for. Use when the user quotes a UV index from a forecast or weather app and asks what it means for being outside.",
        parameters={
            "type": "object",
            "properties": {
                "uv_index": {"type": "number", "description": "The UV index number, e.g. 3 or 7.5"},
            },
            "required": ["uv_index"],
        },
        handler=uv_advice,
    )
    registry.register(
        name="rain_from_forecast",
        description="Read the rain chances out of a forecast the user pastes in. Finds every percentage figure, keeps the words just before it (the day or period it belongs to), lists up to eight, and names the wettest point. It only reads the text given — it fetches nothing. Use when the user pastes a weather forecast and asks when it's likeliest to rain.",
        parameters={
            "type": "object",
            "properties": {
                "forecast_text": {"type": "string", "description": "The pasted forecast text to scan for percentages"},
            },
            "required": ["forecast_text"],
        },
        handler=rain_from_forecast,
    )
    registry.register(
        name="garden_watering",
        description="Say whether a plant is due a water, from a small table of typical watering intervals (tomato, basil, mint, chilli, succulent, fern, lavender — anything else uses a generic interval and the answer says so). The pot size adjusts the interval: small pots dry out about a day faster, large ones hold moisture about a day longer. Use when the user asks whether a plant needs watering.",
        parameters={
            "type": "object",
            "properties": {
                "plant": {"type": "string", "description": "The plant's name, e.g. 'basil'"},
                "days_since_watered": {"type": "integer", "description": "Whole days since it was last watered (0 = today)"},
                "pot_size_cm": {"type": "number", "description": "Pot size in centimetres (default 20)"},
            },
            "required": ["plant", "days_since_watered"],
        },
        handler=garden_watering,
    )
    registry.register(
        name="plant_care_log",
        description="Keep a log of house plants and when each was last watered. Actions: 'add' records a plant (stamped watered today, optional note), 'watered' stamps an existing plant as watered today, 'list' shows every plant with days since watering and whether it's due, most overdue first. The log persists as memory facts. For one-off watering advice without the log, garden_watering is the tool.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'watered' or 'list'"},
                "plant": {"type": "string", "description": "The plant's name, for 'add' and 'watered'"},
                "note": {"type": "string", "description": "An optional note kept with the plant, e.g. 'kitchen window'"},
            },
            "required": ["action"],
        },
        handler=plant_care_log,
    )
    registry.register(
        name="pet_feeding_log",
        description="Keep a log of pet feedings — just the record, no feeding advice. Actions: 'add' records a feeding (pet and grams required, food optional), 'today' totals each pet's grams and feeding count for today, 'list' shows the five most recent feedings. The log persists as memory facts. Use when the user reports feeding a pet or asks what a pet has had today.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'today' or 'list'"},
                "pet": {"type": "string", "description": "The pet's name, for 'add'"},
                "food": {"type": "string", "description": "What was fed, optional, for 'add'"},
                "grams": {"type": "number", "description": "The amount fed in grams, for 'add'"},
            },
            "required": ["action"],
        },
        handler=pet_feeding_log,
    )
    registry.register(
        name="walk_time",
        description="Work out how long a walk takes and what it adds up to. Give the distance in kilometres and a pace — stroll (4 km/h), steady (5), brisk (6) or power (7) — or a pace_kmh number of your own. The result gives the time, an estimated step count at a stated 0.75 m stride, and a rough calorie range labelled as an estimate. Use when the user is planning a walk or commute on foot.",
        parameters={
            "type": "object",
            "properties": {
                "distance_km": {"type": "number", "description": "The distance in kilometres"},
                "pace": {"type": "string", "description": "'stroll', 'steady', 'brisk' (default) or 'power' — or a km/h number as text"},
                "pace_kmh": {"type": "number", "description": "Optional pace in km/h; overrides the pace word"},
            },
            "required": ["distance_km"],
        },
        handler=walk_time,
    )
    registry.register(
        name="step_goal_math",
        description="Turn a step count into the distance it covers, how long it takes to walk at a brisk pace, and what share it is of a 10,000-step day. Uses a stated 0.75 m stride. Use when the user quotes a step count from a tracker and asks what it means in distance or time.",
        parameters={
            "type": "object",
            "properties": {
                "steps": {"type": "integer", "description": "The step count, e.g. 6500"},
            },
            "required": ["steps"],
        },
        handler=step_goal_math,
    )
    registry.register(
        name="stargazing_score",
        description="Score a night for stargazing from two numbers you supply — cloud cover percent and the moon's illumination percent (this doesn't fetch a live sky report). Cloud weighs heavier than moonlight in the formula, which is shown in the result, and the score is labelled a rough guide. Use when the user quotes a forecast and wonders if it's worth going out with a telescope or camera.",
        parameters={
            "type": "object",
            "properties": {
                "cloud_pct": {"type": "number", "description": "Cloud cover as a percentage, 0 (clear) to 100 (overcast)"},
                "moon_illumination_pct": {"type": "number", "description": "The moon's illumination as a percentage, 0 (new) to 100 (full)"},
                "dark_hours": {"type": "number", "description": "How many hours of darkness are available (default 4) — context only, not part of the score"},
            },
            "required": ["cloud_pct", "moon_illumination_pct"],
        },
        handler=stargazing_score,
    )
    registry.register(
        name="daylight_hours",
        description="Work out the length of daylight at a latitude, and the sunrise and sunset times, using the standard NOAA solar approximation. The date defaults to today. The clock times are UTC for the Greenwich meridian (longitude 0) and the result explains how to shift them for other longitudes — the day length itself holds for the whole latitude. Use for planning around light, photography, or travel curiosity.",
        parameters={
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude in degrees, -90 to 90 (north positive)"},
                "date": {"type": "string", "description": "Optional date as YYYY-MM-DD — blank means today"},
            },
            "required": ["latitude"],
        },
        handler=daylight_hours,
    )
    registry.register(
        name="air_quality_advice",
        description="Give activity advice for a US air-quality index (AQI) number you supply — this doesn't fetch a live reading, it reads your number against the standard US bands (0–50 good up to 301–500 hazardous) and says what each band calls for. Use when the user quotes an AQI figure and asks whether it's OK to exercise or be outside.",
        parameters={
            "type": "object",
            "properties": {
                "aqi": {"type": "number", "description": "The US AQI number, 0 to 500"},
            },
            "required": ["aqi"],
        },
        handler=air_quality_advice,
    )

