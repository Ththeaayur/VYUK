"""
VYUK — Senses page (v5.20, SENSES NOSE): what she notices.

The NOSE wave's dashboard: the room and the machine, as VYUK
perceives them. Above the state cards, three sense rows
(microphone, camera, screen) show the standing permission state,
with a one-tap way to Settings → Permissions — the page shows
state, Settings flips it. Four live state cards (connection,
network quality, battery guardian, camera & mic privacy), a
Wi-Fi radar that lists the networks around the machine, a
room-brightness check through the webcam, and the environment
timeline — the sentinels' running log of what changed while the
user wasn't looking.

The page owns no sensing itself: every reading arrives through a
provider seam, ``SensesPage(providers={...})``, all keys optional —
a missing provider renders an honest placeholder, never an invented
reading:

    "state"()      -> {"ssid", "connection", "battery_pct",
                       "battery_plugged", "camera_user", "mic_user"}
                      (cheap; polled every 5 s while visible)
    "wifi_scan"()  -> core.senses.wifi_scan() shape:
                      {"available", "networks", "note"} (slow —
                      always run on a worker thread)
    "quality"()    -> core.senses.network_quality() shape:
                      {"reachable", "latency_ms", "jitter_ms",
                      "verdict"} (worker thread too)
    "events"()     -> core.senses_log.read_events() shape: a
                      newest-first list of {"ts", "kind", "message"}
    "brightness"(done_cb)
                   -> starts an async webcam capture and calls
                      done_cb(result | None) with a
                      core.senses.frame_brightness() shape
                      {"brightness", "label", "note"}, or None
                      when the machine has no camera.

State polling runs on a 5-second timer that only ticks while the
page is on screen, plus one immediate refresh the moment it appears
(the Pulse page's lifecycle, one floor down). Worker results cross
back through a small signals bridge — the main window's
BridgeSignals pattern, kept module-local so the page stays
self-contained. A scan that finishes after the page is hidden still
lands: the slots only touch labels and lists, which is harmless
off-screen.
"""

from __future__ import annotations

import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .style import TYPE_BODY, TYPE_CARD_TITLE
# Command Deck Phase E: the page paints from the token palette —
# the old mirrored indigo/teal constants are gone.
from .style import TEXT, TEXT_MUTED, ACCENT, ACCENT_2

_STATE_POLL_MS = 5000
_TIMELINE_CAP = 100

# UI Phase 4 (D-P4-14): the network-quality feel thresholds — the
# PAGE's thresholds, not the core module's (core/senses_quality.py
# verdicts on 50/120/250; the page's words use 100/300). The card
# states these numbers, so the verdict line is checkable.
_QUALITY_FAST_MS = 100
_QUALITY_SLOW_MS = 300
# The quality probe's targets, from core/senses_quality.py: five
# timed TCP connects alternating 1.1.1.1:53 / 8.8.8.8:53.
_QUALITY_TARGETS = "1.1.1.1 and 8.8.8.8 (DNS)"
_QUALITY_SAMPLES = 5


def _event_time(ts) -> str:
    """Best-effort 'HH:MM' from a timeline timestamp: an ISO string,
    a datetime, or epoch seconds. Anything else reads '--:--' —
    the row still shows its message."""
    if ts is None:
        return "--:--"
    if isinstance(ts, datetime):
        return ts.strftime("%H:%M")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts).strftime("%H:%M")
        except (OSError, OverflowError, ValueError):
            return "--:--"
    text = str(ts)
    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        pass
    if len(text) >= 16 and text[13] == ":":
        return text[11:16]
    return "--:--"


def _event_date_time(ts) -> str:
    """Best-effort '24 Nov 18:14' from a timeline timestamp (UI
    Phase 4, D-P4-15): a row must tell yesterday from an hour ago.
    Same parsing as _event_time; unparseable reads '-- --- --:--'."""
    parsed = None
    if isinstance(ts, datetime):
        parsed = ts
    elif isinstance(ts, (int, float)):
        try:
            parsed = datetime.fromtimestamp(ts)
        except (OSError, OverflowError, ValueError):
            parsed = None
    elif ts is not None:
        text = str(ts)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
    if parsed is None:
        return "-- --- --:--"
    return parsed.strftime("%d %b %H:%M")


class _SensesSignals(QObject):
    """Worker -> GUI bridge (the main window's BridgeSignals
    pattern): slow providers run on daemon threads and their
    results land in slots on the GUI thread through these."""

    wifi_ready = Signal(object)
    quality_ready = Signal(object)
    brightness_ready = Signal(object)


class _StateCard:
    """One mini state card: small caps title, a plain-words value,
    and a muted sub-line — PulsePage's card language, pocket size."""

    def __init__(self, title: str):
        self.frame = QFrame()
        self.frame.setObjectName("card")
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(3)
        # v5.57 (redo): the mockup's small-caps label — caps in a
        # small semibold face (Qt stylesheets have no letter-spacing).
        self.title_label = QLabel(title.upper())
        self.title_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; font-weight: 600;")
        layout.addWidget(self.title_label)
        self.value_label = QLabel("—")
        self.value_label.setWordWrap(True)
        self.value_label.setStyleSheet(
            f"color: {TEXT}; font-size: 18px; font-weight: 600;")
        layout.addWidget(self.value_label)
        self.sub_label = QLabel("")
        self.sub_label.setWordWrap(True)
        self.sub_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self.sub_label)
        layout.addStretch(1)
        self.layout = layout

    def set_text_colors(self, text: str, text_muted: str) -> None:
        """Recolour the card's labels from a theme (batch 5, Light).

        The value label carried the house TEXT (near-white) and
        the title / sub labels the house TEXT_MUTED, so on Light
        the values were invisible and the eyebrows faint. The
        frame itself is a template QFrame#card and already
        follows the theme.
        """
        self.title_label.setStyleSheet(
            f"color: {text_muted}; font-size: 11px; font-weight: 600;")
        self.value_label.setStyleSheet(
            f"color: {text}; font-size: 18px; font-weight: 600;")
        self.sub_label.setStyleSheet(
            f"color: {text_muted}; font-size: 11px;")


def _section_card(title: str):
    """A full-width card frame + its title label, Pulse spacing."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 14, 12, 12)
    layout.setSpacing(8)
    # v5.57 (redo): section titles in caps, the mockup language.
    title_label = QLabel(title.upper())
    title_label.setStyleSheet(
        f"color: {TEXT}; font-size: {TYPE_CARD_TITLE}px; font-weight: 600;")
    layout.addWidget(title_label)
    return frame, layout, title_label


class SensesPage(QWidget):
    """What VYUK notices about the room and the machine.
    See the module docstring for the provider seam and the
    honesty rules (missing provider -> placeholder, never a
    fabricated reading)."""

    def __init__(self, providers: dict | None = None, parent=None,
                 on_open_permissions=None):
        super().__init__(parent)
        self._providers = dict(providers or {})
        # Build 6 (§6.8): the one-tap way to Settings → Permissions,
        # supplied by the window. None means the page runs standalone
        # (tests, probes) — the buttons are simply not created, never
        # a dimmed button that can never be pressed.
        self._on_open_permissions = on_open_permissions
        self._highlight = ACCENT
        self._accent = ACCENT_2

        self.last_wifi_result: dict | None = None
        self.last_quality_result: dict | None = None
        self._scanning = False
        self._measuring = False
        self._brightness_busy = False

        self._signals = _SensesSignals()
        self._signals.wifi_ready.connect(self._on_wifi_ready)
        self._signals.quality_ready.connect(self._on_quality_ready)
        self._signals.brightness_ready.connect(self._on_brightness_ready)

        # ------------------------------------------------ scroll shell
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)
        content = QWidget()
        # The app sheet's one transparent-content rule (the same
        # hook the Settings scroll uses) — without it the scroll
        # content paints palette white over the dark shell.
        content.setObjectName("chatContent")
        scroll.setWidget(content)
        column = QVBoxLayout(content)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(14)

        # ------------------------------------------------ your senses
        # Build 6 (§6.8): one row per sense with its state word.
        # The page shows state; Settings flips it — the Permissions
        # button is the one-tap way to the exact card. There were
        # never switches here to move out: the page is a sensor
        # dashboard, and the state cards below stay exactly as
        # they are.
        senses_frame, senses_layout, _senses_title = _section_card(
            "Your senses")
        # Batch 5 (Light theme): the section title + the sense
        # names are re-coloured by set_palette, so both are kept
        # (the title used to be discarded, the names were locals).
        self._senses_title = _senses_title
        # UI Phase 4 (D-P4-11): the permission matrix — three
        # columns per sense (OS permission / app grant / active
        # capture), each with its own unknown state. One model,
        # two surfaces: the words come from the same Permissions
        # model the Settings card reads, so the two must agree.
        # The grid relayouts below 460 px page width (§6.1): the
        # three columns stack under each sense name.
        self._matrix_grid = QGridLayout()
        self._matrix_grid.setContentsMargins(0, 0, 0, 0)
        self._matrix_grid.setSpacing(8)
        senses_layout.addLayout(self._matrix_grid)
        self._matrix_headers: dict[str, QLabel] = {}
        for _col, (_key, _text) in enumerate((
                ("os", "OS permission"), ("app", "App grant"),
                ("capture", "Active capture")), start=1):
            _h = QLabel(_text)
            _h.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 11px; font-weight: 600;")
            self._matrix_headers[_key] = _h
            self._matrix_grid.addWidget(_h, 0, _col)
        self._matrix_words: dict[tuple[str, str], QLabel] = {}
        self._matrix_kickers: dict[tuple[str, str], QLabel] = {}
        self._sense_rows: dict[str, QLabel] = {}
        self._sense_name_labels: dict[str, QLabel] = {}
        self._sense_buttons: dict[str, QPushButton] = {}
        for pid, name in (("microphone", "Microphone"),
                          ("camera", "Camera"),
                          ("screen", "Screen")):
            name_label = QLabel(name)
            name_label.setStyleSheet(
                f"color: {TEXT}; font-size: {TYPE_CARD_TITLE}px; "
                "font-weight: 600;")
            self._sense_name_labels[pid] = name_label
            for col, _text in (("os", "OS"), ("app", "App"),
                               ("capture", "Capture")):
                word_label = QLabel("—")
                word_label.setStyleSheet(
                    f"color: {TEXT}; font-size: {TYPE_BODY}px;")
                self._matrix_words[(pid, col)] = word_label
                kicker = QLabel(_text)
                kicker.setStyleSheet(
                    f"color: {TEXT_MUTED}; font-size: 11px; "
                    "font-weight: 600;")
                kicker.setVisible(False)
                self._matrix_kickers[(pid, col)] = kicker
            # The old single word was the app grant — _sense_rows
            # keeps meaning the app word, for any reader of it.
            self._sense_rows[pid] = self._matrix_words[(pid, "app")]
            if on_open_permissions is not None:
                btn = QPushButton("Permissions")
                btn.clicked.connect(
                    lambda _checked=False: on_open_permissions())
                self._sense_buttons[pid] = btn
        self._relayout_matrix(self.width())
        column.addWidget(senses_frame)

        # ------------------------------------------------ state row
        self._state_row = QWidget()
        self._state_grid = QGridLayout(self._state_row)
        self._state_grid.setContentsMargins(0, 0, 0, 0)
        self._state_grid.setSpacing(14)
        column.addWidget(self._state_row)

        self._state_cards: dict[str, _StateCard] = {}
        self.state_labels: dict[str, QLabel] = {}
        self._state_subs: dict[str, QLabel] = {}
        for key, title in (
            ("connection", "Connection"),
            ("quality", "Network quality"),
            ("battery", "Battery guardian"),
            ("privacy", "Camera & mic"),
        ):
            card = _StateCard(title)
            self._state_cards[key] = card
            self.state_labels[key] = card.value_label
            self._state_subs[key] = card.sub_label
        # E-F210: "No reading" (102 px at the 18 px value face) fits
        # the ~115 px value label at the 908 px minimum window;
        # "Not measured" (130 px) wrapped to two lines there.
        self.state_labels["quality"].setText("No reading")

        self.measure_button = QPushButton("Measure")
        self.measure_button.clicked.connect(self._start_quality)
        self._state_cards["quality"].layout.insertWidget(
            3, self.measure_button)
        # UI Phase 4 (D-P4-14): the quality card names what it
        # measures against — the hosts and the sample count, the
        # code's constants, shown. The verdict line (below) states
        # the page's 100/300 ms thresholds.
        self._quality_targets_label = QLabel(
            f"Measured against {_QUALITY_TARGETS}, "
            f"{_QUALITY_SAMPLES} samples")
        self._quality_targets_label.setWordWrap(True)
        self._quality_targets_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        self._state_cards["quality"].layout.insertWidget(
            4, self._quality_targets_label)
        # Fix round (F-07, 2026-11-26): the thresholds line is
        # part of the card from the start — the verdict line used
        # to be built only inside _on_quality_result, so the
        # thresholds were invisible until after the first
        # measurement. Built from the same constants, so it cannot
        # drift from the verdicts.
        self._quality_thresholds_label = QLabel(
            f"Feels instant under {_QUALITY_FAST_MS} ms · "
            f"a little slow under {_QUALITY_SLOW_MS} ms · "
            f"slow above {_QUALITY_SLOW_MS} ms")
        self._quality_thresholds_label.setWordWrap(True)
        self._quality_thresholds_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        self._state_cards["quality"].layout.insertWidget(
            5, self._quality_thresholds_label)
        self._relayout_state(self.width())

        # ------------------------------------------------ wifi radar
        wifi_frame, wifi_layout, self._wifi_title = _section_card(
            "Wi-Fi radar")
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self._wifi_title)
        header.addStretch(1)
        self.wifi_status_label = QLabel("Not scanned yet")
        self.wifi_status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        header.addWidget(self.wifi_status_label)
        self.scan_button = QPushButton("Scan")
        self.scan_button.clicked.connect(self._start_scan)
        header.addWidget(self.scan_button)
        # The section helper already placed the title; rebuild the
        # header row in its place so title/status/button share a line.
        wifi_layout.removeWidget(self._wifi_title)
        wifi_layout.insertLayout(0, header)
        self.wifi_list = QListWidget()
        self.wifi_list.setMinimumHeight(110)
        # Fix round (O-1, 2026-11-26): the list stays hidden until
        # the first scan completes — before that the card is its
        # header (title + status + Scan), not a tall empty box
        # holding one "Press Scan" row (§6.2: empty states, not
        # voids). _on_wifi_ready shows it with the results.
        self.wifi_list.setVisible(False)
        wifi_layout.addWidget(self.wifi_list)
        column.addWidget(wifi_frame)

        # ------------------------------------------------ room
        room_frame, room_layout, self._room_title = _section_card("Room")
        room_row = QHBoxLayout()
        room_row.setSpacing(8)
        self.brightness_label = QLabel("Not checked")
        self.brightness_label.setWordWrap(True)
        self.brightness_label.setStyleSheet(
            f"color: {TEXT}; font-size: {TYPE_BODY}px;")
        room_row.addWidget(self.brightness_label, 1)
        self.brightness_button = QPushButton("Check brightness")
        self.brightness_button.clicked.connect(self._start_brightness)
        room_row.addWidget(self.brightness_button)
        room_layout.addLayout(room_row)
        # UI Phase 4 (D-P4-13): the Room check's promise, standing
        # on the card — the frame is measured, never stored or sent.
        # (The Notices hint states the ambient-light equivalent;
        # the Room check now states its own.)
        self._room_promise_label = QLabel(
            "The picture is measured, never stored or sent anywhere.")
        self._room_promise_label.setWordWrap(True)
        self._room_promise_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        room_layout.addWidget(self._room_promise_label)
        column.addWidget(room_frame)

        # ------------------------------------------------ timeline
        tl_frame, tl_layout, self._timeline_title = _section_card(
            "Environment timeline")
        tl_header = QHBoxLayout()
        tl_header.setSpacing(8)
        tl_header.addWidget(self._timeline_title)
        tl_header.addStretch(1)
        self.timeline_refresh_button = QPushButton("Refresh")
        self.timeline_refresh_button.clicked.connect(
            self.refresh_timeline)
        tl_header.addWidget(self.timeline_refresh_button)
        # §14 cluster C: kind filter. Its entries are rebuilt
        # by refresh_timeline from the kinds actually present,
        # so it never offers a kind with nothing under it.
        self.timeline_kind_filter = QComboBox()
        self.timeline_kind_filter.addItem("All kinds")
        self.timeline_kind_filter.currentTextChanged.connect(
            lambda _text: self.refresh_timeline())
        tl_header.addWidget(self.timeline_kind_filter)
        tl_layout.removeWidget(self._timeline_title)
        tl_layout.insertLayout(0, tl_header)
        self.timeline_count_label = QLabel("")
        self.timeline_count_label.setObjectName("muted")
        tl_layout.addWidget(self.timeline_count_label)
        self.timeline_list = QListWidget()
        self.timeline_list.setMinimumHeight(110)
        tl_layout.addWidget(self.timeline_list)
        column.addWidget(tl_frame)

        # ------------------------------------------------ trends (v5.31)
        tr_frame, tr_layout, self._trends_title = _section_card(
            "Last 24 hours")
        self.noise_chart = _TrendChart(
            "Sounds heard — loudest each hour (%)")
        tr_layout.addWidget(self.noise_chart)
        self.light_chart = _TrendChart(
            "Room brightness — average each hour (%)")
        tr_layout.addWidget(self.light_chart)
        column.addWidget(tr_frame)

        column.addStretch(1)

        # ------------------------------------------------ providers
        if self._provider("wifi_scan") is None:
            self.scan_button.setEnabled(False)
            self.wifi_status_label.setText("Not available in this build")
            self._set_wifi_rows(
                ["Wi-Fi scanning isn't available in this build."])
        else:
            self._set_wifi_rows(["Press Scan to look for networks."])
        if self._provider("quality") is None:
            self.measure_button.setEnabled(False)
        if self._provider("brightness") is None:
            self.brightness_button.setEnabled(False)
            self.brightness_label.setText("Not available in this build")

        self._timer = QTimer(self)
        self._timer.setInterval(_STATE_POLL_MS)
        self._timer.timeout.connect(self.refresh_state)

        self.refresh_state()
        self.refresh_timeline()

    # ------------------------------------------------------------- API

    def timer_active(self) -> bool:
        return self._timer.isActive()

    def set_palette(self, highlight: str, accent: str,
                    text: str | None = None,
                    text_muted: str | None = None):
        """Recolour the accents from a theme pair: section titles
        take the highlight; the Last-24-hours bars take the accent
        (E-F209 — they painted the house ACCENT whatever the theme
        was). Stage 0 features batch 5 (Light theme, render-found):
        the state values, sense names and chart titles also
        carried the house TEXT in scoped sheets / paint, so on
        Light they were invisible. ``text`` / ``text_muted``
        (the resolved theme values; house tokens when omitted)
        recolour them; the state titles keep the muted ink.
        On a light theme the caller passes the accent as the
        highlight — the pale highlight is unreadable on light.
        """
        self._highlight = highlight
        self._accent = accent
        for label in (self._wifi_title, self._room_title,
                      self._timeline_title):
            label.setStyleSheet(
                f"color: {highlight}; font-size: {TYPE_CARD_TITLE}px; font-weight: 600;")
        for chart in (self.noise_chart, self.light_chart):
            chart.set_bar_color(accent)
        if text is not None or text_muted is not None:
            t = text or TEXT
            m = text_muted or TEXT_MUTED
            senses_title = getattr(self, "_senses_title", None)
            if senses_title is not None:
                senses_title.setStyleSheet(
                    f"color: {t}; font-size: {TYPE_CARD_TITLE}px; "
                    "font-weight: 600;")
            for card in getattr(self, "_state_cards", {}).values():
                card.set_text_colors(t, m)
            for label in getattr(self, "_sense_name_labels",
                                 {}).values():
                label.setStyleSheet(
                    f"color: {t}; font-size: {TYPE_CARD_TITLE}px; "
                    "font-weight: 600;")
            for label in getattr(self, "_matrix_words", {}).values():
                label.setStyleSheet(
                    f"color: {t}; font-size: {TYPE_BODY}px;")
            for label in list(
                    getattr(self, "_matrix_headers", {}).values()) + list(
                    getattr(self, "_matrix_kickers", {}).values()):
                label.setStyleSheet(
                    f"color: {m}; font-size: 11px; font-weight: 600;")
            for label in (getattr(self, "wifi_status_label", None),
                          getattr(self, "brightness_label", None),
                          getattr(self, "_quality_targets_label", None),
                          getattr(self, "_room_promise_label", None)):
                if label is not None:
                    label.setStyleSheet(
                        f"color: {m}; font-size: 11px;")
            for chart in (getattr(self, "noise_chart", None),
                          getattr(self, "light_chart", None)):
                if chart is not None:
                    chart.set_text_colors(t, m)
        self.update()

    def set_font_scale(self, scale: float) -> None:
        """Scale the painted chart titles (batch 5, item 12).

        The QSS font scale reaches every styled label but not
        the _TrendChart paintEvent text, so the page drives the
        charts' hook from the same appearance value the window
        passes. Called from _apply_appearance alongside the
        theme hook (E-F209 pattern).
        """
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = 1.0
        self._font_scale = scale
        for chart in (getattr(self, "noise_chart", None),
                      getattr(self, "light_chart", None)):
            if chart is not None:
                chart.set_font_scale(scale)

    def painted_title_font_size(self) -> float:
        """The painted chart title's computed point size."""
        return 9.0 * getattr(self, "_font_scale", 1.0)

    def refresh_state(self):
        """Poll the cheap state provider once and repaint the four
        state cards. A missing or failing provider leaves honest
        placeholders. The quality card is NOT touched — it only
        moves on an explicit measurement."""
        provider = self._provider("state")
        state = None
        if provider is not None:
            try:
                raw = provider()
                if isinstance(raw, dict):
                    state = raw
            except Exception:  # noqa: BLE001 — placeholder, never a crash
                state = None
        # The matrix's capture column reads the privacy probe from
        # this state (D-P4-11); a missing state reads as no probe.
        self._last_state = state or {}
        self._apply_state(state)
        self._refresh_sense_rows()

    def _refresh_sense_rows(self):
        """Repaint the permission matrix (D-P4-11): three words per
        sense, each from its own source. OS from the Permissions
        model's OS state (allowed / denied / unknown — the model's
        own unknown line is "couldn't be read", so the column reads
        "—", never "Unknown": the OS probe reports its own state).
        App from the model + session grants (granted "On", session
        "This session only", denied "Off", never-decided "Unknown" —
        the probe cannot tell never-asked from unreadable, so it
        asserts neither (fix round F-08, parent ruling 2026-11-26
        19:19 IST; the model's own "Not set yet" in D-P4-11's word
        is the same unknown). Capture from the privacy probe the
        state poll carries (D-P4-12 flags): in use, idle, or "—"
        when the probe failed. Screen has no OS switch and no
        capture probe, so both read "—". A model read failure shows
        "—" in every column — the page never invents a standing.
        While a brightness check runs, the Camera capture column
        names VYUK's own use (D-P4-13)."""
        try:
            from .pf_permissions import (
                PERMISSION_DENIED, PERMISSION_GRANTED, permissions_model)
            from voice_assistant.core.gui_facade import permission_grants

            model = permissions_model()
        except Exception:  # noqa: BLE001 — honest "—", never a crash
            model = None
            permission_grants = None  # type: ignore[assignment]
        state = getattr(self, "_last_state", None) or {}
        for pid in ("microphone", "camera", "screen"):
            os_label = self._matrix_words.get((pid, "os"))
            app_label = self._matrix_words.get((pid, "app"))
            cap_label = self._matrix_words.get((pid, "capture"))
            if model is None:
                for lbl in (os_label, app_label, cap_label):
                    if lbl is not None:
                        lbl.setText("—")
                continue
            entry = next((e for e in model
                          if e.permission_id == pid), None)
            # OS column — the model's OS state.
            if os_label is not None:
                os_state = (getattr(entry, "os_state", None)
                            if entry is not None else None)
                os_label.setText(
                    {"allowed": "Allowed", "denied": "Denied"}.get(
                        os_state, "—"))
            # App column — the model + session grants.
            if app_label is not None:
                if entry is None:
                    app_label.setText("—")
                elif entry.state == PERMISSION_GRANTED:
                    # A durable grant wins over a session grant: the
                    # "Always allow" flow opens this session too, so
                    # both are present — the row reads "On".
                    app_label.setText("On")
                elif self._has_session_grant(pid, permission_grants):
                    app_label.setText("This session only")
                elif entry.state == PERMISSION_DENIED:
                    app_label.setText("Off")
                else:
                    # Fix round (F-08, parent ruling 2026-11-26
                    # 19:19 IST): "Unknown" — the probe cannot
                    # tell never-asked from unreadable, so the
                    # page asserts neither.
                    app_label.setText("Unknown")
            # Capture column — the privacy probe.
            if cap_label is not None:
                if (pid == "camera"
                        and getattr(self, "_brightness_busy", False)):
                    cap_label.setText(
                        "In use now — VYUK checking brightness")
                elif pid == "screen":
                    cap_label.setText("—")
                elif not state.get("privacy_ok", False):
                    cap_label.setText("—")
                else:
                    user = (state.get("camera_user") if pid == "camera"
                            else state.get("mic_user"))
                    cap_label.setText(
                        f"In use now ({user})" if user else "Idle")

    @staticmethod
    def _has_session_grant(pid, permission_grants) -> bool:
        """Session-grant read, guarded: a store failure reads as
        'no session grant' (the durable state still shows) rather
        than breaking the poll."""
        if permission_grants is None:
            return False
        try:
            return bool(permission_grants.has_session_grant(pid))
        except Exception:  # noqa: BLE001 — durable state still shows
            return False

    def refresh_trends(self, events: list):
        """Feed the two trend charts from the timeline events
        (v5.31): hourly buckets over the last 24 hours — sound
        peaks from 'sound' events, brightness from 'light'
        events. N7 (v5.45): the percentage comes from each
        event's structured "value" field when the line carries
        one; older lines are read out of the message prose,
        the same shape the tools used to write."""
        import re as _re
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        pct_re = _re.compile(r"(\d+(?:\.\d+)?)\s*%")
        now = _dt.now()
        buckets: dict[str, dict[int, list[float]]] = {
            "sound": {}, "light": {}}
        for event in events or []:
            kind = event.get("kind")
            if kind not in buckets:
                continue
            try:
                when = _dt.fromisoformat(event["ts"])
            except (KeyError, TypeError, ValueError):
                continue
            if now - when > _td(hours=24):
                continue
            value = event.get("value")
            if isinstance(value, bool) \
                    or not isinstance(value, (int, float)):
                match = pct_re.search(str(event.get("message", "")))
                if not match:
                    continue
                value = float(match.group(1))
            buckets[kind].setdefault(when.hour, []).append(
                float(value))
        for kind, chart, pick in (
                ("sound", self.noise_chart, max),
                ("light", self.light_chart,
                 lambda vals: sum(vals) / len(vals))):
            series = []
            hours = []
            for back in range(23, -1, -1):
                hour = (now - _td(hours=back)).hour
                hours.append(hour)
                vals = buckets[kind].get(hour)
                series.append(pick(vals) if vals else None)
            chart.set_series(series, hours)

    def refresh_timeline(self):
        """Reload the environment timeline from the events
        provider (newest first, capped at 100 rows). The kind
        filter (§14 cluster C) narrows the DISPLAYED rows only;
        the trends feed always receives every event."""
        provider = self._provider("events")
        events: list = []
        if provider is not None:
            try:
                raw = provider()
                if isinstance(raw, list):
                    events = raw
            except Exception:  # noqa: BLE001 — empty state, never a crash
                events = []
        self.refresh_trends(events)
        # Rebuild the filter's entries from the kinds actually
        # present, keeping the user's selection when it
        # survives (signals blocked: the refill must not
        # re-enter this method).
        kinds = sorted({str(event.get("kind") or "")
                        for event in events
                        if isinstance(event, dict)} - {""})
        combo = self.timeline_kind_filter
        selected = combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("All kinds")
            for kind in kinds:
                combo.addItem(kind)
            combo.setCurrentText(
                selected if selected in kinds else "All kinds")
        finally:
            combo.blockSignals(False)
        selected = combo.currentText()
        self.timeline_list.clear()
        if not events:
            self.timeline_count_label.setText("")
            self.timeline_list.addItem(
                "Nothing noticed yet — sentinels report here.")
            return
        # UI Phase 4 (D-P4-24, render side): in private mode the
        # messages are blanked here too — the window's provider
        # blanks them at raise time, so a provider that returns
        # messages is blanked either way. Statuses and counts
        # stay: the row keeps its time and kind.
        try:
            from voice_assistant.core.gui_facade import (
                private_mode as _private_mode)
            _private = bool(_private_mode.private_mode_on())
        except Exception:  # noqa: BLE001 — a broken read reads public
            _private = False
        shown = 0
        for event in events:
            if shown >= _TIMELINE_CAP:
                break
            if not isinstance(event, dict):
                continue
            if selected != "All kinds" and \
                    str(event.get("kind") or "") != selected:
                continue
            when = _event_date_time(event.get("ts"))
            if _private:
                kind = str(event.get("kind") or "")
                self.timeline_list.addItem(f"{when} · {kind} · —")
            else:
                message = str(event.get("message") or "")
                self.timeline_list.addItem(f"{when} · {message}")
            shown += 1
        if shown == 0:
            # Defensive: kinds come from the loaded events, so
            # a listed filter should always match something.
            self.timeline_list.addItem("Nothing under this filter.")
        if selected == "All kinds":
            # UI Phase 4 (D-P4-15, R-P4-M4): the "of M shown" form
            # for the All-kinds view too, with M = the FILE's line
            # count (capped at 500) from the events_total provider
            # — not the loaded <= 100. Without the provider the
            # count falls back to the loaded events.
            total = self._events_total() or len(events)
            self.timeline_count_label.setText(f"{shown} of {total} shown")
        else:
            self.timeline_count_label.setText(
                f"{shown} of {len(events)} shown")

    def _events_total(self) -> int:
        """The timeline file's line count from the events_total
        provider (D-P4-15). 0 when the provider is absent or fails
        — the caller falls back to the loaded event count."""
        provider = self._provider("events_total")
        if provider is None:
            return 0
        try:
            return max(0, int(provider()))
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------- lifecycle

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self.refresh_state()
        self.refresh_timeline()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        self._timer.stop()

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout_state(self.width())
        self._relayout_matrix(self.width())

    # ------------------------------------------------------------- internals

    def _provider(self, key: str):
        fn = self._providers.get(key)
        return fn if callable(fn) else None

    def _relayout_matrix(self, width: int):
        """The permission matrix stacks below 460 px page width
        (§6.1): three columns per sense at wide widths, one
        stacked block per sense below. The word labels keep their
        bare words in both forms — only the kickers move, so a
        reader of the words never depends on the layout."""
        stacked = width < 460
        if getattr(self, "_matrix_stacked", None) == stacked:
            return
        self._matrix_stacked = stacked
        grid = self._matrix_grid
        for widget in list(self._matrix_headers.values()):
            grid.removeWidget(widget)
        for pid in ("microphone", "camera", "screen"):
            grid.removeWidget(self._sense_name_labels[pid])
            for col in ("os", "app", "capture"):
                grid.removeWidget(self._matrix_words[(pid, col)])
                grid.removeWidget(self._matrix_kickers[(pid, col)])
            btn = self._sense_buttons.get(pid)
            if btn is not None:
                grid.removeWidget(btn)
        for header in self._matrix_headers.values():
            header.setVisible(not stacked)
        if not stacked:
            for col, key in enumerate(("os", "app", "capture"), start=1):
                grid.addWidget(self._matrix_headers[key], 0, col)
            for row, pid in enumerate(
                    ("microphone", "camera", "screen"), start=1):
                grid.addWidget(self._sense_name_labels[pid], row, 0)
                for col, key in enumerate(("os", "app", "capture"),
                                         start=1):
                    grid.addWidget(self._matrix_words[(pid, key)], row, col)
                    self._matrix_kickers[(pid, key)].setVisible(False)
                btn = self._sense_buttons.get(pid)
                if btn is not None:
                    grid.addWidget(btn, row, 4)
            for col, stretch in ((0, 1), (1, 1), (2, 1), (3, 1), (4, 0)):
                grid.setColumnStretch(col, stretch)
        else:
            row = 0
            for pid in ("microphone", "camera", "screen"):
                grid.addWidget(self._sense_name_labels[pid], row, 0)
                btn = self._sense_buttons.get(pid)
                if btn is not None:
                    grid.addWidget(btn, row, 1)
                row += 1
                for key in ("os", "app", "capture"):
                    kicker = self._matrix_kickers[(pid, key)]
                    kicker.setVisible(True)
                    grid.addWidget(kicker, row, 0)
                    grid.addWidget(self._matrix_words[(pid, key)], row, 1)
                    row += 1
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 1)

    def _relayout_state(self, width: int):
        # D8 redesign (owner ruling 2026-11-01): the grid used
        # to drop to 2 columns below a 900 px page, which made
        # it two rows tall at the minimum window and pushed the
        # Wi-Fi radar hint half under the first-paint fold at
        # 120% text. The page is only 632 px wide at the minimum
        # 908 px window, so the 4-column threshold sits at 600:
        # four narrow cards still fit their titles and values
        # at ~140 px each, the grid stays one row tall, and the
        # radar hint clears the fold. Column stretches are
        # reset on every relayout: a previous 4-column pass
        # otherwise leaves empty stretched columns behind that
        # squeeze the cards into half the grid on the way back
        # to 2 columns.
        if width >= 600:
            columns = 4
        elif width >= 460:
            columns = 2
        else:
            columns = 1
        if getattr(self, "_state_columns", 0) == columns:
            return
        self._state_columns = columns
        for card in self._state_cards.values():
            self._state_grid.removeWidget(card.frame)
        for col in range(4):
            self._state_grid.setColumnStretch(col, 0)
        for index, card in enumerate(self._state_cards.values()):
            row, col = divmod(index, columns)
            self._state_grid.addWidget(card.frame, row, col)
            self._state_grid.setColumnStretch(col, 1)

    def _apply_state(self, state: dict | None):
        # UI Phase 4 (D-P4-12): unknown is unknown — each leg
        # carries a probe-ok flag, and a failed leg renders "—" +
        # "Couldn't check", never a state word. A wholly-missing
        # state (provider absent or raised) reads the same way on
        # every leg. A leg that read fine but has no value (no
        # battery, no interface up) keeps its honest state word.
        blank = state is None
        state = state or {}

        if not state.get("connection_ok", False):
            self.state_labels["connection"].setText("—")
            self._state_subs["connection"].setText("Couldn't check")
        else:
            conn = state.get("connection")
            if conn == "wifi":
                self.state_labels["connection"].setText(
                    state.get("ssid") or "Wi-Fi")
                self._state_subs["connection"].setText("Wi-Fi")
            elif conn == "ethernet":
                self.state_labels["connection"].setText("Ethernet")
                self._state_subs["connection"].setText("Wired connection")
            elif conn == "offline":
                self.state_labels["connection"].setText("Offline")
                self._state_subs["connection"].setText("No connection")
            else:
                self.state_labels["connection"].setText("—")
                self._state_subs["connection"].setText("")

        if not state.get("battery_ok", False):
            self.state_labels["battery"].setText("—")
            self._state_subs["battery"].setText("Couldn't check")
        else:
            pct = state.get("battery_pct")
            if pct is None:
                self.state_labels["battery"].setText("—")
                self._state_subs["battery"].setText(
                    "" if blank else "No battery")
            else:
                try:
                    shown = max(0.0, min(100.0, float(pct)))
                    self.state_labels["battery"].setText(f"{shown:.0f}%")
                except (TypeError, ValueError):
                    self.state_labels["battery"].setText("—")
                plugged = state.get("battery_plugged")
                if plugged is True:
                    self._state_subs["battery"].setText("Charging")
                elif plugged is False:
                    self._state_subs["battery"].setText("On battery")
                else:
                    self._state_subs["battery"].setText("")

        if not state.get("privacy_ok", False):
            self.state_labels["privacy"].setText("—")
            self._state_subs["privacy"].setText("Couldn't check")
        else:
            lines = []
            if state.get("camera_user"):
                lines.append(f"Camera: {state['camera_user']}")
            if state.get("mic_user"):
                lines.append(f"Mic: {state['mic_user']}")
            if lines:
                self.state_labels["privacy"].setText("\n".join(lines))
                self._state_subs["privacy"].setText("In use now")
            else:
                self.state_labels["privacy"].setText("Idle")
                self._state_subs["privacy"].setText("Not in use")

    # ------------------------------------------------ wifi radar

    def _set_wifi_rows(self, rows: list[str]):
        self.wifi_list.clear()
        for row in rows:
            self.wifi_list.addItem(row)

    def _start_scan(self):
        provider = self._provider("wifi_scan")
        if provider is None or self._scanning:
            return
        self._scanning = True
        self.scan_button.setEnabled(False)
        self.wifi_status_label.setText("Scanning…")

        def work():
            try:
                result = provider()
            except Exception:  # noqa: BLE001 — honest slot, never a crash
                result = None
            self._signals.wifi_ready.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _on_wifi_ready(self, result):
        self._scanning = False
        if self._provider("wifi_scan") is not None:
            self.scan_button.setEnabled(True)
        # Fix round (O-1): the first completed scan opens the
        # results area — before this the list stays hidden and the
        # card is compact.
        self.wifi_list.setVisible(True)
        self.last_wifi_result = result if isinstance(result, dict) else None
        stamp = datetime.now().strftime("%H:%M:%S")
        self.wifi_status_label.setText(f"Last scan {stamp}")
        if not isinstance(result, dict):
            self._set_wifi_rows(["Couldn't scan just now — try again."])
            return
        if not result.get("available"):
            note = str(result.get("note")
                       or "Wi-Fi isn't available on this machine.")
            self._set_wifi_rows([note])
            return
        networks = result.get("networks") or []
        if not networks:
            self._set_wifi_rows(["No networks found"])
            return
        rows = []
        for net in networks:
            if not isinstance(net, dict):
                continue
            ssid = str(net.get("ssid") or "<hidden network>")
            signal = net.get("signal")
            signal_text = f"{signal}%" if signal is not None else "—"
            secured = net.get("secured")
            if secured is True:
                suffix = " · secured"
            elif secured is False:
                suffix = " · open"
            else:
                suffix = ""
            rows.append(f"{ssid} — {signal_text}{suffix}")
        self._set_wifi_rows(rows or ["No networks found"])

    # ------------------------------------------------ network quality

    def _start_quality(self):
        provider = self._provider("quality")
        if provider is None or self._measuring:
            return
        self._measuring = True
        self.measure_button.setEnabled(False)
        self.state_labels["quality"].setText("Measuring…")

        def work():
            try:
                result = provider()
            except Exception:  # noqa: BLE001
                result = None
            self._signals.quality_ready.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _on_quality_ready(self, result):
        self._measuring = False
        if self._provider("quality") is not None:
            self.measure_button.setEnabled(True)
        self.last_quality_result = (
            result if isinstance(result, dict) else None)
        label = self.state_labels["quality"]
        sub = self._state_subs["quality"]
        if not isinstance(result, dict):
            label.setText("—")
            sub.setText("Measure failed")
            return
        if not result.get("reachable"):
            label.setText("Unreachable")
            sub.setText("No reply")
            return
        verdict = str(result.get("verdict") or "Measured")
        label.setText(verdict[:1].upper() + verdict[1:])
        latency = result.get("latency_ms")
        if isinstance(latency, (int, float)):
            _ms = float(latency)
            # UI Phase 4 (D-P4-14): the verdict line states the
            # PAGE's thresholds (100/300 ms), not the core
            # module's 50/120/250 verdicts.
            if _ms < _QUALITY_FAST_MS:
                _feel = f"Feels instant under {_QUALITY_FAST_MS} ms"
            elif _ms < _QUALITY_SLOW_MS:
                _feel = f"A little slow under {_QUALITY_SLOW_MS} ms"
            else:
                _feel = f"Slow above {_QUALITY_SLOW_MS} ms"
            sub.setText(f"{_feel} — {_ms:.0f} ms")
        else:
            sub.setText("")

    # ------------------------------------------------ room brightness

    def _start_brightness(self):
        provider = self._provider("brightness")
        if provider is None or self._brightness_busy:
            return
        self._brightness_busy = True
        self.brightness_button.setEnabled(False)
        self.brightness_label.setText("Checking…")
        # UI Phase 4 (D-P4-13): the Camera capture column names
        # VYUK's own use while the check runs — refresh the matrix
        # now, not on the next 5 s poll.
        self._refresh_sense_rows()

        def done_cb(result):
            # The provider may call back from a capture thread;
            # route through the bridge so the slot runs on the
            # GUI thread.
            self._signals.brightness_ready.emit(result)

        try:
            provider(done_cb)
        except Exception:  # noqa: BLE001 — treat as "no camera"
            self._signals.brightness_ready.emit(None)

    def _on_brightness_ready(self, result):
        self._brightness_busy = False
        if self._provider("brightness") is not None:
            self.brightness_button.setEnabled(True)
        # The check is over — the Camera capture column goes back
        # to the probe's word (D-P4-13).
        self._refresh_sense_rows()
        if not isinstance(result, dict):
            self.brightness_label.setText(
                "No camera available on this machine.")
            return
        label = str(result.get("label") or "Unknown")
        label = label[:1].upper() + label[1:]
        note = str(result.get("note") or "")
        if note:
            self.brightness_label.setText(f"{label} — {note}")
        else:
            level = result.get("brightness")
            if isinstance(level, (int, float)):
                self.brightness_label.setText(
                    f"{label} · {float(level) * 100:.0f}% light")
            else:
                self.brightness_label.setText(label)


class _TrendChart(QWidget):
    """A tiny hourly bar chart (v5.31): 24 buckets, None means
    no reading that hour. Honest empty state — it says what
    would fill it instead of drawing a flat line at zero."""

    _H = 64

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._series: list = [None] * 24
        # UI Phase 4 (D-P4-29): the bucket hours, riding with the
        # series so the axis can label them. None until the first
        # refresh_trends feed.
        self._hours: list | None = None
        # E-F209: the bar colour follows the theme accent (set by
        # SensesPage.set_palette); the default is the house ACCENT,
        # so Cyan is unchanged.
        self._bar_color = ACCENT
        # Batch 5 (Light theme): the title + axis text paint the
        # house TEXT / TEXT_MUTED — near-white on light. The page
        # re-colours them from the theme via set_text_colors.
        self._text = TEXT
        self._text_muted = TEXT_MUTED
        # Stage 0 features batch 5 (larger text): the painted
        # title scales with the appearance font scale (the QSS
        # cannot reach paintEvent text).
        self._font_scale = 1.0
        # The hour labels sit under the baseline (D-P4-29), so the
        # minimum height leaves room for them.
        self.setMinimumHeight(self._H + 44)
        self.setToolTip(title)

    def set_font_scale(self, scale: float) -> None:
        """Scale the painted title with the appearance font scale."""
        try:
            self._font_scale = float(scale)
        except (TypeError, ValueError):
            self._font_scale = 1.0
        self.update()

    def title_font_size(self) -> float:
        """The painted title's computed point size (probe hook)."""
        return 9.0 * self._font_scale

    def set_series(self, series: list, hours: list | None = None):
        """Replace the 24 hourly buckets; `hours` (the bucket's
        hour, oldest first) labels the axis (D-P4-29). Without
        hours the axis stays unlabelled rather than guessing."""
        self._series = list(series)[:24]
        self._hours = (list(hours)[:24] if hours is not None else None)
        self.update()

    def set_bar_color(self, color: str):
        """Recolour the bars from a theme accent (E-F209)."""
        self._bar_color = color
        self.update()

    def set_text_colors(self, text: str, text_muted: str) -> None:
        """Recolour the painted title + axis text (batch 5, Light)."""
        self._text = text
        self._text_muted = text_muted
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        from PySide6.QtGui import QColor, QFont, QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        title_font = QFont()
        title_font.setPointSizeF(9.0 * self._font_scale)
        painter.setFont(title_font)
        painter.setPen(QColor(self._text))
        painter.drawText(0, 12, self._title)
        top = 20
        height = self._H
        width = max(self.width(), 48)
        slot = width / 24.0
        bar_w = max(2.0, slot - 2.0)
        painter.setPen(QColor(self._text_muted))
        painter.drawLine(0, top + height, width, top + height)
        if all(v is None for v in self._series):
            painter.setPen(QColor(self._text_muted))
            painter.drawText(
                0, top + 18,
                "Nothing logged in the last 24 hours yet — "
                "readings appear here as they're noticed.")
            painter.end()
            return
        painter.setBrush(QColor(self._bar_color))
        painter.setPen(Qt.PenStyle.NoPen)
        for i, value in enumerate(self._series):
            if value is None:
                continue
            h = max(2.0, height * min(max(value, 0.0), 100.0) / 100.0)
            x = i * slot + 1.0
            painter.drawRoundedRect(
                int(x), int(top + height - h), int(bar_w), int(h), 1, 1)
        # UI Phase 4 (D-P4-29): the axis labels each bucket's hour,
        # every 3rd bucket ("18:00"), so a peak can be placed at a
        # time of day. The hours ride with the series; without them
        # the axis stays unlabelled rather than guessing.
        hours = getattr(self, "_hours", None)
        if hours:
            label_font = QFont()
            label_font.setPointSizeF(8.0 * self._font_scale)
            painter.setFont(label_font)
            painter.setPen(QColor(self._text_muted))
            for i in range(0, 24, 3):
                if i >= len(hours) or hours[i] is None:
                    continue
                try:
                    text = f"{int(hours[i]):02d}:00"
                except (TypeError, ValueError):
                    continue
                painter.drawText(int(i * slot), top + height + 13, text)
        painter.end()
