"""Settings redesign (2026-11-16): the plain-words layer for Settings.

Pure, stdlib-only registry — no Qt, no tree imports — so the
Settings page and the tests share one table and cannot drift.

Two tables, both keyed by the card title the Settings page
actually shows (see gui/window_settings*.py):

* ``CARD_SYNONYMS`` — plain words a person types or says, mapped
  to the card that answers them. The Settings search box matches
  these IN ADDITION to the card's own text, so "make her quieter"
  finds the Sounds card even though the word "quieter" appears
  nowhere on it. A phrase never invents a card: it only points
  at a title that exists.
* ``CARD_VOICE`` — the voice line every card carries ("You can
  also say: ..." or "Change this here — voice can't flip it,
  for safety"). Class of each card (the thinking doc §3):
  A standing permissions — voice OPENS the page, never flips
  (D8 stands); B ordinary preferences — voice can change via
  the §5 propose → confirm → announce + undo protocol;
  C secrets & connections — voice opens the Connect screen,
  the key goes in by typing; D destructive / reference —
  Settings only, no voice path at all.
* ``CARD_EFFECT`` — when a change on a card takes effect
  (Phase 4, D-P4-31): "Applies instantly." or "Takes effect
  the next time you connect." A card whose timing the build
  cannot establish is absent from the table and carries no
  line, never a guessed one.

``TOP_TASKS`` is the Settings-home row, in order: the five
things people actually change, each one tap to its card.
``TOP_TASK_TARGETS`` maps a task to the card title it opens
(Language lives on the "Local brain" Advanced card — the
language + Kokoro voice combos sit there, see
gui/window_settings_advbrain.py).
"""

from __future__ import annotations

# Voice classes (thinking doc §3).
CLASS_A = "A"  # standing permission — voice opens, never flips
CLASS_B = "B"  # ordinary preference — voice can change (protocol)
CLASS_C = "C"  # secret / connection — voice opens, key typed
CLASS_D = "D"  # destructive / reference — Settings only

# task -> card title it opens.
TOP_TASKS: tuple[tuple[str, str], ...] = (
    ("Mic", "Microphone"),
    ("Voice", "Voice"),
    ("Connect", "How VYUK thinks"),
    ("Theme", "Appearance"),
    ("Language", "Local brain"),
)

# Plain words -> card title. Every target is a real card title.
CARD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "Voice": (
        "voice", "her voice", "change her voice", "different voice",
        "sound of her voice", "pick a voice", "voice pick",
    ),
    "Whisper hours": (
        "quieter", "softer", "whisper", "quiet at night", "night volume",
        "make her quieter", "speak softer", "softer at night",
    ),
    # Stage 0 features batch 6 (2026-11-23): calendar-aware
    # quiet — the card answers meeting-quiet asks. Bare
    # "quieter" is deliberately NOT here: that means softer
    # speech (Whisper hours), not silence during meetings.
    "Quiet hours": (
        "quiet during meetings", "quiet in meetings",
        "meeting quiet", "quiet in a meeting",
        "silence during meetings", "do not disturb",
        "stop talking during meetings",
    ),
    "Briefing voice": (
        "briefing", "morning briefing", "briefing voice",
        "briefing pace", "briefing speed", "read the briefing",
        # §4b: "make her quieter" names the briefing voice too —
        # the briefing is spoken, and its preset is the quieter
        # briefing.
        "quieter", "make her quieter",
    ),
    "Microphone": (
        "mic", "microphone", "stop listening", "start listening",
        "she can't hear me", "can't hear me", "hear me",
        "microphone off", "microphone on", "mute", "unmute",
        "input device", "which mic", "pick a microphone",
    ),
    "Voice bridge": (
        "voice bridge", "voice goals", "free-form voice",
    ),
    "Sounds": (
        "sounds", "sound", "volume", "louder", "quieter",
        "make her quieter", "make her louder", "interface sounds",
        "earcons", "beeps", "sound effects", "turn the sound down",
        "turn the sound up",
    ),
    "Appearance": (
        "theme", "colour", "color", "colours", "colors", "dark",
        "light", "look", "appearance", "text size", "font size",
        "density", "motion", "glass", "particles", "change the color",
        "change the colour",
    ),
    "Presence": (
        "startup", "start with windows", "launch at startup",
        "mini orb", "floating orb", "status strip", "briefing time",
        "morning briefing time", "summon hotkey", "auto connect",
        "reconnect",
    ),
    "Notices": (
        "notices", "notifications", "notify", "reminders",
    ),
    "Suggestions": (
        "suggestions", "suggestion chips", "chips",
    ),
    "Privacy & security": (
        "privacy", "security", "continuous", "screen watching",
        "what she remembers", "memory",
    ),
    "Screen watching": (
        "screen", "watch my screen", "see my screen",
        "screen watching", "watching",
    ),
    "Browser control": (
        "browser", "browser control", "chrome", "web",
    ),
    # Phase 4: the Your world card was missing from the
    # registry — search could not find it.
    "Your world": (
        "your world", "world", "world connection",
        "connect to my world",
    ),
    "Audit": (
        "audit", "audit log", "what did she do", "history",
    ),
    "Remembered approvals": (
        "remembered approvals", "approvals", "always allow",
    ),
    "How VYUK thinks": (
        "connect", "cloud", "api key", "key", "gemini", "groq",
        "brain", "how she thinks", "connection", "fix the connection",
        "can't reach the cloud", "cloud key", "get a key",
    ),
    "Local brain": (
        "language", "reply language", "home language",
        "kokoro", "on-device voice", "local voice",
        "local brain", "on this pc", "offline brain",
        "transcription", "how she hears",
    ),
    "Your settings file": (
        "settings file", "export settings", "import settings",
    ),
    "Keyboard shortcuts": (
        "shortcuts", "keyboard", "hotkeys", "keys",
    ),
    "Theme packs": (
        "theme packs", "theme pack",
    ),
    "Settings store": (
        "settings store", "stored settings",
    ),
    "System status": (
        "system status", "status",
    ),
    "Doctor": (
        "doctor", "diagnose", "problem", "something wrong",
        "fix it", "checkup",
    ),
    "Report a problem": (
        "report a problem", "report", "bug",
    ),
    "Data portability": (
        "export my data", "my data", "take my data",
    ),
    "Models & storage": (
        "models", "storage", "disk", "download",
    ),
    "What's new": (
        "what's new", "changelog", "new features",
    ),
    "Power & network": (
        "power", "network", "battery",
    ),
    "About": (
        "about", "version",
    ),
}

# card title -> (class, voice line). Cards not listed here fall
# back to their class default (see card_voice_line).
CARD_VOICE: dict[str, tuple[str, str]] = {
    # E-F208: the example names a real offered voice (Aoede,
    # config.available_voices) instead of trailing off with an
    # ellipsis — "change her voice to Aoede" is a command the
    # product honours (app_settings live_voice matches the
    # spoken name against the offered list).
    "Voice": (CLASS_B, "You can also say: “change her voice to Aoede”."),
    "Whisper hours": (
        CLASS_B, "You can also say: “speak softer at night”."),
    # Stage 0 features batch 6 (2026-11-23): class B — voice
    # CAN flip calendar quiet through the §5 propose →
    # confirm protocol ("calendar quiet" / "meeting quiet").
    # Without this entry the card would fall back to the
    # class-D line ("Settings only — voice can't touch
    # this"), which would be false.
    "Quiet hours": (
        CLASS_B, "You can also say: “turn calendar quiet off”."),
    "Briefing voice": (
        CLASS_B, "You can also say: “slow the briefing down”."),
    "Microphone": (
        CLASS_A, "Change this here — voice can't flip it, for safety, "
        "and voice can't grant it either: grants are made on "
        "screen only, in Settings → Permissions."),
    "Voice bridge": (
        CLASS_B, "You can also say: “turn the voice bridge on”."),
    "Sounds": (
        CLASS_B, "You can also say: “turn the sounds off”."),
    "Appearance": (
        CLASS_B, "You can also say: “change the theme to Sunset”."),
    "Presence": (
        CLASS_B, "You can also say: “turn the morning briefing on”."),
    "Notices": (
        CLASS_B, "You can also say: “turn notices off”."),
    "Suggestions": (
        CLASS_B, "You can also say: “turn suggestion chips off”."),
    "Privacy & security": (
        CLASS_A, "Change this here — voice can't flip it, for safety."),
    "Screen watching": (
        CLASS_A, "Change this here — voice can't flip it, for safety. "
        "Voice can't grant it either — grants are made on screen "
        "only."),
    "Browser control": (
        CLASS_A, "Change this here — voice can't flip it, for safety. "
        "Voice can't grant it either — grants are made on screen "
        "only."),
    # Phase 4: the Your world card was missing from the
    # registry — search could not find it.
    "Your world": (
        CLASS_C, "Voice opens this screen — the connection codes "
        "go in by typing, never spoken."),
    "Audit": (
        CLASS_D, "Settings only — voice can't touch this."),
    "Remembered approvals": (
        CLASS_D, "Settings only — voice can't touch this."),
    "How VYUK thinks": (
        CLASS_C, "Voice opens this screen — the key goes in by "
        "typing, never spoken. Say “fix the connection” and VYUK "
        "opens it for you."),
    "Local brain": (
        CLASS_B, "You can also say: “change the language to Hindi”."),
    "Your settings file": (
        CLASS_D, "Settings only — voice can't touch this."),
    "Keyboard shortcuts": (
        CLASS_D, "Settings only — voice can't touch this."),
    "Theme packs": (
        CLASS_B, "You can also say: “change the theme to Sunset”."),
    "Settings store": (
        CLASS_D, "Settings only — voice can't touch this."),
    "System status": (
        CLASS_D, "Settings only — voice can't touch this."),
    "Doctor": (
        CLASS_D, "Settings only — voice can't touch this."),
    "Report a problem": (
        CLASS_D, "Settings only — voice can't touch this."),
    "Data portability": (
        CLASS_D, "Settings only — voice can't touch this."),
    "Models & storage": (
        CLASS_D, "Settings only — voice can't touch this."),
    "What's new": (
        CLASS_D, "Settings only — voice can't touch this."),
    "Power & network": (
        CLASS_D, "Settings only — voice can't touch this."),
    "About": (
        CLASS_D, "Settings only — voice can't touch this."),
}

_CLASS_DEFAULT_LINE = {
    CLASS_A: "Change this here — voice can't flip it, for safety.",
    CLASS_B: "You can also say it — ask her in plain words.",
    CLASS_C: "Voice opens this screen — secrets go in by typing, "
             "never spoken.",
    CLASS_D: "Settings only — voice can't touch this.",
}


def card_class(title: str) -> str:
    """The voice class of a card title (D when unlisted)."""
    entry = CARD_VOICE.get(str(title or ""))
    return entry[0] if entry else CLASS_D


def card_voice_line(title: str) -> str:
    """The voice line a card carries (class default when unlisted)."""
    entry = CARD_VOICE.get(str(title or ""))
    if entry:
        return entry[1]
    return _CLASS_DEFAULT_LINE[CLASS_D]


# Phase 4 (D-P4-31): when a change on a card takes effect.
# Two honest timings only — "Applies instantly." and "Takes
# effect the next time you connect." A card whose timing the
# build cannot establish is NOT in this table, and
# card_effect_line returns "" for it: the line stays hidden
# and the card is named as owed in the build report, never
# guessed. (Presence: autostart/connect-on-launch are
# launch-time while the rest of the card is live — one line
# cannot cover both. Your settings file: Export is one-shot
# immediate while Import applies per-setting.)
_EFFECT_INSTANT = "Applies instantly."
_EFFECT_NEXT_CONNECT = "Takes effect the next time you connect."

CARD_EFFECT: dict[str, str] = {
    # Next connect (3): the brain pick, the cloud provider pick
    # and the keys on How VYUK thinks, the voice pick on Voice,
    # and the engine/tool-profile/transcription knobs on Local
    # brain are all read at connect / brain load, not live.
    "How VYUK thinks": _EFFECT_NEXT_CONNECT,
    "Voice": _EFFECT_NEXT_CONNECT,
    "Local brain": _EFFECT_NEXT_CONNECT,
    # Applies instantly (30): the switch, the save, the
    # connect/disconnect or the control acts at once — the
    # card's own hint or label already says so where it
    # matters (Microphone's wake phrase is the restart
    # exception, labelled inline on the card itself).
    "A choice about watching what you do": _EFFECT_INSTANT,
    "About": _EFFECT_INSTANT,
    "Appearance": _EFFECT_INSTANT,
    "Audit": _EFFECT_INSTANT,
    "Backup passphrase": _EFFECT_INSTANT,
    "Briefing voice": _EFFECT_INSTANT,
    "Browser control": _EFFECT_INSTANT,
    "Data portability": _EFFECT_INSTANT,
    "Doctor": _EFFECT_INSTANT,
    "Feedback & crash reports": _EFFECT_INSTANT,
    "Home hub": _EFFECT_INSTANT,
    "Keyboard shortcuts": _EFFECT_INSTANT,
    "Microphone": _EFFECT_INSTANT,
    "Models & storage": _EFFECT_INSTANT,
    "Notices": _EFFECT_INSTANT,
    "Permissions": _EFFECT_INSTANT,
    "Power & network": _EFFECT_INSTANT,
    "Privacy & security": _EFFECT_INSTANT,
    "Quiet hours": _EFFECT_INSTANT,
    "Remembered approvals": _EFFECT_INSTANT,
    "Report a problem": _EFFECT_INSTANT,
    "Screen watching": _EFFECT_INSTANT,
    "Settings store": _EFFECT_INSTANT,
    "Sounds": _EFFECT_INSTANT,
    "Suggestions": _EFFECT_INSTANT,
    "System status": _EFFECT_INSTANT,
    "Theme packs": _EFFECT_INSTANT,
    "Voice bridge": _EFFECT_INSTANT,
    "What's new": _EFFECT_INSTANT,
    "Whisper hours": _EFFECT_INSTANT,
    "Your world": _EFFECT_INSTANT,
}


def card_effect_line(title: str) -> str:
    """The effect line a card carries ("" when the timing is
    not established — the line stays hidden, never guessed)."""
    return CARD_EFFECT.get(str(title or ""), "")


def card_synonyms(title: str) -> tuple[str, ...]:
    """The plain-words phrases that find a card title."""
    return CARD_SYNONYMS.get(str(title or ""), ())


def match_cards(query: str, titles) -> list[str]:
    """Card titles matching a plain-words query, in given order.

    A title matches when the query (lowercased, stripped) is a
    substring of the title, of any of its synonyms, or when any
    synonym is a substring of the query ("make her quieter"
    contains "quieter"). An empty query matches everything.
    Pure — no Qt, no store.
    """
    q = (query or "").strip().lower()
    titles = [str(t) for t in (titles or [])]
    if not q:
        return list(titles)
    out = []
    for title in titles:
        low = title.lower()
        if q in low:
            out.append(title)
            continue
        for phrase in CARD_SYNONYMS.get(title, ()):
            if q in phrase or phrase in q:
                out.append(title)
                break
    return out


# Relevance tiers (round-2 N3): a title match outranks a
# body/toggle match, so the card the user means is on top
# instead of whichever group comes first in the page. Lower
# is better. Tier 1 (exact synonym) beats tier 2 (title
# contains) on purpose: "theme" means the Appearance card
# (the theme setting), not the "Theme packs" card that merely
# has "theme" in its title.
TIER_TITLE_EXACT = 0
TIER_SYNONYM_EXACT = 1
TIER_TITLE_CONTAINS = 2
TIER_PHRASE = 3
TIER_BODY = 4


def match_tier(query: str, title: str, hay: str = "") -> int | None:
    """Relevance tier of one card for a query (0 = best), or
    None when the card does not match at all. The match itself
    is exactly match_cards plus the card's own text (hay): a
    card matches when the query is in its title, in any of its
    synonyms (either direction), or in its text — so the
    visible SET never changes, only the order. Pure — no Qt,
    no store."""
    q = (query or "").strip().lower()
    title = str(title or "")
    if not q:
        return None
    low = title.lower()
    if q == low:
        return TIER_TITLE_EXACT
    syns = [s.lower() for s in CARD_SYNONYMS.get(title, ())]
    if q in syns:
        return TIER_SYNONYM_EXACT
    if q in low:
        return TIER_TITLE_CONTAINS
    for s in syns:
        if q in s or s in q:
            return TIER_PHRASE
    if q in (hay or "").lower():
        return TIER_BODY
    return None


def rank_cards(query, titles, hay_by_title=None) -> list[str]:
    """Matched titles in relevance order: best tier first,
    page order within a tier. Titles that do not match are
    dropped. An empty query returns every title in given
    order. Pure — no Qt, no store."""
    q = (query or "").strip().lower()
    titles = [str(t) for t in (titles or [])]
    if not q:
        return list(titles)
    hay_by_title = hay_by_title or {}
    scored = []
    for idx, title in enumerate(titles):
        tier = match_tier(
            q, title, hay_by_title.get(title, ""))
        if tier is not None:
            scored.append((tier, idx, title))
    scored.sort()
    return [t for _tier, _idx, t in scored]
