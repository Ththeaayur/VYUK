"""Word games for the skills_games pack: hangman and the
Wordle-style word game (plan §1a family "word-games").
"""

import random

# INTENTIONAL: _finish, _load_games and _unknown_action moved to
# skills_games_helpers in this split (§2 P3(b)); the word tools call
# them by bare name, so they are imported here.
from .skills_games_helpers import _finish, _load_games, _unknown_action


_HANGMAN_WORDS = tuple(
    "apple banana orange planet river ocean forest garden window "
    "bridge castle dragon pirate rocket guitar monkey rabbit turtle "
    "dolphin penguin elephant kangaroo butterfly rainbow thunder "
    "lightning volcano desert jungle meadow kitchen blanket pillow "
    "candle mirror pocket basket market school teacher student "
    "library picture village journey adventure treasure mystery "
    "history science artist dancer singer farmer doctor pilot "
    "sailor bicycle subway highway station airport summer winter "
    "autumn spring morning evening weekend holiday birthday picnic "
    "cheese butter bread honey lemon melon grape peach mango "
    "cherry tomato potato carrot pepper cucumber pumpkin coconut "
    "walnut almond tiger zebra horse sheep goat deer frog duck "
    "swan eagle hawk crow robin shark whale snake spider beetle "
    "happy brave clever gentle funny jolly proud merry swift "
    "quiet clock drum flute puzzle ladder hammer needle button "
    "pencil paper ruler eraser".split()
)


_MAX_WRONG = 6


_GALLOWS = (
    "\n".join(("  +---+", "  |   |", "      |", "      |",
               "      |", "      |", "=========")),
    "\n".join(("  +---+", "  |   |", "  O   |", "      |",
               "      |", "      |", "=========")),
    "\n".join(("  +---+", "  |   |", "  O   |", "  |   |",
               "      |", "      |", "=========")),
    "\n".join(("  +---+", "  |   |", "  O   |", " /|   |",
               "      |", "      |", "=========")),
    "\n".join(("  +---+", "  |   |", "  O   |", " /|\\  |",
               "      |", "      |", "=========")),
    "\n".join(("  +---+", "  |   |", "  O   |", " /|\\  |",
               " /    |", "      |", "=========")),
    "\n".join(("  +---+", "  |   |", "  O   |", " /|\\  |",
               " / \\  |", "      |", "=========")),
)


def _pick_word(rng) -> str:
    """Pick this game's hangman word (a seam so tests can force one)."""
    return rng.choice(_HANGMAN_WORDS)


def _hangman_wrong(state) -> list:
    word = state["word"]
    return [ch for ch in state["tried"] if ch not in word]


def _render_hangman(state) -> str:
    word = state["word"]
    tried = state["tried"]
    wrong = _hangman_wrong(state)
    figure = " ".join(ch.upper() if ch in tried else "_"
                       for ch in word)
    lines = [_GALLOWS[len(wrong)], "", f"Word: {figure}"]
    lines.append("Wrong letters: "
                 + (", ".join(ch.upper() for ch in wrong)
                    if wrong else "none"))
    lines.append(f"Misses left: {_MAX_WRONG - len(wrong)}")
    return "\n".join(lines)


def hangman(action: str = "status", letter: str = "") -> str:
    """Play hangman against VYUK"""
    action = str(action or "status").strip().lower()
    if action not in ("start", "guess", "status", "quit"):
        return _unknown_action(action, "start, guess, status, quit")
    games, note = _load_games()
    state = games.get("hangman")

    if action == "start":
        replaced = state is not None and not state.get("over")
        games["hangman"] = {"word": _pick_word(random),
                            "tried": [], "over": False}
        body = "New game of hangman — I'm thinking of a word. "
        if replaced:
            body += "I've scrapped the game we had going. "
        body += ("You have 6 misses. Guess one letter at a time "
                 "with action 'guess'.\n\n"
                 + _render_hangman(games["hangman"]))
        return _finish(body, games, note, dirty=True)

    if action == "quit":
        if state is None:
            return _finish("No hangman game is in progress.",
                           games, note, dirty=False)
        word = state.get("word", "")
        games.pop("hangman", None)
        return _finish(f"Hangman game scrapped — the word was "
                       f"'{word}'. Start a new one any time.",
                       games, note, dirty=True)

    if state is None or not isinstance(state.get("word"), str) \
            or not isinstance(state.get("tried"), list):
        return _finish("No hangman game is in progress — start one "
                       "with action 'start'.", games, note,
                       dirty=False)

    if action == "status":
        if state.get("over"):
            body = ("That hangman game is finished. Final state:"
                    "\n\n" + _render_hangman(state)
                    + "\n\nStart a new game with action 'start'.")
        else:
            body = ("Hangman in progress:\n\n"
                    + _render_hangman(state))
        return _finish(body, games, note, dirty=False)

    # action == "guess"
    if state.get("over"):
        return _finish("That game is already finished:\n\n"
                       + _render_hangman(state)
                       + "\n\nStart a new one with action 'start'.",
                       games, note, dirty=False)
    guess = str(letter or "").strip().lower()
    if len(guess) != 1 or not guess.isalpha():
        return _finish("One letter at a time, please — give me a "
                       "single letter from A to Z.\n\n"
                       + _render_hangman(state),
                       games, note, dirty=False)
    word = state["word"]
    if guess in state["tried"]:
        return _finish(f"You already tried '{guess.upper()}' — "
                       "repeat guesses are free, no miss charged."
                       "\n\n" + _render_hangman(state),
                       games, note, dirty=False)

    state["tried"].append(guess)
    wrong = _hangman_wrong(state)
    if guess in word:
        if all(ch in state["tried"] for ch in word):
            state["over"] = True
            body = (f"'{guess.upper()}' is in the word — and that "
                    f"completes it: {word.upper()}. You win!\n\n"
                    + _render_hangman(state))
        else:
            body = (f"Good guess — '{guess.upper()}' is in the "
                    "word.\n\n" + _render_hangman(state))
    else:
        if len(wrong) >= _MAX_WRONG:
            state["over"] = True
            body = (f"'{guess.upper()}' isn't in the word — and "
                    f"that's your last miss. The word was "
                    f"'{word}'. Better luck next game.\n\n"
                    + _render_hangman(state))
        else:
            body = (f"'{guess.upper()}' isn't in the word — "
                    "that's a miss.\n\n" + _render_hangman(state))
    return _finish(body, games, note, dirty=True)


_WORDLE_WORDS = tuple(
    "about above abuse actor acute admit adopt adult after again "
    "agent agree ahead alarm album alert algae alibi alien align "
    "alike alive alley allow alloy alone aloud alpha altar alter "
    "amend ample angel angle angry ankle annex annoy anvil apart "
    "apple apply apron arena argue arise armor aroma array arrow "
    "aside asset atlas attic audio audit avail avoid awake award "
    "aware awful bacon badge bagel baggy baker balmy banal banjo "
    "barge baron basic basil basin basis batch baton batty beach "
    "beard beast beech began begin beige being below bench berry "
    "berth beset bible bicep bidet bilge binge bingo birth black "
    "blade blame blank blast blaze bleak bleed blend bless blimp "
    "blind blink bliss blitz bloat block blond blood bloom blown "
    "bluff blunt blurt blush board boast bobby bongo bonus boost "
    "booth booty booze bored boron bosom bossy botch bound bowel "
    "boxer brace braid brain brake brand brash brass brave bread "
    "break breed bribe brick bride brief brine bring brink brisk "
    "broad broil broke brood brook broom broth brown brunt brush "
    "brute buddy budge bugle build built bully bunch burst buxom "
    "buyer bylaw cabin cable cache cadet cagey caked cakes camel "
    "cameo canal candy canny canoe canon caper carat caret cargo "
    "carol carry carts carve cased cases casks caste casts catch "
    "cater catty cause cease cedar cells cello cents certs chafe "
    "chaff chain chair chalk champ chant chaos chard chart chase "
    "chasm cheap cheat check cheek cheer chess chest chewy chick "
    "chide chief child chili chill chime chink chino chips chirp "
    "chive chock choir choke chomp chops chord chore chose chuck "
    "chump chunk churn chute cider cigar cinch circa cited cites "
    "civic civil claim clamp clang clank clans claps clash clasp "
    "class claws clays clean clear cleat cleft clerk click cliff "
    "climb clime cling clink clips cloak clock clogs clone close "
    "cloth clots cloud clout clove clown clubs cluck clued clues "
    "clump clung coach coals coast coats cobra cocky cocoa colic "
    "color comet comic comma couch cough could count court cover "
    "covet cowed coyly crack craft cramp crane crank crash crass "
    "crate crave crawl craze crazy creak cream creek creel creep "
    "creme crepe cress crest crick cried crier cries crime crimp "
    "crisp cross crowd crown crude cruel crumb crush crust crypt "
    "cubby cubic cumin "
    .split()
)


_WORDLE_MAX_GUESSES = 6


def _pick_target(rng) -> str:
    """Pick this game's target word (a seam for tests)."""
    return rng.choice(_WORDLE_WORDS)


def _score(target: str, guess: str) -> list:
    """Two-pass Wordle scoring -> a list of 5 emoji (🟩/🟨/⬛).

    Pass 1 marks exact matches green and banks the target's
    remaining letters; pass 2 walks the non-green positions and
    spends banked letters on yellows — so a letter can never earn
    more marks than it appears in the target (the classic
    duplicate-letter rule)."""
    target = target.upper()
    guess = guess.upper()
    marks = ["⬛"] * 5
    remaining: dict = {}
    for i in range(5):
        if guess[i] == target[i]:
            marks[i] = "🟩"
        else:
            remaining[target[i]] = remaining.get(target[i], 0) + 1
    for i in range(5):
        if marks[i] == "🟩":
            continue
        ch = guess[i]
        if remaining.get(ch, 0) > 0:
            marks[i] = "🟨"
            remaining[ch] -= 1
    return marks


def _render_word_game(state) -> str:
    target = state["target"]
    lines = []
    green: set = set()
    yellow: set = set()
    grey: set = set()
    for g in state["guesses"]:
        marks = _score(target, g)
        lines.append(" ".join(g.upper()))
        lines.append("".join(marks))
        for ch, mark in zip(g.upper(), marks):
            if mark == "🟩":
                green.add(ch)
            elif mark == "🟨":
                yellow.add(ch)
            else:
                grey.add(ch)
    yellow -= green
    grey -= green | yellow
    left = _WORDLE_MAX_GUESSES - len(state["guesses"])
    lines.append(f"Guesses left: {left}")
    known = []
    if green:
        known.append("in (right place): " + ", ".join(sorted(green)))
    if yellow:
        known.append("in (wrong place): " + ", ".join(sorted(yellow)))
    if grey:
        known.append("not in the word: " + ", ".join(sorted(grey)))
    if known:
        lines.append("Letters — " + "; ".join(known))
    return "\n".join(lines)


def word_game(action: str = "status", guess: str = "") -> str:
    """Play the Wordle-style word game against VYUK"""
    action = str(action or "status").strip().lower()
    if action not in ("start", "guess", "status", "quit"):
        return _unknown_action(action, "start, guess, status, quit")
    games, note = _load_games()
    state = games.get("word_game")

    if action == "start":
        replaced = state is not None and not state.get("over")
        games["word_game"] = {"target": _pick_target(random),
                              "guesses": [], "over": False}
        body = "New word game — I'm thinking of a five-letter word. "
        if replaced:
            body += "I've scrapped the game we had going. "
        body += (f"You have {_WORDLE_MAX_GUESSES} guesses. Any "
                 "five-letter word counts as a guess; the target "
                 "always comes from my word list. Green means the "
                 "right letter in the right place, yellow means the "
                 "letter is in the word but in the wrong place, and "
                 "dark means it isn't in the word (or you've used up "
                 "its copies). Guess with action 'guess'.")
        return _finish(body, games, note, dirty=True)

    if action == "quit":
        if state is None:
            return _finish("No word game is in progress.",
                           games, note, dirty=False)
        target = state.get("target", "")
        games.pop("word_game", None)
        return _finish(f"Word game scrapped — the word was "
                       f"'{target.upper()}'. Start a new one any "
                       "time.", games, note, dirty=True)

    if state is None or not isinstance(state.get("target"), str) \
            or not isinstance(state.get("guesses"), list):
        return _finish("No word game is in progress — start one "
                       "with action 'start'.", games, note,
                       dirty=False)

    if action == "status":
        if state.get("over"):
            body = ("That word game is finished. The word was "
                    f"'{state['target'].upper()}'.\n\n"
                    + _render_word_game(state)
                    + "\n\nStart a new game with action 'start'.")
        elif state["guesses"]:
            body = ("Word game in progress:\n\n"
                    + _render_word_game(state))
        else:
            body = ("Word game in progress — no guesses yet. You "
                    f"have {_WORDLE_MAX_GUESSES} guesses; the target "
                    "is a five-letter word.")
        return _finish(body, games, note, dirty=False)

    # action == "guess"
    if state.get("over"):
        return _finish("That game is already finished:\n\n"
                       + _render_word_game(state)
                       + "\n\nStart a new one with action 'start'.",
                       games, note, dirty=False)
    word = str(guess or "").strip().lower()
    if len(word) != 5 or not word.isalpha():
        return _finish("Guesses must be exactly five letters, A–Z. "
                       "That one doesn't count.\n\n"
                       + _render_word_game(state),
                       games, note, dirty=False)
    state["guesses"].append(word)
    target = state["target"]
    if word == target:
        state["over"] = True
        n = len(state["guesses"])
        body = (f"{word.upper()} — that's the word! You got it in "
                f"{n} guess{'es' if n != 1 else ''}.\n\n"
                + _render_word_game(state))
    elif len(state["guesses"]) >= _WORDLE_MAX_GUESSES:
        state["over"] = True
        body = (f"{word.upper()} — no. And that was your last "
                f"guess; the word was '{target.upper()}'.\n\n"
                + _render_word_game(state))
    else:
        body = (f"{word.upper()} — not the word. Here's where "
                "things stand:\n\n" + _render_word_game(state))
    return _finish(body, games, note, dirty=True)
