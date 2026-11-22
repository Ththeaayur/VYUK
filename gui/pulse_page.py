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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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

    def __init__(self, sampler=None, parent=None):
        super().__init__(parent)
        self._sampler = sampler if sampler is not None else _PsutilSampler()
        self._latest: dict = _blank_sample()
        self._net_ceiling = _NET_FLOOR_BPS
        self._user_paused = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
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
        header.addStretch(1)
        root.addLayout(header)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(0)
        root.addLayout(self._rows_layout, 1)

        # Static footnote (the mockup's "bar" wording, not "dial"):
        # what the bars are and when they tick.
        # Wiring Build 7: label disambiguation only — probes, not cards.
        self.bars_note = QLabel(
            "Live bars, sampled once a second while this page is visible. "
            "System probes only — no news or entity cards here.")
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

        self._rows: list[_MetricRow] = []
        self.dials: dict[str, BarMeter] = {}
        self._row_by_key: dict[str, _MetricRow] = {}
        for key, name, color in self._ROW_DEFS:
            row = _MetricRow(key, name, color)
            self._rows.append(row)
            self._row_by_key[key] = row
            if row.meter is not None:
                self.dials[key] = row.meter
            self._rows_layout.addWidget(row.frame)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
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
            for row in getattr(self, "_rows", []):
                row.set_text_colors(text or TEXT,
                                    text_muted or TEXT_MUTED)

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
        self._apply(sample)

    # ------------------------------------------------------------- lifecycle

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self._user_paused:
            return  # the pause survives being hidden and re-shown
        self.sample_now()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        self._timer.stop()

    # ------------------------------------------------------------- internals

    def _on_pause_toggled(self, checked: bool):
        self._user_paused = bool(checked)
        self.live_label.setText("Paused" if self._user_paused else "Live")
        if self._user_paused:
            self._timer.stop()
        elif self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def _apply(self, sample: dict):
        for key in ("cpu", "mem", "disk"):
            self._apply_percent(key, sample.get(key))
        self._apply_network(sample.get("net_bps"))
        self._apply_battery(sample.get("battery_pct"),
                            sample.get("battery_plugged"))
        self._apply_uptime(sample.get("uptime_s"))
        self._apply_procs(sample.get("procs"))
        self._apply_word(sample)

    def _apply_word(self, sample: dict):
        word, level = _metric_word(sample)
        color = (DANGER if level == "error"
                 else AMBER if level == "warning" else TEXT_MUTED)
        self.word_label.setText(word)
        self.word_label.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 600;")

    def _update_stats(self, key: str):
        """Refresh a row's avg/peak line from its meter's recorded
        history. Hidden until at least two readings exist — a single
        reading has no honest 'average window' to report."""
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
        if key == "net":
            text = f"avg {format_rate(avg)} · peak {format_rate(peak)}"
        else:
            text = f"avg {avg * 100:.0f}% · peak {peak * 100:.0f}%"
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
        if rate > self._net_ceiling:
            self._net_ceiling = rate
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
            row.sub_label.setText("⚡ Plugged in")
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
