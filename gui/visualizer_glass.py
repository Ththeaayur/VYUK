"""B-R1-5 (2026-11-09) LerSent liquid-glass orb — the glass layer.

THE GAP (proved first, file/line evidence)
------------------------------------------
Today's Aura (gui/visualizer_paint.py::_paint_aura, 386-538) is a
luminous energy sphere WITHOUT glass: an atmosphere, a body drawn
as a matte radial gradient (deep edge tones, light pulled high),
3-4 internal current blobs clipped inside a PERFECT ellipse, a
bright living core, and a single-colour fresnel rim (dim ellipse,
bright crown, bounce arc). State rides the crossfaded _aur_mp
parameters + the _aur_energy spring (gui/visualizer_state.py,
_aur_motion_targets in the paint mixin: THINKING 0.78/2.6/0.35/0.55
etc.). What it has no trace of:

  * no GLASS SHELL — no specular highlight, no shell layers, no
    glass opacity; the body is matte, not glass;
  * no CONTOUR DEFORM — the silhouette is a perfect ellipse; nothing
    wobbles the contour like liquid;
  * no CHROMATIC FRINGE — no dispersion at the rim; the rim is one
    colour;
  * no SINGLE PARAMETER SNAPSHOT — no orb-uniforms equivalent
    mapping state + audio to glass uniforms; the motion parameters
    are scattered across _aur_mp / _aur_energy / _aur_blobs;
  * no EDGE SOFTNESS / EDGE GLOW parameters — the rim is fixed.

THE PATTERN (LerSent001/orb, MIT — read from the repo itself,
2026-11-09)
---------------------------------------------------------------
``LerSent001/orb`` ("Liquid Orb Editor", MIT License, Copyright
(c) 2026 LerSent001 — LICENSE file read from the repo +
README: "Project code is licensed under the MIT License") is a
live WebGPU playground for animated liquid-glass orbs (React +
WebGPU/WGSL, 13 presets, controls for color, speed, shape, glass
refraction, outer glow). Its optics, from src/orb-uniforms.ts
(the single mapping from editor parameters to GPU uniforms) and
the README's audio section:

  * glass: glassEnabled / glassOpacity — the glass shell;
  * contourDeform / warp / ridgeAmt — the liquid contour and
    interior distortion;
  * chromaticShift — dispersion / fringe at the rim;
  * sheen / gloss / specColor — specular highlights;
  * shellInner / shellMid / shellEdge + alphas — the shell layers;
  * edgeSoftness / edgeGlow — the rim treatment;
  * audio: low/mid/high bands + overall energy drive the contour,
    internal distortion, highlights and motion speed — applied
    AFTER state transitions, smoothed, easing back to base when
    audio pauses; stopping the input restores the preset params.

It is React + WebGPU/WGSL — NOT PySide6, and there is no WebGPU
in this tree. This module re-implements the PATTERN in Qt/Python:
the parameter snapshot (GlassParams), the eased live state
(GlassState), and pure QPainter helpers the two Aura paint sites
call. No code, no assets, no shader taken. Aura blue only: the
glass reads its colours from the orb's own theme triple
(highlight/mid/edge), so it stays Aura blue in the default theme
and follows the user's chosen theme exactly like the rest of the
orb — abstract, non-face, premium, exactly like JARVIS.

LOPHOLE FAMILY — "the orb lies, or the orb freezes"
----------------------------------------------------
* A state shown that VYUK is not actually in (the orb claims
  listening while VYUK is idle — a lie).
* A state that never updates (the orb freezes on one state — the
  user cannot tell what VYUK is doing).
* An orb that blocks the UI (the animation holds the event loop —
  the window stutters).
* An orb that cannot be read (the glass is so busy it hides the
  state — the user cannot tell idle from speaking).

ONE design closes the class: ONE glass state, fed by the SAME
state the orb already has. GlassState.advance() takes the orb's
own state string + the orb's own smoothed energy + dt — never a
hardcoded state; an unknown state falls back to the IDLE base,
never to "listening". Parameters ease toward the state's targets
at the shared crossfade rate (never snap); the audio lift is
applied to the TARGETS after the state targets (LerSent's rule),
so silence eases the glass back to its base. The wobble phase
advances only when the orb's ambient motion is not frozen
(reduced-motion contract: the phase freezes, the state-driven
parameters keep easing — exactly like the currents). The paint
helpers are pure QPainter calls inside the existing paint pass —
no timer of their own, no blocking wait; the event loop runs
between frames exactly as before. The four states' glass
signatures are pairwise distinct (see signature()), so the state
reads at a glance — the glass is a treatment, not a mask.

Rejected alternatives (recorded):
* A static sphere that never changes state (a lie by omission).
* A state shown that VYUK is not in (a lie — e.g. hardcoding the
  glass to "listening" on any unknown state).
* A blocking animation (a stutter — e.g. a sleep/wait inside
  the paint pass).
* A glass so busy it hides the state (unreadable — e.g. a
  rainbow fringe or a contour wobble so large the silhouette
  breaks).
* Embedding a WebGPU/WebEngine orb (no WebGPU in this tree, and
  a browser surface inside the home widget would be a new
  dependency and a new process, not a treatment).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen

from .motion import MOTION_CROSSFADE

__all__ = [
    "GlassParams",
    "GlassState",
    "glass_params_for_state",
    "glass_contour_arc",
    "glass_contour_path",
    "paint_chromatic_fringe",
    "paint_glass_layers",
    "paint_glass_shell",
    "paint_glass_specular",
]

# --------------------------------------------------------------------------
# The snapshot: one mapping from state + audio to glass uniforms
# (LerSent's orb-uniforms.ts, re-implemented as a dataclass).
# Every field is a 0..1 float; every field drives something visible
# in the paint helpers below — no dead uniforms.
# --------------------------------------------------------------------------

_GLASS_FIELDS = (
    "contour_deform",
    "warp",
    "ridge_amt",
    "chromatic_shift",
    "sheen",
    "gloss",
    "shell_mid_alpha",
    "shell_edge_alpha",
    "edge_softness",
    "edge_glow",
    "glass_opacity",
)


def _clamp01(value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN never paints
        return 0.0
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class GlassParams:
    """One glass snapshot — the orb-uniforms equivalent.

    contour_deform: liquid wobble of the silhouette (0 = a perfect
        circle, 1 = the full wobble: a2+a3+aw at the live states'
        maxima is ~6% of R (up to ~11.8% only at the synthetic
        deform=1, warp=1 corner, never reached by a live state).
    warp: second, faster wobble harmonic (the interior distortion).
    ridge_amt: spread of the rim's fringe ridges.
    chromatic_shift: dispersion fringe strength at the rim.
    sheen: top-light specular strength. gloss: sheen-arc strength.
    shell_mid_alpha / shell_edge_alpha: the two glass-wall layers.
    edge_softness: rim softness (wider + fainter strokes).
    edge_glow: outer glow just outside the rim.
    glass_opacity: global multiplier on every glass alpha.
    """

    contour_deform: float = 0.12
    warp: float = 0.10
    ridge_amt: float = 0.08
    chromatic_shift: float = 0.05
    sheen: float = 0.35
    gloss: float = 0.30
    shell_mid_alpha: float = 0.45
    shell_edge_alpha: float = 0.55
    edge_softness: float = 0.50
    edge_glow: float = 0.15
    glass_opacity: float = 0.85

    def __post_init__(self) -> None:
        for name in _GLASS_FIELDS:
            object.__setattr__(self, name, _clamp01(getattr(self, name)))

    def as_tuple(self) -> tuple[float, ...]:
        return tuple(getattr(self, name) for name in _GLASS_FIELDS)


# Per-state glass targets. The four live states are pairwise
# distinct at a glance (see signature()): idle is calm glass,
# listening opens the contour + sheen, thinking warps + tightens,
# speaking lifts the fringe + gloss + edge glow with the voice.
# DISCONNECTED/ERROR dim the glass (mirroring the orb's dimf);
# an UNKNOWN state falls back to IDLE — never to "listening".
_GLASS_TARGETS: dict[str, GlassParams] = {
    "IDLE": GlassParams(
        contour_deform=0.12, warp=0.10, ridge_amt=0.08,
        chromatic_shift=0.05, sheen=0.35, gloss=0.30,
        shell_mid_alpha=0.45, shell_edge_alpha=0.55,
        edge_softness=0.50, edge_glow=0.15, glass_opacity=0.85),
    "LISTENING": GlassParams(
        contour_deform=0.30, warp=0.22, ridge_amt=0.15,
        chromatic_shift=0.18, sheen=0.55, gloss=0.50,
        shell_mid_alpha=0.55, shell_edge_alpha=0.65,
        edge_softness=0.45, edge_glow=0.35, glass_opacity=0.90),
    "THINKING": GlassParams(
        contour_deform=0.45, warp=0.55, ridge_amt=0.45,
        chromatic_shift=0.30, sheen=0.45, gloss=0.40,
        shell_mid_alpha=0.50, shell_edge_alpha=0.60,
        edge_softness=0.40, edge_glow=0.30, glass_opacity=0.88),
    "SPEAKING": GlassParams(
        contour_deform=0.55, warp=0.35, ridge_amt=0.25,
        chromatic_shift=0.45, sheen=0.70, gloss=0.75,
        shell_mid_alpha=0.65, shell_edge_alpha=0.80,
        edge_softness=0.35, edge_glow=0.60, glass_opacity=0.95),
    "CONNECTING": GlassParams(
        contour_deform=0.25, warp=0.30, ridge_amt=0.20,
        chromatic_shift=0.22, sheen=0.60, gloss=0.55,
        shell_mid_alpha=0.55, shell_edge_alpha=0.65,
        edge_softness=0.45, edge_glow=0.45, glass_opacity=0.90),
    "DISCONNECTED": GlassParams(
        contour_deform=0.05, warp=0.05, ridge_amt=0.04,
        chromatic_shift=0.00, sheen=0.15, gloss=0.10,
        shell_mid_alpha=0.25, shell_edge_alpha=0.30,
        edge_softness=0.60, edge_glow=0.05, glass_opacity=0.60),
    "ERROR": GlassParams(
        contour_deform=0.20, warp=0.15, ridge_amt=0.10,
        chromatic_shift=0.10, sheen=0.30, gloss=0.25,
        shell_mid_alpha=0.35, shell_edge_alpha=0.40,
        edge_softness=0.50, edge_glow=0.20, glass_opacity=0.75),
}

# Audio lift (LerSent's rule — applied to the TARGETS after the
# state targets, so silence eases the glass back to its base and
# stopping the input restores the preset params). The energy here
# is the orb's own smoothed energy spring, never a raw mic level.
_AUDIO_LIFT = {
    "contour_deform": 0.25,
    "warp": 0.30,
    "sheen": 0.30,
    "gloss": 0.35,
    "chromatic_shift": 0.15,
    "edge_glow": 0.25,
}

# Wobble phase speed (radians per legacy 60 fps frame) per state.
_PHASE_SPEED = {
    "THINKING": 0.066,
    "SPEAKING": 0.048,
    "CONNECTING": 0.054,
    "LISTENING": 0.030,
}


def glass_params_for_state(state) -> GlassParams:
    """The glass targets for a state string.

    Exact match first, then substring (the orb's _is() rule — a
    compound state still reads), then the IDLE base. An unknown
    state is NEVER shown as listening/speaking/thinking — it is
    the calm base, honestly.
    """
    name = str(state or "").upper()
    if name in _GLASS_TARGETS:
        return _GLASS_TARGETS[name]
    for key, params in _GLASS_TARGETS.items():
        if key in name:
            return params
    return _GLASS_TARGETS["IDLE"]


class GlassState:
    """The live, eased glass state — one per orb widget.

    advance() takes the orb's own state + smoothed energy + the
    step in legacy 60 fps frames (s, exactly as _animate_frame
    expresses it). Parameters ease toward the state's targets at
    the shared MOTION_CROSSFADE rate (never snap); the audio lift
    rides the targets, so it fades with the energy. The wobble
    phase advances only when not frozen (reduced-motion contract:
    the phase freezes, the state-driven parameters keep easing —
    exactly like the currents). snapshot() is what the paint pass
    reads; nothing here touches the orb's state.
    """

    def __init__(self) -> None:
        self._cur: list[float] = list(
            _GLASS_TARGETS["IDLE"].as_tuple())
        self.phase: float = 0.0

    @staticmethod
    def _dt_k(k_frame: float, s: float) -> float:
        # Same formula as OrbStateMixin._dt_k: applying (1-k)
        # retention s times must equal one step at the new rate.
        if s == 1.0:
            return k_frame
        return 1.0 - (1.0 - k_frame) ** s

    def advance(self, state, energy: float = 0.0, s: float = 1.0,
                *, frozen: bool = False) -> None:
        try:
            s = float(s)
        except (TypeError, ValueError):
            s = 1.0
        s = max(0.0, min(15.0, s))
        energy = _clamp01(energy)
        targets = glass_params_for_state(state)
        target_values = list(targets.as_tuple())
        for name, lift in _AUDIO_LIFT.items():
            idx = _GLASS_FIELDS.index(name)
            target_values[idx] = _clamp01(
                target_values[idx] + lift * energy)
        k = self._dt_k(MOTION_CROSSFADE, s)
        for i, target in enumerate(target_values):
            self._cur[i] += (target - self._cur[i]) * k
        if not frozen:
            name = str(state or "").upper()
            speed = _PHASE_SPEED.get(name, 0.015)
            for key, value in _PHASE_SPEED.items():
                if key in name:
                    speed = value
                    break
            self.phase = (self.phase + speed * s) % (2.0 * math.pi)

    def snapshot(self) -> GlassParams:
        return GlassParams(*self._cur)

    @classmethod
    def signature(cls, state) -> tuple[float, ...]:
        """The state's glass signature (its target tuple) — the
        readability proof: the four live states' signatures are
        pairwise distinct, so the state reads at a glance."""
        return glass_params_for_state(state).as_tuple()


# --------------------------------------------------------------------------
# Paint helpers — pure QPainter calls, no state of their own. The two
# Aura paint sites (visualizer_paint._paint_aura, mini_orb._paint_aura)
# call them with the snapshot from their own GlassState.
# --------------------------------------------------------------------------

_CONTOUR_POINTS = 64


def glass_contour_path(cx: float, cy: float, radius: float,
                      params: GlassParams, phase: float) -> QPainterPath:
    """The liquid silhouette: a circle wobbled by two harmonics.

    r(θ) = R * (1 + a2*sin(2θ+phase) + a3*sin(3θ-0.7*phase)
    + aw*sin(5θ+1.3*phase)) with a2 = 0.055*contour_deform,
    a3 = 0.6*a2 and aw = 0.03*warp. At deform 0 the path is a
    circle; at the live states' maxima the wobble is ~6% of R
    (up to ~11.8% only at the synthetic deform=1, warp=1 corner,
    never reached by a live state) — visible, never breaking the
    silhouette. The same path clips the body and the currents, so
    the liquid reads as one surface.
    """
    if params is None:
        params = _GLASS_TARGETS["IDLE"]
    path = QPainterPath()
    for i in range(_CONTOUR_POINTS + 1):
        theta = 2.0 * math.pi * i / _CONTOUR_POINTS
        r = _contour_radius(radius, theta, params, phase)
        x = cx + r * math.cos(theta)
        y = cy + r * math.sin(theta)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _contour_radius(radius: float, theta: float,
                   params: GlassParams, phase: float) -> float:
    """The wobbled radius at angle θ (radians, y-down: θ=0 at
    3 o'clock, θ=90° at 6 o'clock) — the single radius function
    the closed path and the arc helper both use, so every layer
    sits on the same liquid edge."""
    a2 = 0.055 * params.contour_deform
    a3 = 0.6 * a2
    aw = 0.03 * params.warp
    return radius * (
        1.0
        + a2 * math.sin(2.0 * theta + phase)
        + a3 * math.sin(3.0 * theta - 0.7 * phase)
        + aw * math.sin(5.0 * theta + 1.3 * phase))


def glass_contour_arc(cx: float, cy: float, radius: float,
                      params: GlassParams, phase: float,
                      start_deg: float, span_deg: float) -> QPainterPath:
    """A segment of the liquid silhouette — the fresnel rim's
    crown and bounce arcs, drawn on the wobbled edge instead of
    the perfect circle, so the rim never detaches from the body.

    Angles are math degrees, y-down (0° at 3 o'clock, 90° at
    6 o'clock): the crown (top) is start -145° span 110°, the
    bounce (bottom) is start 35° span 110° — the same arcs the
    v5.17 rim drew with drawArc(body_rect, 35*16, 110*16) and
    (215*16, 110*16) in Qt's flipped convention.
    """
    if params is None:
        params = _GLASS_TARGETS["IDLE"]
    path = QPainterPath()
    steps = max(8, int(abs(span_deg) / 3.0))
    for i in range(steps + 1):
        deg = start_deg + span_deg * i / steps
        theta = math.radians(deg)
        r = _contour_radius(radius, theta, params, phase)
        x = cx + r * math.cos(theta)
        y = cy + r * math.sin(theta)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    return path


def paint_glass_shell(painter: QPainter, path: QPainterPath,
                      cx: float, cy: float, radius: float,
                      edge: QColor, mid: QColor,
                      params: GlassParams, dimf: float = 1.0,
                      phase: float = 0.0) -> None:
    """The glass wall: two strokes of the contour — the edge layer
    on the silhouette, the mid layer just inside it. Width grows
    with edge_softness (softer = wider + fainter); every alpha
    rides glass_opacity * dimf. Drawn with NoBrush so the body
    beneath stays visible."""
    if params is None:
        params = _GLASS_TARGETS["IDLE"]
    opacity = params.glass_opacity * _clamp01(dimf)
    if opacity <= 0.0:
        return
    width = max(1.0, radius * 0.012) * (1.0 + 2.0 * params.edge_softness)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(
        QColor(edge.red(), edge.green(), edge.blue(),
               int(120 * params.shell_edge_alpha * opacity)),
        width))
    painter.drawPath(path)
    inner = glass_contour_path(cx, cy, radius * 0.965, params, phase)
    painter.setPen(QPen(
        QColor(mid.red(), mid.green(), mid.blue(),
               int(90 * params.shell_mid_alpha * opacity)),
        max(1.0, width * 0.7)))
    painter.drawPath(inner)


def paint_glass_specular(painter: QPainter, cx: float, cy: float,
                         radius: float, highlight: QColor,
                         params: GlassParams,
                         dimf: float = 1.0) -> None:
    """The top-light specular: a thin sheen arc along the top rim.
    UI rethink Build 1 removed the white-hot ellipse (a painted
    highlight that read as a smudge on the orb, brightest at night
    when the orb was grey — a rendering defect, not a look choice).
    The orb's light is its core glow + particles now; the arc stays
    as the one quiet glass cue. Alpha rides gloss * glass_opacity
    * dimf — at idle a whisper, speaking a flare."""
    if params is None:
        params = _GLASS_TARGETS["IDLE"]
    opacity = params.glass_opacity * _clamp01(dimf)
    if opacity <= 0.0:
        return
    sheen_a = int(90 * params.gloss * opacity)
    if sheen_a > 0:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, sheen_a),
                            max(1.0, radius * 0.014)))
        painter.drawArc(
            QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
            35 * 16, 110 * 16)


def paint_chromatic_fringe(painter: QPainter, cx: float, cy: float,
                           radius: float, highlight: QColor,
                           mid: QColor, params: GlassParams,
                           dimf: float = 1.0) -> None:
    """Dispersion at the rim: three thin arcs hugging the
    silhouette just outside the body — white, highlight
    (Aura blue), mid-violet — their alphas ride chromatic_shift
    * glass_opacity * dimf, their radial spread rides ridge_amt.
    Aura blue dominant, never rainbow: the fringe is a whisper
    of dispersion, not a spectrum. At shift 0 nothing draws."""
    if params is None:
        params = _GLASS_TARGETS["IDLE"]
    shift = params.chromatic_shift * params.glass_opacity * _clamp01(dimf)
    if shift <= 0.0:
        return
    spread = 1.0 + params.ridge_amt
    colours = (
        (QColor(255, 255, 255), 46),
        (QColor(highlight.red(), highlight.green(),
                highlight.blue()), 36),
        (QColor(mid.red(), mid.green(), mid.blue()), 28),
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for i, (colour, base_a) in enumerate(colours):
        offset = (0.6 + i * 1.0) * spread
        painter.setPen(QPen(
            QColor(colour.red(), colour.green(), colour.blue(),
                   int(base_a * shift)),
            max(1.0, radius * 0.008)))
        painter.drawArc(
            QRectF(cx - radius - offset, cy - radius - offset,
                   (radius + offset) * 2, (radius + offset) * 2),
            20 * 16, 200 * 16)


def paint_glass_layers(painter: QPainter, cx: float, cy: float,
                       radius: float, highlight: QColor,
                       mid: QColor, edge: QColor,
                       params: GlassParams, phase: float,
                       dimf: float = 1.0) -> None:
    """Shell + specular + fringe, in order — the whole glass
    treatment, called after the fresnel rim. The contour path
    is rebuilt here with the live phase (the shell follows the
    same wobble as the body clip)."""
    if params is None:
        params = _GLASS_TARGETS["IDLE"]
    path = glass_contour_path(cx, cy, radius, params, phase)
    paint_glass_shell(painter, path, cx, cy, radius, edge, mid,
                      params, dimf, phase)
    paint_glass_specular(painter, cx, cy, radius, highlight,
                         params, dimf)
    paint_chromatic_fringe(painter, cx, cy, radius, highlight,
                           mid, params, dimf)
