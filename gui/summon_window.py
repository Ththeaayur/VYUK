"""Wave 9 (v5.64) — floating summon window (PJ-27 choreography).

A summon surface is NOT an acting surface. This module lists tools,
recent items and screens and shows them near the cursor; it never
executes anything itself and it is never wired to an executor here.
Any dispatch that a caller builds on top of :func:`dispatch_allowed`
must still pass the failsafe gate (``core/failsafe.py`` — a tripped
watchdog refuses control until a human re-arms it) before any tool
runs. The gate lives in the caller, not in this window: this window
only reports whether dispatch would be allowed and emits a
``dispatch_requested`` signal for the caller to gate and execute.

PJ-27 choreography (pure, Qt-free, fully unit-tested):

* the window appears on the cursor's monitor, not the primary one;
* its rectangle is clamped to that monitor's *work area* (the screen
  minus taskbar/dock), never to the full screen rectangle;
* geometry is re-validated on EVERY summon — a monitor unplugged,
  a resolution change, or a moved taskbar between summons must not
  leave the window stranded off-screen. ``summon_geometry`` takes
  the current screen list as an argument for exactly this reason;
  nothing is cached across summons.

Screens are plain dicts (``{"geometry": (x, y, w, h),
"work_area": (x, y, w, h)}``) so the choreography is testable
without Qt; the Qt window class converts real ``QScreen`` objects
into that shape at summon time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MODULE_VERSION = "5.64.0"

# Summon window size. Small enough to fit a laptop work area; the
# clamp below, not this size, is what guarantees on-screen placement.
SUMMON_WIDTH = 360
SUMMON_HEIGHT = 420
# Gap between the cursor and the window edge, in pixels.
CURSOR_GAP = 16


# ---------------------------------------------------------------------------
# Model (Qt-free)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SummonItem:
    """One row in the summon list.

    ``kind`` is one of ``"tool"``, ``"recent"`` or ``"screen"``.
    ``action_id`` is an opaque identifier for the caller; this module
    never resolves it to a tool call.
    """

    kind: str
    title: str
    subtitle: str = ""
    action_id: str = ""


@dataclass
class SummonModel:
    """Tools + recent items + screens, most-recent first for recents."""

    tools: list[SummonItem] = field(default_factory=list)
    recents: list[SummonItem] = field(default_factory=list)
    screens: list[SummonItem] = field(default_factory=list)

    def all_items(self) -> list[SummonItem]:
        return list(self.tools) + list(self.recents) + list(self.screens)

    def filter(self, query: str) -> list[SummonItem]:
        """Case-insensitive substring filter over title and subtitle."""
        q = query.strip().lower()
        if not q:
            return self.all_items()
        return [i for i in self.all_items()
                if q in i.title.lower() or q in i.subtitle.lower()]

    def push_recent(self, item: SummonItem) -> None:
        """Record a recent item at the front, de-duplicated by title."""
        self.recents = [r for r in self.recents if r.title != item.title]
        self.recents.insert(0, item)


# ---------------------------------------------------------------------------
# PJ-27 geometry (Qt-free)
# ---------------------------------------------------------------------------

Rect = tuple[int, int, int, int]  # (x, y, width, height)


def _contains(rect: Rect, px: int, py: int) -> bool:
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


def choose_monitor_for_cursor(cursor: tuple[int, int],
                              screens: list[dict]) -> dict | None:
    """Return the screen dict containing the cursor, else the first.

    Returns ``None`` when ``screens`` is empty (no monitor known —
    the caller must then refuse to summon rather than guess).
    """
    if not screens:
        return None
    px, py = cursor
    for screen in screens:
        if _contains(screen["geometry"], px, py):
            return screen
    return screens[0]


def clamp_rect_to_work_area(rect: Rect, work_area: Rect) -> Rect:
    """Clamp a rectangle fully inside a work area.

    If the rectangle is larger than the work area in a dimension, it
    is pinned to the work-area origin in that dimension (it cannot
    fit, but its top-left stays reachable) rather than pushed
    negative.
    """
    x, y, w, h = rect
    wx, wy, ww, wh = work_area
    if w >= ww:
        x = wx
    else:
        x = min(max(x, wx), wx + ww - w)
    if h >= wh:
        y = wy
    else:
        y = min(max(y, wy), wy + wh - h)
    return (x, y, w, h)


def summon_geometry(cursor: tuple[int, int],
                    screens: list[dict],
                    width: int = SUMMON_WIDTH,
                    height: int = SUMMON_HEIGHT) -> dict:
    """Compute where the summon window goes for one summon.

    Re-validated per summon from the *current* screen list. The
    window prefers below-right of the cursor; the clamp then pulls
    it inside the cursor monitor's work area. Returns a dict with
    ``rect`` and the chosen screen's ``work_area``; ``rect`` is
    ``None`` when no screen is known.
    """
    screen = choose_monitor_for_cursor(cursor, screens)
    if screen is None:
        return {"rect": None, "work_area": None,
                "reason": "no-screens-known"}
    work_area = screen["work_area"]
    cx, cy = cursor
    rect = (cx + CURSOR_GAP, cy + CURSOR_GAP, width, height)
    rect = clamp_rect_to_work_area(rect, work_area)
    return {"rect": rect, "work_area": work_area, "reason": "ok"}


def screens_from_qt(app_screens: list) -> list[dict]:
    """Convert QScreen objects to the plain-dict shape above."""
    out = []
    for screen in app_screens:
        g = screen.geometry()
        w = screen.availableGeometry()
        out.append({
            "geometry": (g.x(), g.y(), g.width(), g.height()),
            "work_area": (w.x(), w.y(), w.width(), w.height()),
        })
    return out


# ---------------------------------------------------------------------------
# Failsafe gate (the acting-surface boundary)
# ---------------------------------------------------------------------------

def dispatch_allowed(failsafe_tripped: bool) -> dict:
    """Whether a dispatch request may proceed.

    ``failsafe_tripped=True`` refuses outright. This module never
    calls an executor; a caller must check this (or, preferably,
    ``core.failsafe.tripped()`` live) and only then execute.
    """
    if failsafe_tripped:
        return {"allowed": False,
                "reason": "failsafe-tripped: control refused until "
                          "a human re-arms it"}
    return {"allowed": True, "reason": "ok"}


# ---------------------------------------------------------------------------
# Qt window (only defined when Qt is importable)
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtWidgets import (QLineEdit, QListWidget, QListWidgetItem,
                                   QVBoxLayout, QWidget)
    _QT_AVAILABLE = True
except Exception:  # pragma: no cover - Qt absent
    _QT_AVAILABLE = False


if _QT_AVAILABLE:
    class SummonWindow(QWidget):
        """Frameless floating summon window.

        ``dispatch_requested`` carries a :class:`SummonItem`; the
        owner must gate it through :func:`dispatch_allowed` /
        ``core.failsafe`` before executing anything. ``rejected``
        fires when a dispatch was asked for while tripped.
        """

        dispatch_requested = Signal(object)
        rejected = Signal(object, str)

        def __init__(self, model: SummonModel | None = None,
                     parent=None):
            super().__init__(parent)
            self.model = model or SummonModel()
            self.failsafe_tripped = False
            self.setWindowFlags(Qt.WindowType.Tool
                                | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint)
            self.setFixedSize(SUMMON_WIDTH, SUMMON_HEIGHT)
            layout = QVBoxLayout(self)
            self.search = QLineEdit(self)
            self.search.setPlaceholderText("Search tools, recents, screens…")
            self.search.textChanged.connect(self._refresh)
            self.list_widget = QListWidget(self)
            self.list_widget.itemActivated.connect(self._on_activated)
            layout.addWidget(self.search)
            layout.addWidget(self.list_widget)
            self._refresh("")

        def _refresh(self, query: str) -> None:
            self.list_widget.clear()
            for item in self.model.filter(query):
                row = QListWidgetItem(
                    f"{item.title} — {item.subtitle}" if item.subtitle
                    else item.title)
                row.setData(Qt.ItemDataRole.UserRole, item)
                self.list_widget.addItem(row)

        def _on_activated(self, row) -> None:
            item = row.data(Qt.ItemDataRole.UserRole)
            self.request_dispatch(item)

        def request_dispatch(self, item: SummonItem) -> bool:
            """Ask to dispatch ``item``; returns False when refused.

            Refusal happens here, before any signal the owner could
            act on, when the failsafe is tripped. When allowed, the
            owner still gates execution itself — this window never
            executes.
            """
            gate = dispatch_allowed(self.failsafe_tripped)
            if not gate["allowed"]:
                self.rejected.emit(item, gate["reason"])
                return False
            self.dispatch_requested.emit(item)
            return True

        def summon_at(self, cursor: tuple[int, int],
                      screens: list[dict]) -> dict:
            """Re-validate geometry from current screens and show.

            Returns the :func:`summon_geometry` result; when no
            screen is known the window is NOT shown.
            """
            result = summon_geometry(cursor, screens)
            if result["rect"] is None:
                return result
            x, y, _w, _h = result["rect"]
            self.move(x, y)
            self.show()
            self.raise_()
            return result
else:  # pragma: no cover - Qt absent
    class SummonWindow:  # type: ignore[no-redef]
        """Placeholder raised on use when Qt is unavailable."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError("PySide6 is required for SummonWindow")
