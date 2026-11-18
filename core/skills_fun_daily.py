from datetime import date, datetime, timedelta
from .memory import episodes  # Phase 3 M1b: moved to core/memory/
from . import google_services
from .brain import planner  # Phase 3 B2-remainder: planner moved to core/brain/
from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
from .scheduler import scheduler
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_fun_helpers import (
    _user_first_name,
)


# ---------------------------------------------------------------------------
# WORD OF THE DAY — a built-in bank, the same word all day
# ---------------------------------------------------------------------------

# (word, part of speech, meaning, example template). Examples use
# simple past tense so "{name}" can be "You" when no name is known.
_WORDS = (
    ("petrichor", "noun", "the earthy scent after rain falls on dry soil",
     "After the long dry spell broke, {name} stepped outside just to breathe in the petrichor."),
    ("serendipity", "noun", "finding something good by happy accident, while looking for something else",
     "At the flea market, {name} stumbled on serendipity — the exact record that completed the collection."),
    ("mellifluous", "adjective", "sweetly flowing, like honey, of a sound or voice",
     "{name} listened to the mellifluous old recording three times in a row."),
    ("ephemeral", "adjective", "lasting a very short time",
     "{name} caught the ephemeral rainbow on camera just before it faded."),
    ("sonder", "noun", "the realisation that every passing stranger has a life as vivid as your own",
     "On the crowded platform, {name} felt a sudden sonder and smiled at no one in particular."),
    ("apricity", "noun", "the warmth of the sun on a cold winter's day",
     "{name} closed both eyes and enjoyed the apricity on the park bench."),
    ("psithurism", "noun", "the sound of wind moving through trees",
     "{name} fell asleep to the psithurism outside the cabin window."),
    ("susurrus", "noun", "a soft, whispering, rustling sound",
     "From the tall grass came a susurrus that made {name} stop and listen."),
    ("halcyon", "adjective", "calm and peaceful, especially of a fondly remembered past time",
     "{name} spent the halcyon afternoon doing gloriously little."),
    ("ineffable", "adjective", "too great or beautiful to describe in words",
     "At the summit, {name} stood in ineffable silence for a full minute."),
    ("ebullient", "adjective", "bursting with cheerful energy and enthusiasm",
     "The ebullient coach had {name} believing the comeback was possible."),
    ("gossamer", "noun", "a fine, filmy substance — cobwebs, or anything equally delicate",
     "{name} brushed a thread of gossamer from the gate and watched it drift away."),
    ("lilt", "noun", "a cheerful rising-and-falling rhythm in a voice or tune",
     "The lilt in the storyteller's voice kept {name} awake past midnight."),
    ("quixotic", "adjective", "romantically idealistic, regardless of practicality",
     "{name} made the quixotic decision to restore the old bicycle by hand."),
    ("tintinnabulation", "noun", "the ringing or tinkling sound of bells",
     "Across the valley came the tintinnabulation of the chapel bells, and {name} counted them."),
    ("vellichor", "noun", "the strange wistfulness of used bookshops, full of books you'll never have time to read",
     "A wave of vellichor stopped {name} in the doorway of the old bookshop."),
    ("phosphene", "noun", "the swirling light patterns you see when you rub your closed eyes",
     "{name} rubbed tired eyes and watched the phosphene patterns bloom and fade."),
    ("crepuscular", "adjective", "of twilight, or active at twilight",
     "The crepuscular deer crossed the field as {name} watched from the porch."),
    ("sidereal", "adjective", "relating to the stars, or measured by them",
     "{name} set the telescope by sidereal time and waited for the sky to turn."),
    ("oneiric", "adjective", "relating to dreams, dreamlike",
     "The film's oneiric second half left {name} quietly unsettled."),
    ("labyrinthine", "adjective", "complicated and twisting, like a maze",
     "{name} navigated the labyrinthine old town without once checking a map."),
    ("verdant", "adjective", "green with lush, growing plants",
     "After the rains, {name} found the hillside completely verdant."),
    ("zephyr", "noun", "a soft, gentle breeze",
     "A zephyr crossed the lake and set the little boat rocking as {name} dozed."),
    ("scintilla", "noun", "a tiny trace or spark of something",
     "{name} could not find a scintilla of evidence for the rumour."),
    ("dulcet", "adjective", "sweet and soothing, usually of a sound",
     "The dulcet tones of the cello drifted over the wall to where {name} sat reading."),
    ("opalescent", "adjective", "showing shifting, rainbow-like colours",
     "{name} turned the opalescent shell over and over in the light."),
    ("peregrinate", "verb", "to travel or wander from place to place",
     "Each spring, {name} liked to peregrinate through villages nobody had heard of."),
    ("solace", "noun", "comfort or consolation in a time of sorrow",
     "{name} found solace in long walks and longer letters."),
    ("fika", "noun", "a Swedish coffee-and-cake break taken to slow down and connect",
     "{name} declared an official fika and put the kettle on for everyone."),
    ("hygge", "noun", "a Danish sense of cosiness and contented well-being",
     "With candles lit and soup on, {name} achieved peak hygge."),
    ("komorebi", "noun", "a Japanese word for sunlight filtering through the leaves of trees",
     "{name} photographed the komorebi on the forest path for ten quiet minutes."),
    ("tsundoku", "noun", "a Japanese word for buying books and letting them pile up unread",
     "{name} surveyed the tsundoku stack and ordered one more book anyway."),
    ("fernweh", "noun", "a German word for a longing for far-off places you've never been",
     "The map on the wall gave {name} a serious case of fernweh."),
    ("saudade", "noun", "a Portuguese word for a deep, wistful longing for something absent",
     "The old song filled {name} with saudade for the summers of childhood."),
    ("meraki", "noun", "a Greek word for doing something with soul, creativity and love",
     "{name} cooked the whole meal with meraki, and it showed."),
    ("ubuntu", "noun", "a southern African philosophy: 'I am because we are' — humanity toward others",
     "{name} quoted ubuntu when the whole street turned out to help."),
    ("wabi-sabi", "noun", "a Japanese idea of beauty in imperfection and impermanence",
     "{name} kept the cracked, mended bowl for its wabi-sabi."),
    ("lagom", "noun", "a Swedish word for just the right amount — not too much, not too little",
     "{name} seasoned the soup to lagom and refused to add another pinch."),
    ("gezellig", "adjective", "a Dutch word for cosy, warm and convivial",
     "The tiny café was so gezellig that {name} stayed for a second coffee."),
    ("resplendent", "adjective", "shining brilliantly; splendid in appearance",
     "{name} arrived resplendent in a coat the colour of traffic lights."),
    ("limerence", "noun", "the state of being intensely, involuntarily infatuated with someone",
     "{name} recognised the limerence for what it was and took a deep breath."),
)


def word_of_the_day() -> str:
    """One interesting word for today, from the built-in bank —
    the same word all day, a new one tomorrow."""
    today = date.today()
    word, pos, meaning, example = _WORDS[today.toordinal() % len(_WORDS)]
    name = _user_first_name()
    example_text = example.replace("{name}", name or "You")
    lines = [f"Today's word is '{word}' ({pos}): {meaning}.",
             f"In a sentence: {example_text}"]
    if name:
        lines.append("(The example uses your name — it's in my "
                     "memory.)")
    saved = False
    try:
        saved = bool(memory_store.save_fact(
            "learned", f"word of the day {today.isoformat()}",
            f"{word} ({pos}) — {meaning}"))
    except Exception:  # noqa: BLE001 — presenting the word is the job
        saved = False
    if saved:
        lines.append("I've saved it to memory as today's word.")
    return "\n".join(lines)



# ---------------------------------------------------------------------------
# COMPLIMENT ENGINE — specific, and honest about its sources
# ---------------------------------------------------------------------------

_GENERIC_COMPLIMENTS = (
    "You ask good questions and actually listen to the answers — "
    "that combination is rarer than you think.",
    "You've built a life with real range in it, and you keep "
    "showing up for it. That counts.",
    "Somewhere today, something went more smoothly because you "
    "were part of it. That's just how you operate.",
    "You have the good kind of stubborn — the kind that finishes "
    "things.",
)

_COMPLIMENT_TEMPLATES = {
    "plan": (
        "You finished every step of '{goal}' — all {n} step(s), "
        "done and dusted. Most plans quietly die at step two; "
        "yours didn't. Genuinely well done."),
    "focus": (
        "You completed {n} Pomodoro focus round(s) today and "
        "stayed with each one right through to the bell. That's "
        "deep work, honestly earned — nicely done."),
    "journal": (
        "Your journal holds {n} {ies} from the last three "
        "days — you've been showing up and getting things down. "
        "Consistency like that quietly compounds."),
}


def _recent_achievements() -> list:
    """Real, checkable achievements from the product's seams, in
    preference order: [(kind, details-dict), ...]. Anything that
    can't be read simply contributes nothing — never a guess."""
    found = []
    try:
        plans, _damaged = planner._load_plans()
        for plan in plans:
            done, total = planner._progress(plan)
            if total and done == total:
                found.append(("plan", {
                    "goal": str(plan.get("goal", "a plan")),
                    "n": total}))
    except Exception:  # noqa: BLE001
        pass
    try:
        today_str = date.today().isoformat()
        # Scheduler batch: fired one-shots are purged from the
        # active store on firing and live in fired history, so
        # the today-count reads history (get_all_reminders()
        # records never carry fired=True anymore). The history
        # records carry the same label/fire_at shape, so the
        # filter below is unchanged.
        history = scheduler.get_fired_history()
        fired = [r for r in history
                 if str(r.get("label", "")).startswith("Pomodoro focus")
                 and str(r.get("fire_at", "")).startswith(today_str)]
        if fired:
            found.append(("focus", {"n": len(fired)}))
    except Exception:  # noqa: BLE001
        pass
    try:
        entries = 0
        for offset in range(3):
            entries += len(episodes.day(
                date.today() - timedelta(days=offset)))
        if entries >= 3:
            found.append(("journal", {
                "n": entries,
                "ies": "entry" if entries == 1 else "entries"}))
    except Exception:  # noqa: BLE001
        pass
    return found


def compliment_engine() -> str:
    """A sincere compliment naming a real recent achievement —
    or a plainly-labelled general one when none can be found."""
    achievements = _recent_achievements()
    if achievements:
        kind, details = achievements[0]
        text = _COMPLIMENT_TEMPLATES[kind].format(**details)
        extra = ""
        if len(achievements) > 1:
            extra = (" (There's more where that came from — ask "
                     "again and I'll pick another.)")
        return text + extra
    generic = _GENERIC_COMPLIMENTS[
        date.today().toordinal() % len(_GENERIC_COMPLIMENTS)]
    return ("This one's a general compliment, not based on "
            "anything specific — I checked your plans, today's "
            "focus rounds and your journal, and found nothing "
            f"recent to point at. Even so: {generic}")



# ---------------------------------------------------------------------------
# QUOTE OF THE MORNING — a built-in bank of public-domain classics
# ---------------------------------------------------------------------------

# Every entry is widely documented; comments mark whether the
# wording is verbatim from a public-domain source or a popular
# rendering carried under "attributed to". Nothing modern, nothing
# fetched.
_QUOTES = {
    "general": (
        # Walden (1854) — verbatim.
        ("I went to the woods because I wished to live deliberately.",
         "Henry David Thoreau"),
        # Popular rendering, traditionally attributed to Seneca.
        ("Luck is what happens when preparation meets opportunity.",
         "attributed to Seneca"),
        # Widely documented attribution in Twain quote collections.
        ("The man who does not read has no advantage over the man "
         "who cannot read.", "attributed to Mark Twain"),
    ),
    "work": (
        # 'Citizenship in a Republic', Paris, 1910 — verbatim.
        ("The credit belongs to the man who is actually in the arena.",
         "Theodore Roosevelt"),
        # Poor Richard's Almanack — verbatim.
        ("Well done is better than well said.", "Benjamin Franklin"),
        # Will Durant's summary of Aristotle, The Story of
        # Philosophy (1926) — credited to the summariser, honestly.
        ("We are what we repeatedly do. Excellence, then, is not "
         "an act, but a habit.", "Will Durant, summarising Aristotle"),
    ),
    "travel": (
        # Popular rendering of Tao Te Ching ch. 64.
        ("A journey of a thousand miles begins with a single step.",
         "attributed to Laozi"),
        # Thoreau's journal — widely quoted verbatim.
        ("An early-morning walk is a blessing for the whole day.",
         "Henry David Thoreau"),
        # Traditional attribution; not verbatim in Augustine's works.
        ("The world is a book, and those who do not travel read "
         "only a page.", "attributed to Saint Augustine"),
    ),
    "family": (
        # Emma (1815) — verbatim.
        ("There is no charm equal to tenderness of heart.",
         "Jane Austen"),
        # Widely documented attribution.
        ("What do we live for, if it is not to make life less "
         "difficult for each other?", "attributed to George Eliot"),
        # Documented in Herndon's recollections of Lincoln.
        ("I remember my mother's prayers and they have always "
         "followed me. They have clung to me all my life.",
         "Abraham Lincoln"),
    ),
    "health": (
        # Poor Richard's Almanack — verbatim.
        ("Early to bed and early to rise makes a man healthy, "
         "wealthy, and wise.", "Benjamin Franklin"),
        # Popular rendering of Letters to Lucilius 13.
        ("We suffer more often in imagination than in reality.",
         "attributed to Seneca"),
        # Thoreau's journal — widely quoted verbatim.
        ("An early-morning walk is a blessing for the whole day.",
         "Henry David Thoreau"),
    ),
    "study": (
        # Widely documented attribution.
        ("An investment in knowledge pays the best interest.",
         "attributed to Benjamin Franklin"),
        # Traditional attribution, popular rendering.
        ("It does not matter how slowly you go as long as you do "
         "not stop.", "attributed to Confucius"),
        # From the notebooks, widely documented attribution.
        ("Learning never exhausts the mind.",
         "attributed to Leonardo da Vinci"),
    ),
    "celebration": (
        # Poem 254 — verbatim.
        ("Hope is the thing with feathers that perches in the soul.",
         "Emily Dickinson"),
        # As You Like It, Act II — verbatim.
        ("All the world's a stage, and all the men and women "
         "merely players.", "William Shakespeare"),
        # Widely documented attribution.
        ("Keep your face always toward the sunshine — and shadows "
         "will fall behind you.", "attributed to Walt Whitman"),
    ),
}

_THEME_KEYWORDS = (
    ("celebration", ("birthday", "party", "celebration",
                     "anniversary", "wedding", "festival",
                     "graduation", "christening")),
    ("travel", ("flight", "train", "trip", "travel", "drive",
                "airport", "hotel", "journey", "visit to",
                "flight to")),
    ("health", ("doctor", "dentist", "gym", "workout", "yoga",
                "clinic", "therapy", "check-up", "checkup",
                "hospital", "physio", "vaccination")),
    ("study", ("class", "exam", "lecture", "study", "course",
               "assignment", "homework", "tutorial", "revision")),
    ("family", ("family", "mother", "father", "parents", "sister",
                "brother", "kids", "grandma", "grandpa", "mum",
                "dad")),
    ("work", ("meeting", "work", "office", "call", "interview",
              "presentation", "deadline", "review", "standup",
              "client", "project", "sync")),
)


def _first_event_today():
    """Today's first calendar event as a dict, or None — via
    google_services.next_events (never raises; empty list when
    Google isn't connected). Monkeypatchable."""
    try:
        events = google_services.next_events(within_hours=24,
                                             limit=20)
    except Exception:  # noqa: BLE001
        return None
    today = date.today()
    for event in events or []:
        start = event.get("start")
        if isinstance(start, datetime) and start.date() == today:
            return event
    return None


def _event_theme(title: str) -> str | None:
    lowered = (title or "").lower()
    for theme, keywords in _THEME_KEYWORDS:
        if any(word in lowered for word in keywords):
            return theme
    return None


def quote_of_morning() -> str:
    """One public-domain quote, matched to the theme of today's
    first calendar event when there is one."""
    event = _first_event_today()
    theme = _event_theme(str(event.get("title", ""))) if event else None
    if theme:
        bank = _QUOTES[theme]
        text, author = bank[date.today().toordinal() % len(bank)]
        return (f'"{text}" — {author}.\nThat one matches this '
                f'morning\'s \'{event.get("title", "")}\' (a {theme} '
                f"theme). It's from my built-in bank of "
                f"public-domain quotes — nothing was fetched.")
    bank = _QUOTES["general"]
    text, author = bank[date.today().toordinal() % len(bank)]
    reason = ("your calendar's clear this morning"
              if event is None else
              "today's first event didn't match a theme")
    return (f'"{text}" — {author}.\nA general one today, because '
            f"{reason}. It's from my built-in bank of "
            f"public-domain quotes — nothing was fetched.")
