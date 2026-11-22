"""Design tokens for VYUK (split from gui/style.py, GUI split #6).

All UPPER constants, font stacks, font-file tuples, and
first_font_family. No Qt state; importable headlessly.
"""

import os

# ------------------------------------------------------------- design tokens
# Spacing lives on a 4-pt rhythm; radii and the type scale have one
# home so pages stop drifting apart.
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24

# Radius scale — one value per element class.
# Phase D (D13): the frameless app shell's silhouette is PAINTED
# by AppShell (chamfered top-left + bottom-right, 6 px arcs on the
# other corners) — this token tracks the arc radius for the
# legacy QFrame#appShell rule, which currently has no widget.
RADIUS_WINDOW = 6   # the frameless app shell's arc radius
RADIUS_DIALOG = 2    # floating dialogs (command palette)
RADIUS_CARD = 2      # cards, panels, chat bubbles (cut corners)
RADIUS_BTN = 2       # buttons, inputs, nav rows, brand tile
RADIUS_INPUT = RADIUS_BTN
RADIUS_SM = 2        # window controls, palette rows, square slider handle
# Pill tier (Phase C): pills and chips are cut-corner chips at 2 px.
# Only the mic (56 px) and the icon buttons (42 px) stay true
# circles. Qt does NOT clamp an oversized border-radius the way CSS
# does: a radius past half the smaller side renders SQUARE corners
# (proven in the Wave 1B renders — 999px turned the mic, chips and
# icon buttons into boxes). Never "simplify" the circles back to
# one big number.
RADIUS_PILL = 2      # primary / danger action pills (cut-corner chips)
RADIUS_PILL_MIC = 28   # the hero mic (56px circle)
RADIUS_PILL_ICON = 21  # round icon buttons (42px circle)
RADIUS_CHIP = 2        # chip buttons and suggestion chips
RADIUS_PILL_SM = 2     # command pills, the status pill
RADIUS_DOCK = 2        # the dock input (chamfered in Phase D)

TYPE_TITLE = 20        # page titles (weight 600); also the Activity headline line (Build 2)
TYPE_ROW = 16          # Screen 3 Activity: primary values inside rows, and section headings (Build 2: weight 600 separates a heading from a weight-400 row title; ladder rung 13 x 1.2 = 15.6, 0.4 off)
TYPE_CARD_TITLE = 13   # card titles (weight 600)
TYPE_BODY = 13
TYPE_CAPTION = 11.5    # muted captions / hints
TYPE_MICRO = 10.5      # timestamps, eyebrow microcopy

# Chrome palette (the app has one dark chrome; the per-user "themes"
# are orb accent palettes in visualizer.THEMES, applied to the orb and
# the ambient glow — QSS accents stay on these two house colours).
ACCENT = "#35D6FF"
ACCENT_2 = "#3D8BFF"
TEXT = "#E6FBFF"
TEXT_MUTED = "#B0C1CD"
# Command Deck (Phase B): TEXT_FAINT is the guide's neutral
# #7E9AA6. Every text-on-surface pair in every theme is pinned
# >= 4.5:1 by tests/test_commanddeck_phaseB.py (the old v5.51 F-4
# note measured against the retired surfaces).
TEXT_FAINT = "#7E9AA6"
TEXT_DISABLED = "rgba(230, 251, 255, 115)"  # TEXT at ~45%

# Sci-fi Home (2026-11-16): the ambient particle field is re-tinted
# into the Aura family — the orb's blue and the text's near-white —
# so the field reads as dust in the orb's lamplight, not a second
# light. (R, G, B) tuples mirroring ACCENT_2 and TEXT.
PARTICLE_AURA_BLUE = (61, 139, 255)
PARTICLE_AURA_PALE = (230, 251, 255)

# Elevation steps over the deep background.
BG = "#04090E"
SURFACE_1 = "#0C1D28"  # dialogs: one solid step above the window
SURFACE_2 = "#08131B"  # popups / palette: the highest resting step
SURFACE_3 = "#122636"  # raised / selected
# Code blocks stay dark on every theme: the pale-green code text
# (style_sheet QPlainTextEdit#codeText) reads only on a dark
# panel. On the dark themes this is the effective colour of the
# old 0.28-black wash over BG, so nothing changes there; on a
# light theme it keeps the panel dark instead of letting the
# wash go pale.
CODE_BG = "#0A0E13"
# Floating status strip (always-on-top bar): the same deep surface as
# mini_orb / visualizer_paint QColor(16, 19, 26, 235), at ~92% opacity.
# Flaw sweep 2026-11-16 (parent fix): this literal lived in
# gui/status_strip.py and failed test_no_new_colour_literals; it now
# lives here, in the token system, where the guard expects it.
STATUS_STRIP_BG = "rgba(16, 19, 26, 235)"

# Command Deck line roles (guide §5.2): cyan hairlines. BORDER_*
# stay white-alpha until Phase C, when HudPanel owns outlines.
LINE_SUBTLE = "rgba(53, 214, 255, 36)"  # cyan 14%
LINE_STRONG = "rgba(53, 214, 255, 102)"  # cyan 40% (guide range 30-45%)

# Hairlines (Phase C): BORDER_* spell the LINE_* roles — the
# white-alpha hairlines are retired. HudPanel owns outlines from
# here; these constants stay so existing ${BORDER} sites migrate
# with the value, and themed builds remap the cyan triplet.
BORDER_FAINT = LINE_SUBTLE
BORDER = LINE_SUBTLE
BORDER_STRONG = LINE_STRONG
BORDER_TOP = LINE_STRONG
# The resting cards' top hairline — the same value as BORDER_TOP
# today, but a token of its own so themed builds (build_stylesheet)
# can brighten the cards' top edge for the glass treatment without
# touching the dialogs that share BORDER_TOP.
CARD_TOP = BORDER_TOP

# Quiet glass for secondary actions — RETIRED in Phase C (solid
# surfaces replace it; see the button rules). Kept defined so
# _TOKENS stays stable; no template rule uses them anymore.
GLASS = "rgba(255, 255, 255, 10)"
GLASS_HOVER = "rgba(255, 255, 255, 17)"
GLASS_PRESSED = "rgba(255, 255, 255, 25)"

# Resting card fill — RETIRED in Phase C: card rules are solid
# SURFACE_2 now. Kept defined (themed builds still carry the keys)
# so _TOKENS stays stable; no template rule uses them anymore.
CARD_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    "stop:0 rgba(255, 255, 255, 22), stop:1 rgba(255, 255, 255, 10))"
)
# Result cards kept the accent-wash identity — RETIRED in Phase C
# with CARD_GRADIENT (result cards are solid SURFACE_2 with an
# accent left bar now).
CARD_GRADIENT_INDIGO = (
    "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
    "stop:0 rgba(53, 214, 255, 40), stop:1 rgba(53, 214, 255, 18))"
)

# Inputs are a well, not a slab: darker than the surface around them.
INPUT_BG = "rgba(0, 0, 0, 96)"
INPUT_BG_FOCUS = "rgba(53, 214, 255, 12)"
INPUT_BG_DISABLED = "rgba(0, 0, 0, 48)"

# Selection is the accent at ~38% with white text, everywhere.
SELECTION_BG = "rgba(53, 214, 255, 96)"

# Scrollbar thumbs (Phase C, guide §8): cyan at 30% rest, 70% on
# hover; pressed holds the hover strength (no third value invented).
SCROLL_REST = "rgba(53, 214, 255, 76)"
SCROLL_HOVER = "rgba(53, 214, 255, 178)"
SCROLL_PRESSED = "rgba(53, 214, 255, 178)"

# Command Deck role colours (guide §5.2). Danger keeps its red
# identity across states; the text tint is the same role colour
# (6.4:1 on BG, pinned by the Phase B contrast test).
AMBER = "#FFB020"
OK = "#5CF2A8"
DANGER = "#FF5468"
DANGER_TEXT = "#FF5468"
NEUTRAL = "#7E9AA6"

# Command Deck (Phase B): primary actions are a solid cyan outline
# with a cyan wash on hover — no gradients on buttons. The wash
# spellings use the accent triplet, so themed builds rewrite them
# per theme via _remap_colors (like every other accent-alpha value).
PRIMARY_WASH = "rgba(53, 214, 255, 28)"
PRIMARY_WASH_PRESSED = "rgba(53, 214, 255, 48)"
# The pale rim around the brand mark — the soft-glow stand-in.
PRIMARY_RIM = "rgba(255, 255, 255, 90)"

# The one focus language (see the header): a 1px accent ring. A
# token of its own so a theme can thicken the ring — the Contrast
# theme uses 2px — without touching the template. The substituted
# value is exactly what the focus rules spelled out before.
FOCUS_BORDER = "1px solid #35D6FF"

# B-R1-7r (2026-11-09): the body face is Sora — the variable font
# (wght 100–800, single file) from the google/fonts repo (OFL 1.1,
# licence in gui/assets/fonts/OFL-Sora.txt). One file covers every
# weight the UI uses (400/500/600/700); a surface that asks for a
# weight outside the axis gets Qt's nearest-weight match, honestly
# — never a silent swap to another family. B-R1-7's Rajdhani
# (Regular/Medium/SemiBold/Bold) is REMOVED in this batch: the
# owner picked Sora ("whatever brings us close to scifi") over the
# Hindi-first Rajdhani pick, which had no recorded justification.
_FONT_FILES = (
    "Sora-Variable.ttf",
)
# B-R1-7: one bundled DISPLAY face for headline/title elements
# only — Orbitron, the variable font (wght 400–900, single file)
# from the google/fonts repo; SIL OFL 1.1, licence in
# gui/assets/fonts/OFL-Orbitron.txt. Body text is Sora (B-R1-7r); the
# display stack below is substituted into the headline selectors
# (brandName / pageTitle / stateLabel / paletteInput) in _QSS.
# Orbitron replaces Space Grotesk (v5.51 F-2) and Chakra Petch
# (Command Deck HUD face) as the display face; both files are
# removed in the same batch — one stack, no dead weight.
DISPLAY_FAMILY = "Orbitron"
# B-R1-7: HUD words are Orbitron first — the display face IS the
# HUD face now (the JARVIS-like futuristic face the HUD wants) —
# falling back to the system faces, so the stack renders correctly
# before the OFL files are supplied (they are bundled by this
# batch; the fallbacks stay for machines without the bundle).
DISPLAY_FONT_STACK = (
    "'Orbitron', "
    "'Segoe UI Variable', 'Segoe UI', sans-serif"
)
# Numbers and micro-labels: Share Tech Mono first, then the
# platform mono fallbacks (Cascadia, then Consolas).

def first_font_family(stack: str) -> str:
    """First family of a QSS font stack, unquoted.

    The stacks above are single QSS strings ("'Chakra Petch',
    'Space Grotesk', ..."), so stack[0] is a quote character, not
    a family — QFont(stack[0]) builds an empty family and Qt
    falls back to a symbol font (found by the Phase C renders:
    the panel eyebrow rendered as outline glyphs). Callers that
    need a QFont use this, never stack[0].
    """
    return stack.split(",", 1)[0].strip().strip("'\"")
MONO_FONT_STACK = (
    "'Share Tech Mono', 'Cascadia Code', 'Cascadia Mono', "
    "'Consolas', monospace"
)
_DISPLAY_FONT_FILES = (
    "Orbitron-Variable.ttf",
)
# B-R1-7: the HUD face IS the display face now (Orbitron), so this
# list is empty by design — kept (not deleted) so the loader loop
# in gui/style.py keeps its four-list shape and a future HUD-only
# face has somewhere to land.
_HUD_FONT_FILES = ()
_MONO_FONT_FILES = (
    "ShareTechMono-Regular.ttf",
)
