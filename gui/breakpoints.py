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
- NARROW, 908 <= width < 1008: the stage floor relaxes 420 -> 320
  and the hero minimum width relaxes 400 -> 320 (the orb
  visualizer's own floor is 320, so nothing paints below its
  floor, and the hero carries no laid-out text to garble).
  Sidebar 220, chat floor 300, composer floors, and the palette
  cap/clamp are UNCHANGED at this tier:
  220 + 56 + 320 + 12 + 300 = 908.
- COMPACT, width < 908 (Stage 0 features batch 5, parent ruling):
  the sidebar collapses to a 64 px icon rail (labels hidden,
  tooltips carry the names, nothing clipped or unreachable),
  the stage floor relaxes 320 -> 240, the hero minimum 320 -> 240
  and the chat floor 300 -> 240 (the chat is still fully usable
  at 240 — the composer wraps, the messages scroll). The sums
  hold with slack: 64 + 56 + 240 + 12 + 240 = 612 <= 640.
  The old "NO DRAWER / sidebar fixed 220 at every tier" decision
  is superseded by this ruling below 908 only — there is still
  no overlay drawer: the rail IS the navigation at compact
  widths, every page stays one click away.

Window minimum (640, 500): width is the COMPACT derivation; height
500 fits both measured work-area heights (576, 516) with slack
while keeping titlebar + header + home strip + composer + a scroll
viewport visible. It is a fit-chosen floor, not a widget sum —
with scroll-safe pages the content minimum no longer dictates it.

Default size: min(1108, availW - 40) x min(750, availH - 40), so the
owner's 1920x1080@125% machine still opens at exactly 1108x750
while compact displays get a fitting default. A default below the
(640, 500) minimum is clamped UP by Qt's minimum-size enforcement
to (640, 500), which still fits both measured work areas — the
clamp is part of the design, not a defeat of it.
"""

from __future__ import annotations

SIDEBAR_WIDTH = 220
SIDEBAR_WIDTH_COMPACT = 64
PAGE_MARGINS = 56
SPLITTER_HANDLE = 12
STAGE_FLOOR_COMFORT = 420
STAGE_FLOOR_NARROW = 320
STAGE_FLOOR_COMPACT = 240
CHAT_FLOOR = 300
CHAT_FLOOR_COMPACT = 240
CHAT_PREFERRED = 400
COMPACT_WIDTH = 908
COMFORT_WIDTH = 1008
WINDOW_MINIMUM = (640, 500)
DEFAULT_SIZE = (1108, 750)
DEFAULT_MARGIN = 40
HERO_MIN_WIDTH_COMFORT = 400
HERO_MIN_WIDTH_NARROW = 320
HERO_MIN_WIDTH_COMPACT = 240


def tier_for(width: int) -> str:
    """Width tier name: "compact" below 908, "narrow" below the
    comfort width, else "comfort". The boundaries are the
    derivations themselves — at 908 the narrow floors still fit
    exactly (chat 300, stage 320); at 1008 the comfort floors
    still fit exactly (chat 300, stage 420)."""
    if width < COMPACT_WIDTH:
        return "compact"
    return "narrow" if width < COMFORT_WIDTH else "comfort"


def stage_floor_for(width: int) -> int:
    """Stage minimum width at this window width."""
    if width < COMPACT_WIDTH:
        return STAGE_FLOOR_COMPACT
    return (STAGE_FLOOR_NARROW if width < COMFORT_WIDTH
            else STAGE_FLOOR_COMFORT)


def hero_min_width_for(width: int) -> int:
    """Hero minimum width at this window width. Must relax with
    the stage floor: a 400 px hero minimum inside a 320 px stage
    would let the splitter clamp the chat below its 300 floor."""
    if width < COMPACT_WIDTH:
        return HERO_MIN_WIDTH_COMPACT
    return (HERO_MIN_WIDTH_NARROW if width < COMFORT_WIDTH
            else HERO_MIN_WIDTH_COMFORT)


def chat_floor_for(width: int) -> int:
    """Chat minimum width at this window width: 240 in the
    compact tier (the chat stays fully usable — the composer
    wraps, messages scroll), 300 elsewhere."""
    return CHAT_FLOOR_COMPACT if width < COMPACT_WIDTH else CHAT_FLOOR


def chat_target(avail: int, target: int, width: int) -> int:
    """Splitter chat width for a window of this width: the
    requested target (frozen 400 at first show, else the stored /
    dragged / current width) clamped to [tier chat floor, avail
    minus the tier's stage floor]. Preserves the S5 rule at comfort
    widths and the drag-preserved width across the tier boundary
    (a drag down through 1008 keeps chat 300 while the stage
    shrinks 420 -> 419 -> ... -> 320: no cliff, no jump). At 640
    the compact floors give chat 280 with the stage at its 240
    floor (avail 520 = 640 - 64 rail - 56 margins)."""
    floor = chat_floor_for(width)
    stage_floor = stage_floor_for(width)
    return max(floor, min(target, avail - stage_floor))


def default_window_size(avail_w: int, avail_h: int) -> tuple[int, int]:
    """Initial window size for an available geometry: the frozen
    default, pulled inside the work area with a 40 px margin.
    Never floored here — Qt's minimum-size enforcement clamps a
    below-minimum default up to WINDOW_MINIMUM, which still fits
    both measured compact work areas."""
    return (min(DEFAULT_SIZE[0], avail_w - DEFAULT_MARGIN),
            min(DEFAULT_SIZE[1], avail_h - DEFAULT_MARGIN))


# ---------------------------------------------------------------- UI P1 §5
# Screenshot sizes (ruling 2026-11-24, Aayur — supersedes the
# 2026-11-23 "don't increase the orb" ruling and the old §5
# budgets). The orb grows to 19.5% of the window width at every
# tier, funded by the chat (400 -> ~230 at 1280), the sidebar
# (200 -> ~170) and the right rail (180 -> ~150); the left rail
# grows (180 -> ~224). The 1024/900/640 rows are unchanged from
# the old §5. Tier boundaries are the midpoints between the
# table's window widths, so a resize to exactly 1280 / 1920 /
# 1024 / 900 / 640 lands on its own tier.
ORB_DIAMETER_FRACTION = 0.195

# (window width, sidebar, chat, rail, centre, right); rail None
# means the left rail collapses into the header cluster + an
# overlay toggle (1024/900); right 0 at 640 means the single
# column of the orientation switch. Widths are the §5 table,
# verbatim.
_HUD_TIERS: tuple[tuple[int, int, int, int | None, int, int], ...] = (
    (1920, 255, 345, 335, 740, 225),
    (1280, 170, 230, 224, 494, 150),
    (1024, 200, 400, None, 260, 164),
    (900, 64, 400, None, 272, 164),
    (640, 64, 400, None, 176, 0),
)


def _hud_tier(width: int) -> tuple[int, int, int, int | None, int, int]:
    """The §5 row for this window width. Boundaries are the
    midpoints between the table's window widths, so a resize
    to exactly 1280 / 1920 / 1024 / 900 / 640 lands on its
    own tier."""
    if width >= 1600:
        return _HUD_TIERS[0]
    if width >= 1152:
        return _HUD_TIERS[1]
    if width >= 962:
        return _HUD_TIERS[2]
    if width >= 770:
        return _HUD_TIERS[3]
    return _HUD_TIERS[4]


def hud_sidebar_for(width: int) -> int:
    """Sidebar width at this window width (§5)."""
    return _hud_tier(width)[1]


def hud_chat_for(width: int) -> int:
    """Chat-panel target width at this window width (§5)."""
    return _hud_tier(width)[2]


def hud_columns_for(width: int) -> tuple[int | None, int, int]:
    """(rail, centre, right) column widths at this window width
    (§5). Rail None means the left rail is collapsed."""
    row = _hud_tier(width)
    return (row[3], row[4], row[5])


def hud_stage_min_for(width: int) -> int:
    """Stage minimum width at this window width (§5): the column
    budgets summed (the 8 px column gaps are internal insets,
    so the stage is exactly the sum)."""
    rail, centre, right = hud_columns_for(width)
    return (rail or 0) + centre + right


def hud_orb_diameter_for(width: int) -> int:
    """Orb body diameter at this window width (§5): 19.5% of the
    window width. H-02/H-18: the orb never exceeds this size,
    even when columns collapse and the centre takes the full
    width — it is a max, not a target to fill."""
    return round(width * ORB_DIAMETER_FRACTION)


def hud_visualizer_height_for(width: int) -> int:
    """Visualizer widget height at this window width: tall
    enough for the §5 orb + its gold / faint rings at rest
    (outermost ring 1.22 x the body radius) with a small
    margin. The listening swell may clip the faint rings
    slightly at peak voice energy — a transient, noted in
    the Phase 1 report, not a layout defect."""
    diameter = hud_orb_diameter_for(width)
    return int(diameter * 1.22) + 8
