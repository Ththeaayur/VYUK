"""
FUN skills III for VYUK — a second play pack: a riddle
game with a rotating bank, rock-paper-scissors with a running
score, Mad Libs from built-in templates, and a fortune cookie
that gives the same fortune all day.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise — an exception
becomes an honest "I couldn't…" line; caps and fallbacks are
disclosed in the result. All game state (the active riddle,
the rotation pointer, the scores) lives in memory facts under
the "fun" category, read and written only through the
module-level _store() seam so tests can substitute a temporary
store and never touch the real database. The fortune is picked
by date ordinal — the same all day, a new one tomorrow, the
same trick word_of_the_day uses in skills_fun.

Nothing here invents facts about the user: scores are counted
from games actually played, the riddle bank and the fortunes
are built in, and nothing is fetched.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import json
import random
import re
from datetime import date


# ---------------------------------------------------------------------------
# Small shared helpers — facts through the _store() seam
# ---------------------------------------------------------------------------


def _store():
    """The memory store seam: the product's shared store in
    real runs; tests substitute a temporary store by replacing
    this function, so game state never lands in the real DB."""
    from voice_assistant.core.memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py

    return memory_store


def _fact_json(key: str, default: dict) -> dict:
    """A 'fun' memory fact holding a JSON object, or a copy of
    default."""
    try:
        fact = _store().get_fact("fun", key)
        if fact and fact.get("value"):
            data = json.loads(fact["value"])
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001 — memory is best-effort here
        pass
    return dict(default)


def _save_fact_json(key: str, data: dict) -> None:
    try:
        _store().save_fact("fun", key, json.dumps(data))
    except Exception:  # noqa: BLE001 — the game must not crash on it
        pass


def _fact_int(key: str, default: int = 0) -> int:
    try:
        fact = _store().get_fact("fun", key)
        if fact and fact.get("value"):
            return int(fact["value"])
    except Exception:  # noqa: BLE001
        pass
    return default


def _save_fact_int(key: str, value: int) -> None:
    try:
        _store().save_fact("fun", key, str(int(value)))
    except Exception:  # noqa: BLE001
        pass


def _score_line(right: int, wrong: int) -> str:
    if right or wrong:
        return f" Score so far: {right} right, {wrong} wrong."
    return ""


# ---------------------------------------------------------------------------
# RIDDLE TIME — a rotating bank of classics
# ---------------------------------------------------------------------------

# (question, answer as spoken, key noun the guess is checked
# against). All stock classic riddles.
_RIDDLES = (
    ("I speak without a mouth and hear without ears. I have no "
     "body, but I come alive with wind. What am I?",
     "an echo", "echo"),
    ("The more of me you take, the more you leave behind. "
     "What am I?",
     "footsteps", "footstep"),
    ("What has keys but can't open a single lock?",
     "a piano", "piano"),
    ("What gets wetter the more it dries?",
     "a towel", "towel"),
    ("What has a head and a tail but no body?",
     "a coin", "coin"),
    ("What runs but never walks, has a mouth but never talks, "
     "has a bed but never sleeps?",
     "a river", "river"),
    ("What has many teeth but can't bite?",
     "a comb", "comb"),
    ("The person who makes me has no need of me; the person who "
     "buys me has no use for me; the person who uses me can "
     "neither see nor feel me. What am I?",
     "a coffin", "coffin"),
    ("What has one eye but can't see?",
     "a needle", "needle"),
    ("What belongs to you, but everyone else uses it more than "
     "you do?",
     "your name", "name"),
    ("What can you catch, but never throw?",
     "a cold", "cold"),
    ("What has a face and two hands but no arms or legs?",
     "a clock", "clock"),
    ("What gets bigger the more you take away from it?",
     "a hole", "hole"),
    ("I'm tall when I'm young and short when I'm old. "
     "What am I?",
     "a candle", "candle"),
)


def _norm_guess(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _riddle_matches(guess: str, key: str) -> bool:
    """Lenient check: the key noun, normalised, appears in the
    normalised guess — 'footsteps' answers the footstep riddle,
    'it's an echo!' answers the echo one."""
    g = _norm_guess(guess)
    k = _norm_guess(key)
    if not g or not k:
        return False
    return k in g


def riddle_time(action: str = "ask", answer: str = "") -> str:
    """A riddle game over memory facts: 'ask' serves the next
    riddle in the rotation, 'answer' checks a guess leniently,
    'reveal' gives the answer away and moves on."""
    action = (action or "ask").strip().lower()
    if action not in ("ask", "answer", "reveal"):
        return ("Riddle actions are 'ask' (serve a riddle), "
                "'answer' (guess it) and 'reveal' (give up and "
                "hear the answer).")
    score = _fact_json("riddle_score", {"right": 0, "wrong": 0})
    right = int(score.get("right", 0))
    wrong = int(score.get("wrong", 0))
    current = _fact_json("riddle_current", {})
    has_current = "index" in current
    if action == "ask":
        idx = _fact_int("riddle_index", 0) % len(_RIDDLES)
        question, _display, _key = _RIDDLES[idx]
        _save_fact_json("riddle_current", {"index": idx})
        _save_fact_int("riddle_index", (idx + 1) % len(_RIDDLES))
        lead = ("Skipping the one you hadn't answered — "
                if has_current else "")
        return (f"{lead}Here's your riddle: {question} Guess "
                f"with riddle_time(action='answer', answer="
                f"'...'), or give up with action='reveal'."
                f"{_score_line(right, wrong)}")
    # answer / reveal both need an active riddle
    if not has_current:
        return ("No riddle is waiting — ask for one first with "
                "riddle_time(action='ask').")
    idx = int(current.get("index", 0)) % len(_RIDDLES)
    _question, display, key = _RIDDLES[idx]
    if action == "reveal":
        _save_fact_json("riddle_current", {})
        return (f"The answer was {display}. No points either way "
                "for a reveal — ask for a fresh riddle whenever "
                "you're ready.")
    guess = (answer or "").strip()
    if not guess:
        return ("Give me your guess as the answer — like "
                "riddle_time(action='answer', answer='an echo').")
    if _riddle_matches(guess, key):
        right += 1
        _save_fact_json("riddle_score",
                        {"right": right, "wrong": wrong})
        _save_fact_json("riddle_current", {})
        return (f"That's it — {display}! Score so far: {right} "
                f"right, {wrong} wrong. Ask for another whenever "
                "you like.")
    wrong += 1
    _save_fact_json("riddle_score", {"right": right, "wrong": wrong})
    return (f"Not '{guess}' — that's a miss (score: {right} "
            f"right, {wrong} wrong). The riddle is still open: "
            "guess again, ask for a fresh one, or reveal the "
            "answer.")


# ---------------------------------------------------------------------------
# ROCK PAPER SCISSORS — a fair pick, a kept score
# ---------------------------------------------------------------------------

_BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
_THROW_VERBS = {
    ("rock", "scissors"): "crushes",
    ("scissors", "paper"): "cut",
    ("paper", "rock"): "covers",
}


def rock_paper_scissors(choice: str = "") -> str:
    """One throw against VYUK's fair random pick; the
    running score (yours, hers, draws) is kept in memory."""
    pick = (choice or "").strip().lower()
    if pick == "scissor":
        pick = "scissors"
    if pick not in _BEATS:
        return ("Rock, paper or scissors — those are the three "
                "options. Throw one: rock_paper_scissors(choice="
                "'rock').")
    hers = random.choice(tuple(_BEATS))
    score = _fact_json("rps_score",
                       {"wins": 0, "losses": 0, "draws": 0})
    wins = int(score.get("wins", 0))
    losses = int(score.get("losses", 0))
    draws = int(score.get("draws", 0))
    if hers == pick:
        draws += 1
        outcome = f"We both threw {pick} — a draw."
    elif _BEATS[pick] == hers:
        wins += 1
        outcome = (f"{pick.capitalize()} "
                   f"{_THROW_VERBS[(pick, hers)]} {hers} — "
                   "you win this one!")
    else:
        losses += 1
        outcome = (f"{hers.capitalize()} "
                   f"{_THROW_VERBS[(hers, pick)]} {pick} — "
                   "I win this one.")
    _save_fact_json("rps_score",
                    {"wins": wins, "losses": losses, "draws": draws})
    return (f"You threw {pick}; I threw {hers}. {outcome} Score "
            f"so far: {wins} win(s) for you, {losses} for me, "
            f"{draws} draw(s).")


# ---------------------------------------------------------------------------
# MAD LIBS — three templates, words supplied in order
# ---------------------------------------------------------------------------

# (title, template with {0}.. slots, slot labels in order)
_MAD_LIBS = (
    ("The Great Escape",
     "One morning, a {0} {1} woke up in {2} and decided today "
     "was the day. It spent the whole morning {3}, stopped only "
     "for a bite of {4}, and shouted '{5}!' at the sky.",
     ("adjective", "animal", "place", "verb ending in -ing",
      "food", "exclamation")),
    ("The Brave Cook",
     "{0}, the bravest cook in town, picked up a {1} {2}, {3} "
     "it twice for luck, and served it proudly at {4}.",
     ("a name", "adjective", "object", "verb (past tense)",
      "place")),
    ("The Road Trip",
     "The {0} bought a {1} {2} and went {3} down the coast "
     "road, munching {4} and counting all the way to {5} just "
     "for fun.",
     ("animal", "adjective", "vehicle", "verb ending in -ing",
      "snack", "number")),
)


def mad_libs(words: str = "", story: int = 0) -> str:
    """Mad Libs in two steps: with no words, list the word types
    a story needs, in order; with the words comma-separated,
    read the finished story back."""
    try:
        idx = int(story)
    except (TypeError, ValueError):
        return "Stories are numbered 0, 1 and 2 — pick one of those."
    if idx < 0 or idx >= len(_MAD_LIBS):
        return "Stories are numbered 0, 1 and 2 — pick one of those."
    title, template, slots = _MAD_LIBS[idx]
    if not (words or "").strip():
        numbered = "; ".join(f"{n + 1}. {slot}"
                             for n, slot in enumerate(slots))
        blanked = re.sub(r"\{\d+\}", "___", template)
        return (f"Mad Libs story {idx}, '{title}'. I need "
                f"{len(slots)} words, in this order: {numbered}. "
                f"Give them back comma-separated: mad_libs(words="
                f"'...', story={idx}). The story goes: {blanked}")
    given = [w.strip() for w in str(words).split(",")]
    if len(given) != len(slots):
        return (f"Story {idx} ('{title}') needs exactly "
                f"{len(slots)} words — {'; '.join(slots)} — but "
                f"you gave me {len(given)}. Try again with "
                f"{len(slots)}, comma-separated.")
    filled = template.format(*given)
    return f"Here's your story, '{title}':\n\n{filled}"


# ---------------------------------------------------------------------------
# FORTUNE COOKIE — a built-in bank, the same fortune all day
# ---------------------------------------------------------------------------

_FORTUNES = (
    "A pleasant surprise is waiting for you around the next corner.",
    "The project you've been putting off will go better than you fear.",
    "Someone you helped long ago is about to return the favour.",
    "Good news will arrive before the week is out — check your messages.",
    "Your patience in a small matter will pay off in a large one.",
    "An old friend will reappear with an interesting offer.",
    "The answer you need is in the book you keep meaning to open.",
    "A small risk taken today beats a large regret tomorrow.",
    "You will find the lost thing in the last place you'd think to look.",
    "Kindness you give away this week comes back doubled.",
    "The hard conversation will be shorter than the worrying about it.",
    "A journey soon: short in distance, long in stories.",
    "Your next idea is better than you think — write it down before bed.",
    "Someone is quietly grateful for something you did. You'll hear about it.",
    "Say yes to the invitation you'd normally decline.",
    "Money saved on a small thing funds a bigger joy.",
    "A stranger's offhand remark will solve a problem you've been chewing on.",
    "Finish the nearly-done thing first. The rest unlocks after.",
)


def fortune_cookie() -> str:
    """Today's fortune from the built-in bank — picked by date
    ordinal, so it's the same all day and new tomorrow."""
    pick = _FORTUNES[date.today().toordinal() % len(_FORTUNES)]
    return (f"Today's fortune: {pick} (From my built-in fortune "
            "bank — the same one all day, a new one tomorrow.)")


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Fun III pack tool on a ToolRegistry-like object."""
    registry.register(
        name="riddle_time",
        description="Play a riddle game: 'ask' serves a classic riddle. 'answer' checks your guess (leniently — the key word is enough) and 'reveal' gives the answer away and moves on. The bank of 14 classics rotates so you don't get the same riddle twice in a row, and your score is kept in memory. Use for a quick brain-teaser.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["ask", "answer", "reveal"], "description": "'ask' (serve a riddle), 'answer' (guess it) or 'reveal' (give up)"},
                "answer": {"type": "string", "description": "Your guess at the riddle's answer (for action 'answer')"},
            },
            "required": ["action"],
        },
        handler=riddle_time,
    )
    registry.register(
        name="rock_paper_scissors",
        description="Play rock-paper-scissors against VYUK She picks fairly at random, the result names both throws and who won, and the running score — your wins, her wins, draws — is kept in memory. Use for a quick game or to settle a tiny decision the fun way.",
        parameters={
            "type": "object",
            "properties": {
                "choice": {"type": "string", "enum": ["rock", "paper", "scissors"], "description": "Your throw: 'rock', 'paper' or 'scissors'"},
            },
            "required": ["choice"],
        },
        handler=rock_paper_scissors,
    )
    registry.register(
        name="mad_libs",
        description="Fill in a silly Mad Libs story from three built-in templates. Call it with just a story number and she lists the words she needs, in order (an adjective, an animal, a place…); call it again with those words comma-separated and she reads the finished story back. Use for a quick laugh.",
        parameters={
            "type": "object",
            "properties": {
                "words": {"type": "string", "description": "The words for the story, comma-separated, in the order she asked for them"},
                "story": {"type": "integer", "description": "Which story: 0, 1 or 2 (default 0)"},
            },
            "required": [],
        },
        handler=mad_libs,
    )
    registry.register(
        name="fortune_cookie",
        description="Open today's fortune cookie: a short fortune from a built-in bank. The same fortune all day, a new one tomorrow — no two people consulting her on different days get the same run of luck. Use for a small daily ritual or a tie-breaking bit of whimsy.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=fortune_cookie,
    )
