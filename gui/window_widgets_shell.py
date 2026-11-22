# Refactor Phase 1 step 5: moved verbatim from gui/window_widgets.py (split by family).
from PySide6.QtCore import (
    Property,
    QElapsedTimer,
    QEvent,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPauseAnimation,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QSize,
    Qt,
    QTimer,
    Signal,
)
import asyncio
from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import live_session  # Phase 3 B2h: brain hub moved to core/brain/brain.py
import threading
from .style import ACCENT, BG, LINE_STRONG, TYPE_CARD_TITLE
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import random


class BridgeSignals(QObject):
    """The Qt thread-bridge (GAPS-28, G-V4) — NOT a second event
    bus. Worker threads (the voice worker, the routine scheduler,
    the watcher engine, the Google sign-in flow, the Settings
    worker threads) cannot touch widgets, so they hand their
    results to the GUI thread through these Qt signals, which Qt
    delivers queued on the GUI thread. The TYPED event channel
    is the state bus (core/state_bus.py): typed AppState /
    SensorState / MissionState / event dataclasses, produced by
    core modules and consumed by the overlay, the design system
    AND the main window (which subscribes to SettingsChanged).
    The two channels do not duplicate each other: the bus carries
    typed state/events, the bridge carries worker callbacks to
    Qt slots. The one deliberate overlap is the failsafe trip,
    which is emitted on both (the producers' FailsafeTripped
    event AND this bridge's failsafe_tripped signal, wired to
    the same handler) — the trip is safety-critical, so both
    paths must reach the window even if one channel fails.
    """

    status_changed = Signal(str)
    transcript_received = Signal(str, str)
    # Chat UI (2026-11-16): per-chunk live-reply signal — the
    # same words as transcript_received, but as they arrive from
    # the server, so the transcript streams in real time with
    # the voice instead of dropping the whole reply in at once.
    # Delivered queued on the GUI thread like the rest.
    transcript_chunk_received = Signal(str, str)
    tool_executed = Signal(str, str)
    scheduler_trigger = Signal(str, str)
    clipboard_content = Signal(str)
    wake_word_triggered = Signal()
    summon_requested = Signal()
    brain_check_completed = Signal(str)
    brain_setup_progress = Signal(str)
    brain_setup_completed = Signal(str)
    # Presence pack (v2.1): routine scheduler fires on its own thread;
    # headless routine runs report back through routine_result.
    routine_due = Signal(object)
    routine_result = Signal(str)
    # Watchers pack (v2.3): the watcher engine polls on its own thread;
    # notices cross to the GUI thread through this signal.
    watcher_event = Signal(str, str)
    # Knows-your-world pack (v2.4): Google connect()/disconnect() run on
    # worker threads (the sign-in flow blocks); results land here.
    world_status = Signal(str)
    # Batch 3 (2026-11-05): the Settings browser-enable flow runs on
    # a worker thread (attach can take ~10 s); the result dict lands
    # here as a JSON string — same bridge pattern as world_status.
    browser_enable_completed = Signal(str)
    # Cloud connect fix (v3.2): the Settings "Test key" check runs on a
    # worker thread (a real Google metadata call); the verdict lands
    # here as (kind, raw message) — kinds per core.live_client.
    key_test_completed = Signal(str, str)
    # v5.15: the Groq "Test key" verdict lands here the same way.
    groq_key_test_completed = Signal(str, str)
    # Round-3 OAuth build: the Settings "Continue with Google" /
    # "Test sign-in" verdict lands here the same way.
    gemini_signin_completed = Signal(str, str)
    # Auto-update (v5.2): worker-thread results for the About card's
    # update check / staging, as (state, JSON-or-message payload).
    update_check_completed = Signal(str, str)
    update_stage_completed = Signal(str, str)
    # Hardening (v4.3): the executor confirm gate calls its handler on
    # a worker thread; the popup itself must live on the GUI thread.
    # v5.48 (programme item 19): the request ID rides along so the
    # popup, a voice answer and the gate's decision log all name
    # the same request.
    confirm_requested = Signal(str, str, int)
    # Fix 9: a voice routine's plan approval crosses to the
    # GUI thread the same way — the task + its preview ride
    # the signal, the slot shows the plan dialog.
    plan_approval_requested = Signal(object, object)
    # v5.48 (programme item 21): capture_guard's indicator listener
    # fires on the capturing thread; the pill itself lives here.
    capture_indicator_changed = Signal(str, bool)
    # v5.48 (programme item 19b): plugin-trust worker results cross
    # to the GUI thread through these — a chat line, or a pinned
    # plugin to reload + announce.
    plugin_trust_notice = Signal(str)
    plugin_trust_pinned = Signal(str)
    # Chatfix H1 (v5.93 typed-chat silence): a typed send that fails
    # on the worker loop thread lands here — the GUI-thread slot
    # posts it as a system line, so a failed typed send is visible
    # instead of silent. Same bridge pattern as every other
    # worker-thread surface above.
    typed_send_failed = Signal(str, str)
    # Hardening (v4.3): the failsafe watchdog trips on its own thread;
    # the notice + tray state update land here (reason string).
    failsafe_tripped = Signal(str)
    # v5.42 (H14): the panic hotkey fires on the daemon's thread; the
    # stop itself (trip + mute + interrupt + caption) lands here, on
    # the GUI thread — the same bridge pattern as summon_requested.
    panic_requested = Signal()
    # v5.17: download diagnosis probes run on a worker thread; the
    # plain-language lines land here.
    diagnosis_ready = Signal(object)
    # v5.49 (programme item 35): suggestion deliveries cross to
    # the GUI thread through here — core.brain.suggestions' listener
    # fires on whatever thread raised the suggestion (a watcher
    # tick), and the card itself must be built on this thread.
    suggestion_received = Signal(object)
    # v5.57.1 (item 7): a suggestion Act runs the tool funnel,
    # which executes synchronously in the calling thread — so
    # running it in the slot froze the GUI for the tool's whole
    # duration (a browser nav or network tool = a visible app
    # hang). Act now runs on a worker thread; its outcome
    # (sid, outcome dict) lands here, like every other
    # worker-thread surface above.
    suggestion_act_completed = Signal(str, object)
    # v5.49 (programme items 29 + 32): progress lines from a
    # GUI-started headless plan run (the Plans Resume button)
    # cross here, one line at a time, into the goal pill — the
    # same pill the connected clients feed through _on_transcript.
    plan_progress_line = Signal(str)

class BackendWorker(threading.Thread):
    def __init__(self, signals: BridgeSignals):
        super().__init__(daemon=True)
        self.signals = signals
        self.loop = None

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        def on_status(status: str):
            self.signals.status_changed.emit(status)

        def on_transcript(role: str, text: str):
            self.signals.transcript_received.emit(role, text)

        def on_transcript_chunk(role: str, text: str):
            self.signals.transcript_chunk_received.emit(role, text)

        def on_tool(name: str, args: dict, result: dict):
            self.signals.tool_executed.emit(name, str(result.get("result", "")))

        live_session.on_status_change = on_status
        live_session.on_transcript = on_transcript
        live_session.on_transcript_chunk = on_transcript_chunk
        live_session.on_tool_call = on_tool

        try:
            self.loop.run_until_complete(live_session.start(audio_manager))
        except Exception as e:  # noqa: BLE001
            # v5.29: was [:60] — a truncated exception is
            # unrecognisable, so _friendly_error fell through to
            # the generic label and the real cause was invisible
            # anywhere in the UI. Session-level errors use [:300];
            # worker escapes now match.
            self.signals.status_changed.emit(f"ERROR: {str(e)[:300]}")
        finally:
            # v5.32: the loop is closed HERE, on the worker thread,
            # once the session future has fully unwound — never
            # stopped out from under a running session (see stop()).
            try:
                pending = [task for task in asyncio.all_tasks(self.loop)
                           if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(
                        *pending, return_exceptions=True))
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass
            try:
                self.loop.close()
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        # v5.32: ask the session to end, and ONLY that. Cancelling
        # its tasks makes start() unwind over several loop turns
        # and return, which ends run() above cleanly. The old code
        # also queued loop.stop() immediately; the loop died before
        # the unwind finished, run_until_complete raised
        # "Event loop stopped before Future completed.", and every
        # stop of a live session (Disconnect, close, a reconnect
        # handover) surfaced as that ERROR — field-reported
        # 2026-10-22, twice in one activity log. A session that
        # ignores its stop is still force-stopped, after a grace
        # period, by _force_stop below.
        live_session.stop()
        if self.loop and self.loop.is_running():
            timer = threading.Timer(5.0, self._force_stop)
            timer.daemon = True
            timer.start()

    def _force_stop(self):
        """Grace-period enforcer for stop(): force the loop down
        only when the worker is STILL alive 5 s after the session
        was asked to end (a wedged session). A session that
        unwound normally leaves a dead thread and a closed loop,
        and this is a no-op."""
        if self.is_alive() and self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

class AppShell(QWidget):
    """The window's body: chamfered dark shell + ambient orb glow + grain.

    Command Deck Phase D (D13): the silhouette is chamfered
    top-left + bottom-right (CHAMFER cuts) with tight 6 px arcs on
    the other two corners, a cyan strong-line border, and accent
    bracket ticks hugging the cuts. This is a PAINT-ONLY change:
    window flags, the QSizeGrip and the minimum size are untouched,
    so resize behaviour cannot regress here — but native Windows
    Snap Layouts cannot be exercised offscreen, so field
    verification on real Windows stays owed (see DECISIONS.md);
    if it ever regresses, the fallback is a 6 px radius with
    brackets and no silhouette cut.
    """

    CHAMFER = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.radius = 6
        self._glow_color = QColor(ACCENT)
        self._glow_strength = 0.15
        self._glow_center = None
        self._noise = self._make_noise()
        # v5.24 (Eyes E32): the translucent-shell option. Off by
        # default — the paint path below is then EXACTLY the
        # pre-v5.24 one. On, the shell's fill goes see-through
        # (the window is already translucent-frameless, so the
        # desktop ghosts through) and the edge pen firms up.
        self._glass = False

    @staticmethod
    def _make_noise() -> QPixmap:
        pix = QPixmap(128, 128)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        rng = random.Random(42)
        for _ in range(650):
            painter.setPen(QColor(255, 255, 255, 6))
            painter.drawPoint(rng.randrange(128), rng.randrange(128))
        for _ in range(300):
            painter.setPen(QColor(0, 0, 0, 6))
            painter.drawPoint(rng.randrange(128), rng.randrange(128))
        painter.end()
        return pix

    def set_glow(self, color: QColor, strength: float, center=None):
        self._glow_color = color
        self._glow_strength = max(0.0, min(strength, 1.0))
        if center is not None:
            self._glow_center = center
        self.update()

    def set_glass(self, enabled: bool):
        """v5.24 (E32): toggle the translucent shell. Only the
        fill alpha and the border pen change; everything else in
        the paint pass is shared with the opaque path."""
        self._glass = bool(enabled)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        w, h = rect.width(), rect.height()
        cut, arc = self.CHAMFER, self.radius
        path = QPainterPath()
        if arc <= 0:
            # Maximised (window_shell sets radius 0 on maximise):
            # a full square — chamfer cuts would show the desktop
            # through the corners of a maximised window.
            path.addRect(0, 0, w, h)
        else:
            path.moveTo(cut, 0)
            path.lineTo(w - arc, 0)
            path.quadTo(w, 0, w, arc)
            path.lineTo(w, h - cut)
            path.lineTo(w - cut, h)
            path.lineTo(arc, h)
            path.quadTo(0, h, 0, h - arc)
            path.lineTo(0, cut)
            path.closeSubpath()
        fill = QColor(BG)
        if self._glass:
            fill.setAlpha(205)
        painter.fillPath(path, fill)

        painter.save()
        painter.setClipPath(path)
        gx = self._glow_center.x() if self._glow_center else rect.width() * 0.62
        gy = self._glow_center.y() if self._glow_center else 300
        glow = QRadialGradient(gx, gy, 560)
        c = self._glow_color
        glow.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), int(16 + self._glow_strength * 34)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, glow)
        for x in range(0, rect.width(), 128):
            for y in range(0, rect.height(), 128):
                painter.drawPixmap(x, y, self._noise)
        painter.restore()

        painter.setPen(QColor(LINE_STRONG))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        # Bracket ticks hugging the two cuts (accent, 2 px) — only
        # when the cuts exist (not when maximised square).
        if arc > 0:
            painter.setPen(QPen(QColor(ACCENT), 2))
            tl = cut + 3
            painter.drawLine(tl, 1, tl + 12, 1)
            painter.drawLine(1, tl, 1, tl + 12)
            brx, bry = w - cut - 3, h - cut - 3
            painter.drawLine(brx - 12, h - 1, brx, h - 1)
            painter.drawLine(w - 1, bry - 12, w - 1, bry)

class TitleBar(QWidget):
    """Draggable custom title bar for the frameless window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setMinimumHeight(38)  # v5.54 (DPI rule ⑤): the title text may grow with font metrics
        self.on_toggle_maximize = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if self.on_toggle_maximize:
            self.on_toggle_maximize()
        event.accept()
