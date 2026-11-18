"""Spoken rendering of tool errors (v5.51, M-4).

What the user HEARS when a tool fails must never be the raw
error: brains receive core/local_client._result_to_text's
"Error: {message}" (message text can carry exception wording,
file paths, errno-style detail), and several speech paths speak
a result-derived line directly. This module is the ONE place
that turns an error result into a line fit to say aloud:

  * one calm butler line per H8 kind (the taxonomy
    core/funnel/tools_gate produces: validation / unavailable /
    refused / not_confirmed / timeout / handler_error /
    budget) — plain words, no engineer-speak, no apology-spam;
  * when the result carries the H-1 failure script
    (result["failure"] = {"tried", "next"}, core/
    failure_script.py), its "next" line is REUSED verbatim
    after the kind line — it was written to read well aloud,
    and duplicating its wording here would let the two drift;
  * an unknown / missing kind falls back to the generic line.

Chat and text display keep the full detail — only the SPOKEN
rendering goes through here. Wired at the speech choke points
listed in the v5.51 M-4 suite (test_v551_m4.py): the state-
guard repairs and the last-result fallbacks in
core/voice/local_client.py, lite_client's intent reply speech,
cloud_text_client's state-guard repair, and a last-resort net
inside LocalLiveSession._speak for any text that still arrives
headed "Error: …". Gemini Live renders its own audio
server-side from model free text, so no local choke point
exists there — stated honestly, not papered over.
"""

from typing import Any

# v5.51 (M-4) copy conventions, shared with the X-1 micro-copy
# pass: the product is "VYUK" (always capitalised); plain words
# only (never "exception", "traceback", "errno", "null", "stack
# trace"); at most one plain statement of fact — no apology
# spam; the same thing keeps the same name everywhere.
_KIND_LINES = {
    "validation": (
        "That request wasn't in quite the right shape, "
        "so nothing was run."),
    "unavailable": "That isn't available right now.",
    "refused": "I can't do that one — it's blocked for safety.",
    "not_confirmed": (
        "That needed your OK in VYUK's window, and it didn't "
        "get it, so I didn't run it."),
    "timeout": "That took too long, so I stopped.",
    "handler_error": (
        "Something went wrong with that — "
        "I've noted what happened."),
    "budget": (
        "I've taken a lot of steps just now, so I stopped "
        "there. Ask me again in a moment."),
}

GENERIC_LINE = _KIND_LINES["handler_error"]


def spoken_error_text(result: Any) -> str:
    """The line to SAY for one tools.execute() result.

    For an error dict: the kind's butler line, plus the H-1
    failure script's "next" line verbatim when one is present.
    The raw `message` is never included. For anything that is
    not an error dict, returns "" — callers keep their normal
    text for successes."""
    if not isinstance(result, dict):
        return ""
    # Audit A5: contract non-success statuses (plus legacy "error"
    # from direct callers) get the kind's butler line; successes
    # return "", exactly as before.
    from ..funnel.tool_result import is_non_success as _is_non_success  # Phase 3 V1a-2: alias _is_non_success RESTORED (V1a-2 dropped it, breaking spoken_error_text with NameError); Phase 3 F2a: tool_result moved to core/funnel/

    if not _is_non_success(result):
        return ""
    kind = str(result.get("kind") or "")
    line = _KIND_LINES.get(kind, GENERIC_LINE)
    failure = result.get("failure")
    if isinstance(failure, dict):
        nxt = str(failure.get("next") or "").strip()
        if nxt:
            return f"{line} {nxt}"
    return line


def spoken_line_for_raw_text(text: str | None) -> str | None:
    """The last-resort net for speech text: a string that still
    arrives at the speech choke point headed "Error: …" (the
    _result_to_text rendering, spoken verbatim by a direct
    path) is replaced by the generic butler line. Anything
    else returns None — the caller's text is untouched."""
    if text and str(text).lstrip().startswith("Error:"):
        return GENERIC_LINE
    return None
