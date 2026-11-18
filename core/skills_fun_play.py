import difflib
import hashlib
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from .memory import episodes  # Phase 3 M1b: moved to core/memory/
from .config import DATA_DIR
from .skills_creative import _SAMPLE_RATE, _render_melody
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_fun_helpers import (
    _fact_json,
    _play_float_samples,
    _save_fact_json,
)


# ---------------------------------------------------------------------------
# MUSIC TRIVIA — guess the traditional tune
# ---------------------------------------------------------------------------

# Traditional / public-domain melodies only, as (title, aliases,
# [(midi, beats), ...]) — the first phrase of each, at a steady
# beat. MIDI: C4=60, D4=62, E4=64, F4=65, G4=67, A4=69, B4=71.
_TUNES = (
    ("Twinkle Twinkle Little Star", ("twinkle twinkle",),
     [(60, 1), (60, 1), (67, 1), (67, 1), (69, 1), (69, 1), (67, 2),
      (65, 1), (65, 1), (64, 1), (64, 1), (62, 1), (62, 1), (60, 2)]),
    ("Mary Had a Little Lamb", ("mary had a little lamb",),
     [(64, 1), (62, 1), (60, 1), (62, 1), (64, 1), (64, 1), (64, 2),
      (62, 1), (62, 1), (62, 2), (64, 1), (67, 1), (67, 2)]),
    ("Ode to Joy", ("ode to joy", "beethoven's ninth"),
     [(64, 1), (64, 1), (65, 1), (67, 1), (67, 1), (65, 1), (64, 1),
      (62, 1), (60, 1), (60, 1), (62, 1), (64, 1), (64, 1.5),
      (62, 0.5), (62, 2)]),
    ("Happy Birthday", ("happy birthday to you",),
     [(67, 0.75), (67, 0.25), (69, 1), (67, 1), (72, 1), (71, 2),
      (67, 0.75), (67, 0.25), (69, 1), (67, 1), (74, 1), (72, 2)]),
    ("Jingle Bells", ("jingle bells", "jingle all the way"),
     [(64, 1), (64, 1), (64, 2), (64, 1), (64, 1), (64, 2), (64, 1),
      (67, 1), (60, 1.5), (62, 0.5), (64, 4)]),
    ("Yankee Doodle", ("yankee doodle",),
     [(60, 1), (60, 1), (62, 1), (64, 1), (60, 1), (64, 1), (62, 2),
      (60, 1), (60, 1), (62, 1), (64, 1), (60, 2), (59, 2)]),
    ("London Bridge Is Falling Down", ("london bridge",),
     [(67, 1.5), (69, 0.5), (67, 1), (65, 1), (64, 1), (65, 1),
      (67, 2), (62, 1), (64, 1), (65, 2)]),
    ("Row, Row, Row Your Boat", ("row row row your boat", "row your boat"),
     [(60, 1), (60, 0.75), (60, 0.75), (62, 0.5), (64, 2),
      (64, 0.75), (62, 0.75), (64, 0.75), (65, 0.75), (67, 2)]),
    ("Hot Cross Buns", ("hot cross buns",),
     [(64, 1.5), (62, 0.5), (60, 2), (64, 1.5), (62, 0.5), (60, 2),
      (60, 0.5), (60, 0.5), (60, 0.5), (60, 0.5), (62, 0.5),
      (62, 0.5), (62, 0.5), (62, 0.5), (64, 1.5), (62, 0.5), (60, 2)]),
    ("Frère Jacques", ("frere jacques", "brother john"),
     [(60, 1), (62, 1), (64, 1), (60, 1), (60, 1), (62, 1), (64, 1),
      (60, 1), (64, 1), (65, 1), (67, 2)]),
    ("Amazing Grace", ("amazing grace",),
     [(55, 1), (60, 1.5), (64, 0.5), (60, 1.5), (64, 0.5), (62, 1),
      (60, 1), (57, 1), (55, 2)]),
    ("Auld Lang Syne", ("auld lang syne",),
     [(60, 1), (65, 1.5), (65, 0.5), (65, 1), (69, 1), (67, 1.5),
      (65, 0.5), (67, 1), (69, 1), (72, 2)]),
    ("Old MacDonald Had a Farm", ("old macdonald", "old mcdonald"),
     [(60, 1), (60, 1), (60, 1), (55, 1), (57, 1), (57, 1), (55, 2),
      (64, 1), (64, 1), (62, 1), (62, 1), (60, 2)]),
)

_TUNE_BEAT_SECONDS = 0.38
_NOTE_NAMES = ("C", "C sharp", "D", "D sharp", "E", "F", "F sharp",
               "G", "G sharp", "A", "A sharp", "B")


def _render_tune(notes) -> tuple:
    """Render a bank tune [(midi, beats), ...] to (float32
    samples, rate) via skills_creative's melody renderer."""
    seq = [(midi, beats * _TUNE_BEAT_SECONDS) for midi, beats in notes]
    total = sum(dur for _midi, dur in seq)
    pcm = _render_melody(seq, total)
    import numpy as np

    return np.asarray(pcm, dtype=np.float32) / 32768.0, _SAMPLE_RATE


def _play_tune(notes) -> bool:
    """Render and play a tune; True when it actually played."""
    try:
        samples, rate = _render_tune(notes)
        _play_float_samples(samples, rate)
        return True
    except Exception:  # noqa: BLE001 — any failure = the text fallback
        return False


def _note_names(notes, limit: int = 12) -> str:
    return ", ".join(_NOTE_NAMES[midi % 12] for midi, _b in notes[:limit])


def _norm_title(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned.startswith("the "):
        cleaned = cleaned[4:]
    return cleaned


def _title_matches(guess: str, title: str, aliases) -> bool:
    """Fuzzy title check: exact, containment, shared-word subset,
    or a strong difflib ratio all count as naming the tune."""
    g = _norm_title(guess)
    if not g:
        return False
    for candidate in (title,) + tuple(aliases):
        n = _norm_title(candidate)
        if not n:
            continue
        if g == n or g in n or n in g:
            return True
        if difflib.SequenceMatcher(None, g, n).ratio() >= 0.72:
            return True
        words = set(g.split())
        if words and words <= set(n.split()):
            return True
    return False


def music_trivia(action: str = "start", answer: str = "") -> str:
    """Guess-the-tune: 'start' plays a traditional melody,
    'answer' checks a guess and keeps the score in memory."""
    action = (action or "start").strip().lower()
    if action not in ("start", "answer"):
        return ("Music trivia actions are 'start' (play a tune) "
                "and 'answer' (guess the one that's playing).")
    score = _fact_json("fun", "music_trivia_score",
                       {"right": 0, "wrong": 0})
    right = int(score.get("right", 0))
    wrong = int(score.get("wrong", 0))
    if action == "answer":
        current = _fact_json("fun", "music_trivia_current", {})
        title = str(current.get("title", "")).strip()
        if not title:
            return ("No tune is waiting for a guess — start one "
                    "with music_trivia(action='start').")
        guess = (answer or "").strip()
        if not guess:
            return ("Give me your guess as the answer — like "
                    "music_trivia(action='answer', answer="
                    "'Twinkle Twinkle Little Star').")
        tune = next((t for t in _TUNES if t[0] == title), None)
        aliases = tune[1] if tune else ()
        if _title_matches(guess, title, aliases):
            right += 1
            _save_fact_json("fun", "music_trivia_score",
                            {"right": right, "wrong": wrong})
            _save_fact_json("fun", "music_trivia_current", {})
            return (f"That's right — it was '{title}'. Score so "
                    f"far: {right} right, {wrong} wrong. Start "
                    "another whenever you like.")
        wrong += 1
        _save_fact_json("fun", "music_trivia_score",
                        {"right": right, "wrong": wrong})
        return (f"Not '{guess}' — that's a miss (score: {right} "
                f"right, {wrong} wrong). The tune is still open: "
                "guess again, or start a fresh one.")
    # start: pick the next tune (day + attempts keeps it rotating)
    current = _fact_json("fun", "music_trivia_current", {})
    replacing = str(current.get("title", "")).strip()
    attempts = right + wrong
    tune = _TUNES[(date.today().toordinal() + attempts) % len(_TUNES)]
    title, _aliases, notes = tune
    _save_fact_json("fun", "music_trivia_current", {"title": title})
    lead = (f"Replacing the tune you hadn't guessed yet. "
            if replacing else "")
    score_line = (f" Score so far: {right} right, {wrong} wrong."
                  if attempts else "")
    if _play_tune(notes):
        return (f"{lead}Playing a tune now — the first phrase of "
                f"a very well-known traditional song. What is it? "
                f"Answer with music_trivia(action='answer', "
                f"answer='...').{score_line}")
    return (f"{lead}I couldn't play the tune aloud on this "
            f"machine, so here it is in note names instead: "
            f"{_note_names(notes)} — the first phrase of a very "
            f"well-known traditional song. What is it? Answer with "
            f"music_trivia(action='answer', answer='...')."
            f"{score_line}")



# ---------------------------------------------------------------------------
# STORY CHAIN — a paragraph at a time, saved beside the day journal
# ---------------------------------------------------------------------------

_STORY_READ_CAP = 4000
_STORY_PARA_CAP = 1200

_HEROES = ("Mira", "Tomas", "Ada", "Nils", "Priya", "Old Fenwick")
_OBJECTS = ("brass key", "wooden compass", "sealed jar",
            "pocket watch", "glass marble")
_PLACES = ("Lantern Lane", "the old pier", "Hollow Street",
           "the harbour steps", "Miller's Row")

_OPENINGS = (
    "The morning {hero} found the {object} on the doorstep, it was "
    "still warm — and it was ticking, very softly, like it was "
    "trying not to be noticed.",
    "{hero} had kept the little shop on {place} for eleven years, "
    "and in all that time the bell above the door had never once "
    "rung by itself. That Tuesday, it did.",
    "The letter arrived forty years late, addressed simply to "
    "'{hero}, when you are ready.' The postmark showed a town "
    "that no longer existed.",
    "Everyone in the village agreed that {place} was quite empty. "
    "{hero}, who swept its steps each dawn, knew better — someone "
    "had been leaving the gate unlatched.",
    "When the tide went out farther than anyone could remember, "
    "{hero} walked down to see what the sea had been hiding, "
    "carrying only a lantern and a stubborn curiosity.",
)

_CONTINUATIONS = (
    "{hero} turned the thought of the {keyword} over and over, "
    "the way you turn a strange coin in your palm, and at last "
    "nodded slowly. 'Then we start there,' {hero} said.",
    "It was then that the {keyword} caught {hero}'s eye again — "
    "small, ordinary, and suddenly the most important thing in "
    "the room.",
    "'{Keyword},' {hero} murmured. 'Of course. It was never about "
    "anything else.' Outside, the light was beginning to change.",
    "By the time the clocks struck the hour, {hero} had made a "
    "decision about the {keyword}, and there was no talking them "
    "out of it.",
    "The {keyword} would have to wait — first there was the "
    "matter of the door, which had just opened on its own for "
    "the second time that day.",
    "{hero} wrote the word '{keyword}' in the little notebook, "
    "underlined it twice, and blew out the lamp. Tomorrow, "
    "everything would be clearer. Or stranger.",
    "Somewhere behind {hero}, a floorboard creaked. '{Keyword} "
    "first,' came a voice from the shadows. 'Then we'll talk.'",
    "And so, with the {keyword} safely pocketed and the sky going "
    "the colour of embers, {hero} set off — whistling, which "
    "surprised them both.",
)

_KEYWORD_FALLBACKS = ("letter", "lantern", "key", "tide", "door")
_KEYWORD_STOPWORDS = frozenset({
    "about", "above", "after", "again", "along", "because",
    "before", "behind", "could", "every", "first", "found",
    "going", "great", "house", "never", "night", "other",
    "place", "right", "round", "small", "still", "their",
    "there", "these", "thing", "think", "those", "three",
    "through", "under", "until", "water", "where", "which",
    "while", "world", "would", "young",
})


def _stories_dir() -> Path:
    """Where the dated story files live (monkeypatchable)."""
    return Path(DATA_DIR) / "stories"


def _story_path(when: date | None = None) -> Path:
    day = when or date.today()
    return _stories_dir() / f"story-{day.isoformat()}.txt"


def _story_hero(when: date | None = None) -> str:
    """The story's hero, fixed for the day so every paragraph
    of a day's story is about the same person."""
    day = when or date.today()
    return _HEROES[day.toordinal() % len(_HEROES)]


def _read_story(path: Path) -> list:
    """Parse a story file into [(speaker, paragraph), ...]."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    blocks = []
    for chunk in raw.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("VYUK:"):
            blocks.append(("VYUK", chunk[len("VYUK:"):].strip()))
        elif chunk.startswith("You:"):
            blocks.append(("You", chunk[len("You:"):].strip()))
        else:
            blocks.append(("VYUK", chunk))
    return [(who, text) for who, text in blocks if text]


def _append_block(path: Path, who: str, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        label = "VYUK" if who == "VYUK" else "You"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{label}: {text}\n\n")
        return True
    except OSError:
        return False


def _story_opening(when: date | None = None) -> str:
    day = when or date.today()
    rng = random.Random(day.toordinal() * 31 + 7)
    template = _OPENINGS[day.toordinal() % len(_OPENINGS)]
    return template.format(hero=_story_hero(day),
                           object=rng.choice(_OBJECTS),
                           place=rng.choice(_PLACES))


def _story_keyword(user_text: str, story_so_far: str) -> str:
    """A notable word from the user's paragraph to weave into
    VYUK's reply — the longest content word, else a seeded
    fallback. Pure."""
    words = re.findall(r"[A-Za-z']+", user_text or "")
    candidates = [w.lower() for w in words
                  if len(w) >= 5 and w.lower() not in _KEYWORD_STOPWORDS]
    if candidates:
        return max(candidates, key=len)
    seed = int(hashlib.sha256((story_so_far or "").encode("utf-8"))
               .hexdigest(), 16)
    return _KEYWORD_FALLBACKS[seed % len(_KEYWORD_FALLBACKS)]


def _story_continuation(user_text: str, story_so_far: str,
                        when: date | None = None) -> str:
    keyword = _story_keyword(user_text, story_so_far)
    seed = int(hashlib.sha256(
        (story_so_far + "|" + user_text).encode("utf-8")).hexdigest(), 16)
    template = _CONTINUATIONS[seed % len(_CONTINUATIONS)]
    return template.format(hero=_story_hero(when), keyword=keyword,
                           Keyword=keyword.capitalize())


def story_chain(action: str = "start", text: str = "") -> str:
    """Build today's story a paragraph at a time; saved as a
    dated text file in the data folder, beside the journal."""
    action = (action or "start").strip().lower()
    if action not in ("start", "continue", "read"):
        return ("Story actions are 'start' (open today's story), "
                "'continue' (add your paragraph, passed as text) "
                "and 'read' (hear the story so far).")
    path = _story_path()
    blocks = _read_story(path)
    if action == "read":
        if not blocks:
            return ("There's no story yet today — start one with "
                    "story_chain(action='start').")
        full = "\n\n".join(f"{who}: {para}" for who, para in blocks)
        if len(full) > _STORY_READ_CAP:
            full = (full[:_STORY_READ_CAP]
                    + f"\n[story truncated: showing {_STORY_READ_CAP} "
                      f"of {len(full)} characters]")
        return (f"Today's story so far ({len(blocks)} paragraph(s)):"
                f"\n\n{full}")
    if action == "start":
        if blocks:
            return (f"Today's story is already under way — "
                    f"{len(blocks)} paragraph(s) in, with "
                    f"{_story_hero()} in the thick of it. Add yours "
                    f"with story_chain(action='continue', text="
                    f"'...'), or hear it with action='read'.")
        opening = _story_opening()
        if not _append_block(path, "VYUK", opening):
            return (f"I couldn't save the story to {path} — the "
                    "folder may not be writable.")
        try:
            episodes.record(
                "action",
                f"Started a story chain — {_story_hero()}'s story.")
        except Exception:  # noqa: BLE001 — journal note is a bonus
            pass
        return (f"Here's the opening of today's story:\n\n{opening}"
                f"\n\nNow you: continue it with story_chain"
                f"(action='continue', text='your paragraph').")
    # continue
    mine = (text or "").strip()
    if not mine:
        return ("Tell me your paragraph as the text — like "
                "story_chain(action='continue', text='The door "
                "opened onto a staircase going down.').")
    if len(mine) > _STORY_PARA_CAP:
        mine = mine[:_STORY_PARA_CAP]
        trimmed = (f" (I trimmed it to {_STORY_PARA_CAP} "
                   "characters.)")
    else:
        trimmed = ""
    started_now = ""
    if not blocks:
        opening = _story_opening()
        if not _append_block(path, "VYUK", opening):
            return (f"I couldn't save the story to {path} — the "
                    "folder may not be writable.")
        blocks = [("VYUK", opening)]
        started_now = ("There was no story yet today, so I've "
                       "opened one first.\n\n")
    if not _append_block(path, "You", mine):
        return (f"I couldn't save your paragraph to {path} — the "
                "folder may not be writable.")
    so_far = "\n\n".join(f"{who}: {para}" for who, para in blocks)
    reply = _story_continuation(mine, so_far)
    if not _append_block(path, "VYUK", reply):
        return (f"I couldn't save my paragraph to {path} — the "
                "folder may not be writable.")
    total = len(blocks) + 2
    return (f"{started_now}Added yours{trimmed} — and here's mine:"
            f"\n\n{reply}\n\n(The story now has {total} "
            f"paragraph(s). Your turn whenever you're ready.)")
