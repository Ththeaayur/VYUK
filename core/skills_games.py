"""
Games pack for VYUK (v5.13) — five playable games the user plays
against VYUK by voice or chat: tic-tac-toe, hangman, number guessing,
a trivia quiz, and a Wordle-style word game.

Every tool is a single entry point with an `action` enum
(start / the game's verb / status / quit), because that's how a
conversation flows: the brain calls start once, then move/guess/
answer repeatedly. EVERY result renders the full current game state
as plain text — board, gallows, word figure, question — so the brain
can narrate it back verbatim and the user always knows where the
game stands.

Storage follows the plans/snippets discipline: state lives in
data/games.json via the established config.DATA_DIR seam (resolved
lazily behind _store_path() so tests can point it elsewhere). One
active game per type; starting a new one replaces the old, and the
result says so. Loads are hardened at the boundary — a corrupt file
is disclosed and treated as fresh, invalid per-game entries are
dropped and counted — and saves are atomic (temp file + os.replace).

These tools mutate VYUK's own data file, so they are NOT read-only
(the remember_* precedent) and they are ungated — they're games.

Difficulty honesty: tic-tac-toe VYUK plays a fixed heuristic —
win if possible, block if needed, else centre/corner/random — NOT
full minimax. A game VYUK literally cannot lose is no fun; the tool
description says "plays to win but can be beaten".

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import json
import os
import random
from pathlib import Path

# INTENTIONAL: the old module's namespace carried its stdlib imports
# (json, os, random, Path) and every private helper/constant, and one
# live importer (core/skills_creative_play.py, function-local
# `from . import skills_games`) reads _HANGMAN_WORDS and _WORDLE_WORDS
# via module attribute — so the facade re-exports ALL of them, and
# the dir() diff vs pre-split is empty in both directions (§2 P2;
# the leaked-import lesson from prior batches). No importer changes.
from .skills_games_helpers import (
    _GAME_KEYS,
    _finish,
    _load_games,
    _save_games,
    _store_path,
    _unknown_action,
)
from .skills_games_classic import (
    _NUMBER_PAR,
    _TTT_LINES,
    _nova_move,
    _pick_secret,
    _render_board,
    _render_number,
    _warmth,
    _winner,
    number_guess,
    tic_tac_toe,
)
from .skills_games_word import (
    _GALLOWS,
    _HANGMAN_WORDS,
    _MAX_WRONG,
    _WORDLE_MAX_GUESSES,
    _WORDLE_WORDS,
    _hangman_wrong,
    _pick_target,
    _pick_word,
    _render_hangman,
    _render_word_game,
    _score,
    hangman,
    word_game,
)
from .skills_games_quiz import (
    _TRIVIA_BANK,
    _TRIVIA_ROUNDS,
    _deal_questions,
    _render_question,
    _trivia_verdict,
    trivia_quiz,
)


def register_all(registry) -> None:
    """Register every Games pack tool on a ToolRegistry-like object."""
    _action = lambda verbs: {  # noqa: E731 — tiny schema helper
        "type": "string", "enum": verbs,
        "description": "What to do: " + ", ".join(verbs)}
    registry.register(
        name="tic_tac_toe",
        description="Play tic-tac-toe against VYUK — the user is X, VYUK is O. One entry point: action 'start' begins a new game (scrapping any unfinished one), 'move' plays a square by position 1–9, 'status' shows the board, 'quit' abandons the game. VYUK plays to win but can be beaten — it takes a win when offered and blocks the user's win, otherwise plays centre/corner at random rather than perfect strategy. Every result shows the full board, with empty squares numbered.",
        parameters={
            "type": "object",
            "properties": {
                "action": _action(["start", "move", "status", "quit"]),
                "position": {"type": "integer",
                             "description": "For action 'move': the square to play, 1–9 as shown on the board"},
            },
            "required": ["action"],
        },
        handler=tic_tac_toe,
    )
    registry.register(
        name="hangman",
        description="Play hangman against VYUK action 'start' begins a game with a word from VYUK's built-in bank of common English words (4–9 letters), 'guess' tries ONE letter, 'status' shows the gallows and word figure, 'quit' abandons the game and reveals the word. Six misses lose the game; repeat guesses are free. Every result shows the full gallows figure, the word as blanks and revealed letters, and the wrong letters so far.",
        parameters={
            "type": "object",
            "properties": {
                "action": _action(["start", "guess", "status", "quit"]),
                "letter": {"type": "string",
                           "description": "For action 'guess': a single letter A–Z"},
            },
            "required": ["action"],
        },
        handler=hangman,
    )
    registry.register(
        name="number_guess",
        description="Play the number-guessing game: VYUK thinks of a number between 1 and 100. action 'start' begins, 'guess' offers a number — VYUK answers higher/lower plus how warm the guess is (burning within 5, hot within 10, warm within 25, otherwise cold) — 'status' lists the guesses so far without revealing the number, 'quit' gives up and reveals it. Guesses outside 1–100 don't count.",
        parameters={
            "type": "object",
            "properties": {
                "action": _action(["start", "guess", "status", "quit"]),
                "guess": {"type": "integer",
                          "description": "For action 'guess': your number, 1–100"},
            },
            "required": ["action"],
        },
        handler=number_guess,
    )
    registry.register(
        name="trivia_quiz",
        description="Play a five-question trivia round from VYUK's built-in bank (48+ timeless general-knowledge questions across science, geography, history, sport, arts and tech — no facts that expire). action 'start' deals 5 fresh questions, 'answer' answers the current one with the letter A–D or the exact answer text and scores it immediately (a wrong answer is shown the right one), 'status' re-shows the current question and score, 'quit' abandons the round.",
        parameters={
            "type": "object",
            "properties": {
                "action": _action(["start", "answer", "status", "quit"]),
                "answer": {"type": "string",
                           "description": "For action 'answer': the letter A–D or the answer text"},
            },
            "required": ["action"],
        },
        handler=trivia_quiz,
    )
    registry.register(
        name="word_game",
        description="Play a Wordle-style word game: VYUK picks a five-letter target word from its built-in list and the user has 6 guesses. action 'start' begins, 'guess' scores any five-letter word (guesses just need five letters — only the target is guaranteed to come from the list), 'status' shows all guesses so far, 'quit' gives up and reveals the word. Feedback is per letter: green = right letter, right place; yellow = in the word, wrong place; dark = not in the word — with duplicate letters scored by the classic two-pass rule, so a letter never earns more marks than it appears in the target.",
        parameters={
            "type": "object",
            "properties": {
                "action": _action(["start", "guess", "status", "quit"]),
                "guess": {"type": "string",
                          "description": "For action 'guess': a five-letter word"},
            },
            "required": ["action"],
        },
        handler=word_game,
    )
