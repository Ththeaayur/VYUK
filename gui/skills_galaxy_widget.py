"""Skills Galaxy container widget + skill detail card.

Split from gui/skills_galaxy.py (GUI split #5, move only).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from .design_system.palette import with_alpha
from .skills_galaxy_canvas2d import Galaxy2DCanvas
from .skills_galaxy_canvas3d import Galaxy3DWidget, _GL_AVAILABLE
from .skills_galaxy_data import GalaxyData, build_galaxy
from .style import BG, RADIUS_BTN
from .style_tokens import MONO_FONT_STACK, first_font_family
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set

# ---------------------------------------------------------------------------
# The container: hosts the 2D canvas and the 3D view behind
# "2D | 3D" chips, mirroring MemoryWebWidget's contract. The
# choice persists in QSettings ("skills/dimension"); selection
# and search sync across a swap so it feels like the same map,
# re-lit — not a reset.
# ---------------------------------------------------------------------------

class SkillsGalaxyWidget(QWidget):
    """The Skills Galaxy: every built-in tool as a star around
    its group hub, in 2D or 3D. Feed it declarations (plugins
    already excluded — the window owns that filter, as it does
    for the Skills list) plus the friendly-name map."""

    skill_chosen = Signal(str)           # tool name
    selection_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 280)
        # Without tracking, Qt drops no-button mouse-move events
        # addressed to this widget before its handlers ever see
        # them — and the relay below lives on those events.
        self.setMouseTracking(True)

        self._mode = "2d"
        self._mode_pending: str | None = None
        self._gl_unavailable = not _GL_AVAILABLE
        self._galaxy = GalaxyData()
        self._search_text = ""
        self._canvas = Galaxy2DCanvas(self)
        self._canvas.skill_chosen.connect(self.skill_chosen.emit)
        self._canvas.selection_cleared.connect(
            self.selection_cleared.emit)
        self._view3d = None
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._canvas)
        # Dimension chips, floating top-right (same chrome as the
        # Memory Web's, its own object name).
        self._chip_bar = QWidget(self)
        self._chip_bar.setObjectName("galaxyDimBar")
        self._chip_bar.setStyleSheet(
            f"#galaxyDimBar {{ background: {with_alpha(BG, 175)};"
            f" border-radius: {RADIUS_BTN}px; }}")
        bar_layout = QHBoxLayout(self._chip_bar)
        bar_layout.setContentsMargins(2, 2, 2, 2)
        bar_layout.setSpacing(2)
        self._btn_2d = QPushButton("2D")
        self._btn_2d.setObjectName("chipBtn")
        self._btn_2d.setCheckable(True)
        self._btn_2d.setChecked(True)
        self._btn_2d.clicked.connect(lambda: self._set_mode("2d"))
        bar_layout.addWidget(self._btn_2d)
        self._btn_3d = QPushButton("3D")
        self._btn_3d.setObjectName("chipBtn")
        self._btn_3d.setCheckable(True)
        self._btn_3d.clicked.connect(lambda: self._set_mode("3d"))
        bar_layout.addWidget(self._btn_3d)
        if self._gl_unavailable:
            self._disable_3d_chip()
        self._chip_bar.adjustSize()
        persisted = str(qsettings_get("skills/dimension") or "2d").lower()
        if persisted == "3d" and not self._gl_unavailable:
            # GL needs a shown window — activate on first show.
            self._mode_pending = "3d"

    # ------------------------------------------------------------ chips/mode

    @property
    def dimension_mode(self) -> str:
        return self._mode

    def _disable_3d_chip(self):
        self._btn_3d.setEnabled(False)
        self._btn_3d.setToolTip("3D needs OpenGL on this PC")

    def _sync_chips(self):
        for btn, value in ((self._btn_2d, "2d"), (self._btn_3d, "3d")):
            btn.blockSignals(True)
            btn.setChecked(self._mode == value)
            btn.blockSignals(False)

    def _position_chips(self):
        self._chip_bar.adjustSize()
        self._chip_bar.move(max(0, self.width()
                                - self._chip_bar.width() - 10), 10)
        self._chip_bar.raise_()

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self._mode_pending == "3d":
            self._mode_pending = None
            self._set_mode("3d", persist=False)
        self._position_chips()

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._position_chips()

    def _ensure_3d(self) -> bool:
        if self._view3d is not None:
            return True
        if not _GL_AVAILABLE:
            self._gl_unavailable = True
            return False
        try:
            widget = Galaxy3DWidget(self)
        except Exception:  # noqa: BLE001
            self._gl_unavailable = True
            return False
        widget.skill_chosen.connect(self.skill_chosen.emit)
        widget.selection_cleared.connect(self.selection_cleared.emit)
        self._view3d = widget
        self._stack.addWidget(widget)
        widget.set_data(self._galaxy)
        if self._search_text:
            widget.set_search_highlight(self._search_text)
        return True

    def _set_mode(self, mode: str, persist: bool = True):
        if mode == self._mode:
            # Re-clicking the live chip is a no-op — syncing here
            # would read state back from the hidden renderer and
            # clobber whatever the user just did on stage.
            self._sync_chips()
            return
        if mode == "3d":
            if self._gl_unavailable or not self._ensure_3d():
                self._disable_3d_chip()
                mode = "2d"
        if mode == "3d" and self._view3d is not None:
            view = self._view3d
            if self._search_text:
                view.set_search_highlight(self._search_text)
            selected = self._canvas.selected_name()
            self._stack.setCurrentWidget(view)
            self._mode = "3d"
            view.set_active(True)
            if selected:
                view.select(selected)
            else:
                view.clear_selection()
            # v5.57.1 (item 9): recover an already-stranded
            # camera on re-entry (a stranded camera persists
            # across page switches otherwise). Deliberate
            # framing is untouched — only a stranded one resets.
            if view.is_framing_stranded():
                view.reset_camera()
            view.update()
            QTimer.singleShot(450, self._verify_gl)
        else:
            if self._view3d is not None:
                view = self._view3d
                selected = view.selected_name()
                view.set_active(False)
                self._stack.setCurrentWidget(self._canvas)
                self._mode = "2d"
                if selected:
                    self._canvas.select(selected)
                else:
                    self._canvas.clear_selection()
            else:
                self._stack.setCurrentWidget(self._canvas)
                self._mode = "2d"
        if persist:
            qsettings_set("skills/dimension", self._mode)
        self._sync_chips()
        self._position_chips()

    def _verify_gl(self):
        """First-show safety net: the 3D widget constructed fine
        but its GL context may still be unusable (VMs, remote
        desktop, ancient drivers). If so, fall back to 2D."""
        if self._mode != "3d" or self._view3d is None:
            return
        view = self._view3d
        context = None
        try:
            context = view.context()
        except Exception:  # noqa: BLE001
            context = None
        ok = bool(view.gl_ready) and context is not None \
            and context.isValid()
        if not ok:
            self._gl_unavailable = True
            self._disable_3d_chip()
            self._set_mode("2d", persist=True)

    # ------------------------------------------------------------ active

    def _active(self):
        if self._mode == "3d" and self._view3d is not None:
            return self._view3d
        return self._canvas

    @property
    def canvas2d(self) -> Galaxy2DCanvas:
        return self._canvas

    @property
    def view3d(self):
        return self._view3d

    @property
    def galaxy(self) -> GalaxyData:
        return self._galaxy

    # ------------------------------------------------------------ data

    def set_data(self, declarations: list, friendly_names: dict):
        """(Re)build the galaxy from tool declarations + the
        window's friendly-name map. GAPS-29 (E-V1): plugin tools
        are INCLUDED — they land in "More skills" through the
        group_for_name fallback, exactly like the list view.
        (The old docstring said plugins must already be excluded
        by the caller; that exclusion was the E-V1 flaw.)"""
        self._galaxy = build_galaxy(declarations, friendly_names)
        self._canvas.set_data(self._galaxy)
        if self._view3d is not None:
            self._view3d.set_data(self._galaxy)
            if self._search_text:
                self._view3d.set_search_highlight(self._search_text)

    def set_search_highlight(self, text: str):
        self._search_text = (text or "").strip()
        self._canvas.set_search_highlight(text)
        if self._view3d is not None:
            self._view3d.set_search_highlight(text)

    # ------------------------------------------------------------ selection

    def selected_name(self) -> str | None:
        return self._active().selected_name()

    def select(self, name: str | None):
        """Select a star by tool name on the live renderer (and
        keep the hidden one honest for the next swap)."""
        self._active().select(name)
        other = self._canvas if self._active() is self._view3d \
            else self._view3d
        if other is not None:
            # Silent sync: set the field directly so the swap
            # doesn't re-emit (the live renderer already spoke).
            node = self._galaxy.node(name) if name else None
            other._selected = node.name if node is not None \
                and node.kind == "star" else None
            other.update()

    def clear_selection(self):
        self.select(None)

    def search_dimmed_ids(self) -> set:
        return self._active().search_dimmed_ids()

    def search_matched_ids(self) -> set:
        return self._active().search_matched_ids()

    # ------------------------------------------------------------ event relay

    # Real pointer events land on whichever child covers the
    # widget; these handlers exist so events addressed to the
    # container itself (programmatic senders, and any chrome
    # around the renderers) still reach the active renderer
    # unchanged. The renderers fill the container exactly, so
    # coordinates pass through untouched.
    def _relay(self, event) -> bool:
        from PySide6.QtWidgets import QApplication

        target = self._active()
        if target is None or target is self:
            return False
        etype = event.type()
        # The in-flight event object cannot be re-sent from inside
        # its own delivery — forward a faithful clone.
        if etype in (QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonRelease,
                     QEvent.Type.MouseButtonDblClick,
                     QEvent.Type.MouseMove):
            clone = QMouseEvent(etype, event.position(),
                                event.globalPosition(), event.button(),
                                event.buttons(), event.modifiers())
            QApplication.sendEvent(target, clone)
        elif etype == QEvent.Type.Wheel:
            clone = QWheelEvent(event.position(),
                                event.globalPosition(),
                                event.pixelDelta(), event.angleDelta(),
                                event.buttons(), event.modifiers(),
                                event.phase(), event.inverted(),
                                event.source())
            QApplication.sendEvent(target, clone)
        else:
            return False
        event.accept()
        return True

    def mousePressEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):  # noqa: N802
        if not self._relay(event):
            super().wheelEvent(event)


# ---------------------------------------------------------------------------
# The detail card (E22): what the chosen star is, and a way to
# start a conversation with it.
# ---------------------------------------------------------------------------

class SkillDetailCard(QWidget):
    """Shows one skill: friendly title, group, trimmed
    description and the raw tool name — plus an "Ask with this"
    button. Starts (and clear() returns it to) a quiet hint
    state; the window fills it from skill_chosen."""

    ask_requested = Signal(str, str)     # name, friendly

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        self._name = ""
        self._friendly = ""
        self._hint = QLabel("Pick a star to see what it does")
        self._hint.setObjectName("muted")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)
        self._title = QLabel("")
        self._title.setObjectName("cardTitle")
        self._title.setWordWrap(True)
        layout.addWidget(self._title)
        self._group = QLabel("")
        self._group.setObjectName("muted")
        layout.addWidget(self._group)
        self._description = QLabel("")
        self._description.setWordWrap(True)
        layout.addWidget(self._description)
        self._raw_name = QLabel("")
        self._raw_name.setObjectName("muted")
        # B-R1-7: the raw skill name renders in the mono stack
        # (Share Tech Mono first); the Monospace style hint stays
        # as the fallback when the bundle is absent.
        mono = QFont(first_font_family(MONO_FONT_STACK))
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPixelSize(11)
        self._raw_name.setFont(mono)
        layout.addWidget(self._raw_name)
        layout.addSpacing(6)
        self._ask_btn = QPushButton("Ask with this")
        self._ask_btn.setObjectName("primaryBtn")
        self._ask_btn.clicked.connect(self._emit_ask)
        layout.addWidget(self._ask_btn)
        layout.addStretch()
        self.clear()

    def _emit_ask(self):
        if self._name:
            self.ask_requested.emit(self._name, self._friendly)

    def set_skill(self, name: str, friendly: str, group: str,
                  description: str):
        self._name = name or ""
        self._friendly = friendly or name or ""
        self._hint.setVisible(False)
        self._title.setText(self._friendly)
        self._title.setVisible(True)
        self._group.setText(group or "")
        self._group.setVisible(bool(group))
        self._description.setText(description or "")
        self._description.setVisible(bool(description))
        self._raw_name.setText(self._name)
        self._raw_name.setVisible(bool(self._name))
        self._ask_btn.setVisible(True)

    def clear(self):
        self._name = ""
        self._friendly = ""
        self._hint.setVisible(True)
        for widget in (self._title, self._group, self._description,
                       self._raw_name, self._ask_btn):
            widget.setVisible(False)

    @property
    def current_name(self) -> str:
        return self._name

    @property
    def current_friendly(self) -> str:
        return self._friendly
