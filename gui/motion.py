"""VYUK motion vocabulary + frame-time watchdog (v5.51 F-3 / D-3).

F-3 — ONE home for the spring / easing vocabulary
--------------------------------------------------
Before v5.51 the motion constants lived as literals scattered
through the painters and window mixins: the same ``0.40`` attack
in the visualizer and the mini orb, ``OutCubic`` re-spelled at
every animation site, durations (120 / 180 / 220 / 400 / 450 /
720 / 760 ms) re-typed per call. This module names them once:

  * Smoothing factors are per-legacy-frame factors — the value
    fed to ``OrbVisualizer._dt_k(k, s)`` (or applied raw in the
    mini orb's fixed-step tick). They are NOT durations.
  * Spring pairs are ``(stiff, damp)`` for ``_spring_step`` /
    ``_spring_step_dt``: stiff is the per-frame pull toward the
    target, damp the per-frame velocity retention.
  * Easing presets are ``QEasingCurve.Type`` members, applied via
    ``anim.setEasingCurve(EASE_*)``.
  * Durations are milliseconds for ``QPropertyAnimation``.

The numeric values are Aura's, extracted verbatim from
gui/visualizer.py (the canonical painter) at v5.50 — F-3 is a
vocabulary unification, NOT a retune: every routed site keeps the
exact number it had, now spelled through this module. The only
values that changed anywhere are the documented adoptions in the
v5.51 F-3 report (ad-hoc literals with no vocabulary equivalent,
moved to the NEAREST vocabulary value).

D-3 — FrameWatchdog
-------------------
A small pure decision class (no Qt state, no timers): feed it
frame dt samples in milliseconds and it decides a quality level
for the orb renderer.

  level 0 — full quality: repaint at the renderer's adaptive
            rate (60 fps live / 30 fps idle), springs unscaled.
  level 1 — reduced repaint: the interval is multiplied by
            ``interval_factor`` (2 — 60 fps toward 30 fps).
  level 2 — reduced repaint AND slowed springs: spring steps are
            integrated with ``spring_factor`` (0.6) of the step,
            so surges settle lazier under sustained load.

Thresholds (documented, hysteresis by construction):
  * dt is tracked as an EMA (alpha 0.15) against the live target
    interval (``target_ms``, 16 ms for the 60 fps rate).
  * Step DOWN when the EMA exceeds ``DOWN_FACTOR`` (1.8x) of the
    target for ``DOWN_SAMPLES`` (10) consecutive samples.
  * Step UP only when the EMA falls below ``UP_FACTOR`` (1.15x)
    of the target for ``UP_SAMPLES`` (45) consecutive samples —
    the up threshold sits far below the down threshold and needs
    a much longer run, so a borderline stream (EMA hovering
    between the thresholds, or alternating good/bad frames)
    resets both counters and cannot oscillate the level.
  * One step at a time, with a ``COOLDOWN_SAMPLES`` (30) pause
    between steps, so recovery from level 2 visibly passes
    through level 1.
  * Reduced motion: ``set_reduced_motion(True)`` installs a
    quality ceiling — the effective level never drops below 1
    (quality never rises above the reduced-rate tier) while it
    is set, whatever the samples say. Clearing it lets the
    watchdog earn its way back to level 0 by the normal up-rule.
"""

from PySide6.QtCore import QEasingCurve

# ------------------------------------------------------------- smoothing
# Per-legacy-frame smoothing factors (see header). The energy
# pair is the Aura analyser feel: fast attack, graceful
# release — values rise quickly toward louder targets and fall
# back slowly.
SPRING_ENERGY_ATTACK = 0.40    # energy feed, rising (visualizer + mini orb)
SPRING_ENERGY_RELEASE = 0.12   # energy feed, falling
SPECTRUM_ATTACK = 0.45         # spectrum bands / EQ bars, rising
SPECTRUM_RELEASE = 0.18        # spectrum bands / EQ bars, falling
PALETTE_CROSSFADE = 0.06       # palette colours toward their targets
MOTION_CROSSFADE = 0.05        # per-state motion params (all orb styles),
                               # the Aura orbit-scale tighten, mini currents
SWELL_DECAY = 0.10             # F6 connecting swell, early-exit decay
ACK_FLARE_DECAY = 0.22         # v5.51 E-1 ack flare: per-frame decay of the
                               # visualizer's additive receipt pulse —
                               # (1-0.22)^12 ~= 0.05, so the flare lives
                               # ~12 legacy frames ~= the ack wav's 0.20 s
RIM_BREATH_ATTACK = 0.20       # F6 speaking rim breath, rising
RIM_BREATH_RELEASE = 0.09      # F6 speaking rim breath, falling

# ------------------------------------------------------------- springs
# (stiff, damp) pairs for _spring_step / _spring_step_dt.
SPRING_ENERGY_ATTACK_PAIR = (0.30, 0.58)    # energy spring, target above value
SPRING_ENERGY_RELEASE_PAIR = (0.05, 0.84)   # energy spring, target below value
SPRING_SCALE_PAIR = (0.055, 0.84)           # core scale ("surge") spring
SPRING_SURGE_PAIR = SPRING_SCALE_PAIR
SPRING_BRIGHTNESS_PAIR = (0.07, 0.82)       # core brightness ("gauge") spring
SPRING_GAUGE_PAIR = SPRING_BRIGHTNESS_PAIR

# ------------------------------------------------------------- easing
# The house easing: every panel / dialog / splash animation in
# the window mixins eases out cubic — fast in, gentle landing,
# no overshoot. Named per use so a future retune is one edit.
EASE_STANDARD = QEasingCurve.Type.OutCubic
EASE_PANEL = EASE_STANDARD       # page transitions, chat bubbles
EASE_DIALOG = EASE_STANDARD      # command palette, splash, window fades
EASING_PRESETS = {
    "standard": EASE_STANDARD,
    "panel": EASE_PANEL,
    "dialog": EASE_DIALOG,
}

# ------------------------------------------------------------- durations (ms)
DURATION_PALETTE_FADE_MS = 120   # command palette window fade-in
DURATION_QUICK_MS = 180          # chat bubble entrance, splash fade-in
DURATION_PAGE_MS = 220           # page transition fade + rise
DURATION_DISMISS_MS = 220        # splash dismiss crossfade
DURATION_TAGLINE_MS = 400        # splash tagline fade
DURATION_WINDOW_FADE_MS = 450    # main window launch fade-in
DURATION_IGNITE_MS = 720         # splash orb-mark ignition
DURATION_WORDMARK_MS = 760       # splash wordmark draw-on
DURATION_ACK_MS = 200            # v5.51 E-1: the ack earcon's length — the
                                 # scale the orb flare's decay is tuned to


class FrameWatchdog:
    """Pure frame-time watchdog — see the module header (D-3).

    Feed measured frame times with :meth:`feed`; read the
    effective quality with :attr:`level` / :attr:`quality`, and
    the renderer's knobs with :meth:`interval_factor` /
    :meth:`spring_factor`.
    """

    TARGET_MS = 16.0          # the live 60 fps interval
    DOWN_FACTOR = 1.8         # EMA > 1.8x target -> overrun
    UP_FACTOR = 1.15          # EMA < 1.15x target -> headroom
    EMA_ALPHA = 0.15
    DOWN_SAMPLES = 10         # consecutive overrun samples to step down
    UP_SAMPLES = 45           # consecutive headroom samples to step up
    COOLDOWN_SAMPLES = 30     # samples between successive steps
    MAX_LEVEL = 2
    SPRING_SLOW_FACTOR = 0.6  # level-2 spring step scale

    def __init__(self, target_ms: float = TARGET_MS):
        self.target_ms = float(target_ms) if target_ms else self.TARGET_MS
        self._level = 0
        self._ema_ms: float | None = None
        self._down_run = 0
        self._up_run = 0
        self._cooldown = 0
        self._reduced_motion = False
        self.samples = 0

    # ---------------------------------------------------------- config
    def set_target_ms(self, target_ms: float):
        """Point the thresholds at a new live interval (the orb's
        adaptive rate is 16 ms live / 33 ms idle)."""
        try:
            value = float(target_ms)
        except (TypeError, ValueError):
            return
        if value > 0:
            self.target_ms = value

    def set_reduced_motion(self, reduced: bool):
        """Install / clear the reduced-motion quality ceiling."""
        self._reduced_motion = bool(reduced)

    def is_reduced_motion(self) -> bool:
        return self._reduced_motion

    # ---------------------------------------------------------- state
    @property
    def ema_ms(self) -> float | None:
        return self._ema_ms

    @property
    def level(self) -> int:
        """Effective degradation level (0 full, 1 reduced rate,
        2 reduced rate + slowed springs). Under reduced motion
        the effective level never falls below 1."""
        if self._reduced_motion:
            return max(self._level, 1)
        return self._level

    @property
    def raw_level(self) -> int:
        """The level the samples alone have earned (pre-ceiling)."""
        return self._level

    @property
    def quality(self) -> str:
        return ("full", "reduced_rate",
                "reduced_rate_slow_springs")[self.level]

    def interval_factor(self) -> int:
        """Repaint-interval multiplier for the effective level."""
        return 1 if self.level == 0 else 2

    def spring_factor(self) -> float:
        """Spring-step scale for the effective level (level 2
        only; 1.0 = springs exactly as tuned)."""
        return self.SPRING_SLOW_FACTOR if self.level >= 2 else 1.0

    def reset(self):
        self._level = 0
        self._ema_ms = None
        self._down_run = 0
        self._up_run = 0
        self._cooldown = 0
        self.samples = 0

    # ---------------------------------------------------------- feed
    def feed(self, dt_ms: float) -> int:
        """Consume one frame-time sample (ms); return the
        effective level after the decision."""
        try:
            dt = float(dt_ms)
        except (TypeError, ValueError):
            return self.level
        if dt <= 0:
            return self.level
        self.samples += 1
        if self._ema_ms is None:
            self._ema_ms = dt
        else:
            self._ema_ms += (dt - self._ema_ms) * self.EMA_ALPHA
        if self._cooldown > 0:
            self._cooldown -= 1
        down_thr = self.target_ms * self.DOWN_FACTOR
        up_thr = self.target_ms * self.UP_FACTOR
        if self._ema_ms > down_thr:
            self._down_run += 1
            self._up_run = 0
        elif self._ema_ms < up_thr:
            self._up_run += 1
            self._down_run = 0
        else:
            # The hysteresis band: neither overrun nor headroom —
            # both runs reset, so borderline input holds the level.
            self._down_run = 0
            self._up_run = 0
        if (self._cooldown == 0 and self._down_run >= self.DOWN_SAMPLES
                and self._level < self.MAX_LEVEL):
            self._level += 1
            self._down_run = 0
            self._cooldown = self.COOLDOWN_SAMPLES
        elif (self._cooldown == 0 and self._up_run >= self.UP_SAMPLES
                and self._level > 0):
            self._level -= 1
            self._up_run = 0
            self._cooldown = self.COOLDOWN_SAMPLES
        return self.level


__all__ = [
    "SPRING_ENERGY_ATTACK", "SPRING_ENERGY_RELEASE",
    "SPECTRUM_ATTACK", "SPECTRUM_RELEASE",
    "PALETTE_CROSSFADE", "MOTION_CROSSFADE",
    "SWELL_DECAY", "RIM_BREATH_ATTACK", "RIM_BREATH_RELEASE",
    "ACK_FLARE_DECAY",
    "SPRING_ENERGY_ATTACK_PAIR", "SPRING_ENERGY_RELEASE_PAIR",
    "SPRING_SCALE_PAIR", "SPRING_SURGE_PAIR",
    "SPRING_BRIGHTNESS_PAIR", "SPRING_GAUGE_PAIR",
    "EASE_STANDARD", "EASE_PANEL", "EASE_DIALOG", "EASING_PRESETS",
    "DURATION_PALETTE_FADE_MS", "DURATION_QUICK_MS", "DURATION_PAGE_MS",
    "DURATION_DISMISS_MS", "DURATION_TAGLINE_MS",
    "DURATION_WINDOW_FADE_MS", "DURATION_IGNITE_MS",
    "DURATION_WORDMARK_MS", "DURATION_ACK_MS",
    "FrameWatchdog",
]
