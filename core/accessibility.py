"""Accessibility deltas, Wave 9 Build 5 (ACCESS) — descriptors
and records only. Nothing here reads a live screen, infers
anything about the user, or labels anyone.

Per build_flow_v6.md: "ACCESS (accessibility deltas
beyond the shipped Accessibility edition) — BUILD, Wave 9,
under the dignity bar and S-6 where capture is involved."
The dignity bar, as this module honours it: accessibility
features serve the user in plain language, never with
infantilising language, never with a capability claim,
never by labelling the user, and never by collecting or
inferring data about a disability. S-6, as this module
honours it: any screen-understanding involvement needs
the opt-in with indicator — so this module builds NO
capture at all. A window description here works only on
a caller-supplied snapshot of control names and roles;
a live screen is never read, and a snapshot flagged live
or captured refuses whole.

WHAT THIS MODULE IS
-------------------
* A text-clarity descriptor: caller-supplied text in, a
  plain-language note out — sentence count, word count,
  how many sentences are long, and which words from a
  fixed jargon list appear. It says nothing about the
  reader, and it never produces a reading-level,
  capability, or comprehension score.
* A window-description descriptor: a caller-supplied
  accessibility-tree snapshot in — control names and
  roles as supplied, in the order supplied — and an
  eyes-free layout description out. The snapshot is
  data the caller hands over; nothing is read live.
* A voice-first routine descriptor: caller-chosen steps,
  each from a fixed list only (voice command, larger
  target, keyboard alternative, pause), in the order
  given. An unknown step refuses the whole routine,
  never swapped for a near neighbour; duplicates are
  kept once and reported.
* A re-validation rule for recording an accessibility
  preference: the preference is re-built from its own
  fields and must match exactly, or it is refused, never
  written on trust. A preference label is plain language
  only — no disability label, no health label, no score.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* any disability, diagnosis, or health label — as a
  preference label, or as a field on a preference
  record — refuses whole. (Prose inside a text-clarity
  input is not a label: the descriptor counts sentences
  and words and never repeats the text, so a word in
  prose is not a label placed on anyone. Labels are
  what this module refuses.)
* any request for a reading-level, capability, or
  comprehension score refuses whole — the clarity
  descriptor has no score to give, and a caller asking
  for one is refused, not given a number under another
  name;
* any inference about the user's abilities, condition,
  or needs refuses whole — this module infers nothing,
  and a record carrying an inferred flag refuses;
* any live screen capture, screenshot, or live
  accessibility-tree read refuses whole — a snapshot
  flagged live or captured, or carrying a capture
  source, refuses, and the descriptor never reaches
  for a live screen itself;
* any claim that a feature cures, fixes, treats, or
  compensates for a condition refuses whole — the
  preference label scan refuses cure/treat wording.
The module imports no screen-capture, OCR, or sensor
code — proven by the AST test in tests/test_access_w9.py.
It opens no window, reads no screen, and touches no
camera, microphone, or sensor: there is nothing here
to capture with, by construction.

FAIL-CLOSED, WHOLE-CALL
-----------------------
A refused label, score request, inference flag, live
snapshot, or cure claim refuses the whole call. Nothing
is softened, relabelled, or quietly answered in part:
an accessibility answer built on a label placed on the
user, or on a screen read without the opt-in, is worse
than no answer.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only — in fact, no imports
beyond ``__future__`` annotations and the stdlib ``re``
module. No new dependency, no telemetry write, no
database access, no network access.
Nothing here reads or writes the install tree's ``data/``
directory.
"""

from __future__ import annotations

import re

__all__ = [
    "MAX_TEXT_CHARS",
    "MAX_LABEL_CHARS",
    "MAX_CONTROLS",
    "MAX_CONTROL_CHARS",
    "MAX_STEPS",
    "LONG_SENTENCE_WORDS",
    "JARGON_WORDS",
    "ROUTINE_STEPS",
    "LABEL_WORDS",
    "build_text_clarity",
    "describe_window",
    "build_routine",
    "validate_recordable_preference",
]

#: Caller-supplied bounds. A longer field refuses whole,
#: never truncated into a different description.
MAX_TEXT_CHARS = 10000
MAX_LABEL_CHARS = 200
MAX_CONTROLS = 200
MAX_CONTROL_CHARS = 200
MAX_STEPS = 20

#: A sentence longer than this many words is counted long.
#: The count is about the text, never about the reader.
LONG_SENTENCE_WORDS = 30

#: Fixed jargon list for the clarity descriptor. Only
#: these words are flagged, by whole-word match; nothing
#: else is judged, and no score is derived from them.
JARGON_WORDS = (
    "utilize",
    "utilise",
    "utilization",
    "leverage",
    "synergy",
    "paradigm",
    "facilitate",
    "implementation",
    "functionality",
    "methodology",
    "optimize",
    "optimise",
    "incentivize",
)

#: The fixed routine-step list. Anything else refuses
#: whole — a step is never invented or swapped.
ROUTINE_STEPS = (
    "voice command",
    "larger target",
    "keyboard alternative",
    "pause",
)

#: Words that, as a whole word in a preference label,
#: make the label a disability or health label, or a
#: cure/treat claim — and refuse the record whole.
#: This scan applies to labels only, never to prose
#: inside a text-clarity input (see module docstring).
LABEL_WORDS = frozenset({
    "disabled",
    "disability",
    "blind",
    "deaf",
    "wheelchair",
    "autism",
    "autistic",
    "dyslexia",
    "dyslexic",
    "adhd",
    "diagnosis",
    "diagnosed",
    "disorder",
    "syndrome",
    "impairment",
    "impaired",
    "handicap",
    "medical",
    "patient",
    "therapy",
    "cure",
    "cures",
    "treats",
    "treatment",
})


def _check_text_field(name, value, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty: the caller "
                         f"must supply it, it is never invented")
    if len(stripped) > maximum:
        raise ValueError(f"{name} exceeds {maximum} chars")
    return stripped


def _refuse_label_words(label):
    lowered = label.lower()
    for word in sorted(LABEL_WORDS):
        if re.search(r"\b" + re.escape(word) + r"\b", lowered):
            raise ValueError(
                f"refused — preference label carries a disability, "
                f"health, or cure/treat word ({word!r}): labels here "
                f"are plain language only and never label the user")


def _split_sentences(text):
    parts = [p.strip() for p in re.split(r"[.!?]+", text) if p.strip()]
    return parts


def build_text_clarity(text, want_score=None):
    """A text-clarity note from caller-supplied text only.

    Returns sentence count, word count, how many
    sentences are long (over LONG_SENTENCE_WORDS words),
    and which fixed-list jargon words appear. It says
    nothing about the reader: no reading level, no
    capability, no comprehension score, and no claim
    about what the reader can or cannot do. A caller
    asking for a score (want_score truthy) refuses
    whole. The text itself is never repeated back, so
    prose inside it is never turned into a label.
    """
    if want_score:
        raise ValueError(
            "refused — a reading-level, capability, or "
            "comprehension score is never produced here: "
            "the clarity descriptor counts sentences and "
            "words only")
    clean = _check_text_field("text", text, MAX_TEXT_CHARS)
    sentences = _split_sentences(clean)
    if not sentences:
        raise ValueError("text must contain at least one sentence")
    words = clean.split()
    long_count = 0
    for sentence in sentences:
        if len(sentence.split()) > LONG_SENTENCE_WORDS:
            long_count += 1
    lowered = clean.lower()
    found = []
    for word in JARGON_WORDS:
        if re.search(r"\b" + re.escape(word) + r"\b", lowered):
            found.append(word)
    return {
        "kind": "TEXT_CLARITY",
        "sentence_count": len(sentences),
        "word_count": len(words),
        "long_sentence_count": long_count,
        "long_sentence_words": LONG_SENTENCE_WORDS,
        "jargon_found": found,
        "about_reader": False,
        "score": None,
        "note": ("A note about this text's sentences and words "
                 "only: it says nothing about the reader, and "
                 "it is not a reading level, a capability "
                 "rating, or a comprehension score."),
    }


def describe_window(snapshot):
    """An eyes-free layout description from a snapshot.

    The snapshot is a caller-supplied object with a
    "controls" list of {"name": ..., "role": ...} entries,
    in the order supplied. The description lists them in
    that order and states plainly that nothing was read
    from a live screen. A snapshot flagged live or
    captured, or carrying a capture source, refuses
    whole — as does a missing, empty, or malformed
    controls list. Control names and roles are echoed
    exactly as supplied, never inferred or completed.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object with a "
                         "controls list supplied by the caller")
    if snapshot.get("live") or snapshot.get("captured"):
        raise ValueError(
            "refused — a live or captured snapshot: this "
            "descriptor works only on a caller-supplied "
            "snapshot, never on a live screen read")
    source = snapshot.get("source")
    if isinstance(source, str) and source.strip().lower() in (
            "live", "screenshot", "capture", "screen"):
        raise ValueError(
            "refused — snapshot source names a live screen "
            "or capture: only a caller-supplied snapshot "
            "is described here")
    controls = snapshot.get("controls")
    if isinstance(controls, (str, bytes)) or not isinstance(
            controls, (list, tuple)):
        raise ValueError(
            "controls must be a list of caller-supplied "
            "{name, role} entries")
    if not controls:
        raise ValueError(
            "controls must not be empty: the caller must "
            "supply the snapshot, it is never captured")
    if len(controls) > MAX_CONTROLS:
        raise ValueError(f"controls exceeds {MAX_CONTROLS} entries")
    lines = []
    for entry in controls:
        if not isinstance(entry, dict):
            raise ValueError("each control must be an object "
                             "with name and role")
        name = _check_text_field("control name",
                                 entry.get("name"), MAX_CONTROL_CHARS)
        role = _check_text_field("control role",
                                 entry.get("role"), MAX_CONTROL_CHARS)
        lines.append(f"{name} ({role})")
    return {
        "kind": "WINDOW_DESCRIPTION",
        "controls_count": len(lines),
        "layout": "Top to bottom: " + "; ".join(lines),
        "live_read": False,
        "note": ("This describes the snapshot you supplied, "
                 "in the order you supplied it. Nothing was "
                 "read from a live screen, and no control "
                 "was inferred or completed."),
    }


def build_routine(steps):
    """A voice-first routine from caller-chosen steps.

    Every step must be one of ROUTINE_STEPS, in the
    order given. An unknown step refuses the whole
    routine, never swapped for a near neighbour.
    Duplicates are kept once and reported. The routine
    is a description of chosen steps, never a claim
    about the user and never a live change to anything.
    """
    if isinstance(steps, (str, bytes)) or not isinstance(
            steps, (list, tuple)):
        raise ValueError("steps must be a list of caller-chosen "
                         "step names from the fixed list")
    if not steps:
        raise ValueError("steps must not be empty: the caller "
                         "must choose at least one step")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"steps exceeds {MAX_STEPS} entries")
    chosen = []
    duplicates = 0
    for step in steps:
        if not isinstance(step, str):
            raise ValueError("each step must be a string naming "
                             "a step from the fixed list")
        word = step.strip().lower()
        if word not in ROUTINE_STEPS:
            raise ValueError(
                f"refused — unknown routine step {step!r}: "
                f"steps come only from the fixed list "
                f"({', '.join(ROUTINE_STEPS)}), never invented")
        if word in chosen:
            duplicates += 1
            continue
        chosen.append(word)
    return {
        "kind": "VOICE_FIRST_ROUTINE",
        "steps": chosen,
        "duplicates_removed": duplicates,
        "note": ("A routine of the steps you chose, in the "
                 "order you chose them. It changes nothing "
                 "by itself and claims nothing about you."),
    }


def validate_recordable_preference(fields):
    """Re-build a preference from its own fields.

    Allowed fields are exactly "label" (a plain-language
    setting label, non-empty, at most MAX_LABEL_CHARS)
    and "enabled" (a boolean). Any other field refuses
    whole — a record carrying a score, a disability or
    health field, or an inferred flag is never written
    on trust. A label carrying a disability, health, or
    cure/treat word refuses whole. The rebuilt
    preference is returned; it labels a setting, never
    a person.
    """
    if not isinstance(fields, dict):
        raise ValueError("preference must be a JSON object with "
                         "label and enabled")
    allowed = {"label", "enabled"}
    for key in fields:
        if key not in allowed:
            raise ValueError(
                f"refused — unexpected preference field {key!r}: "
                f"a preference carries only a plain-language "
                f"label and enabled, never a score, a label "
                f"about the user, or an inferred flag")
    if fields.get("inferred"):
        raise ValueError(
            "refused — inferred preference: a preference "
            "must be chosen by the caller, never inferred")
    label = _check_text_field("label", fields.get("label"),
                              MAX_LABEL_CHARS)
    _refuse_label_words(label)
    enabled = fields.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false, chosen "
                         "by the caller")
    return {
        "kind": "ACCESS_PREFERENCE",
        "label": label,
        "enabled": enabled,
        "note": ("A setting preference, in plain language. "
                 "It labels a setting, never a person, and "
                 "it says nothing about anyone's abilities "
                 "or condition."),
    }
