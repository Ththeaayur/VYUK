"""Shared store helpers for the skills_games pack.

The data/games.json store (plans/snippets load discipline) plus the
shared result-finishing helpers every game tool uses. Dependency
bottom of the split: helpers <- classic, word, quiz <- facade.
"""

import json
import os
from pathlib import Path


_GAME_KEYS = ("tic_tac_toe", "hangman", "number_guess", "trivia_quiz",
              "word_game")


def _store_path() -> Path:
    """Where game state lives: the app's data dir (core/config.py's
    DATA_DIR), resolved lazily so tests can point it at a temp dir."""
    from .config import DATA_DIR

    return DATA_DIR / "games.json"


def _load_games() -> tuple[dict, str]:
    """(games, disclosure_note).

    Missing file -> empty, no note. Corrupt file (unreadable, invalid
    JSON, or not a JSON object) -> empty with a note so the caller can
    disclose that saved games were lost. Entries that aren't dicts
    (or aren't known game keys) are dropped and counted in the note.
    """
    path = _store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, ""
    except Exception:  # noqa: BLE001 — corrupt state must never crash a tool
        return {}, ("Note: the saved games file (data/games.json) "
                    "couldn't be read — it looks damaged, so I'm "
                    "starting fresh.")
    if not isinstance(data, dict):
        return {}, ("Note: the saved games file (data/games.json) "
                    "couldn't be read — it looks damaged, so I'm "
                    "starting fresh.")
    games: dict = {}
    dropped = 0
    for key, value in data.items():
        if key in _GAME_KEYS and isinstance(value, dict):
            games[key] = value
        else:
            dropped += 1
    note = ""
    if dropped:
        note = (f"Note: skipped {dropped} invalid saved game "
                f"entr{'y' if dropped == 1 else 'ies'} that couldn't "
                "be read.")
    return games, note


def _save_games(games: dict) -> bool:
    """Write the whole store atomically (temp file + os.replace).
    False when the write failed."""
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(games, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        return False


def _finish(body: str, games: dict, note: str, dirty: bool) -> str:
    """Persist (when dirty) and append any disclosures to a result."""
    if dirty and not _save_games(games):
        body += ("\n\n(Warning: I couldn't save the game state — "
                 "this game may not survive a restart.)")
    if note:
        body += f"\n\n{note}"
    return body


def _unknown_action(action: str, verbs: str) -> str:
    return (f"I don't know the action '{action}' — for this game "
            f"use one of: {verbs}.")
