"""Creative skills impl - name generator tool + its private banks and helpers.
Split from core/skills_creative_generate.py (part 2 of 2, close-out); code moved verbatim.
"""

import random


# ---------------------------------------------------------------------------
# NAME GENERATOR — combinatorial banks, >= 500 combinations per kind
# ---------------------------------------------------------------------------

_NAME_FIRST = (
    "Aarav", "Mira", "Jonas", "Priya", "Kenji", "Lucia", "Omar",
    "Ingrid", "Felix", "Zara", "Mateo", "Anaya", "Hugo", "Sana",
    "Elias", "Rhea", "Tomas", "Ivy", "Cyrus", "Lena", "Dante",
    "Noor", "Silas", "Amara", "Vikram", "Elena",
)
_NAME_LAST = (
    "Sharma", "Novak", "Garcia", "Khan", "Tanaka", "Rossi",
    "Haddad", "Lindqvist", "Moreau", "Okafor", "Silva", "Iyer",
    "Weber", "Nasser", "Kovacs", "Dubey", "Fontaine", "Ali",
    "Bergstrom", "Costa", "Dube", "Elmi", "Farrell", "Gauthier",
    "Hoshino", "Ivanova",
)
_BAND_ADJ = (
    "Velvet", "Electric", "Hollow", "Golden", "Midnight", "Paper",
    "Iron", "Silent", "Crimson", "Lunar", "Static", "Honey",
    "Broken", "Neon", "Gentle", "Savage", "Amber", "Cosmic",
    "Restless", "Ivory", "Thunder", "Mellow",
)
_BAND_NOUN = (
    "Foxes", "Rivers", "Machines", "Gardens", "Wolves", "Mirrors",
    "Comets", "Harbor", "Violets", "Engines", "Sparrows", "Oceans",
    "Lanterns", "Rebels", "Clouds", "Tigers", "Windows", "Echoes",
    "Meadows", "Rockets", "Sisters", "Pyramids", "Monsoons",
    "Ghosts",
)
_START_A = (
    "VYUK", "Quan", "Zeb", "Kip", "Ora", "Ven", "Tal", "Nex",
    "Umi", "Pyr", "Sol", "Kav", "Rin", "Ost", "Wil", "Xan",
    "Dru", "Miv", "Halo", "Jot", "Fer", "Gly", "Asp", "Cor",
)
_START_B = (
    "ly", "hub", "pay", "base", "loop", "stack", "wise", "grid",
    "flow", "sync", "labs", "mint", "dock", "beam", "port", "kit",
    "wave", "nest", "forge", "byte", "path", "zen",
)
_PET_NAMES = (
    "Biscuit", "Momo", "Pepper", "Ziggy", "Luna", "Milo", "Poppy",
    "Bruno", "Clover", "Dash", "Miso", "Ruby", "Otis", "Pearl",
    "Simba", "Tofu", "Willow", "Rocky", "Mochi", "Hazel", "Bingo",
    "Suki", "Maple", "Chester", "Nori", "Gnocchi",
)
_PET_EPITHETS = (
    "the Brave", "the Tiny", "the Magnificent", "the Sleepy",
    "the Fearless", "the Wobbly", "the Dignified",
    "the Mischievous", "the Gentle", "the Proud", "the Curious",
    "the Lazy", "the Noble", "the Cheerful", "the Grumpy",
    "the Swift", "the Loyal", "the Dramatic", "the Hungry",
    "the Wise",
)
_PLACE_PRE = (
    "Ember", "Thorn", "Raven", "Storm", "Golden", "Night",
    "Frost", "Elder", "Wolf", "Star", "Mist", "Iron", "Dawn",
    "Grim", "Ashen", "Oak", "Stone", "Bright", "Shadow",
    "Crystal", "Dragon", "Moon",
)
_PLACE_SUF = (
    "fell", "mere", "shire", "hold", "ford", "crest", "watch",
    "gate", "reach", "moor", "hollow", "spire", "bridge", "keep",
    "vale", "throne", "rock", "field", "shore", "tower", "wood",
    "peak", "haven", "fall",
)
_HERO_PRE = (
    "Captain", "Doctor", "The Crimson", "The Silent", "Professor",
    "Agent", "The Golden", "Major", "The Velvet", "Sergeant",
    "The Iron", "Madame", "The Midnight", "Baron", "The Solar",
    "Commander", "The Phantom", "Sister", "The Emerald", "Judge",
    "The Cosmic", "Marshal",
)
_HERO_NOUN = (
    "Comet", "Falcon", "Anvil", "Tempest", "Lantern", "Viper",
    "Monsoon", "Cipher", "Banner", "Onyx", "Rocket", "Sable",
    "Thunder", "Willow", "Zenith", "Quake", "Harbor", "Mantis",
    "Orbit", "Pyre", "Echo", "Glacier", "Sparrow", "Vortex",
)

_NAME_KINDS = ("character", "band", "startup", "pet",
               "fantasy_place", "superhero")
_NAME_LABELS = {
    "character": "Character", "band": "Band", "startup": "Startup",
    "pet": "Pet", "fantasy_place": "Fantasy place",
    "superhero": "Superhero",
}
_NAME_MIN_COUNT = 1
_NAME_MAX_COUNT = 10


def _combo_count(kind: str) -> int:
    """How many distinct names a kind's banks can produce —
    computed from the bank sizes and the kind's formats."""
    if kind == "character":
        return len(_NAME_FIRST) * len(_NAME_LAST)
    if kind == "band":
        return 2 * len(_BAND_ADJ) * len(_BAND_NOUN)
    if kind == "startup":
        return len(_START_A) * len(_START_B)
    if kind == "pet":
        return len(_PET_NAMES) * (1 + len(_PET_EPITHETS))
    if kind == "fantasy_place":
        return len(_PLACE_PRE) * len(_PLACE_SUF)
    if kind == "superhero":
        return len(_HERO_PRE) * len(_HERO_NOUN)
    return 0


def _gen_name(kind: str, rng: random.Random) -> str:
    """One name of a kind, drawn from its banks."""
    if kind == "character":
        return f"{rng.choice(_NAME_FIRST)} {rng.choice(_NAME_LAST)}"
    if kind == "band":
        adj, noun = rng.choice(_BAND_ADJ), rng.choice(_BAND_NOUN)
        return f"The {adj} {noun}" if rng.random() < 0.5 \
            else f"{adj} {noun}"
    if kind == "startup":
        return rng.choice(_START_A) + rng.choice(_START_B)
    if kind == "pet":
        name = rng.choice(_PET_NAMES)
        if rng.random() < 0.55:
            return f"{name} {rng.choice(_PET_EPITHETS)}"
        return name
    if kind == "fantasy_place":
        return rng.choice(_PLACE_PRE) + rng.choice(_PLACE_SUF)
    if kind == "superhero":
        return f"{rng.choice(_HERO_PRE)} {rng.choice(_HERO_NOUN)}"
    return ""


def name_generator(kind: str = "character", count: int = 5) -> str:
    """Deal `count` fresh names of a kind from the banks."""
    kind = (kind or "character").strip().lower()
    if kind not in _NAME_KINDS:
        return (f"I don't generate '{kind}' names — kinds are "
                "character, band, startup, pet, fantasy_place "
                "and superhero.")
    try:
        count = int(count)
    except (TypeError, ValueError):
        return f"'{count}' isn't a whole number of names."
    if not (_NAME_MIN_COUNT <= count <= _NAME_MAX_COUNT):
        return (f"I deal {_NAME_MIN_COUNT}–{_NAME_MAX_COUNT} "
                f"names at a time — {count} is outside that.")
    rng = random.Random()
    names = []
    attempts = 0
    while len(names) < count and attempts < count * 12:
        candidate = _gen_name(kind, rng)
        if candidate and candidate not in names:
            names.append(candidate)
        attempts += 1
    lines = [f"{_NAME_LABELS[kind]} names:"]
    lines += [f"- {n}" for n in names]
    return "\n".join(lines)
