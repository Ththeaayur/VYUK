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
from .style import ACCENT, BG, LINE_STRONG, TYPE_CARD_TITLE
from .style import ACCENT_2, BG, DANGER, OK, TEXT, TEXT_MUTED
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
from .design_system.palette import parse_color, with_alpha
from .motion import (
    DURATION_DISMISS_MS,
    DURATION_IGNITE_MS,
    DURATION_PALETTE_FADE_MS,
    DURATION_QUICK_MS,
    DURATION_TAGLINE_MS,
    DURATION_WORDMARK_MS,
    EASE_DIALOG,
)


class _SlideOffset(QObject):
    """Animated offset for a layout-managed widget.

    v5.16: the offset travels along a (dx, dy) direction — the
    default (0, 1) is the original vertical rise; page transitions
    pass a horizontal direction so pages slide the way you navigated.
    """

    def __init__(self, widget, parent=None, dx: float = 0.0, dy: float = 1.0):
        super().__init__(parent)
        self._widget = widget
        self._base: QPoint | None = None
        self._offset = 0.0
        self._dx = float(dx)
        self._dy = float(dy)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float):
        self._offset = float(value)
        try:
            if self._base is None:
                self._base = QPoint(self._widget.pos())
            self._widget.move(
                self._base.x() + int(round(self._offset * self._dx)),
                self._base.y() + int(round(self._offset * self._dy)))
        except Exception:  # noqa: BLE001
            pass

    offset = Property(float, _get_offset, _set_offset)

    def snap(self):
        """Restore the captured base position exactly (idempotent)."""
        try:
            if self._base is not None:
                self._widget.move(self._base)
            self._offset = 0.0
        except Exception:  # noqa: BLE001
            pass

class _SplashOrbMark(QWidget):
    """The splash's igniting orb mark: rings draw in, glow blooms.

    Driven by a single float ``ignition`` property (0..1) animated
    by the splash; the paint pass only reads it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(132, 132)
        self._ignition = 0.0

    def _get_ignition(self) -> float:
        return self._ignition

    def _set_ignition(self, value: float):
        self._ignition = max(0.0, min(1.0, float(value)))
        self.update()

    ignition = Property(float, _get_ignition, _set_ignition)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        t = self._ignition
        _ar, _ag, _ab, _aa = parse_color(ACCENT)
        accent = QColor(_ar, _ag, _ab)
        _br, _bg, _bb, _ba = parse_color(ACCENT_2)
        teal = QColor(_br, _bg, _bb)
        _tr, _tg, _tb, _ta = parse_color(TEXT)
        # Glow bloom — grows and brightens with ignition.
        glow_r = 18 + 46 * t
        glow = QRadialGradient(cx, cy, glow_r + 22)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(),
                                    accent.blue(), int(90 * t)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(int(cx - glow_r - 22), int(cy - glow_r - 22),
                            int(2 * (glow_r + 22)), int(2 * (glow_r + 22)))
        # Three rings drawing in (arc sweep follows ignition).
        for radius, frac, col, width in (
            (58, 0.92, accent, 2.4),
            (48, 0.68, teal, 2.0),
            (38, 0.80, accent, 1.6),
        ):
            sweep = 360.0 * max(0.0, min(1.0, t * 1.15 - (1.0 - frac) * 0.3))
            if sweep <= 0:
                continue
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(col.red(), col.green(), col.blue(),
                                       70 + int(185 * t)), width))
            painter.drawArc(int(cx - radius), int(cy - radius),
                            int(2 * radius), int(2 * radius),
                            int(90 * 16), int(-sweep * 16))
        # Core — blooms from a point.
        core_r = 4 + 11 * t
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_tr, _tg, _tb, 60 + int(195 * t)))
        painter.drawEllipse(int(cx - core_r), int(cy - core_r),
                            int(2 * core_r), int(2 * core_r))
        painter.end()

class _WordmarkMark(QWidget):
    """The splash's draw-on wordmark (v5.24, Eyes E29).

    "VYUK" paints itself in: each letter fades in on a
    left-to-right stagger while the letter tracking contracts
    from wide (+16 px extra per gap) to its resting +4 px, and a
    thin accent rule draws in underneath, 0→100% of the text's
    span. Driven by a single float ``progress`` property (0..1),
    mirroring _SplashOrbMark's ``ignition`` pattern exactly; the
    paint pass only reads it.
    """

    TEXT = "VYUK"
    _INK = QColor(TEXT)       # text token, near the old pale ink
    _ACCENT = QColor(ACCENT)    # accent token, was indigo

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(392, 54)
        self._progress = 0.0

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float):
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        t = self._progress
        if t <= 0.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont(self.font())
        font.setPixelSize(30)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)
        # Tracking: 4 px at rest, +16 px extra while progress is 0.
        gap = 4.0 + 16.0 * (1.0 - t)
        widths = [fm.horizontalAdvance(ch) for ch in self.TEXT]
        span = sum(widths) + gap * (len(self.TEXT) - 1)
        x = (self.width() - span) / 2.0
        baseline = 36
        for i, ch in enumerate(self.TEXT):
            local = (t - i * 0.09) / 0.45
            local = max(0.0, min(1.0, local))
            if local > 0.0:
                painter.setPen(QColor(self._INK.red(), self._INK.green(),
                                      self._INK.blue(), int(255 * local)))
                painter.drawText(int(round(x)), baseline, ch)
            x += widths[i] + gap
        # The accent rule draws in under the letters, tracking the
        # text's current span.
        if t > 0.0:
            rule_x = (self.width() - span) / 2.0
            painter.setPen(QPen(QColor(self._ACCENT.red(),
                                       self._ACCENT.green(),
                                       self._ACCENT.blue(),
                                       int(255 * 0.70)), 1.5))
            painter.drawLine(int(round(rule_x)), 46,
                             int(round(rule_x + span * t)), 46)
        painter.end()

class SplashIgnition(QWidget):
    """Branded splash shown while the main window constructs.

    Phase G (guide S3 ignition sequence): dark surface, the orb
    mark igniting, the VYUK wordmark, and a rail of real
    self-check lines (LINK, MIC, VOICE, MODE) fed by the caller
    from real boot probes — never placeholders. A failed check
    renders an honest FAULT line with its reason. Timing is
    event-driven: the splash dismisses when the checks resolve,
    targeting 1.2 s from show and capped at 2.0 s no matter what;
    any click or keypress dismisses it immediately, and it is
    never modal and never blocks construction — it is shown, and
    the window builds behind it while the event loop is still
    the caller's. Once per launch: the caller creates it once.
    """

    TOTAL_MS = 1200
    HARD_CAP_MS = 2000

    def __init__(self, parent=None, reduced_motion: bool = False):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(440, 320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._reduced = bool(reduced_motion)
        self._dismissing = False
        self._closed = False
        self._anims: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 24)
        layout.setSpacing(6)
        layout.addStretch()
        self.orb_mark = _SplashOrbMark(self)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.orb_mark)
        row.addStretch()
        layout.addLayout(row)
        # v5.24 (E29): the wordmark draws itself on (letters
        # stagger in, tracking contracts, rule draws under) —
        # a custom-painted widget centred like the orb row above.
        self.wordmark = _WordmarkMark(self)
        mark_row = QHBoxLayout()
        mark_row.addStretch()
        mark_row.addWidget(self.wordmark)
        mark_row.addStretch()
        layout.addLayout(mark_row)
        tag = QLabel("Igniting…")
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;")
        # The tagline starts invisible and fades in late in the
        # sequence (see show_ignition); a QGraphicsOpacityEffect
        # animates without any layout shift.
        self._tag_effect = QGraphicsOpacityEffect(tag)
        self._tag_effect.setOpacity(0.0)
        tag.setGraphicsEffect(self._tag_effect)
        layout.addWidget(tag)
        # Phase G (S3): the self-check rail — one line per boot
        # check (LINK, MIC, VOICE, MODE), rendered from real
        # results by set_checks(); before results arrive every
        # line reads Starting…, never a fake Ready.
        self._check_labels: dict[str, QLabel] = {}
        for _name in ("LINK", "MIC", "VOICE", "MODE"):
            _lbl = QLabel(f"{_name} · Starting…")
            _lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _lbl.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 12px;"
                " background: transparent;")
            layout.addWidget(_lbl)
            self._check_labels[_name] = _lbl
        layout.addStretch()

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.dismiss)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._force_close)
        self._checks_done = False
        self._elapsed = None

    # ------------------------------------------------------------ paint
    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect(), 20, 20)
        _fr, _fg, _fb, _fa = parse_color(BG)
        _pr, _pg, _pb, _pa = parse_color(ACCENT)
        painter.fillPath(path, QColor(_fr, _fg, _fb, 248))
        painter.setPen(QPen(QColor(_pr, _pg, _pb, 70), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 20, 20)
        painter.end()
        super().paintEvent(event)

    # -------------------------------------------------------- lifecycle
    @staticmethod
    def _normalise_checks(checks):
        """Accept BootCheck objects, dicts, or (name, ok, detail)
        triples; return {name: (ok, detail)} for the four S3
        names. Anything unparseable reads as an unresolved line,
        never as a pass."""
        out: dict[str, tuple[bool, str]] = {}
        if not checks:
            return out
        for item in checks:
            try:
                if isinstance(item, dict):
                    name = str(item.get("name", ""))
                    ok = bool(item.get("ok", False))
                    detail = str(item.get("detail", ""))
                elif isinstance(item, (tuple, list)) and len(item) >= 3:
                    name, ok, detail = str(item[0]), bool(item[1]), str(item[2])
                else:
                    name = str(getattr(item, "name", ""))
                    ok = bool(getattr(item, "ok", False))
                    detail = str(getattr(item, "detail", ""))
            except Exception:  # noqa: BLE001 — unparseable, not a pass
                continue
            if name in ("LINK", "MIC", "VOICE", "MODE"):
                out[name] = (ok, detail)
        return out

    def set_checks(self, checks) -> None:
        """Render the check rail from real results: Ready in the
        ok token, FAULT + reason in the danger token, unresolved
        names staying on Starting…. Never raises."""
        try:
            resolved = self._normalise_checks(checks)
            for name, lbl in self._check_labels.items():
                if name in resolved:
                    ok, detail = resolved[name]
                    if ok:
                        lbl.setText(f"{name} · Ready")
                        lbl.setStyleSheet(
                            f"color: {OK}; font-size: 12px;"
                            " background: transparent;")
                    else:
                        lbl.setText(f"{name} · FAULT: {detail}")
                        lbl.setStyleSheet(
                            f"color: {DANGER}; font-size: 12px;"
                            " background: transparent;")
                else:
                    lbl.setText(f"{name} · Starting…")
                    lbl.setStyleSheet(
                        f"color: {TEXT_MUTED}; font-size: 12px;"
                        " background: transparent;")
            self._checks_done = len(resolved) == 4
        except Exception:  # noqa: BLE001 — the rail must never break boot
            pass

    def check_lines(self) -> list[str]:
        """The rendered check lines, in S3 order (for tests)."""
        return [self._check_labels[name].text()
                for name in ("LINK", "MIC", "VOICE", "MODE")]

    def checks_resolved(self, checks) -> None:
        """Event-driven dismiss: render the results, then dismiss
        — at once if the 1.2 s target already passed, else at the
        target mark. A no-op once dismissed; the watchdog still
        caps everything at 2.0 s."""
        if self._closed or self._dismissing:
            return
        self.set_checks(checks)
        try:
            self._auto_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._reduced:
            self.dismiss()
            return
        try:
            elapsed = (self._elapsed.elapsed()
                       if self._elapsed is not None else self.TOTAL_MS)
        except Exception:  # noqa: BLE001
            elapsed = self.TOTAL_MS
        if elapsed >= self.TOTAL_MS:
            self.dismiss()
        else:
            QTimer.singleShot(self.TOTAL_MS - int(elapsed), self.dismiss)

    def show_ignition(self, checks=None):
        """Show the splash and start the ignition sequence.

        `checks` (optional) renders the S3 rail at once and the
        splash dismisses at the 1.2 s target; without it the rail
        waits on checks_resolved() and the 2.0 s watchdog is the
        only closer besides click/key."""
        try:
            screen = self.screen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.x() + (geo.width() - self.width()) // 2,
                          geo.y() + (geo.height() - self.height()) // 2)
        except Exception:  # noqa: BLE001
            pass
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        if checks is not None:
            self.set_checks(checks)
        self.setWindowOpacity(0.0 if not self._reduced else 1.0)
        self.show()
        self.raise_()
        total = 220 if self._reduced else self.TOTAL_MS
        cap = 600 if self._reduced else self.HARD_CAP_MS
        self._watchdog.start(cap)
        if checks is not None or self._reduced:
            self._auto_timer.start(total)
        if self._reduced:
            try:
                self.orb_mark._set_ignition(1.0)
                self.wordmark._set_progress(1.0)
                self._tag_effect.setOpacity(1.0)
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            fade = QPropertyAnimation(self, b"windowOpacity", self)
            fade.setDuration(DURATION_QUICK_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(EASE_DIALOG)
            ignite = QPropertyAnimation(self.orb_mark, b"ignition", self)
            ignite.setDuration(DURATION_IGNITE_MS)
            ignite.setStartValue(0.0)
            ignite.setEndValue(1.0)
            ignite.setEasingCurve(EASE_DIALOG)
            # v5.24 (E29): the wordmark draws on alongside the orb.
            mark = QPropertyAnimation(self.wordmark, b"progress", self)
            mark.setDuration(DURATION_WORDMARK_MS)
            mark.setStartValue(0.0)
            mark.setEndValue(1.0)
            mark.setEasingCurve(EASE_DIALOG)
            # …and the tagline fades in late: a 350 ms pause, then
            # a 400 ms fade, sequenced inside the same group.
            tag_fade = QPropertyAnimation(self._tag_effect, b"opacity", self)
            tag_fade.setDuration(DURATION_TAGLINE_MS)
            tag_fade.setStartValue(0.0)
            tag_fade.setEndValue(1.0)
            tag_fade.setEasingCurve(EASE_DIALOG)
            tag_seq = QSequentialAnimationGroup(self)
            tag_seq.addAnimation(QPauseAnimation(350, self))
            tag_seq.addAnimation(tag_fade)
            group = QParallelAnimationGroup(self)
            group.addAnimation(fade)
            group.addAnimation(ignite)
            group.addAnimation(mark)
            group.addAnimation(tag_seq)
            group.start()
            self._anims = [group, fade, ignite, mark, tag_seq, tag_fade]
        except Exception:  # noqa: BLE001
            try:
                self.setWindowOpacity(1.0)
                self.orb_mark._set_ignition(1.0)
                self.wordmark._set_progress(1.0)
                self._tag_effect.setOpacity(1.0)
            except Exception:  # noqa: BLE001
                pass

    def dismiss(self):
        """Crossfade out (~220 ms); safe to call any number of times."""
        if self._closed or self._dismissing:
            return
        self._dismissing = True
        try:
            self._auto_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        # Watchdog for the fade itself: never linger half-faded.
        QTimer.singleShot(380, self._force_close)
        if self._reduced:
            self._force_close()
            return
        try:
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(DURATION_DISMISS_MS)
            anim.setStartValue(self.windowOpacity())
            anim.setEndValue(0.0)
            anim.setEasingCurve(EASE_DIALOG)
            anim.finished.connect(self._force_close)
            anim.start()
            self._anims.append(anim)
        except Exception:  # noqa: BLE001
            self._force_close()

    def _force_close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._watchdog.stop()
            self._auto_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.setWindowOpacity(0.0)
            self.hide()
            self.close()
        except Exception:  # noqa: BLE001
            pass

    def is_dismissed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------ input
    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        self.dismiss()
        event.accept()

    def keyPressEvent(self, event):  # noqa: N802 (Qt naming)
        self.dismiss()
        event.accept()
