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
    QRect,
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
    QLayout,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
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



class _ChipFlowLayout(QLayout):
    """E-F101: the chip strip wraps onto a second line instead
    of running past the panel edge.

    The strip was a QHBoxLayout sized to the chips' true total
    (508 px for three chips, 664 for four), so in a 300–400 px
    panel the later chips were cut mid-word at the right edge at
    every width, day and night, on both pages — with no visible
    affordance that anything was hidden. This layout places the
    chips left to right and wraps onto a second line when the
    next chip would pass the row width, so every chip is fully
    readable at rest. Each line is centred in the row — the
    pre-fix look (the strip's stretch spacers centred the single
    line) kept per line.

    It reads each chip's own sizeHint, which is correct
    synchronously for freshly rebuilt chips (fix #8: the layout
    minimum lags one event-loop pass behind). The row's centring
    spacers are QSpacerItems, not widgets — a flow layout cannot
    place them on a line — so addItem drops them (the row
    recentres each line itself in _do_layout).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []

    def addItem(self, item):
        if item.widget() is not None:
            self._items.append(item)

    def addStretch(self, stretch=0):
        # E-F101: a no-op — spacers are dropped (see addItem),
        # the row recentres each line itself. Kept so
        # _refresh_chip_row (which brackets the chips with
        # stretches for the old QHBoxLayout) works unchanged;
        # QLayout itself has no addStretch (QBoxLayout only).
        return None

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        # One line tall, widest chip wide — the official flow
        # example's shape. The height for a given width comes
        # from heightForWidth, not from here.
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        top = rect.y() + margins.top()
        avail = max(rect.width() - margins.left()
                    - margins.right(), 0)
        spacing = max(self.spacing(), 0)
        x = left
        y = top
        line_height = 0
        line = []

        def _flush(items, cy, lh):
            if not items:
                return cy
            total = sum(it.sizeHint().width() for it in items)
            total += spacing * (len(items) - 1)
            cx = left + max(0, (avail - total) // 2)
            for it in items:
                if not test_only:
                    it.setGeometry(QRect(cx, cy,
                                         it.sizeHint().width(), lh))
                cx += it.sizeHint().width() + spacing
            return cy + lh + spacing

        for item in self._items:
            if item.widget() is None:
                continue
            iw = item.sizeHint().width()
            ih = item.sizeHint().height()
            if line and x + iw > left + avail:
                y = _flush(line, y, line_height)
                line = []
                x = left
                line_height = 0
            line.append(item)
            x += iw + spacing
            line_height = max(line_height, ih)
        if line:
            y = _flush(line, y, line_height)
            y -= spacing
        return y - rect.y() + margins.bottom()


class _ChipScrollArea(QScrollArea):
    """E-F101: the chip strip's scroll area, sized to the
    wrapped row instead of the fixed 42 px single line.

    A plain QScrollArea reports the row's sizeHint height —
    the fully stacked height (every chip on its own line) —
    so the strip would stand 125–168 px tall with a band of
    dead space under one or two lines of chips. This subclass
    is a heightForWidth widget: its height for a given width
    is the row's wrapped height at the viewport width plus
    the frame, so the panel layout sizes the strip to exactly
    the lines the chips need — one line where they fit, two
    where they wrap. The wheel filter (fix #8) stays
    installed; with nothing to scroll (bar maximum 0) it
    passes the wheel through to the transcript.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self):
        row = self.widget()
        return row is not None and row.layout() is not None

    def heightForWidth(self, width):
        row = self.widget()
        if row is None or row.layout() is None:
            return super().heightForWidth(width)
        avail = max(width - 2 * self.frameWidth(), 0)
        return (row.layout().heightForWidth(avail)
                + 2 * self.frameWidth())


class _ChipRow(QWidget):
    """The suggestion-chip strip inside _chip_scroll."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # E-F101: the row reports its wrapped height for a
        # given width, so the scroll area (widgetResizable)
        # sizes the strip to the wrapped content.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def fit_chips(self):
        """(Re)apply per-chip minimum widths from live metrics.

        E-F101: the row no longer sizes itself to the chips'
        total — it wraps (see _ChipFlowLayout), so the old
        minimum-width forcing (508 px for three chips, 664
        for four) is what pushed the later chips past the
        panel edge, and it is gone. The per-chip minimum
        widths stay: a chip must never squeeze mid-word,
        whatever the panel width. The wrapped height follows
        from the layout's heightForWidth; updateGeometry()
        tells the scroll area to re-ask for it.
        """
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
        self.updateGeometry()
        # E-F101: the row's parent is the scroll area's viewport,
        # which has no layout — so the row's own updateGeometry()
        # dies there and the panel layout never re-asks the strip
        # for its wrapped height. The strip then keeps the
        # previous set's height: after a 4-line set shrinks to 3
        # lines the strip stands 168 px tall with a band of dead
        # space under the chips (the suite caught it: 168 !=
        # 125). Tell the scroll area too, so the panel layout
        # re-queries heightForWidth on the next event-loop pass.
        try:
            viewport = self.parentWidget()
            scroll = (viewport.parentWidget()
                      if viewport is not None else None)
            if isinstance(scroll, QScrollArea):
                scroll.updateGeometry()
        except Exception:  # noqa: BLE001 — cosmetics never break chat
            pass

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
