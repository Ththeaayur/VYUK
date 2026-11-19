"""Voice-drop causes (Fix 4, 2026-11-05): ONE classification for
every way a Live session can drop, carried from the drop point to
the caption and to data/activity.log.

THE LOOPHOLE FAMILY (proven from the tree before this module
existed — see the batch report, not this docstring alone):

  L1  No drop cause existed anywhere (grep `_last_drop|_drop_cause`
      over core/ + gui/ was empty). A socket close, a server end,
      a refused resumption handle, a watchdog silence and a mic
      stall all presented identically ("Connection dropped" /
      "Press Connect"), and only ERROR statuses reached
      activity.log — a bare DISCONNECTED logged nothing. Neither
      the user nor the sendable log could say what happened.
  L2  Resume-handle refusal was invisible above the client: the
      client already goes cold cleanly inside one connect
      attempt (live_client_lifecycle._try_candidates), but the
      GUI could not name it, so a refused handle looked like
      any other drop.
  L3  Zombie cross-episode loop: _note_session_live ended a
      reconnect episode on LISTENING and reset the attempt
      counter to 0. A session that connects, reports LISTENING,
      then goes quiet 7 s later (watchdog) starts a FRESH
      episode with a full 4 attempts — forever. The watchdog
      streak counted the episodes but never fed the budget.
  L4  Give-up was not terminal: _reconnect_give_up left
      _was_connected True, so any late ERROR/DISCONNECTED
      status started a NEW episode after the user was told to
      press Connect. Give-up was also caption-only: no chat
      line, no log line.
  L5  Give-up was not one-tap recoverable when the worker was
      wedged: _attempt_reconnect's refused start left the
      worker alive, so the button showed "Cancel", not
      "Connect" — recovery took two taps.
  L6  Mic stall never surfaced: the mic watchdog reopens the
      stream locally and tells nobody; mic_status() already
      exposes callback_stalled, but nothing read it as a drop
      cause.
  L7  (OUT OF SCOPE, Fix 9) A drop mid-task does not pause the
      task: no caller of coordinator pause_task() exists
      outside tests, and no DISCONNECTED hook exists in
      core/brain/task_coordinator*.py. This batch does not
      half-build it.

R2 (2026-11-06) — the status-less worker death. Fix 4 left one
hole open, stated in its own report: a worker that dies
WITHOUT emitting any status leaves the GUI showing
"Listening…" with no timer and no drop, because every drop
path in Fix 4 starts from a status the worker emits. The
shapes, proven from the tree:
  * a wedged event loop — the thread is alive but the loop
    never processes another callback, so no status, no
    transcript, no tool result ever arrives. The RS-1
    watchdog cannot fire: it is an asyncio task ON the
    same loop. BackendWorker.run only emits ERROR on a
    Python exception from run_until_complete — a wedged
    loop raises nothing — and start()'s finally
    (DISCONNECTED + epoch bump) never runs while wedged.
  * a thread dead with no status — native-level death, or
    an exception in run()'s own machinery outside the
    coroutine: the mic watch (2 s, the ONLY periodic
    worker check) sees is_alive() False and just stops
    itself, declaring nothing.
  * an attempt in flight that never answers — the
    reconnect timer already fired, _reconnect_pending
    stays True, no timer runs, no status ever arrives.
The R2 cause below is one cause for all three shapes: the
worker stopped responding. Detection lives on the GUI
thread (a QTimer + a loop probe), never on the worker's
own loop, and feeds the same one-drop-record / one-budget
/ one-terminal-give-up machinery — never a second path.
One hard rule from the tree: the declaration NEVER calls
worker.stop() — live_session.stop() clears the saved
resumption handle (a clean close), and an unexpected
drop must keep its handle for the resume-first reconnect
(the watchdog death's own rule). A wedged worker frees
its slot through the existing refused-start accounting
and the give-up path, not through a stop here.

THE ONE DESIGN — "one drop record, one budget, one terminal
give-up":

  * One pure classifier here. The GUI records exactly one
    DropRecord per fall (first, most specific cause wins; a
    later status for the same fall never overwrites it) and
    carries it to the caption and to activity.log.
  * One cross-episode budget: consecutive episodes that end
    WITHOUT a real server response (SPEAKING/THINKING — the
    same bar the watchdog streak already uses) count against
    a budget of 3; LISTENING alone never resets it. Episodes
    more than one resumption-handle lifetime apart (600 s,
    the same window live_client_resume enforces) are a new
    incident and reset the count. At 3, the next drop gives
    up instead of starting a fourth episode.
  * One terminal give-up: a latch. While latched no drop
    starts a new episode, the wedged worker is stopped so
    Connect is one tap, and the give-up is posted once in
    chat and once in the log. Only a human Connect clears
    the latch.

REJECTED ALTERNATIVES (with reasons):
  * More attempts with longer backoff (e.g. 8 attempts to
    minutes): delays the zombie loop, does not close it —
    LISTENING still resets the counter.
  * Infinite reconnect: spins forever on a dead key or a
    dead network; the fatal case already proves some drops
    can never succeed.
  * No time window on the episode budget: unrelated drops
    hours apart would accumulate into a false give-up.
  * Per-cause attempt counts: more knobs for the same class;
    the client already handles per-cause resume policy, the
    GUI only needs one budget plus the cause name.
  * Driving resume-vs-cold from the GUI: the handle lives in
    the client; duplicating the decision risks a mode-wrong
    config (the v5.51 transparent lesson). The GUI names the
    cause; the client keeps the decision.
  * Auto-Connect from give-up after a delay: takes the choice
    back after telling the user to press Connect, and loops
    while the cause persists.

This module is stdlib-only and Qt-free on purpose: the
classifier is unit-testable on both interpreters without a
GUI, and the GUI mixin only imports these pure functions.
"""

from __future__ import annotations

# The drop causes, in classification-precedence order for the
# ambiguous cases (see classify_drop): a status can carry two
# markers at once (a refused handle also carries "1008"), so
# the order below is load-bearing, not alphabetical.
DROP_CAUSES = (
    "fatal",
    "mic_stall",
    "watchdog_silence",
    "no_reply",
    "worker_died_silent",
    "already_connected",
    "handle_refused",
    "server_ended",
    "network",
    "unknown",
)

# Plain-words reconnecting captions, one per cause. Each says
# what happened and that a reconnect is running — never a bare
# "reconnecting", never a technical code.
DROP_CAPTIONS = {
    "fatal": "That needs fixing first — see the message above.",
    "mic_stall": "The microphone stopped responding — reopening it…",
    "watchdog_silence": (
        "The connection went quiet after you spoke — reconnecting…"),
    "no_reply": (
        "The live session never answered — reconnecting…"),
    "worker_died_silent": (
        "The voice worker stopped responding — reconnecting…"),
    "already_connected": (
        "Google still held the old session — waiting, then "
        "starting fresh…"),
    "handle_refused": (
        "The old session couldn't be resumed — starting fresh…"),
    "server_ended": "Google ended the session — reconnecting…",
    "network": "The connection to Google dropped — reconnecting…",
    "unknown": "The connection dropped — reconnecting…",
}

# Give-up captions, one per cause. Every one keeps the pinned
# sentence shape — "Couldn't reconnect" + "press Connect" —
# so the give-up state is recognisable by text as well as by
# pill, and the cause is named instead of swallowed.
DROP_GIVE_UP_CAPTIONS = {
    "fatal": (
        "Couldn't reconnect — that needs fixing first; "
        "press Connect when you're ready."),
    "mic_stall": (
        "Couldn't reconnect — the microphone kept failing; "
        "press Connect when you're ready."),
    "watchdog_silence": (
        "Couldn't reconnect — the connection kept going quiet "
        "after you spoke; press Connect when you're ready."),
    "no_reply": (
        "Couldn't reconnect — the live session kept never "
        "answering; press Connect when you're ready."),
    "worker_died_silent": (
        "Couldn't reconnect — the voice worker kept not "
        "responding; press Connect when you're ready."),
    "already_connected": (
        "Couldn't reconnect — Google kept holding the old "
        "session; press Connect when you're ready."),
    "handle_refused": (
        "Couldn't reconnect — the old session couldn't be "
        "resumed; press Connect when you're ready."),
    "server_ended": (
        "Couldn't reconnect — Google kept ending the session; "
        "press Connect when you're ready."),
    "network": (
        "Couldn't reconnect — the connection to Google kept "
        "dropping; press Connect when you're ready."),
    "unknown": (
        "Couldn't reconnect — press Connect when you're ready."),
}

# Substring markers, matched against the lowered status text.
# "already connected" is checked BEFORE the handle markers:
# the field error carries both the 1008 code and that phrase,
# and a stale-first check would misname it (the LIVEFIX
# ordering lesson from live_client_resume).
_ALREADY_CONNECTED_MARKERS = ("already connected", "existing client")
_HANDLE_MARKERS = (
    "1008", "resumption", "resume", "session handle",
    "handle invalid", "handle expired", "invalid handle",
)
_NETWORK_MARKERS = (
    "timed out", "timeout", "connection", "network",
    "unreachable", "getaddrinfo", "dns", "ssl", "1006",
    "name or service",
)
_SERVER_END_MARKERS = (
    "goaway", "go away", "server closed", "session ended",
)


def classify_drop(status: str = "", *, fatal: bool = False,
                  watchdog: bool = False, go_away: bool = False,
                  mic_stalled: bool = False, silent: bool = False,
                  no_reply: bool = False) -> str:
    """One cause for a drop, from the status text plus the
    context only the drop point knows. Precedence is the
    design (see the module docstring): fatal first, then the
    causes no status text can name (mic stall, watchdog,
    no-reply, silent worker death), then already-connected before
    handle-refused, then server-ended, then network, then
    unknown — never a guess dressed as a diagnosis."""
    if fatal:
        return "fatal"
    if mic_stalled:
        return "mic_stall"
    if watchdog:
        return "watchdog_silence"
    if no_reply:
        return "no_reply"
    if silent:
        return "worker_died_silent"
    low = (status or "").lower()
    if any(m in low for m in _ALREADY_CONNECTED_MARKERS):
        return "already_connected"
    if any(m in low for m in _HANDLE_MARKERS):
        return "handle_refused"
    if go_away or any(m in low for m in _SERVER_END_MARKERS):
        return "server_ended"
    if any(m in low for m in _NETWORK_MARKERS):
        return "network"
    return "unknown"


def reconnect_caption(cause: str) -> str:
    """The plain-words caption while a reconnect is running."""
    return DROP_CAPTIONS.get(cause or "unknown",
                             DROP_CAPTIONS["unknown"])


def give_up_caption(cause: str) -> str:
    """The plain-words caption for the terminal give-up state."""
    return DROP_GIVE_UP_CAPTIONS.get(cause or "unknown",
                                     DROP_GIVE_UP_CAPTIONS["unknown"])


def drop_log_line(cause: str, raw: str = "") -> str:
    """One sendable-log line for a drop: the cause first, the
    raw status verbatim after it, so the log names the class
    and preserves the evidence."""
    raw = (raw or "").strip()
    return f"[GUI] DROP {cause}: {raw}" if raw else f"[GUI] DROP {cause}"
