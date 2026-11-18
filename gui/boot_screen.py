"""R7-2 — cinematic boot screen (jarvis-mark-i pattern, own code).

The round-7 #2 pick (bhavyajustchill/jarvis-mark-i, MIT, class (b)
pattern-only) pairs its voice-reactive sphere with a cinematic
boot sequence. The repo itself is a Next.js/React/Three.js app;
VYUK has no WebEngine view, so NOTHING from its stack is
adopted. This module is the pattern's second half as VYUK's own
code — and it closes the OWED B-R1-4 visual render: the frame
(gui/window_shell_frame.py) says "the visual boot (rings/greeting
painted in Aura blue) is a later render batch reading
self.boot_report". This is that batch.

* ``boot_lines`` (gui/boot_lines.py, pure) turns a boot report
  (the core/desktop/boot_sequence.py ``report()`` dict — beats,
  every stage with its verdict, the log, the greeting) into
  boot-terminal rows, stage name + verdict per beat in order,
  greeting last. Pure so the lab can prove it on both
  interpreters, the agent_overlay.py two-layer pattern.
* ``BootScreen`` is the Qt overlay: a translucent panel in Share
  Tech Mono over the window while the sequence runs, dismissed on
  completion or on the Escape skip. It never takes input
  (WA_TransparentForMouseEvents) — the boot is a ritual, never a
  gate — and it never renders through _on_status (a boot status
  would falsely clear the session-liveness probe).
* VYUK's own words only: no Marvel marks, no "INITIALIZE", no
  arc-reactor imagery. Aura blue only — every colour comes from
  the caller's theme triple, never a literal (the
  test_commanddeck_guards colour rule).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from .boot_lines import boot_lines, boot_progress


class BootScreen(QWidget):
    """The boot overlay: a translucent boot-terminal panel.

    A child of the main window, sized to it on every report, shown
    while the boot sequence runs and hidden on completion or skip.
    It never takes mouse input and never blocks the event loop.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._report = None
        self._highlight = None
        self._mid = None
        self._edge = None
        self.hide()

    def set_palette(self, highlight, mid, edge):
        """The theme triple the panel paints in (from the orb's
        ``_colors()`` — the same Aura blue the orb already is)."""
        self._highlight = highlight
        self._mid = mid
        self._edge = edge

    def show_report(self, report):
        """Show the overlay with this boot report (a dict from
        ``BootSequence.report()``). Fits the parent window, raises,
        repaints. Never raises."""
        try:
            self._report = report
            parent = self.parentWidget()
            if parent is not None:
                self.setGeometry(parent.rect())
            self.show()
            self.raise_()
            self.update()
        except Exception:  # noqa: BLE001 — the boot never breaks startup
            pass

    def hide_screen(self):
        """Dismiss the overlay. Never raises."""
        try:
            self.hide()
        except Exception:  # noqa: BLE001
            pass

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        if self._report is None or self._highlight is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        edge = self._edge
        # Panel: the edge colour darkened to near-black blue.
        panel = QColor(max(0, edge.red() // 12),
                       max(0, edge.green() // 12),
                       max(0, edge.blue() // 12), 232)
        painter.fillRect(self.rect(), panel)
        lines = boot_lines(self._report)
        width, height = self.width(), self.height()
        # Fit the font to the panel: the block must fit vertically
        # AND the longest line horizontally (a long reason must
        # never run off the right edge). Shrink until it fits,
        # then elide anything that still overflows.
        px = max(11, min(17, height // 30))
        font = QFont("Share Tech Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
        while px > 9:
            font.setPixelSize(px)
            probe = QFontMetrics(font)
            line_h = int(probe.height() * 1.55)
            widest = max((probe.horizontalAdvance(t)
                          for (t, _) in lines), default=0)
            if (line_h * len(lines) <= height * 0.90
                    and widest <= width * 0.84):
                break
            px -= 1
            font.setPixelSize(px)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        line_h = int(metrics.height() * 1.55)
        total_h = line_h * len(lines)
        y = (height - total_h) // 2 + metrics.ascent()
        x = max(16, width // 8)
        max_text_w = width - x - 16
        hi, mid, ed = self._highlight, self._mid, self._edge
        for text, kind in lines:
            if kind == "header":
                col = QColor(hi.red(), hi.green(), hi.blue(), 255)
            elif kind == "greeting":
                col = QColor(hi.red(), hi.green(), hi.blue(), 255)
            elif kind == "beat":
                col = QColor(mid.red(), mid.green(), mid.blue(), 210)
            elif kind == "progress":
                col = QColor(mid.red(), mid.green(), mid.blue(), 160)
            else:
                col = QColor(mid.red(), mid.green(), mid.blue(), 225)
            painter.setPen(col)
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                       max_text_w)
            painter.drawText(x, y, shown)
            y += line_h
        # Progress rule: a thin bar under the text, filled to the
        # checked fraction, in the mid colour.
        checked, total = boot_progress(self._report)
        if total > 0:
            bar_w = width - 2 * x
            bar_y = y + 8
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ed.red(), ed.green(), ed.blue(), 70))
            painter.drawRect(x, bar_y, bar_w, 3)
            painter.setBrush(QColor(mid.red(), mid.green(), mid.blue(),
                                    220))
            painter.drawRect(x, bar_y,
                             int(bar_w * checked / total), 3)
