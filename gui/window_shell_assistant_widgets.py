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
    QObject,
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
        widths = []
        heights = []
        for i in range(layout.count()):
            wdg = layout.itemAt(i).widget()
            if wdg is None or wdg.objectName() != "chipSuggest":
                continue
            fm = QFontMetrics(wdg.font())
            need = (fm.horizontalAdvance(wdg.text())
                    + 2 * (_CHIP_PAD_X + _CHIP_BORDER))
            wdg.setMinimumWidth(max(need, wdg.sizeHint().width()))
            widths.append(wdg.minimumWidth())
            heights.append(wdg.sizeHint().height())
        # Round-3 fix #8 (A-UI5): the total comes from the chips'
        # own minimum widths, NOT from layout.minimumSize().
        # Freshly rebuilt chips report a stale layout minimum
        # synchronously (8 px — margins only — until the event
        # loop's first layout pass), so the old code froze the row
        # at the previous state's width with minimumWidth 8: in
        # the 4-chip state the row stayed 508 px, the scroll
        # maximum stayed 208, and the last chip (right edge 550)
        # could never be scrolled into view at all. The chips'
        # minimum widths above are set from live font metrics, so
        # their sum plus the layout's own margins and spacing is
        # the true total now — 508 px for three chips, 664 for
        # four — with no wait for a layout pass.
        if widths:
            margins = layout.contentsMargins()
            total = (margins.left() + margins.right()
                     + sum(widths)
                     + max(layout.spacing(), 0) * (len(widths) - 1))
        else:
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
            # Round-3 fix #8 (A-UI5): the same stale-layout trap
            # fires on EVERY rebuild, not just construction —
            # freshly rebuilt chips report a 4 px layout sizeHint
            # synchronously, so the old line froze the row at 4 px
            # tall and the chips at 0 px (invisible) in the
            # 4-chip state, and nothing ever re-ran it. The
            # chips' own sizeHints are correct synchronously
            # (35 px each), so the height comes from them plus
            # the layout's own top/bottom margins — 39 px —
            # with the sizeHint path kept only for the no-chips
            # case.
            if heights:
                margins = layout.contentsMargins()
                _want_h = (max(heights) + margins.top()
                           + margins.bottom())
            else:
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



class _ChipStripWheelFilter(QObject):
    """Round-3 fix #8 (A-UI5): the chip strip slides on the wheel.

    The strip was built as a horizontal scroll strip — the chips
    keep their natural size and slide instead of squeezing — but
    nothing could ever slide it: the scrollbar is always off, and
    a wheel over the strip did nothing (QScrollArea scrolls
    vertically, and the vertical bar is off too). The row is wider
    than the panel (508 px of chips in a 300 px viewport), so the
    later chips were cut at the right edge with no way to reach
    them.

    This filter, installed on the scroll area's viewport, turns a
    vertical wheel delta over the strip into horizontal scrolling
    (wheel down slides right, toward the later chips; wheel up
    slides back). A wheel that already carries a horizontal delta
    (trackpad swipe, Shift+wheel) is left alone — the scroll area
    handles it natively. At the scroll limit in the wheel's
    direction the event is NOT swallowed, so it keeps propagating
    to the chat transcript above (the _SettingsWheelFilter rule) —
    and a step that would pass the limit is clamped onto it, so
    the wheel lands exactly on the maximum instead of stalling a
    few px short (repair round: a notched wheel stalled at 200 of
    max 208 and left the last chip 3 px cut) — and when the strip
    fits its viewport there is nothing to scroll and the wheel
    passes through untouched.
    """

    def __init__(self, scroll: QScrollArea):
        super().__init__()
        self._scroll = scroll

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if event.type() != QEvent.Type.Wheel:
            return False
        bar = self._scroll.horizontalScrollBar()
        if bar.maximum() <= bar.minimum():
            return False
        if event.pixelDelta().x() or event.angleDelta().x():
            return False
        dy = event.pixelDelta().y() or event.angleDelta().y()
        if not dy:
            return False
        if event.pixelDelta().y():
            step = -dy
        else:
            step = -dy / 120.0 * bar.singleStep()
        # Repair round (parent-proven): CLAMP the step onto the
        # bar's range instead of rejecting an overshoot outright.
        # The first build returned False without moving when the
        # step would pass the limit, so a notched wheel stalled a
        # few px short of the maximum (200 of max 208, empty
        # state) and the last chip stayed cut by that much — the
        # wheel could never take the bar to its maximum. Clamping
        # lands exactly on the limit; when the bar is already
        # there the value does not move and the event still
        # propagates (the rule above is unchanged).
        new = bar.value() + step
        clamped = max(bar.minimum(), min(bar.maximum(), new))
        if clamped == bar.value():
            return False
        bar.setValue(round(clamped))
        return True


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
