"""Command Deck Phase F — agent overlay (guide §10 Phase F, §9 S9).

One transparent, click-through, always-on-top window per monitor,
showing what VYUK is doing with the input it holds. Guide S9, in
full: when VYUK holds input — target-lock reticle with a tag
(UIA · BUTTON), action chip (CLICK › Save), fading cursor trail
with click pulse, scan sweep during OCR, amber frame with AGENT
CONTROL ACTIVE · MOVE MOUSE TO ABORT, red CONTROL RETURNED flash
on failsafe. S9 rule (hard): cosmetic only, excluded from
capture, never receives input, killing it never stops an action.

The module is split in two layers so the lab can prove the
behaviour without a Windows desktop:

* ``OverlayModel`` — pure Python, no Qt. It consumes bus events
  (TargetRect, AgentAction, CursorPath, CaptureStarted/Finished,
  InputAcquired/Released, FailsafeTripped, Verified, Verifying)
  and holds exactly what the windows paint. Every model rule is
  unit-testable on both interpreters: chip formats, the
  password-text rule, the 400 ms trail fade, the frame states,
  the input hold count, the cue mapping.
* ``OverlayWindow`` / ``AgentOverlay`` — the Qt layer (only when
  PySide6 imports). One window per screen, frameless,
  always-on-top, transparent-for-input, translucent background.
  Coordinates come from the same DPI-aware basis as cursor.py:
  the process is marked DPI-aware through cursor.py's own helper
  before any screen geometry is read, and every event coordinate
  arrives in true screen pixels.

Display affinity (guide D8): every overlay window calls
SetWindowDisplayAffinity with WDA_EXCLUDEFROMCAPTURE. The call
is made through ``apply_display_affinity`` so tests can stub the
platform seam; when the OS cannot apply it — non-Windows, or an
older Windows build, or a failed call — the window is NOT shown
at all. A hidden overlay cannot leak into a capture, which is
strictly stronger than exclusion. On Windows the affinity flag
is what excludes the window from screen-capture APIs (BitBlt,
desktop duplication, Windows Graphics Capture): the capture
paths themselves need no overlay awareness, and the capture test
asserts the flag, not pixels — pixel-level proof on real
Windows is field-owed, never claimed from the lab.

Development-only capturable switch: the overlay is capturable
only when the shipped constant ``DEV_CAPTURABLE_SWITCH_ENABLED``
is True AND the environment switch is set. The shipped constant
is False, so the environment switch alone can never make a
release build capturable; the capture test pins both halves.

Killing the overlay: ``AgentOverlay.kill()`` closes the windows
and detaches the model. It touches no action state, no bus
producer, and no failsafe — a killed overlay changes nothing
about the action in flight (guide §11 guard 10).

Tkinter beacon fallback (guide §9.5): the old tkinter beacon in
core/desktop/cursor.py stays as the fallback and is NOT removed or
rewritten here. When the Qt overlay cannot show (affinity
unavailable), callers keep the beacon path they already had.

Colours: this file carries NO colour literals (guide §11 guard:
new gui files must be literal-free). Every colour comes from
gui.style tokens, with translucency via QColor alpha, never via
a colour string.

Earcons: the overlay requests cues by name through an injected
``play_cue`` callable (default: silent no-op). The mapping lives
in ``cue_for_event`` so tests can pin it without audio: target
lock -> lock tick, failsafe return -> handback sweep, step
completion -> step tick. One cue per real phase (guide S14: no
per-tool spam, no cue over speech — the app's Earcons gate
enforces the rest).
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

# ------------------------------------------------------------------ constants
#: Cursor-trail fade window (guide §10: "fades over about 400 ms").
TRAIL_FADE_MS = 400

#: Windows display-affinity value that excludes a window from capture.
WDA_EXCLUDEFROMCAPTURE = 0x11

#: Minimum Windows build for display affinity (guide D8: Windows 10
#: version 2004 or later, build 19041).
MIN_WINDOWS_BUILD = 19041

#: Release default for the marketing-capture switch. The switch is
#: ABSENT from release builds in effect: False here means no
#: environment variable, flag, or caller can make a shipped build
#: capturable. A dev build flips this constant at build time.
DEV_CAPTURABLE_SWITCH_ENABLED = False

#: Environment switch, honoured ONLY when the constant above is True.
DEV_CAPTURABLE_ENV = "VYUK_OVERLAY_CAPTURABLE"

#: Frame states the overlay paints (S9).
FRAME_IDLE = "idle"
FRAME_ACTIVE = "active"
FRAME_RETURNED = "returned"

#: How long the red CONTROL RETURNED flash stays up (ms).
RETURNED_FLASH_MS = 1500

#: Tag prefix for the target-lock reticle (S9: "UIA · BUTTON").
RETICLE_TAG_PREFIX = "UIA"

# ------------------------------------------------------------------ chip text
def format_action_chip(kind, label="", chars=0, password=False):
    """Render one action chip string (guide §10 formats).

    CLICK › Save — the control name, never coordinates.
    TYPE › 14 chars — a character COUNT, never the text.
    SCROLL › down — the direction.
    HOTKEY › ctrl+c — the combo (a combo is not secret text).
    DRAG › (x1,y1) to (x2,y2) — endpoints, guide extension.

    Password rule (hard): when ``password`` is True the chip is
    "TYPE › ..." with NO count — not even a length may leak from
    a password field. Unknown kinds render as "" (no chip), never
    as a guessed string. ``label`` is trusted to be a name /
    direction / combo: producers must never pass typed text here
    (see core/state_bus_producers.emit_agent_action).
    """
    kind = str(kind or "").strip().lower()
    if kind == "click":
        name = str(label or "").strip()
        return "CLICK › " + (name if name else "click")
    if kind == "type":
        if password:
            return "TYPE › " + chr(8226) * 3
        try:
            count = int(chars)
        except (TypeError, ValueError):
            count = 0
        return "TYPE › %d chars" % max(0, count)
    if kind == "scroll":
        direction = str(label or "").strip().lower()
        if direction not in ("up", "down", "left", "right"):
            direction = "down"
        return "SCROLL › " + direction
    if kind == "hotkey":
        combo = str(label or "").strip()
        return "HOTKEY › " + (combo if combo else "key")
    if kind == "drag":
        return "DRAG › " + str(label or "").strip()
    return ""


def cue_for_event(event_name):
    """Earcon cue name for one overlay event, or "" for none.

    Target lock -> lock tick. Failsafe return -> handback sweep.
    Step completion -> step tick. Everything else: silence (S14:
    one cue per real phase, no per-tool spam).
    """
    mapping = {
        "target_lock": "lock",
        "failsafe": "handback",
        "step": "step",
    }
    return mapping.get(str(event_name or ""), "")


# ------------------------------------------------------- capture / affinity
def is_capturable():
    """True only for a dev build with the env switch set.

    The shipped constant is False, so in release builds this is
    False regardless of the environment. The capture test pins
    both halves: constant False + env set still excluded.
    """
    if not DEV_CAPTURABLE_SWITCH_ENABLED:
        return False
    return os.environ.get(DEV_CAPTURABLE_ENV, "") == "1"


def is_excluded_from_capture():
    """True when the overlay cannot appear in a capture."""
    return not is_capturable()


def os_supports_affinity():
    """True on Windows 10 version 2004+ (guide D8), else False."""
    if not sys.platform.startswith("win"):
        return False
    try:
        version = sys.getwindowsversion()
        return int(version.build) >= MIN_WINDOWS_BUILD
    except Exception:  # noqa: BLE001 — unknown build means unsupported
        return False


def _set_window_display_affinity(hwnd, value):
    """The OS call itself, isolated as a seam.

    Tests stub this (not the ctypes module) to prove the Windows
    path on Linux: that every overlay window is offered exactly
    this value, and that a failed call refuses the show.
    """
    try:
        result = ctypes.windll.user32.SetWindowDisplayAffinity(
            int(hwnd), int(value))
        return bool(result)
    except Exception:  # noqa: BLE001 — a failed call means do not show
        return False


def apply_display_affinity(hwnd):
    """Apply WDA_EXCLUDEFROMCAPTURE to one window handle.

    Returns True when the OS call succeeded. On non-Windows, or
    when the call fails, returns False — and the caller must NOT
    show the window (guide §10: "If the OS cannot, do not show
    the overlay").
    """
    if not os_supports_affinity():
        return False
    return _set_window_display_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE)


def ensure_dpi_aware():
    """Mark this process DPI-aware via cursor.py's own helper.

    The overlay uses the same DPI-aware coordinates as cursor.py:
    true screen pixels on scaled displays, not virtualised ones.
    Never raises.
    """
    try:
        from core.gui_facade import cursor as _cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

        _cursor._ensure_dpi_aware()
    except Exception:  # noqa: BLE001 — overlay geometry still works
        pass


# --------------------------------------------------------------------- model
class OverlayModel:
    """Pure-Python overlay state: what the windows paint.

    Consumed bus events only change this model; nothing here can
    approve, deny, delay, or stop an action (guide §8 safety rule).
    Times are milliseconds from ``time.monotonic`` unless a test
    passes ``now_ms`` explicitly (the fade tests do).
    """

    def __init__(self, play_cue=None, clock=None):
        self.play_cue = play_cue if play_cue is not None else (lambda _n: None)
        self._clock = clock if clock is not None else (lambda: time.monotonic() * 1000.0)
        self.target_rect = None  # (x, y, w, h, role, name) or None
        self.chip = ""
        self.trail = []  # [(x, y, t_ms)] in arrival order
        self.click_pulse = None  # (x, y, t_ms) or None
        self.scan_sweep = False
        self._sweep_scopes = set()
        self.frame = FRAME_IDLE
        self._hold_count = 0
        self.returned_until_ms = 0.0
        self.verifying_kind = ""
        self.verified_evidence = ""
        self.cue_requests = []

    # ------------------------------------------------------------ helpers
    def _now(self, now_ms):
        return float(now_ms) if now_ms is not None else float(self._clock())

    def _cue(self, event_name):
        name = cue_for_event(event_name)
        if name:
            self.cue_requests.append(name)
            try:
                self.play_cue(name)
            except Exception:  # noqa: BLE001 — a cue never breaks display
                pass

    # ------------------------------------------------------------ bus events
    def on_target_rect(self, event):
        """Target lock: reticle + tag + lock tick."""
        try:
            self.target_rect = (int(event.x), int(event.y),
                                int(event.w), int(event.h),
                                str(event.role or ""), str(event.name or ""))
        except Exception:  # noqa: BLE001 — a bad event draws nothing
            return
        self._cue("target_lock")

    def on_agent_action(self, event, now_ms=None):
        """Action chip from a click / type / scroll / hotkey / drag."""
        try:
            chip = format_action_chip(event.kind, event.label,
                                      event.chars, event.password)
        except Exception:  # noqa: BLE001 — a bad event draws nothing
            return
        if chip:
            self.chip = chip
        if str(getattr(event, "kind", "")).lower() == "click":
            try:
                self.click_pulse = (int(event.x), int(event.y),
                                    self._now(now_ms))
            except Exception:  # noqa: BLE001 — pulse is cosmetic
                self.click_pulse = None

    def on_cursor_path(self, event):
        """Trail points from one planned glide path."""
        now = self._now(None)
        try:
            points = tuple(event.points or ())
        except Exception:  # noqa: BLE001 — a bad event draws nothing
            return
        for point in points:
            try:
                self.trail.append((int(point[0]), int(point[1]), now))
            except Exception:  # noqa: BLE001 — skip a bad point
                continue
        self.prune(now)

    def on_capture_started(self, event):
        """Scan sweep on while an OCR pass runs (scope "ocr")."""
        scope = str(getattr(event, "scope", "") or "")
        if scope == "ocr":
            self._sweep_scopes.add(scope)
            self.scan_sweep = True

    def on_capture_finished(self, event):
        """Scan sweep off when the OCR pass ends."""
        scope = str(getattr(event, "scope", "") or "")
        if scope == "ocr":
            self._sweep_scopes.discard(scope)
            self.scan_sweep = bool(self._sweep_scopes)

    def on_input_acquired(self, event=None):
        """VYUK holds input: amber AGENT CONTROL ACTIVE frame."""
        self._hold_count += 1
        if self.frame != FRAME_RETURNED:
            self.frame = FRAME_ACTIVE

    def on_input_released(self, event=None):
        """Input free: frame falls back unless a return flash runs."""
        self._hold_count = max(0, self._hold_count - 1)
        if self._hold_count == 0 and self.frame == FRAME_ACTIVE:
            self.frame = FRAME_IDLE

    def on_failsafe(self, event=None, now_ms=None):
        """Failsafe trip: red CONTROL RETURNED flash + handback cue.

        The flash outranks the active frame until it expires; the
        hold count is cleared because a trip releases held input.
        """
        self._hold_count = 0
        self.frame = FRAME_RETURNED
        self.returned_until_ms = self._now(now_ms) + RETURNED_FLASH_MS
        self._cue("failsafe")

    def on_verifying(self, event):
        """A verification check is running (kind only)."""
        self.verifying_kind = str(getattr(event, "kind", "") or "")

    def on_verified(self, event):
        """A verification check met: record the evidence, clear Verifying."""
        self.verified_evidence = str(getattr(event, "evidence", "") or "")
        self.verifying_kind = ""

    def on_step(self, event=None):
        """A step completed: step tick (S14: soft tick per step)."""
        self._cue("step")

    # ------------------------------------------------------------ fade / frame
    def prune(self, now_ms=None):
        """Drop trail points older than the fade window; expire flash."""
        now = self._now(now_ms)
        cutoff = now - TRAIL_FADE_MS
        self.trail = [p for p in self.trail if p[2] > cutoff]
        if self.frame == FRAME_RETURNED and now >= self.returned_until_ms:
            self.frame = FRAME_IDLE if self._hold_count == 0 else FRAME_ACTIVE

    def trail_alphas(self, now_ms=None):
        """Per-point opacity 1.0 -> 0.0 over the fade window."""
        now = self._now(now_ms)
        out = []
        for (_x, _y, t) in self.trail:
            age = now - t
            if age >= TRAIL_FADE_MS:
                continue
            out.append(max(0.0, 1.0 - age / TRAIL_FADE_MS))
        return out

    def reticle_tag(self):
        """Tag text for the reticle, or "" when nothing is locked."""
        if self.target_rect is None:
            return ""
        role = self.target_rect[4] or "control"
        return "%s · %s" % (RETICLE_TAG_PREFIX, role.upper())

    def frame_banner(self):
        """Banner text for the current frame, or "" when idle."""
        if self.frame == FRAME_ACTIVE:
            return "AGENT CONTROL ACTIVE · MOVE MOUSE TO ABORT"
        if self.frame == FRAME_RETURNED:
            return "CONTROL RETURNED"
        return ""


# ------------------------------------------------------------------ Qt layer
try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
    from PySide6.QtWidgets import QWidget

    QT_AVAILABLE = True
except Exception:  # pragma: no cover - headless fallback path
    QT_AVAILABLE = False


if QT_AVAILABLE:
    from gui.style import ACCENT, AMBER, DANGER, TEXT

    class OverlayWindow(QWidget):
        """One per-monitor overlay window: transparent, click-through,
        always-on-top. It NEVER receives input (transparent-for-input
        flag) and is shown only after display affinity succeeds —
        otherwise it stays hidden (see apply_display_affinity).
        """

        def __init__(self, screen_geometry, model):
            super().__init__(
                None,
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.WindowTransparentForInput
                | Qt.Tool,
            )
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self._model = model
            self._shown = False
            x, y, w, h = (int(v) for v in screen_geometry)
            self.setGeometry(x, y, w, h)

        # ------------------------------------------------------------ show
        def show_overlay(self):
            """Show only after affinity succeeds; else stay hidden.

            Returns True when shown. A False return is not an error:
            on a machine that cannot exclude the window from capture,
            not showing it is the specified behaviour.
            """
            if self._shown:
                return True
            try:
                hwnd = int(self.winId())
            except Exception:  # noqa: BLE001 — no handle, do not show
                return False
            if not apply_display_affinity(hwnd):
                return False
            try:
                self.show()
            except Exception:  # noqa: BLE001 — show failure = hidden
                return False
            self._shown = True
            return True

        def shown(self):
            """True only after a successful affinity-gated show."""
            return self._shown

        def kill(self):
            """Close this window. Touches no action state: killing
            the overlay changes nothing about the action in flight."""
            self._shown = False
            try:
                self.close()
            except Exception:  # noqa: BLE001 — close must not raise
                pass

        # ------------------------------------------------------------ paint
        def paintEvent(self, _event):  # noqa: N802 - Qt naming
            try:
                self._paint_model()
            except Exception:  # noqa: BLE001 — paint never raises
                pass

        def _paint_model(self):
            model = self._model
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.Antialiasing, True)
                ox, oy = self.x(), self.y()
                cyan = QColor(ACCENT)
                amber = QColor(AMBER)
                red = QColor(DANGER)
                white = QColor(TEXT)
                # Target-lock reticle + tag.
                if model.target_rect is not None:
                    rx, ry, rw, rh, _role, _name = model.target_rect
                    pen = QPen(cyan)
                    pen.setWidth(2)
                    painter.setPen(pen)
                    painter.drawRect(rx - ox, ry - oy, rw, rh)
                    tag = model.reticle_tag()
                    if tag:
                        painter.drawText(rx - ox, max(0, ry - oy - 4), tag)
                # Fading cursor trail.
                alphas = model.trail_alphas()
                for (tx, ty, _t), alpha in zip(model.trail, alphas):
                    dot = QColor(cyan)
                    dot.setAlpha(int(255 * alpha))
                    painter.setPen(QPen(dot))
                    painter.drawPoint(tx - ox, ty - oy)
                # Click pulse.
                if model.click_pulse is not None:
                    px, py, _t = model.click_pulse
                    pen = QPen(cyan)
                    pen.setWidth(2)
                    painter.setPen(pen)
                    painter.drawEllipse(px - ox - 12, py - oy - 12, 24, 24)
                # Scan sweep during OCR.
                if model.scan_sweep:
                    sweep = QColor(cyan)
                    sweep.setAlpha(60)
                    painter.fillRect(0, 0, self.width(), 24, sweep)
                # Frame banner.
                banner = model.frame_banner()
                if banner:
                    colour = amber if model.frame == FRAME_ACTIVE else red
                    pen = QPen(colour)
                    pen.setWidth(3)
                    painter.setPen(pen)
                    painter.drawRect(1, 1, self.width() - 2,
                                     self.height() - 2)
                    painter.setPen(QPen(white))
                    painter.drawText(12, 28, banner)
            finally:
                painter.end()

    class AgentOverlay:
        """Owns one OverlayWindow per monitor + the shared model.

        ``kill()`` closes every window and detaches the model; it
        never touches action state, the bus, or the failsafe.
        """

        def __init__(self, model=None, play_cue=None):
            self.model = model if model is not None else OverlayModel(
                play_cue=play_cue)
            self.windows = []
            self._tokens = []

        def build_windows(self):
            """Create (not show) one window per screen."""
            ensure_dpi_aware()
            self.windows = []
            try:
                screens = QGuiApplication.screens()
            except Exception:  # noqa: BLE001 — no screens, no windows
                screens = []
            for screen in screens:
                try:
                    geo = screen.geometry()
                    window = OverlayWindow(
                        (geo.x(), geo.y(), geo.width(), geo.height()),
                        self.model)
                    self.windows.append(window)
                except Exception:  # noqa: BLE001 — skip a bad screen
                    continue
            return self.windows

        def show_all(self):
            """Show every window whose affinity succeeds.

            Returns the count shown. Windows that cannot be
            excluded stay hidden by design.
            """
            shown = 0
            for window in self.windows:
                try:
                    if window.show_overlay():
                        shown += 1
                except Exception:  # noqa: BLE001 — one window never
                    continue
            return shown

        def attach_to_bus(self, bus):
            """Subscribe the model to the overlay's bus events.

            Consumers never block a producer (guide §8): the bus
            delivers queued, exception-isolated, so a slow or
            raising overlay consumer cannot delay any producer.
            Returns the subscription tokens (for detach in tests).
            """
            # Subscribe with the BUS's own event classes, not an
            # imported copy: bus delivery dispatches on class
            # identity, and in a hybrid layout (lab tests that
            # register a voice_assistant shim beside gui.*) the
            # two namespaces hold distinct class objects — so
            # subscribing with the wrong copy silently drops
            # every event. The bus object is authoritative
            # about its own events; the dual-path import below
            # is only the fallback for buses that do not carry
            # their module (fakes in tests).
            try:
                import importlib as _importlib
                _bus_mod = _importlib.import_module(
                    type(bus).__module__)
                AgentAction = _bus_mod.AgentAction
                CaptureFinished = _bus_mod.CaptureFinished
                CaptureStarted = _bus_mod.CaptureStarted
                CursorPath = _bus_mod.CursorPath
                FailsafeTripped = _bus_mod.FailsafeTripped
                InputAcquired = _bus_mod.InputAcquired
                InputReleased = _bus_mod.InputReleased
                TargetRect = _bus_mod.TargetRect
                Verified = _bus_mod.Verified
                Verifying = _bus_mod.Verifying
            except Exception:  # noqa: BLE001 — fall back to import
                try:
                    from voice_assistant.gui.state_bus import (
                        AgentAction, CaptureFinished, CaptureStarted,
                        CursorPath, FailsafeTripped, InputAcquired,
                        InputReleased, TargetRect, Verified, Verifying)
                except Exception:  # noqa: BLE001 — lab layout uses gui.*
                    from gui.state_bus import (
                        AgentAction, CaptureFinished, CaptureStarted,
                        CursorPath, FailsafeTripped, InputAcquired,
                        InputReleased, TargetRect, Verified, Verifying)

            model = self.model
            pairs = (
                (TargetRect, model.on_target_rect),
                (AgentAction, model.on_agent_action),
                (CursorPath, model.on_cursor_path),
                (CaptureStarted, model.on_capture_started),
                (CaptureFinished, model.on_capture_finished),
                (InputAcquired, model.on_input_acquired),
                (InputReleased, model.on_input_released),
                (FailsafeTripped, lambda _e: model.on_failsafe()),
                (Verified, model.on_verified),
                (Verifying, model.on_verifying),
            )
            self._tokens = [bus.subscribe(kind, cb) for kind, cb in pairs]
            return list(self._tokens)

        def detach_from_bus(self, bus):
            """Unsubscribe (tests / teardown)."""
            for token in self._tokens:
                try:
                    bus.unsubscribe(token)
                except Exception:  # noqa: BLE001 — teardown never raises
                    pass
            self._tokens = []

        def kill(self):
            """Close every window, detach the model, change nothing else."""
            for window in self.windows:
                try:
                    window.kill()
                except Exception:  # noqa: BLE001 — kill never raises
                    pass
            self.windows = []

        def window_count(self):
            """Windows currently owned (built, shown or not)."""
            return len(self.windows)

        def shown_count(self):
            """Windows currently shown (affinity-gated)."""
            return sum(1 for w in self.windows if w.shown())


def create_and_attach_overlay():
    """Create the Phase F agent overlay and attach it to the state bus.

    This is the product wiring for this module: until this call, the
    overlay existed only in tests (no product module instantiated
    it). Returns (overlay_or_None, status_dict). Never raises: any
    failure (Qt absent, no screens, bus unavailable) yields overlay
    None and an honest status reason — the main window must never
    break on the overlay.

    Visual-only: no cue playback sink is wired here (the model
    records cue requests; playback stays a separate sink decision,
    and sound pools are ear-veto gated anyway).

    Status keys: attached (bool), windows_built (int),
    windows_shown (int), reason (str). On a machine where display
    affinity cannot be applied (non-Windows, older builds, failed
    call), windows stay hidden BY DESIGN (guide D8) and the status
    says so — hidden is not an error.
    """
    try:
        try:
            from voice_assistant.gui.state_bus import get_state_bus
        except Exception:  # noqa: BLE001 — lab layout uses gui.*
            from gui.state_bus import get_state_bus
        bus = get_state_bus()
    except Exception as exc:  # noqa: BLE001 — bus unavailable
        return None, {"attached": False, "windows_built": 0,
                      "windows_shown": 0,
                      "reason": "state bus unavailable: %s" % (exc,)}
    try:
        overlay = AgentOverlay()
    except Exception as exc:  # noqa: BLE001 — Qt absent/unavailable
        return None, {"attached": False, "windows_built": 0,
                      "windows_shown": 0,
                      "reason": "overlay unavailable: %s" % (exc,)}
    try:
        overlay.attach_to_bus(bus)
        built = len(overlay.build_windows())
        shown = overlay.show_all()
    except Exception as exc:  # noqa: BLE001 — attach/build never breaks launch
        try:
            overlay.kill()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass
        return None, {"attached": False, "windows_built": 0,
                      "windows_shown": 0,
                      "reason": "overlay attach failed: %s" % (exc,)}
    if shown:
        reason = "overlay live on %d window(s)" % shown
    elif built:
        reason = ("overlay attached; windows hidden by design: "
                  "display affinity unavailable on this machine (D8)")
    else:
        reason = "overlay attached; no screens found, no windows built"
    return overlay, {"attached": True, "windows_built": built,
                     "windows_shown": shown, "reason": reason}
