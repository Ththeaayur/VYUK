"""OrbVisualizer paint mixin: the single paintEvent and all paint helpers.

Split from gui/visualizer.py (GUI split #7, move only). Paint methods
only read state and paint — they never write widget state. The orb
renders teal (ruling 2026-11-24, supersedes the Aura lock): a teal
particle + wireframe-mesh sphere, bright core, tilted orbit rings +
faint reticle rings, the gold live ring. State lives in
visualizer_state.py; the composed class lives in visualizer.py.
"""

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPen,
    QRadialGradient,
)

from .orb_sphere import paint_mesh_sphere, paint_particle_sphere
from .style import AMBER
from .visualizer_glass import (
    glass_contour_arc,
    glass_contour_path,
    paint_glass_layers,
)


# Conversion Phase 0 (home text/orb overlap): the paint margin.
# Every dispatch radius in paintEvent is computed from the
# widget's short side MINUS this margin on each side, and the
# Aura atmosphere is additionally capped 16 px inside the
# widget edge (see _paint_aura) — so the painted orb edge
# never reaches the widget boundary, and with the stage's
# 16 px hero/wordmark spacing the painted edge sits at least
# ~32 px above the wordmark. The pre-fix Aura atmosphere was
# min * 0.34 * scale * 1.60 — wider than the widget itself
# (diameter 1.088 * min at scale 1.0) — which is why the
# orb's lower edge washed over the text below it.
_ORB_PAINT_MARGIN = 20
_ORB_EDGE_INSET = 16


def ring_alpha_for_energy(energy: float) -> int:
    """The gold ring's alpha for the smoothed voice energy (0..1).

    Resting (silence) the ring is visible but quiet; it brightens
    steadily with the voice — never a timer pulse, only the
    energy. Strictly increasing, so a louder passage always
    reads brighter than silence.
    """
    e = max(0.0, min(1.0, float(energy or 0.0)))
    return int(96 + 128 * e)


# Spectrum ring (v5.17): 16 bars at 22.5° spacing, band 0 at the top,
# same screen convention (band 0 at the top).
_SPEC_DIRS_16: tuple[tuple[float, float], ...] = tuple(
    (math.cos(math.radians(i * 22.5 - 90.0)),
     math.sin(math.radians(i * 22.5 - 90.0)))
    for i in range(16)
)


class OrbPaintMixin:
    """Paint half of OrbVisualizer (GUI split #7).

    The single paintEvent plus every _paint_* helper and the
    painted-size helpers. Reads state only, writes none.
    """
    # ------------------------------------------------- Conversion Phase 0
    def sizeHint(self):  # noqa: N802 (Qt naming)
        """An honest size hint (was invalid (-1, -1)): the stacked
        hero's sizeHint is the max of its children's, so an
        invalid hint here made the hero's hint (0, 0) and the
        stage layout treated the hero as wanting no height —
        the root of the home overlap (see _HeroWidget)."""
        return QSize(320, 320)

    def _painted_min(self) -> float:
        """The short side the dispatch radii are computed from:
        the widget's short side inset by the Phase 0 paint
        margin on each side."""
        return max(1.0, float(min(self.width(), self.height()))
                   - 2.0 * _ORB_PAINT_MARGIN)

    def set_orb_diameter(self, diameter_px: float) -> None:
        """UI Phase 1 §5 (ruling 2026-11-24, Aayur): the orb's
        body diameter in px, set by the window from 19.5% of
        the window width (see gui/breakpoints.py
        hud_orb_diameter_for). H-02/H-18: the orb never exceeds
        this size, even when columns collapse — it is a max,
        not a target to fill. 0.0 clears it (legacy path)."""
        self._orb_diameter = max(0.0, float(diameter_px or 0.0))
        self.update()

    def _orb_radius(self) -> float:
        """Body radius for this paint pass. With a §5 diameter
        set, it is half that diameter, capped so the outermost
        faint ring (1.22 x the body radius, painted at the end
        of _paint_aura) stays inside the widget. Without one,
        the legacy paint_min * 0.34 body."""
        if getattr(self, "_orb_diameter", 0.0) > 0:
            cap = (min(self.width(), self.height()) / 2.0) / 1.22
            return max(1.0, min(self._orb_diameter / 2.0, cap))
        return self._painted_min() * 0.34

    def _paint_scale(self, energy: float) -> float:
        """The paintEvent breathing/energy scale for the
        current state (Aura's branch)."""
        if self._is("LISTENING"):
            return (1.0 + math.sin(self.breathe_phase) * 0.015
                    + energy * 0.14)
        if self._is("DISCONNECTED"):
            return 1.0 + math.sin(self.breathe_phase) * 0.006
        return (1.0 + math.sin(self.breathe_phase) * 0.03
                + energy * 0.08)

    def painted_radius(self) -> float:
        """Radius, from the widget centre, of the painted orb
        edge for the current style/state — computed with the
        same inputs paintEvent uses, so a layout test can
        assert the painted edge clears the labels below.

        For Aura this is exact: the atmosphere radius,
        capped 16 px inside the widget edge. The faint ambient
        glow tail (which fades to transparent) is not counted
        as an edge."""
        energy = min(1.0, self.current_energy + 0.45 * self._ack_pulse)
        half = min(self.width(), self.height()) / 2.0
        if min(self.width(), self.height()) < 130:
            return self._painted_min() * 0.40
        base = self._orb_radius()
        radius = base * self._paint_scale(energy)
        radius = min(radius * 1.60,
                     max(radius, half - _ORB_EDGE_INSET))
        if self._is("LISTENING"):
            # The old floor was paint_min * 0.492 = 1.447 x the
            # old base (paint_min * 0.34); the ratio is kept,
            # now against the §5 base radius.
            radius = max(radius, base * 1.447)
        if self._ripples:
            radius = max(radius, base * 1.441)
        # The Phase 0 promise: the painted edge never claims
        # past 16 px inside the widget edge.
        return min(radius, half - _ORB_EDGE_INSET)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        highlight, mid, edge = self._colors()
        # v5.51 E-1: the ack flare rides on top as a brief energy
        # lift (local to this paint pass — current_energy itself,
        # and every spring fed by it, is untouched).
        energy = min(1.0, self.current_energy + 0.45 * self._ack_pulse)
        # v5.43 F2: a muted orb is globally dimmed on top of its
        # dimmed palette — several painters lay white-hot cores
        # over the palette, which palette dimming alone cannot
        # quiet. The badge (drawn at every exit below) resets the
        # opacity, exactly as the mini orb's badge sits at full
        # strength over its dimmed canvas.
        if self._muted:
            painter.setOpacity(0.55)

        # Conversion Phase 0: dispatch radii come from the
        # margin-inset short side, never the raw widget size.
        paint_min = self._painted_min()
        scale = self._paint_scale(energy)
        # Listening ripples (v5.16): shed while listening, drawn
        # UNDER every style's body — including the compact mark
        # every style collapses to below 130 px.
        if self._ripples:
            self._paint_ripples(painter, cx, cy, scale, mid)
        # Listening spectrum ring (v5.17): the live spectrum as 16
        # radial bars in the widget-edge margin — above the ripples
        # (they are pulses; this is data), still under the body.
        if self._is("LISTENING"):
            self._paint_spectrum_ring(painter, cx, cy, highlight, mid)
        if min(w, h) < 130:
            # v3.0 small-size LOD: below 130 px a detailed orb turns
            # to mush (at the mini orb's 92 px a dial read as a
            # clock), so the orb collapses to the compact mark.
            self._paint_compact(painter, cx, cy, highlight, mid, edge,
                                energy, scale)
            if self._muted:
                self._paint_muted_badge(painter, cx, cy)
            return
        # Orb v5.17: a luminous energy sphere — its atmosphere
        # needs more of the widget than the quiet body styles,
        # less than the dials. Motion arrives crossfaded from
        # _animate_frame via _aur_mp / _aur_blobs / _aur_energy.
        lod = 2 if w < 170 else 1 if w < 250 else 0
        aura_r = self._orb_radius() * scale
        self._paint_aura(painter, cx, cy, aura_r,
                         highlight, mid, edge, energy, lod)
        if self._muted:
            self._paint_muted_badge(painter, cx, cy)

    def _paint_muted_badge(self, painter, cx, cy):
        """The mini orb's muted badge (gui/mini_orb.py), main-orb
        scale — the shared muted glyph: a small dark disc at the
        body's lower right carrying a pale slash and centre dot.
        Drawn at full opacity over the dimmed body, exactly as on
        the mini orb's canvas."""
        half = min(self.width(), self.height()) / 2.0
        bx, by, br = cx + half * 0.53, cy + half * 0.53, half * 0.16
        painter.save()
        painter.setOpacity(1.0)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
        painter.setBrush(QColor(16, 19, 26, 235))
        painter.drawEllipse(QRectF(bx - br, by - br, br * 2, br * 2))
        slash = QPen(QColor(200, 206, 216, 220), max(1.4, half * 0.035))
        slash.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(slash)
        painter.drawLine(QPointF(bx - br * 0.55, by + br * 0.55),
                         QPointF(bx + br * 0.55, by - br * 0.55))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(200, 206, 216, 220))
        painter.drawEllipse(QRectF(bx - br * 0.18, by - br * 0.18,
                                   br * 0.36, br * 0.36))
        painter.restore()

    def _paint_compact(self, painter, cx, cy, highlight, mid, edge,
                       energy, scale):
        """Small-size mark (v3.0) for widgets under 130 px — the mini
        orb's 92 px home. One premium glyph instead of a shrunken dial:
        a layered radial-gradient core (theme highlight -> mid -> edge
        -> a deep shade of the edge), a single thin orbit ring carrying
        one travelling dot (phase = the shared _orbit accumulator, so
        state speeds feel identical to the big orb), and — only while
        LISTENING or SPEAKING — a vital arc hugging the core whose span
        rides the voice energy. Palette, crossfade, breathe scale and
        state plumbing all arrive from paintEvent / _animate_frame,
        unchanged; DISCONNECTED greys through the palette and the mini
        orb's own 0.55 window opacity, exactly as before.
        """
        half = min(self.width(), self.height()) / 2.0
        dim = self._is("DISCONNECTED")

        # -- ambient glow (same recipe family as the big styles) --------
        glow_r = half * 0.99
        glow = QRadialGradient(cx, cy, glow_r)
        a = int((self._glow_alpha + energy * 55) * (0.55 if dim else 1.0))
        glow.setColorAt(0.0, QColor(mid.red(), mid.green(), mid.blue(), a))
        glow.setColorAt(0.55, QColor(edge.red(), edge.green(), edge.blue(),
                                     int(a * 0.35)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QRectF(cx - glow_r, cy - glow_r,
                                   glow_r * 2, glow_r * 2))

        # -- layered core ------------------------------------------------
        # The core must fill most of the sphere (parent by-eye
        # fix, 2026-11-24): at 0.56 the teal gradient covered
        # only the inner 56% and the outer 44% stayed dark
        # background + sparse particles, so the orb read dark
        # navy. At 0.72 the bright teal body fills the sphere
        # and the gold ring sits just outside it.
        core_r = half * 0.72 * scale
        # The body must READ teal by eye (parent by-eye fix,
        # 2026-11-24): pixel sampling showed the body was a
        # deep teal (32,82,93) that reads as dark navy, not
        # teal — the bright rim #52C5CC covered too little of
        # the sphere. The gradient is shifted one step
        # brighter: the bright rim #52C5CC now covers the
        # sphere out to 0.70, a mid teal #2F8FA0 (47,143,160)
        # at 0.88, and the measured body #227084 (34,112,132)
        # is the outer edge — so the whole sphere is a
        # clearly-teal body with a bright core, not a dark ball.
        deep = QColor(34, 112, 132)
        _mid_teal = QColor(47, 143, 160)
        core = QRadialGradient(
            QPointF(cx, cy), core_r * 1.3,
            QPointF(cx - core_r * 0.28, cy - core_r * 0.32))
        core.setColorAt(0.0, highlight)
        core.setColorAt(0.70, mid)
        core.setColorAt(0.88, _mid_teal)
        core.setColorAt(1.0, deep)
        painter.setBrush(QBrush(core))
        painter.drawEllipse(QRectF(cx - core_r, cy - core_r,
                                   core_r * 2, core_r * 2))

        # -- vital arc (LISTENING / SPEAKING only) -----------------------
        if self._is("LISTENING", "SPEAKING"):
            arc_r = core_r * 1.17
            arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
            span = int(80 + 240 * min(1.0, 0.2 + energy))
            arc_pen = QPen(QColor(mid.red(), mid.green(), mid.blue(), 215),
                           max(1.6, half * 0.045))
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Qt arcs: 1/16 degree, 0 = 3 o'clock, positive = CCW.
            # Centre the arc on 12 o'clock.
            painter.drawArc(arc_rect, int((90 - span / 2) * 16), span * 16)

        # -- orbit ring + single travelling dot --------------------------
        ring_r = half * 0.80
        painter.setPen(QPen(QColor(255, 255, 255, 42), 1.1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(cx - ring_r, cy - ring_r,
                                   ring_r * 2, ring_r * 2))
        ang = math.radians(self._orbit)
        dx = cx + math.cos(ang) * ring_r
        dy = cy - math.sin(ang) * ring_r
        dr = max(1.7, half * 0.052)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(highlight.red(), highlight.green(),
                                 highlight.blue(), 235))
        painter.drawEllipse(QRectF(dx - dr, dy - dr, dr * 2, dr * 2))

    def _ripple_span(self) -> tuple[float, float]:
        """(birth radius, final radius) for listening ripples, in
        widget pixels, following the geometry of the current style.

        The orb body fills the widget —
        a ring born outside the body would have nowhere to travel.
        Its ripples are born at the core instead and pulse outward
        across the orb face,
        fading as they reach the rim. The compact mark has room around
        them: their rings are born just outside the (unscaled) body
        radius and expand toward 1.6x it, capped at the widget edge.
        """
        # Conversion Phase 0: ripple radii come from the
        # margin-inset short side, not the widget edge; the
        # 130 px compact threshold still keys off the
        # raw widget size, as before.
        raw = min(self.width(), self.height())
        m = self._painted_min()
        if raw < 130:
            body = m * 0.28          # compact core: half * 0.56
        else:
            body = m * 0.34          # Aura body
        r0 = body * 1.03
        return r0, min(r0 * 1.6, m * 0.49)

    def _paint_ripples(self, painter, cx, cy, scale, mid):
        """Listening ripples (v5.16) — soft expanding rings drawn
        under the orb body, for every style (see _ripple_span for
        the two geometries). A ring fades over its whole journey;
        its strength is the mic level at spawn time."""
        if not self._ripples:
            return
        r0, r1 = self._ripple_span()
        if r1 <= r0:
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for age, strength in self._ripples:
            r = r0 + (r1 - r0) * age
            alpha = int(150 * strength * (1.0 - age) ** 1.6)
            if alpha <= 0:
                continue
            painter.setPen(QPen(QColor(mid.red(), mid.green(),
                                       mid.blue(), alpha),
                                max(1.0, r0 * 0.030)))
            painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

    def _paint_spectrum_ring(self, painter, cx, cy, highlight, mid):
        """Listening spectrum ring (v5.17) — the 16 smoothed spectrum
        bands (display_bands, the update_audio_data feed) as radial
        bars around the orb, drawn under the body, after the ripples.

        ONE geometry, anchored to the WIDGET, not
        the body: the outer end of each bar sits just inside the
        widget edge (r = 0.492 * min(w, h)) and the bar reaches
        inward, its length proportional to the band value. A body
        anchor cannot work — the orb body fills ~97% of the widget
        while listening and its disc is opaque, so bars starting
        outside the body would have nowhere to live and bars inside
        would be painted over. In the edge margin the ring floats
        in the glow around the body,
        outside. Loud bars slide their tips under the body edge —
        they read as radiating from the orb.

        Values ride the display bands' existing fast-attack /
        slow-release smoothing (the _bar_values rates), alpha grows
        with the value, and a peak band (> 0.78) takes the
        highlight colour, mirroring the shared analyser. This layer is
        data, not decoration: a per-bar floor of 0.02 keeps noise
        invisible, and when every band is under it the ring draws
        nothing at all — a quiet room shows a quiet orb.
        """
        bands = self.display_bands
        if not bands or max(bands) < 0.02:
            return
        # Conversion Phase 0: the ring anchors to the
        # margin-inset short side, not the widget edge.
        s = self._painted_min()
        r_out = s * 0.492
        max_len = s * 0.080
        min_len = s * 0.014
        pen = QPen(mid, max(1.8, s * 0.0095))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i, (ca, sa) in enumerate(_SPEC_DIRS_16):
            v = bands[i] if i < len(bands) else 0.0
            if v < 0.02:
                continue
            v = min(1.0, v)
            length = min_len + (max_len - min_len) * v
            col = highlight if v > 0.78 else mid
            pen.setColor(QColor(col.red(), col.green(), col.blue(),
                                int(45 + 205 * v)))
            painter.setPen(pen)
            painter.drawLine(
                QPointF(cx + ca * (r_out - length),
                        cy + sa * (r_out - length)),
                QPointF(cx + ca * r_out, cy + sa * r_out))

    def _aur_motion_targets(self):
        """Per-state Aura motion targets: (core brightness, current
        swirl, core pulse, orbit scale). _animate_frame crossfades
        the live parameters toward these over ~600 ms.

        v5.44 F6: the fourth value is the currents' orbit scale —
        THINKING pulls the blobs in to 0.55 of their rest radii
        while its swirl target (2.6 vs idle's 0.35) quickens the
        orbit ~4.5x, so the state reads at a glance as the same
        currents, tightened and hurrying. Every other state keeps
        the currents near their v5.17 spread."""
        if self._is("THINKING"):
            return 0.78, 2.6, 0.35, 0.55
        if self._is("DISCONNECTED"):
            return 0.34, 0.12, 0.10, 1.0
        if self._is("ERROR"):
            return 0.55, 0.30, 0.20, 1.0
        if self._is("SPEAKING"):
            return 0.92, 0.70, 1.0, 0.95
        if self._is("CONNECTING"):
            return 0.80, 1.50, 0.55, 0.90
        if self._is("LISTENING"):
            return 0.88, 0.85, 0.50, 0.92
        return 0.62, 0.35, 0.25, 1.0

    def _aur_rim_gain(self) -> float:
        """F6 rim gain for the Aura fresnel rim: 1.0 at rest, up to
        1.6 at full voice. The single place the painter reads the
        rim breath, so the trait is provable without pixel-peeping:
        _aur_rim_breath is driven by the energy spring in
        _animate_frame, never by a fixed oscillator."""
        return 1.0 + 0.60 * self._aur_rim_breath

    def _paint_aura(self, painter, cx, cy, R, highlight, mid, edge,
                    energy, lod: int = 0):
        """Orb v5.17 'Aura' — a luminous energy sphere.

        The anti-dial: no ticks, no rings, no gauge, no text. Back
        to front: a deep-colour atmosphere in the palette edge that
        spills well past the body; the sphere itself, shaded deep
        edge tones with its focal light pulled high; three or four
        internal "currents" — soft offset blobs in the palette hues
        orbiting at different radii and speeds, clipped inside the
        glass; a bright living core; and a faint fresnel rim (dim
        full contour, bright top arc, whisper of bounce below).

        B-R1-5 LerSent liquid glass (gui/visualizer_glass.py): the
        silhouette is the wobbled glass contour (never a perfect
        circle), and over the rim sit the glass wall (two shell
        strokes), the top-light specular and the dispersion fringe
        — all from the same state + smoothed energy, eased, never
        snapped. The glass is Aura blue (the theme triple), abstract
        and non-face; the four live states' glass signatures are
        pairwise distinct, so the state reads at a glance.
        State rides the crossfaded _aur_mp parameters and the
        _aur_energy spring: idle breathes slowly, listening
        brightens the core and quickens the currents, thinking
        swirls them markedly faster, speaking pulses the core with
        the shared EQ energy. ERROR / DISCONNECTED arrive through
        the shared palette, like every style. lod 2 drops one
        current and the top-light arc.

        v5.44 F6 signatures, all inside this same language: THINKING
        tightens the currents' orbits (the crossfaded orbit scale)
        on top of the faster swirl; CONNECTING passes one slow
        brightening swell through the core per entry; SPEAKING
        breathes the fresnel rim at the voice's own pace (the
        energy-driven rim breath). No new shapes, no dial furniture.
        """
        dimf = 0.55 if self._is("DISCONNECTED") else 1.0
        bright, _swirl, pulse_amt = self._aur_mp
        live = max(0.0, self._aur_energy)
        bar_mean = sum(self._bar_values) / len(self._bar_values)
        bar_pulse = bar_mean if self._is("SPEAKING") else 0.0

        # -- outer atmosphere ----------------------------------------
        # Conversion Phase 0: the atmosphere is capped 16 px
        # inside the widget edge (and never below the body
        # radius) — pre-fix it was R * 1.60 uncapped, wider
        # than the widget, and its lower edge washed over the
        # wordmark and state line below the hero.
        atm_r = min(R * 1.60,
                    max(R, min(self.width(), self.height()) / 2.0
                        - _ORB_EDGE_INSET))
        atm = QRadialGradient(cx, cy, atm_r)
        # F6: the CONNECTING swell lifts the whole atmosphere a
        # touch as the wave passes through the core below.
        a = int((30.0 + 42.0 * bright + energy * 22.0
                 + 24.0 * self._aur_swell
                 + 20.0 * self._ack_pulse) * dimf)
        atm.setColorAt(0.0, QColor(edge.red(), edge.green(),
                                   edge.blue(), a))
        atm.setColorAt(0.55, QColor(edge.red(), edge.green(),
                                    edge.blue(), int(a * 0.55)))
        atm.setColorAt(0.72, QColor(edge.red(), edge.green(),
                                    edge.blue(), int(a * 0.22)))
        atm.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(atm))
        painter.drawEllipse(QRectF(cx - atm_r, cy - atm_r,
                                   atm_r * 2, atm_r * 2))

        # -- B-R1-5 LerSent glass: the liquid silhouette --------------
        # The body + currents are clipped to the wobbled contour (one
        # surface, not a matte disc in a perfect circle). The snapshot
        # comes from the state mixin — the SAME state + smoothed
        # energy the orb already has, never a second source; a widget
        # without a glass state (older callers) paints the IDLE base.
        _glass = getattr(self, "_glass", None)
        _snap = _glass.snapshot() if _glass is not None else None
        _phase = _glass.phase if _glass is not None else 0.0
        contour = glass_contour_path(cx, cy, R, _snap, _phase)

        # -- sphere body: deep edge tones, light pulled high ----------
        # The body must READ teal by eye (parent by-eye fix,
        # 2026-11-24): the old body was the edge colour darkened
        # (x0.50 centre, x0.14 at 0.85, near-black (6,8,12) at
        # the rim) — a dark navy ball with only a bright core,
        # so the orb read dark navy, not teal. The body is now
        # the measured teal ramp: bright rim #52C5CC at the
        # centre (under the living core), mid teal #2F8FA0 at
        # 0.55, measured body #227084 at 0.85, and the darker
        # measured body #204A56 at the rim — a clearly-teal
        # sphere, not a dark one.
        body = QRadialGradient(QPointF(cx, cy), R * 1.05,
                               QPointF(cx, cy - R * 0.26))
        body.setColorAt(0.0, QColor(mid.red(), mid.green(),
                                    mid.blue(), 238))
        body.setColorAt(0.55, QColor(47, 143, 160, 244))
        body.setColorAt(0.85, QColor(edge.red(), edge.green(),
                                     edge.blue(), 248))
        body.setColorAt(1.0, QColor(32, 74, 86, 250))
        painter.setBrush(QBrush(body))
        painter.drawPath(contour)

        # -- internal currents: palette blobs orbiting inside ---------
        clip = contour
        painter.save()
        painter.setClipPath(clip)
        blob_cols = (mid, edge, highlight, edge)
        blobs = self._aur_blobs if lod < 2 else self._aur_blobs[:3]
        for i, (ang, orad, _spd, brad, twk) in enumerate(blobs):
            col = blob_cols[i % len(blob_cols)]
            rad = math.radians(ang)
            # F6: the orbit radius rides the crossfaded orbit scale
            # — THINKING draws every current in toward the core.
            bx = cx + math.cos(rad) * R * orad * self._aur_orbit_scale
            by = cy + math.sin(rad) * R * orad * self._aur_orbit_scale
            br = R * brad
            twinkle = 0.75 + 0.25 * math.sin(
                self.breathe_phase * 2.0 + twk)
            # Same orb-brightness correction (2026-11-24): the
            # currents ran hot enough to wash the body light
            # cyan when a blob sat near the core. Slightly
            # dimmer; still visible, still quickening with the
            # state (the twinkle and the bright slope stay).
            a_b = int((48.0 + 44.0 * bright) * twinkle * dimf)
            blob = QRadialGradient(bx, by, br)
            blob.setColorAt(0.0, QColor(col.red(), col.green(),
                                        col.blue(), a_b))
            blob.setColorAt(0.6, QColor(col.red(), col.green(),
                                        col.blue(), int(a_b * 0.45)))
            blob.setColorAt(1.0, QColor(col.red(), col.green(),
                                        col.blue(), 0))
            painter.setBrush(QBrush(blob))
            painter.drawEllipse(QRectF(bx - br, by - br, br * 2, br * 2))
        painter.restore()

        # -- R7-2: voice-reactive particle sphere -------------------
        # The jarvis-mark-i pattern as own code: a shell of
        # particles on a slowly turning sphere, swelling with the
        # same smoothed voice energy the core already reads.
        # Painted inside the glass clip, under the living core.
        _sphere = getattr(self, "_sphere", None)
        if _sphere is not None:
            painter.save()
            painter.setClipPath(clip)
            paint_particle_sphere(painter, cx, cy, R, _sphere,
                                  energy, highlight, mid, edge,
                                  dimf, lod)
            painter.restore()

        # -- living core ------------------------------------------------
        # Scale and brightness breathe at rest, surge on the spring
        # energy, and — while SPEAKING — pulse with the EQ bars,
        # the same feed the shared speech motion reads.
        # F6: the CONNECTING swell swells this core — one slow
        # brightening wave per entry, peaking at +11 % radius and
        # +20 % alpha, then gone until the next entry.
        # Orb-brightness correction (owner, 2026-11-24): the
        # 11:56 render read washed-out light cyan — the living
        # core was too large and too white-hot, so it filled
        # the sphere instead of reading as a compact bright
        # core inside a dark teal body. The core is smaller
        # (0.25R) and dimmer at rest; it still brightens with
        # voice energy (the slope) and still pulses with the
        # EQ bars and the CONNECTING swell, so the live states
        # keep their read. The 11:04 target is a dark teal
        # sphere with a bright core, teal rim and gold ring —
        # not a bright cyan ball. The background glow is NOT
        # touched here (owner: dark orb + glowing background).
        core_r = R * (0.25 + 0.02 * math.sin(self.breathe_phase)
                      + 0.13 * live * pulse_amt + 0.10 * bar_pulse
                      + 0.11 * self._aur_swell
                      + 0.08 * self._ack_pulse)
        core_a = int(min(255, (112.0 + 68.0 * bright)
                         * (1.0 + 0.20 * self._aur_swell) * dimf))
        core = QRadialGradient(cx, cy, core_r)
        core.setColorAt(0.0, QColor(255, 255, 255, core_a))
        core.setColorAt(0.28, QColor(highlight.red(),
                                      highlight.green(),
                                      highlight.blue(),
                                      int(core_a * 0.82)))
        core.setColorAt(0.62, QColor(mid.red(), mid.green(),
                                     mid.blue(), int(core_a * 0.42)))
        core.setColorAt(1.0, QColor(mid.red(), mid.green(),
                                    mid.blue(), 0))
        painter.setBrush(QBrush(core))
        painter.drawEllipse(QRectF(cx - core_r, cy - core_r,
                                   core_r * 2, core_r * 2))

        # -- UI Phase 1 teal redesign: geodesic mesh ----------------
        # The logo's wireframe mesh, over the particle sphere and
        # the core (so it reads across the bright centre), clipped
        # to the glass contour. It turns with the same yaw/pitch/
        # swell as the particles — one sphere, not two.
        if _sphere is not None:
            painter.save()
            painter.setClipPath(clip)
            paint_mesh_sphere(painter, cx, cy, R, _sphere,
                              highlight, mid, dimf, lod)
            painter.restore()

        # -- fresnel rim: dim full contour, bright crown, low bounce -
        # F6: while SPEAKING the rim breathes with the voice — its
        # gain is the energy-driven _aur_rim_breath, so a louder
        # passage lifts the rim's alpha and softens its crown wider,
        # and silence lets it settle back to this v5.17 rest.
        # B-R1-5: the rim follows the same wobbled contour as the
        # body (glass_contour_arc) — a rim on the perfect circle
        # would visibly detach from the liquid edge at full deform.
        rim_boost = (0.6 + 0.6 * bright) * self._aur_rim_gain()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(highlight.red(), highlight.green(),
                                   highlight.blue(),
                                   int(46 * rim_boost * dimf)), 1.0))
        painter.drawPath(contour)
        if lod < 2:
            painter.setPen(QPen(QColor(255, 255, 255,
                                       int(84 * rim_boost * dimf)),
                                1.2 + 0.8 * self._aur_rim_breath))
            painter.drawPath(glass_contour_arc(
                cx, cy, R, _snap, _phase, -145.0, 110.0))
        painter.setPen(QPen(QColor(edge.red(), edge.green(), edge.blue(),
                                   int(40 * rim_boost * dimf)), 1.0))
        painter.drawPath(glass_contour_arc(
            cx, cy, R, _snap, _phase, 35.0, 110.0))

        # -- B-R1-5 LerSent glass: shell + specular + fringe ---------
        # Drawn last (over the rim): the glass wall, the top-light
        # specular and the dispersion fringe at the rim — all from
        # the same snapshot + phase as the contour above.
        paint_glass_layers(painter, cx, cy, R, highlight, mid, edge,
                           _snap, _phase, dimf)

        # -- UI Phase 1 teal redesign: orbit rings + reticle --------
        # The logo's tilted orbit rings (static structure, three
        # flattened ellipses at different tilts) and the
        # reference's reticle ticks (short radial ticks at the
        # four diagonals + the cardinal points, just outside the
        # outer faint ring). Thin and faint, in the palette mid —
        # the gold ring below stays the single bright, live ring.
        if lod < 2:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for ring_r, tilt, alpha, width in (
                    (R * 1.18, -18.0, 110, 1.2),
                    (R * 1.28, 24.0, 90, 1.0),
                    (R * 1.38, -38.0, 70, 1.0)):
                painter.save()
                painter.translate(cx, cy)
                painter.rotate(tilt)
                painter.setPen(QPen(QColor(
                    mid.red(), mid.green(), mid.blue(),
                    int(alpha * dimf)), width))
                painter.drawEllipse(QRectF(
                    -ring_r, -ring_r * 0.32,
                    ring_r * 2, ring_r * 0.64))
                painter.restore()
            # Reticle ticks: short radial strokes from the outer
            # faint ring (1.22R) outward, at the diagonals and the
            # cardinals — the reference's crosshair.
            tick_col = QColor(mid.red(), mid.green(), mid.blue(),
                              int(90 * dimf))
            painter.setPen(QPen(tick_col, 1.0))
            for deg in (45.0, 135.0, 225.0, 315.0,
                        0.0, 90.0, 180.0, 270.0):
                rad = math.radians(deg)
                inner = R * 1.22
                outer = R * 1.30 if deg % 90.0 == 45.0 else R * 1.28
                painter.drawLine(
                    QPointF(cx + math.cos(rad) * inner,
                            cy + math.sin(rad) * inner),
                    QPointF(cx + math.cos(rad) * outer,
                            cy + math.sin(rad) * outer))

        # -- UI Phase 1 (R2/R5): the gold ring -----------------------
        # The ring the probe found missing (the "gold ring" was a
        # 1 px blue fresnel rim, invisible): a real gold ring just
        # outside the body at R x 1.06, plus two faint concentric
        # rings. Its brightness rides the smoothed voice energy
        # (ring_alpha_for_energy) — never a timer pulse. In
        # private mode the ring is the palette's grey, not gold:
        # the palette is the grey triple there, so `mid` is grey.
        if getattr(self, "_private", False):
            ring_col = mid
        else:
            ring_col = QColor(AMBER)
        ring_a = ring_alpha_for_energy(energy)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ring_col.red(), ring_col.green(),
                                   ring_col.blue(), ring_a), 2.0))
        ring_r = R * 1.06
        painter.drawEllipse(QRectF(cx - ring_r, cy - ring_r,
                                   ring_r * 2, ring_r * 2))
        # The two faint concentric rings are the reticle rings
        # (reference) — static structure in the palette mid, NOT
        # gold. The gold ring above stays the single gold, live
        # ring; in private mode `mid` is the grey triple, so they
        # go grey with it.
        for factor, div in ((1.14, 3), (1.22, 5)):
            faint_r = R * factor
            painter.setPen(QPen(QColor(mid.red(), mid.green(),
                                       mid.blue(),
                                       max(1, ring_a // div)), 1.0))
            painter.drawEllipse(QRectF(cx - faint_r, cy - faint_r,
                                       faint_r * 2, faint_r * 2))
