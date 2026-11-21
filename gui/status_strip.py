"""VYUK — status strip (R8-6, yasb port; rethink Build 3, 2026-11-16):
one dot + one word, docked as the window's bottom row.

PORT, not adopt: YASB (amnweb/yasb, MIT — LICENSE read from ``main``)
is a full standalone Windows status-bar app with a widget catalogue
(CPU, Memory, Disk, Battery, Media, workspaces, ...), YAML config and
CSS theming. Shipping it whole would ship a SECOND app, not a VYUK
feature. What is ported here is the pattern only — a small set of live
widgets in a bar — built from VYUK's own pieces:

* the five vitals come from the System Pulse sampler seam
  (``gui/pulse_page.py``: ``_PsutilSampler``), the same guarded psutil
  probes the Pulse page already uses; a sensor that cannot be read
  shows an honest "—", never a stale or invented number;
* the bar is a VYUK QWidget (PySide6), not a YASB window — no YASB
  code, no YAML config surface, no CSS theme port (the strip uses
  VYUK's own design tokens).

BUILD 3 (rethink §6.11/§6.12) — the floating bar is gone, and with it
a real bug it carried: the floating strip shared its window attribute
(``self.status_strip``) with the Home page's header cluster, so on the
real window ``_ensure_status_strip`` returned the header cluster,
the vitals bar could never appear, and the funnel tool's ``"status"``
action crashed with ``AttributeError: 'StatusStrip' object has no
attribute 'latest_text'`` (probe: rethink_build3, 2026-11-16). The
docked strip owns its own attribute (``self.vitals_strip``) and its
own place in the shell layout, so the collision is gone by
construction.

The row is 32 px tall with one hairline above it, docked below all
pages. One dot + one word (13 Sora): the dot is green when the
machine is healthy, amber on a warning, red on an error; the word
names the worst one ("Memory high", "Mic off") — never a dot alone.
The 8 numbers live behind a tap on the dot or the word (a popover)
and permanently on the System Pulse page.

The word is derived, worst first, from real sources only
(``derive_strip_state`` — pure, pinned by tests): metric
error (disk ≥95%, battery ≤10% unplugged) and the session's own
"Problem" → red; metric warning (CPU / memory / disk ≥90%,
battery ≤20% unplugged) and "Setup needed" → amber; mic muted
while a live turn is running (Listening / Responding / Thinking)
→ amber "Mic off"; a disconnected session → amber "Offline"
(E-F102 — it is not "Ready"); otherwise the session's own word
while it is in an active state, or any other real session word
("Local core") as-is, else "Ready" when there is no session word
at all. There is NO counted "N errors"
word: the tree has no app-wide error count to read (no
``error_count`` source exists), so none is invented — the strip
names the worst real state it can see.

HONEST SCOPE — what the strip deliberately does NOT show:
* media — the tree has no now-playing / current-track source anywhere
  (0 hits in core), so there is nothing honest to display;
* workspaces — the tree has no virtual-desktop concept, so no
  workspace state is claimed;
* pomodoro — ``focus_session`` / ``pomodoro`` already exist in
  ``core/well.py``; the strip does not duplicate them;
* per-app anything — the strip samples system-wide psutil only, the
  same sensing-free shape as the Pulse page (B-R4-9 boundary).

The strip is ON by default (setting ``presence/status_strip``,
default True since Build 3): the row is the window's bottom edge,
and the Settings checkbox / tray item / ``status_strip`` funnel tool
hide it. Its 1-second sampler only ticks while the strip is
visible, the Pulse page's rule; hiding it stops sampling.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .design_system.session_states import STATUS_DISPLAY_WORDS
from .pulse_page import (
    STRIP_ERROR_BATTERY_PCT,
    STRIP_ERROR_DISK_PCT,
    STRIP_WARN_BATTERY_PCT,
    STRIP_WARN_CPU_PCT,
    STRIP_WARN_DISK_PCT,
    STRIP_WARN_MEM_PCT,
    _PsutilSampler,
    _format_uptime,
    format_rate,
)
from .state_bus import AppState, get_state_bus
from .style import (
    AMBER,
    DANGER,
    LINE_SUBTLE,
    MONO_FONT_STACK,
    OK,
    TEXT,
    TEXT_MUTED,
    TYPE_BODY,
)

# The strip samples on the Pulse page's beat: once a second, only while
# visible. No sampling while hidden — an always-on bar that sampled in
# the background would be a watcher, not a display.
_SAMPLE_MS = 1000

# Session words (display form) in which the mic SHOULD be hearing —
# a muted mic during one of these is a real "Mic off", not the idle
# state (at idle, or while connecting, a muted mic is normal).
_LIVE_WORDS = ("Listening", "Responding", "Thinking")
# Session words worth showing as-is while they last.
_ACTIVE_WORDS = _LIVE_WORDS + ("Connecting", "Reconnecting", "Setting up")

_DOT_COLOURS = {"ok": OK, "warning": AMBER, "error": DANGER}


def _is_at_least(value, threshold) -> bool:
    """True when a sensor reading exists and reaches the threshold —
    an unknown sensor (None) never fires a warning or an error."""
    return isinstance(value, (int, float)) and value >= threshold


def _is_at_most(value, threshold) -> bool:
    """True when a sensor reading exists and falls to the threshold."""
    return isinstance(value, (int, float)) and value <= threshold


def derive_strip_state(sample, session_word, mic_muted):
    """Derive (dot, word) from real sources only.

    ``sample`` is the Pulse sampler dict (``cpu``, ``mem``, ``disk``,
    ``net_bps``, ``battery_pct``, ``battery_plugged``, ``uptime_s``,
    ``procs`` — any key may be ``None`` when its sensor is unknown).
    ``session_word`` is the session's display word ("" when unknown).
    ``mic_muted`` is the mic's muted state. Worst first: a metric
    error or the session's own "Problem" is red; a metric warning,
    "Setup needed", or a muted mic during a live turn is amber;
    otherwise the session's own word while active, else the word
    itself when it names a real session state ("Offline" is amber —
    a degraded session, not a fault — "Local core" is ok), and
    "Ready" only when there is no session word at all.
    """
    disk = sample.get("disk")
    battery = sample.get("battery_pct")
    plugged = bool(sample.get("battery_plugged"))
    if _is_at_least(disk, STRIP_ERROR_DISK_PCT):
        # E-F104: an error must read as an error in WORDS, not
        # just in dot colour — "Disk almost full" is the warning
        # word; at >=95% the disk is critically full.
        return ("error", "Disk critically full")
    if not plugged and _is_at_most(battery, STRIP_ERROR_BATTERY_PCT):
        # E-F104: same shape as the disk error — "Battery low"
        # is the warning word; at <=10% unplugged it is
        # critically low.
        return ("error", "Battery critically low")
    if session_word == "Problem":
        return ("error", "Problem")
    if _is_at_least(sample.get("cpu"), STRIP_WARN_CPU_PCT):
        return ("warning", "CPU high")
    if _is_at_least(sample.get("mem"), STRIP_WARN_MEM_PCT):
        return ("warning", "Memory high")
    if _is_at_least(disk, STRIP_WARN_DISK_PCT):
        return ("warning", "Disk almost full")
    if not plugged and _is_at_most(battery, STRIP_WARN_BATTERY_PCT):
        return ("warning", "Battery low")
    if session_word == "Setup needed":
        return ("warning", "Setup needed")
    if mic_muted and session_word in _LIVE_WORDS:
        return ("warning", "Mic off")
    if session_word in _ACTIVE_WORDS:
        return ("ok", session_word)
    if session_word == "Offline":
        # E-F102: a disconnected session is not "Ready" — the
        # pill read Offline while the strip claimed all good.
        # Offline is a degraded session (the user cannot talk),
        # not a fault (nothing broke) and not "all good": amber.
        return ("warning", "Offline")
    if session_word:
        # E-F102: any other session word the pill shows must
        # appear as-is on the strip — "Ready" is only honest
        # when there is no session word at all. This covers
        # "Local core" (the on-device brain is live) and any
        # future word, so the fall-through can never again
        # swallow a real session state into "Ready".
        return ("ok", session_word)
    return ("ok", "Ready")


def _fmt_pct(value) -> str:
    """A percent reading, or the honest "—" when the sensor is unknown."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "—"


class _StripPopover(QWidget):
    """The 8 numbers behind a tap on the dot: one mono line each."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("statusStripPopover")
        self.setStyleSheet(
            f"QWidget#statusStripPopover {{ background: {TEXT};"
            f" border: 1px solid {LINE_SUBTLE}; border-radius: 8px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        self._line_labels: list[QLabel] = []
        for _ in range(8):
            label = QLabel("—")
            label.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: {TYPE_BODY}px;"
                f" font-family: {MONO_FONT_STACK}; background: transparent;"
            )
            layout.addWidget(label)
            self._line_labels.append(label)

    def set_lines(self, lines) -> None:
        """Fill the 8 lines (missing lines read "—", never stale)."""
        for label, text in zip(self._line_labels, lines):
            label.setText(text or "—")
        for label in self._line_labels[len(lines):]:
            label.setText("—")

    def lines(self) -> list[str]:
        """The 8 lines currently shown (tests read the popover here)."""
        return [label.text() for label in self._line_labels]


class StatusStrip(QWidget):
    """One dot + one word, docked as the window's bottom row (32 px).

    ``sampler`` is the PulsePage seam: a callable returning the sample
    dict (``cpu``, ``mem``, ``disk``, ``net_bps``, ``battery_pct``,
    ``battery_plugged``, ``uptime_s``, ``procs``). Tests inject a
    fake; production uses the built-in psutil sampler. ``latest()``
    returns the most recent sample dict (all-``None`` before the
    first successful sample — a failed probe never leaves old
    numbers standing as if fresh). The session word and mic state
    arrive on the state bus (``AppState``), the same source the
    header pill reads, so the strip and the pill can never disagree
    about the session.
    """

    def __init__(self, sampler=None, parent=None, bus=None):
        super().__init__(parent)
        self._sampler = sampler if sampler is not None else _PsutilSampler()
        self._latest: dict = {
            key: None
            for key in (
                "cpu", "mem", "disk", "net_bps",
                "battery_pct", "battery_plugged", "uptime_s", "procs",
            )
        }
        self._session_word = ""
        self._mic_muted = False
        self._dot_state = None
        self._popover: _StripPopover | None = None

        self.setObjectName("dockedStatusStrip")
        self.setFixedHeight(32)
        self.setStyleSheet(
            f"QWidget#dockedStatusStrip {{"
            f" border-top: 1px solid {LINE_SUBTLE}; background: transparent; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(8)
        self.dot_button = QPushButton("●")
        self.dot_button.setObjectName("statusStripDot")
        self.dot_button.setFlat(True)
        self.dot_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dot_button.setStyleSheet(
            f"QPushButton#statusStripDot {{ border: none;"
            f" background: transparent; color: {OK};"
            f" font-size: {TYPE_BODY}px; padding: 0 2px; }}"
        )
        self.dot_button.setAccessibleName("System status")
        self.dot_button.clicked.connect(self._toggle_popover)
        layout.addWidget(self.dot_button)
        self.word_button = QPushButton("Ready")
        self.word_button.setObjectName("statusStripWord")
        self.word_button.setFlat(True)
        self.word_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.word_button.setStyleSheet(
            f"QPushButton#statusStripWord {{ border: none;"
            f" background: transparent; color: {TEXT};"
            f" font-size: {TYPE_BODY}px; text-align: left; padding: 0; }}"
        )
        self.word_button.setAccessibleName("System status word")
        self.word_button.clicked.connect(self._toggle_popover)
        layout.addWidget(self.word_button)
        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.setInterval(_SAMPLE_MS)
        self._timer.timeout.connect(self._tick)

        self._bus = bus if bus is not None else get_state_bus()
        self._tokens = []
        try:
            self._tokens.append(
                self._bus.subscribe(AppState, self._on_app_state))
        except Exception:  # noqa: BLE001 — no bus, no session word
            self._bus = None

    # ------------------------------------------------------------ sampling
    def _tick(self) -> None:
        try:
            sample = self._sampler()
        except Exception:  # noqa: BLE001 — a broken sampler reads "—"
            sample = {}
        if isinstance(sample, dict):
            for key in self._latest:
                if key in sample:
                    self._latest[key] = sample[key]
        self._render()

    def _render(self) -> None:
        dot, word = derive_strip_state(
            self._latest, self._session_word, self._mic_muted)
        # The dot's sheet is re-applied only when its colour
        # changes — restyling every 1 s tick would repaint the
        # row for nothing.
        if dot != self._dot_state:
            self._dot_state = dot
            self.dot_button.setStyleSheet(
                f"QPushButton#statusStripDot {{ border: none;"
                f" background: transparent; color: {_DOT_COLOURS[dot]};"
                f" font-size: {TYPE_BODY}px; padding: 0 2px; }}"
            )
        self.word_button.setText(word)
        description = f"System status: {word}. Tap for the numbers."
        self.dot_button.setAccessibleDescription(description)
        self.word_button.setAccessibleDescription(description)
        if self._popover is not None and self._popover.isVisible():
            self._popover.set_lines(self.numbers_lines())

    def latest(self) -> dict:
        """The most recent sample dict (a copy; all-None before tick 1)."""
        return dict(self._latest)

    def state_word(self) -> str:
        """The strip's current one-line word, for the tool result."""
        return self.word_button.text()

    def numbers_lines(self) -> list[str]:
        """The 8 numbers, one line each, for the popover."""
        latest = self._latest
        battery = latest.get("battery_pct")
        if battery is None:
            power = "—"
        else:
            power = "Plugged" if latest.get("battery_plugged") else "On battery"
        uptime = _format_uptime(latest.get("uptime_s"))
        procs = latest.get("procs")
        if procs is None:
            procs_text = "—"
        else:
            try:
                procs_text = str(int(procs))
            except (TypeError, ValueError):
                procs_text = "—"
        return [
            f"CPU {_fmt_pct(latest.get('cpu'))}",
            f"Memory {_fmt_pct(latest.get('mem'))}",
            f"Disk {_fmt_pct(latest.get('disk'))}",
            f"Network {format_rate(latest.get('net_bps'))}",
            f"Battery {_fmt_pct(battery)}",
            f"Power {power}",
            f"Uptime {uptime if uptime is not None else '—'}",
            f"Processes {procs_text}",
        ]

    def set_sampler(self, sampler) -> None:
        """Swap the sampler (tests drive the strip through this seam)."""
        self._sampler = sampler

    # ------------------------------------------------------- session state
    def _on_app_state(self, state) -> None:
        """Bus AppState: the HUD word becomes the display word (the
        same map the header pill reads), and the mic state arrives
        with it — so the strip and the pill can never disagree."""
        hud_word = getattr(state, "status", "") or ""
        self._session_word = STATUS_DISPLAY_WORDS.get(hud_word, hud_word)
        self._mic_muted = bool(getattr(state, "mic_muted", False))
        self._render()

    # ------------------------------------------------------------ popover
    def _toggle_popover(self) -> None:
        if self._popover is not None and self._popover.isVisible():
            self._popover.hide()
            return
        if self._popover is None:
            self._popover = _StripPopover()
        self._popover.set_lines(self.numbers_lines())
        self._popover.adjustSize()
        top_left = self.mapToGlobal(QPoint(8, 0))
        self._popover.move(
            top_left.x(), top_left.y() - self._popover.height() - 4)
        self._popover.show()

    # ------------------------------------------------------- visibility
    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._tick()  # one immediate sample the moment it appears
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        if self._timer.isActive():
            self._timer.stop()  # hidden means not sampling, by design
        if self._popover is not None and self._popover.isVisible():
            self._popover.hide()

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        for token in self._tokens:
            try:
                self._bus.unsubscribe(token)
            except Exception:  # noqa: BLE001 — bus teardown is best-effort
                pass
        super().closeEvent(event)
