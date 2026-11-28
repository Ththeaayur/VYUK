"""
VYUK — System Pulse page (v5.18, SENSES EYES II): live system vitals.

Wiring Build 7 label note: this page is SYSTEM PROBES ONLY (CPU,
memory, disk, network, battery). It does not show, and must not be
confused with, the entity cards in core/pulse_w5.py (Wikidata /
Wikipedia / news items) — those are a separate, unwired library.

Seven rows — CPU, memory, disk, network throughput, battery,
uptime and processes — each a plain-words label and a live number
in mono, with a horizontal bar in its vital's colour (indigo for
CPU, Memory and Disk; teal for Network and Battery) where the
metric has a proportion to show. The page is deliberately
self-contained: psutil is the only data source, every probe is
individually guarded, and a sensor that cannot be read shows an
honest "—" instead of a stale or invented number. Sampling runs
on a 1-second timer that only ticks while the page is on screen,
plus one immediate sample the moment it appears.

The sampler is a seam: ``PulsePage(sampler=fn)`` where ``fn()`` returns
a dict with keys ``cpu``, ``mem``, ``disk``, ``net_bps``,
``battery_pct``, ``battery_plugged``, ``uptime_s``, ``procs``
(floats / bools, or ``None`` for
"unknown"). Tests drive the page through that seam; production uses
the built-in psutil sampler. ``latest()`` returns the most recent
sample dict (all-``None`` before the first successful sample, and
after a sampler failure — a failed probe never leaves old numbers
standing as if they were fresh).
"""

from __future__ import annotations

import datetime
import os
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .design_system.flowlayout import FlowLayout
from .style import TYPE_CARD_TITLE
# Command Deck Phase E: the page paints from the token palette —
# the old mirrored indigo/teal/green/amber/red constants are gone.
from .design_system.palette import parse_color
from .style import (TEXT, TEXT_MUTED, ACCENT, ACCENT_2, OK, AMBER,
                    DANGER, LINE_SUBTLE)
from .style_tokens import MONO_FONT_STACK, first_font_family

try:
    import psutil
except Exception:  # noqa: BLE001 — page must still build; meters read "—"
    psutil = None  # type: ignore[assignment]

TRACK = QColor(*parse_color(LINE_SUBTLE))

_HISTORY_LEN = 60
_NET_FLOOR_BPS = 1_000_000.0   # adaptive network ceiling never below 1 MB/s

# UI Phase 4 (D-P4-08): refresh cadence, QSettings `pulse/refresh_ms`.
# 1000 / 2000 / 5000 ms, default 1000. The Pulse header control and
# the Appearance card mirror write the same key; the page reads it
# on show so the two controls can never drift apart.
_CADENCES_MS = (1000, 2000, 5000)
_CADENCE_DEFAULT_MS = 1000
_CADENCE_KEY = "pulse/refresh_ms"

# UI Phase 4 (D-P4-07): warning word -> Doctor check label. The
# mapping is a named table, not a string guess — a word with no
# check stays text. As verified against doctor.py (see the Phase 4
# build report's mapping table): NO Doctor check verdicts on disk
# space, machine memory, or battery level — `check_data` is data-dir
# writability, `check_process_load` is VYUK's own-process load, and
# `check_watchers` reports battery percent only in its detail while
# verdicting on the watcher. So this table is EMPTY: every word
# stays text today. The link mechanism (chip + on_word_doctor hook)
# exists so a future check can be linked by adding one row here.
_WORD_TO_DOCTOR: dict[str, str] = {}

_SAMPLE_KEYS = ("cpu", "mem", "disk", "net_bps", "battery_pct",
                "battery_plugged", "uptime_s", "procs")

# UI rethink Build 3 (rethink §6.11/§6.12): the docked status strip's
# word thresholds — the metrics' home owns the numbers. The Pulse page
# renders the meters; the strip names the worst one. Battery
# thresholds apply only while unplugged (a charging battery at 15%
# is not "Battery low"). These are the strip's words only — the page
# itself keeps its meters unthresholded.
STRIP_WARN_CPU_PCT = 90
STRIP_WARN_MEM_PCT = 90
STRIP_WARN_DISK_PCT = 90
STRIP_ERROR_DISK_PCT = 95
STRIP_WARN_BATTERY_PCT = 20
STRIP_ERROR_BATTERY_PCT = 10


def _blank_sample() -> dict:
    return {key: None for key in _SAMPLE_KEYS}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = _clamp01(t)
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def _heat_color(base: QColor, frac: float) -> QColor:
    """Accent colour that warms toward amber, then red, as the value
    nears its ceiling: calm below 70%, amber blend 70–88%, red above."""
    if frac < 0.70:
        return QColor(base)
    if frac < 0.88:
        return _lerp_color(base, QColor(AMBER), (frac - 0.70) / 0.18)
    return _lerp_color(QColor(AMBER), QColor(DANGER), (frac - 0.88) / 0.12)


def format_rate(bps) -> str:
    """Bytes-per-second -> human rate, decimal units (999 -> '999 B/s',
    1_500_000 -> '1.5 MB/s'). ``None`` (unknown) -> '—'."""
    if bps is None:
        return "—"
    try:
        value = float(bps)
    except (TypeError, ValueError):
        return "—"
    if value < 0:
        value = 0.0
    if value < 1000:
        return f"{int(value)} B/s"
    for unit, scale in (("GB/s", 1e9), ("MB/s", 1e6), ("KB/s", 1e3)):
        if value >= scale:
            text = f"{value / scale:.1f}"
            if text.endswith(".0"):
                text = text[:-2]
            return f"{text} {unit}"
    return f"{int(value)} B/s"  # unreachable, kept for clarity


def _format_uptime(seconds) -> str | None:
    """Seconds since boot -> '1d 2h' / '3h 12m' / '5m'.
    ``None`` (or unparseable) -> ``None``: the footer omits the half
    it cannot state honestly."""
    if seconds is None:
        return None
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return None
    if total < 0:
        total = 0
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


class _PsutilSampler:
    """Default sampler: one guarded psutil probe per key, so a single
    missing sensor never takes the others down with it."""

    def __init__(self):
        self._net_last: tuple[int, float] | None = None
        if psutil is not None:
            try:  # prime cpu_percent; its first real read needs a prior call
                psutil.cpu_percent(interval=None)
            except Exception:  # noqa: BLE001
                pass
            # UI Phase 4 (D-P4-10): prime per-process cpu_percent too —
            # a process's first cpu_percent() call returns 0.0, so
            # without priming the first top-processes read would rank
            # on zeros. Best-effort: a process that exits mid-prime
            # is skipped, never fatal.
            try:
                for _proc in psutil.process_iter():
                    try:
                        _proc.cpu_percent(interval=None)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

    def top_processes(self, n: int = 5) -> list[dict] | None:
        """Top `n` processes by CPU%: [{name, cpu_pct, rss_bytes}].
        None when the process list cannot be read at all (no psutil,
        or the iteration fails) — the page renders that as
        unavailable, never as an empty list. Private mode blanks
        the names at raise time (D-P4-24); the page blanks them
        again at render time."""
        if psutil is None:
            return None
        try:
            from voice_assistant.core.gui_facade import (
                private_mode as _private_mode)
            _private = bool(_private_mode.private_mode_on())
        except Exception:  # noqa: BLE001 — a broken read reads public
            _private = False
        try:
            rows = []
            for proc in psutil.process_iter(
                    ["name", "cpu_percent", "memory_info"]):
                try:
                    info = proc.info
                    cpu = info.get("cpu_percent")
                    mem = info.get("memory_info")
                    rows.append({
                        "name": (None if _private else
                                 (info.get("name") or "—")),
                        "cpu_pct": (None if cpu is None
                                    else float(cpu)),
                        "rss_bytes": (None if mem is None else
                                      int(getattr(mem, "rss", 0) or 0)),
                    })
                except Exception:  # noqa: BLE001 — one dead process
                    continue  # never takes the list down
            rows.sort(key=lambda r: (r["cpu_pct"] is None,
                                     -(r["cpu_pct"] or 0.0)))
            return rows[:max(0, int(n))]
        except Exception:  # noqa: BLE001 — the list says so
            return None

    @staticmethod
    def _probe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 — honest "unknown", never a crash
            return None

    def __call__(self) -> dict:
        if psutil is None:
            return _blank_sample()
        sample = _blank_sample()
        sample["cpu"] = self._probe(lambda: float(psutil.cpu_percent(interval=None)))
        sample["mem"] = self._probe(lambda: float(psutil.virtual_memory().percent))
        sample["disk"] = self._probe(lambda: float(psutil.disk_usage(self._anchor()).percent))
        sample["net_bps"] = self._probe(self._net_rate)
        battery = self._probe(psutil.sensors_battery)
        if battery is not None:
            sample["battery_pct"] = self._probe(lambda: float(battery.percent))
            sample["battery_plugged"] = bool(battery.power_plugged)
        sample["uptime_s"] = self._probe(
            lambda: max(0.0, time.time() - psutil.boot_time()))
        sample["procs"] = self._probe(lambda: len(psutil.pids()))
        return sample

    @staticmethod
    def _anchor() -> str:
        # The drive this app lives on (C:\ on Windows, / elsewhere).
        try:
            return Path(__file__).resolve().anchor or os.sep
        except Exception:  # noqa: BLE001
            return os.sep

    def _net_rate(self):
        counters = psutil.net_io_counters()
        total = counters.bytes_sent + counters.bytes_recv
        now = time.monotonic()
        last, self._net_last = self._net_last, (total, now)
        if last is None:
            return None  # no delta yet — first tick reads "—" honestly
        prev_total, prev_time = last
        elapsed = now - prev_time
        if elapsed <= 0:
            return None
        return max(0.0, (total - prev_total) / elapsed)


class BarMeter(QWidget):
    """One horizontal bar meter: a faint track with a fill that
    grows with the reading, in the vital's own colour (mockup
    language — indigo for CPU/Memory/Disk, teal for Network/
    Battery). The value itself lives on the card, not in the
    meter; history is still recorded, so window statistics and
    the public API are unchanged from the dial it replaces."""

    def __init__(self, base_color: str = ACCENT, parent=None):
        super().__init__(parent)
        self._base_color = QColor(base_color)
        self._fraction: float | None = None
        self._value_text = "—"
        self._color_override: QColor | None = None
        self._history: deque[float] = deque(maxlen=_HISTORY_LEN)
        self._raw_history: deque[float] = deque(maxlen=_HISTORY_LEN)
        self.setMinimumSize(100, 10)

    # ------------------------------------------------------------- API

    def set_base_color(self, color: str):
        self._base_color = QColor(color)
        self.update()

    def set_reading(self, fraction, text: str, color: QColor | None = None,
                    value: float | None = None):
        """fraction: 0..1 (clamped) or None for 'unknown'. ``color``
        overrides the vital's colour for this reading. ``value``
        is the reading in its native unit (bytes/s for the network
        meter, whose fraction is relative to a moving ceiling); it is
        recorded alongside the fraction so window statistics can be
        stated in real units. Without it the fraction itself is the
        raw record (exact for the percent meters)."""
        self._fraction = None if fraction is None else _clamp01(float(fraction))
        self._value_text = text
        self._color_override = color
        if self._fraction is not None:
            self._history.append(self._fraction)
            self._raw_history.append(
                self._fraction if value is None else float(value))
        self.update()

    def fraction(self) -> float | None:
        return self._fraction

    def value_text(self) -> str:
        return self._value_text

    def history(self) -> list[float]:
        return list(self._history)

    def raw_history(self) -> list[float]:
        """The recorded readings in native units (see set_reading),
        oldest first, same window as history()."""
        return list(self._raw_history)

    def clear_history(self) -> None:
        """UI Phase 4 (D-P4-03, R-P4-M3): the per-visit reset —
        hideEvent clears both deques so the stats window and the
        network ceiling never span a hide gap."""
        self._history.clear()
        self._raw_history.clear()

    # ------------------------------------------------------------- paint

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bar_h = 10
        y = (self.height() - bar_h) // 2
        w = self.width()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(TRACK)
        painter.drawRoundedRect(0, y, w, bar_h, bar_h / 2, bar_h / 2)
        if self._fraction is not None and self._fraction > 0:
            color = self._color_override or self._base_color
            painter.setBrush(color)
            fill_w = max(bar_h, int(w * self._fraction))
            painter.drawRoundedRect(0, y, fill_w, bar_h,
                                    bar_h / 2, bar_h / 2)
        painter.end()


# v5.57 (redo): the radial dials became bars. The old name stays as
# an alias so existing imports (tests included) keep working; the
# class's real name — and the test update — is BarMeter.
DialGauge = BarMeter


class _MetricRow:
    """One 48 px row: plain-words label + live number in mono,
    with the bar between them where the metric has one. The
    sub-line carries what the card's sub-label carried (network
    ceiling, battery state) and the stats line the window
    average/peak — nothing the cards showed is dropped, it just
    lives in the row now."""

    def __init__(self, key: str, name: str, base_color: str | None):
        self.key = key
        self.frame = QWidget()
        self.frame.setFixedHeight(48)
        layout = QHBoxLayout(self.frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        left.addStretch(1)
        self.name_label = QLabel(name)
        self.name_label.setStyleSheet(
            f"color: {TEXT}; font-size: {TYPE_CARD_TITLE}px; font-weight: 600;")
        left.addWidget(self.name_label)
        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        left.addWidget(self.sub_label)
        left.addStretch(1)
        layout.addLayout(left)
        self.meter = (BarMeter(base_color) if base_color is not None
                      else None)
        if self.meter is not None:
            self.meter.setFixedHeight(10)
            layout.addWidget(self.meter, stretch=1)
        else:
            layout.addStretch(1)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addStretch(1)
        self.value_label = QLabel("—")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.value_label.setFont(QFont(first_font_family(MONO_FONT_STACK)))
        self.value_label.setStyleSheet("font-size: 13px;")
        right.addWidget(self.value_label)
        self.stats_label = QLabel("")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.stats_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        self.stats_label.setVisible(False)
        right.addWidget(self.stats_label)
        right.addStretch(1)
        layout.addLayout(right)
        self.default_name = name

    def set_text_colors(self, text: str, text_muted: str) -> None:
        """Recolour the row labels from a theme (batch 5, Light).

        The name / sub / stats labels carry scoped sheets with
        the house TEXT / TEXT_MUTED, so on Light the names were
        near-white on light — invisible — while the live values
        (no colour of their own, so they inherit the theme text)
        read fine. The value label is deliberately untouched:
        it already follows the theme.
        """
        self.name_label.setStyleSheet(
            f"color: {text}; font-size: {TYPE_CARD_TITLE}px; font-weight: 600;")
        self.sub_label.setStyleSheet(
            f"color: {text_muted}; font-size: 11px;")
        self.stats_label.setStyleSheet(
            f"color: {text_muted}; font-size: 11px;")

    def set_value(self, text: str) -> None:
        """Set the row's live value, keeping its minimum width in
        step with the text (Golden-fix D8, carried over from the
        card): a longer live value widens the value column instead
        of clipping, and a shorter one gives the width back. The
        reset first matters: QLabel's sizeHint is floored by an
        explicitly set minimum width, so measuring without
        resetting would ratchet the column to the widest value
        ever seen."""
        self.value_label.setMinimumWidth(0)
        self.value_label.setText(text)
        self.value_label.setMinimumWidth(
            self.value_label.sizeHint().width())


# The old card name stays importable (tests included) — the real
# name is _MetricRow, and the row keeps the card's set_value
# contract (value text + width tracking).
_BarCard = _MetricRow
_DialCard = _BarCard


def _at_least(value, threshold) -> bool:
    try:
        return value is not None and float(value) >= threshold
    except (TypeError, ValueError):
        return False


def _at_most(value, threshold) -> bool:
    try:
        return value is not None and float(value) <= threshold
    except (TypeError, ValueError):
        return False


def _metric_word(sample: dict) -> tuple[str, str]:
    """(word, level) naming the worst metric, from the same
    thresholds the status strip uses — its metric legs only (the
    strip adds the session word and the mic state, which this
    page does not know). "error" is red, "warning" amber."""
    disk = sample.get("disk")
    battery = sample.get("battery_pct")
    plugged = bool(sample.get("battery_plugged"))
    if _at_least(disk, STRIP_ERROR_DISK_PCT):
        # E-F104: the error word must differ from the warning
        # word — "Disk almost full" is the warning; at >=95%
        # the disk is critically full (same vocabulary as the
        # status strip's derive_strip_state).
        return ("Disk critically full", "error")
    if not plugged and _at_most(battery, STRIP_ERROR_BATTERY_PCT):
        # E-F104: same shape — "Battery low" is the warning;
        # at <=10% unplugged it is critically low.
        return ("Battery critically low", "error")
    if _at_least(sample.get("cpu"), STRIP_WARN_CPU_PCT):
        return ("CPU high", "warning")
    if _at_least(sample.get("mem"), STRIP_WARN_MEM_PCT):
        return ("Memory high", "warning")
    if _at_least(disk, STRIP_WARN_DISK_PCT):
        return ("Disk almost full", "warning")
    if not plugged and _at_most(battery, STRIP_WARN_BATTERY_PCT):
        return ("Battery low", "warning")
    return ("Ready", "ok")


class PulsePage(QWidget):
    """Live system vitals (system probes): seven rows, sampled once a
    second while the page is visible. See the module docstring for
    the sampler seam and the honesty rules. Not entity cards."""

    # v5.57 (redo): bar colours per the approved mockup — indigo
    # for CPU/Memory/Disk, teal for Network/Battery. Uptime and
    # Processes have no bar: no proportion to show, just a number.
    _ROW_DEFS = (
        ("cpu", "CPU", ACCENT),
        ("mem", "Memory", ACCENT),
        ("disk", "Disk", ACCENT),
        ("net", "Network", ACCENT_2),
        ("battery", "Battery", ACCENT_2),
        ("uptime", "Uptime", None),
        ("procs", "Processes", None),
    )

    def __init__(self, sampler=None, parent=None, settings=None):
        super().__init__(parent)
        self._sampler = sampler if sampler is not None else _PsutilSampler()
        self._latest: dict = _blank_sample()
        self._net_ceiling = _NET_FLOOR_BPS
        self._user_paused = False
        # UI Phase 4 (D-P4-02 / D-P4-08 / D-P4-09): sample times, the
        # cadence, and the per-tick hooks the window wires (chart
        # refresh, Doctor navigation). The hooks are plain attributes
        # so the standalone page (tests, no window) simply has none.
        self._settings = settings
        self._cadence_ms = self._read_cadence()
        self._last_sample_at: float | None = None
        self._last_good_at: float | None = None
        self._last_sample_good = False
        self.on_sampled = None
        self.on_word_doctor = None
        self._word_doctor_target: str | None = None
        # UI Phase 4 (D-P4-05): the drive the app lives on.
        self._disk_anchor = _PsutilSampler._anchor()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # Header: Pause, Live/Paused, word (+ Doctor link when
        # D-P4-07 applies), stamp, cadence. A FlowLayout so the
        # header wraps instead of forcing the page wide at 640
        # (§6.1: word + stamp stack at the narrow width).
        header = FlowLayout(spacing=8)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("chipBtn")
        self.pause_button.setCheckable(True)
        self.pause_button.clicked.connect(self._on_pause_toggled)
        header.addWidget(self.pause_button)
        self.live_label = QLabel("Live")
        self.live_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        header.addWidget(self.live_label)
        # Build 6 (§6.7): the word names the worst metric — the
        # same thresholds the strip uses, on this page.
        self.word_label = QLabel("Ready")
        self.word_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 600;")
        header.addWidget(self.word_label)
        # D-P4-07: the Doctor link chip — hidden while the mapping
        # table is empty (every word stays text today).
        self.word_link_button = QPushButton("Open in Doctor ›")
        self.word_link_button.setObjectName("chipBtn")
        self.word_link_button.setVisible(False)
        self.word_link_button.clicked.connect(
            self._on_word_doctor_clicked)
        header.addWidget(self.word_link_button)
        # D-P4-02: the sample timestamp — evidence the numbers
        # are fresh; without it the page must not look live.
        self.stamp_label = QLabel("No reading yet")
        self.stamp_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        header.addWidget(self.stamp_label)
        # D-P4-08: the cadence control (mirrored on Appearance).
        self.cadence_combo = QComboBox()
        self.cadence_combo.setAccessibleName("Refresh every")
        for _ms in _CADENCES_MS:
            self.cadence_combo.addItem(f"Every {_ms // 1000} s", _ms)
        self._sync_cadence_combo()
        self.cadence_combo.currentIndexChanged.connect(
            self._on_cadence_changed)
        header.addWidget(self.cadence_combo)
        root.addLayout(header)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(0)
        root.addLayout(self._rows_layout, 1)

        self._rows: list[_MetricRow] = []
        self.dials: dict[str, BarMeter] = {}
        self._row_by_key: dict[str, _MetricRow] = {}
        # UI Phase 4 (D-P4-06): "VYUK herself" — service health,
        # separate from the host rows below, in the same row shape
        # (single-line rows with a wrapped sub-line, §9 density).
        vyuk_label = QLabel("VYUK herself")
        vyuk_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; font-weight: 600;")
        # Stored (not local): set_palette recolours it with the
        # resolved muted on Light — the house TEXT_MUTED in a
        # scoped sheet is ~1.9:1 on white (Look-fix 2 pattern).
        self._vyuk_label = vyuk_label
        self._rows_layout.addWidget(vyuk_label)
        self._service_rows: dict[str, _MetricRow] = {}
        for _key, _name in (("brain", "Brain"), ("voice", "Voice"),
                            ("connection", "Connection")):
            _srow = _MetricRow(_key, _name, None)
            self._service_rows[_key] = _srow
            # NOT appended to self._rows: that list is the meter
            # rows (cpu/mem/disk/net/battery/uptime/procs) — the
            # E-F206 suite pins its exact keys, and the service
            # rows have no meter. They recolour with the meters
            # in set_palette via _service_rows.
            self._rows_layout.addWidget(_srow.frame)
        for key, name, color in self._ROW_DEFS:
            row = _MetricRow(key, name, color)
            self._rows.append(row)
            self._row_by_key[key] = row
            if row.meter is not None:
                self.dials[key] = row.meter
            self._rows_layout.addWidget(row.frame)
        # D-P4-05: the disk row names its drive.
        _disk_row = self._row_by_key["disk"]
        _disk_row.default_name = f"Disk · {self._disk_anchor}"
        _disk_row.name_label.setText(_disk_row.default_name)

        # UI Phase 4 (D-P4-10): top processes, read-only, collapsed
        # by default. No kill action — HELD.
        self.top_procs_toggle = QPushButton("Top processes ▸")
        self.top_procs_toggle.setObjectName("chipBtn")
        self.top_procs_toggle.setCheckable(True)
        self.top_procs_toggle.setChecked(False)
        self.top_procs_toggle.clicked.connect(self._on_top_procs_toggled)
        root.addWidget(self.top_procs_toggle,
                       alignment=Qt.AlignmentFlag.AlignLeft)
        self._top_procs_box = QWidget()
        _tp_layout = QVBoxLayout(self._top_procs_box)
        _tp_layout.setContentsMargins(0, 0, 0, 0)
        _tp_layout.setSpacing(2)
        self._top_proc_labels: list[QLabel] = []
        for _i in range(5):
            _lbl = QLabel("—")
            _lbl.setObjectName("muted")
            _tp_layout.addWidget(_lbl)
            self._top_proc_labels.append(_lbl)
        self._top_procs_box.setVisible(False)
        root.addWidget(self._top_procs_box)

        # Footnote: what the bars are and when they tick — the
        # cadence is live, never a hardcoded "once a second"
        # (D-P4-08). Wiring Build 7: label disambiguation only —
        # probes, not cards.
        self.bars_note = QLabel(self._footnote_text())
        # E-F206: the footnote wraps instead of forcing the page
        # wide — unwrapped, its 611 px sizeHint drove the shell's
        # minimum to 683, and the vertical scrollbar (viewport 680
        # at 908x600) left a 3 px horizontal scrollbar under the
        # page. Wrapping drops the note's minimum to its widest
        # word; at wide widths the line stays whole.
        self.bars_note.setWordWrap(True)
        self.bars_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bars_note.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        root.addWidget(self.bars_note)

        self._timer = QTimer(self)
        self._timer.setInterval(self._cadence_ms)
        self._timer.timeout.connect(self.sample_now)

    # ------------------------------------------------------------- API

    def latest(self) -> dict:
        """The most recent sample dict (a copy). All values are None
        before the first sample and after a failed one."""
        return dict(self._latest)

    def timer_active(self) -> bool:
        return self._timer.isActive()

    def is_paused(self) -> bool:
        """True while the user has paused sampling from the header
        toggle. The pause survives the page being hidden and shown
        again; only the toggle itself clears it."""
        return self._user_paused

    # ------------------------------------------------- cadence (D-P4-08)

    def _read_cadence(self) -> int:
        """The stored cadence (QSettings `pulse/refresh_ms`), or the
        default when nothing is stored / the stored value is not one
        of the three offered cadences. Never raises: a settings read
        must never break the page."""
        if self._settings is None:
            return _CADENCE_DEFAULT_MS
        try:
            from voice_assistant.core.gui_facade import qsettings_get
            value = qsettings_get(_CADENCE_KEY, settings=self._settings)
        except Exception:  # noqa: BLE001 — fall back to the raw store
            try:
                value = self._settings.value(_CADENCE_KEY)
            except Exception:  # noqa: BLE001
                value = None
        try:
            ms = int(value)
        except (TypeError, ValueError):
            return _CADENCE_DEFAULT_MS
        return ms if ms in _CADENCES_MS else _CADENCE_DEFAULT_MS

    def cadence_ms(self) -> int:
        """The live cadence in ms (the timer interval)."""
        return self._cadence_ms

    def set_cadence(self, ms) -> None:
        """Apply a cadence (timer interval + footnote + stats-window
        label). Writes nothing — the control handlers own the
        QSettings write, so the Pulse header and the Appearance
        mirror can never double-write."""
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            ms = _CADENCE_DEFAULT_MS
        if ms not in _CADENCES_MS:
            ms = _CADENCE_DEFAULT_MS
        self._cadence_ms = ms
        self._timer.setInterval(ms)
        self._sync_cadence_combo()
        self.bars_note.setText(self._footnote_text())

    def _sync_cadence_combo(self) -> None:
        combo = getattr(self, "cadence_combo", None)
        if combo is None:
            return
        idx = combo.findData(self._cadence_ms)
        if idx >= 0 and idx != combo.currentIndex():
            combo.blockSignals(True)
            try:
                combo.setCurrentIndex(idx)
            finally:
                combo.blockSignals(False)

    def _on_cadence_changed(self, _index: int = 0) -> None:
        combo = getattr(self, "cadence_combo", None)
        if combo is None or combo.currentIndex() < 0:
            return
        ms = combo.itemData(combo.currentIndex())
        if self._settings is not None:
            try:
                from voice_assistant.core.gui_facade import qsettings_set
                qsettings_set(_CADENCE_KEY, int(ms),
                              settings=self._settings)
            except Exception:  # noqa: BLE001 — the in-memory value
                pass  # already carries the choice
        self.set_cadence(ms)

    def _footnote_text(self) -> str:
        # E-F206 + Wiring Build 7 pin the two literal sentences
        # in source (test_r3ef206_pulse_scroll,
        # test_wiring7_triage_pulse): the 1 s branch carries the
        # original sentence whole, and the disambiguation
        # sentence is one literal, never split across lines.
        # The cadence still reads live (D-P4-08) — the branch
        # is chosen from the stored cadence, not hard-coded.
        if self._cadence_ms == 2000:
            cadence = "Live bars, sampled every 2 seconds while this page is visible. "
        elif self._cadence_ms == 5000:
            cadence = "Live bars, sampled every 5 seconds while this page is visible. "
        else:
            cadence = "Live bars, sampled once a second while this page is visible. "
        return cadence + "System probes only — no news or entity cards here."

    def set_palette(self, accent: str, accent_2: str,
                    text: str | None = None,
                    text_muted: str | None = None):
        """Recolour the meters from a theme pair (E-F209): the
        ACCENT family (CPU/Memory/Disk) takes accent, the ACCENT_2
        family (Network/Battery) takes accent_2 — the v5.57 mockup
        families. Under Cyan the pair is the house tokens, so
        nothing changes. (The previous mapping — CPU -> highlight,
        Memory/Network -> accent, Disk/Battery untouched — could not
        fix E-F209: it would have moved CPU to the pale highlight
        under Cyan and left Disk/Battery cyan under every theme.)
        The per-reading colour override (disk heat shift) still
        applies on top of the base colour.
        Stage 0 features batch 5 (Light theme, render-found):
        the row labels also carried the house TEXT / TEXT_MUTED
        in scoped sheets, so on Light the metric names were
        invisible. ``text`` / ``text_muted`` (the resolved theme
        values; house tokens when omitted) recolour them.
        """
        for key in ("cpu", "mem", "disk"):
            self.dials[key].set_base_color(accent)
        for key in ("net", "battery"):
            self.dials[key].set_base_color(accent_2)
        for meter in self.dials.values():
            meter.update()
        if text is not None or text_muted is not None:
            # Meter rows plus the D-P4-06 service rows (kept out
            # of _rows — that list is the meter rows — but they
            # are _MetricRow instances and recolour the same way).
            _all_rows = list(getattr(self, "_rows", []))
            _all_rows.extend(
                getattr(self, "_service_rows", {}).values())
            for row in _all_rows:
                row.set_text_colors(text or TEXT,
                                    text_muted or TEXT_MUTED)
            # Look-fix 2 (2026-11-24): the "Live" word and the
            # footnote carried the house TEXT_MUTED in scoped
            # sheets, so on Light both were faint grey on white
            # (the caption measured ~1.9:1). They follow the
            # resolved text_muted now. The state word (word_label)
            # keeps its per-state colour — amber / red by state —
            # but its OK level was the same house TEXT_MUTED, so
            # "Ready" was faint on Light too; the OK level follows
            # the resolved text_muted as well (stored here, applied
            # in _apply_word and re-applied below).
            _muted = text_muted or TEXT_MUTED
            self._muted = _muted
            # The D-P4-02 stamp and the D-P4-06 "VYUK herself"
            # kicker carry the same house TEXT_MUTED in scoped
            # sheets as the "Live" word did — on Light they were
            # faint grey on white until recoloured here. The
            # kicker keeps its semibold weight (the shared sheet
            # below would drop it).
            for _label in (getattr(self, "live_label", None),
                           getattr(self, "bars_note", None),
                           getattr(self, "stamp_label", None)):
                if _label is not None:
                    _label.setStyleSheet(
                        f"color: {_muted}; font-size: 11px;")
            _vyuk = getattr(self, "_vyuk_label", None)
            if _vyuk is not None:
                _vyuk.setStyleSheet(
                    f"color: {_muted}; font-size: 11px;"
                    " font-weight: 600;")
            try:
                _level = getattr(self, "_word_level", "ok")
                _color = (DANGER if _level == "error"
                          else AMBER if _level == "warning"
                          else _muted)
                self.word_label.setStyleSheet(
                    f"color: {_color}; font-size: 13px;"
                    f" font-weight: 600;")
            except Exception:  # noqa: BLE001 — cosmetic
                pass

    def sample_now(self):
        """Take one sample and repaint the meters. A sampler that
        raises leaves every meter at '—' — never stale numbers.
        While the user has paused, this is a no-op: a manual sample
        must not thaw a page the user deliberately froze."""
        if self._user_paused:
            return
        try:
            raw = self._sampler()
            sample = {key: raw.get(key) for key in _SAMPLE_KEYS}
        except Exception:  # noqa: BLE001
            sample = _blank_sample()
        self._latest = sample
        now = time.time()
        self._last_sample_at = now
        self._last_sample_good = any(v is not None for v in sample.values())
        if self._last_sample_good:
            self._last_good_at = now
        self._apply(sample)
        self._update_stamp()
        # D-P4-09: the per-tick hook the window wires (the overview
        # chart refresh — the tick IS the throttle, so the refresh
        # runs at the live cadence). Guarded: a hook failure never
        # breaks the sample.
        hook = getattr(self, "on_sampled", None)
        if callable(hook):
            try:
                hook(sample)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- lifecycle

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        # D-P4-08: the header control shows the stored value on
        # show — the Appearance mirror may have changed it while
        # this page was hidden.
        self._cadence_ms = self._read_cadence()
        self.set_cadence(self._cadence_ms)
        if self._user_paused:
            self._update_stamp()
            return  # the pause survives being hidden and re-shown
        self.sample_now()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        self._timer.stop()
        # UI Phase 4 (D-P4-03, R-P4-M3): the per-visit reset — the
        # histories clear so the stats window and the network
        # ceiling never span a hide gap. The ceiling falls back to
        # its floor with the empty window (D-P4-04: the ceiling is
        # the maximum of the CURRENT window).
        for meter in self.dials.values():
            meter.clear_history()
        self._net_ceiling = _NET_FLOOR_BPS

    # ------------------------------------------------------------- internals

    def _on_pause_toggled(self, checked: bool):
        self._user_paused = bool(checked)
        self.live_label.setText("Paused" if self._user_paused else "Live")
        if self._user_paused:
            self._timer.stop()
        elif self.isVisible() and not self._timer.isActive():
            self._timer.start()
        self._update_stamp()

    def _update_stamp(self) -> None:
        """UI Phase 4 (D-P4-02): the header stamp, from the sample
        that painted the rows. Paused shows the age of the last
        good reading; a failed sample names the last good one
        instead of looking live.

        Fix round (F-06, parent ruling 2026-11-26 19:19 IST):
        ONE status line. While paused, only the stamp carries
        status — the bare "Paused" live label and the word label
        ("Ready" / the worst metric) are hidden, so the header
        cannot stutter "Paused … Paused — last sample …". While
        running, the live state shows again. This is the choke
        point: every sample, every pause toggle and every show
        passes through here.
        """
        label = getattr(self, "stamp_label", None)
        if label is None:
            return
        for _name in ("live_label", "word_label"):
            _lbl = getattr(self, _name, None)
            if _lbl is not None:
                _lbl.setVisible(not self._user_paused)
        now = time.time()

        def _hhmmss(ts: float) -> str:
            return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")

        if self._user_paused:
            if self._last_good_at is not None:
                age = max(0, int(now - self._last_good_at))
                label.setText(
                    f"Paused — last sample {_hhmmss(self._last_good_at)}, "
                    f"{age} s ago")
            else:
                label.setText("Paused — no reading yet")
            return
        if self._last_sample_good and self._last_sample_at is not None:
            label.setText(f"Updated {_hhmmss(self._last_sample_at)}")
        elif self._last_good_at is not None:
            label.setText(
                "No reading — last good sample "
                f"{_hhmmss(self._last_good_at)}")
        else:
            label.setText("No reading yet")

    def _on_word_doctor_clicked(self) -> None:
        hook = getattr(self, "on_word_doctor", None)
        if callable(hook) and self._word_doctor_target:
            try:
                hook(self._word_doctor_target)
            except Exception:  # noqa: BLE001 — navigation is best-effort
                pass

    def _on_top_procs_toggled(self, checked: bool) -> None:
        self.top_procs_toggle.setText(
            "Top processes ▾" if checked else "Top processes ▸")
        self._top_procs_box.setVisible(bool(checked))
        if checked:
            # Opening the disclosure paints the freshest read at
            # once instead of waiting for the next tick.
            self._apply_top_processes()

    def _apply(self, sample: dict):
        for key in ("cpu", "mem", "disk"):
            self._apply_percent(key, sample.get(key))
        self._apply_network(sample.get("net_bps"))
        self._apply_battery(sample.get("battery_pct"),
                            sample.get("battery_plugged"))
        self._apply_uptime(sample.get("uptime_s"))
        self._apply_procs(sample.get("procs"))
        self._apply_word(sample)
        self._apply_service_rows()
        self._apply_top_processes()

    def _apply_word(self, sample: dict):
        word, level = _metric_word(sample)
        self._word_level = level
        # Look-fix 2 (2026-11-24): the OK level was the house
        # TEXT_MUTED — faint grey on Light (~1.9:1). It follows
        # the resolved muted stored by set_palette; the error /
        # warning colours are semantic and unchanged.
        color = (DANGER if level == "error"
                 else AMBER if level == "warning"
                 else getattr(self, "_muted", TEXT_MUTED))
        self.word_label.setText(word)
        self.word_label.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 600;")
        # UI Phase 4 (D-P4-07): the word becomes a Doctor link only
        # when the mapping table names a check for it — the table
        # is empty today, so the chip stays hidden and the word
        # stays text.
        target = _WORD_TO_DOCTOR.get(word)
        self._word_doctor_target = target
        self.word_link_button.setVisible(target is not None)

    def _update_stats(self, key: str):
        """Refresh a row's avg/peak line from its meter's recorded
        history. Hidden until at least two readings exist — a single
        reading has no honest 'average window' to report. The window
        is labelled from the deque length × the live cadence (D-P4-03):
        60 samples at 1 s is 'last 60 s', at 2 s 'last 120 s' — never
        a hardcoded window."""
        row = self._row_by_key[key]
        if row.meter is None:
            return
        values = row.meter.raw_history()
        if len(values) < 2:
            row.stats_label.setText("")
            row.stats_label.setVisible(False)
            return
        avg = sum(values) / len(values)
        peak = max(values)
        window_s = _HISTORY_LEN * self._cadence_ms // 1000
        if key == "net":
            text = (f"avg {format_rate(avg)} · peak {format_rate(peak)}"
                    f" · last {window_s} s")
        else:
            text = (f"avg {avg * 100:.0f}% · peak {peak * 100:.0f}%"
                    f" · last {window_s} s")
        row.stats_label.setText(text)
        row.stats_label.setVisible(True)

    def _apply_percent(self, key: str, value):
        meter = self.dials[key]
        row = self._row_by_key[key]
        row.name_label.setText(row.default_name)
        row.sub_label.setText("")
        if value is None:
            meter.set_reading(None, "—")
            row.set_value(meter.value_text())
            self._update_stats(key)
            return
        shown = max(0.0, min(100.0, float(value)))
        meter.set_reading(shown / 100.0, f"{shown:.0f}%")
        row.set_value(meter.value_text())
        self._update_stats(key)

    def _apply_network(self, bps):
        meter = self.dials["net"]
        row = self._row_by_key["net"]
        row.name_label.setText(row.default_name)
        if bps is None:
            row.sub_label.setText("")
            meter.set_reading(None, "—")
            row.set_value(meter.value_text())
            self._update_stats("net")
            return
        rate = max(0.0, float(bps))
        # UI Phase 4 (D-P4-04): the ceiling DECAYS — it is the
        # maximum of the current 60-sample window, recomputed per
        # sample, never a lifetime maximum. The window is the
        # recorded history PLUS this reading (dropping the oldest
        # when full), so the ceiling is the max of the window the
        # new reading actually joins — a burst leaving the window
        # lowers the ceiling on the next sample and the bar
        # re-scales upward. The sub-label always shows the live
        # ceiling, and the stats line keeps the true window peak —
        # two different, labelled numbers.
        window = meter.raw_history() + [rate]
        if len(window) > _HISTORY_LEN:
            window = window[-_HISTORY_LEN:]
        self._net_ceiling = max(_NET_FLOOR_BPS, *window)
        # "ceiling", not "peak": this is the meter's adaptive
        # scale top, and the stats line below now carries the
        # true window peak — two different "peak"s on one meter
        # would contradict each other.
        row.sub_label.setText(f"ceiling {format_rate(self._net_ceiling)}")
        meter.set_reading(rate / self._net_ceiling, format_rate(rate),
                          value=rate)
        row.set_value(meter.value_text())
        self._update_stats("net")

    def _apply_battery(self, pct, plugged):
        meter = self.dials["battery"]
        row = self._row_by_key["battery"]
        if pct is None:
            row.name_label.setText("Battery · none")
            row.sub_label.setText("No battery detected")
            meter.set_reading(None, "—")
            row.set_value(meter.value_text())
            self._update_stats("battery")
            return
        shown = max(0.0, min(100.0, float(pct)))
        row.name_label.setText("Battery")
        if plugged:
            # UI Phase 4 (D-P4-30): no emoji in UI copy.
            row.sub_label.setText("Plugged in")
            color = QColor(OK)
        else:
            row.sub_label.setText("On battery")
            if shown <= 15:
                color = QColor(DANGER)
            elif shown <= 30:
                color = QColor(AMBER)
            else:
                color = QColor(OK)
        meter.set_reading(shown / 100.0, f"{shown:.0f}%", color=color)
        row.set_value(meter.value_text())
        self._update_stats("battery")

    def _apply_uptime(self, seconds):
        row = self._row_by_key["uptime"]
        text = _format_uptime(seconds)
        row.set_value(text if text is not None else "—")

    def _apply_procs(self, procs):
        row = self._row_by_key["procs"]
        if procs is None:
            row.set_value("—")
            return
        try:
            row.set_value(str(int(procs)))
        except (TypeError, ValueError):
            row.set_value("—")

    # ------------------------------------------------- VYUK herself (D-P4-06)

    def _service_stamp(self) -> str:
        return datetime.datetime.now().strftime("%H:%M:%S")

    def _resolve_brain_row(self) -> tuple[str, str]:
        """(value, sub-label) for the Brain service row, from the
        same resolution the Doctor's Brain check uses
        (doctor.py check_brain): the live engine from config, the
        effective model/tier from local_assets for the built-in
        backend, the Ollama model name for Ollama. Cache-only /
        disk-only reads — the Ollama reachability probe is the
        Doctor's job, never a per-tick network probe (S-P4-02)."""
        try:
            from voice_assistant.core.gui_facade import config as _cfg
        except Exception:  # noqa: BLE001
            return ("—", "Couldn't check")
        stamp = self._service_stamp()
        try:
            engine = (getattr(_cfg, "brain_engine", "local")
                      or "local").strip().lower()
        except Exception:  # noqa: BLE001
            return ("—", "Couldn't check")
        if engine != "local":
            key_state = ("key saved" if str(
                getattr(_cfg, "api_key", "") or "") else "no key saved")
            return ("Cloud · Gemini Live", f"{key_state} · as of {stamp}")
        backend = (getattr(_cfg, "local_backend", "builtin")
                   or "builtin").strip().lower()
        if backend == "ollama":
            model = str(getattr(_cfg, "ollama_model", "qwen2.5:7b") or
                        "qwen2.5:7b")
            return (f"On this PC · Ollama {model}", f"as of {stamp}")
        try:
            from voice_assistant.core.gui_facade import (
                local_assets as _assets)
            eff = _assets.effective_model()
            model_file = str(eff.get("file", "") or getattr(
                _cfg, "local_model_file",
                "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"))
            ready = bool(_assets.model_ready())
        except Exception:  # noqa: BLE001 — fall back to the config name
            model_file = str(getattr(
                _cfg, "local_model_file",
                "Qwen3-4B-Instruct-2507-Q4_K_M.gguf") or "")
            ready = False
        state = "model ready" if ready else "not downloaded yet"
        return (f"On this PC · {model_file}", f"{state} · as of {stamp}")

    def _resolve_voice_row(self) -> tuple[str, str]:
        """(value, sub-label) for the Voice service row: the first
        ready local rung, by file-only size checks (R-P4-D — this
        read must never trigger a download). Kokoro, then Piper,
        then SAPI on Windows; nothing ready reads as such."""
        stamp = self._service_stamp()
        try:
            from voice_assistant.core.gui_facade import (
                local_assets_kokoro as _kokoro)
            from voice_assistant.core.gui_facade import (
                local_assets_piperwake as _piper)
            if bool(_kokoro.kokoro_ready()):
                return ("Kokoro — files ready", f"as of {stamp}")
            if bool(_piper.piper_ready()):
                return ("Piper — files ready", f"as of {stamp}")
        except Exception:  # noqa: BLE001 — a broken read reads as
            return ("—", "Couldn't check")  # unknown, never a guess
        if os.name == "nt":
            return ("SAPI — system voice", f"as of {stamp}")
        return ("No local voice files ready", f"as of {stamp}")

    def _resolve_connection_row(self) -> tuple[str, str]:
        """(value, sub-label) for the Connection service row: cloud
        doors only when a key exists, from the last Doctor run's
        stored timestamp — never a fresh probe (D-P4-06)."""
        try:
            from voice_assistant.core.gui_facade import config as _cfg
            has_key = bool(str(getattr(_cfg, "api_key", "") or "")
                           or str(getattr(_cfg, "groq_api_key", "") or ""))
        except Exception:  # noqa: BLE001
            return ("—", "Couldn't check")
        if not has_key:
            return ("No cloud key", "doors not tested")
        last_run = ""
        if self._settings is not None:
            try:
                from voice_assistant.core.gui_facade import qsettings_get
                last_run = str(qsettings_get(
                    "doctor/last_run", settings=self._settings) or "")
            except Exception:  # noqa: BLE001
                last_run = ""
        if last_run:
            # The stored stamp may carry the run duration
            # ("24 Nov 2026, 18:14:03 (took 4 s)", D-P4-19) — the
            # Connection row names the check time, not the duration.
            when = last_run.split(" (took ")[0]
            return ("Cloud key saved", f"doors last checked {when}")
        return ("Cloud key saved", "not checked yet")

    def _apply_service_rows(self) -> None:
        """Re-read the three cached service rows on the sampler tick
        (§6.3). Each resolver is guarded: a failed read renders
        '—' + 'Couldn't check', never a stale state."""
        for key, resolver in (
                ("brain", self._resolve_brain_row),
                ("voice", self._resolve_voice_row),
                ("connection", self._resolve_connection_row)):
            row = self._service_rows.get(key)
            if row is None:
                continue
            try:
                value, sub = resolver()
            except Exception:  # noqa: BLE001
                value, sub = ("—", "Couldn't check")
            row.set_value(value)
            row.sub_label.setText(sub)

    # ------------------------------------------------- top processes (D-P4-10)

    def _apply_top_processes(self) -> None:
        """Paint the top-5 list from the sampler's read. Names are
        blanked at render time too when private mode is on (D-P4-24)
        — the sampler already blanks them at raise time; a fake
        sampler that returns names is blanked here instead. The
        counts stay either way."""
        try:
            from voice_assistant.core.gui_facade import (
                private_mode as _private_mode)
            _private = bool(_private_mode.private_mode_on())
        except Exception:  # noqa: BLE001 — a broken read reads public
            _private = False
        getter = getattr(self._sampler, "top_processes", None)
        rows = None
        if callable(getter):
            try:
                rows = getter(5)
            except Exception:  # noqa: BLE001 — the list says so
                rows = None
        if rows is None:
            for lbl in self._top_proc_labels:
                lbl.setText("—")
            if self._top_procs_box.isVisible():
                self._top_proc_labels[0].setText(
                    "Processes unavailable in this build.")
            return
        for i, lbl in enumerate(self._top_proc_labels):
            if i >= len(rows):
                lbl.setText("—")
                continue
            entry = rows[i] or {}
            name = entry.get("name") or "—"
            if _private:
                name = "—"
            cpu = entry.get("cpu_pct")
            cpu_text = ("—" if cpu is None else f"{float(cpu):.1f}%")
            rss = entry.get("rss_bytes")
            if rss is None:
                rss_text = "—"
            else:
                try:
                    rss_text = f"{float(rss) / 1e6:.1f} MB"
                except (TypeError, ValueError):
                    rss_text = "—"
            lbl.setText(f"{name} · {cpu_text} · {rss_text}")
