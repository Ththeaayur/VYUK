# INTENTIONAL (B27): helpers moved verbatim from core/voice/lite_client.py (old lines 48-215).
import re
from typing import Any

from . import local_assets  # Phase 3 V1b: moved to core/voice/


def _bump_epoch(reason: str) -> None:
    """v5.48 (programme item 20): advance the session epoch at a
    session end/start seam, voiding any confirmation approval
    still in flight from the previous session (core/session_epoch).
    Guarded: epoch bookkeeping never breaks a session."""
    try:
        from .. import session_epoch  # Phase 3 V1b: moved to core/voice/

        session_epoch.bump_epoch(reason)
    except Exception:  # noqa: BLE001
        pass


WATCH_INTERVAL_S = 5.0          # readiness poll cadence for the auto-upgrade
_IMAGE_REPLY_COOLDOWN_S = 60.0  # the image line repeats at most this often

_READY_LINE = "My full brain is ready — entirely at your service."
_UPGRADE_FAILED_LINE = (
    "My full brain hit a snag starting, I'm afraid — I'll keep "
    "handling the basics for now."
)
_IMAGE_LINE = (
    "I can't see images until my full brain is ready — though I can "
    "read text on your screen, if you'd like me to look."
)


# ---------------------------------------------------------------------- #
# Intent responder — small, deliberate, and honest about its limits.
# Every matched intent executes a REAL tool through tools.execute();
# anything unmatched gets the getting-ready line, never a bluff.
# ---------------------------------------------------------------------- #

_ARITHMETIC_CHARS = set("0123456789+-*/().%^ ")
_ARITHMETIC_OPERATORS = set("+-*/%^")


def _looks_like_arithmetic(text: str) -> bool:
    """A bare arithmetic expression: digits, spaces and operator
    characters only, with at least one digit and one operator.
    (Round-3 fix Q5+Q7, for the lite router's maths ask.)"""
    s = (text or "").strip()
    if not s or not set(s) <= _ARITHMETIC_CHARS:
        return False
    return (any(c.isdigit() for c in s)
            and any(c in _ARITHMETIC_OPERATORS for c in s))

def _route_intent(text: str) -> tuple[str, dict[str, Any]] | None:
    """Maps a user utterance to (tool name, args), or None.

    Order matters: the specific claims (remember / maths /
    routine / open / screenshot / schedule / mail) are tested
    before the broad ones (time / system), so "what time is my
    meeting" lands on the schedule, not the clock.
    """
    low = " ".join((text or "").lower().split())
    if not low:
        return None

    match = re.match(r"^remember\s+(?:that\s+)?(.+)$", low)
    if match:
        return ("remember_user_fact", {"key": "note", "value": match.group(1).strip()})

    # S13: a spoken reminder is the most assistant ask there is;
    # the full tool already takes minutes + message.
    match = re.match(r"^remind me in (\d{1,4}) minutes? to (.+)$", low)
    if match:
        return ("set_reminder", {"message": match.group(2).strip(),
                                 "minutes": int(match.group(1))})
    match = re.match(r"^remind me in (\d{1,4}) minutes?$", low)
    if match:
        return ("set_reminder", {"message": "Reminder",
                                 "minutes": int(match.group(1))})

    # Round-3 fix Q5+Q7: a spoken arithmetic ask routes to
    # quick_math with the expression verbatim — "what's 2+2",
    # "calculate 12 * 3". Only an explicit ask prefix plus a
    # bare arithmetic expression qualifies (digits, spaces and
    # operators); "what's on today" and "what's my name" are
    # not arithmetic and fall through to their own tools, and
    # a bare digit string (a year, a phone number) stays
    # unrouted — no prefix, no route. Function names (sqrt,
    # sin, ...) stay with the full brain, which reaches
    # quick_math through its resident declaration.
    match = re.match(
        r"^(?:what(?:'s| is)|calculate|compute|work out|how much is)\s+(.+)$",
        low)
    if match and _looks_like_arithmetic(match.group(1)):
        return ("quick_math", {"expression": match.group(1).strip()})

    # Round-3 fix Q5+Q7: a NAMED routine runs with run_routine —
    # the planner's tool, not the anticipation runner. The name
    # is required (run_routine takes one); a bare "run my
    # routine" stays unrouted rather than inventing a name. A
    # name containing "anticipation" is never stolen: it belongs
    # to run_anticipation_routine (the toolbox ranking already
    # sends that phrase there).
    match = re.match(r"^run\s+(?:my\s+|the\s+)?(.+?)\s+routine$", low)
    if match:
        name = match.group(1).strip()
        # "my" / "the" alone is not a name ("run my routine"
        # with the possessive unconsumed) — a bare routine ask
        # stays unrouted rather than inventing one.
        if (name and name not in ("my", "the")
                and "anticipation" not in name.split()):
            return ("run_routine", {"name": name})

    match = re.search(r"\b(?:open|launch|start)\s+(?:the\s+)?([a-z0-9][a-z0-9 .&'\-]{1,40})$", low)
    if match:
        return ("launch_application", {"app_name": match.group(1).strip()})

    if re.search(r"\b(screenshot|screen\s?shot|capture (?:my|the) screen)\b", low):
        return ("take_screenshot", {})

    # Round-3 fix Q6: the morning-briefing ask routes to the
    # deterministic briefing tool, not to the bare schedule — the
    # briefing renders the day's schedule itself. The bare schedule
    # ask ("my day", "my schedule") stays with get_todays_schedule
    # below, so the two never fight over one phrase.
    if re.search(r"\b(morning briefing|briefing|what'?s on today)\b", low):
        return ("get_morning_briefing", {})

    if re.search(r"\b(my day|schedule|calendar|events?|meetings?|appointments?|agenda)\b", low):
        return ("get_todays_schedule", {})

    if re.search(r"\b(unread|emails?|mails?|inbox|gmail)\b", low):
        return ("get_unread_email", {})

    # S13: clipboard read-back and the two fun tools — small
    # asks the lite brain can now simply do.
    if re.search(r"\b(what(?:'s| is) on (?:my|the) clipboard|read (?:my|the) clipboard|show (?:me )?(?:my|the) clipboard|clipboard contents?)\b", low):
        return ("read_clipboard", {})
    if re.search(r"\b(tell (?:me )?my fortune|fortune cookie|my fortune)\b", low):
        return ("fortune_cookie", {})
    if re.search(r"\briddle\b", low):
        return ("riddle_time", {})

    match = re.search(r"\bweather\b(?:\s+(?:in|for|at)\s+([a-z .'\-]+))?", low)
    if match:
        return ("get_weather", {"location": (match.group(1) or "").strip()})

    if re.search(r"\bbattery\b", low):
        return ("battery_info", {})

    # S13: volume and lock — the two control asks a lite brain
    # hears most; both tools answer with a read-back line.
    match = re.search(r"\b(?:volume|sound)\s+(up|down)\b", low)
    if match:
        return ("adjust_volume", {"action": match.group(1), "steps": 3})
    match = re.search(r"\b(?:turn|bring) (?:the )?(?:volume|sound) (up|down)\b", low)
    if match:
        return ("adjust_volume", {"action": match.group(1), "steps": 3})
    if "volume" in low and re.search(r"\b(raise|increase|louder)\b", low):
        return ("adjust_volume", {"action": "up", "steps": 3})
    if "volume" in low and re.search(r"\b(lower|decrease|quieter|softer)\b", low):
        return ("adjust_volume", {"action": "down", "steps": 3})
    if re.search(r"\bmute\b", low) and (
            "volume" in low or "sound" in low or low.strip() == "mute"):
        return ("adjust_volume", {"action": "mute", "steps": 3})
    if re.search(r"\block\b", low) and not re.search(r"\bunlock\b", low):
        return ("lock_screen", {})

    if re.search(r"(how(?:'s| is) my (pc|computer|laptop|machine)|system (status|info|health|usage)|pc (status|info|health))", low):
        return ("get_system_info", {})

    # Round-3 fix Q5+Q7: "what do you remember about X" is a
    # memory search for X — the name-only recall below stays
    # for "what's my name". A bare "what do you remember"
    # carries no topic and stays unrouted.
    match = re.match(r"^what do you remember about (.+)$", low)
    if match:
        return ("recall_memories", {"query": match.group(1).strip()})

    if re.search(r"\b(what(?:'s| is) my name|who am i|do you know me|my name)\b", low):
        return ("recall_memories", {"query": "name"})

    if re.search(r"\b(what(?:'s| is) the (time|date|day)|what time|what day|current (time|date)|tell me the time|the time|the date)\b", low):
        return ("get_current_time_date", {})

    return None


def apply_no_think(messages: list[dict]) -> list[dict]:
    """Qwen3 no-think discipline (v5.45, BRAIN B13).

    The Lite tier's model is Qwen3-1.7B — a THINKING model whose
    reasoning tokens come out of the same reply cap as the
    answer, so a lite turn could spend its whole budget thinking
    and say nothing. Qwen3's documented soft switch is the
    literal "/no_think" directive in the prompt. This helper
    returns a COPY of `messages` with "/no_think" appended to the
    last user message (or a user message added when there is
    none); the input is never mutated. core/local_client's
    _llama_chat applies it whenever the Lite tier model is the
    one generating — the lite SESSION itself runs no LLM, so the
    discipline lives at that one generation seam, and this
    helper is its canonical home."""
    out = [dict(m) for m in messages or []]
    for msg in reversed(out):
        if msg.get("role") == "user":
            content = str(msg.get("content") or "")
            if "/no_think" not in content:
                msg["content"] = (content + " /no_think").strip()
            return out
    out.append({"role": "user", "content": "/no_think"})
    return out


def _download_pct() -> int | None:
    """Best-known OVERALL download percentage (legacy helper, kept for
    compatibility; the getting-ready line now reads pack_progress()
    directly via LiteLiveSession._getting_ready_line).

    v3.2: derived from local_assets.pack_progress() — structured byte
    counts — never scraped from the manager's text lines (one file's
    "100%" line is how a former version announced a fully-downloaded
    pack that hadn't landed). Clamped to 99 until the brain is truly
    ready; None when no total is knowable yet.
    """
    try:
        progress = local_assets.pack_progress()
    except Exception:  # noqa: BLE001 — the line is a nicety, never a crash
        return None
    pct = progress.get("overall_pct")
    if pct is None:
        return None
    try:
        if local_assets.brain_ready():
            return 100
    except Exception:  # noqa: BLE001
        pass
    return max(0, min(99, int(pct)))
