"""Split from core/anticipation.py (skills split B19).
Move only: code below is verbatim from the old module apart from
module-level import lines.
"""

from __future__ import annotations


from dataclasses import dataclass

# INTENTIONAL (skills split B19): moved code resolves these by bare
# name in the gates module's globals.
from .anticipation_gates import DEFAULT_DENYLIST_PROCESSES, DEFAULT_DENYLIST_TITLE_KEYWORDS, _normalise_process, is_denylisted



# ---------------------------------------------------------------------------
# CA-1 activity stream
# ---------------------------------------------------------------------------


class ActivityRefusedError(RuntimeError):
    """emit_strict() refusal: no opt-in, no indicator kind, or no
    denylist — the observation was never turned into an event."""


class ActivitySuppressedError(RuntimeError):
    """emit_strict() suppression: the observation matched the
    denylist. The event was never constructed or stored."""


@dataclass(frozen=True)
class ActivityEvent:
    """One emitted activity observation.

    Only observations that passed the session gate AND the
    denylist ever become instances of this class. ``app`` is the
    (normalised) process/app token; ``title`` is the window title
    as supplied — denylisted titles never reach this class.
    """
    app: str
    title: str
    timestamp: float
    kind: str = "window"


class ActivityCaptureSession:
    """One per-session CA-1 capture session (S-6 posture).

    A session starts REFUSING: no opt-in, no indicator kind.
    The caller must call ``grant_opt_in()`` AND
    ``set_indicator(kind)`` before any event can be emitted.
    The denylist is applied from construction (defaults are this
    module's own lists; a caller may pass replacements, but may
    not construct a session with no denylist at all — passing
    ``None`` keeps the defaults, it never disables the list).

    The session keeps emitted events in memory only and never
    writes to disk. Ending/revoking clears the opt-in; a fresh
    session object is a fresh opt-in decision.
    """

    def __init__(self, *, processes=None, title_keywords=None,
                 indicator_listener=None):
        # None -> defaults (denylist always applied). An explicit
        # iterable (even empty) is honoured as the caller's list.
        self._processes = (frozenset(
            str(p).strip().lower() for p in processes)
            if processes is not None else DEFAULT_DENYLIST_PROCESSES)
        self._keywords = (frozenset(
            str(k).strip().lower() for k in title_keywords if str(k).strip())
            if title_keywords is not None else DEFAULT_DENYLIST_TITLE_KEYWORDS)
        self._opted_in = False
        self._indicator_kind = None
        self._indicator_listener = indicator_listener
        self._events: list = []
        self._suppressed_count = 0
        self._refused_count = 0
        self.ended = False

    # -- session posture -------------------------------------------------

    def grant_opt_in(self) -> None:
        """Record a live per-session opt-in for THIS session."""
        self._opted_in = True
        self.ended = False

    def revoke_opt_in(self) -> None:
        """Withdraw the opt-in; emission refuses again at once."""
        self._opted_in = False

    def end_session(self) -> None:
        """End the session: opt-in and indicator are cleared."""
        self._opted_in = False
        self._indicator_kind = None
        self.ended = True

    def set_indicator(self, kind) -> None:
        """Set the live-capture indicator kind (e.g.
        ``"activity_stream"``). An empty kind clears it — emission
        then refuses until a real kind is set again."""
        kind = str(kind).strip() if kind is not None else ""
        self._indicator_kind = kind or None
        if self._indicator_listener is not None and kind:
            try:
                self._indicator_listener(kind, True)
            except Exception:
                pass

    @property
    def opted_in(self) -> bool:
        return bool(self._opted_in) and not self.ended

    @property
    def indicator_kind(self):
        return self._indicator_kind

    @property
    def denylist_applied(self) -> bool:
        """True: this session always carries a denylist (defaults
        when the caller passed none). There is no path to a
        session with the denylist disabled."""
        return True

    def _refusal_reason(self) -> str | None:
        if self.ended or not self._opted_in:
            return ("refused — no live per-session opt-in for the "
                    "activity stream; nothing was captured")
        if not self._indicator_kind:
            return ("refused — no capture indicator kind set; "
                    "nothing was captured")
        return None

    # -- emission ----------------------------------------------------------

    def emit(self, process_name=None, title=None, timestamp=None,
             kind: str = "window"):
        """Try to turn one foreground observation into an event.

        Returns ``(event, None)`` on success, ``(None, reason)``
        on refusal or denylist suppression. Refusal and
        suppression both mean NO event was constructed, stored,
        or forwarded. Suppressed observations increment only an
        anonymous counter — the offending text is not retained.
        ``timestamp`` defaults to 0.0 when not supplied so tests
        and callers without a clock stay deterministic; callers
        with a clock should pass a real epoch value.
        """
        reason = self._refusal_reason()
        if reason is not None:
            self._refused_count += 1
            return None, reason
        deny = is_denylisted(process_name, title,
                             processes=self._processes,
                             title_keywords=self._keywords)
        if deny is not None:
            # Suppress BEFORE capture: no event, no title stored,
            # no forwarding. Only the anonymous counter moves.
            self._suppressed_count += 1
            return None, f"suppressed — denylist match ({deny}); nothing was captured"
        event = ActivityEvent(
            app=_normalise_process(process_name) or "unknown",
            title=str(title or ""),
            timestamp=float(timestamp) if timestamp is not None else 0.0,
            kind=str(kind),
        )
        self._events.append(event)
        return event, None

    def emit_strict(self, process_name=None, title=None, timestamp=None,
                    kind: str = "window") -> ActivityEvent:
        """Like emit(), but raises on refusal / suppression."""
        event, reason = self.emit(process_name, title, timestamp, kind)
        if event is not None:
            return event
        if reason is not None and reason.startswith("suppressed"):
            raise ActivitySuppressedError(reason)
        raise ActivityRefusedError(reason or "refused")

    # -- reads ---------------------------------------------------------------

    def events(self) -> list:
        """Emitted events, in order (a copy; caller mutations do
        not affect the session). Suppressed/refused observations
        never appear here."""
        return list(self._events)

    def suppressed_count(self) -> int:
        return int(self._suppressed_count)

    def refused_count(self) -> int:
        return int(self._refused_count)

    def action_sequence(self) -> list:
        """The CA-1 export shape for AN-2: the app token of each
        emitted event, in order. Safe to hand to prefix_span()."""
        return [event.app for event in self._events]
