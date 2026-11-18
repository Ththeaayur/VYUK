from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.
import datetime

from .summary import summarise_text  # R8-3 sumy port: real summaries, not first-5-lines

# ===========================================================================
# Meeting notes — capture-guard-gated WITHOUT exception (§8 S-6)
# ===========================================================================

class MeetingCaptureRefused(RuntimeError):
    """Raised / returned when any meeting-capture gate is missing."""


# Denylist mirrors the capture-guard posture for meeting capture:
# password managers, authenticators, private browsing, banking.
_MEETING_DENY_PROCESSES = frozenset({
    "1password", "lastpass", "bitwarden", "keepass", "keepassxc",
    "dashlane", "nordpass", "roboform", "authy", "enpass",
    "keeper", "protonpass", "quicken", "moneydance", "gnucash",
})
_MEETING_DENY_TITLES = frozenset({
    "password manager", "incognito", "inprivate",
    "private browsing", "private window", "password vault",
    "authenticator", "online banking", "internet banking",
    "bank login", "banking login",
})


def _denylist_hit(process_name, title) -> str | None:
    proc = str(process_name or "").strip().lower()
    if proc.endswith(".exe"):
        proc = proc[:-4]
    proc = proc.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if proc and proc in _MEETING_DENY_PROCESSES:
        return f"foreground process '{process_name}' is denylisted"
    lowered = str(title or "").strip().lower()
    if lowered:
        for keyword in _MEETING_DENY_TITLES:
            if keyword in lowered:
                return f"foreground title matches denylist keyword '{keyword}'"
    return None


class MeetingGate:
    """The three S-6 gates, all required, no exceptions:

      * opt_in — the user opted into meeting capture;
      * indicator_on — the on-screen capture indicator is live;
      * denylist — the foreground is not a denylisted surface
        (checked via the caller-supplied foreground probe, which
        returns (process_name, title) or None when unknown).

    ``check()`` returns (allowed, reason). ``require()`` raises
    MeetingCaptureRefused. An unknown foreground is NOT treated
    as denylisted (consistent with the capture guard), but a
    missing opt-in or a dark indicator refuses regardless.
    """

    def __init__(self, opt_in: bool = False, indicator_on: bool = False,
                 foreground_probe=None):
        self.opt_in = bool(opt_in)
        self.indicator_on = bool(indicator_on)
        self.foreground_probe = foreground_probe

    def check(self):
        if not self.opt_in:
            return False, ("meeting capture refused — meeting capture "
                           "is not opted in. Opt in first; nothing "
                           "was captured.")
        if not self.indicator_on:
            return False, ("meeting capture refused — the capture "
                           "indicator is not on. Capture without a "
                           "visible indicator is never allowed.")
        probe = self.foreground_probe
        if probe is not None:
            try:
                result = probe()
            except Exception:  # noqa: BLE001 — broken probe: unknown
                result = None
            if result is not None:
                if isinstance(result, dict):
                    proc = result.get("process") or result.get("process_name")
                    title = result.get("title")
                else:
                    try:
                        proc, title = result
                    except (TypeError, ValueError):
                        proc, title = None, None
                hit = _denylist_hit(proc, title)
                if hit is not None:
                    return False, (f"meeting capture refused — {hit}; "
                                   "denylisted surfaces are never captured.")
        return True, ""

    def require(self) -> None:
        allowed, reason = self.check()
        if not allowed:
            raise MeetingCaptureRefused(reason)


def detect_meeting(calendar_events, mic_active: bool = False):
    """Detect a meeting from calendar events + mic state.

    ``calendar_events`` is a list of dicts with "title" and
    "start"/"end" (datetimes or ISO strings) — the caller owns
    the calendar read; this function only matches. Returns the
    matching event dict, or None. A calendar event overlapping
    now counts; mic activity alone never declares a meeting
    (it only corroborates a calendar event).
    """
    now = datetime.datetime.now()
    for event in calendar_events or []:
        if not isinstance(event, dict):
            continue
        start = event.get("start")
        end = event.get("end")
        try:
            if isinstance(start, str):
                start = datetime.datetime.fromisoformat(start)
            if isinstance(end, str):
                end = datetime.datetime.fromisoformat(end)
        except ValueError:
            continue
        if start is not None and end is not None and start <= now <= end:
            return event
    return None


_ACTION_VERBS = (
    "action", "todo", "follow up", "follow-up", "will do", "shall",
    "must", "need to", "assign", "owner", "due", "send", "prepare",
    "book", "call", "email", "share", "review", "deliver",
)


def _extract_action_items(transcript: str) -> list[str]:
    items = []
    for line in str(transcript or "").splitlines():
        stripped = line.strip(" \t-•*").strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith(("action:", "todo:", "action item:")):
            items.append(stripped)
        elif any(verb in lowered for verb in _ACTION_VERBS) and len(stripped) > 8:
            items.append(stripped)
    # De-duplicate, keep order.
    seen = set()
    unique = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def produce_meeting_notes(transcript, gate: MeetingGate,
                          meeting_title: str = ""):
    """Produce notes + action items from a transcript.

    Returns (notes_dict, refusal_or_None). When any gate is
    missing, NOTHING is produced — the return is (None, reason)
    and the transcript is not processed. Notes are local
    organisation of the supplied transcript, not a brain call.
    """
    allowed, reason = gate.check()
    if not allowed:
        return None, reason
    text = str(transcript or "")
    # R8-3 sumy port: the summary is an extractive TextRank pick
    # of the sentences that carry the meeting — not the first 5
    # lines. Short transcripts return whole (the meetily pin:
    # "hello world\nAction: ship it" still summarises to itself).
    summary = summarise_text(text)
    summary_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    notes = {
        "title": str(meeting_title or "Meeting notes"),
        "captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "summary": summary["summary"],
        "summary_method": summary["method"],
        "summary_sentences": summary["sentence_count"],
        "action_items": _extract_action_items(text),
        "line_count": len(summary_lines),
    }
    return notes, None


