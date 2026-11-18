"""Taint state and lifecycle (split from core/injection.py, B9).

SECURITY SEAM: every taint global (_tainted, _taint_source,
_taint_kind, _taint_segment_counter, _sources_seen, _taint_sources,
_in_flight, _clear_pending, _taint_lock) and every writer and reader
of those globals lives in THIS module. Splitting writers from
readers would silently break the defence (a reader in another module
would see its own module's untouched globals). The lock travels with
the state it guards.
"""

from __future__ import annotations

import threading

# INTENTIONAL: cross-part names from the defining modules (functions,
# never mutable state).
from .injection_scan import looks_like_injection
from .injection_secondpass import _classifier_escalates

# Taint state. Module-level on purpose: producers (browser worker,
# Google fetch, OCR) and consumers (session clients, the executor)
# live on different threads and in different modules; the session is
# the unit of trust, and VYUK runs one session at a time.
# --------------------------------------------------------------------- #

_taint_lock = threading.Lock()
_tainted = False
_taint_source: str | None = None
# Taint kind (v5.46, programme item 2, D-C1/D-C2): None while
# untainted, "soft" for provenance-only taint, "hard" once the
# pattern screen has tripped. Sticky in one direction only: a
# pattern hit upgrades soft -> hard; nothing downgrades hard
# except a clear (clear_taint / _apply_clear resets to None).
_taint_kind: str | None = None
# Segment counter (v5.46, programme item 2): incremented on
# EVERY _apply_clear, so a scoped approval granted in one
# session-segment can be bound to it (core/funnel/tools_gate.py screen scope)
# and can never silently revive in the next segment after a
# clear + fresh note. Exposed as taint_segment().
_taint_segment_counter = 0

# Soft-taint sources (v5.46, programme item 2, D-C2): the
# external-content classes whose mere ARRIVAL soft-taints the
# session even when the pattern screen does not trip. Built from
# a caller audit of every note_untrusted / wrap_untrusted call
# site in the tree (labels are the exact strings producers pass):
#   web       — "web page" (core/browser, producer seam),
#               "RSS feed" (producer seam), "web search" and
#               "news search" (tools' internal web producers)
#   email     — "email" (google_services, skills_productivity_day),
#               "email attachment" (tools download handler +
#               producer seam)
#   documents — "file", "image file" (producer seam)
#   calendar  — "calendar" (google_services,
#               skills_productivity_day)
#   screen    — "screen text" (OCR: tools, control,
#               local_client), "screen description" (the local
#               client's VLM description OF a frame — a
#               description of screen pixels is screen content),
#               "image" (note_image_frame)
# Deliberately EXCLUDED — provenance is recorded but NO soft
# taint is set: the user's own machine-state sources "command
# output", "window controls", "window title", "filenames" and
# "clipboard". Their text is produced ON the user's machine by
# actions the user asked VYUK to take (run this command, list
# these files), not delivered from an outside author; soft-
# tainting them would popup-gate the session after nearly every
# tool result and train the user to wave popups through. They
# stay enveloped and pattern-screened exactly as before — a
# pattern hit in any of them still hard-taints.
_SOFT_TAINT_SOURCES = frozenset({
    "web page", "RSS feed", "web search", "news search",
    "email", "email attachment",
    "file", "image file",
    "calendar",
    "screen text", "screen description", "image",
})
# Provenance (v5.45, H18): EVERY source seen this session-segment,
# in first-seen order, and the subset that actually tainted. The
# single first-source above stays the headline (taint_source());
# the lists let a refusal name everything that fed the session.
_sources_seen: list[str] = []
_taint_sources: list[str] = []
# Taint-clear deferral (v5.45, H18): a clear must never outrun a
# tool still executing — a result landing mid-turn could re-taint
# (or be judged) under trust state that was cleared beneath it.
# ToolRegistry.execute() brackets handler runs with
# begin_execution()/end_execution(); clear_taint() while the
# counter is non-zero is QUEUED and applied when the funnel drains.
_in_flight = 0
_clear_pending = False


def _remember_source(source: str, tainting: bool) -> None:
    """Provenance bookkeeping; caller holds _taint_lock."""
    if source and source not in _sources_seen:
        _sources_seen.append(source)
    if tainting and source and source not in _taint_sources:
        _taint_sources.append(source)


def note_untrusted(source: str, text: str) -> None:
    """Records a piece of external content. If it screens as an
    injection attempt, the session taints HARD (and remembers the
    FIRST tainting source — later sources don't overwrite the
    evidence). The source is remembered in the provenance list
    either way (unchanged by v5.46).

    v5.46 (programme item 2, D-C2): with NO pattern hit, content
    from an external-content source (_SOFT_TAINT_SOURCES) now
    taints SOFT — arrival alone is grounds for caution on
    high-impact actions, because the pattern screen honestly
    misses paraphrased instructions. A later pattern hit
    upgrades soft to hard; hard never downgrades until a clear.
    Machine-state sources outside the set keep the pre-v5.46
    behaviour exactly: provenance only, no taint without a hit.

    v5.49 (programme item 37): the second-pass classifier may
    additionally escalate ANY provenance-only content — including
    those excluded machine-state sources — to SOFT when its
    heuristic score reaches threshold. Soft only, never hard."""
    global _tainted, _taint_source, _taint_kind
    hit = False
    try:
        hit = bool(looks_like_injection(text))
    except Exception:  # noqa: BLE001 — the screen must never break a producer
        hit = False
    # Second pass (v5.49, programme item 37): consulted ONLY when
    # the screen did not trip — a pattern hit is hard regardless
    # of the classifier, and the classifier can never soften it.
    classifier_soft = False
    if not hit:
        try:
            classifier_soft = bool(_classifier_escalates(text))
        except Exception:  # noqa: BLE001 — same never-break rule
            classifier_soft = False
    with _taint_lock:
        _remember_source(source, hit)
        if hit:
            if not _tainted:
                _tainted = True
                _taint_source = source
            _taint_kind = "hard"
        elif not _tainted and (source in _SOFT_TAINT_SOURCES
                               or classifier_soft):
            # v5.46 provenance soft taint, plus the classifier's
            # one taint power (v5.49 item 37): heuristic suspicion
            # escalates even an excluded machine-state source to
            # SOFT. It can never produce "hard" here.
            _tainted = True
            _taint_source = source
            _taint_kind = "soft"


def note_image_frame() -> None:
    """A frame (screenshot / shared screen / camera still) was just
    handed to a brain (v5.45, H12). Images are untrusted content
    the text screen cannot read — text baked into pixels is the
    classic visual injection — so delivery itself taints the
    session with source "image". Text-only deliveries don't come
    here: their OCR text is enveloped and screened as text
    instead.

    v5.46 (programme item 2, D-C1): frame taint is PROVENANCE
    taint — no pattern ever matched — so it is now SOFT, not
    hard (this amends H12's delivery-equals-a-hit rule for
    frames only). A session that has merely SEEN a frame can
    keep navigating inside a scoped approval; high-impact
    actions still ask, and a pattern hit in real text upgrades
    the session to hard as before."""
    global _tainted, _taint_source, _taint_kind
    with _taint_lock:
        _remember_source("image", True)
        if not _tainted:
            _tainted = True
            _taint_source = "image"
            _taint_kind = "soft"


def tainted() -> bool:
    """Whether untrusted content has tainted this session since
    the last genuine user message — True for BOTH kinds (soft
    provenance taint and hard pattern taint, v5.46 item 2)."""
    with _taint_lock:
        return _tainted


def taint_kind() -> str | None:
    """The taint kind (v5.46, programme item 2): "hard" when the
    pattern screen tripped, "soft" for provenance-only taint,
    None when untainted. The executor's tiering keys on this."""
    with _taint_lock:
        return _taint_kind


def taint_segment() -> int:
    """The current session-segment number (v5.46, programme
    item 2): incremented on every clear, so approvals bound to
    a segment (core/funnel/tools_gate.py screen scope) die with it."""
    with _taint_lock:
        return _taint_segment_counter


def taint_source() -> str | None:
    """The producer source ("web page", "email", …) that first
    tainted the session, or None when untainted."""
    with _taint_lock:
        return _taint_source


def taint_sources() -> list[str]:
    """Every source that tainted this session-segment, first-seen
    order (H18) — the refusal message can name them all."""
    with _taint_lock:
        return list(_taint_sources)


def sources_seen() -> list[str]:
    """Every untrusted source seen this session-segment, tainting
    or not, first-seen order (H18 provenance)."""
    with _taint_lock:
        return list(_sources_seen)


def in_flight() -> int:
    """How many tool executions are inside the funnel right now."""
    with _taint_lock:
        return _in_flight


def clear_pending() -> bool:
    """True when a clear_taint() is queued behind in-flight tools."""
    with _taint_lock:
        return _clear_pending


def begin_execution() -> None:
    """Mark one tool execution in flight (the execute() funnel)."""
    global _in_flight
    with _taint_lock:
        _in_flight += 1


def end_execution() -> None:
    """Mark one tool execution finished; a queued clear applies
    the moment the last in-flight execution drains."""
    global _in_flight
    with _taint_lock:
        _in_flight = max(0, _in_flight - 1)
        drain = _in_flight == 0 and _clear_pending
    if drain:
        _apply_clear()


class ExecutionBracket:
    """Exactly-once end ownership for one bracketed execution
    (audit fix A5, master P1-3 nuance).

    The funnel's deadline worker and its caller race: the worker
    finishes while the caller is deciding it timed out. The old
    boolean handoff lost that race — a handler finishing between
    join() returning and the caller's timeout flag being set left
    the bracket begun-but-never-ended (begin=1, end=0), stalling
    every queued taint clear. This protocol resolves every
    interleaving under one lock to exactly one ender:

    - worker_finished(): the worker's finally. Returns True only
      when the caller already returned on timeout (the worker
      must end); otherwise records "handler_done" and returns
      False (the caller ends).
    - caller_timed_out(): the caller's timeout decision. Returns
      (caller_ends_now, worker_owns): when the handler already
      finished in the race window, the caller ends immediately;
      otherwise the worker owns the end.
    - caller_finished(): the caller's finally. Returns True
      unless the worker owns the end or it already ended.

    Every path — success, failure, timeout, cancellation — ends
    the bracket exactly once: exactly one of the three methods
    returns True across the whole execution, and later calls
    return False. end_execution() itself is still the caller's
    job; this class only decides WHO calls it.
    """

    def __init__(self) -> None:
        import threading as _threading

        self._lock = _threading.Lock()
        # "caller" | "worker" | "handler_done" | "ended"
        self._mode = "caller"

    def worker_finished(self) -> bool:
        with self._lock:
            if self._mode == "worker":
                self._mode = "ended"
                return True
            if self._mode == "caller":
                self._mode = "handler_done"
            return False

    def caller_timed_out(self) -> tuple[bool, bool]:
        with self._lock:
            if self._mode == "handler_done":
                self._mode = "ended"
                return True, False
            self._mode = "worker"
            return False, True

    def caller_finished(self) -> bool:
        with self._lock:
            if self._mode in ("caller", "handler_done"):
                self._mode = "ended"
                return True
            return False


def _apply_clear() -> None:
    global _tainted, _taint_source, _clear_pending
    global _taint_kind, _taint_segment_counter
    with _taint_lock:
        _tainted = False
        _taint_source = None
        # v5.46 (programme item 2): a clear resets the kind and
        # closes the segment — segment-bound approvals (screen
        # scope) must not survive into the next segment.
        _taint_kind = None
        _taint_segment_counter += 1
        _taint_sources.clear()
        _sources_seen.clear()
        _clear_pending = False


def clear_taint(force: bool = False) -> bool:
    """A genuine user message arrived: the user is driving again, so
    the standing taint lifts. Called by the session clients (typed
    text / transcribed speech / fresh session start) — never by
    producers, never for VYUK-internal bracketed injections.

    H18: while a tool execution is in flight the clear is QUEUED,
    not applied — it lands when the funnel drains (end_execution),
    so a clear can never outrun a tool still executing. Returns
    True when the taint is clear on return, False when the clear
    was deferred. force=True (session teardown / tests) clears
    immediately regardless."""
    global _clear_pending
    if force:
        _apply_clear()
        return True
    with _taint_lock:
        if _in_flight > 0:
            _clear_pending = True
            return False
    _apply_clear()
    return True
