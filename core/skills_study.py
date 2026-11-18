"""
Study skills pack for VYUK — the revision shelf: flashcards
with a review/answer loop, multiple-choice quizzes built from
those same cards, a spelling bee and a word of the day, a
mnemonic maker, a typing test, and a reading-level checker.

Where each tool's truth lives — and what it honestly can't know:

- Flashcards persist as memory facts (core/memory/memory.py) under the
  category `flashcards`: the fact key is the card's front and
  the value is its back, as plain text, so they survive
  restarts and show up in the user's memory like anything else
  VYUK remembers. Re-adding the same front updates its
  back (the fact store upserts on category+key).
- Everything ephemeral about a study session lives in the same
  store under `flashcard_state`: which card is under review
  (`current`), the right/wrong answer counters (`right`,
  `wrong`), the fronts answered correctly in review
  (`mastered`, a JSON list — the review picker only offers
  unmastered cards, and resets the list when the whole deck has
  been through), the pending quiz's correct letter and front
  (`quiz_answer`, `quiz_front`), the spelling-bee word
  (`bee_word`) and the typing test's sentence
  (`typing_sentence`). flashcard_answer checks a pending quiz
  first, then a pending spelling word, then the review card —
  one guess is never graded against two things.
- "Deterministic-random" picks seed a Random on the day and the
  deck, the way skills_smart_convert seeds its generators: the same
  day and deck offer the same card, so a pick never changes
  mid-conversation just because the tool was called twice.
- The spelling and vocabulary lists are small built-in sets
  (30 and 24 words) picked by date ordinal — no network, no
  pretending to be a dictionary beyond the gloss each entry
  carries. reading_level is the standard Flesch formulas over
  a vowel-group syllable count: an estimate, labelled as one.

Same rules as the packs before it: plain functions returning
human-readable strings, never raising; persistence sits behind
one module-level seam so tests can monkeypatch it; stdlib only.
Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

# INTENTIONAL: the old module's namespace carried these stdlib imports;
# the facade re-exports them so the dir diff stays empty both ways
# (files3/files2/productivity5/photo_effects/kitchen/words leaked-import lesson).
import datetime
import difflib
import json
import random
import re

# INTENTIONAL: re-exports of every moved name (hands3 discipline).
from .skills_study_helpers import (
    _CARDS_CATEGORY,
    _MAX_BACK_CHARS,
    _MAX_FRONT_CHARS,
    _STATE_CATEGORY,
    _answers_total,
    _bump,
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
from .skills_study_flashcards import (
    _answer_bee,
    _answer_quiz,
    _answer_review,
    flashcard_add,
    flashcard_answer,
    flashcard_review,
    flashcard_stats,
)
from .skills_study_drills import (
    _BEE_WORDS,
    _MAX_MNEMONIC_ITEMS,
    _MAX_READING_CHARS,
    _MNEMONIC_CONNECTORS,
    _MNEMONIC_WORDS,
    _QUIZ_LETTERS,
    _SENTENCE_SPLIT_RE,
    _TYPING_SENTENCES,
    _VOCAB_WORDS,
    _VOWEL_GROUP_RE,
    _WORD_RE,
    _reading_band,
    _syllables,
    mnemonic_maker,
    quiz_me,
    reading_level,
    spelling_bee,
    typing_test,
    vocab_word,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Study pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="flashcard_add",
        description="Save a flashcard for later revision: a front (the question or prompt) and a back (the answer). Cards persist as memory facts. Adding the same front again updates its back and says so. Use when the user wants to memorise something; flashcard_review and quiz_me then drill them on the deck.",
        parameters={
            "type": "object",
            "properties": {
                "front": {"type": "string", "description": "The front of the card — the question or prompt"},
                "back": {"type": "string", "description": "The back of the card — the answer"},
            },
            "required": ["front", "back"],
        },
        handler=flashcard_add,
    )
    registry.register(
        name="flashcard_review",
        description="Drill one flashcard: shows the front of a card not yet answered correctly (the deck restarts once every card has been), and keeps the card pending. The user then answers with flashcard_answer, which grades the guess and updates the score. Use when the user wants to revise or be tested on their cards.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=flashcard_review,
    )
    registry.register(
        name="flashcard_answer",
        description="Grade the user's answer to whatever study question is pending: a quiz from quiz_me first (answer with the option letter or the full answer), then a spelling word from spelling_bee, then the review card from flashcard_review. Reports right or wrong with the correct answer, and keeps the running score that flashcard_stats shows. Use right after one of those three tools has set a question.",
        parameters={
            "type": "object",
            "properties": {
                "guess": {"type": "string", "description": "The user's answer — the answer text, a quiz option letter, or a spelling"},
            },
            "required": ["guess"],
        },
        handler=flashcard_answer,
    )
    registry.register(
        name="flashcard_stats",
        description="Show the state of the user's studying: how many flashcards are saved, how many have been answered correctly in review, and the running right/wrong answer count with accuracy across reviews, quizzes and spelling checks. Use when the user asks how their revision is going.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=flashcard_stats,
    )
    registry.register(
        name="quiz_me",
        description="Build a four-option multiple-choice question from the user's own flashcards: one card's front, its back plus three other cards' backs as distractors, lettered A to D. Needs at least 4 cards and says how many more if there aren't enough. The user answers with flashcard_answer (the letter or the full answer). Use for a quicker, gentler drill than flashcard_review.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=quiz_me,
    )
    registry.register(
        name="mnemonic_maker",
        description="Make memory aids for a list of items in order: their first letters spelled out, plus three template mnemonic sentences built from a built-in letter-to-word map. Rule-based only — the sentences are playful templates, not clever wordplay. Up to 12 items. Use when the user has to memorise a sequence like the planets or a checklist.",
        parameters={
            "type": "object",
            "properties": {
                "words": {"type": "string", "description": "The items to remember, in order — comma-separated, or space-separated single words"},
            },
            "required": ["words"],
        },
        handler=mnemonic_maker,
    )
    registry.register(
        name="spelling_bee",
        description="Set the day's spelling challenge from a built-in list of 30 commonly misspelled words: gives the definition and an example sentence with the word blanked out, but never the spelling itself. The user spells it back with flashcard_answer, which grades it letter-perfect. The same word stays all day. Use for spelling practice.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=spelling_bee,
    )
    registry.register(
        name="vocab_word",
        description="Give the word of the day from a built-in list of 24 useful words: the word, what it means, and an example sentence. The same word stays all day and nothing is stored. Use when the user wants to grow their vocabulary a word at a time.",
        parameters={"type": "object", "properties": {},
                    "required": []},
        handler=vocab_word,
    )
    registry.register(
        name="typing_test",
        description="Run a typing test. Action 'start' sets a practice sentence (it rotates daily) for the user to copy against a timer. Action 'score' takes what they typed (typed_text) and how many seconds it took, and reports words per minute plus accuracy against the set sentence. The scoring is honest arithmetic on the numbers given — the timing is the user's own stopwatch.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "score"], "description": "'start' to get a sentence, 'score' to grade an attempt"},
                "typed_text": {"type": "string", "description": "What the user typed, for 'score'"},
                "seconds": {"type": "number", "description": "How many seconds the attempt took, for 'score'"},
            },
            "required": ["action"],
        },
        handler=typing_test,
    )
    registry.register(
        name="reading_level",
        description="Estimate how hard a piece of text is to read: a Flesch reading-ease score out of 100 with a plain-English band, a US school-grade estimate, and the word, sentence and syllable counts behind them. Uses the standard formulas over a rough vowel-group syllable count, so treat it as an estimate. Use when the user wants to know if their writing is pitched at the right level.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The passage to check (at least ten words)"},
            },
            "required": ["text"],
        },
        handler=reading_level,
    )
