"""Shared premium stylesheet for VYUK

Wave 1B (premium wave): the whole QSS is generated from the design
tokens below — one elevation language for every surface. Before this
pass the file was five layered "phases" of literal values and pages
had drifted apart (six different card alphas, three focus styles,
radii from 8 to 24 with no system). Now:

  * Elevation is lightness steps over the deep background: resting
    cards/panels sit one step up (a vertical white wash, 22 -> 10),
    dialogs and popups sit on solid steps (SURFACE_1 / SURFACE_2),
    and inputs sit one step DOWN — a dark well with a hairline rim —
    so fields read as recessed into the surface they live on.
  * Every raised surface gets the same edge treatment: a 1px border
    (BORDER) plus a lighter top edge (BORDER_TOP) — the QSS stand-in
    for a top light source / inner highlight, since Qt has no
    box-shadow.
  * Primary actions wear the house gradient with a pale rim (the
    glow stand-in), a brightened hover gradient and a deepened
    pressed gradient. Secondary actions stay quiet glass.
  * Focus is one language everywhere: a 1px accent ring (never a
    width change, so nothing shifts layout), or an accent wash on
    the borderless chrome buttons. No dotted outlines anywhere.
  * Scrollbars are 8px, thumb-only (no track, no buttons), quiet at
    rest and brighter on hover/press — floating over the content.
"""

# GUI split #6 INTENTIONAL: style split verbatim into style_tokens
# (design tokens), style_sheet (QSS templates), and style_theme
# (accent themes + build_stylesheet); this file re-exports every
# name the original module defined so every existing importer
# (including the core/tokens.py -> gui/style edge) keeps working
# unchanged. _fonts_loaded + load_bundled_fonts stay defined here
# on purpose: the loader's `global _fonts_loaded` must resolve to
# the dict importers see as gui.style, and a plain re-export
# would bind a stale bool value, silently breaking the font-loader
# guard test's flag resets.
import os

from .style_tokens import (
    SPACING_XS,
    SPACING_SM,
    SPACING_MD,
    SPACING_LG,
    SPACING_XL,
    RADIUS_WINDOW,
    RADIUS_DIALOG,
    RADIUS_CARD,
    RADIUS_BTN,
    RADIUS_INPUT,
    RADIUS_SM,
    RADIUS_PILL,
    RADIUS_PILL_MIC,
    RADIUS_PILL_ICON,
    RADIUS_CHIP,
    RADIUS_PILL_SM,
    RADIUS_DOCK,
    TYPE_TITLE,
    TYPE_CARD_TITLE,
    TYPE_BODY,
    TYPE_CAPTION,
    TYPE_MICRO,
    ACCENT,
    ACCENT_2,
    TEXT,
    TEXT_MUTED,
    TEXT_FAINT,
    TEXT_DISABLED,
    BG,
    SURFACE_1,
    SURFACE_2,
    SURFACE_3,
    STATUS_STRIP_BG,
    LINE_SUBTLE,
    LINE_STRONG,
    BORDER_FAINT,
    BORDER,
    BORDER_STRONG,
    BORDER_TOP,
    CARD_TOP,
    GLASS,
    GLASS_HOVER,
    GLASS_PRESSED,
    CARD_GRADIENT,
    CARD_GRADIENT_INDIGO,
    INPUT_BG,
    INPUT_BG_FOCUS,
    INPUT_BG_DISABLED,
    SELECTION_BG,
    SCROLL_REST,
    SCROLL_HOVER,
    SCROLL_PRESSED,
    AMBER,
    OK,
    DANGER,
    DANGER_TEXT,
    NEUTRAL,
    PRIMARY_WASH,
    PRIMARY_WASH_PRESSED,
    PRIMARY_RIM,
    FOCUS_BORDER,
    _FONT_FILES,
    DISPLAY_FAMILY,
    DISPLAY_FONT_STACK,
    first_font_family,
    MONO_FONT_STACK,
    _DISPLAY_FONT_FILES,
    _HUD_FONT_FILES,
    _MONO_FONT_FILES,
)
from .style_sheet import (
    _QSS,
    _TOKENS,
    PREMIUM_STYLE,
    DIALOG_STYLE,
)
from .style_theme import (
    _HOUSE_ACCENT_HOVER,
    _HOUSE_ACCENT_2_HOVER,
    _HOUSE_ACCENT_PRESSED,
    _HOUSE_ACCENT_2_PRESSED,
    _HOUSE_PALE,
    _HOUSE_DISABLED_A,
    _HOUSE_DISABLED_B,
    _GLASS_CARD_TOP,
    _GLASS_CARD_GRADIENT,
    _GLASS_CARD_GRADIENT_INDIGO,
    _DENSITY_FACTORS,
    THEMES,
    _HEX_RE,
    _hex_rgb,
    _rgb_hex,
    _mix,
    _triplet,
    derive_theme,
    _resolve_theme,
    _remap_colors,
    _format_px,
    _FONT_SIZE_RE,
    _PADDING_RE,
    _PX_RE,
    _scale_fonts,
    _apply_density,
    build_stylesheet,
)

_fonts_loaded = False


def load_bundled_fonts() -> bool:
    """Register the bundled OFL faces (gui/assets/fonts/) with Qt.

    Sora is the QSS font stack's first choice for body text. The
    TTFs are packaged with the app (SIL OFL 1.1; see
    gui/assets/fonts/OFL-*.txt + BUNDLE_RECORD.md), so every machine
    renders the same typeface. Idempotent, and a silent no-op
    returning False when the files or Qt are unavailable — the
    fallback stack in the QSS keeps the app presentable either way.

    B-R1-7: one stack — Orbitron (variable, _DISPLAY_FONT_FILES) is
    the display face for the headline selectors in the QSS, Rajdhani
    (_FONT_FILES) was the body face, Share Tech Mono (_MONO_FONT_FILES)
    is the code/numbers face. The HUD face IS the display face now
    (Orbitron), so _HUD_FONT_FILES is empty by design. The previous
    stack (Inter / Space Grotesk / Chakra Petch / JetBrains Mono) was
    removed in the same batch — one stack, no dead weight.

    B-R1-7r (2026-11-09): the body face is Sora (variable,
    _FONT_FILES) — the owner picked Sora over Rajdhani ("whatever
    brings us close to scifi"); the Rajdhani files are removed in
    the same batch.
    """
    global _fonts_loaded
    if _fonts_loaded:
        return True
    try:
        from PySide6.QtGui import QFontDatabase, QGuiApplication
    except Exception:  # noqa: BLE001
        return False
    # QFontDatabase without an application instance segfaults in Qt
    # (found by the Phase B font test, which calls the loader
    # app-less): no app means "Qt unavailable" for this loader.
    if QGuiApplication.instance() is None:
        return False
    font_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
    ok = False
    for name in (_FONT_FILES + _DISPLAY_FONT_FILES + _HUD_FONT_FILES
                 + _MONO_FONT_FILES):
        path = os.path.join(font_dir, name)
        if os.path.isfile(path):
            try:
                if QFontDatabase.addApplicationFont(path) >= 0:
                    ok = True
            except Exception:  # noqa: BLE001
                pass
    _fonts_loaded = ok
    return ok


