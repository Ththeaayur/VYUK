"""
MUSIC skills for VYUK — the music-maths shelf: a metronome
calculator, note frequencies, scales and chords spelled out,
intervals named, tempo words both ways, tuning in cents, song
lengths in beats and bars, rhythm subdivisions, and a practice
log that remembers sessions.

Where each tool's truth lives — and what it honestly can't know:

- Everything except the practice log is pure arithmetic on the
  standard equal-temperament maths: A4 = 440 Hz, a semitone is
  a ratio of 2^(1/12), and a note's frequency is
  440 × 2^((midi − 69)/12). Nothing is looked up online and no
  audio is played — a metronome *sound* is beyond this pack; it
  gives the numbers behind one.
- Note names come back in sharps unless the root was given with
  a flat (or the scale is a minor-family one), where flats read
  more naturally — the pitches are identical either way.
- Tempo words are traditions, not standards: the bands here are
  the commonly taught ones, and the results say the band edges
  vary between teachers.
- Practice sessions persist as memory facts (core/memory/memory.py)
  under the fixed category `practice` — a small JSON payload of
  instrument, minutes and date per session — so they survive
  restarts and show up in the user's memory. Only the date a
  session was logged is known; there are no start times.

Same rules as the packs before it: plain functions returning
human-readable strings; handlers never raise — bad input becomes
an honest "I can't…" line, and caps and clamps are disclosed in
the result. Stdlib only, no network.
Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import datetime
import json
import math
import re


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _coerce_float(value):
    """A float, or None when the value isn't numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _fmt_num(value: float) -> str:
    """'1,299.5' / '100' — plain number formatting."""
    text = f"{value:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# ---------------------------------------------------------------------------
# NOTE PARSING — letters, sharps, flats and octaves into MIDI numbers
# ---------------------------------------------------------------------------

_SHARP_NAMES = ("C", "C#", "D", "D#", "E", "F",
                "F#", "G", "G#", "A", "A#", "B")
_FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F",
               "Gb", "G", "Ab", "A", "Bb", "B")
_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(
    r"^\s*([A-Ga-g])\s*(#|♯|b|♭)?\s*(-?\d+)?\s*$")


def _parse_note(text):
    """Parse 'A4', 'C#5', 'Bb3', 'F♯4' or a bare root like 'Bb'.

    Returns (midi_or_None, pitch_class, root_was_flat). The midi
    number is None when no octave was written. Pitch class and
    midi follow the arithmetic, so enharmonic spellings like
    B#3 and C4 land on the same key, as they should."""
    match = _NOTE_RE.match(str(text or ""))
    if not match:
        return None
    letter, accidental, octave = match.groups()
    offset = 0
    was_flat = False
    if accidental in ("#", "♯"):
        offset = 1
    elif accidental in ("b", "♭"):
        offset = -1
        was_flat = True
    pc = (_LETTER_PC[letter.upper()] + offset) % 12
    midi = None
    if octave is not None:
        # Semitones above C-1: octave*12 + natural letter + offset.
        midi = int(octave) * 12 + _LETTER_PC[letter.upper()] \
            + offset + 12
    return midi, pc, was_flat


def _note_name(pitch_class: int, prefer_flat: bool = False) -> str:
    names = _FLAT_NAMES if prefer_flat else _SHARP_NAMES
    return names[pitch_class % 12]


def _freq_of_midi(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


# ---------------------------------------------------------------------------
# METRONOME — a tempo in milliseconds, beats per second, bar times
# ---------------------------------------------------------------------------


def metronome_bpm(bpm, beats_per_bar: int = 4, bars: int = 0) -> str:
    """Turn a tempo in bpm into the time between beats, and the
    length of whole bars when asked."""
    tempo = _coerce_float(bpm)
    if tempo is None or tempo <= 0:
        return ("A tempo has to be a number of beats per minute "
                "above zero — like 60, 96 or 132.")
    if tempo > 999:
        return ("That's faster than any metronome goes — give me "
                "a tempo of 999 bpm or below.")
    per_bar = _coerce_int(beats_per_bar, 4, 1, 32)
    bar_count = _coerce_int(bars, 0, 0, 100000)
    ms = 60000.0 / tempo
    lines = [f"At {_fmt_num(tempo)} bpm, each beat lasts "
             f"{ms:.1f} ms — that's {tempo / 60.0:.2f} beats "
             "per second."]
    if bar_count > 0:
        total_beats = bar_count * per_bar
        seconds = total_beats * 60.0 / tempo
        minutes, secs = divmod(int(round(seconds)), 60)
        if minutes:
            span = f"{minutes} min {secs:02d} s"
        else:
            span = f"{secs} s"
        lines.append(f"{bar_count} bar"
                     f"{'s' if bar_count != 1 else ''} of "
                     f"{per_bar} beats is {total_beats} beats — "
                     f"{span} in total.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# NOTE FREQUENCY — a note name in, Hertz and MIDI out
# ---------------------------------------------------------------------------


def note_frequency(note: str = "") -> str:
    """The frequency in Hertz and the MIDI number of a note."""
    parsed = _parse_note(note)
    if parsed is None or parsed[0] is None:
        return (f"I couldn't read '{note}' as a note — give me "
                "a letter A–G with an optional sharp or flat and "
                "an octave number, like 'A4', 'C#5' or 'Bb3'.")
    midi, pc, was_flat = parsed
    freq = _freq_of_midi(midi)
    name = _note_name(pc, was_flat) + str(midi // 12 - 1)
    other = ""
    if str(note).strip().replace("♯", "#") != name:
        other = f" (the key you mean is {name})"
    return (f"{str(note).strip()} is {freq:.2f} Hz — MIDI note "
            f"{midi}.{other}")


# ---------------------------------------------------------------------------
# SCALE NOTES — spell a scale from its root
# ---------------------------------------------------------------------------

# Semitone steps from the root, and whether the scale reads
# better spelled with flats.
_SCALES = {
    "major": ([0, 2, 4, 5, 7, 9, 11], False),
    "natural minor": ([0, 2, 3, 5, 7, 8, 10], True),
    "harmonic minor": ([0, 2, 3, 5, 7, 8, 11], True),
    "major pentatonic": ([0, 2, 4, 7, 9], False),
    "minor pentatonic": ([0, 3, 5, 7, 10], True),
    "blues": ([0, 3, 5, 6, 7, 10], True),
    "dorian": ([0, 2, 3, 5, 7, 9, 10], True),
    "mixolydian": ([0, 2, 4, 5, 7, 9, 10], False),
}
_SCALE_ALIASES = {
    "minor": "natural minor",
    "min": "natural minor",
    "pentatonic": "major pentatonic",
    "major pent": "major pentatonic",
    "minor pent": "minor pentatonic",
}


def _scale_key(text: str) -> str:
    key = " ".join(str(text or "major").strip().lower()
                   .replace("_", " ").split())
    return _SCALE_ALIASES.get(key, key)


def _step_words(steps: list) -> str:
    """'W W H W W W H' for a step list (whole/half between notes,
    including the step back up to the octave)."""
    gaps = []
    for a, b in zip(steps, steps[1:] + [12]):
        gaps.append({1: "H", 2: "W", 3: "W+H"}.get(b - a, f"{b - a}"))
    return " ".join(gaps)


def scale_notes(root: str = "", scale: str = "major") -> str:
    """Spell out the notes of a scale from its root note."""
    parsed = _parse_note(root)
    if parsed is None:
        return (f"I couldn't read '{root}' as a root note — "
                "give me a letter A–G with an optional sharp or "
                "flat, like 'C', 'F#' or 'Bb'.")
    _midi, root_pc, was_flat = parsed
    key = _scale_key(scale)
    if key not in _SCALES:
        known = ", ".join(sorted(_SCALES))
        return (f"I don't know a '{scale}' scale — the ones I "
                f"know are: {known}.")
    steps, minor_family = _SCALES[key]
    prefer_flat = was_flat or minor_family
    names = [_note_name(root_pc + step, prefer_flat)
             for step in steps]
    root_name = _note_name(root_pc, prefer_flat)
    result = (f"The {root_name} {key} scale is: "
              + " ".join(names) + ".")
    if key in ("major", "natural minor"):
        result += (f" Steps: {_step_words(steps)} "
                   "(W = whole step, H = half step).")
    return result


# ---------------------------------------------------------------------------
# CHORD NOTES — a root and a chord type, spelled out with its recipe
# ---------------------------------------------------------------------------

# (semitones from the root, the interval recipe in words)
_CHORDS = {
    "major": ([0, 4, 7], "root, major third, perfect fifth"),
    "minor": ([0, 3, 7], "root, minor third, perfect fifth"),
    "7": ([0, 4, 7, 10],
          "root, major third, perfect fifth, minor seventh"),
    "maj7": ([0, 4, 7, 11],
             "root, major third, perfect fifth, major seventh"),
    "min7": ([0, 3, 7, 10],
             "root, minor third, perfect fifth, minor seventh"),
    "dim": ([0, 3, 6], "root, minor third, diminished fifth"),
    "aug": ([0, 4, 8], "root, major third, augmented fifth"),
    "sus2": ([0, 2, 7], "root, major second, perfect fifth"),
    "sus4": ([0, 5, 7], "root, perfect fourth, perfect fifth"),
    "add9": ([0, 4, 7, 14],
             "root, major third, perfect fifth, added ninth"),
}
_CHORD_ALIASES = {
    "": "major", "maj": "major", "m": "minor", "min": "minor",
    "dom7": "7", "dominant7": "7", "dominant 7": "7",
    "major7": "maj7", "major 7": "maj7",
    "m7": "min7", "minor7": "min7", "minor 7": "min7",
    "diminished": "dim", "augmented": "aug",
    "sus": "sus4", "add 9": "add9",
}


def chord_notes(root: str = "", chord: str = "major") -> str:
    """Spell out the notes of a chord and how it's built."""
    parsed = _parse_note(root)
    if parsed is None:
        return (f"I couldn't read '{root}' as a root note — "
                "give me a letter A–G with an optional sharp or "
                "flat, like 'G', 'D' or 'Bb'.")
    _midi, root_pc, was_flat = parsed
    key = " ".join(str(chord or "major").strip().lower()
                   .replace("_", " ").split())
    key = _CHORD_ALIASES.get(key, key)
    if key not in _CHORDS:
        known = ", ".join(sorted(_CHORDS))
        return (f"I don't know a '{chord}' chord — the types I "
                f"know are: {known}.")
    steps, recipe = _CHORDS[key]
    names = [_note_name(root_pc + step, was_flat) for step in steps]
    root_name = _note_name(root_pc, was_flat)
    label = "major" if key == "major" else key
    return (f"{root_name} {label} is {' '.join(names)} — "
            f"built as {recipe}.")


# ---------------------------------------------------------------------------
# INTERVAL NAME — the distance between two notes, named
# ---------------------------------------------------------------------------

_INTERVALS = {
    0: "unison", 1: "minor 2nd", 2: "major 2nd", 3: "minor 3rd",
    4: "major 3rd", 5: "perfect 4th", 6: "tritone",
    7: "perfect 5th", 8: "minor 6th", 9: "major 6th",
    10: "minor 7th", 11: "major 7th", 12: "octave",
}


def interval_name(note_a: str = "", note_b: str = "") -> str:
    """Name the interval from one note up to another."""
    first = _parse_note(note_a)
    second = _parse_note(note_b)
    if first is None or second is None:
        bad = note_a if first is None else note_b
        return (f"I couldn't read '{bad}' as a note — give me "
                "letters A–G with optional sharps, flats and "
                "octaves, like 'C4' to 'G4', or just 'C' to 'G'.")
    midi_a, pc_a, _flat_a = first
    midi_b, pc_b, _flat_b = second
    if midi_a is not None and midi_b is not None \
            and 0 < midi_b - midi_a <= 12:
        semis = midi_b - midi_a
    else:
        semis = (pc_b - pc_a) % 12
    name = _INTERVALS[semis]
    a_name = str(note_a).strip()
    b_name = str(note_b).strip()
    if semis == 0:
        return (f"{a_name} to {b_name} is a unison — the same "
                "pitch, 0 semitones apart.")
    return (f"{a_name} up to {b_name} is a {name} — "
            f"{semis} semitone{'s' if semis != 1 else ''} apart.")


# ---------------------------------------------------------------------------
# TEMPO TERMS — Italian tempo words and their bpm bands, both ways
# ---------------------------------------------------------------------------

# (name, lowest bpm, highest bpm, band wording) — the commonly
# taught bands; edges differ between teachers by a few bpm.
_TEMPO_BANDS = (
    ("Largo", 1, 49, "under 50"),
    ("Adagio", 50, 65, "50–65"),
    ("Andante", 66, 76, "66–76"),
    ("Moderato", 77, 108, "77–108"),
    ("Allegro", 109, 156, "109–156"),
    ("Vivace", 157, 175, "157–175"),
    ("Presto", 176, 100000, "176 and above"),
)


def tempo_terms(bpm=0, term: str = "") -> str:
    """Give the Italian tempo word for a bpm, or the bpm band
    for a tempo word."""
    lines = []
    tempo = _coerce_float(bpm)
    if tempo is not None and tempo > 0:
        word, band = "Presto", "176 and above"
        for name, lo, hi, wording in _TEMPO_BANDS:
            if lo <= tempo <= hi:
                word, band = name, wording
                break
        lines.append(f"{_fmt_num(tempo)} bpm is {word} "
                     f"(traditionally {band} bpm).")
    word_text = str(term or "").strip().lower().capitalize()
    if word_text:
        hit = None
        for name, lo, hi, wording in _TEMPO_BANDS:
            if name.lower() == word_text.lower():
                hit = (name, wording)
                break
        if hit is None:
            known = ", ".join(name for name, *_ in _TEMPO_BANDS)
            return (f"I don't know a tempo word '{term}' — "
                    f"the ones I know are: {known}.")
        lines.append(f"{hit[0]} is traditionally about "
                     f"{hit[1]} beats per minute.")
    if not lines:
        return ("Give me a tempo in bpm and I'll name it, or a "
                "tempo word like 'Andante' and I'll give its "
                "bpm band.")
    lines.append("(Tempo bands are conventions — teachers "
                 "differ by a few bpm at the edges.)")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# TUNING CENTS — how far apart two frequencies sit, in cents
# ---------------------------------------------------------------------------


def tuning_cents(freq_a, freq_b) -> str:
    """The distance between two frequencies in cents (100ths
    of a semitone)."""
    first = _coerce_float(freq_a)
    second = _coerce_float(freq_b)
    if first is None or second is None or first <= 0 or second <= 0:
        return ("I need two frequencies in Hertz, both above "
                "zero — like 440 and 442.")
    cents = 1200.0 * math.log2(second / first)
    shown = round(cents, 1)
    if abs(cents) <= 6:
        return (f"{_fmt_num(second)} Hz is {abs(shown):.1f} "
                f"cents from {_fmt_num(first)} Hz — within "
                "6 cents, which counts as in tune.")
    direction = "sharp of" if cents > 0 else "flat of"
    return (f"{_fmt_num(second)} Hz is {abs(shown):.1f} cents "
            f"{direction} {_fmt_num(first)} Hz.")


# ---------------------------------------------------------------------------
# SONG LENGTH IN BEATS — seconds and bpm into beats and bars
# ---------------------------------------------------------------------------


def song_length_beats(seconds, bpm,
                      beats_per_bar: int = 4) -> str:
    """How many beats and bars a song of a given length holds
    at a given tempo."""
    secs = _coerce_float(seconds)
    tempo = _coerce_float(bpm)
    if secs is None or secs <= 0:
        return ("The song length has to be a number of seconds "
                "above zero.")
    if tempo is None or tempo <= 0:
        return ("The tempo has to be a number of beats per "
                "minute above zero.")
    per_bar = _coerce_int(beats_per_bar, 4, 1, 32)
    beats = secs * tempo / 60.0
    bars = beats / per_bar
    return (f"A {_fmt_num(secs)}-second song at "
            f"{_fmt_num(tempo)} bpm holds {_fmt_num(beats)} "
            f"beats — at {per_bar} beats per bar that's "
            f"{bars:.1f} bars. (Maths: {_fmt_num(secs)} s × "
            f"{_fmt_num(tempo)} beats a minute ÷ 60 = "
            f"{_fmt_num(beats)} beats.)")


# ---------------------------------------------------------------------------
# RHYTHM SUBDIVIDE — the length of every note value at a tempo
# ---------------------------------------------------------------------------


def rhythm_subdivide(bpm) -> str:
    """Milliseconds per note value at a tempo: wholes down to
    sixteenths, plus triplets."""
    tempo = _coerce_float(bpm)
    if tempo is None or tempo <= 0:
        return ("A tempo has to be a number of beats per "
                "minute above zero.")
    if tempo > 999:
        return ("That's faster than any metronome goes — give "
                "me a tempo of 999 bpm or below.")
    beat = 60000.0 / tempo
    rows = [
        ("Whole note", beat * 4),
        ("Half note", beat * 2),
        ("Quarter note (one beat)", beat),
        ("Eighth note", beat / 2),
        ("Sixteenth note", beat / 4),
        ("Eighth triplet (3 in one beat)", beat / 3),
        ("Quarter triplet (3 in two beats)", beat * 2 / 3),
    ]
    lines = [f"At {_fmt_num(tempo)} bpm, one beat is "
             f"{beat:.0f} ms:"]
    for label, ms in rows:
        lines.append(f"• {label}: {ms:.0f} ms")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PRACTICE LOG — sessions as memory facts, streaks and totals
# ---------------------------------------------------------------------------

_PRACTICE_CATEGORY = "practice"
_MAX_INSTRUMENT_CHARS = 40
_LIST_WINDOW_DAYS = 7


def _store():
    """The memory store singleton — this pack's only persistence
    seam. Tests substitute a temp MemoryStore by replacing this
    function; handlers never touch the singleton directly."""
    from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    return memory_store


def _facts(category: str) -> list:
    """Every stored fact in one category ({id, key, value, ...})."""
    return [f for f in _store().get_all_facts()
            if f.get("category") == category]


def _load_sessions() -> tuple[list, int]:
    """(sessions, broken_count): {instrument, minutes, date}
    dicts parsed from the practice facts; unparseable ones are
    counted."""
    sessions = []
    broken = 0
    for fact in _facts(_PRACTICE_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            minutes = float(stored["minutes"])
            day = str(stored["date"])
            datetime.date.fromisoformat(day)
            instrument = str(stored.get("instrument") or "").strip()
            if not instrument or minutes <= 0:
                raise ValueError
            sessions.append({"instrument": instrument,
                             "minutes": minutes, "date": day})
        except Exception:  # noqa: BLE001
            broken += 1
    sessions.sort(key=lambda s: (s["date"],
                                 s["instrument"].lower()))
    return sessions, broken


def _streak(days: set) -> int:
    """Consecutive practiced days ending today — or yesterday,
    since today may simply not have happened yet."""
    today = datetime.date.today()
    if today.isoformat() in days:
        cursor = today
    elif (today - datetime.timedelta(days=1)).isoformat() in days:
        cursor = today - datetime.timedelta(days=1)
    else:
        return 0
    count = 0
    while cursor.isoformat() in days:
        count += 1
        cursor -= datetime.timedelta(days=1)
    return count


def practice_log(action: str, instrument: str = "",
                 minutes=0) -> str:
    """Log a practice session, or review the last 7 days with
    totals and the current streak."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list"):
        return ("Practice actions are 'add' and 'list' — e.g. "
                "practice_log with action 'add', an instrument "
                "and the minutes practised.")
    if action == "add":
        name = " ".join(str(instrument or "").split())
        if not name:
            return ("Tell me which instrument the session was "
                    "on — nothing was logged.")
        if len(name) > _MAX_INSTRUMENT_CHARS:
            return (f"That instrument name is {len(name)} "
                    f"characters — names are capped at "
                    f"{_MAX_INSTRUMENT_CHARS}.")
        length = _coerce_float(minutes)
        if length is None or length <= 0:
            return ("A session needs minutes above zero — "
                    "nothing was logged.")
        today = datetime.date.today().isoformat()
        key = f"{today} {name.lower()}"
        payload = {"instrument": name, "minutes": length,
                   "date": today}
        try:
            suffix = 1
            candidate = key
            while _store().get_fact(_PRACTICE_CATEGORY,
                                    candidate) is not None:
                suffix += 1
                candidate = f"{key} ({suffix})"
            _store().save_fact(_PRACTICE_CATEGORY, candidate,
                               json.dumps(payload))
            sessions, _broken = _load_sessions()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the session: {e}"
        today_total = sum(s["minutes"] for s in sessions
                          if s["date"] == today)
        return (f"Logged {_fmt_num(length)} minutes of {name} "
                f"for today ({today}) — {_fmt_num(today_total)} "
                "minutes practised today in all.")
    # action == "list"
    try:
        sessions, broken = _load_sessions()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the practice log: {e}"
    if not sessions:
        base = ("No practice sessions logged yet. Add one with "
                "practice_log, action 'add', an instrument and "
                "the minutes.")
        if broken:
            base += (f" ({broken} stored session"
                     f"{'s' if broken != 1 else ''} couldn't be "
                     "read and were skipped.)")
        return base
    today = datetime.date.today()
    window_start = today - datetime.timedelta(
        days=_LIST_WINDOW_DAYS - 1)
    recent = [s for s in sessions
              if window_start.isoformat() <= s["date"]
              <= today.isoformat()]
    all_days = {s["date"] for s in sessions}
    streak = _streak(all_days)
    lines = []
    if recent:
        total = sum(s["minutes"] for s in recent)
        days = len({s["date"] for s in recent})
        lines.append(f"Last {_LIST_WINDOW_DAYS} days: "
                     f"{len(recent)} session"
                     f"{'s' if len(recent) != 1 else ''}, "
                     f"{_fmt_num(total)} minutes across "
                     f"{days} day{'s' if days != 1 else ''}.")
        per_instrument = {}
        for session in recent:
            slot = per_instrument.setdefault(
                session["instrument"].lower(),
                {"name": session["instrument"], "minutes": 0.0,
                 "count": 0})
            slot["minutes"] += session["minutes"]
            slot["count"] += 1
        for slot in sorted(per_instrument.values(),
                           key=lambda s: -s["minutes"]):
            lines.append(f"• {slot['name']}: "
                         f"{_fmt_num(slot['minutes'])} minutes "
                         f"({slot['count']} session"
                         f"{'s' if slot['count'] != 1 else ''})")
    else:
        lines.append(f"No sessions in the last "
                     f"{_LIST_WINDOW_DAYS} days — the log holds "
                     f"{len(sessions)} older session"
                     f"{'s' if len(sessions) != 1 else ''}.")
    if streak:
        lines.append(f"Current streak: {streak} day"
                     f"{'s' if streak != 1 else ''} in a row.")
    else:
        lines.append("Current streak: 0 days — no session "
                     "today or yesterday.")
    if broken:
        lines.append(f"({broken} stored session"
                     f"{'s' if broken != 1 else ''} couldn't be "
                     "read and were skipped.)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Music pack tool on a ToolRegistry-like
    object."""
    registry.register(
        name="metronome_bpm",
        description="Turn a tempo in beats per minute into timing: how many milliseconds each beat lasts and how many beats land per second. With bars and beats_per_bar it also gives the total time that many bars take. It's the maths behind a metronome — no sound is played. Use when timing practice, takes or loops.",
        parameters={
            "type": "object",
            "properties": {
                "bpm": {"type": "number", "description": "The tempo in beats per minute"},
                "beats_per_bar": {"type": "integer", "description": "Beats in each bar (default 4)"},
                "bars": {"type": "integer", "description": "Optional: how many bars to total up (default 0 = not shown)"},
            },
            "required": ["bpm"],
        },
        handler=metronome_bpm,
    )
    registry.register(
        name="note_frequency",
        description="Give the frequency of a musical note: a letter A–G with an optional sharp or flat and an octave, like A4, C#5 or Bb3. Returns the pitch in Hertz (equal temperament, A4 = 440 Hz) and the MIDI note number. Use for tuning, synthesis and theory questions.",
        parameters={
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The note, e.g. 'A4', 'C#5', 'Bb3'"},
            },
            "required": ["note"],
        },
        handler=note_frequency,
    )
    registry.register(
        name="practice_log",
        description="Log music practice and look back at it. Actions: 'add' records a session (instrument and minutes, dated today), 'list' shows the last 7 days — session count, total minutes, days practised, per-instrument totals and the current daily streak. Sessions persist as memory facts. Use when the user practises an instrument or asks how their practice is going.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'add' or 'list'"},
                "instrument": {"type": "string", "description": "The instrument practised, for 'add'"},
                "minutes": {"type": "number", "description": "How many minutes the session lasted, for 'add'"},
            },
            "required": ["action"],
        },
        handler=practice_log,
    )
    registry.register(
        name="scale_notes",
        description="Spell out the notes of a scale from a root note: major, natural minor, harmonic minor, major and minor pentatonic, blues, dorian and mixolydian ('minor' works as a short name for natural minor). Major and natural minor also show their whole/half step pattern. Use for theory, improvisation and exam revision.",
        parameters={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "The root note, e.g. 'C', 'F#', 'Bb'"},
                "scale": {"type": "string", "description": "The scale type (default 'major')"},
            },
            "required": ["root"],
        },
        handler=scale_notes,
    )
    registry.register(
        name="chord_notes",
        description="Spell out the notes of a chord from a root note and a type — major, minor, 7, maj7, min7, dim, aug, sus2, sus4 or add9 — and say how it's built (root, third, fifth and so on). Use for songwriting, theory and working out what to play.",
        parameters={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "The root note, e.g. 'G', 'D', 'Bb'"},
                "chord": {"type": "string", "description": "The chord type (default 'major')"},
            },
            "required": ["root"],
        },
        handler=chord_notes,
    )
    registry.register(
        name="interval_name",
        description="Name the interval between two notes — how far the second sits above the first, within an octave: unison, minor 2nd, major 3rd, perfect 5th, tritone and so on, with the semitone count. Notes can carry octaves (C4 to G4) or not (C to G). Use for theory and ear-training revision.",
        parameters={
            "type": "object",
            "properties": {
                "note_a": {"type": "string", "description": "The lower (starting) note"},
                "note_b": {"type": "string", "description": "The note above it"},
            },
            "required": ["note_a", "note_b"],
        },
        handler=interval_name,
    )
    registry.register(
        name="tempo_terms",
        description="Translate between Italian tempo words and beats per minute, both ways. Give a bpm to get its word (132 bpm is Allegro), or give a word like 'Andante' to get its traditional bpm band. The bands are the commonly taught ones; teachers differ by a few bpm at the edges and the result says so. Use when a score marking needs a speed, or the other way round.",
        parameters={
            "type": "object",
            "properties": {
                "bpm": {"type": "number", "description": "A tempo in beats per minute to name (0 to skip)"},
                "term": {"type": "string", "description": "A tempo word like 'Andante' to look up (blank to skip)"},
            },
            "required": [],
        },
        handler=tempo_terms,
    )
    registry.register(
        name="tuning_cents",
        description="Say how far apart two frequencies are in cents — hundredths of a semitone. Reports whether the second is sharp or flat of the first and by how much; within 6 cents counts as in tune. Use when comparing an instrument or a recording against a reference pitch like 440 Hz.",
        parameters={
            "type": "object",
            "properties": {
                "freq_a": {"type": "number", "description": "The reference frequency in Hertz"},
                "freq_b": {"type": "number", "description": "The frequency to compare, in Hertz"},
            },
            "required": ["freq_a", "freq_b"],
        },
        handler=tuning_cents,
    )
    registry.register(
        name="song_length_beats",
        description="Work out how many beats and bars a piece of music holds: give its length in seconds and its tempo in bpm (and beats per bar if it isn't 4). The maths is shown in the result. Use for arranging, looping and set planning.",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "The song's length in seconds"},
                "bpm": {"type": "number", "description": "The tempo in beats per minute"},
                "beats_per_bar": {"type": "integer", "description": "Beats in each bar (default 4)"},
            },
            "required": ["seconds", "bpm"],
        },
        handler=song_length_beats,
    )
    registry.register(
        name="rhythm_subdivide",
        description="Give the length in milliseconds of every common note value at a tempo: whole, half, quarter, eighth and sixteenth notes, plus eighth and quarter triplets. One beat is the quarter note. Use for delay-time settings, sequencing and rhythm practice.",
        parameters={
            "type": "object",
            "properties": {
                "bpm": {"type": "number", "description": "The tempo in beats per minute"},
            },
            "required": ["bpm"],
        },
        handler=rhythm_subdivide,
    )
