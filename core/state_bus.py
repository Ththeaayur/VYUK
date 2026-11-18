"""Command Deck state bus (Phase A) — one thread-safe channel, core to UI.

Guide §8: typed ``AppState`` / ``SensorState`` / ``MissionState`` plus
the event catalogue below. Producers (Phase D) are core modules;
consumers are the orb, Mission panel, tray, taskbar badge, overlay,
Mission Log, earcons — and, since GAPS-28, the main window itself
(``SettingsChanged`` -> live appearance apply).

GAPS-28: this module is the bus's home. It used to live in
``gui/state_bus.py``; the implementation is Qt-optional by design
(it degrades to a plain object when PySide6 cannot be imported),
so it belongs in core and core producers import it directly instead
of importlib-loading the GUI package. ``gui/state_bus.py`` is now a
thin re-export shim (through the facade) for the old import path.

SAFETY RULE (guide §8, hard): the channel is cosmetic. Consumers
never block a producer, exceptions are swallowed and logged, and
nothing on the channel can approve, deny or delay an action. The
mechanism that enforces it:

* ``emit()`` only appends to a locked deque and posts a *queued*
  drain request — it never runs a subscriber synchronously, so a
  slow or raising subscriber cannot delay the producer, on any
  thread. (Qt cross-thread signals are queued automatically; the
  explicit ``QueuedConnection`` invoke below makes the same promise
  for same-thread emitters too.)
* ``_drain()`` runs on the bus owner's thread, calling each
  subscriber inside its own try/except; one subscriber's failure
  never stops the others, and failures are logged, never raised.
* State snapshots: ``emit()`` also records the latest state model
  per type, so a late subscriber can read current state via
  ``latest_state()`` without waiting for the next event.

Qt / no-Qt: in the real app PySide6 is always present and the Qt
path runs. When PySide6 cannot be imported (headless lab runs, the
system interpreter), the bus degrades to a plain object with the
identical ``emit`` / ``subscribe`` / ``drain`` API and manual
draining — the pure queue, ordering and isolation logic is the same
code path, and the Qt-specific wakeup is the only difference. The
no-Qt path is never used in production.

Phase A ships the bus and the models with NO producers (guide
Phase A exit). Producers land in Phase D.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Type

log = logging.getLogger(__name__)

try:
    from PySide6.QtCore import QMetaObject, QObject, Qt, Slot

    QT_AVAILABLE = True
except Exception:  # pragma: no cover - headless fallback path
    QObject = object  # type: ignore[assignment]
    QMetaObject = None  # type: ignore[assignment]
    Qt = None  # type: ignore[assignment]

    def Slot(*_a, **_k):  # type: ignore[misc]
        def wrap(fn):
            return fn

        return wrap

    QT_AVAILABLE = False


# ---------------------------------------------------------------- typed states
@dataclass(frozen=True)
class AppState:
    """One-word HUD status + the strip facts around it (guide §7).

    ``colour`` is a ROLE name (cyan/amber/red/green/neutral/ice/grey),
    never a hex literal — the rendering layer maps roles to tokens.
    ``glyph`` is one of ring/diamond/triangle/square/dotted.
    ``detail`` is the secondary line (or "" when there is none).
    """

    status: str
    colour: str = "cyan"
    glyph: str = "ring"
    mic_muted: bool = False
    speech_muted: bool = False
    wake_on: bool = False
    mode: str = "LOCAL CORE"
    capture_active: bool = False
    detail: str = ""


@dataclass(frozen=True)
class SensorState:
    """One sensor tile (guide §8 TelemetryPanel / Senses page).

    ``source`` names the data source in code (honesty rule, guide
    §11.12): an empty source means the readout has no producer and
    must render as NOT MEASURED, never as a plausible value.
    """

    name: str
    state: str = "unavailable"  # off/armed/active/denied/unavailable
    value: str = ""
    unit: str = ""
    source: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class MissionState:
    """The current goal as the Mission panel shows it (guide §8)."""

    goal: str = ""
    phase: str = ""
    step: int = 0
    steps_total: int = 0
    target_app: str = ""
    target_window: str = ""
    permission: str = "none"  # none/required/granted/denied
    outcome: str = "running"  # running/success/partial/failed/unknown/cancelled
    evidence: Tuple[str, ...] = ()


# ---------------------------------------------------------------- event catalogue
@dataclass(frozen=True)
class StepEvent:
    n: int
    m: int
    label: str = ""


@dataclass(frozen=True)
class ToolStarted:
    kind: str
    target: str = ""


@dataclass(frozen=True)
class ToolFinished:
    status: str  # success/failed/unknown/skipped
    kind: str = ""
    target: str = ""


@dataclass(frozen=True)
class InputAcquired:
    source: str = ""


@dataclass(frozen=True)
class InputReleased:
    source: str = ""


@dataclass(frozen=True)
class TargetRect:
    x: int
    y: int
    w: int
    h: int
    role: str = ""
    name: str = ""


@dataclass(frozen=True)
class FailsafeTripped:
    reason: str = ""


@dataclass(frozen=True)
class CaptureStarted:
    scope: str = ""


@dataclass(frozen=True)
class CaptureFinished:
    scope: str = ""


@dataclass(frozen=True)
class Verified:
    evidence: str = ""


@dataclass(frozen=True)
class Verifying:
    """A verification check is running (Phase F producer: uia).

    ``kind`` is only "window" / "text" / "" — never the expectation
    text itself, which may carry user content. The outcome arrives
    as ``Verified`` (met) or nothing (not-met / unverifiable emit
    no Verified event: no verdict is ever guessed).
    """

    kind: str = ""


@dataclass(frozen=True)
class CursorPath:
    """One planned cursor path (Phase F producer: cursor._follow_path).

    ``points`` are true screen pixels in glide order. The overlay
    draws the fading trail from these; a pure move emits a path
    too, but moves alone never produce an action chip (guide §10:
    "Mouse moves alone are not enough").
    """

    points: Tuple[Tuple[int, int], ...] = ()
    source: str = ""


@dataclass(frozen=True)
class AgentAction:
    """One assistant input action, for the overlay chip (Phase F).

    ``kind`` is click / type / scroll / hotkey / drag. ``label``
    is a control name, a scroll direction, or a key combo — NEVER
    typed text: ``type`` actions carry only ``chars`` (and no
    count at all when ``password`` is True, so even a length
    cannot leak from a password field).
    """

    kind: str = ""
    label: str = ""
    chars: int = 0
    password: bool = False
    x: int = 0
    y: int = 0


@dataclass(frozen=True)
class SettingsChanged:
    """One appearance setting changed (GAPS-28 change bus).

    Emitted by ``core/app_settings.py`` after a chat-settable
    appearance key is written (``theme``,
    ``appearance/font_scale``, ``appearance/density``,
    ``appearance/custom_hex``, ``appearance/follow_windows``).
    ``value`` is the stored value. The main window subscribes and
    calls ``_apply_appearance()`` — a chat-set theme applies live
    instead of on the next build. Cosmetic only: a subscriber
    failure can never block or delay the setter (see the module
    SAFETY RULE)."""

    key: str
    value: object = None


EVENT_TYPES: Tuple[Type, ...] = (
    AppState,
    SensorState,
    MissionState,
    StepEvent,
    ToolStarted,
    ToolFinished,
    InputAcquired,
    InputReleased,
    TargetRect,
    FailsafeTripped,
    CaptureStarted,
    CaptureFinished,
    Verified,
    Verifying,
    CursorPath,
    AgentAction,
    SettingsChanged,
)


class StateBus(QObject if QT_AVAILABLE else object):
    """Thread-safe core-to-UI event channel (see module docstring)."""

    def __init__(self) -> None:
        if QT_AVAILABLE:
            super().__init__()
        self._lock = threading.Lock()
        self._queue: deque = deque()
        self._subs: Dict[Type, List[Tuple[int, Callable]]] = {}
        self._latest: Dict[Type, object] = {}
        self._next_token = 0

    # ------------------------------------------------------------ subscribing
    def subscribe(self, event_type: Type, callback: Callable) -> int:
        """Register ``callback(event)`` for one event type; returns a token."""
        with self._lock:
            self._next_token += 1
            token = self._next_token
            self._subs.setdefault(event_type, []).append((token, callback))
            return token

    def unsubscribe(self, token: int) -> bool:
        """Remove a subscription by token; True when one was removed."""
        with self._lock:
            for event_type, entries in list(self._subs.items()):
                for i, (tok, _cb) in enumerate(entries):
                    if tok == token:
                        del entries[i]
                        return True
        return False

    def latest_state(self, model_type: Type) -> Optional[object]:
        """Most recent state model of this type, or None if none seen."""
        with self._lock:
            return self._latest.get(model_type)

    # ------------------------------------------------------------ emitting
    def emit(self, event: object) -> None:
        """Queue an event for async delivery. Never blocks, never raises
        for a subscriber's failure (subscribers run only in _drain)."""
        with self._lock:
            self._queue.append(event)
            if type(event) in (AppState, SensorState, MissionState):
                self._latest[type(event)] = event
        if QT_AVAILABLE:
            # Queued even for same-thread emitters: delivery happens on
            # the event loop, never inline inside emit().
            QMetaObject.invokeMethod(self, "_drain", Qt.QueuedConnection)

    # ------------------------------------------------------------ draining
    @Slot()
    def _drain(self) -> None:
        """Deliver queued events, one subscriber at a time, isolated."""
        while True:
            with self._lock:
                if not self._queue:
                    return
                event = self._queue.popleft()
                entries = list(self._subs.get(type(event), ()))
            for token, callback in entries:
                try:
                    callback(event)
                except Exception:
                    log.exception(
                        "state-bus subscriber %s failed for %s; "
                        "continuing with the remaining subscribers",
                        token,
                        type(event).__name__,
                    )

    def drain(self) -> None:
        """Manual drain (no-Qt path and tests). On the Qt path this is
        identical to what the event loop runs via _drain."""
        self._drain()

    def queued_count(self) -> int:
        """Events still waiting for delivery (diagnostics / tests)."""
        with self._lock:
            return len(self._queue)


_BUS: Optional[StateBus] = None
_BUS_LOCK = threading.Lock()


def get_state_bus() -> StateBus:
    """Process-wide bus singleton (created on first use, on the
    caller's thread — production creates it on the GUI thread)."""
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = StateBus()
        return _BUS


def reset_state_bus() -> StateBus:
    """Replace the singleton (tests only — production never calls this;
    a test that subscribes must reset in tearDown so subscriptions
    cannot leak into another suite)."""
    global _BUS
    with _BUS_LOCK:
        _BUS = StateBus()
        return _BUS
