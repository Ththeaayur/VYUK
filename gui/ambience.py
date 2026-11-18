"""
VYUK — Ambience widgets (v5.18 EYES II: E4 + E5).

Two small, self-contained visual instruments that make the room feel
alive without ever getting in the way:

* ``WaveformRibbon`` (E4) — a slim live amplitude strip. The app's
  telemetry pushes (level, mode) pairs at ~10–20 Hz; a 33 ms timer
  that runs ONLY while the ribbon is visible keeps the wave scrolling
  between pushes and decays it gracefully toward flat when the audio
  stops, so the strip never freezes mid-gesture. Colour follows the
  mode: cyan while listening and speaking (the section-7 word, not
  the colour, tells them apart), a dim grey whisper while idle.

* ``ParticleField`` (E5) — an ambient particle background: ~70 tiny
  Aura-family motes drifting slowly with a gentle sine sway and a
  soft alpha twinkle. Deterministic (seeded RNG), normalised
  coordinates (resize-proof), transparent to the mouse, and its
  timer runs ONLY while the field is visible AND enabled AND not in
  reduced-motion mode. Disabled paints nothing at all; reduced
  motion paints one static frame. The motes are dust in the orb's
  lamplight — dim, behind the canvas — never a second light.

Neither widget knows anything about the rest of the app — the window
feeds the ribbon and layers the field; both are safe to construct
with no audio, no settings and no event loop running.
"""

import math
import random
import time
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

# Command Deck Phase E: ambience colours are the token palette.
# D2 puts Listening AND Speaking on cyan (the §7 word, not the
# colour, tells them apart); idle is the muted text tone.
from .design_system.palette import parse_color
from .room_home import PARTICLE_NIGHT_FACTOR
from .style import ACCENT, TEXT, TEXT_MUTED
from .style_tokens import PARTICLE_AURA_BLUE, PARTICLE_AURA_PALE

LISTENING_HEX = ACCENT   # cyan — her ears are open
SPEAKING_HEX = ACCENT    # cyan — her voice (D2)
IDLE_HEX = TEXT_MUTED    # dim grey — at rest

_TICK_MS = 33               # ~30 fps for both widgets
_STALE_S = 0.045            # push gap after which the ribbon decays


def _as_color(value, fallback: QColor) -> QColor:
    if value is None:
        return QColor(fallback)
    if isinstance(value, QColor):
        return QColor(value)
    color = QColor(str(value))
    return color if color.isValid() else QColor(fallback)


# ------------------------------------------------------------------
# E4 — WaveformRibbon
# ------------------------------------------------------------------

class WaveformRibbon(QWidget):
    """A slim scrolling amplitude strip (~30 px tall).

    Feed it with :meth:`push` from any telemetry source; it scrolls
    itself. ``samples()`` returns the visible history (oldest first)
    and ``current_mode()`` the mode of the most recent sample.
    """

    MAX_SAMPLES = 240
    DECAY = 0.92

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._buffer: deque = deque(maxlen=self.MAX_SAMPLES)
        self._held = 0.0
        self._mode = "idle"
        self._last_push = 0.0
        self._colors = {
            "listening": QColor(LISTENING_HEX),
            "speaking": QColor(SPEAKING_HEX),
            "idle": QColor(IDLE_HEX),
        }
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    # ---------------------------------------------------------- API

    def push(self, level: float, mode: str) -> None:
        """Append one telemetry sample. ``level`` clamps to 0..1;
        unknown modes fall back to the current mode's colour lane
        but are stored verbatim for ``current_mode()``."""
        try:
            value = float(level)
        except (TypeError, ValueError):
            value = 0.0
        value = max(0.0, min(1.0, value))
        mode = mode if mode in self._colors else (mode or "idle")
        self._buffer.append((value, mode))
        self._held = value
        self._mode = mode
        self._last_push = time.monotonic()
        self.update()

    def samples(self) -> list:
        """A copy of the visible history, oldest first, levels only."""
        return [value for value, _mode in self._buffer]

    def current_mode(self) -> str:
        return self._mode

    def is_scrolling(self) -> bool:
        return self._timer.isActive()

    def set_colors(self, listening=None, speaking=None, idle=None) -> None:
        self._colors["listening"] = _as_color(listening, self._colors["listening"])
        self._colors["speaking"] = _as_color(speaking, self._colors["speaking"])
        self._colors["idle"] = _as_color(idle, self._colors["idle"])
        self.update()

    # -------------------------------------------------------- timer

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        self._timer.stop()

    def _on_tick(self) -> None:
        # Between pushes the held level decays and keeps scrolling,
        # so the wave settles to flat instead of freezing. A fresh
        # push within the stale window already appended its sample.
        if time.monotonic() - self._last_push > _STALE_S:
            self._held *= self.DECAY
            if self._held < 0.001:
                self._held = 0.0
            self._buffer.append((self._held, self._mode))
        self.update()

    # -------------------------------------------------------- paint

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        if not self._buffer:
            return
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        # A whisper of a centre line the bars hang from.
        centre = height / 2.0
        _tr, _tg, _tb, _ta = parse_color(TEXT)
        line_color = QColor(_tr, _tg, _tb, 14)
        painter.setBrush(line_color)
        painter.drawRect(0, int(centre), width, 1)

        slot = width / self.MAX_SAMPLES
        bar_w = max(1.4, slot * 0.55)
        offset = self.MAX_SAMPLES - len(self._buffer)  # right-aligned
        max_half = height / 2.0 - 2.0
        for i, (value, mode) in enumerate(self._buffer):
            base = self._colors.get(mode, self._colors["idle"])
            x = (offset + i + 0.5) * slot - bar_w / 2.0
            half = max(0.6, value * max_half)
            if mode == "idle":
                alpha = 46 + int(50 * value)
            else:
                alpha = 70 + int(180 * value)
            # Soft halo pass, then the core bar.
            glow = QColor(base)
            glow.setAlpha(max(10, alpha // 4))
            painter.setBrush(glow)
            painter.drawRoundedRect(
                x - bar_w * 0.4, centre - half, bar_w * 1.8, half * 2,
                bar_w * 0.9, bar_w * 0.9)
            core = QColor(base)
            core.setAlpha(alpha)
            painter.setBrush(core)
            painter.drawRoundedRect(
                x, centre - half, bar_w, half * 2, bar_w / 2.0, bar_w / 2.0)
        painter.end()


# ------------------------------------------------------------------
# E5 — ParticleField
# ------------------------------------------------------------------

class ParticleField(QWidget):
    """An ambient field of slow, twinkling motes (Aura blue / pale).

    Positions live in normalised [0, 1) space, so the field is
    resize-proof and wraps seamlessly. Construction is seeded —
    every instance lays its particles out identically. Sci-fi Home
    (2026-11-16): the motes are re-tinted into the Aura family and
    dimmed (alpha 25-70, was 50-130) — dust in the orb's
    lamplight, behind the canvas. At night they dim further
    (see set_night); if they ever compete with the orb on a
    render, they go.
    """

    BASE_COUNT = 70
    _SEED = 518207
    _AURA_BLUE = PARTICLE_AURA_BLUE
    _AURA_PALE = PARTICLE_AURA_PALE

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._enabled = True
        self._reduced_motion = False
        self._night = False
        self._density = 1.0
        self._t = 0.0
        self._particles: list = []
        self._build_particles(self.BASE_COUNT)
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    # ---------------------------------------------------------- API

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._update_timer_state()
        self.update()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_reduced_motion(self, reduced: bool) -> None:
        self._reduced_motion = bool(reduced)
        self._update_timer_state()
        self.update()  # one static frame

    def set_night(self, night: bool) -> None:
        """Night dimming (sci-fi Home): the motes' alpha scales by
        the room model's night factor, so night is the same room
        dimmed. Never re-enables a disabled field."""
        self._night = bool(night)
        self.update()

    def is_night(self) -> bool:
        return self._night

    def set_density(self, scale: float) -> None:
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            return
        self._density = max(0.5, min(2.0, scale))
        self._build_particles(int(round(self.BASE_COUNT * self._density)))
        self.update()

    def is_animating(self) -> bool:
        return self._timer.isActive()

    def particle_count(self) -> int:
        return len(self._particles)

    def particle_positions(self) -> list:
        """Current particle centres in widget pixels, sway included,
        always inside the widget bounds (empty list at zero size
        collapses to the origin corner rather than escaping)."""
        w = max(1, self.width())
        h = max(1, self.height())
        out = []
        for p in self._particles:
            x = (p["x"] + math.sin(self._t * p["sway_speed"] + p["phase"])
                 * p["sway_amp"]) % 1.0
            y = (p["y"] + math.cos(self._t * p["sway_speed"] * 0.9 + p["phase"])
                 * p["sway_amp"]) % 1.0
            out.append((x * w, y * h))
        return out

    # ----------------------------------------------------- internals

    def _build_particles(self, count: int) -> None:
        rng = random.Random(self._SEED)
        particles = []
        for _ in range(count):
            particles.append({
                "x": rng.random(),
                "y": rng.random(),
                "vx": rng.uniform(-0.012, 0.012),
                "vy": rng.uniform(-0.009, 0.009),
                "radius": rng.uniform(1.0, 2.2),
                "sway_amp": rng.uniform(0.002, 0.006),
                "sway_speed": rng.uniform(0.10, 0.35),
                "phase": rng.uniform(0.0, math.tau),
                "twinkle_speed": rng.uniform(0.4, 1.1),
                "alpha": rng.uniform(25, 70),
                "rgb": self._AURA_BLUE if rng.random() < 0.7 else self._AURA_PALE,
            })
        self._particles = particles

    def _update_timer_state(self) -> None:
        active = (self.isVisible() and self._enabled
                  and not self._reduced_motion)
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._update_timer_state()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        self._timer.stop()

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self.update()

    def _on_tick(self) -> None:
        dt = _TICK_MS / 1000.0
        self._t += dt
        for p in self._particles:
            p["x"] = (p["x"] + p["vx"] * dt) % 1.0
            p["y"] = (p["y"] + p["vy"] * dt) % 1.0
        self.update()

    # -------------------------------------------------------- paint

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        if not self._enabled:
            return
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        positions = self.particle_positions()
        for p, (x, y) in zip(self._particles, positions):
            twinkle = 0.6 + 0.4 * math.sin(
                self._t * p["twinkle_speed"] + p["phase"])
            alpha = p["alpha"] * twinkle
            if self._night:
                alpha *= PARTICLE_NIGHT_FACTOR
            r, g, b = p["rgb"]
            color = QColor(r, g, b)
            color.setAlpha(int(alpha))
            painter.setBrush(color)
            radius = p["radius"]
            painter.drawEllipse(
                int(x - radius), int(y - radius),
                int(radius * 2), int(radius * 2))
        painter.end()
