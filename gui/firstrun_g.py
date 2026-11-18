"""Phase G (Command Deck) — first-run kit: boot checks, stage
progress, coach-mark overlay.

Three pieces, each honest by construction:

* Boot checks (S3 ignition lines LINK / MIC / VOICE / MODE) read
  REAL state only — the stored brain choice, the configured key's
  presence (never its value), the enumerated microphones, the
  on-device voice readiness. A probe that cannot run reports
  FAULT with its reason; nothing here invents an OK, performs
  network I/O, downloads, or repairs anything.

* First-run stage progress (the guide's four required stages:
  interaction, brain, permissions, first real mission) is pure
  logic over a dict-like store: a stage is done, skipped, or
  open; resuming returns the first open stage. Skipping never
  blocks — it records the skip and moves on.

* The coach-mark overlay draws corner brackets around a real
  target widget and shows one card with Next / Back / Dismiss.
  Targets that do not resolve are skipped, never drawn around a
  guess. The overlay masks input to its card + brackets only, so
  the highlighted target stays clickable.

Token-only: this file carries no colour literal (the Command
Deck colour guard scans gui/*.py); every colour comes from
gui/style.py tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .style import (
    ACCENT,
    AMBER,
    BORDER,
    DANGER,
    OK,
    RADIUS_BTN,
    RADIUS_CARD,
    SURFACE_1,
    SURFACE_2,
    TEXT,
    TEXT_MUTED,
)

# ---------------------------------------------------------------- boot checks

S3_CHECK_ORDER = ("LINK", "MIC", "VOICE", "MODE")


@dataclass(frozen=True)
class BootCheck:
    """One S3 ignition line: name, pass/fail, honest detail."""

    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        """The rendered line: 'LINK · Ready' or 'LINK · FAULT: …'."""
        if self.ok:
            return f"{self.name} · Ready"
        return f"{self.name} · FAULT: {self.detail}"


def _brain_choice(settings_get=None, config_obj=None) -> str | None:
    """The stored brain choice ('local' | 'cloud' | 'both'), or
    None when nothing is stored. Never guesses from hardware."""
    try:
        if settings_get is not None:
            value = settings_get("setup/brain_choice")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    except Exception:  # noqa: BLE001 — unreadable reads as unset
        pass
    try:
        if config_obj is not None:
            engine = str(getattr(config_obj, "brain_engine", "") or "")
            engine = engine.strip().lower()
            if engine in ("local",):
                return "local"
            if engine in ("gemini", "groq", "cloud"):
                return "cloud"
    except Exception:  # noqa: BLE001 — unreadable reads as unset
        pass
    return None


def _key_present(config_obj=None) -> bool:
    """Whether a cloud key is configured — presence only, never
    the value, never a validity claim (validity needs the network
    and this probe does none)."""
    try:
        if config_obj is not None:
            key = getattr(config_obj, "api_key", "") or ""
            return bool(str(key).strip())
    except Exception:  # noqa: BLE001 — unreadable reads as absent
        pass
    return False


def _mic_inputs(audio_inputs=None) -> list:
    """Enumerated microphone inputs, or [] when enumeration is
    unavailable. The caller may inject a list for tests."""
    if audio_inputs is not None:
        try:
            return list(audio_inputs)
        except Exception:  # noqa: BLE001 — undListable reads as none
            return []
    try:
        from voice_assistant.core.gui_facade import audio_manager

        devs = audio_manager.get_devices() or {}
        return list(devs.get("inputs", []) or [])
    except Exception:  # noqa: BLE001 — no enumeration, no claim
        return []


def _voice_ready(config_obj=None) -> bool:
    """Whether a voice is ready: the on-device voice reports
    ready, or a cloud voice name is configured. Neither is a
    playback proof — playback is the first Connect's job."""
    try:
        from voice_assistant.core.gui_facade import local_assets

        if local_assets.voice_ready():
            return True
    except Exception:  # noqa: BLE001 — fall through to the name
        pass
    try:
        if config_obj is not None:
            name = getattr(config_obj, "voice_name", "") or ""
            return bool(str(name).strip())
    except Exception:  # noqa: BLE001 — unreadable reads as unready
        pass
    return False


def run_boot_checks(settings_get=None, config_obj=None,
                    audio_inputs=None) -> list[BootCheck]:
    """The four S3 lines in guide order. Every line is real state
    or an honest FAULT; this function never raises, never waits
    on the network, and never starts a download."""
    choice = _brain_choice(settings_get, config_obj)
    if choice in ("local", "cloud", "both"):
        mode = BootCheck("MODE", True,
                         {"local": "On this PC",
                          "cloud": "Cloud brain",
                          "both": "Cloud first, on-PC backup"}[choice])
    else:
        mode = BootCheck("MODE", False, "Brain choice not set yet")
    if choice == "local":
        link = BootCheck("LINK", True,
                         "On-PC brain — no cloud link needed")
    elif choice in ("cloud", "both"):
        link = (BootCheck("LINK", True, "Cloud key present")
                if _key_present(config_obj) else
                BootCheck("LINK", False, "No cloud key yet"))
    else:
        link = BootCheck("LINK", False, "Brain choice not set yet")
    inputs = _mic_inputs(audio_inputs)
    if inputs:
        mic = BootCheck("MIC", True, f"{len(inputs)} input(s) found")
    else:
        mic = BootCheck("MIC", False, "No microphone detected")
    if _voice_ready(config_obj):
        voice = BootCheck("VOICE", True, "Voice ready")
    else:
        voice = BootCheck("VOICE", False, "No voice ready yet")
    ordered = {"LINK": link, "MIC": mic, "VOICE": voice, "MODE": mode}
    return [ordered[name] for name in S3_CHECK_ORDER]


# ------------------------------------------------------------ stage progress

FIRST_RUN_STAGES: tuple[str, ...] = (
    "interaction",
    "brain",
    "permissions",
    "first_mission",
)

STAGE_TITLES: dict[str, str] = {
    "interaction": "1 · Interaction",
    "brain": "2 · Brain",
    "permissions": "3 · Permissions",
    "first_mission": "4 · First mission",
}


@dataclass
class FirstRunProgress:
    """Skippable, resumable stage progress over a dict-like
    store (QSettings in production, a plain dict in tests).

    States per stage: open (default), done, skipped. Resume
    returns the first stage that is neither done nor skipped;
    None means the flow is complete. Skipping records the skip
    — it never deletes progress and never blocks the flow."""

    store: dict = field(default_factory=dict)
    stages: tuple[str, ...] = FIRST_RUN_STAGES

    def _state(self, stage: str) -> str:
        try:
            value = self.store.get(f"firstrun/stage/{stage}", "open")
        except Exception:  # noqa: BLE001 — unreadable reads as open
            return "open"
        return value if value in ("done", "skipped") else "open"

    def _set(self, stage: str, value: str) -> None:
        try:
            self.store[f"firstrun/stage/{stage}"] = value
        except Exception:  # noqa: BLE001 — a broken store keeps memory only
            pass

    def mark_done(self, stage: str) -> None:
        if stage in self.stages:
            self._set(stage, "done")

    def skip_stage(self, stage: str) -> None:
        if stage in self.stages:
            self._set(stage, "skipped")

    def state(self, stage: str) -> str:
        return self._state(stage)

    def resume_stage(self) -> str | None:
        for stage in self.stages:
            if self._state(stage) == "open":
                return stage
        return None

    def is_complete(self) -> bool:
        return self.resume_stage() is None

    def summary(self) -> dict[str, str]:
        return {stage: self._state(stage) for stage in self.stages}


# ------------------------------------------------------------ coach overlay

try:
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QRegion
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
    _QT_AVAILABLE = True
except Exception:  # pragma: no cover - Qt absent
    _QT_AVAILABLE = False


def resolve_coach_target(window, target_id: str):
    """Map a coach-mark target id to a real widget on the window,
    or None when it does not resolve. Never guesses a widget."""
    if window is None:
        return None
    if target_id == "orb":
        return getattr(window, "visualizer", None)
    if target_id == "chat-composer":
        widget = getattr(window, "input_cmd", None)
        if widget is not None:
            return widget
    if target_id == "voice-chip":
        return getattr(window, "voice_chip", None)
    try:
        found = window.findChildren(QWidget)
        for widget in found:
            if widget.objectName() == target_id:
                return widget
    except Exception:  # noqa: BLE001 — unsearchable reads as missing
        pass
    return None


# ------------------------------------------------------- coach sequence
# The guide's Phase G coach marks: one short pointer each for the
# orb, the composer, and the voice chip, shown ONCE after a full
# first run (the onboarding done page names them, so the overlay
# after Finish is never a surprise). The driver is pure sequence
# logic over injected callables, so it is testable without a
# MainWindow: the window supplies targets, the settings pair
# supplies the once-only flag, the overlay class supplies the
# widget. Unresolvable targets are skipped, never drawn around
# a guess. The flag is set on every terminal path (finish,
# dismiss, no resolvable targets, overlay error) because there
# is no later trigger that would retry a failed run — leaving
# the flag unset would be dead state, not a retry.

COACH_SHOWN_KEY = "coach_marks/g_shown"

COACH_MARK_STEPS = (
    ("orb", "Meet your assistant",
     "The orb shows listening, thinking, and speaking. "
     "Its rings move with the turn."),
    ("chat-composer", "Type or talk",
     "Type here, or hold Space anywhere in the window to talk. "
     "Anything that sends, deletes, or buys asks you first."),
    ("voice-chip", "Your voice",
     "Pick the voice and language here. "
     "The choice sticks between launches."),
)


def run_coach_sequence(window, settings_get, settings_set,
                       overlay_cls=None):
    """Run the coach-mark sequence once.

    Always returns a dict: {"outcome": str, "callbacks": dict
    or None}. "outcome" is "already-shown", "no-targets", or
    "error" for terminal paths (callbacks None), and "running"
    for a live sequence — the callbacks then drive it ("next",
    "back", "dismiss"), and the dict's "outcome" updates to
    "finished" or "dismissed" when the sequence ends. The
    shown flag is set on every terminal path except
    "already-shown". Never raises: any failure inside the
    sequence degrades to "error" with the flag set, so a
    broken overlay can never break startup.
    """
    if overlay_cls is None:
        overlay_cls = CoachMarkOverlay
    state = {"outcome": "running", "callbacks": None}
    try:
        if settings_get(COACH_SHOWN_KEY, False):
            state["outcome"] = "already-shown"
            return state
    except Exception:  # noqa: BLE001 — unreadable store reads as fresh
        pass
    steps = []
    for target_id, title, body in COACH_MARK_STEPS:
        try:
            target = resolve_coach_target(window, target_id)
        except Exception:  # noqa: BLE001 — unresolvable reads as missing
            target = None
        if target is not None:
            steps.append((target, title, body))
    if not steps:
        try:
            settings_set(COACH_SHOWN_KEY, True)
        except Exception:  # noqa: BLE001 — flag write is best-effort
            pass
        state["outcome"] = "no-targets"
        return state

    live = {"index": 0, "current": None}

    def _finish(outcome):
        if live["current"] is not None:
            try:
                live["current"].close()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass
            live["current"] = None
        try:
            settings_set(COACH_SHOWN_KEY, True)
        except Exception:  # noqa: BLE001 — flag write is best-effort
            pass
        state["outcome"] = outcome

    def _close_current():
        if live["current"] is not None:
            try:
                live["current"].close()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass
            live["current"] = None

    def _show(index):
        _close_current()
        target, title, body = steps[index]
        overlay = overlay_cls(
            target, title, body,
            on_next=_next, on_back=_back, on_dismiss=_dismiss,
            parent=window)
        if index == 0:
            try:
                overlay.set_back_visible(False)
            except Exception:  # noqa: BLE001 — optional affordance
                pass
        if index == len(steps) - 1:
            try:
                overlay.set_next_label("Done")
            except Exception:  # noqa: BLE001 — optional affordance
                pass
        live["current"] = overlay
        return overlay

    def _next():
        if live["index"] >= len(steps) - 1:
            _finish("finished")
            return "finished"
        live["index"] += 1
        _show(live["index"])
        return "next"

    def _back():
        if live["index"] > 0:
            live["index"] -= 1
            _show(live["index"])
        return "back"

    def _dismiss():
        _finish("dismissed")
        return "dismissed"

    try:
        _show(0)
    except Exception:  # noqa: BLE001 — overlay failure never breaks startup
        try:
            settings_set(COACH_SHOWN_KEY, True)
        except Exception:  # noqa: BLE001 — flag write is best-effort
            pass
        state["outcome"] = "error"
        return state
    # The sequence is driven by the overlay's own buttons from
    # here; the caller does not block on it.
    state["callbacks"] = {"next": _next, "back": _back,
                          "dismiss": _dismiss}
    return state


if _QT_AVAILABLE:
    class CoachMarkOverlay(QWidget):
        """Corner brackets around a target + one card (Next / Back
        / Dismiss). The input mask covers only the card and the
        brackets, so the highlighted target stays clickable; the
        rest of the screen passes input through.

        Bracket geometry is the target's global rect expanded by
        a margin, drawn as four L-shaped corners in the accent
        token. The card sits below the target when there is room,
        else above it."""

        BRACKET_LEN = 22
        BRACKET_MARGIN = 10
        BRACKET_WIDTH = 2

        def __init__(self, target, title: str, body: str,
                     on_next=None, on_back=None, on_dismiss=None,
                     parent=None):
            super().__init__(parent)
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self._target = target
            self._on_next = on_next
            self._on_back = on_back
            self._on_dismiss = on_dismiss
            self._target_rect = self._global_rect(target)
            self._build_card(title, body)
            self._place()
            self._apply_mask()

        @staticmethod
        def _global_rect(widget) -> QRect:
            try:
                top_left = widget.mapToGlobal(widget.rect().topLeft())
                return QRect(top_left, widget.rect().size())
            except Exception:  # noqa: BLE001 — unmappable target
                return QRect(0, 0, 0, 0)

        def _build_card(self, title: str, body: str) -> None:
            self._card = QWidget(self)
            self._card.setObjectName("coachCard")
            # Opaque dialog surface, not GLASS: the overlay spans
            # the whole screen, so a near-transparent card would
            # sit over whatever window happens to be behind it
            # and the light title text would wash out. Buttons
            # get the HudDialog treatment (same tokens) instead
            # of the OS default style.
            self._card.setStyleSheet(
                f"QWidget#coachCard {{ background: {SURFACE_1};"
                f" border: 1px solid {BORDER};"
                f" border-radius: {RADIUS_CARD}px; }}"
                f"QWidget#coachCard QPushButton {{"
                f" background: {SURFACE_2};"
                f" border: 1px solid {BORDER}; border-radius: 2px;"
                f" padding: 8px 16px; color: {TEXT}; }}"
                f"QWidget#coachCard QPushButton#primaryBtn {{"
                f" border: 1px solid {ACCENT}; }}")
            layout = QVBoxLayout(self._card)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(6)
            head = QLabel(title)
            head.setStyleSheet(
                f"color: {TEXT}; font-weight: 600;"
                " background: transparent; border: none;")
            head.setWordWrap(True)
            layout.addWidget(head)
            text = QLabel(body)
            text.setStyleSheet(
                f"color: {TEXT_MUTED}; background: transparent;"
                " border: none;")
            text.setWordWrap(True)
            layout.addWidget(text)
            row = QHBoxLayout()
            self._btn_back = QPushButton("Back")
            self._btn_back.setObjectName("chipBtn")
            self._btn_back.clicked.connect(self._back)
            self._btn_next = QPushButton("Next")
            self._btn_next.setObjectName("primaryBtn")
            self._btn_next.clicked.connect(self._next)
            self._btn_dismiss = QPushButton("Dismiss")
            self._btn_dismiss.setObjectName("chipBtn")
            self._btn_dismiss.clicked.connect(self._dismiss)
            row.addWidget(self._btn_back)
            row.addStretch()
            row.addWidget(self._btn_dismiss)
            row.addWidget(self._btn_next)
            layout.addLayout(row)
            self._card.adjustSize()

        def _place(self) -> None:
            try:
                screen = self.screen()
                area = (screen.availableGeometry()
                        if screen is not None else QRect(0, 0, 1280, 800))
            except Exception:  # noqa: BLE001 — fallback geometry
                area = QRect(0, 0, 1280, 800)
            self.setGeometry(area)
            rect = self._target_rect
            card_w = min(320, max(220, self._card.width()))
            card_h = self._card.height()
            below_y = rect.bottom() + self.BRACKET_MARGIN + 8
            if below_y + card_h <= area.bottom():
                card_y = below_y
            else:
                card_y = max(area.top(), rect.top() - card_h - 8)
            card_x = min(max(area.left(), rect.left()),
                         max(area.left(), area.right() - card_w))
            self._card.setGeometry(card_x, card_y, card_w, card_h)

        def _bracket_rects(self) -> list[QRect]:
            rect = self._target_rect
            m = self.BRACKET_MARGIN
            length = self.BRACKET_LEN
            outer = rect.adjusted(-m, -m, m, m)
            return [
                QRect(outer.left(), outer.top(), length, self.BRACKET_WIDTH),
                QRect(outer.left(), outer.top(), self.BRACKET_WIDTH, length),
                QRect(outer.right() - length + 1, outer.top(),
                      length, self.BRACKET_WIDTH),
                QRect(outer.right() - self.BRACKET_WIDTH + 1, outer.top(),
                      self.BRACKET_WIDTH, length),
                QRect(outer.left(), outer.bottom() - self.BRACKET_WIDTH + 1,
                      length, self.BRACKET_WIDTH),
                QRect(outer.left(), outer.bottom() - length + 1,
                      self.BRACKET_WIDTH, length),
                QRect(outer.right() - length + 1,
                      outer.bottom() - self.BRACKET_WIDTH + 1,
                      length, self.BRACKET_WIDTH),
                QRect(outer.right() - self.BRACKET_WIDTH + 1,
                      outer.bottom() - length + 1,
                      self.BRACKET_WIDTH, length),
            ]

        def _apply_mask(self) -> None:
            try:
                region = QRegion(self._card.geometry())
                for part in self._bracket_rects():
                    local = QRect(self.mapFromGlobal(part.topLeft()),
                                  part.size())
                    region = region.united(QRegion(local))
                self.setMask(region)
            except Exception:  # noqa: BLE001 — unmasked overlay still shows
                pass

        def paintEvent(self, event):  # noqa: N802 (Qt naming)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(ACCENT))
            pen.setWidth(self.BRACKET_WIDTH)
            painter.setPen(pen)
            for part in self._bracket_rects():
                local = QRect(self.mapFromGlobal(part.topLeft()),
                              part.size())
                painter.fillRect(local, QColor(ACCENT))
            painter.end()
            super().paintEvent(event)

        def _next(self) -> None:
            if self._on_next is not None:
                self._on_next()

        def _back(self) -> None:
            if self._on_back is not None:
                self._on_back()

        def _dismiss(self) -> None:
            if self._on_dismiss is not None:
                self._on_dismiss()

        def target_rect(self) -> QRect:
            return QRect(self._target_rect)

        def set_next_label(self, text: str) -> None:
            self._btn_next.setText(text)

        def set_back_visible(self, visible: bool) -> None:
            self._btn_back.setVisible(bool(visible))
else:  # pragma: no cover - Qt absent
    class CoachMarkOverlay:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("CoachMarkOverlay needs Qt")
