"""Quiz game for the skills_games pack: the trivia quiz
(plan §1a family "quiz").
"""

import random

# INTENTIONAL: _finish, _load_games and _unknown_action moved to
# skills_games_helpers in this split (§2 P3(b)); the quiz tool calls
# them by bare name, so they are imported here.
from .skills_games_helpers import _finish, _load_games, _unknown_action


_TRIVIA_BANK = (
    # Science
    {"q": "What is the chemical symbol for water?",
     "choices": ["H2O", "CO2", "O2", "NaCl"], "correct": "H2O",
     "category": "Science"},
    {"q": "Which planet is closest to the Sun?",
     "choices": ["Venus", "Mercury", "Mars", "Earth"],
     "correct": "Mercury", "category": "Science"},
    {"q": "Which gas do plants absorb from the air for photosynthesis?",
     "choices": ["Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"],
     "correct": "Carbon dioxide", "category": "Science"},
    {"q": "How many legs does a spider have?",
     "choices": ["Six", "Eight", "Ten", "Four"], "correct": "Eight",
     "category": "Science"},
    {"q": "What force keeps your feet on the ground?",
     "choices": ["Magnetism", "Friction", "Gravity", "Inertia"],
     "correct": "Gravity", "category": "Science"},
    {"q": "What is the main gas in Earth's atmosphere?",
     "choices": ["Oxygen", "Nitrogen", "Carbon dioxide", "Argon"],
     "correct": "Nitrogen", "category": "Science"},
    {"q": "How many sides does a hexagon have?",
     "choices": ["Five", "Six", "Seven", "Eight"], "correct": "Six",
     "category": "Science"},
    {"q": "Which organ pumps blood around the human body?",
     "choices": ["The liver", "The brain", "The heart", "The lung"],
     "correct": "The heart", "category": "Science"},
    # Geography
    {"q": "What is the capital of France?",
     "choices": ["London", "Paris", "Berlin", "Madrid"],
     "correct": "Paris", "category": "Geography"},
    {"q": "Which is the largest ocean on Earth?",
     "choices": ["Atlantic", "Indian", "Arctic", "Pacific"],
     "correct": "Pacific", "category": "Geography"},
    {"q": "Which river flows through Egypt?",
     "choices": ["The Amazon", "The Nile", "The Danube",
                 "The Mississippi"], "correct": "The Nile",
     "category": "Geography"},
    {"q": "What is the capital of Japan?",
     "choices": ["Beijing", "Seoul", "Tokyo", "Bangkok"],
     "correct": "Tokyo", "category": "Geography"},
    {"q": "What is the largest hot desert in the world?",
     "choices": ["The Gobi", "The Sahara", "The Mojave",
                 "The Kalahari"], "correct": "The Sahara",
     "category": "Geography"},
    {"q": "Mount Everest sits on the border of Nepal and which "
          "other country?",
     "choices": ["India", "China", "Bhutan", "Pakistan"],
     "correct": "China", "category": "Geography"},
    {"q": "What is the capital of Australia?",
     "choices": ["Sydney", "Melbourne", "Canberra", "Perth"],
     "correct": "Canberra", "category": "Geography"},
    {"q": "On which continent is Brazil?",
     "choices": ["Africa", "South America", "North America",
                 "Europe"], "correct": "South America",
     "category": "Geography"},
    # History
    {"q": "Who was the first President of the United States?",
     "choices": ["Abraham Lincoln", "Thomas Jefferson",
                 "George Washington", "John Adams"],
     "correct": "George Washington", "category": "History"},
    {"q": "The Great Wall is in which country?",
     "choices": ["Japan", "China", "India", "Mongolia"],
     "correct": "China", "category": "History"},
    {"q": "The ancient Egyptians wrote in what?",
     "choices": ["Cuneiform", "Hieroglyphs", "Latin", "Runes"],
     "correct": "Hieroglyphs", "category": "History"},
    {"q": "In which year did World War II end?",
     "choices": ["1918", "1939", "1945", "1950"], "correct": "1945",
     "category": "History"},
    {"q": "In which year did the Titanic sink?",
     "choices": ["1905", "1912", "1920", "1898"], "correct": "1912",
     "category": "History"},
    {"q": "Who was the first person to walk on the Moon?",
     "choices": ["Buzz Aldrin", "Yuri Gagarin", "Neil Armstrong",
                 "Michael Collins"], "correct": "Neil Armstrong",
     "category": "History"},
    {"q": "What language did the ancient Romans speak?",
     "choices": ["Greek", "Latin", "Arabic", "Hebrew"],
     "correct": "Latin", "category": "History"},
    {"q": "Which ship carried the Pilgrims to America in 1620?",
     "choices": ["The Mayflower", "The Santa Maria", "The Beagle",
                 "The Endeavour"], "correct": "The Mayflower",
     "category": "History"},
    # Sport
    {"q": "How many players does a football (soccer) team field "
          "at once?",
     "choices": ["Nine", "Ten", "Eleven", "Twelve"],
     "correct": "Eleven", "category": "Sport"},
    {"q": "In tennis, what is a score of zero called?",
     "choices": ["Love", "Duck", "Nil", "Ace"], "correct": "Love",
     "category": "Sport"},
    {"q": "How many rings are on the Olympic flag?",
     "choices": ["Four", "Five", "Six", "Seven"], "correct": "Five",
     "category": "Sport"},
    {"q": "A marathon is roughly how many kilometres?",
     "choices": ["26", "32", "42", "50"], "correct": "42",
     "category": "Sport"},
    {"q": "Which sport uses a shuttlecock?",
     "choices": ["Squash", "Badminton", "Table tennis", "Lacrosse"],
     "correct": "Badminton", "category": "Sport"},
    {"q": "In golf, what is one stroke under par on a hole called?",
     "choices": ["An eagle", "A bogey", "A birdie", "An albatross"],
     "correct": "A birdie", "category": "Sport"},
    {"q": "How many minutes does a standard football (soccer) "
          "match last, not counting extra time?",
     "choices": ["80", "90", "100", "120"], "correct": "90",
     "category": "Sport"},
    {"q": "How high is a standard basketball hoop, in feet?",
     "choices": ["8 feet", "9 feet", "10 feet", "11 feet"],
     "correct": "10 feet", "category": "Sport"},
    # Arts
    {"q": "Who painted the Mona Lisa?",
     "choices": ["Vincent van Gogh", "Pablo Picasso",
                 "Leonardo da Vinci", "Claude Monet"],
     "correct": "Leonardo da Vinci", "category": "Arts"},
    {"q": "How many keys does a standard piano have?",
     "choices": ["76", "84", "88", "96"], "correct": "88",
     "category": "Arts"},
    {"q": "Who wrote Romeo and Juliet?",
     "choices": ["Charles Dickens", "William Shakespeare",
                 "Jane Austen", "Oscar Wilde"],
     "correct": "William Shakespeare", "category": "Arts"},
    {"q": "Who painted The Starry Night?",
     "choices": ["Vincent van Gogh", "Salvador Dali",
                 "Pablo Picasso", "Rembrandt"],
     "correct": "Vincent van Gogh", "category": "Arts"},
    {"q": "How many symphonies did Beethoven compose?",
     "choices": ["Five", "Seven", "Nine", "Twelve"],
     "correct": "Nine", "category": "Arts"},
    {"q": "How many players are in a string quartet?",
     "choices": ["Three", "Four", "Five", "Six"], "correct": "Four",
     "category": "Arts"},
    {"q": "Origami, the art of paper folding, comes from which "
          "country?",
     "choices": ["China", "Korea", "Japan", "Thailand"],
     "correct": "Japan", "category": "Arts"},
    {"q": "Who wrote the novel Pride and Prejudice?",
     "choices": ["Charlotte Bronte", "Jane Austen", "Mary Shelley",
                 "Emily Bronte"], "correct": "Jane Austen",
     "category": "Arts"},
    # Tech
    {"q": "What does 'www' stand for in a web address?",
     "choices": ["World Wide Web", "Web World Window",
                 "Wide World Web", "World Web Works"],
     "correct": "World Wide Web", "category": "Tech"},
    {"q": "What does CPU stand for?",
     "choices": ["Computer Personal Unit", "Central Processing Unit",
                 "Central Program Utility", "Core Processing Utility"],
     "correct": "Central Processing Unit", "category": "Tech"},
    {"q": "How many digits does the binary number system use?",
     "choices": ["Two", "Eight", "Ten", "Sixteen"], "correct": "Two",
     "category": "Tech"},
    {"q": "Who co-founded Apple together with Steve Jobs?",
     "choices": ["Bill Gates", "Steve Wozniak", "Elon Musk",
                 "Jeff Bezos"], "correct": "Steve Wozniak",
     "category": "Tech"},
    {"q": "In which year was the first iPhone released?",
     "choices": ["2005", "2007", "2009", "2010"], "correct": "2007",
     "category": "Tech"},
    {"q": "What does USB stand for?",
     "choices": ["Universal Serial Bus", "United System Backup",
                 "Universal System Board", "Unified Serial Bridge"],
     "correct": "Universal Serial Bus", "category": "Tech"},
    {"q": "Which programming language shares its name with a snake?",
     "choices": ["Java", "Ruby", "Python", "Perl"],
     "correct": "Python", "category": "Tech"},
    {"q": "How many bits are in a byte?",
     "choices": ["Four", "Eight", "Sixteen", "Thirty-two"],
     "correct": "Eight", "category": "Tech"},
)


_TRIVIA_ROUNDS = 5


def _deal_questions(rng, count: int = _TRIVIA_ROUNDS) -> list:
    """Deal `count` questions at random, no repeats, each with its
    choices shuffled (a seam so tests can script the order)."""
    picked = rng.sample(range(len(_TRIVIA_BANK)),
                        min(count, len(_TRIVIA_BANK)))
    dealt = []
    for i in picked:
        q = _TRIVIA_BANK[i]
        choices = list(q["choices"])
        rng.shuffle(choices)
        dealt.append({"q": q["q"], "choices": choices,
                      "correct": q["correct"],
                      "category": q["category"]})
    return dealt


def _render_question(state) -> str:
    idx = state["index"]
    q = state["questions"][idx]
    lines = [f"Question {idx + 1} of {len(state['questions'])} — "
             f"{q['category']}", q["q"]]
    for j, choice in enumerate(q["choices"]):
        lines.append(f"{'ABCD'[j]}) {choice}")
    lines.append(f"Score so far: {state['score']}")
    lines.append("Answer with action 'answer' and the letter A–D or "
                 "the answer text.")
    return "\n".join(lines)


def _trivia_verdict(score: int, total: int) -> str:
    if score == total:
        return "A perfect score — trivia champion!"
    if score == total - 1:
        return "Great round — nearly perfect."
    if score >= total // 2 + 1:
        return "A solid score."
    if score >= 2:
        return "Room to grow — the next round could be yours."
    return "A tough round — trivia is cruel sometimes. Play again?"


def trivia_quiz(action: str = "status", answer: str = "") -> str:
    """Play a five-question trivia quiz against VYUK's bank."""
    action = str(action or "status").strip().lower()
    if action not in ("start", "answer", "status", "quit"):
        return _unknown_action(action, "start, answer, status, quit")
    games, note = _load_games()
    state = games.get("trivia_quiz")

    if action == "start":
        replaced = state is not None \
            and state.get("index", 0) < len(state.get("questions", []))
        games["trivia_quiz"] = {"questions": _deal_questions(random),
                                "index": 0, "score": 0}
        body = "New trivia round — five questions, no repeats. "
        if replaced:
            body += "I've scrapped the unfinished round. "
        body += "\n\n" + _render_question(games["trivia_quiz"])
        return _finish(body, games, note, dirty=True)

    if action == "quit":
        if state is None:
            return _finish("No trivia round is in progress.",
                           games, note, dirty=False)
        score = state.get("score", 0)
        games.pop("trivia_quiz", None)
        return _finish(f"Trivia round scrapped — you had {score} "
                       "point(s). Start a new round any time.",
                       games, note, dirty=True)

    if state is None or not isinstance(state.get("questions"), list) \
            or not state["questions"]:
        return _finish("No trivia round is in progress — start one "
                       "with action 'start'.", games, note,
                       dirty=False)

    total = len(state["questions"])
    if action == "status":
        if state["index"] >= total:
            body = (f"That trivia round is finished — final score "
                    f"{state['score']} of {total}. "
                    + _trivia_verdict(state["score"], total)
                    + " Start a new round with action 'start'.")
        else:
            body = "Trivia round in progress.\n\n" \
                + _render_question(state)
        return _finish(body, games, note, dirty=False)

    # action == "answer"
    if state["index"] >= total:
        return _finish("That round is already finished — you scored "
                       f"{state['score']} of {total}. Start a new "
                       "round with action 'start'.", games, note,
                       dirty=False)
    q = state["questions"][state["index"]]
    raw = str(answer or "").strip()
    picked = None
    if len(raw) == 1 and raw.upper() in "ABCD":
        picked = q["choices"]["ABCD".index(raw.upper())]
    else:
        for choice in q["choices"]:
            if choice.lower() == raw.lower():
                picked = choice
                break
    if picked is None:
        return _finish("That doesn't match any of the choices — "
                       "answer with the letter A–D or the exact "
                       "answer text.\n\n" + _render_question(state),
                       games, note, dirty=False)

    correct = picked == q["correct"]
    if correct:
        state["score"] += 1
        head = f"Correct — {picked} is right."
    else:
        head = (f"Not quite — you said {picked}; the correct "
                f"answer is {q['correct']}.")
    state["index"] += 1
    if state["index"] >= total:
        body = (head + f"\n\nThat's the round: you scored "
                f"{state['score']} of {total}. "
                + _trivia_verdict(state["score"], total))
    else:
        body = head + "\n\n" + _render_question(state)
    return _finish(body, games, note, dirty=True)
