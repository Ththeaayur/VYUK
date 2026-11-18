"""Study skills impl — shared seam/state helpers (split from skills_study.py).

Holds the persistence seam and state helpers used by both the flashcards
and drills groups (plan §2 P3(c) helpers refinement).
"""


import json


# ---------------------------------------------------------------------------
# The seam — persistence lives behind this one function
# ---------------------------------------------------------------------------

_CARDS_CATEGORY = "flashcards"
_STATE_CATEGORY = "flashcard_state"
_MAX_FRONT_CHARS = 120
_MAX_BACK_CHARS = 300


def _store():
    """The memory store singleton — this pack's only persistence
    seam. Tests substitute a temp MemoryStore by replacing this
    function; handlers never touch the singleton directly."""
    from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    return memory_store


def _cards() -> list:
    """Every flashcard as a {front, back} dict, sorted by front
    so deck order (and every seeded pick over it) is stable."""
    found = []
    for fact in _store().get_all_facts():
        if fact.get("category") != _CARDS_CATEGORY:
            continue
        front = str(fact.get("key") or "").strip()
        back = str(fact.get("value") or "").strip()
        if front:
            found.append({"front": front, "back": back})
    found.sort(key=lambda c: c["front"].lower())
    return found


def _state_get(key: str) -> str:
    fact = _store().get_fact(_STATE_CATEGORY, key)
    if fact is None:
        return ""
    return str(fact.get("value") or "")


def _state_set(key: str, value: str) -> None:
    _store().save_fact(_STATE_CATEGORY, key, value)


def _state_clear(key: str) -> None:
    _state_set(key, "")


def _count(key: str) -> int:
    try:
        return max(0, int(_state_get(key) or 0))
    except ValueError:
        return 0


def _bump(key: str) -> int:
    value = _count(key) + 1
    _state_set(key, str(value))
    return value


def _mastered() -> list:
    try:
        parsed = json.loads(_state_get("mastered") or "[]")
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(front) for front in parsed]


def _save_mastered(fronts: list) -> None:
    _state_set("mastered", json.dumps(fronts))


def _answers_total() -> int:
    return _count("right") + _count("wrong")


def _grade(correct: bool) -> None:
    _bump("right" if correct else "wrong")
