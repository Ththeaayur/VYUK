"""
Premium Orb Visualizer for VYUK (P0 + Phase 2 + Phase 3 + Orb v4 + Orb v5).

- One orb style, Aura (Orb v5.17 — a luminous energy sphere:
  deep-colour atmosphere, a bright living core and slow internal
  currents in the palette hues; no ticks, no rings, no gauge).
  Aura-only since 2026-11-01 by owner decision ("keep only the
  aura"); the other styles (Apex, Reactor, Sphere, Ring, Wave,
  Prism, Halo, Eclipse) were deleted, and the style is no longer
  a user setting.
- Curated accent themes; palette CROSSFADES (colours are interpolated
  every frame) — the orb never hard-cuts between colours. A custom
  palette (set_custom_theme, v5.17) installs an arbitrary
  highlight/mid/edge triple that wins over the named theme — the
  crossfade targets it exactly like a theme — until the next
  set_theme() call clears it; that is how Settings themes with no
  orb palette of their own still recolour the orb.
- State is shown by MOTION: idle breathes, listening pulses with the
  voice, thinking swirls, speaking ripples, disconnected is still/grey.
  Colour changes only for meaning (error) or the user's chosen theme.
- Listening ripples (v5.16): while LISTENING, every style sheds soft
  expanding rings drawn under the orb body — spawned at a rate that
  follows the mic level (the update_audio_data feed), so a quiet
  room stays still.
- Listening spectrum ring (v5.17): while LISTENING, 16 radial bars
  anchored in the widget-edge margin trace the smoothed spectrum
  bands around whatever style is showing — data, not decoration:
  a silent room (all bands ~0) draws nothing at all.
- Muted + reduced motion (v5.43 F2/F5): set_muted() installs a real
  MUTED presentation — a dimmed, desaturated palette, a global dim,
  the mini orb's slash badge, stilled ambient motion and an energy
  feed that reads ~0 — whatever the session state, and unmuting
  restores the prior behaviour exactly (the session state and the
  stored audio feed are never touched). set_reduced_motion() —
  pushed by the main window through the same channel as the
  ambience ParticleField — freezes only the ambient accumulators
  (rotation / swirl / current phases); the energy-driven core and
  the palette state changes keep working.
- Power discipline (v5.45 F4): the animation timer pauses while
  the widget is hidden (the mini orb is the visible face then),
  runs at ~30 fps in IDLE / DISCONNECTED and ~60 fps in every
  live state, and every accumulator in _animate_frame is driven
  by the elapsed dt (seconds), never by a raw frame count — so
  the motion character is identical at any frame rate.
"""

# GUI split #7 INTENTIONAL: OrbVisualizer split verbatim into
# the state mixin (visualizer_state.py) and the paint mixin
# (visualizer_paint.py); this file keeps the composed class so
# every existing importer keeps working unchanged. paintEvent
# is defined exactly once, in the paint mixin.
from PySide6.QtWidgets import QWidget

from .visualizer_paint import (
    OrbPaintMixin,
    _ORB_EDGE_INSET,
    _ORB_PAINT_MARGIN,
    _SPEC_DIRS_16,
)
from .visualizer_state import THEMES, OrbStateMixin, _rgb


class OrbVisualizer(OrbStateMixin, OrbPaintMixin, QWidget):
    """Composed orb widget (GUI split #7): state mixin +
    paint mixin, in that order. Method bodies live verbatim
    in the part modules; this class only composes them.
    """


# Backwards-compatible alias — older imports keep working.
CyberpunkHexVisualizer = OrbVisualizer
