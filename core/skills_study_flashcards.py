"""Study skills impl — flashcards group (split from skills_study.py).
"""


import datetime
import random

# INTENTIONAL: cross-part names — flashcards resolve these bare names in
# the helpers module's globals (bare-name rule, plan §2 P3), not the facade's.
from .skills_study_helpers import (
    _CARDS_CATEGORY,
    _MAX_BACK_CHARS,
    _MAX_FRONT_CHARS,
    _answers_total,
    _cards,
    _count,
    _grade,
    _mastered,
    _save_mastered,
    _state_clear,
    _state_get,
    _state_set,
    _store,
)


# ---------------------------------------------------------------------------
# FLASHCARD ADD — fronts are fact keys, backs are fact values
# ---------------------------------------------------------------------------


def flashcard_add(front: str = "", back: str = "") -> str:
    """Save a flashcard, or update the back of an existing one."""
    clean_front = " ".join(str(front or "").split())
    clean_back = " ".join(str(back or "").split())
    if not clean_front or not clean_back:
        return ("A flashcard needs both sides — a front (the "
                "question or prompt) and a back (the answer). "
                "Nothing was saved.")
    if len(clean_front) > _MAX_FRONT_CHARS:
        return (f"That front is {len(clean_front)} characters — "
                f"flashcard fronts are capped at "
                f"{_MAX_FRONT_CHARS}. Shorten it and I'll save "
                "the card.")
    if len(clean_back) > _MAX_BACK_CHARS:
        return (f"That back is {len(clean_back)} characters — "
                f"flashcard backs are capped at "
                f"{_MAX_BACK_CHARS}. Shorten it and I'll save "
                "the card.")
    try:
        existed = (_store().get_fact(_CARDS_CATEGORY, clean_front)
                   is not None)
        _store().save_fact(_CARDS_CATEGORY, clean_front, clean_back)
        total = len(_cards())
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the flashcard: {e}"
    if existed:
        return (f"Updated the flashcard '{clean_front}' — its "
                f"back now reads '{clean_back}'. You have "
                f"{total} flashcard{'s' if total != 1 else ''} "
                "saved.")
    return (f"Saved the flashcard '{clean_front}' → "
            f"'{clean_back}'. You have {total} "
            f"flashcard{'s' if total != 1 else ''} saved — "
            "flashcard_review will quiz you on them.")


# ---------------------------------------------------------------------------
# REVIEW + ANSWER — the study loop, shared with quiz and bee
# ---------------------------------------------------------------------------


def flashcard_review() -> str:
    """Offer one unmastered card's front and stash it as the
    pending review card."""
    try:
        cards = _cards()
        if not cards:
            return ("No flashcards saved yet — add some with "
                    "flashcard_add (a front and a back each), "
                    "then I'll quiz you on them.")
        mastered = _mastered()
        fresh = [c for c in cards
                 if c["front"] not in mastered]
        note = ""
        if not fresh:
            fresh = cards
            _save_mastered([])
            note = ("You've answered every card correctly at "
                    "least once, so the deck starts over. ")
        rng = random.Random(
            f"visor-review|{datetime.date.today().isoformat()}|"
            f"{len(cards)}|{_answers_total()}")
        card = rng.choice(fresh)
        _state_set("current", card["front"])
    except Exception as e:  # noqa: BLE001
        return f"Couldn't start a review: {e}"
    return (f"{note}Flashcard: {card['front']}\n"
            "Think of the answer, then use flashcard_answer "
            "with your guess.")


def flashcard_answer(guess: str = "") -> str:
    """Grade a guess against whatever is pending: a quiz answer
    first, then a spelling-bee word, then the review card."""
    given = " ".join(str(guess or "").split())
    if not given:
        return ("Give me your guess — flashcard_answer with the "
                "answer (or the option letter for a quiz).")
    try:
        quiz_letter = _state_get("quiz_answer").strip()
        if quiz_letter:
            return _answer_quiz(given, quiz_letter)
        bee_word = _state_get("bee_word").strip()
        if bee_word:
            return _answer_bee(given, bee_word)
        current = _state_get("current").strip()
        if current:
            return _answer_review(given, current)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't check that answer: {e}"
    return ("Nothing is waiting for an answer right now. Start "
            "with flashcard_review for a card, quiz_me for a "
            "multiple-choice question, or spelling_bee for a "
            "spelling word.")


def _answer_review(given: str, front: str) -> str:
    card = None
    for candidate in _cards():
        if candidate["front"] == front:
            card = candidate
            break
    _state_clear("current")
    if card is None:
        return (f"The card '{front}' isn't in the deck any "
                "more, so there's nothing to grade against. "
                "flashcard_review will offer another.")
    correct = given.lower() == card["back"].lower()
    _grade(correct)
    if not correct:
        return (f"Not quite — '{front}' has the back "
                f"'{card['back']}' (you said '{given}'). It "
                "stays in the review pile.")
    mastered = _mastered()
    if front not in mastered:
        mastered.append(front)
        _save_mastered(mastered)
    total = len(_cards())
    return (f"Right — '{front}' → '{card['back']}'. That's "
            f"{len(mastered)} of {total} card"
            f"{'s' if total != 1 else ''} answered correctly "
            "in review.")


def _answer_quiz(given: str, letter: str) -> str:
    front = _state_get("quiz_front").strip()
    correct_back = ""
    for candidate in _cards():
        if candidate["front"] == front:
            correct_back = candidate["back"]
            break
    _state_clear("quiz_answer")
    _state_clear("quiz_front")
    said_letter = given.strip().upper()
    correct = (said_letter == letter.upper()
               or (correct_back
                   and given.lower() == correct_back.lower()))
    _grade(correct)
    where = f" for '{front}'" if front else ""
    answer = (f"{letter}" + (f" — '{correct_back}'"
                             if correct_back else ""))
    if correct:
        return f"Right — the answer{where} was {answer}."
    return (f"Not quite — the answer{where} was {answer} "
            f"(you said '{given}').")


def _answer_bee(given: str, word: str) -> str:
    _state_clear("bee_word")
    correct = given.lower() == word.lower()
    _grade(correct)
    if correct:
        return (f"Right — '{word}' is spelled exactly like "
                "that. Nicely done.")
    return (f"Not quite — the word is spelled '{word}' "
            f"(you wrote '{given}').")


# ---------------------------------------------------------------------------
# STATS — the deck and the counters, no more than that
# ---------------------------------------------------------------------------


def flashcard_stats() -> str:
    """Report deck size, answers graded and accuracy."""
    try:
        total = len(_cards())
        mastered = len(_mastered())
        right = _count("right")
        wrong = _count("wrong")
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the study stats: {e}"
    if total == 0:
        return ("No flashcards saved yet — flashcard_add makes "
                "the first one (a front and a back).")
    lines = [f"You have {total} flashcard"
             f"{'s' if total != 1 else ''} saved; {mastered} "
             f"ha{'s' if mastered == 1 else 've'} been answered "
             "correctly in review."]
    graded = right + wrong
    if graded == 0:
        lines.append("No answers graded yet — flashcard_review "
                     "or quiz_me starts the first round.")
    else:
        accuracy = round(100.0 * right / graded)
        lines.append(
            f"Answers so far: {right} right, {wrong} wrong "
            f"— {accuracy}% accuracy across reviews, quizzes "
            "and spelling checks.")
    return " ".join(lines)
