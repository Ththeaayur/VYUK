"""Creative skills impl - haiku tool + its private banks and helpers.
Split from core/skills_creative_generate.py (part 2 of 2, close-out); code moved verbatim.
"""

import random


# ---------------------------------------------------------------------------
# HAIKU — 5-7-5 by construction, from syllable-tagged line banks
# ---------------------------------------------------------------------------

# Lines are stored as (text, syllables) with syllables exactly 5
# or 7, and a haiku is built ONLY from the tagged banks — so the
# form is guaranteed by construction rather than counted after
# the fact. (The proof suite re-counts every line with an
# independent vowel-group heuristic; the banks use plain,
# unambiguous vocabulary so the tags and the heuristic agree.)
_HAIKU_BANKS = {
    "nature": {
        "five": (
            ("soft rain on green leaves", 5),
            ("cold frost on pine boughs", 5),
            ("frog leaps in the pond", 5),
            ("wind through tall grass bends", 5),
            ("snow melts on stone steps", 5),
            ("bees hum in warm light", 5),
            ("plums fall in deep dusk", 5),
            ("mist hugs the low hills", 5),
            ("dry leaves drift down lanes", 5),
            ("first buds on bare twigs", 5),
            ("rain taps on tin roofs", 5),
            ("dew clings to silk webs", 5),
        ),
        "seven": (
            ("an old frog sits in still ponds", 7),
            ("the moon climbs over dark pines", 7),
            ("wild geese call across the marsh", 7),
            ("soft moss grows thick on old stones", 7),
            ("the cold stream sings past black rocks", 7),
            ("small ants march home through cut grass", 7),
            ("dusk brings out the first bright star", 7),
            ("the oak drops one gold leaf more", 7),
            ("pine shade cools the worn foot path", 7),
            ("spring rain wakes the sleeping seeds", 7),
            ("a thrush sings at the field's edge", 7),
            ("brown leaves spin in the cold wind", 7),
        ),
    },
    "sea": {
        "five": (
            ("waves slap the pier posts", 5),
            ("gulls cry at first light", 5),
            ("salt spray stings my face", 5),
            ("the tide pulls back slow", 5),
            ("crabs scuttle on sand", 5),
            ("foam threads the shore line", 5),
            ("deep bells toll far off", 5),
            ("a far light blinks red", 5),
            ("nets dry on dock rails", 5),
            ("storm clouds mass out west", 5),
        ),
        "seven": (
            ("the port sleeps in grey dawn mist", 7),
            ("the white horses ride wild surf", 7),
            ("a lone ship blinks at the strait", 7),
            ("night tide drags the small boats home", 7),
            ("the lighthouse sweeps the dark bay", 7),
            ("fish wives mend nets by lamplight", 7),
            ("the storm spends itself on cliffs", 7),
            ("grey swells roll from the cold north", 7),
            ("we haul the ropes while gulls wheel", 7),
            ("the salt wind stings my cold hands", 7),
        ),
    },
    "tech": {
        "five": (
            ("the screen glows at dawn", 5),
            ("code hums through the night", 5),
            ("soft clicks in dark rooms", 5),
            ("the bot learns my voice", 5),
            ("green text on black glass", 5),
            ("one bug in the build", 5),
            ("the fans spin up loud", 5),
            ("prompts bloom past midnight", 5),
        ),
        "seven": (
            ("servers blink in the cold room", 7),
            ("a small script sorts the night shift", 7),
            ("the cursor waits for my hand", 7),
            ("the low fans cool the bright chips", 7),
            ("my inbox fills while I sleep", 7),
            ("the update stalls near the end", 7),
            ("the robot counts sheep at night", 7),
            ("data flows like a dark stream", 7),
        ),
    },
    "city": {
        "five": (
            ("street signs hum at dusk", 5),
            ("the last train sighs home", 5),
            ("rain slicks the broad streets", 5),
            ("cabs honk at cross roads", 5),
            ("steam climbs from the drains", 5),
            ("pigeons crowd the square", 5),
            ("shop lights spill on stone", 5),
            ("the dawn bus sighs past", 5),
        ),
        "seven": (
            ("a sax cries in the subway", 7),
            ("the night clerk locks the hotel", 7),
            ("old street lamps bloom in the rain", 7),
            ("we cross the old bridge at dusk", 7),
            ("markets wake with shouts and steam", 7),
            ("tall towers blink at the clouds", 7),
            ("a stray dog barks at the dawn", 7),
            ("buses sigh at each wet stop", 7),
        ),
    },
}

_HAIKU_THEMES = tuple(_HAIKU_BANKS)
_HAIKU_ALIASES = {
    "ocean": "sea", "beach": "sea", "shore": "sea",
    "coast": "sea", "waves": "sea",
    "forest": "nature", "mountain": "nature", "garden": "nature",
    "rain": "nature", "snow": "nature", "river": "nature",
    "computer": "tech", "code": "tech", "robot": "tech",
    "screen": "tech", "data": "tech",
    "street": "city", "urban": "city", "town": "city",
    "train": "city", "traffic": "city",
}


def _resolve_haiku_topic(topic: str) -> tuple:
    """(theme, note) for a topic string: exact theme, alias hit,
    empty (a random theme), or the honest nature fallback."""
    t = (topic or "").strip().lower()
    if not t:
        theme = random.choice(_HAIKU_THEMES)
        return theme, f"(a haiku — theme: {theme})"
    if t in _HAIKU_BANKS:
        return t, f"(a haiku — theme: {t})"
    for alias, theme in _HAIKU_ALIASES.items():
        if alias in t:
            return theme, f"(a haiku — theme: {theme})"
    return "nature", (f"(I don't have a '{t}' bank, so here's "
                      "one from the nature bank)")


def _compose_haiku(theme: str, rng: random.Random) -> list:
    """[(text, syllables) × 3] — a five, a seven, a five, all
    from the theme's tagged banks."""
    bank = _HAIKU_BANKS[theme]
    return [rng.choice(bank["five"]), rng.choice(bank["seven"]),
            rng.choice(bank["five"])]


def haiku(topic: str = "") -> str:
    """A 5-7-5 haiku on a theme, built from tagged lines so the
    syllable form is guaranteed by construction."""
    theme, note = _resolve_haiku_topic(topic)
    lines = _compose_haiku(theme, random.Random())
    return "\n".join(text for text, _syl in lines) + "\n" + note
