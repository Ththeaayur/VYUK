"""Onboarding widgets (split from gui/onboarding.py, GUI split #13 batch 1, move only).

Small widgets used by the first-run pages: the rotating feature-card
data, the selectable choice card, the segmented mic meter, and the
QSettings-backed store adapter.
"""

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set_noted
from .style import (
    ACCENT,
    AMBER,
    BORDER,
    DANGER,
    GLASS,
    GLASS_HOVER,
    OK,
    PREMIUM_STYLE,
    PRIMARY_WASH,
    RADIUS_BTN,
    RADIUS_CARD,
    TEXT,
    TEXT_MUTED,
    TYPE_BODY,
    TYPE_CAPTION,
    TYPE_MICRO,
    load_bundled_fonts,
)


def _feature_cards() -> tuple:
    """The rotating feature cards for the Setting up page (v4.2).

    The tool count is read from the live registry, guarded — the
    old copy hard-coded "122 built-in skills" and silently went
    stale as the registry grew. Any failure degrades the copy to
    "hundreds of"; onboarding must never break on a blurb.
    """
    try:
        from voice_assistant.core.gui_facade import tools  # Phase 4 S1b INTENTIONAL: registry split to funnel

        count = str(len(tools.get_declarations()))
    except Exception:  # noqa: BLE001
        count = "hundreds of"
    return (
        ("Hands on your whole PC",
         "VYUK opens apps, clicks, types, and manages files for you "
         f"— {count} built-in skills."),
        ("She can see your screen",
         "Ask what's on screen; VYUK reads it and acts on what she finds."),
        ("She remembers",
         "Facts you share are kept in a memory web you can explore and edit."),
        ("Private by design",
         "With the built-in brain, your voice and data never leave this PC."),
        ("Routines & briefings",
         "Wake to a spoken briefing; schedule routines that run themselves."),
        ("Summon her anywhere",
         "Ctrl+Shift+Space brings VYUK forward from any app."),
    )


class _ChoiceCard(QFrame):
    """One selectable brain-choice card (v5.23) — a QFrame that
    behaves like a large radio button: a click (or Space/Enter
    while focused) emits `clicked`; the dialog owns which choice
    is selected and calls set_selected(). States: resting (faint
    border), hover (brighter border), selected (accent border +
    tinted fill). The border is 2 px in EVERY state, so selecting
    a card never shifts the layout by a pixel."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected = False
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._restyle()

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, value: bool):
        self._selected = bool(value)
        self._restyle()

    def _restyle(self):
        if self._selected:
            bg, border = PRIMARY_WASH, ACCENT
        elif self._hovered:
            bg, border = GLASS_HOVER, BORDER
        else:
            bg, border = GLASS, BORDER
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 2px solid {border};"
            f" border-radius: {RADIUS_CARD}px; }}")

    def enterEvent(self, event):  # noqa: N802 (Qt naming)
        self._hovered = True
        self._restyle()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt naming)
        self._hovered = False
        self._restyle()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 (Qt naming)
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                           Qt.Key.Key_Enter):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _MicSegments(QWidget):
    """Segmented mic meter: twelve segments that light with the
    live level. Dark segments are unlit, never fake movement —
    silence reads as silence. The caption names the meter
    (field-check #44): bare bars read as a dead placeholder."""

    COUNT = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        caption = QLabel("Speak — watch the bars move")
        caption.setObjectName("muted")
        caption.setWordWrap(True)
        caption.setAccessibleName("Microphone level caption")
        layout.addWidget(caption)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self._segments: list[QFrame] = []
        for _ in range(self.COUNT):
            seg = QFrame()
            seg.setFixedHeight(14)
            seg.setStyleSheet(
                f"QFrame {{ background: {GLASS};"
                f" border-radius: {RADIUS_BTN}px; }}")
            row.addWidget(seg, stretch=1)
            self._segments.append(seg)
        layout.addLayout(row)
        self._level = 0.0

    def set_level(self, level: float) -> None:
        try:
            level = max(0.0, min(1.0, float(level)))
        except Exception:  # noqa: BLE001 — unparseable reads as zero
            level = 0.0
        self._level = level
        lit = int(round(level * self.COUNT))
        for index, seg in enumerate(self._segments):
            if index < lit:
                seg.setStyleSheet(
                    f"QFrame {{ background: {ACCENT};"
                    f" border-radius: {RADIUS_BTN}px; }}")
            else:
                seg.setStyleSheet(
                    f"QFrame {{ background: {GLASS};"
                    f" border-radius: {RADIUS_BTN}px; }}")

    def level(self) -> float:
        return self._level


class _QSettingsStore:
    """Dict-like adapter over QSettings for FirstRunProgress."""

    def __init__(self, settings: QSettings):
        self._settings = settings

    def get(self, key: str, default: str = "open") -> str:
        try:
            return qsettings_get(key, settings=self._settings)
        except Exception:  # noqa: BLE001 — unreadable reads as open
            return default

    def __setitem__(self, key: str, value: str) -> None:
        # Round-3 item 14: a first-run progress write that never
        # persists re-shows a finished step at the next launch. The
        # note never raises, so a broken store still keeps memory
        # only for this walk — and the failure is logged + recorded
        # instead of swallowed.
        qsettings_set_noted(key, value, settings=self._settings)
