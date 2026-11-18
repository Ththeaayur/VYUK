"""
Kitchen skills pack for VYUK — the cooking shelf: recipe
scaling, oven temperature conversion, roast timings by weight,
a pantry check, a weekly meal rotation, coffee ratios, dough
hydration, egg timings, spice substitutions and a grocery list.

Where each tool's truth lives:

- Everything here is arithmetic on tables written into this
  module — sensible, widely used kitchen figures, stated as the
  rough guides they are (the roast and egg tools both say so in
  their answers). No network, no lookups, no sensors.
- The grocery list persists as memory facts (core/memory/memory.py)
  under the fixed category `grocery`, one fact per item, so it
  survives restarts and shows up in the user's memory like
  anything else VYUK remembers.

Same rules as the packs before it: plain functions returning
human-readable strings, verdict first, handlers never raise —
bad input becomes an honest "I can't…" line, and every clamp
and assumption is disclosed in the result. Stdlib only.
Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import datetime
import difflib
import random
import re
from fractions import Fraction

from .skills_kitchen_helpers import (  # INTENTIONAL: facade re-exports
    _coerce_float,
    _fmt_num,
    _qty_text,
    _duration_words,
)

from .skills_kitchen_scale import (  # INTENTIONAL: facade re-exports
    _QTY_MIXED,
    _QTY_FRACTION,
    _QTY_PLAIN,
    _leading_qty,
    recipe_scale,
    _GAS_TABLE,
    _UNIT_ALIASES,
    oven_temp_convert,
    _ROAST_TABLE,
    _ROAST_NAMES,
    cooking_time_per_weight,
)

from .skills_kitchen_plan import (  # INTENTIONAL: facade re-exports
    _split_list,
    _normalise,
    _same_item,
    pantry_check,
    meal_rotation,
    _parse_ratio,
    _strength_word,
    coffee_ratio,
    _HYDRATION_BANDS,
    dough_hydration,
    _EGG_TABLE,
    egg_timer_math,
    _SPICE_TABLE,
    _SPICE_ALIASES,
    spice_substitute,
    _GROCERY_CATEGORY,
    _store,
    _grocery_facts,
    _count_word,
    grocery_list,
)


def register_all(registry) -> None:
    """Register every Kitchen pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="recipe_scale",
        description="Scale a recipe up or down: give the ingredients, one per line, each starting with its amount (whole numbers, decimals, fractions like 1/2 and mixed amounts like 1 1/2 all work), and a factor — 2 doubles, 0.5 halves. Only the leading amount of each line changes; lines without an amount are left alone and counted in a note. Use when a recipe needs to feed more or fewer people.",
        parameters={
            "type": "object",
            "properties": {
                "ingredients": {"type": "string", "description": "The ingredients, one per line, each starting with its amount — e.g. '200 g flour'"},
                "factor": {"type": "number", "description": "The scaling factor — 2 doubles the recipe, 0.5 halves it"},
            },
            "required": ["ingredients", "factor"],
        },
        handler=recipe_scale,
    )
    registry.register(
        name="oven_temp_convert",
        description="Convert an oven temperature between celsius, fahrenheit and gas mark, and give all three forms. Gas marks use the standard table (gas mark 6 is 200°C / 400°F); for plain temperatures the closest gas mark is named. Use when a recipe states a temperature in a scale you don't cook in.",
        parameters={
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "The temperature number (a whole gas mark 1–9 for gas)"},
                "from_unit": {"type": "string", "description": "Which scale the number is in: 'celsius', 'fahrenheit' or 'gas mark'"},
            },
            "required": ["value", "from_unit"],
        },
        handler=oven_temp_convert,
    )
    registry.register(
        name="cooking_time_per_weight",
        description="Rough roast timing for a joint or bird by weight: chicken, turkey, pork, and beef or lamb with a doneness (rare, medium, well). Uses the standard minutes-per-kilo plus a fixed extra, adds the resting time, and says plainly it's a rough guide and a thermometer is the safety check. Use when planning a roast.",
        parameters={
            "type": "object",
            "properties": {
                "protein": {"type": "string", "description": "What's roasting: 'chicken', 'turkey', 'beef', 'pork' or 'lamb'"},
                "weight_kg": {"type": "number", "description": "The weight in kilos"},
                "doneness": {"type": "string", "description": "For beef and lamb: 'rare', 'medium' or 'well' (default medium) — ignored for the others"},
            },
            "required": ["protein", "weight_kg"],
        },
        handler=cooking_time_per_weight,
    )
    registry.register(
        name="pantry_check",
        description="Check a recipe against the cupboard: give what you have and what the recipe needs, both as comma lists, and get back just what's missing — a small difference in plurals ('egg' vs 'eggs') is forgiven. Use before cooking or shopping, to find out whether a trip to the shop is needed at all.",
        parameters={
            "type": "object",
            "properties": {
                "have": {"type": "string", "description": "What you have, as a comma list — e.g. 'flour, eggs, milk'"},
                "need": {"type": "string", "description": "What the recipe needs, as a comma list"},
            },
            "required": ["have", "need"],
        },
        handler=pantry_check,
    )
    registry.register(
        name="meal_rotation",
        description="Plan dinners for the coming days by rotating a list of dishes. The order is shuffled the same way for the whole week (seeded by the week number), so asking twice gives the same plan; if there are fewer dishes than days, the repeats are flagged. Days run from 1 to 14. Use for weekly meal planning.",
        parameters={
            "type": "object",
            "properties": {
                "dishes": {"type": "string", "description": "The dishes to rotate, as a comma list — e.g. 'pasta, curry, stir fry, soup'"},
                "days": {"type": "integer", "description": "How many days to plan, 1 to 14 (default 7)"},
            },
            "required": ["dishes"],
        },
        handler=meal_rotation,
    )
    registry.register(
        name="coffee_ratio",
        description="Work out a coffee brew ratio like 1:15: give the coffee weight to get the water it needs, or the water volume to get the coffee for it. The result also says how strong the ratio is — 1:12 is strong, 1:17 is mild. Use when dialling in a pour-over, French press or filter brew.",
        parameters={
            "type": "object",
            "properties": {
                "coffee_g": {"type": "number", "description": "Coffee weight in grams — give this OR water_ml"},
                "water_ml": {"type": "number", "description": "Water volume in millilitres — give this OR coffee_g"},
                "ratio": {"type": "string", "description": "The brew ratio as '1:15' (default) — one part coffee to that many parts water"},
            },
            "required": [],
        },
        handler=coffee_ratio,
    )
    registry.register(
        name="dough_hydration",
        description="Work out a dough's hydration — the baker's percentage of total wet weight to total dry weight — from flour and water, with optional extra dry (seeds, wholemeal) and extra wet (milk, eggs, oil) weights. The result names the band: firm, sandwich loaf, rustic, or wet focaccia-style. Use when following or adjusting a bread recipe.",
        parameters={
            "type": "object",
            "properties": {
                "flour_g": {"type": "number", "description": "Flour weight in grams"},
                "water_g": {"type": "number", "description": "Water weight in grams"},
                "extra_dry_g": {"type": "number", "description": "Other dry ingredients in grams (default 0)"},
                "extra_wet_g": {"type": "number", "description": "Other wet ingredients in grams — milk, eggs, oil (default 0)"},
            },
            "required": ["flour_g", "water_g"],
        },
        handler=dough_hydration,
    )
    registry.register(
        name="egg_timer_math",
        description="How long to boil an egg for the yolk you want: small, medium or large; runny, soft, medium or hard; and whether it starts from the fridge (the default) or at room temperature. Timings assume the egg goes into water that's already boiling, and a fridge-cold egg gets an extra minute. Use when boiling eggs.",
        parameters={
            "type": "object",
            "properties": {
                "size": {"type": "string", "description": "'small', 'medium' or 'large'"},
                "doneness": {"type": "string", "description": "'runny', 'soft', 'medium' or 'hard'"},
                "from_fridge": {"type": "boolean", "description": "True (default) if the egg starts fridge-cold, false if it's at room temperature"},
            },
            "required": ["size", "doneness"],
        },
        handler=egg_timer_math,
    )
    registry.register(
        name="spice_substitute",
        description="Find a substitute for a spice that's run out, from a table of common swaps — allspice, cardamom, cinnamon, cumin, garam masala, paprika, turmeric and more — each with how much to use compared with the original. If the spice isn't in the table, the closest known ones are named instead of a guess. Use mid-recipe when the jar is empty.",
        parameters={
            "type": "object",
            "properties": {
                "spice": {"type": "string", "description": "The spice that's run out — e.g. 'garam masala'"},
            },
            "required": ["spice"],
        },
        handler=spice_substitute,
    )
    registry.register(
        name="grocery_list",
        description="Keep the grocery list: 'add' puts an item on it (adding the same item twice is refused politely), 'show' reads it back numbered, 'remove' takes one item off, 'clear' empties it. The list persists as memory facts, so it survives restarts. Use when the user wants something bought, asks what's on the list, or reports buying something.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add', 'show', 'remove' or 'clear'"},
                "item": {"type": "string", "description": "The item, for 'add' and 'remove' — e.g. 'oat milk'"},
            },
            "required": ["action"],
        },
        handler=grocery_list,
    )
