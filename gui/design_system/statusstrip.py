"""StatusStrip — the one chip row (guide §8, §9.2, Phase D).

Replaces the header's loose pills (status + watch + capture) with
one strip. Ownership is split deliberately, so no two writers fight
over one chip:

* STATUS word — owned by ``_set_pill`` (window_services), which sets
  its text + role colour synchronously on every session status
  change and emits the same word on the state bus. The strip never
  restyles it. UI rethink Build 1: the status is one quiet word
  in the role colour, not a chip — the header holds one state
  word + Connect + overflow.
* WATCH chip — owned by ``_sync_watch_pill`` (screen_watch state).
* CAPTURE chip — owned by ``_render_capture_indicator``
  (capture_guard's live set).
* MODE / MIC / WAKE chips — owned by the state bus: this strip
  shows MIC OFF only while muted, and WAKE ON only while the wake
  word is on. No chip without data. UI rethink Build 1: there is
  NO mode chip — the plan's header is one state word + Connect +
  overflow, and the mode is carried by the state word itself
  ("Local core" is one of its values, via _set_pill's mapping of
  the LOCAL CORE word). AppState still carries `mode` (Phase A
  pin); the strip simply does not display it.

Colours come from ``gui/style.py`` tokens only (this package is
scanned with an EMPTY colour allowlist). The strip follows the
house consumer pattern (see MissionRail): it unsubscribes in
closeEvent, and tests reset the bus in tearDown.
"""

from PySide6.QtWidgets import QLabel, QWidget

from ..state_bus import AppState, get_state_bus
from ..style import (
    AMBER,
    LINE_SUBTLE,
    NEUTRAL,
    SURFACE_2,
)
from .flowlayout import FlowLayout


def _chip_style(colour):
    """One cut-corner chip: role text on the resting surface."""
    return (
        f"color: {colour}; padding: 6px 12px; border-radius: 2px;"
        f"background-color: {SURFACE_2};"
        f"border: 1px solid {LINE_SUBTLE}; font-size: 12px;"
    )


class StatusStrip(QWidget):
    """The header status cluster as one widget."""

    def __init__(self, parent=None, bus=None):
        super().__init__(parent)
        self._bus = bus if bus is not None else get_state_bus()
        self._tokens = []
        # D1 wrap fix: the chips ride a flow layout, not a hbox, so
        # the row wraps whole chips to a second line when — and
        # only when — it does not fit, instead of clipping chips
        # mid-glyph (908x500@120). Same spacing (8) and margins
        # (0) as the old hbox, so normal sizes render identically.
        # heightForWidth lets the parent header row grow taller
        # when chips wrap instead of squeezing the row.
        layout = FlowLayout(self, spacing=8)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        # STATUS word (owned by _set_pill; initial = Offline, the
        # honest startup state — the app starts disconnected). UI
        # rethink Build 1: the glyph beside it is hidden — the
        # gear/spinner plus the chip plus the state line said one
        # fact three times. The glyph object stays (updaters keep
        # syncing it), only its visibility goes.
        self.glyph_label = QLabel()
        self.glyph_label.setFixedSize(16, 16)
        try:
            from ..w9_wiring_theme import glyph_pixmap

            self.glyph_label.setPixmap(glyph_pixmap("offline", 16))
        except Exception:  # noqa: BLE001 — glyph is cosmetic
            pass
        self.glyph_label.setVisible(False)
        layout.addWidget(self.glyph_label)
        self.status_label = QLabel("Offline")
        self.status_label.setObjectName("statusPill")
        self.status_label.setStyleSheet(
            f"color: {NEUTRAL}; font-size: 12px;")
        layout.addWidget(self.status_label)

        # WATCH chip (owned by _sync_watch_pill; hidden until watching).
        self.watch_label = QLabel("")
        self.watch_label.setObjectName("statusPill")
        self.watch_label.setStyleSheet(_chip_style(AMBER))
        self.watch_label.setVisible(False)
        layout.addWidget(self.watch_label)

        # CAPTURE chip (owned by _render_capture_indicator; hidden
        # until a capture is live). Amber = attention, not fault:
        # a live capture is something to notice, not an error.
        self.capture_label = QLabel("")
        self.capture_label.setObjectName("statusPill")
        self.capture_label.setStyleSheet(_chip_style(AMBER))
        self.capture_label.setVisible(False)
        layout.addWidget(self.capture_label)

        # Bus-driven secondary chips (hidden until their fact exists).
        # UI rethink Build 1: no mode chip (see the module
        # docstring) — the state word carries "Local core".
        self.mic_label = QLabel("MIC OFF")
        self.mic_label.setStyleSheet(_chip_style(NEUTRAL))
        self.mic_label.setVisible(False)
        layout.addWidget(self.mic_label)
        self.wake_label = QLabel("WAKE ON")
        self.wake_label.setStyleSheet(_chip_style(NEUTRAL))
        self.wake_label.setVisible(False)
        layout.addWidget(self.wake_label)

        self._tokens.append(
            self._bus.subscribe(AppState, self._on_app_state))
        # D-01 (Phase 6): closeEvent never fires for a child widget
        # when its parent window is deleted — the subscription would
        # outlive the strip and _drain would call _on_app_state on a
        # deleted QLabel (the scifi_home RuntimeError, 6+ per run).
        # Unsubscribe on destroyed too: the closure captures only
        # the bus and the token list, never the strip, so it cannot
        # keep the strip alive. unsubscribe() is idempotent, so the
        # closeEvent path below and this one never double-remove.
        _bus = self._bus
        _tokens = self._tokens
        self.destroyed.connect(
            lambda: [_bus.unsubscribe(t) for t in list(_tokens)])

    def set_theme(self, resolved: dict) -> None:
        """Recolour the resting state word from a resolved theme.

        Look-fix 2 (2026-11-24): the state word's resting sheet is
        the house NEUTRAL (#7E9AA6, ~2.5:1 on Light), so the
        initial "Offline" word — and any state _set_pill leaves in
        the neutral role — was faint on Light. The word follows
        the theme text_muted; the chips keep their role colours
        (their dark pill backgrounds read on both themes). _set_pill
        still owns the word's text + role colour on every status
        change — this only re-resolves the resting sheet, and
        _set_pill substitutes the same muted value when the role
        colour is neutral. Under Cyan the resolved text_muted
        (#B0C1CD) is a touch brighter than the house NEUTRAL
        (#7E9AA6) — the word reads the same, marginally clearer.
        """
        try:
            muted = (resolved or {}).get("text_muted", NEUTRAL)
            self.status_label.setStyleSheet(
                f"color: {muted}; font-size: 12px;")
        except Exception:  # noqa: BLE001 — theme never breaks the strip
            pass

    def heightForWidth(self, width):  # noqa: N802 (Qt naming)
        """Wrapped height for the given width (flow layout)."""
        layout = self.layout()
        if layout is not None:
            return layout.heightForWidth(width)
        return super().heightForWidth(width)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        """Grow the row when chips wrap.

        A parent box layout sizes rows from sizeHint, which cannot
        express "taller when narrower" on its own — so the strip
        drives its own minimum height from its current width. When
        the header squeezes the strip, the wrapped height becomes
        the minimum, the header row grows, and the stage scrolls
        (D1 stage-scroll fix); when the window widens again, the
        minimum shrinks back to one row. The value is stable per
        width, so this converges in one extra pass, never loops.
        """
        super().resizeEvent(event)
        layout = self.layout()
        if layout is not None:
            wrapped = layout.heightForWidth(self.width())
            if wrapped > 0 and wrapped != self.minimumHeight():
                self.setMinimumHeight(wrapped)

    def _on_app_state(self, state):
        """Secondary chips only — STATUS stays _set_pill-owned.

        UI rethink Build 1: no mode chip — AppState.mode is read
        by nobody here (the state word carries "Local core")."""
        self.mic_label.setVisible(bool(state.mic_muted))
        self.wake_label.setVisible(bool(state.wake_on))

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        for token in self._tokens:
            self._bus.unsubscribe(token)
        super().closeEvent(event)
