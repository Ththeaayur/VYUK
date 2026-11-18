"""FlowLayout — a wrapping row layout (Qt flow-layout pattern).

Used by StatusStrip so the header pills row wraps to a second line
when — and only when — it does not fit, instead of clipping chips
mid-glyph (D1 residual at 908x500@120). Properties that matter:

* A chip is never shrunk past its glyphs: each item is placed at
  its sizeHint, and a line that overflows by no more than the
  chips' padding slack is absorbed by shrinking chips into their
  right padding (a QLabel draws its text left-aligned inside its
  padding, so shrinking by up to the right padding eats padding,
  never glyphs). Only when even the padding slack is exhausted
  does a chip move whole to the next line. This reproduces the
  old hbox behaviour at sizes that were legible (908x500@100
  squeezed ~12 px into padding and stayed one row) while fixing
  the sizes where the squeeze ate glyphs (908x500@120).
* Single-row equivalence: when the available width fits every
  visible chip at full sizeHint, the layout is exactly one row
  with the same spacing as the QHBoxLayout it replaces — normal
  sizes render identically.
* Hidden widgets are skipped (watch/capture/mode/mic/wake chips
  are hidden until their fact exists; they take no line space).
* ``hasHeightForWidth`` is true and ``heightForWidth`` is exact,
  so a parent box layout grows the row's height when chips wrap
  instead of squeezing the row.
"""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLabel


class FlowLayout(QLayout):
    """Left-to-right flow with wrapping, Qt-example semantics."""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):  # noqa: N802 (Qt naming)
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 (Qt naming)
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):  # noqa: N802 (Qt naming)
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802 (Qt naming)
        return Qt.Orientations()

    def hasHeightForWidth(self):  # noqa: N802 (Qt naming)
        return True

    def heightForWidth(self, width):  # noqa: N802 (Qt naming)
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):  # noqa: N802 (Qt naming)
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):  # noqa: N802 (Qt naming)
        # Preferred size = the single-row arrangement: every visible
        # chip on one line, exactly the old QHBoxLayout sizeHint.
        width = 0
        height = 0
        visible = [it for it in self._items
                   if it.widget() is None or not it.widget().isHidden()]
        for i, item in enumerate(visible):
            hint = item.sizeHint()
            width += hint.width()
            if i:
                width += self.spacing()
            height = max(height, hint.height())
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(),
                     height + margins.top() + margins.bottom())

    def minimumSize(self):  # noqa: N802 (Qt naming)
        # The row may shrink to a single chip wide — wrapping, not
        # clipping, absorbs the rest.
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def _do_layout(self, rect, test_only):
        """Lay out visible items; return the total height.

        Lines are broken greedily at sizeHint widths. When the
        next chip would overflow the line by no more than the
        line's padding slack (see _shrink_allowance), the line is
        kept whole and the overflow is absorbed by shrinking
        chips into their right padding, last chip first — glyphs
        stay intact. Only a larger overflow wraps the chip.
        """
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(),
                                  -margins.right(), -margins.bottom())
        spacing = self.spacing()
        visible = [it for it in self._items
                   if it.widget() is None or not it.widget().isHidden()]
        lines = []  # each: list of (item, width_to_use)
        current = []
        current_width = 0
        for item in visible:
            width = item.sizeHint().width()
            need = width if not current else current_width + spacing + width
            if not current or effective.width() <= 0:
                current.append((item, width))
                current_width = need
                continue
            if effective.x() + need - 1 <= effective.right():
                current.append((item, width))
                current_width = need
                continue
            overflow = effective.x() + need - 1 - effective.right()
            allowance = sum(_shrink_allowance(it) for it, _ in current)
            allowance += _shrink_allowance(item)
            if overflow <= allowance:
                # Absorb into right padding, last chip first, so
                # earlier chips keep their full padding.
                remaining = overflow
                widths = {}
                for it, iw in reversed(current + [(item, width)]):
                    take = min(remaining, _shrink_allowance(it))
                    widths[id(it)] = iw - take
                    remaining -= take
                    if remaining == 0:
                        break
                placed = [(it, widths.get(id(it), iw))
                          for it, iw in current + [(item, width)]]
                lines.append(placed)
                current = []
                current_width = 0
            else:
                lines.append(current)
                current = [(item, width)]
                current_width = width
        if current:
            lines.append(current)
        y = effective.y()
        line_height = 0
        for line in lines:
            x = effective.x()
            for item, width in line:
                hint = item.sizeHint()
                if not test_only:
                    item.setGeometry(
                        QRect(QPoint(x, y), QSize(width, hint.height())))
                x += width + spacing
                line_height = max(line_height, hint.height())
            y += line_height + spacing
            line_height = 0
        if not lines:
            return margins.top() + margins.bottom()
        # y advanced one spacing past the last line; undo it.
        return y - spacing - rect.y() + margins.bottom()


def _shrink_allowance(item):
    """Pixels an item may shrink without clipping its glyphs.

    For a text QLabel, the sizeHint width is text plus symmetric
    stylesheet padding; shrinking from the right by up to half
    the padding (the right share) eats padding only, because the
    text is drawn left-aligned inside the padding box. Anything
    else (glyph pixmap, non-label) gets zero allowance.
    """
    widget = item.widget()
    if widget is None or not isinstance(widget, QLabel):
        return 0
    if not widget.text():
        return 0
    # Qt6: pixmap() returns a (possibly null) QPixmap value, not
    # None, so test nullness explicitly — `is not None` alone
    # would wrongly disqualify every text label.
    pixmap = widget.pixmap()
    if pixmap is not None and not pixmap.isNull():
        return 0
    text_width = widget.fontMetrics().horizontalAdvance(widget.text())
    return max(0, (widget.sizeHint().width() - text_width) // 2)
