"""classifier concern extracted from core/voice/local_client.py (refactor Phase 1 step 2). Behaviour identical; every name here stays importable from core.local_client."""

import re
from dataclasses import dataclass, replace


_THINK_SHAPE_RE = re.compile(
    r"\b(why|how come|explain|compare|difference|reason|analyse|analyze|"
    r"think|plan|strategy|calculate|compute|solve|prove|debug|fix|"
    r"should i|which is better|pros and cons|step by step|write|draft|"
    r"compose|summarise|summarize|translate|idea|ideas|brainstorm)\b",
    re.IGNORECASE)
_QUICK_SHAPE_RE = re.compile(
    r"\b(time|date|day|battery|volume|brightness|mute|unmute|lock|unlock|"
    r"open|launch|close|play|pause|stop|resume|next|previous|turn|set|"
    r"switch|wifi|weather|remind|timer|alarm|screenshot|clipboard)\b",
    re.IGNORECASE)
@dataclass(frozen=True)
class TurnProfileFeatures:
    """Measured features one profile decision reads (7th-B09).

    Text shape is ONE input among several, never the decider on
    its own: the old classifier read only word count + shape
    regexes, so a short command after a failed turn still earned
    the cheap profile and a long control list earned the dear
    one. Every field here is measurable in code at the site that
    fills it — nothing is a model judgment smuggled in.
    """
    words: int = 0
    questions: int = 0
    think_shape: bool = False
    quick_shape: bool = False
    tool_calls: int = 0
    distinct_tools: int = 0
    repair_rounds: int = 0
    context_pressure: float = 0.0
    prior_turn_failed: bool = False
    bad_tool_output: bool = False
    low_confidence: bool = False
    after_repair: bool = False
def features_from_text(user_text: str | None) -> TurnProfileFeatures:
    """Text-only features for one utterance (turn-start input)."""
    text = " ".join(str(user_text or "").split())
    words = text.split()
    return TurnProfileFeatures(
        words=len(words),
        questions=text.count("?"),
        think_shape=bool(_THINK_SHAPE_RE.search(text)),
        quick_shape=bool(_QUICK_SHAPE_RE.search(text)),
    )
def profile_escalation_triggers(f: TurnProfileFeatures) -> tuple[str, ...]:
    """Escalation triggers firing for these features, in order.

    - "ambiguous": shape cannot resolve the utterance — neither
      shape regex matches and the length sits in the middle band
      (13-25 words), where the old word-count rule alone decided.
    - "low_confidence": this turn already produced an unreliable
      model output (unparseable reply, invented tool, empty reply).
    - "bad_tool_output": an executed tool already returned a
      non-success result this turn.
    - "repeated_repair": a failed repair already happened
      (after_repair) or repairs have run twice or more.
    """
    triggers: list[str] = []
    if (not f.think_shape and not f.quick_shape
            and 13 <= f.words <= 25):
        triggers.append("ambiguous")
    if f.low_confidence:
        triggers.append("low_confidence")
    if f.bad_tool_output:
        triggers.append("bad_tool_output")
    if f.after_repair or f.repair_rounds >= 2:
        triggers.append("repeated_repair")
    return tuple(triggers)
def classify_turn_from_features(f: TurnProfileFeatures) -> str:
    """'quick' or 'think' from measured features (7th-B09).

    Any escalation trigger earns think, regardless of the other
    features. Otherwise think follows measured complexity — a
    reasoning shape that is not a short control command, length
    or multi-question turns, real tool use, a repair already
    spent, a nearly-exhausted context, or a failed prior turn.
    A short control utterance with none of those stays quick.
    """
    if profile_escalation_triggers(f):
        return "think"
    if ((f.think_shape and not (f.quick_shape and f.words <= 12))
            or f.words > 25 or f.questions >= 2
            or f.tool_calls >= 2 or f.distinct_tools >= 2
            or f.repair_rounds == 1
            or f.context_pressure >= 0.7
            or f.prior_turn_failed):
        return "think"
    if f.quick_shape or f.words <= 12:
        return "quick"
    return "think"
def classify_turn_profile(user_text: str | None,
                          after_repair: bool = False,
                          *,
                          prior_turn_failed: bool = False,
                          context_pressure: float = 0.0) -> str:
    """'quick' or 'think' for one turn (B13, 7th-B09).

    Turn-start entry: text features plus the session features
    known before the turn runs (whether the previous turn
    failed, how full the context already is). Mid-turn
    re-derivation uses escalate_turn_profile() instead, so a
    turn never demotes once it has escalated.
    """
    f = replace(features_from_text(user_text),
                after_repair=after_repair,
                prior_turn_failed=prior_turn_failed,
                context_pressure=context_pressure)
    return classify_turn_from_features(f)
def escalate_turn_profile(current: str,
                          *,
                          repair_rounds: int = 0,
                          bad_tool_output: bool = False,
                          low_confidence: bool = False) -> tuple[str, tuple[str, ...]]:
    """Mid-turn re-derivation (7th-B09): monotonic escalation.

    Returns (profile, triggers). Any trigger earns think; with
    no trigger the current profile stands — a turn never
    demotes once it has escalated. Callers pass the turn's
    accumulated evidence (repairs spent, whether a tool already
    failed, whether the model's own output was unreliable).
    Callers read the current profile through entry_profile()
    below — never a bare getattr with a silent default.
    """
    f = TurnProfileFeatures(
        repair_rounds=repair_rounds,
        bad_tool_output=bad_tool_output,
        low_confidence=low_confidence)
    triggers = profile_escalation_triggers(f)
    if triggers:
        return "think", triggers
    return current, ()


#: The profile a turn starts from when the session has not
#: classified one yet (GAPS-22, B-V8). The one named default,
#: in one place — the old silent ``getattr(..., "quick")`` at
#: every call site is gone.
ENTRY_PROFILE = "quick"


def entry_profile(session) -> str:
    """The profile a turn escalates from (GAPS-22, B-V8).

    The session's ``_active_profile`` when it has been set
    (the mic classifies it per turn); ``ENTRY_PROFILE`` when
    it has not — a harness-driven session that never ran
    ``_brain_turn`` reads as the entry profile, explicitly,
    instead of a silent default at each call site. A profile
    of None (the session's initial "undecided") reads the same
    way. The Ollama turn path does NOT use this: it reads the
    raw attribute (None → the fixed pre-B13 profile), and that
    behaviour is unchanged.
    """
    return getattr(session, "_active_profile", None) or ENTRY_PROFILE
