# Refactor Phase 1 step 5: moved verbatim from gui/window_widgets.py (split by family).
from pathlib import Path
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
import math
from .style import ACCENT_2, BG, DANGER, OK, TEXT, TEXT_MUTED
from .design_system.palette import parse_color, with_alpha


_ASSETS = Path(__file__).resolve().parent / "assets"

class WaveformWidget(QWidget):
    """Live voice waveform for the dock (Phase 2).

    Command Deck Phase E (§9.2 Waveform): the dock carries 48
    cyan bars — the same canonical count as the orb's EQ halo —
    in the same 104 px widget, so the bars go thin (1.5 px bar,
    0.66 px gap: 48 bars span ~103 px). The accent is the cyan
    token; the idle shimmer and band resampling are unchanged.
    """

    _BAR_COUNT = 48

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(104, 30)
        self._bands = [0.0] * self._BAR_COUNT
        self._targets = [0.0] * self._BAR_COUNT
        self._accent = QColor(ACCENT)
        self._phase = 0.0

    def set_accent(self, color: QColor):
        self._accent = color

    def update_data(self, energy: float, bands):
        self._phase += 0.35
        src = bands if bands and len(bands) >= 16 else [0.0] * 16
        for i in range(self._BAR_COUNT):
            b = src[int(i / self._BAR_COUNT * 15)]
            idle = 0.06 + 0.04 * abs(__import__("math").sin(self._phase + i * 0.7))
            self._targets[i] = max(idle, min(1.0, b * 1.4 + energy * 0.25))
            self._bands[i] += (self._targets[i] - self._bands[i]) * 0.35
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        bar_w, gap = 1.5, 0.66
        mid = self.height() / 2
        for i, v in enumerate(self._bands):
            h = 3 + v * (self.height() - 6)
            x = i * (bar_w + gap)
            painter.setBrush(QColor(self._accent.red(), self._accent.green(),
                                    self._accent.blue(), 90 + int(v * 165)))
            painter.drawRoundedRect(int(x), int(mid - h / 2), int(bar_w), int(h), 0.75, 0.75)

class MiniEq(QWidget):
    """Tiny equaliser that animates inside VYUK's bubble while she speaks."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 13)
        self._values = [0.2] * 5

    def set_data(self, energy: float, bands):
        src = bands if bands and len(bands) >= 5 else [energy] * 5
        picks = [src[1], src[4], src[7], src[10], src[13]] if len(src) >= 14 else list(src[:5])
        self._values = [max(0.12, min(1.0, v * 1.5 + energy * 0.3)) for v in picks]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(125, 211, 252, 210))
        for i, v in enumerate(self._values):
            h = 2 + v * 10
            painter.drawRoundedRect(i * 10, int(13 - h), 4, int(h), 2, 2)

class ThinkingDots(QWidget):
    """Painted thinking indicator — three accent dots on a travelling wave.

    Refinement A: replaces the old \u25cf \u25cb \u25cb text glyphs (which
    rendered differently on every machine). Real painted circles whose
    radius and alpha ride a sine wave; the main window's existing thinking
    timer drives it through set_phase(), and the colour follows the orb's
    live accent.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(48, 14)
        self._phase = 0.0
        self._accent = QColor(ACCENT)

    def set_accent(self, color: QColor):
        try:
            if color is not None and color.isValid():
                self._accent = QColor(color)
        except Exception:  # noqa: BLE001
            pass

    def set_phase(self, phase: int):
        self._phase = phase * 0.55
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        cy = self.height() / 2.0
        for i in range(3):
            wave = 0.5 + 0.5 * math.sin(
                2 * math.pi * (self._phase - i * 0.8) / 4.5)
            r = 2.6 + 1.9 * wave
            painter.setBrush(QColor(
                self._accent.red(), self._accent.green(),
                self._accent.blue(), 80 + int(175 * wave)))
            cx = 8 + i * 16
            painter.drawEllipse(
                int(cx - r), int(cy - r), int(2 * r), int(2 * r))

class _ActivitySparkline(QWidget):
    """v5.17 (Ears A13): seven days of listening vs speaking time,
    two slim bars per day (green = listening, indigo = speaking),
    painted from the per-day seconds the telemetry tick accrues.
    A quiet widget: no text, the tooltip carries the totals."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(96, 24)
        self._days: dict[str, list] = {}
        self.setToolTip("Listening / speaking time, last 7 days")

    def set_days(self, days: dict):
        self._days = days or {}
        try:
            import datetime as _dt
            today = _dt.date.today()
            total_l = total_s = 0
            for back in range(7):
                rec = self._days.get((today - _dt.timedelta(days=back)).isoformat(), [0, 0])
                total_l += int(rec[0]); total_s += int(rec[1])
            self.setToolTip(
                f"Last 7 days — listening {total_l // 60}m {total_l % 60}s · "
                f"speaking {total_s // 60}m {total_s % 60}s")
        except Exception:  # noqa: BLE001
            pass
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt name)
        try:
            import datetime as _dt
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            today = _dt.date.today()
            recs = []
            peak = 60.0
            for back in range(6, -1, -1):
                rec = self._days.get((today - _dt.timedelta(days=back)).isoformat(), [0, 0])
                recs.append(rec)
                peak = max(peak, float(rec[0]) + float(rec[1]))
            base = self.height() - 3
            painter.setPen(QColor(*parse_color(TEXT)[:3], 40))
            painter.drawLine(0, base, self.width(), base)
            slot = self.width() / 7.0
            for i, rec in enumerate(recs):
                x = i * slot + slot * 0.22
                w = slot * 0.24
                hl = (float(rec[0]) / peak) * (self.height() - 6)
                hs = (float(rec[1]) / peak) * (self.height() - 6)
                if hl > 0.5:
                    painter.setBrush(QColor(OK))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(int(x), int(base - hl), int(w), int(hl), 1, 1)
                if hs > 0.5:
                    painter.setBrush(QColor(ACCENT))
                    painter.drawRoundedRect(int(x + w + 1), int(base - hs), int(w), int(hs), 1, 1)
            painter.end()
        except Exception:  # noqa: BLE001 — a sparkline must never break the page
            pass

class _CodeBlock(QFrame):
    """A fenced code block inside a chat bubble: monospace,
    spacing preserved, horizontally scrollable, with its own
    Copy button (Phase 2, chatbox #14)."""

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.setObjectName("codeBlock")
        self._code = code
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        head = QHBoxLayout()
        lbl = QLabel("CODE")
        lbl.setObjectName("codeHead")
        head.addWidget(lbl)
        head.addStretch()
        btn = QPushButton("Copy")
        btn.setObjectName("bubbleAction")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAccessibleDescription("Copy this code block")
        btn.clicked.connect(self._copy)
        head.addWidget(btn)
        lay.addLayout(head)
        edit = QPlainTextEdit()
        edit.setObjectName("codeText")
        edit.setPlainText(code)
        edit.setReadOnly(True)
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lines = max(1, code.count("\n") + 1)
        lh = edit.fontMetrics().lineSpacing()
        edit.setFixedHeight(min(10, lines) * lh + 14)
        lay.addWidget(edit)
        self._edit = edit

    def _copy(self):
        try:
            QGuiApplication.clipboard().setText(self._code)
        except Exception:  # noqa: BLE001
            pass

class _ComposerEdit(QTextEdit):
    """Phase 2 (chatbox #3): the dock composer — multi-line and
    auto-growing (1–5 lines). Enter sends; Shift+Enter makes a new
    line. Keeps the small QLineEdit-shaped surface the rest of the
    window already uses: text(), setText(), clear()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_send = None
        self.on_recall = None
        self.setAcceptRichText(False)
        self.textChanged.connect(self._adjust_height)
        # Audit-2 slice 1 (UI-06): textChanged alone leaves a stale
        # height when the panel is resized under a multiline draft —
        # rewrapping changes the document size without editing text.
        # documentSizeChanged fires on every relayout, so the height
        # tracks the wrapped document in both directions.
        try:
            self.document().documentLayout().documentSizeChanged.connect(
                self._adjust_height)
        except Exception:  # noqa: BLE001
            pass
        self._adjust_height()

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value: str):  # noqa: N802 (Qt naming)
        self.setPlainText(value or "")

    def keyPressEvent(self, event):  # noqa: N802 (Qt naming)
        # S12 (C2): Up/Down walk the sent-message history when the
        # window's recall hook offers a line (it declines while a
        # draft is being typed, so cursor movement is untouched).
        if (event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)
                and self.on_recall is not None):
            offered = self.on_recall(
                -1 if event.key() == Qt.Key.Key_Up else 1)
            if offered is not None:
                self.setText(offered)
                cursor = self.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.setTextCursor(cursor)
                event.accept()
                return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            if self.on_send is not None:
                self.on_send()
            event.accept()
            return
        super().keyPressEvent(event)

    def _adjust_height(self):
        try:
            doc_h = int(self.document().size().height())
        except Exception:  # noqa: BLE001
            doc_h = 22
        line = max(18, self.fontMetrics().lineSpacing())
        new_h = max(46, min(doc_h + 20, line * 5 + 20))
        # No-op guard: a height set issued from a document-layout
        # signal must not re-enter layout when already correct.
        if new_h != self.height():
            self.setFixedHeight(new_h)



# ------------------------------------------------------------ v5.45 F8 chip
_AURA_CHIP_SIZE = 28

_aura_chip_pixmap_cache = None

def _aura_chip_pixmap():
    """The compact Aura identity mark for her chat bubbles (F8).

    Rendered ONCE from the shipped master (gui/assets/vyuk_icon.png
    — the F3 Aura render) and cached at module level: every her-
    bubble shares this single QPixmap, no per-bubble re-render.
    The chip is a static pixmap — no animation of any kind — so
    reduced-motion needs nothing further from it. A missing master
    degrades to an empty (transparent) chip, never a crash."""
    global _aura_chip_pixmap_cache
    if _aura_chip_pixmap_cache is not None:
        return _aura_chip_pixmap_cache
    pm = QPixmap(str(_ASSETS / "vyuk_icon.png"))
    if pm.isNull():
        pm = QPixmap(_AURA_CHIP_SIZE, _AURA_CHIP_SIZE)
        pm.fill(Qt.GlobalColor.transparent)
    else:
        pm = pm.scaled(
            _AURA_CHIP_SIZE, _AURA_CHIP_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
    _aura_chip_pixmap_cache = pm
    return pm
