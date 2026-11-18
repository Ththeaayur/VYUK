"""OrbVisualizer state mixin: state/theme/timer/spring/palette.

Split from gui/visualizer.py (GUI split #7, move only). Holds the
widget's state: construction, theme/palette, timers, springs, the
animation frame, and the colour helpers. Paint lives in
visualizer_paint.py; the composed class lives in visualizer.py.
"""

import math
import random
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor

from .motion import (
    ACK_FLARE_DECAY,
    MOTION_CROSSFADE,
    PALETTE_CROSSFADE,
    RIM_BREATH_ATTACK,
    RIM_BREATH_RELEASE,
    SPECTRUM_ATTACK,
    SPECTRUM_RELEASE,
    SPRING_ENERGY_ATTACK,
    SPRING_ENERGY_ATTACK_PAIR,
    SPRING_ENERGY_RELEASE,
    SPRING_ENERGY_RELEASE_PAIR,
    SWELL_DECAY,
    FrameWatchdog,
)
from .orb_sphere import SphereState
from .visualizer_glass import GlassState


# theme name -> (highlight, mid, edge)
THEMES: dict[str, tuple[str, str, str]] = {
    "Cyan": ("#E6FBFF", "#35D6FF", "#3D8BFF"),
    "Sunset": ("#FFE8D6", "#FF9A7A", "#FF7EB6"),
    "Violet": ("#EFE9FF", "#A78BFA", "#7C8CFF"),
    "Mono": ("#FFFFFF", "#C9CED6", "#8A919C"),
    "Amber": ("#FFF1D6", "#FFB020", "#FF6A00"),
    "Crimson": ("#FFE3E6", "#FF5468", "#FD465A"),
}


def _rgb(hex_color: str) -> list[float]:
    c = QColor(hex_color)
    return [float(c.red()), float(c.green()), float(c.blue())]


class OrbStateMixin:
    """State half of OrbVisualizer (GUI split #7).

    Construction, theme/palette, timers, springs, animation.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)

        self.state = "IDLE"
        self.theme_name = "Cyan"
        # Custom palette override (v5.17): a (highlight, mid, edge)
        # hex triple installed by set_custom_theme(). While set, it
        # replaces THEMES[self.theme_name] at the single palette
        # resolution point (_target_palette); set_theme() clears it.
        self._custom_theme: tuple[str, str, str] | None = None
        self.orb_style = "Aura"

        # ---- v5.43 face: muted + reduced motion --------------------
        # _muted is a presentation flag only: it never rewrites
        # self.state or the stored audio feed, so clearing it
        # restores exactly the behaviour the orb had before.
        # _reduced_motion mirrors ambience.ParticleField's contract
        # (a stored flag, pushed by the main window): it freezes
        # the ambient accumulators in _animate_frame.
        self._muted = False
        self._reduced_motion = False

        # Travelling-dot phase (degrees) for the compact mark's orbit
        # ring, shared with the big orb so state speeds feel identical.
        # Advanced in _animate_frame.
        self._orbit = 0.0


        self.target_energy = 0.0
        self.current_energy = 0.0
        # ---- v5.51 E-1: instant-ack flare ----------------------------
        # An ADDITIVE transient, not a state: ack_flare() sets it to
        # 1.0 and _animate_frame decays it to exactly 0 at
        # ACK_FLARE_DECAY (~the ack wav's 0.20 s). The paint pass
        # reads it as a brief energy/rim lift riding ON TOP of
        # whatever the F6 state machine is doing — the base state,
        # its springs and the stored audio feed are never touched,
        # so state signatures are undisturbed and the flare always
        # fully decays away.
        self._ack_pulse = 0.0
        self.spectrum_bands: list[float] = [0.0] * 16
        self.display_bands: list[float] = [0.0] * 16
        # Shared analyser smoothing: per-bar smoothed amplitudes
        # (always the canonical 48 bars). Bars attack fast and release
        # slowly in _animate_frame — a real analyser feel, no snapping
        # when telemetry delivers fresh band values.
        self._bar_values: list[float] = [0.0] * 48

        # ---- Orb v5.17 Aura state ------------------------------------
        # Crossfaded motion parameters [core brightness, current
        # swirl, core pulse] eased in _animate_frame at the shared
        # rate, plus a spring-driven copy of the voice energy (same
        # attack/release constants as the shared energy spring) and
        # the internal current blobs: [angle deg, orbit radius
        # fraction, deg/frame (sign = direction), blob radius
        # fraction, twinkle phase]. The paint pass only reads them.
        self._aur_mp: list[float] = [0.62, 0.35, 0.25]
        self._aur_energy = 0.0
        self._aur_energy_vel = 0.0
        # B-R1-5: the LerSent liquid-glass state — eased toward the
        # state's glass targets in _animate_frame from the SAME
        # state + smoothed energy the orb already has (never a
        # second state source); the paint pass only reads it.
        self._glass = GlassState()
        # R7-2: the voice-reactive particle sphere — eased in
        # _animate_frame from the SAME smoothed energy the orb
        # already has (never a second energy source); the paint
        # pass only reads it.
        self._sphere = SphereState()
        _aurng = random.Random(23)
        self._aur_blobs = [
            [_aurng.uniform(0, 360), _aurng.uniform(0.26, 0.50),
             _aurng.uniform(0.08, 0.22) * (-1.0 if i % 2 else 1.0),
             _aurng.uniform(0.36, 0.50), _aurng.uniform(0, 6.283)]
            for i in range(4)
        ]
        # ---- v5.44 F6: one signature per state (Aura only) -----------
        # THINKING tightens the currents: _aur_orbit_scale crossfades
        # the blobs' orbit radii toward the state's target (1.0 =
        # the v5.17 spread, ~0.55 = pulled in close) while the swirl
        # target quickens their orbit — same blobs, same language.
        # CONNECTING fires exactly ONE core swell per entry:
        # _aur_swell_phase runs -1 (idle) -> 0..1 over ~150 frames
        # and _aur_swell is its smooth envelope; _aur_swell_count
        # counts entries so the single-swell contract is provable.
        # SPEAKING breathes the fresnel rim on the voice itself:
        # _aur_rim_breath (0..1) tracks the Aura energy spring while
        # speaking and decays back to 0 in every other state — no
        # metronome anywhere. All three are ambient traits: while
        # _ambient_frozen() (muted / reduced motion) their updates
        # hold, exactly like the blob angles themselves.
        self._aur_orbit_scale = 1.0
        self._aur_rim_breath = 0.0
        self._aur_swell = 0.0
        self._aur_swell_phase = -1.0
        self._aur_swell_count = 0
        self._aur_prev_state = self.state

        # ---- Listening ripples (v5.16, all styles) ------------------
        # Expanding rings shed while LISTENING, spawned at a rate set
        # by the mic level (current_energy — the smoothed
        # update_audio_data feed). Each entry is [age 0..1, strength
        # 0..1]; _animate_frame ages/spawns them, the paint pass
        # draws them under the orb body.
        self._ripples: list[list[float]] = []
        self._ripple_clock = 0.0

        self.breathe_phase = 0.0
        self.swirl_phase = 0.0
        self.ripple_phase = 0.0

        # Phase 3: micro-particles drifting inside the sphere
        # (angle, distance fraction, size, speed, twinkle phase)
        _rng = random.Random(7)
        self._particles = [
            (_rng.uniform(0, 6.283), _rng.uniform(0.15, 0.8), _rng.uniform(1.0, 2.4),
             _rng.uniform(0.3, 1.0), _rng.uniform(0, 6.283))
            for _ in range(26)
        ]

        # Interpolated palette (floats) for crossfades
        hi, mid, edge = THEMES[self.theme_name]
        self._cur_hi, self._cur_mid, self._cur_edge = _rgb(hi), _rgb(mid), _rgb(edge)
        self._glow_alpha = 60.0

        # ---- v5.45 F4: power discipline -------------------------
        # _frame_dt_nominal is the legacy 60 fps step: direct
        # _animate_frame() calls (suites, harnesses) keep exactly
        # the pre-F4 motion, while the real timer feeds measured
        # elapsed dt through _on_timer_tick.
        self._frame_dt_nominal = 1.0 / 60.0
        self._last_frame_dt = self._frame_dt_nominal
        self._last_timer_tick: float | None = None
        self._hidden_paused = False
        # v5.51 D-3: frame-time watchdog — fed measured dt from
        # _on_timer_tick only (direct _animate_frame calls from
        # suites/harnesses never feed it, so their motion is the
        # tuned motion, bit for bit).
        self._watchdog = FrameWatchdog()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start(self._desired_interval_ms())

    # ------------------------------------------------------------------ API
    def set_state(self, state: str):
        self.state = state.upper()
        self._apply_timer_rate()

    def set_muted(self, muted: bool):
        """Install / clear the MUTED presentation (v5.43 F2).

        Driven by the main window from the app's mic-mute flag.
        While muted the orb is dimmed and stilled and its energy
        reads ~0 — regardless of the session state — and the mini
        orb's slash badge is drawn over the body. The session state
        itself is NOT rewritten, so unmuting returns the orb to
        exactly its prior state behaviour.
        """
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.update()

    def is_muted(self) -> bool:
        return self._muted

    def set_reduced_motion(self, reduced: bool):
        """Honour the reduced-motion setting (v5.43 F5) — the same
        contract as ambience.ParticleField.set_reduced_motion: a
        stored flag the main window pushes from its
        ``motion/reduced`` gate. While set, the ambient rotation /
        swirl / current phases freeze; the energy-driven core and
        the palette state changes keep working."""
        reduced = bool(reduced)
        if reduced == self._reduced_motion:
            return
        self._reduced_motion = reduced
        # v5.51 D-3: reduced motion caps the watchdog's quality —
        # it may never step back up above the reduced-rate tier
        # while reduced motion holds.
        self._watchdog.set_reduced_motion(reduced)
        self.update()  # one frame in the new mode

    def is_reduced_motion(self) -> bool:
        return self._reduced_motion

    def _ambient_frozen(self) -> bool:
        """True when ambient motion must hold still: muted (F2 —
        stilled) or reduced motion (F5 — frozen phases)."""
        return self._muted or self._reduced_motion

    def set_theme(self, theme_name: str):
        if theme_name in THEMES:
            self.theme_name = theme_name
            # A named palette stays exact: choosing one retires any
            # custom override.
            self._custom_theme = None

    def set_custom_theme(self, hi: str, mid: str, edge: str):
        """Install a custom palette triple (highlight, mid, edge).

        Wins over THEMES[self.theme_name] wherever the palette
        resolves (_target_palette), so the crossfade eases toward
        it exactly like a theme change. All three values must be
        colours QColor accepts (hex strings, or QColor objects);
        if any is invalid the call is ignored and the current
        palette — named or custom — is left untouched. Cleared by
        the next set_theme().
        """
        triple = []
        for value in (hi, mid, edge):
            if not isinstance(value, (str, QColor)):
                return
            col = QColor(value)
            if not col.isValid():
                return
            triple.append(col.name())
        self._custom_theme = (triple[0], triple[1], triple[2])

    def set_orb_style(self, style: str):
        # Aura-only (owner decision 2026-11-01): any requested style
        # resolves to Aura, so a saved Apex/Reactor/unknown value can
        # never re-select a deleted style at runtime.
        self.orb_style = "Aura"

    def ack_flare(self):
        """v5.51 (E-1/X-3): the instant-acknowledgement flare.

        A synchronous, additive pulse input — deliberately NOT
        update_audio_data: that is the live analyser feed (its
        stored target is overwritten by real audio and zeroed
        under mute), so a receipt transient parked there would
        corrupt the feed and never decay on its own. This pulse
        lives beside the feed, is spent by _animate_frame's decay,
        and leaves every state signature exactly as it was."""
        self._ack_pulse = 1.0
        self.update()

    @property
    def ack_pulse(self) -> float:
        """The live ack-flare pulse, 1.0 at receipt decaying to 0."""
        return self._ack_pulse

    def update_audio_data(self, energy: float, bands=None):
        self.target_energy = max(0.0, min(energy, 1.0))
        if bands and len(bands) >= 16:
            self.spectrum_bands = list(bands[:16])

    def accent_color(self) -> QColor:
        """Current (crossfaded) mid colour — used for the window's ambient glow."""
        return QColor(int(self._cur_mid[0]), int(self._cur_mid[1]), int(self._cur_mid[2]))

    # ------------------------------------------------- v5.45 F4 helpers
    def _desired_interval_ms(self) -> int:
        """Adaptive rate: ~20 fps while IDLE / DISCONNECTED (a slow
        breathe does not need 30 — Fix 6, 2026-11-05: the breathe
        is dt-corrected in _animate_frame, so the motion is
        identical, just fewer frames; lab: orb ~1.4 ms/frame, so
        idle falls from ~4.5% to ~2.8% of one core), ~60 fps in
        live states."""
        return 50 if self._is("IDLE", "DISCONNECTED") else 16

    def frame_report(self):
        """Fix 6: the GUI-thread responsiveness read for the
        briefing line — {"ema_ms": float|None, "quality": str}
        from the frame watchdog, or None when there is no
        watchdog. ema_ms is None until frames have been fed."""
        wd = getattr(self, "_watchdog", None)
        if wd is None:
            return None
        return {"ema_ms": wd.ema_ms, "quality": wd.quality}

    def _apply_timer_rate(self):
        try:
            # v5.51 D-3: the watchdog's reduced-rate tier doubles
            # the interval (60 fps toward 30 on the live rate).
            want = (self._desired_interval_ms()
                    * self._watchdog.interval_factor())
            if self.timer.interval() != want:
                self.timer.setInterval(want)
        except Exception:  # noqa: BLE001 — pre-timer construction
            pass

    def _on_timer_tick(self):
        now = time.monotonic()
        if self._last_timer_tick is None:
            dt = self.timer.interval() / 1000.0
        else:
            dt = now - self._last_timer_tick
        self._last_timer_tick = now
        dt = max(0.001, min(0.10, dt))
        # v5.51 D-3: judge this frame against the state's own
        # adaptive interval (16 ms live / 33 ms idle), then let a
        # level change retime the timer for the NEXT frame.
        before = self._watchdog.level
        self._watchdog.set_target_ms(self._desired_interval_ms())
        self._watchdog.feed(dt * 1000.0)
        if self._watchdog.level != before:
            self._apply_timer_rate()
        self._animate_frame(dt=dt)

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        super().hideEvent(event)
        # F4: no painting a hidden orb — the mini orb is the
        # visible face while the main window is hidden.
        if self.timer.isActive():
            self._hidden_paused = True
            self.timer.stop()

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self._hidden_paused:
            self._hidden_paused = False
            self._last_timer_tick = None
            self.timer.start(self._desired_interval_ms())
        elif not self.timer.isActive():
            self._last_timer_tick = None
            self.timer.start(self._desired_interval_ms())

    @staticmethod
    def _dt_k(k_frame: float, s: float) -> float:
        """Per-frame smoothing factor -> dt-correct equivalent.

        Applying (1-k) retention s times in a row must equal one
        step at the new rate: k_dt = 1 - (1-k)**s, where s is the
        step length in legacy 60 fps frames. Exact at s == 1."""
        if s == 1.0:
            return k_frame
        return 1.0 - (1.0 - k_frame) ** s

    @staticmethod
    def _spring_step(val: float, vel: float, target: float,
                     stiff: float, damp: float):
        """One semi-implicit spring step in frame units. damp is the
        per-frame velocity retention; paired with stiff it lands near
        critically damped — quick to arrive, no ringing."""
        vel = (vel + (target - val) * stiff) * damp
        return val + vel, vel

    @classmethod
    def _spring_step_dt(cls, val, vel, target, stiff, damp, s):
        """_spring_step integrated over s legacy frames.

        Whole frames replay the exact legacy step (so a 30 fps
        step of s == 2 lands bit-close to two 60 fps steps); a
        fractional remainder uses the scaled first-order step."""
        if s == 1.0:
            return cls._spring_step(val, vel, target, stiff, damp)
        remaining = float(s)
        while remaining > 1e-9:
            step = min(1.0, remaining)
            if step == 1.0:
                val, vel = cls._spring_step(val, vel, target, stiff, damp)
            else:
                vel = (vel + (target - val) * stiff * step) * (damp ** step)
                val = val + vel * step
            remaining -= step
        return val, vel

    def _spring_s(self, s: float) -> float:
        """The step the springs integrate over: at watchdog
        level 2 (v5.51 D-3) springs run at the documented slow
        factor; at every other level this is s itself, unchanged."""
        factor = self._watchdog.spring_factor()
        return s if factor == 1.0 else s * factor

    # ------------------------------------------------------------- internal
    def _is(self, *names) -> bool:
        return any(n in self.state for n in names)

    def _target_palette(self):
        if self._muted:
            # v5.43 F2: MUTED wins over every session state — the
            # face must not wear a live palette while the mic is
            # muted. The resolved theme palette (custom triple
            # included) is pulled toward its own luminance and
            # dimmed; unmuting returns to the normal resolution
            # below, untouched.
            hi, mid, edge = self._custom_theme or THEMES[self.theme_name]

            def _dim(rgb: list[float]) -> list[float]:
                lum = 0.35 * rgb[0] + 0.45 * rgb[1] + 0.20 * rgb[2]
                return [c * 0.38 + lum * 0.30 for c in rgb]

            return _dim(_rgb(hi)), _dim(_rgb(mid)), _dim(_rgb(edge)), 16.0
        if self._is("ERROR"):
            return _rgb("#FFBEBE"), _rgb("#F06E6E"), _rgb("#AA3746"), 40.0
        if self._is("DISCONNECTED"):
            # UI rethink Build 1: a disconnected orb keeps a dim
            # blue core, never grey — the grey ball read as dead,
            # and at night it was the brightest thing on the page.
            # The Aura (Cyan) triple dimmed toward dark blue; the
            # state itself is still named in words (state line,
            # status word), never by the orb's colour alone.
            return _rgb("#8CAFC8"), _rgb("#186073"), _rgb("#1B3F73"), 22.0
        # The single palette resolution point: a custom triple
        # (set_custom_theme) replaces the named theme here, and
        # every downstream modulation (the THINKING violet blend,
        # the crossfade in _animate_frame) treats it identically.
        hi, mid, edge = self._custom_theme or THEMES[self.theme_name]
        hi_l, mid_l, edge_l = _rgb(hi), _rgb(mid), _rgb(edge)
        if self._is("THINKING"):
            violet = _rgb("#A78BFA")
            mid_l = [(a + b) / 2 for a, b in zip(mid_l, violet)]
        return hi_l, mid_l, edge_l, 65.0 if self._is("THINKING") else 60.0

    @staticmethod
    def _lerp(cur: list[float], target: list[float], k: float) -> list[float]:
        return [c + (t - c) * k for c, t in zip(cur, target)]

    def _animate_frame(self, dt: float | None = None):
        # v5.45 F4: dt-driven. dt is seconds since the previous
        # frame; None means one legacy 60 fps step (direct calls).
        # s is the step in legacy frames — every rate / phase /
        # easing below is expressed through s (or _dt_k / the dt
        # spring), so equal wall-times land on equal states at
        # any frame rate.
        if dt is None:
            dt = self._frame_dt_nominal
        try:
            dt = float(dt)
        except Exception:  # noqa: BLE001
            dt = self._frame_dt_nominal
        dt = max(0.0, min(0.25, dt))
        if dt == 0.0:
            return
        s = dt * 60.0
        self._last_frame_dt = dt
        # v5.51 E-1: spend the ack flare. It decays in EVERY state
        # and even while ambient motion is frozen — a receipt cue
        # must be brief and must always fully decay, never park.
        if self._ack_pulse > 0.0:
            self._ack_pulse += (0.0 - self._ack_pulse) * \
                self._dt_k(ACK_FLARE_DECAY, s)
            if self._ack_pulse < 0.01:
                self._ack_pulse = 0.0
        # v5.43 F2/F5: one freeze flag for every ambient accumulator
        # below. Muted also zeroes the energy FEED for this frame —
        # the stored target_energy / spectrum_bands are left exactly
        # as update_audio_data delivered them, so unmuting resumes
        # the feed with no state to repair.
        frozen = self._ambient_frozen()
        feed_energy = 0.0 if self._muted else self.target_energy
        # Asymmetric smoothing throughout: fast attack, graceful
        # release — values rise quickly toward louder targets and fall
        # back slowly, like a real analyser / a breathing core pulse.
        e_k = self._dt_k(SPRING_ENERGY_ATTACK if feed_energy > self.current_energy else SPRING_ENERGY_RELEASE, s)
        self.current_energy += (feed_energy - self.current_energy) * e_k
        for i in range(16):
            t = 0.0 if self._muted else (
                self.spectrum_bands[i] if i < len(self.spectrum_bands) else 0.0)
            cur = self.display_bands[i]
            k = self._dt_k(SPECTRUM_ATTACK if t > cur else SPECTRUM_RELEASE, s)
            self.display_bands[i] = cur + (t - cur) * k

        # Shared analyser smoothing: advance the 48 per-bar values toward their
        # targets (cyclic linear interpolation over the display bands,
        # the same mapping the paint pass used to do inline) with the
        # same fast-attack / slow-release rates.
        nb = len(self.display_bands) or 16
        for i in range(48):
            pos = i * nb / 48.0
            i0 = int(pos) % nb
            i1 = (i0 + 1) % nb
            frac = pos - int(pos)
            target = (self.display_bands[i0] * (1 - frac)
                      + self.display_bands[i1] * frac)
            cur = self._bar_values[i]
            k = self._dt_k(SPECTRUM_ATTACK if target > cur else SPECTRUM_RELEASE, s)
            self._bar_values[i] = cur + (target - cur) * k

        t_hi, t_mid, t_edge, t_glow = self._target_palette()
        _pk = self._dt_k(PALETTE_CROSSFADE, s)
        self._cur_hi = self._lerp(self._cur_hi, t_hi, _pk)
        self._cur_mid = self._lerp(self._cur_mid, t_mid, _pk)
        self._cur_edge = self._lerp(self._cur_edge, t_edge, _pk)
        self._glow_alpha += (t_glow - self._glow_alpha) * _pk

        if not frozen:
            if self._is("DISCONNECTED"):
                self.breathe_phase += 0.004 * s
            elif self._is("LISTENING"):
                self.breathe_phase += 0.035 * s
            elif self._is("SPEAKING"):
                self.breathe_phase += 0.03 * s
                self.ripple_phase = (self.ripple_phase + 0.012 * s) % 1.0
            else:
                self.breathe_phase += 0.016 * s

            if self._is("THINKING"):
                self.swirl_phase += 0.03 * s
            else:
                self.swirl_phase += 0.004 * s

        # Travelling-dot phase: THINKING quickens it 3x, DISCONNECTED
        # and ERROR crawl; SPEAKING rides the voice; CONNECTING is
        # eager; LISTENING is calm.
        if self._is("THINKING"):
            speed = 3.0
        elif self._is("DISCONNECTED"):
            speed = 0.25
        elif self._is("ERROR"):
            speed = 0.4
        elif self._is("SPEAKING"):
            speed = 1.4
        elif self._is("CONNECTING"):
            speed = 1.6
        elif self._is("LISTENING"):
            speed = 1.0
        else:
            speed = 0.6
        if not frozen:
            self._orbit = (self._orbit + 0.55 * speed * s) % 360.0

        # ---- Orb v5.17 Aura: crossfaded motion + energy spring -------
        # Parameters ease toward the state's targets at k=0.05/frame,
        # the current blobs orbit at the eased swirl rate, and the core
        # energy is a spring (fast attack, eased release) fed by the
        # shared voice level.
        a_bright_t, a_swirl_t, a_pulse_t, a_orbit_t = \
            self._aur_motion_targets()
        amp = self._aur_mp
        for i, t in enumerate((a_bright_t, a_swirl_t, a_pulse_t)):
            amp[i] += (t - amp[i]) * self._dt_k(MOTION_CROSSFADE, s)
        # B-R1-5: the LerSent glass eases toward the same state's
        # targets at the same rate, from the same smoothed energy
        # (the audio lift rides the targets, so silence eases the
        # glass back to base). The phase freezes with the ambient
        # motion (reduced-motion contract); the state-driven
        # parameters keep easing, exactly like the currents.
        self._glass.advance(self.state, self._aur_energy, s,
                            frozen=frozen)
        # R7-2: the sphere turns slowly and eases its swell toward
        # the same smoothed energy — silence settles it back.
        self._sphere.advance(dt, self.current_energy, frozen=frozen)
        # F6 CONNECTING entry: exactly one swell per entry. The
        # trigger registers even while frozen (so the count stays
        # honest and the swell is still owed); only its advance
        # below is motion, and that holds while frozen.
        if self._is("CONNECTING") and "CONNECTING" not in \
                self._aur_prev_state:
            self._aur_swell_phase = 0.0
            self._aur_swell_count += 1
        self._aur_prev_state = self.state
        if not frozen:
            # Aura's internal currents are the ambient motion F2/F5
            # still — the energy spring below keeps running.
            for blob in self._aur_blobs:
                blob[0] = (blob[0] + blob[2] * (0.30 + amp[1]) * s) % 360.0
            # F6 THINKING tighten: the orbit radii ease toward the
            # state's scale at the same crossfade rate as _aur_mp —
            # the currents draw in (or relax back out) over ~600 ms,
            # never snapping.
            self._aur_orbit_scale += \
                (a_orbit_t - self._aur_orbit_scale) * self._dt_k(MOTION_CROSSFADE, s)
            # F6 CONNECTING swell: one slow brightening wave, ~150
            # frames up and back down on a sine envelope. Leaving
            # CONNECTING early retires the wave to the decay branch
            # below — it fades, it never cuts or repeats.
            if self._aur_swell_phase >= 0.0 and self._is("CONNECTING"):
                self._aur_swell_phase += (1.0 / 150.0) * s
                if self._aur_swell_phase >= 1.0:
                    self._aur_swell_phase = -1.0
                    self._aur_swell = 0.0
                else:
                    self._aur_swell = math.sin(
                        math.pi * self._aur_swell_phase) ** 1.3
            elif self._aur_swell > 0.0:
                self._aur_swell_phase = -1.0
                self._aur_swell += (0.0 - self._aur_swell) * self._dt_k(SWELL_DECAY, s)
                if self._aur_swell < 0.01:
                    self._aur_swell = 0.0
        tgt = self.current_energy
        if tgt > self._aur_energy:
            self._aur_energy, self._aur_energy_vel = self._spring_step_dt(
                self._aur_energy, self._aur_energy_vel, tgt, *SPRING_ENERGY_ATTACK_PAIR, self._spring_s(s))
        else:
            self._aur_energy, self._aur_energy_vel = self._spring_step_dt(
                self._aur_energy, self._aur_energy_vel, tgt, *SPRING_ENERGY_RELEASE_PAIR, self._spring_s(s))
        self._aur_energy = max(-0.05, min(1.20, self._aur_energy))
        if not frozen:
            # F6 SPEAKING rim breath: follows the energy spring
            # itself (fast in on a louder voice, slower out as it
            # falls) while speaking; in every other state the target
            # is 0 and the rim settles back to its v5.17 rest.
            rim_tgt = max(0.0, min(1.0, self._aur_energy)) \
                if self._is("SPEAKING") else 0.0
            rim_k = self._dt_k(RIM_BREATH_ATTACK if rim_tgt > self._aur_rim_breath else RIM_BREATH_RELEASE, s)
            self._aur_rim_breath += \
                (rim_tgt - self._aur_rim_breath) * rim_k

    def _colors(self):
        hi = QColor(int(self._cur_hi[0]), int(self._cur_hi[1]), int(self._cur_hi[2]))
        mid = QColor(int(self._cur_mid[0]), int(self._cur_mid[1]), int(self._cur_mid[2]))
        edge = QColor(int(self._cur_edge[0]), int(self._cur_edge[1]), int(self._cur_edge[2]))
        return hi, mid, edge
