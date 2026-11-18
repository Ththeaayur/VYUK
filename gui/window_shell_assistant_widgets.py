# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtCore import (
    QEvent,
    QRectF,
    QSettings,
    QSize,
    QThread,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from .style import (
    ACCENT,
    AMBER,
    BG,
    DANGER,
    LINE_SUBTLE,
    PREMIUM_STYLE,
    SURFACE_2,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    TYPE_CARD_TITLE,
    TYPE_TITLE,
    RADIUS_BTN,
    load_bundled_fonts,
)

_CHIP_PAD_X = 14

_CHIP_BORDER = 1



class _ChipRow(QWidget):
    """The suggestion-chip strip inside _chip_scroll."""

    def fit_chips(self):
        """(Re)apply per-chip minimum widths from live metrics
        and size the row to the layout's true total."""
        layout = self.layout()
        if layout is None:
            return
        for i in range(layout.count()):
            wdg = layout.itemAt(i).widget()
            if wdg is None or wdg.objectName() != "chipSuggest":
                continue
            fm = QFontMetrics(wdg.font())
            need = (fm.horizontalAdvance(wdg.text())
                    + 2 * (_CHIP_PAD_X + _CHIP_BORDER))
            wdg.setMinimumWidth(max(need, wdg.sizeHint().width()))
        total = layout.minimumSize().width()
        if total > 0:
            # A plain resize() is not enough: the scroll area's
            # own layout pass re-applies the row's (lagging)
            # sizeHint afterwards. The floor has to be a minimum
            # width — every later pass honours it.
            self.setMinimumWidth(total)
            # Chat pass (2026-11-17): the height here must come
            # from the layout's sizeHint, NOT from self.height().
            # At construction the row has no geometry yet, so
            # height() is the bare margins (4 px) — and this
            # resize froze the row at 4 px tall forever, so the
            # chips (35 px tall) never painted: the strip above
            # the composer showed as 42 px of dead space. The
            # same line shipped in v6.3.0 (see the backup) — the
            # starters were invisible there too. The sizeHint
            # height is the layout's true height (39 px); fall
            # back to the current height only if it says 0.
            _want_h = self.sizeHint().height()
            if _want_h <= 0:
                _want_h = self.height()
            self.resize(max(total, self.width()), _want_h)

    def changeEvent(self, event):  # noqa: N802 (Qt naming)
        super().changeEvent(event)
        try:
            if event.type() in (QEvent.Type.FontChange,
                                QEvent.Type.StyleChange,
                                QEvent.Type.PaletteChange):
                self.fit_chips()
        except Exception:  # noqa: BLE001 — cosmetics never break chat
            pass



class _HeroWidget(QWidget):
    """Conversion Phase 0: the home hero's sizeHint is honest.

    The hero carries a QStackedLayout (StackAll) whose children
    (ParticleField, OrbVisualizer) both report an invalid
    sizeHint, so the stacked layout's totalSizeHint — and with
    it the hero's — was (0, 0). The stage layout then treated
    the hero as wanting no height at all: at the default
    750 px window height it gave the hero a ~159 px cell while
    the hero widget, clamped by its 340 px minimum size, still
    painted 340 px tall — straight over the wordmark and the
    state line below it (render-proven at 960/1060 px wide).
    This subclass states the intended height (340 px) so the
    layout gives the hero its full height whenever the stage
    has room, while the low minimum height (80 px, set at
    construction, with the visualizer's) lets it shrink
    toward its cell when the stage is short — the old 340 px
    floor re-clamped the widget 73 px above its cell at the
    620 px window minimum and the overlap returned there,
    while a zero floor collapsed the orb to a 13 px sliver
    at that height. No other behaviour changes."""

    def sizeHint(self):  # noqa: N802 (Qt naming)
        return QSize(400, 340)



class _DotsSplitterHandle(QSplitterHandle):
    """v5.57 (redo, A9.3): the stage/chat divider grip — three
    faint dots, centred, on a transparent handle."""

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TEXT_FAINT))
        cx = self.width() // 2
        cy = self.height() // 2
        for dy in (-14, 0, 14):
            painter.drawEllipse(cx - 2, cy + dy - 2, 4, 4)



class _DotsSplitter(QSplitter):
    """A horizontal splitter whose handle is the dots grip."""

    def createHandle(self):  # noqa: N802 (Qt naming)
        return _DotsSplitterHandle(self.orientation(), self)
