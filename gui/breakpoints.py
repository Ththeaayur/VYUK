"""Window-size breakpoints (UI programme, breakpoints slice, 2026-10-30).

Pure logic, no Qt imports: every function here is unit-testable on
any interpreter, and the shell applies the results to real widgets.

Measured breakage this slice solves (audit slice 1, parent-verified):
- 1366x768 @125%: logical 1092x614; work area ~1092x576 after a
  48 px taskbar. The old minimum 1008x620 failed on height
  (physical 1260x775 vs the 768 display, before taskbar); the old
  default 1108x750 failed both dims.
- 200% @1080p: logical 960x540; work area ~960x516. The old
  minimum failed both dims (2016x1240 physical).

Tiers (window WIDTH drives reflow; height is absorbed uniformly by
scroll-safe pages at every height, so height has no tier switch —
a scroll area shows its bars whenever content exceeds the viewport,
at 500 px just as at 750 px):
- COMFORT, width >= 1008: the S5 floors hold exactly
  (220 + 56 + 420 + 12 + 300 = 1008).
- NARROW, width < 1008: the stage floor relaxes 420 -> 320 and the
  hero minimum width relaxes 400 -> 320 (the orb visualizer's own
  floor is 320, so nothing paints below its floor, and the hero
  carries no laid-out text to garble). Sidebar 220, chat floor 300,
  composer floors, and the palette cap/clamp are UNCHANGED at every
  tier: 220 + 56 + 320 + 12 + 300 = 908.
- Nothing collapses and NO DRAWER is built: every measured failure
  above is solved by the tiered floors + scroll-safe pages (see
  the fit checks in tests/test_uibreakpoints.py), so the drawer
  decision — delegated on measurements, not taste — is NO DRAWER.
  The sidebar stays fixed at 220 at every tier.

Window minimum (908, 500): width is the NARROW derivation; height
500 fits both measured work-area heights (576, 516) with slack
while keeping titlebar + header + home strip + composer + a scroll
viewport visible. It is a fit-chosen floor, not a widget sum —
with scroll-safe pages the content minimum no longer dictates it.

Default size: min(1108, availW - 40) x min(750, availH - 40), so the
owner's 1920x1080@125% machine still opens at exactly 1108x750
while compact displays get a fitting default. A default below the
(908, 500) minimum is clamped UP by Qt's minimum-size enforcement
to (908, 500), which still fits both measured work areas — the
clamp is part of the design, not a defeat of it.
"""

from __future__ import annotations

SIDEBAR_WIDTH = 220
PAGE_MARGINS = 56
SPLITTER_HANDLE = 12
STAGE_FLOOR_COMFORT = 420
STAGE_FLOOR_NARROW = 320
CHAT_FLOOR = 300
CHAT_PREFERRED = 400
COMFORT_WIDTH = 1008
WINDOW_MINIMUM = (908, 500)
DEFAULT_SIZE = (1108, 750)
DEFAULT_MARGIN = 40
HERO_MIN_WIDTH_COMFORT = 400
HERO_MIN_WIDTH_NARROW = 320


def tier_for(width: int) -> str:
    """Width tier name: "narrow" below the comfort width, else
    "comfort". The boundary is the S5 derivation itself — at 1008
    the comfort floors still fit exactly (chat 300, stage 420)."""
    return "narrow" if width < COMFORT_WIDTH else "comfort"


def stage_floor_for(width: int) -> int:
    """Stage minimum width at this window width."""
    return (STAGE_FLOOR_NARROW if width < COMFORT_WIDTH
            else STAGE_FLOOR_COMFORT)


def hero_min_width_for(width: int) -> int:
    """Hero minimum width at this window width. Must relax with
    the stage floor: a 400 px hero minimum inside a 320 px stage
    would let the splitter clamp the chat below its 300 floor."""
    return (HERO_MIN_WIDTH_NARROW if width < COMFORT_WIDTH
            else HERO_MIN_WIDTH_COMFORT)


def chat_target(avail: int, target: int, width: int) -> int:
    """Splitter chat width for a window of this width: the
    requested target (frozen 400 at first show, else the stored /
    dragged / current width) clamped to [chat floor, avail minus
    the tier's stage floor]. Preserves the S5 rule at comfort
    widths and the drag-preserved width across the tier boundary
    (a drag down through 1008 keeps chat 300 while the stage
    shrinks 420 -> 419 -> ... -> 320: no cliff, no jump)."""
    floor = stage_floor_for(width)
    return max(CHAT_FLOOR, min(target, avail - floor))


def default_window_size(avail_w: int, avail_h: int) -> tuple[int, int]:
    """Initial window size for an available geometry: the frozen
    default, pulled inside the work area with a 40 px margin.
    Never floored here — Qt's minimum-size enforcement clamps a
    below-minimum default up to WINDOW_MINIMUM, which still fits
    both measured compact work areas."""
    return (min(DEFAULT_SIZE[0], avail_w - DEFAULT_MARGIN),
            min(DEFAULT_SIZE[1], avail_h - DEFAULT_MARGIN))
