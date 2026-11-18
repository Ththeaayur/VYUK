from __future__ import annotations

# GAPS-15 (2026-11-13): moved from core/ to gui/ — this module
# builds Qt widgets / stylesheets / panels, which is GUI work
# that lived in core (core -> GUI imports). Code unchanged;
# only the imports changed: GUI imports are now relative,
# core imports go through the GUI facade (core/gui_facade.py).

from voice_assistant.core.gui_facade import theme_sync as _theme_sync  # GAPS-15: core import via the GUI facade
from voice_assistant.core.gui_facade import tokens as _tokens  # GAPS-15: core import via the GUI facade

# ------------------------------------------------------------------ #
# 8. Design tokens -> live stylesheet
# ------------------------------------------------------------------ #

_token_proof_cache: dict | None = None


def _token_proof() -> dict | None:
    """Memoised prove_byte_identity(); None when it cannot run."""
    global _token_proof_cache
    if _token_proof_cache is not None:
        return _token_proof_cache
    try:
        from . import style as _shipped  # GAPS-15: the proof needs the shipped style, passed in

        _token_proof_cache = _tokens.prove_byte_identity(_shipped)
    except Exception:  # noqa: BLE001 — proof unavailable, not fatal
        _token_proof_cache = {}
    return _token_proof_cache


def token_sheet_proven(theme_name: str) -> bool:
    """True only when the token sheet for this theme is byte-identical
    to the shipped sheet (the staged module's own proof)."""
    proof = _token_proof() or {}
    row = (proof.get("themes") or {}).get(theme_name) or {}
    return bool(row.get("sheet_identical"))


def stylesheet_for(theme_arg, font_scale: float = 1.0,
                   density: str = "comfortable") -> tuple[str, str]:
    """Live stylesheet for a theme argument, token-sourced when proven.

    Returns (sheet, source) where source is "tokens" when the token
    sheet for a named theme is byte-identical to the shipped sheet,
    else "shipped". A dict theme (Custom / derived) has no token
    sheet and always takes the shipped path. Scale/density transforms
    are the shipped ones, applied after the token sheet exactly as
    build_stylesheet() applies them after its own sheet.
    """
    from . import style as _shipped

    if isinstance(theme_arg, str) and token_sheet_proven(theme_arg):
        sheet = _tokens.generate_stylesheet(theme_arg, _shipped)
        if font_scale != 1.0:
            sheet = _shipped._scale_fonts(sheet, float(font_scale))
        factor = _shipped._DENSITY_FACTORS[density]
        if factor != 1.0:
            sheet = _shipped._apply_density(sheet, factor)
        return sheet, "tokens"
    return _shipped.build_stylesheet(theme_arg, font_scale=font_scale,
                                     density=density), "shipped"


# ------------------------------------------------------------------ #
# 2. OS theme / accent sync ("Follow Windows", opt-in)
# ------------------------------------------------------------------ #

def follow_windows_theme(theme_arg, follow_enabled: bool):
    """Apply "Follow Windows" to a theme argument.

    Detection runs on every call (darkdetect first, registry fallback
    inside the staged module). When follow is OFF, or detection is
    unavailable (non-Windows), the theme is returned unchanged and
    the report says so — the shipped theme never changes by
    surprise. When follow is ON and an OS accent is available, only
    the accent token is overridden on the selected theme (never a
    new theme); a named theme is resolved to its shipped dict first
    so no other token moves.
    """
    detected = _theme_sync.detect_os_theme()
    accent_info = _theme_sync.detect_windows_accent()
    report = {
        "available": bool(detected.get("available")),
        "os_mode": detected.get("mode"),
        "source": detected.get("source"),
        "reason": detected.get("reason"),
        "accent": accent_info.get("accent"),
        "follow_enabled": bool(follow_enabled),
        "applied": False,
    }
    if not follow_enabled or not detected.get("available"):
        return theme_arg, report
    accent = accent_info.get("accent")
    if not accent:
        return theme_arg, report
    try:
        if isinstance(theme_arg, dict):
            out = _theme_sync.apply_accent(theme_arg, accent)
        else:
            from . import style as _shipped

            base = dict(_shipped.THEMES.get(theme_arg) or {})
            if not base:
                return theme_arg, report
            out = _theme_sync.apply_accent(base, accent)
    except Exception:  # noqa: BLE001 — a bad accent never breaks boot
        return theme_arg, report
    report["applied"] = True
    return out, report


# ------------------------------------------------------------------ #
# 7. Backdrop / material roles (DWM-gated, non-Windows fall-through)
# ------------------------------------------------------------------ #

def apply_window_backdrop(window, role: str = "main") -> dict:
    """Re-apply the window-chrome backdrop for a role.

    Gated by the staged module's is_supported() (DWM availability);
    on non-Windows hosts this records a no-op and changes nothing.
    A window with no native handle yet (not shown) is deferred,
    never applied to handle 0. Returns a plain dict so callers can
    record it without importing the staged module.
    """
    from . import dwm_backdrops as _backdrop

    if not _backdrop.is_supported():
        return {"applied": False, "role": role,
                "reason": "backdrop-unsupported-here"}
    try:
        win_id = window.winId()
    except Exception:  # noqa: BLE001
        return {"applied": False, "role": role, "reason": "no-window-handle"}
    if not win_id:
        return {"applied": False, "role": role,
                "reason": "deferred-not-shown"}
    try:
        result = _backdrop.on_theme_changed(window, role)
    except Exception as exc:  # noqa: BLE001 — chrome must not break boot
        return {"applied": False, "role": role,
                "reason": f"apply-failed: {exc.__class__.__name__}"}
    return {"applied": bool(result.applied), "role": role,
            "reason": result.reason}


# ------------------------------------------------------------------ #
# 4. Glyph set -> live status glyph
# ------------------------------------------------------------------ #

_STATUS_GLYPH_BY_WORD = (
    ("offline", "offline"),
    ("listening", "listening"),
    ("thinking", "thinking"),
    ("speaking", "speaking"),
    ("error", "error"),
    ("muted", "muted"),
    ("success", "success"),
    # Command Deck Phase D: the §7 HUD words reuse the closest
    # v564 pictorial glyph (the set has no warning triangle, so
    # SETUP REQUIRED / LOCAL CORE take the neutral idle mark
    # rather than the red error mark — attention is not fault).
    # "connecting" also covers "reconnecting" (substring match).
    ("responding", "speaking"),
    ("processing", "thinking"),
    ("connecting", "thinking"),
    ("calibrating", "thinking"),
    ("fault", "error"),
    ("setup required", "idle"),
    ("local core", "idle"),
)


def glyph_pixmap(name: str, size: int = 20):
    """Render a v564 glyph SVG to a QPixmap (lazy Qt)."""
    from . import status_glyphs as _glyphs

    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QPixmap
    from PySide6.QtSvg import QSvgRenderer

    validation = _glyphs.validate_all_glyphs()
    if not validation["all_valid"]:
        raise ValueError("glyph set failed validation")
    renderer = QSvgRenderer(QByteArray(_glyphs.glyph(name).encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(0x00000000)
    from PySide6.QtGui import QPainter

    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def status_glyph_for_text(text: str) -> str | None:
    """Glyph name for a status-pill text, or None when unmapped."""
    lowered = (text or "").lower()
    for word, glyph_name in _STATUS_GLYPH_BY_WORD:
        if word in lowered:
            return glyph_name
    return None


def sync_status_glyph(window, text: str) -> str | None:
    """Set the companion status-glyph label from a status text.

    The label is created by the assistant-page hook; when it is
    absent this is a no-op. Returns the glyph name used, or None.
    """
    label = getattr(window, "status_glyph_label", None)
    if label is None:
        return None
    name = status_glyph_for_text(text)
    if name is None:
        return None
    try:
        label.setPixmap(glyph_pixmap(name, 16))
    except Exception:  # noqa: BLE001 — a glyph must not break status
        return None
    return name


# ------------------------------------------------------------------ #
# 3. SuperQt widgets probe (factories enforce probe-gating)
# ------------------------------------------------------------------ #

def superqt_probe() -> dict:
    """The staged probe, as data (superqt stays lazy-optional)."""
    from . import superqt_widgets as _widgets

    return _widgets.superqt_available()


def searchable_combo(items=None, parent=None):
    """Searchable combo: superqt's when the probe passes, else the
    editable-QComboBox fallback the staged module provides."""
    from . import superqt_widgets as _widgets

    return _widgets.make_searchable_combo(items, parent)


def range_slider(minimum: int = 0, maximum: int = 100, parent=None):
    from . import superqt_widgets as _widgets

    return _widgets.make_range_slider(minimum, maximum, parent)


def collapsible(title: str = "", parent=None):
    from . import superqt_widgets as _widgets

    return _widgets.make_collapsible(title, parent)
