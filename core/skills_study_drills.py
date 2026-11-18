"""Study skills impl — drills group (split from skills_study.py).
"""


import datetime
import difflib
import random
import re

# INTENTIONAL: cross-part names — drills resolve these bare names in
# the helpers module's globals (bare-name rule, plan §2 P3), not the facade's.
from .skills_study_helpers import _answers_total, _cards, _state_get, _state_set


# ---------------------------------------------------------------------------
# QUIZ — four options from the deck, answer checked by
# flashcard_answer
# ---------------------------------------------------------------------------

_QUIZ_LETTERS = ("A", "B", "C", "D")


def quiz_me() -> str:
    """Build a 4-option multiple-choice question from the saved
    flashcards and stash its correct letter for grading."""
    try:
        cards = _cards()
        if len(cards) < 4:
            return (f"Quizzes need at least 4 flashcards and "
                    f"you have {len(cards)} — add "
                    f"{4 - len(cards)} more with flashcard_add "
                    "and I'll build one.")
        rng = random.Random(
            f"visor-quiz|{datetime.date.today().isoformat()}|"
            f"{len(cards)}|{_answers_total()}")
        card = rng.choice(cards)
        others = [c for c in cards if c["front"] != card["front"]]
        distractors = rng.sample(others, 3)
        options = [card["back"]] + [d["back"] for d in distractors]
        rng.shuffle(options)
        letter = _QUIZ_LETTERS[options.index(card["back"])]
        _state_set("quiz_answer", letter)
        _state_set("quiz_front", card["front"])
    except Exception as e:  # noqa: BLE001
        return f"Couldn't build a quiz question: {e}"
    lines = [f"Quiz: which of these matches '{card['front']}'?"]
    for mark, option in zip(_QUIZ_LETTERS, options):
        lines.append(f"{mark}) {option}")
    lines.append("Answer with flashcard_answer and the letter "
                 "(or the full answer).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MNEMONIC MAKER — first letters plus template sentences from a
# small letter→word map; rule-based only
# ---------------------------------------------------------------------------

_MNEMONIC_WORDS = {
    "A": ("apples", "astronauts", "anchors"),
    "B": ("bears", "bakers", "balloons"),
    "C": ("cats", "captains", "candles"),
    "D": ("dogs", "dancers", "dragons"),
    "E": ("eagles", "engines", "echoes"),
    "F": ("foxes", "farmers", "flags"),
    "G": ("goats", "gardeners", "guitars"),
    "H": ("horses", "heroes", "harbours"),
    "I": ("islands", "icicles", "inventors"),
    "J": ("jaguars", "jugglers", "journeys"),
    "K": ("kittens", "knights", "kites"),
    "L": ("lions", "ladders", "lanterns"),
    "M": ("monkeys", "mountains", "musicians"),
    "N": ("nightingales", "needles", "navigators"),
    "O": ("owls", "oceans", "orchards"),
    "P": ("penguins", "painters", "parrots"),
    "Q": ("queens", "quilts", "quarries"),
    "R": ("rabbits", "rivers", "rockets"),
    "S": ("snakes", "sailors", "stars"),
    "T": ("tigers", "towers", "turtles"),
    "U": ("unicorns", "umbrellas", "umpires"),
    "V": ("violets", "voyagers", "valleys"),
    "W": ("wolves", "wizards", "waterfalls"),
    "X": ("xylophones", "x-rays", "xenops"),
    "Y": ("yaks", "yogis", "yachts"),
    "Z": ("zebras", "zephyrs", "zookeepers"),
}
_MNEMONIC_CONNECTORS = ("chase", "follow", "visit")
_MAX_MNEMONIC_ITEMS = 12


def mnemonic_maker(words: str = "") -> str:
    """Turn a list of items into their first letters plus three
    template mnemonic sentences built from the letter→word map."""
    raw = str(words or "").strip()
    if not raw:
        return ("Give me the items to remember, in order — "
                "mnemonic_maker with something like 'Mercury, "
                "Venus, Earth, Mars'.")
    items = [part.strip() for part in raw.split(",")
             if part.strip()]
    if len(items) <= 1:
        items = raw.split()
    items = [item for item in items if item]
    if not items:
        return "I couldn't find any items in that — try a comma-separated list."
    trimmed = len(items) > _MAX_MNEMONIC_ITEMS
    items = items[:_MAX_MNEMONIC_ITEMS]
    letters = "".join(item[0].upper() for item in items
                      if item[0].isalpha())
    sentences = []
    for variant in range(3):
        parts = []
        for index, item in enumerate(items):
            choices = _MNEMONIC_WORDS.get(item[0].upper())
            if choices:
                word = choices[(variant + index) % len(choices)]
                parts.append(word.capitalize()
                             if index == 0 else word)
            else:
                parts.append(item.capitalize()
                             if index == 0 else item)
        verb = _MNEMONIC_CONNECTORS[variant]
        if len(parts) > 2:
            sentence = (f"{parts[0]} {verb} " + ", ".join(
                parts[1:-1]) + f" and {parts[-1]}")
        elif len(parts) == 2:
            sentence = f"{parts[0]} {verb} {parts[1]}"
        else:
            sentence = parts[0]
        sentences.append(sentence + ".")
    lines = [
        f"Items: {', '.join(items)}",
        f"First letters: {letters}",
        "Mnemonic ideas:",
    ]
    lines.extend(f"{n}. {s}" for n, s in enumerate(sentences, 1))
    if trimmed:
        lines.append(f"(Only the first {_MAX_MNEMONIC_ITEMS} "
                     "items were used.)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SPELLING BEE — the day's word from a built-in list, checked by
# flashcard_answer
# ---------------------------------------------------------------------------

_BEE_WORDS = (
    ("occurrence", "An incident or event; the fact of something happening.",
     "The occurrence of snow in April surprised everyone."),
    ("rhythm", "A regular pattern of beats or movement.",
     "The drummer kept a steady rhythm throughout the song."),
    ("necessary", "Needed; required for a purpose.",
     "It is necessary to bring your ID to the exam."),
    ("embarrass", "To make someone feel awkward or ashamed.",
     "He did not want to embarrass his friend in front of the class."),
    ("accommodate", "To provide space for someone, or to adjust to fit needs.",
     "The hall can accommodate up to three hundred guests."),
    ("questionnaire", "A written set of questions used to gather information.",
     "Please fill in the questionnaire before you leave."),
    ("maintenance", "The work of keeping something in good condition.",
     "Regular maintenance keeps the old bicycle running smoothly."),
    ("conscience", "The inner sense of what is right and wrong.",
     "Her conscience would not let her keep the money."),
    ("phenomenon", "Something that is observed to happen, often unusual.",
     "The northern lights are a natural phenomenon."),
    ("silhouette", "A dark outline of a shape against a lighter background.",
     "The silhouette of the castle stood against the sunset."),
    ("bureaucracy", "Administration through many officials and rules.",
     "Getting the permit took weeks because of the bureaucracy."),
    ("kaleidoscope", "A tube of mirrors and coloured pieces making changing patterns.",
     "The garden was a kaleidoscope of colour in spring."),
    ("miscellaneous", "Made up of various kinds of things.",
     "He kept screws and buttons in a miscellaneous drawer."),
    ("pronunciation", "The way a word is spoken.",
     "Her pronunciation of French words was excellent."),
    ("restaurant", "A place where meals are prepared and served to customers.",
     "We booked a table at the new restaurant for Friday."),
    ("separate", "To divide into different parts, or not joined together.",
     "Please separate the recyclables from the general waste."),
    ("tomorrow", "The day after today.",
     "The results will be announced tomorrow morning."),
    ("vocabulary", "All the words a person knows or a language contains.",
     "Reading every day steadily grows your vocabulary."),
    ("withhold", "To refuse to give something that is due.",
     "The bank may withhold payment until the form is signed."),
    ("yield", "To produce or give way under pressure.",
     "These fields yield a good harvest most years."),
    ("zealous", "Full of enthusiasm and energy for a cause.",
     "She is a zealous supporter of the local library."),
    ("harass", "To bother or trouble someone repeatedly.",
     "The helpline advises people who harass callers will be blocked."),
    ("guarantee", "A firm promise that something will happen or be done.",
     "The watch comes with a two-year guarantee."),
    ("independent", "Free from outside control; not relying on others.",
     "She has been financially independent since college."),
    ("millennium", "A period of one thousand years.",
     "The bridge has stood for over a millennium in legend."),
    ("occasionally", "Sometimes, but not often.",
     "We occasionally meet for coffee on Sundays."),
    ("possession", "Something that is owned; the state of owning.",
     "The old map was her most treasured possession."),
    ("recommend", "To suggest something as being good or suitable.",
     "I recommend the tomato soup at that café."),
    ("threshold", "The entrance to a room, or the level at which something starts.",
     "He paused at the threshold before entering."),
    ("weird", "Strange or unusual.",
     "A weird noise came from the attic last night."),
)


def spelling_bee() -> str:
    """Give the day's spelling word by its definition and a
    gapped example; the word itself is stashed for grading."""
    today = datetime.date.today()
    word, definition, example = _BEE_WORDS[
        today.toordinal() % len(_BEE_WORDS)]
    try:
        _state_set("bee_word", word)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't start the spelling bee: {e}"
    gapped = re.sub(re.escape(word), "_____", example,
                    flags=re.IGNORECASE)
    return (f"Spelling bee — today's word means: {definition}\n"
            f"Example: {gapped}\n"
            "Spell it with flashcard_answer — letter-perfect "
            "counts.")


# ---------------------------------------------------------------------------
# VOCAB WORD — the day's word from a built-in list, no state
# ---------------------------------------------------------------------------

_VOCAB_WORDS = (
    ("serendipity", "Finding something good by happy accident.",
     "By pure serendipity, she bumped into her old friend at the airport."),
    ("ephemeral", "Lasting for a very short time.",
     "The beauty of cherry blossom is ephemeral."),
    ("luminous", "Full of or shedding light; bright.",
     "The luminous dial glowed in the dark room."),
    ("meticulous", "Showing great attention to detail; very careful.",
     "He kept meticulous notes of every experiment."),
    ("resilient", "Able to recover quickly from difficulties.",
     "Children are often remarkably resilient."),
    ("tranquil", "Free from disturbance; calm.",
     "The tranquil lake reflected the mountains perfectly."),
    ("vivid", "Producing powerful feelings or strong, clear images.",
     "She gave a vivid account of her travels."),
    ("wanderlust", "A strong desire to travel and explore.",
     "His wanderlust took him to thirty countries."),
    ("eloquent", "Fluent and persuasive in speaking or writing.",
     "The eloquent speech moved the whole audience."),
    ("harmony", "Agreement and peaceful coexistence; musical togetherness.",
     "The team worked in harmony all season."),
    ("nostalgia", "A sentimental longing for the past.",
     "The old photograph filled her with nostalgia."),
    ("pristine", "In its original condition; unspoiled.",
     "The beach was pristine, without a single footprint."),
    ("quintessential", "Representing the most perfect example of something.",
     "He is the quintessential gentleman — polite to everyone."),
    ("radiant", "Sending out light; glowing with health or happiness.",
     "She was radiant on her graduation day."),
    ("solace", "Comfort in a time of sadness.",
     "He found solace in long evening walks."),
    ("tenacity", "The quality of holding firmly to a goal; persistence.",
     "Her tenacity carried the project through every setback."),
    ("ubiquitous", "Present or found everywhere.",
     "Mobile phones are now ubiquitous."),
    ("verbose", "Using more words than are needed.",
     "The report was verbose and hard to follow."),
    ("whimsical", "Playfully unusual or fanciful.",
     "The garden was full of whimsical statues."),
    ("zenith", "The highest point; the peak.",
     "The sun reached its zenith at noon."),
    ("arduous", "Requiring a lot of effort; strenuous.",
     "Climbing the pass was an arduous trek."),
    ("benevolent", "Well-meaning and kindly.",
     "A benevolent donor paid for the new library."),
    ("candid", "Truthful and straightforward; frank.",
     "She gave a candid answer about the mistake."),
    ("diligent", "Hard-working and careful in one's duties.",
     "The diligent student revised every topic twice."),
)


def vocab_word() -> str:
    """Give the day's vocabulary word with its meaning and an
    example sentence."""
    today = datetime.date.today()
    word, meaning, example = _VOCAB_WORDS[
        today.toordinal() % len(_VOCAB_WORDS)]
    return (f"Word of the day: {word} — {meaning}\n"
            f"Example: {example}")


# ---------------------------------------------------------------------------
# TYPING TEST — a sentence to copy against the clock
# ---------------------------------------------------------------------------

_TYPING_SENTENCES = (
    "The quick brown fox jumps over the lazy dog.",
    "Practice makes progress, and steady practice makes speed.",
    "A journey of a thousand miles begins with a single step.",
    "The five boxing wizards jump quickly at dusk.",
    "Good typing is quiet, accurate and relaxed.",
    "Pack my box with five dozen liquor jugs.",
    "How vexingly quick daft zebras jump at noon.",
    "Bright vixens jump; dozy fowl quack at midnight.",
)


def typing_test(action: str = "start", typed_text: str = "",
                seconds=0) -> str:
    """Start a typing test or score one: words per minute and
    accuracy against the sentence that was set."""
    action = str(action or "start").strip().lower()
    if action not in ("start", "score"):
        return ("Typing test actions are 'start' (get a "
                "sentence) and 'score' (send what you typed plus "
                "the seconds it took).")
    if action == "start":
        today = datetime.date.today()
        sentence = _TYPING_SENTENCES[
            today.toordinal() % len(_TYPING_SENTENCES)]
        try:
            _state_set("typing_sentence", sentence)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't start the typing test: {e}"
        return (f"Type this sentence exactly:\n{sentence}\n"
                "Time yourself, then call typing_test with "
                "action 'score', your typed text and the "
                "seconds it took.")
    # action == "score"
    try:
        target = _state_get("typing_sentence").strip()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the typing test state: {e}"
    if not target:
        return ("No typing test is running — start one with "
                "typing_test, action 'start', and I'll set you "
                "a sentence.")
    typed = str(typed_text or "").strip()
    if not typed:
        return "I need what you typed to score it — send it as typed_text."
    try:
        elapsed = float(seconds)
    except (TypeError, ValueError):
        return ("I need the time as a number of seconds to "
                "work out your speed.")
    if elapsed <= 0:
        return ("The time has to be more than zero seconds — "
                "time the attempt and send the real figure.")
    minutes = elapsed / 60.0
    gross_wpm = (len(typed) / 5.0) / minutes
    accuracy = difflib.SequenceMatcher(
        None, target, typed).ratio() * 100.0
    exact = typed == target
    verdict = ("Perfect copy. " if exact else "")
    return (f"{verdict}You typed {len(typed.split())} words "
            f"({len(typed)} characters) in {elapsed:g} seconds: "
            f"{gross_wpm:.0f} words per minute at "
            f"{accuracy:.0f}% accuracy against the set "
            "sentence.")


# ---------------------------------------------------------------------------
# READING LEVEL — the Flesch scores over a vowel-group syllable
# count; an estimate, and it says so
# ---------------------------------------------------------------------------

_MAX_READING_CHARS = 20000
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def _syllables(word: str) -> int:
    """A rough syllable count: vowel groups, minus a silent
    trailing 'e', at least one per word."""
    cleaned = word.lower().strip("'")
    if not cleaned:
        return 0
    count = len(_VOWEL_GROUP_RE.findall(cleaned))
    if cleaned.endswith("e") and count > 1 \
            and not cleaned.endswith(("le", "ee", "ye")):
        count -= 1
    return max(1, count)


def _reading_band(ease: float) -> str:
    if ease >= 90:
        return "very easy — most readers breeze through it"
    if ease >= 80:
        return "easy to read"
    if ease >= 70:
        return "fairly easy"
    if ease >= 60:
        return "standard — plain everyday English"
    if ease >= 50:
        return "fairly difficult — a careful read"
    if ease >= 30:
        return "difficult — dense, academic-style writing"
    return "very difficult — specialist material"


def reading_level(text: str = "") -> str:
    """Estimate how hard a piece of text is to read: Flesch
    reading ease, a grade estimate, and the counts behind them."""
    source = str(text or "")
    if not source.strip():
        return ("Give me the text to check — reading_level "
                "with the passage itself.")
    trimmed_note = ""
    if len(source) > _MAX_READING_CHARS:
        source = source[:_MAX_READING_CHARS]
        trimmed_note = (f" (Only the first "
                        f"{_MAX_READING_CHARS:,} characters were "
                        "checked.)")
    words = _WORD_RE.findall(source)
    if len(words) < 10:
        return ("That's too short to judge fairly — ten words "
                "or more gives the formulas something to work "
                "with.")
    sentences = [part for part in
                 _SENTENCE_SPLIT_RE.split(source)
                 if _WORD_RE.search(part)]
    sentence_count = max(1, len(sentences))
    word_count = len(words)
    syllable_count = sum(_syllables(word) for word in words)
    words_per_sentence = word_count / sentence_count
    syllables_per_word = syllable_count / word_count
    ease = (206.835 - 1.015 * words_per_sentence
            - 84.6 * syllables_per_word)
    grade = (0.39 * words_per_sentence
             + 11.8 * syllables_per_word - 15.59)
    ease = max(0.0, min(100.0, ease))
    return (f"Reading ease {ease:.0f} out of 100 — "
            f"{_reading_band(ease)}. Grade estimate: about "
            f"{max(0.0, grade):.1f} (US school grade). Based on "
            f"{word_count} words in {sentence_count} sentence"
            f"{'s' if sentence_count != 1 else ''} "
            f"({syllable_count} syllables counted). These are "
            f"the standard Flesch formulas over a rough syllable "
            f"count — an estimate, not a judgement of quality."
            f"{trimmed_note}")
