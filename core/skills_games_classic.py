"""Classic games for the skills_games pack: tic-tac-toe and the
number-guessing game (the parlour games, as opposed to the word games
and the quiz).
"""

import random

# INTENTIONAL: _finish, _load_games and _unknown_action moved to
# skills_games_helpers in this split (§2 P3(b)); the classic tools
# call them by bare name, so they are imported here.
from .skills_games_helpers import _finish, _load_games, _unknown_action


_TTT_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8),
              (0, 3, 6), (1, 4, 7), (2, 5, 8),
              (0, 4, 8), (2, 4, 6))


def _winner(board) -> str | None:
    """'X' or 'O' when that mark has a line, 'draw' on a full board
    with no line, else None."""
    for a, b, c in _TTT_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if all(board):
        return "draw"
    return None


def _nova_move(board, rng) -> int:
    """VYUK's reply move (the board index). Fixed heuristic: take a
    win when one is offered, block the human's win when threatened,
    else centre, then a random free corner, then a random free cell.
    Deliberately NOT minimax — VYUK plays to win but can be beaten."""
    for mark in ("O", "X"):  # win first, then block
        for i in range(9):
            if not board[i]:
                trial = list(board)
                trial[i] = mark
                if _winner(trial) == mark:
                    return i
    if not board[4]:
        return 4
    corners = [i for i in (0, 2, 6, 8) if not board[i]]
    if corners:
        return rng.choice(corners)
    free = [i for i in range(9) if not board[i]]
    return rng.choice(free)


def _render_board(board) -> str:
    cells = [board[i] if board[i] else str(i + 1) for i in range(9)]
    rows = [" {} | {} | {} ".format(*cells[r * 3:r * 3 + 3])
            for r in range(3)]
    return "\n---+---+---\n".join(rows)


def tic_tac_toe(action: str = "status", position: int = 0) -> str:
    """Play tic-tac-toe against VYUK: the user is X, VYUK is O."""
    action = str(action or "status").strip().lower()
    if action not in ("start", "move", "status", "quit"):
        return _unknown_action(action, "start, move, status, quit")
    games, note = _load_games()
    state = games.get("tic_tac_toe")

    if action == "start":
        replaced = state is not None and not state.get("over")
        games["tic_tac_toe"] = {"board": [""] * 9, "over": False}
        body = "New game of tic-tac-toe — you're X, I'm O. "
        if replaced:
            body += "I've scrapped the unfinished game we had going. "
        body += ("Call tic_tac_toe with action 'move' and a position "
                 "from 1 to 9.\n\n" + _render_board([""] * 9))
        return _finish(body, games, note, dirty=True)

    if action == "quit":
        if state is None:
            return _finish("No tic-tac-toe game is in progress.",
                           games, note, dirty=False)
        games.pop("tic_tac_toe", None)
        return _finish("Tic-tac-toe game scrapped. Start a new one "
                       "any time.", games, note, dirty=True)

    if state is None or not isinstance(state.get("board"), list) \
            or len(state.get("board", [])) != 9:
        return _finish("No tic-tac-toe game is in progress — start "
                       "one with action 'start'.", games, note,
                       dirty=False)

    board = state["board"]
    if action == "status":
        if state.get("over"):
            body = ("That tic-tac-toe game is finished. Final board:"
                    "\n\n" + _render_board(board) +
                    "\n\nStart a new game with action 'start'.")
        else:
            body = ("Tic-tac-toe in progress — you're X, I'm O. "
                    "Your move: pick an empty numbered square.\n\n"
                    + _render_board(board))
        return _finish(body, games, note, dirty=False)

    # action == "move"
    if state.get("over"):
        return _finish("That game is already finished:\n\n"
                       + _render_board(board)
                       + "\n\nStart a new one with action 'start'.",
                       games, note, dirty=False)
    try:
        pos = int(position)
    except (TypeError, ValueError):
        pos = 0
    if not 1 <= pos <= 9:
        return _finish("A move needs a position from 1 to 9. "
                       "Current board:\n\n" + _render_board(board),
                       games, note, dirty=False)
    if board[pos - 1]:
        return _finish(f"Square {pos} is already taken by "
                       f"{board[pos - 1]} — pick an empty numbered "
                       "square.\n\n" + _render_board(board),
                       games, note, dirty=False)

    board[pos - 1] = "X"
    result = _winner(board)
    if result == "X":
        state["over"] = True
        body = (f"You played square {pos} — and that's a line. "
                "You win! Well played.\n\n" + _render_board(board))
        return _finish(body, games, note, dirty=True)
    if result == "draw":
        state["over"] = True
        body = (f"You played square {pos} — the board is full. "
                "It's a draw.\n\n" + _render_board(board))
        return _finish(body, games, note, dirty=True)

    nova_pos = _nova_move(board, random)
    board[nova_pos] = "O"
    result = _winner(board)
    if result == "O":
        state["over"] = True
        body = (f"You played square {pos}; I played square "
                f"{nova_pos + 1} — and that completes my line. "
                "I win this one.\n\n" + _render_board(board))
    elif result == "draw":
        state["over"] = True
        body = (f"You played square {pos}; I played square "
                f"{nova_pos + 1} — and the board is full. "
                "It's a draw.\n\n" + _render_board(board))
    else:
        body = (f"You played square {pos}; I played square "
                f"{nova_pos + 1}. Your move.\n\n"
                + _render_board(board))
    return _finish(body, games, note, dirty=True)


_NUMBER_PAR = 8


def _pick_secret(rng) -> int:
    """Pick this game's secret number (a seam for tests)."""
    return rng.randint(1, 100)


def _warmth(guess: int, secret: int) -> str:
    """Distance band for a guess: burning <=5, hot <=10, warm <=25,
    else cold."""
    d = abs(guess - secret)
    if d <= 5:
        return "burning"
    if d <= 10:
        return "hot"
    if d <= 25:
        return "warm"
    return "cold"


def _render_number(state) -> str:
    guesses = state["guesses"]
    if not guesses:
        return ("I've thought of a number between 1 and 100. "
                "Take a guess with action 'guess'.")
    secret = state["secret"]
    history = ", ".join(
        f"{g} ({'higher' if secret > g else 'lower'}, "
        f"{_warmth(g, secret)})" for g in guesses)
    return (f"Guesses so far ({len(guesses)}): {history}.\n"
            "My number is still between 1 and 100 — guess again "
            "with action 'guess'.")


def number_guess(action: str = "status", guess: int = 0) -> str:
    """Play the number guessing game against VYUK"""
    action = str(action or "status").strip().lower()
    if action not in ("start", "guess", "status", "quit"):
        return _unknown_action(action, "start, guess, status, quit")
    games, note = _load_games()
    state = games.get("number_guess")

    if action == "start":
        replaced = state is not None and not state.get("over")
        games["number_guess"] = {"secret": _pick_secret(random),
                                 "guesses": [], "over": False}
        body = "New game — "
        if replaced:
            body += "I've scrapped the number I was thinking of. "
        body += _render_number(games["number_guess"])
        return _finish(body, games, note, dirty=True)

    if action == "quit":
        if state is None:
            return _finish("No number-guessing game is in progress.",
                           games, note, dirty=False)
        secret = state.get("secret", "?")
        games.pop("number_guess", None)
        return _finish(f"Game scrapped — my number was {secret}. "
                       "Start a new one any time.", games, note,
                       dirty=True)

    if state is None or not isinstance(state.get("secret"), int) \
            or not isinstance(state.get("guesses"), list):
        return _finish("No number-guessing game is in progress — "
                       "start one with action 'start'.", games, note,
                       dirty=False)

    if action == "status":
        if state.get("over"):
            body = ("That number-guessing game is finished — you "
                    f"found {state['secret']} in "
                    f"{len(state['guesses'])} guesses. Start a new "
                    "game with action 'start'.")
        else:
            body = "Number guessing in progress.\n" \
                + _render_number(state)
        return _finish(body, games, note, dirty=False)

    # action == "guess"
    if state.get("over"):
        return _finish("That game is already finished. Start a new "
                       "one with action 'start'.", games, note,
                       dirty=False)
    try:
        value = int(guess)
    except (TypeError, ValueError):
        value = 0
    if not 1 <= value <= 100:
        return _finish("Give me a whole number between 1 and 100. "
                       "That one doesn't count as a guess.\n"
                       + _render_number(state),
                       games, note, dirty=False)

    secret = state["secret"]
    state["guesses"].append(value)
    attempts = len(state["guesses"])
    if value == secret:
        state["over"] = True
        if attempts <= 6:
            par = (f"Excellent — par is {_NUMBER_PAR} and you beat "
                   "it comfortably.")
        elif attempts <= _NUMBER_PAR:
            par = f"Right on par ({_NUMBER_PAR})."
        else:
            par = (f"Par was {_NUMBER_PAR} — but a win is a win.")
        body = (f"{value} — that's my number! You found it in "
                f"{attempts} guess{'es' if attempts != 1 else ''}. "
                + par)
        return _finish(body, games, note, dirty=True)

    direction = "higher" if secret > value else "lower"
    body = (f"{value} — too {'low' if secret > value else 'high'}: "
            f"my number is {direction}, and you're "
            f"{_warmth(value, secret)}.\n"
            + _render_number(state))
    return _finish(body, games, note, dirty=True)
