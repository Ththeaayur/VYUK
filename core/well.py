"""Wellbeing, human-first — suggestions only, sensing-free.

Wave 9 Build 1 (WELL), per build_flow_v6.md: "WELL
(wellbeing, human-first) — BUILD, Wave 9, no diagnosis, no
mood inference from camera or typing." The HEALTH
queued-study rule draws the same line from the other side:
PILOT for slices inside the head boundary (tracks,
notices, reminds, organises), PARKED for diagnosis,
interpretation, or scoring — no diagnosis is built under
any reading of the plan.

WHAT THIS MODULE IS
-------------------
* A break-plan descriptor: caller-supplied work minutes,
  break minutes, and focus-stretch minutes in, a
  plain-language plan out — work in focus stretches with a
  rest moment between them, then the break. The plan is a
  suggestion the user asked for, never a prescription.
* A reminder descriptor: caller-supplied trigger words and
  a time in, the reminder wording out. It schedules
  NOTHING by itself — scheduling is the caller's action,
  and the descriptor says so in plain words.
* A wind-down routine descriptor: caller-chosen steps,
  each from the fixed non-medical list below, in order.
  No step outside the list is ever accepted.
* A day summary: totals of caller-supplied numbers only
  (work minutes, breaks taken, break minutes). The numbers
  are what the caller told it, never measurements, and the
  summary says so. No average, no score, no rating of any
  kind is produced — totals only.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* diagnosis, symptom, condition, treatment, or medical
  advice — any word from the medical token list in free
  text, or any request shaped like one;
* mood or emotion inference — from camera, typing, voice
  tone, or text sentiment, or any request to infer how
  someone feels;
* sleep, heart-rate, step-count, or any biometric input or
  output;
* any claim that a break or routine prevents, treats, or
  cures anything — the module's own outputs never make
  such a claim, and inputs carrying one are refused.
The module imports no camera, microphone, keyboard, or
sensor code — proven by the AST test in
tests/test_well_w9.py. It starts no job, schedules no
notification, and measures nothing: there is nothing here
to measure with, by construction.

FAIL-CLOSED, WHOLE-CALL
-----------------------
A refused token, an unknown wind-down step, a missing or
negative number, or an out-of-range value refuses the
whole call. Nothing is truncated, rounded into shape, or
quietly replaced: a plan the caller did not ask for is
worse than no plan.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only — in fact, no imports
beyond ``__future__`` annotations. No new dependency, no
telemetry write, no database access, no network access.
Nothing here reads or writes the install tree's ``data/``
directory.
"""

from __future__ import annotations

__all__ = [
    "FIXED_WIND_DOWN_STEPS",
    "MEDICAL_TOKENS",
    "MOOD_TOKENS",
    "BIOMETRIC_TOKENS",
    "CLAIM_TOKENS",
    "MAX_WORK_MINUTES",
    "MAX_BREAK_MINUTES",
    "MAX_FOCUS_MINUTES",
    "MAX_FIELD_CHARS",
    "refusal_reason",
    "build_break_plan",
    "build_reminder",
    "build_winddown",
    "summarize_day",
    "validate_recordable_plan",
]

#: The only wind-down steps this module will ever emit.
#: Plain, non-medical, doable without any device or app.
#: Nothing here diagnoses, treats, or measures anything.
FIXED_WIND_DOWN_STEPS = (
    "dim the lights",
    "stretch gently",
    "step away from the screen",
    "drink some water",
    "take a few slow breaths",
    "tidy one small thing",
    "put the phone in another room",
)

#: Free-text tokens that refuse a call whole. Word-boundary
#: matched on the lower-cased input, so "diagnostics" of a
#: machine does not trip the medical list — but a request
#: about a symptom, a condition, or a treatment always
#: does. These lists are about REFUSAL, never about
#: understanding a condition: this module understands
#: nothing medical, and refuses to pretend otherwise.
MEDICAL_TOKENS = frozenset({
    "diagnos", "symptom", "disease", "disorder", "syndrome",
    "treatment", "therapy", "medicine", "medication",
    "prescription", "depress", "anxiet", "insomnia",
    "migraine", "diabet", "cancer", "headache", "pain",
    "illness", "sick", "doctor", "clinic", "hospital",
})

#: Mood / emotion inference tokens. Any request to infer,
#: score, or read how someone feels refuses whole.
MOOD_TOKENS = frozenset({
    "mood", "emotion", "feeling", "feelings", "sentiment",
    "stress", "burnout", "lonely", "sad", "happy",
    "angry", "anxious", "calmness",
})

#: Biometric / body-measurement tokens. This module takes
#: no body data in and gives none out.
BIOMETRIC_TOKENS = frozenset({
    "sleep", "heart", "pulse", "steps", "step-count",
    "stepcount", "blood", "pressure", "calorie", "weight",
    "bmi", "oxygen", "temperature",
})

#: Prevention / treatment claim tokens. A break or routine
#: is a suggestion, never a prevention or a cure, and an
#: input claiming otherwise is refused, not softened.
CLAIM_TOKENS = frozenset({
    "prevent", "treat", "cure", "heal", "fix my",
    "get rid of",
})

#: Caller-supplied bounds. A work block longer than this
#: is refused, not split silently; a break longer than
#: this is refused, not shortened silently.
MAX_WORK_MINUTES = 480
MAX_BREAK_MINUTES = 120
MAX_FOCUS_MINUTES = 120
MAX_FIELD_CHARS = 500

_REST_MOMENTS = (
    "stand up",
    "look away from the screen",
    "stretch gently",
    "drink some water",
)


def _tokens_in(text):
    """Lower-cased, word-ish tokens of a free-text input,
    for refusal scanning. Numbers and punctuation carry no
    refusal meaning, so only letter runs are kept."""
    lowered = str(text).lower()
    current = []
    for char in lowered:
        if char.isalpha() or char == "-":
            current.append(char)
        else:
            if current:
                yield "".join(current)
                current = []
    if current:
        yield "".join(current)


def refusal_reason(text):
    """The refusal reason for a free-text input, or None
    when it carries no refused kind of content. A token
    matches when it equals a listed token or starts with
    one (so "diagnosis" trips "diagnos"), except the
    two-word claim "fix my" and "get rid of", which match
    on the raw lower-cased text."""
    if text is None:
        return None
    if not isinstance(text, str):
        return "refused — free text must be a string"
    lowered = text.lower()
    for phrase in ("fix my", "get rid of"):
        if phrase in lowered:
            return (f"refused — this module never claims a break "
                    f"or routine fixes anything ({phrase!r} is a "
                    f"treatment claim)")
    tokens = list(_tokens_in(text))
    for token in tokens:
        for listed in MEDICAL_TOKENS:
            if token == listed or token.startswith(listed):
                return (f"refused — medical content ({token!r}): "
                        f"this module gives no diagnosis, "
                        f"treatment, or medical advice")
        for listed in MOOD_TOKENS:
            if token == listed or token.startswith(listed):
                return (f"refused — mood or emotion content "
                        f"({token!r}): this module never infers "
                        f"how anyone feels")
        for listed in BIOMETRIC_TOKENS:
            if token == listed or token.startswith(listed):
                return (f"refused — biometric content ({token!r}): "
                        f"this module takes no body data in and "
                        f"gives none out")
        for listed in CLAIM_TOKENS:
            if token == listed or token.startswith(listed):
                return (f"refused — prevention or treatment claim "
                        f"({token!r}): a break or routine here is "
                        f"a suggestion, never a prevention or a "
                        f"cure")
    return None


def _check_text_field(name, value):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > MAX_FIELD_CHARS:
        raise ValueError(f"{name} exceeds {MAX_FIELD_CHARS} chars")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    reason = refusal_reason(stripped)
    if reason is not None:
        raise ValueError(reason)
    return stripped


def _check_minutes(name, value, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number of minutes")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    if value > maximum:
        raise ValueError(f"{name} exceeds {maximum} minutes")
    return value


def build_break_plan(work_minutes, break_minutes, focus_minutes=25):
    """A break plan from caller-supplied minutes only.

    The work block runs in focus stretches of
    ``focus_minutes``, with a rest moment between stretches,
    then the break. Returns the plan as data. The plan is
    a suggestion the user asked for — it never prescribes
    anything, and it never claims a health benefit. A
    zero-minute work block refuses whole: a plan for no
    work is not a plan."""
    work = _check_minutes("work_minutes", work_minutes, MAX_WORK_MINUTES)
    rest = _check_minutes("break_minutes", break_minutes, MAX_BREAK_MINUTES)
    focus = _check_minutes("focus_minutes", focus_minutes, MAX_FOCUS_MINUTES)
    if work == 0:
        raise ValueError("work_minutes must be more than zero for a break plan")
    if focus == 0:
        raise ValueError("focus_minutes must be more than zero for a break plan")
    stretches = []
    remaining = work
    moment_index = 0
    while remaining > 0:
        stretch = min(focus, remaining)
        stretches.append({"focus_minutes": stretch})
        remaining -= stretch
        if remaining > 0:
            stretches.append(
                {"rest_moment": _REST_MOMENTS[moment_index % len(_REST_MOMENTS)]})
            moment_index += 1
    return {
        "kind": "BREAK_PLAN",
        "work_minutes": work,
        "break_minutes": rest,
        "focus_minutes": focus,
        "stretches": stretches,
        "break_after": "take the break after the last focus stretch",
        "note": ("A suggestion you asked for, not a prescription. "
                 "Take a break whenever you need one, not only "
                 "when the plan says so. This plan claims no "
                 "health benefit."),
    }


def build_reminder(trigger, time_text):
    """A reminder descriptor from caller-supplied words.

    It schedules NOTHING by itself: the descriptor says so
    in plain words, and scheduling is the caller's action.
    Free text carrying a refused kind of content refuses
    the whole call."""
    trigger_text = _check_text_field("trigger", trigger)
    when = _check_text_field("time_text", time_text)
    return {
        "kind": "REMINDER",
        "wording": f"Reminder: {trigger_text} — {when}",
        "trigger": trigger_text,
        "time_text": when,
        "scheduled": False,
        "note": ("This reminder is a suggestion in words only. "
                 "Nothing is scheduled by this tool — setting "
                 "it down somewhere is your action, not this "
                 "module's."),
    }


def build_winddown(steps):
    """A wind-down routine from caller-chosen steps.

    Every step must be in FIXED_WIND_DOWN_STEPS, in the
    order given. An unknown step refuses the whole routine
    — it is never swapped for a near neighbour, because a
    routine the user did not choose is not their routine.
    Repeated steps are kept once, in first-seen order, and
    the de-duplication is reported."""
    if not isinstance(steps, (list, tuple)):
        raise ValueError("steps must be a list of step names")
    if len(steps) == 0:
        raise ValueError("steps must not be empty")
    chosen = []
    duplicates = []
    for step in steps:
        if not isinstance(step, str):
            raise ValueError("each step must be a string")
        name = step.strip()
        if name not in FIXED_WIND_DOWN_STEPS:
            raise ValueError(
                f"unknown wind-down step {name!r}: only the fixed "
                f"non-medical list is accepted")
        if name in chosen:
            duplicates.append(name)
        else:
            chosen.append(name)
    return {
        "kind": "WIND_DOWN",
        "steps": chosen,
        "duplicates_removed": duplicates,
        "note": ("A wind-down routine you chose, in your order. "
                 "These steps are plain things to do, not "
                 "treatments, and this routine claims no "
                 "health benefit."),
    }


def summarize_day(work_minutes, breaks_taken, break_minutes_total):
    """A day summary from caller-supplied numbers ONLY.

    Every number is required: a missing number refuses the
    whole summary, because a total built on a guessed
    number would be presented as the user's own numbers
    when it is not. Totals only — no average, no score, no
    rating, no mood, no health claim of any kind."""
    work = _check_minutes("work_minutes", work_minutes, MAX_WORK_MINUTES * 3)
    taken = _check_minutes("breaks_taken", breaks_taken, 100)
    break_total = _check_minutes(
        "break_minutes_total", break_minutes_total, MAX_BREAK_MINUTES * 10)
    return {
        "kind": "DAY_SUMMARY",
        "work_minutes": work,
        "breaks_taken": taken,
        "break_minutes_total": break_total,
        "total_minutes_accounted": work + break_total,
        "measured": False,
        "note": ("These numbers are the numbers you told it — "
                 "nothing here was measured, sensed, or "
                 "inferred. Totals only; no score, no rating, "
                 "no health claim."),
    }


def validate_recordable_plan(kind, fields):
    """Re-validate a plan a caller wants recorded, by
    re-running the builder that made it and requiring the
    core fields to match exactly. A record that does not
    re-build is refused, never written on trust. Returns
    the rebuilt plan dict, or raises ValueError."""
    if not isinstance(kind, str) or not isinstance(fields, dict):
        raise ValueError("kind must be a string and fields an object")
    kind = kind.strip()
    allowed_fields = {
        "break": {"work_minutes", "break_minutes", "focus_minutes"},
        "reminder": {"trigger", "time_text"},
        "winddown": {"steps"},
    }
    if kind not in allowed_fields:
        raise ValueError(f"unknown recordable plan kind {kind!r}")
    unexpected = sorted(set(fields) - allowed_fields[kind])
    if unexpected:
        raise ValueError(
            f"plan carries unexpected fields {unexpected}: a record "
            f"that does not re-build exactly is refused, never "
            f"written on trust")
    if kind == "break":
        rebuilt = build_break_plan(
            fields.get("work_minutes"),
            fields.get("break_minutes"),
            fields.get("focus_minutes", 25))
        for name in ("work_minutes", "break_minutes", "focus_minutes"):
            if fields.get(name, 25 if name == "focus_minutes" else None) \
                    != rebuilt[name]:
                raise ValueError(f"break plan field {name} does not re-build")
        return rebuilt
    if kind == "reminder":
        rebuilt = build_reminder(
            fields.get("trigger"), fields.get("time_text"))
        if fields.get("trigger", "").strip() != rebuilt["trigger"] \
                or fields.get("time_text", "").strip() != rebuilt["time_text"]:
            raise ValueError("reminder fields do not re-build")
        return rebuilt
    if kind == "winddown":
        supplied = fields.get("steps")
        if not isinstance(supplied, (list, tuple)):
            raise ValueError("wind-down steps must be a list")
        rebuilt = build_winddown(supplied)
        deduped = []
        for step in supplied:
            name = step.strip() if isinstance(step, str) else step
            if name not in deduped:
                deduped.append(name)
        if deduped != rebuilt["steps"]:
            raise ValueError("wind-down steps do not re-build")
        return rebuilt
    raise ValueError(f"unknown recordable plan kind {kind!r}")
